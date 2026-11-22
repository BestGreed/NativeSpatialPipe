#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare capture anchors across several audiodg images.

Prints one line per image and groups identical prologues, which is the evidence
needed for the "prologue follows the build family" assumption: if a family keeps
its prologue, an `anchor` entry can be inherited inside that family and only the
PDB-derived addresses need to change per build.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pdb_symbols
import resolve

ENTRY = resolve.ENTRY
VT_OBJECT = resolve.VT_OBJECT
VT_CONTAINED = resolve.VT_CONTAINED


def describe(exe):
    guid, age, name, pe = pdb_symbols.read_rsds(exe)
    pdb = resolve.fetch_pdb(guid, age, name, os.path.join(os.environ.get("TEMP", "."), "nsp-pdb-cache"))
    stream = pdb_symbols._msf_streams(pdb)
    sections = layout.sections_from_pdb(stream)
    publics = pdb_symbols.public_symbols(pdb)
    addresses = layout.address_level(
        publics, sections, {"entry": [ENTRY], "vtable": [VT_OBJECT], "candidate": [VT_CONTAINED]})
    if addresses["missing"]:
        raise layout.LayoutError("missing symbols: %s" % addresses["missing"])
    prologue = resolve.prologue_of(pe, addresses["entryRva"])
    return {
        "file": os.path.basename(os.path.dirname(exe)) or exe,
        "key": guid + format(age, "X"),
        "entry": addresses["entryRva"],
        "prologue": prologue,
        "vtables": addresses["vtableRva"],
        "sections": len(sections),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", nargs="+", help="audiodg.exe images")
    args = parser.parse_args(argv)
    rows, failures = [], 0
    for exe in args.exe:
        try:
            rows.append(describe(exe))
        except Exception as exc:  # noqa: BLE001
            print("FAIL %s: %s" % (exe, exc))
            failures += 1
    if not rows:
        return 1
    print("%-22s %-34s %-9s %-34s %s" % ("image", "rsds", "entry", "prologue", "vtables"))
    groups = {}
    for row in rows:
        print("%-22s %-34s 0x%-7X %-34s %s" % (
            row["file"][:22], row["key"], row["entry"], row["prologue"],
            " ".join("0x%X" % v for v in row["vtables"])))
        groups.setdefault(row["prologue"], []).append(row["file"])
    print("\nprologue groups: %d" % len(groups))
    for prologue, files in groups.items():
        print("  %s -> %s%s" % (prologue, ", ".join(files), "  (shared)" if len(files) > 1 else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
