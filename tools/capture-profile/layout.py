#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Turn PDB symbols into capture layout values.

A `complete` profile needs the matching image anyway (the prologue bytes and the
descriptor offsets cannot come from a PDB), so the segment-to-RVA mapping is
taken from the image's own section table. Nothing here guesses.
"""
import struct

SECTION_HEADER = 40
DBI_FIXED = 64
OPTIONAL_DBG_SECTION_HDR = 5


class LayoutError(Exception):
    pass


def sections_from_pdb(stream):
    """Section start RVAs taken from the PDB's own `SectionHdr` debug stream.

    The DBI optional debug header is a uint16 array of stream indices; entry 5
    holds the COFF section table, so no image is needed to place a symbol.
    """
    dbi = stream(3)
    if len(dbi) < DBI_FIXED:
        raise LayoutError("DBI stream too small")
    mod_info, sect_contrib, sect_map, source_info, type_server, _mfc, opt_dbg = struct.unpack_from("<7i", dbi, 24)
    ec = struct.unpack_from("<i", dbi, 52)[0]
    base = DBI_FIXED + mod_info + sect_contrib + sect_map + source_info + type_server + ec
    if opt_dbg < (OPTIONAL_DBG_SECTION_HDR + 1) * 2 or base + opt_dbg > len(dbi):
        raise LayoutError("optional debug header absent")
    index = struct.unpack_from("<H", dbi, base + OPTIONAL_DBG_SECTION_HDR * 2)[0]
    data = stream(index)
    if len(data) < SECTION_HEADER:
        raise LayoutError("section header stream empty")
    out = []
    for i in range(len(data) // SECTION_HEADER):
        _vsize, vaddr = struct.unpack_from("<II", data, i * SECTION_HEADER + 8)
        out.append(vaddr)
    return out


def image_sections(pe):
    """Return section start RVAs indexed by segment-1 from a PE image."""
    if len(pe) < 0x40 or pe[:2] != b"MZ":
        raise LayoutError("not a PE image")
    e = struct.unpack_from("<I", pe, 0x3C)[0]
    if pe[e:e + 4] != b"PE\0\0":
        raise LayoutError("missing PE signature")
    nsec = struct.unpack_from("<H", pe, e + 6)[0]
    opt_size = struct.unpack_from("<H", pe, e + 20)[0]
    base = e + 24 + opt_size
    out = []
    for i in range(nsec):
        o = base + i * SECTION_HEADER
        if o + SECTION_HEADER > len(pe):
            raise LayoutError("section table truncated")
        _vsize, vaddr = struct.unpack_from("<II", pe, o + 8)
        out.append(vaddr)
    return out


def rva_of(sections, segment, offset):
    if segment < 1 or segment > len(sections):
        return None
    return sections[segment - 1] + offset


def address_level(publics, sections, spec):
    """Resolve entry and vtable RVAs from fully decorated symbol names.

    `spec` maps a logical key to acceptable decorated names; the first match wins
    so a caller can express fallbacks without re-parsing the PDB.
    """
    result = {"entryRva": None, "vtableRva": [], "missing": []}
    index = {name: (segment, offset) for segment, offset, name in publics}
    for key, names in spec.items():
        found = None
        for name in names:
            where = index.get(name)
            if where is not None:
                found = rva_of(sections, *where)
                break
        if found is None:
            result["missing"].append(key)
        elif key == "entry":
            result["entryRva"] = found
        else:
            result["vtableRva"].append(found)
    return result
