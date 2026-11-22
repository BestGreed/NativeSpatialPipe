#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read a CodeView RSDS record from a PE image and resolve public symbols from
its matching PDB.

No signature scanning: the entry point and the vtable addresses are taken from
public symbol names, and the caller is expected to confirm the anchor afterwards
(the hook compares the prologue before patching).
"""
import struct

RSDS = b"RSDS"
MSF_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0"
S_PUB32 = 0x110E


class ProfileError(Exception):
    pass


def read_rsds(image_path):
    """Return (guid_hex, age, pdb_name, pe) for a PE image."""
    with open(image_path, "rb") as fh:
        pe = fh.read()
    if len(pe) < 0x40 or pe[:2] != b"MZ":
        raise ProfileError("not a PE image")
    e_lfanew = struct.unpack_from("<I", pe, 0x3C)[0]
    if pe[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ProfileError("missing PE signature")
    nsec = struct.unpack_from("<H", pe, e_lfanew + 6)[0]
    opt_size = struct.unpack_from("<H", pe, e_lfanew + 20)[0]
    opt = e_lfanew + 24
    magic = struct.unpack_from("<H", pe, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise ProfileError("unexpected optional header magic")
    dirs = opt + (112 if magic == 0x20B else 96)
    dbg_rva = struct.unpack_from("<I", pe, dirs + 6 * 8)[0]
    sections = _sections(pe, opt, opt_size, nsec)
    offset = _rva_to_offset(sections, dbg_rva)
    if offset is None:
        raise ProfileError("debug directory outside any section")
    size_of_data = struct.unpack_from("<I", pe, offset + 16)[0]
    raw = struct.unpack_from("<I", pe, offset + 24)[0]
    data = pe[raw:raw + size_of_data]
    if data[:4] != RSDS:
        raise ProfileError("first debug entry is not CodeView RSDS")
    d1, d2, d3 = struct.unpack_from("<IHH", data, 4)
    guid_hex = "%08X%04X%04X%s" % (d1, d2, d3, data[12:20].hex().upper())
    age = struct.unpack_from("<I", data, 20)[0]
    name = data[24:].split(b"\0")[0].decode("ascii", "replace")
    return guid_hex, age, name, pe


def _sections(pe, opt, opt_size, nsec):
    out = []
    base = opt + opt_size
    for i in range(nsec):
        o = base + i * 40
        name = pe[o:o + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", pe, o + 8)
        out.append((name, vaddr, max(vsize, rawsize), rawptr))
    return out


def _rva_to_offset(sections, rva):
    for _, vaddr, span, rawptr in sections:
        if vaddr <= rva < vaddr + span:
            return rawptr + (rva - vaddr)
    return None


def _msf_streams(pdb):
    if not pdb.startswith(MSF_MAGIC):
        raise ProfileError("not an MSF 7.0 PDB")
    block, _, _, dir_bytes, _, dir_block = struct.unpack_from("<IIIIII", pdb, 32)
    n_dir = (dir_bytes + block - 1) // block
    mapping = pdb[dir_block * block: dir_block * block + n_dir * 4]
    blocks = struct.unpack_from("<%dI" % n_dir, mapping)
    dirstream = b"".join(pdb[b * block:(b + 1) * block] for b in blocks)[:dir_bytes]
    count = struct.unpack_from("<I", dirstream, 0)[0]
    sizes = struct.unpack_from("<%dI" % count, dirstream, 4)
    pos = 4 + count * 4
    layout = []
    for size in sizes:
        if size in (0, 0xFFFFFFFF):
            layout.append([])
            continue
        n = (size + block - 1) // block
        layout.append(struct.unpack_from("<%dI" % n, dirstream, pos))
        pos += n * 4
    def stream(i):
        if i >= len(sizes) or sizes[i] in (0, 0xFFFFFFFF):
            return b""
        return b"".join(pdb[b * block:(b + 1) * block] for b in layout[i])[:sizes[i]]
    return stream


def pdb_identity(pdb, stream):
    info = stream(1)
    if len(info) < 28:
        raise ProfileError("PDB info stream missing")
    version, signature, age = struct.unpack_from("<III", info, 0)
    d1, d2, d3 = struct.unpack_from("<IHH", info, 12)
    guid = "%08X%04X%04X%s" % (d1, d2, d3, info[20:28].hex().upper())
    return {"version": version, "signature": signature, "age": age, "guid": guid}


def public_symbols(pdb):
    """Yield (decorated_name, rva) for every S_PUB32 record usable as an RVA."""
    stream = _msf_streams(pdb)
    dbi = stream(3)
    if len(dbi) < 22:
        raise ProfileError("DBI stream missing")
    sym_stream = struct.unpack_from("<H", dbi, 20)[0]
    records = stream(sym_stream)
    out = []
    i = 0
    while i + 4 <= len(records):
        length, kind = struct.unpack_from("<HH", records, i)
        if length < 4:
            break
        body = records[i + 4:i + 2 + length]
        if kind == S_PUB32 and len(body) >= 10:
            _flags, offset, segment = struct.unpack_from("<IIH", body, 0)
            name = body[10:].split(b"\0")[0].decode("ascii", "replace")
            out.append((segment, offset, name))
        i += 2 + length
    return out


def find_symbol(publics, decorated):
    """Exact decorated-name match; returns (segment, offset) or None."""
    for segment, offset, name in publics:
        if name == decorated:
            return segment, offset
    return None
