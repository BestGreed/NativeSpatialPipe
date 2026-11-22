#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Merge PCM capture anchors into the profile table.

The PCM entry point and its input vtable live in the same PDB as the spatial
ones, so the same public-symbol resolution applies. The base offset comes from
`lea rXX,[rcx+disp]` inside the resolved function, and the 32-byte entry
prologue comes from the matching image.

Levels: `anchor` means addresses, base offset and prologue are all recorded, so
the C side may use the path. `address` means only the addresses are known (no
image): the path stays disabled because the prologue cannot be verified.
`unavailable` means the symbols or the displacement instruction were missing.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pdb_symbols
import resolve

ENTRY = ("?GetInputDataPointer@?$CCrossProcessServerInputEndpoint@UStaticControlData_V1@@"
         "UVolatileControlData_V0@@UControlData_V1@@@@UEAAXPEAUAPO_CONNECTION_PROPERTY@@PEAUAE_CURRENT_POSITION@@@Z")
VTABLE = ("??_7?$CComObject@V?$CCrossProcessServerInputEndpoint@UStaticControlData_V1@@"
          "UVolatileControlData_V0@@UControlData_V1@@@@@ATL@@6B?$CCrossProcessInputEndpoint@"
          "UStaticControlData_V1@@UVolatileControlData_V0@@UControlData_V1@@@@@")
CONTRACT = "rcx-0x1E0"
BASE_DISP = 0xFFFFFE20  # -0x1E0 as a little-endian disp32
PROLOGUE_BYTES = 32
ENTRY_PROLOGUE = PROLOGUE_BYTES


def base_disp(pe, entry_rva):
    """Return the base displacement from `lea rXX,[rcx+disp]`, or None."""
    e = struct.unpack_from("<I", pe, 0x3C)[0]
    nsec = struct.unpack_from("<H", pe, e + 6)[0]
    opt_size = struct.unpack_from("<H", pe, e + 20)[0]
    base = e + 24 + opt_size
    for i in range(nsec):
        o = base + i * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", pe, o + 8)
        if vaddr <= entry_rva < vaddr + max(vsize, rawsize):
            start = rawptr + (entry_rva - vaddr)
            body = pe[start:start + 0x200]
            for j in range(2, len(body) - 6):
                if body[j - 2] == 0x8D and (body[j - 1] & 0xC7) == 0x81:
                    disp = struct.unpack_from("<i", body, j)[0]
                    if -0x800 <= disp < 0:
                        return disp
            return None
    raise layout.LayoutError("entry RVA outside every section")


ENTRY_PREFIXES = (
    "?GetInputDataPointer@?$CCrossProcessServerInputEndpoint@",
    "?GetInputDataPointer@CCrossProcessServerInputEndpoint@@",
)
VTABLE_PREFIXES = (
    ("??_7?$CComObject@V?$CCrossProcessServerInputEndpoint@", "6B?$CCrossProcessInputEndpoint@"),
    ("??_7?$CComObject@VCCrossProcessServerInputEndpoint@@", "6BCCrossProcessInputEndpoint@"),
)
VTABLE_PREFIX = "??_7?$CComObject@V?$CCrossProcessServerInputEndpoint@"
VTABLE_MARK = "6B?$CCrossProcessInputEndpoint@"


def by_prefix(publics, prefix, mark=None):
    for segment, offset, name in publics:
        if name.startswith(prefix) and (mark is None or mark in name):
            return segment, offset
    return None


def pcm_section(publics, sections, pe, entry_rva):
    spec = {"entry": [ENTRY], "vtable": [VTABLE]}
    found = layout.address_level(publics, sections, spec)
    if found["missing"]:
        # Template instantiations differ between builds; fall back to the prefix.
        entry_hit = next((h for h in (by_prefix(publics, p) for p in ENTRY_PREFIXES) if h), None)
        vtable_hit = next((h for h in (by_prefix(publics, p, m) for p, m in VTABLE_PREFIXES) if h), None)
        if entry_hit and vtable_hit:
            found = {"entryRva": layout.rva_of(sections, *entry_hit),
                     "vtableRva": [layout.rva_of(sections, *vtable_hit)], "missing": []}
        else:
            return {"level": "unavailable", "reason": "symbols missing: %s" % found["missing"]}
    section = {
        "getInputRva": found["entryRva"],
        "inputVtableRva": found["vtableRva"][0],
        "contract": "lea rXX,[rcx+disp]",
    }
    disp = base_disp(pe, found["entryRva"])
    if disp is None:
        section["level"] = "unavailable"
        section["reason"] = "base displacement instruction absent"
        return section
    section["baseOffset"] = disp
    try:
        section["prologue"] = resolve.prologue_of(pe, found["entryRva"], PROLOGUE_BYTES)
    except layout.LayoutError as exc:
        section["level"] = "address"
        section["reason"] = "entry prologue unavailable: %s" % exc
    else:
        section["level"] = "anchor"
    return section


def merge_pcm(existing, computed):
    """Merge recomputed anchors into an existing PCM section.

    Field metadata and a documented `closed` decision survive; only the anchor
    values are refreshed.
    """
    merged = dict(existing)
    for field in ("getInputRva", "inputVtableRva", "contract", "baseOffset", "prologue"):
        if field in computed:
            merged[field] = computed[field]
    if existing.get("closed"):
        merged["level"] = "unavailable"
        merged.setdefault("reason", "control layout differs; path closed")
    else:
        merged["level"] = computed["level"]
        if computed.get("reason"):
            merged["reason"] = computed["reason"]
        elif merged.get("level") == "anchor":
            merged.pop("reason", None)
    return merged


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", action="append", default=[], help="audiodg.exe (repeatable)")
    parser.add_argument("--table", default="modules/native-capture/profiles/capture-profiles.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    table = resolve.load_table(args.table)
    failures = 0
    for exe in args.exe:
        try:
            guid, age, name, pe = pdb_symbols.read_rsds(exe)
            key = guid + format(age, "X")
            pdb = resolve.fetch_pdb(guid, age, name, os.path.join(os.environ.get("TEMP", "."), "nsp-pdb-cache"))
            stream = pdb_symbols._msf_streams(pdb)
            sections = layout.sections_from_pdb(stream)
            publics = pdb_symbols.public_symbols(pdb)
            lid = table["members"].get(key)
            if lid is None:
                print("SKIP %s: not in members" % key)
                continue
            existing = table["layouts"][lid].get("pcm", {})
            computed = pcm_section(publics, sections, pe,
                                   table["layouts"][lid]["capture"]["entryRva"])
            table["layouts"][lid]["pcm"] = merge_pcm(existing, computed)
            print("OK   %s -> %s pcm=%s" % (key, lid, table["layouts"][lid]["pcm"]["level"]))
        except Exception as exc:  # noqa: BLE001
            print("FAIL %s: %s" % (exe, exc))
            failures += 1
    if not args.dry_run and not failures:
        resolve.save_table(args.table, table)
        print("wrote %s" % args.table)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
