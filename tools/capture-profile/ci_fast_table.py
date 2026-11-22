#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI step: merge extracted audiodg images into the shipped fast table.

Input is a list of images (`--exe`, an extractor tree, or extract_images.py's
manifest). For a known RSDS only the prologue anchors are refreshed; a new RSDS
is resolved through its PDB and either joins an existing layout or creates one
with its PCM section. The run refuses to rewrite the table when a member or
layout would be dropped or moved, so a CI failure cannot silently shrink the
release matrix.

`--offline` forbids the PDB fallback: only images that already have a member can
be refreshed.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pdb_symbols
import resolve
import resolve_pcm


def collect_images(args):
    paths = list(args.exe)
    if args.images:
        manifest = os.path.join(args.images, "manifest.json")
        if os.path.isfile(manifest):
            with open(manifest, encoding="utf-8") as fh:
                for entry in json.load(fh):
                    paths.append(os.path.join(args.images, entry["image"]))
        else:
            for base, _dirs, files in os.walk(args.images):
                for name in files:
                    if name.lower() == "audiodg.exe":
                        paths.append(os.path.join(base, name))
    seen, unique = set(), []
    for path in paths:
        resolved = os.path.abspath(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def pdb_sections(guid, age, name, cache_dir, offline):
    if offline:
        raise RuntimeError("--offline forbids PDB resolution for an unknown member")
    pdb = resolve.fetch_pdb(guid, age, name, cache_dir)
    stream = pdb_symbols._msf_streams(pdb)
    return layout.sections_from_pdb(stream), pdb_symbols.public_symbols(pdb)


def validate(table):
    problems = []
    for key, lid in table["members"].items():
        if lid not in table["layouts"]:
            problems.append("member %s points at missing layout %s" % (key, lid))
    for lid, slot in table["layouts"].items():
        capture = slot.get("capture") or {}
        if not capture.get("entryRva") or not capture.get("vtableRva"):
            problems.append("layout %s has no entry/vtable" % lid)
        prologue = capture.get("prologue") or ""
        if len(prologue) % 2 or len(prologue) > 64:
            problems.append("layout %s prologue is malformed" % lid)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", action="append", default=[], help="audiodg.exe (repeatable)")
    parser.add_argument("--images", help="extract_images.py output directory")
    parser.add_argument("--table", default="modules/native-capture/profiles/capture-profiles.json")
    parser.add_argument("--pdb-cache", default=os.path.join(os.environ.get("TEMP", "."), "nsp-pdb-cache"))
    parser.add_argument("--report", help="write the run report to this path")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="allow a member to move to another layout")
    args = parser.parse_args(argv)
    images = collect_images(args)
    if not images:
        print("FAIL no images given (--exe/--images)")
        return 1
    table = resolve.load_table(args.table)
    before_members = dict(table["members"])
    before_layouts = set(table["layouts"])
    report = []
    failures = []
    for exe in images:
        entry = {"exe": exe}
        try:
            guid, age, name, pe = pdb_symbols.read_rsds(exe)
            key = guid + format(age, "X")
            entry["symbolKey"] = key
            actions = []
            lid = table["members"].get(key)
            if lid is None:
                candidates = resolve.fingerprint_candidates(table, pe)
                if candidates:
                    entry["fingerprintCandidates"] = resolve.rank_fingerprint_candidates(
                        table, candidates, resolve.file_version(exe))
                # Only publish an exact RSDS mapping once its addresses are verified.
                sections, publics = pdb_sections(guid, age, name, args.pdb_cache, args.offline)
                addr = resolve.resolve_addresses(guid, age, name, args.pdb_cache)
                lid = resolve.layout_id(addr["entryRva"], [addr["primary"]])
                prologue = resolve.prologue_of(pe, addr["entryRva"])
                if lid in table["layouts"]:
                    table["members"][key] = lid
                    actions.append("joined-layout")
                else:
                    resolve.add_build(table, key, addr["entryRva"], addr["primary"],
                                      addr["contained"], prologue)
                    capture = table["layouts"][lid]["capture"]
                    table["layouts"][lid]["pcm"] = resolve_pcm.pcm_section(
                        publics, sections, pe, capture["entryRva"])
                    actions.append("added-layout")
            else:
                actions.append("member")
            capture = table["layouts"][lid]["capture"]
            stored = (capture.get("prologue") or "").lower()
            actual = resolve.prologue_of(pe, capture["entryRva"], len(stored) // 2 or 16)
            if stored and actual.lower() != stored:
                raise RuntimeError(
                    "image prologue at 0x%X differs from layout %s" % (capture["entryRva"], lid))
            if not stored:
                capture["prologue"] = actual
                actions.append("prologue")
            pcm = table["layouts"][lid].get("pcm")
            if isinstance(pcm, dict) and pcm.get("getInputRva"):
                pcm_stored = (pcm.get("prologue") or "").lower()
                pcm_actual = resolve.prologue_of(pe, pcm["getInputRva"], 32)
                if pcm_stored and pcm_actual.lower() != pcm_stored:
                    raise RuntimeError("image PCM prologue at 0x%X differs from layout %s"
                                       % (pcm["getInputRva"], lid))
                if not pcm_stored:
                    pcm["prologue"] = pcm_actual
                    actions.append("pcm-prologue")
                entry["pcm"] = pcm.get("level")
            resolve.record_file_version(table["layouts"][lid], resolve.file_version(exe))
            entry.update(status="ok", layout=lid, actions=actions)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            entry.update(status="failed", error=str(exc))
            failures.append(entry)
        report.append(entry)

    for key, lid in before_members.items():
        if table["members"].get(key) != lid and not args.force:
            failures.append({"symbolKey": key,
                             "error": "member would move from %s to %s"
                                      % (lid, table["members"].get(key))})
    for lid in sorted(before_layouts):
        if lid not in table["layouts"]:
            failures.append({"layout": lid, "error": "layout would be dropped"})
    failures.extend({"error": problem} for problem in validate(table))

    payload = {"ok": not failures, "table": args.table, "images": report, "failures": failures}
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    print(json.dumps(payload, indent=2))
    if failures:
        return 1
    if not args.dry_run:
        resolve.save_table(args.table, table)
        print("wrote %s (%d layouts, %d members)" % (
            args.table, len(table["layouts"]), len(table["members"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
