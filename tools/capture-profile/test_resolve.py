#!/usr/bin/env python3
"""Self-check for offline profile import and PDB-backed selection.

Run directly:  python tools/capture-profile/test_resolve.py
It uses the local System32 audiodg.exe as the identity source; a missing image
or cached PDB is reported as SKIP rather than a failure.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pdb_symbols
import resolve

IMAGE = r"C:\Windows\System32\audiodg.exe"


def profile_for(key, prologue="48895c240848896c2410488974241857"):
    profile = {"schema": 1, "symbolKey": key, "layout": "test",
               "capture": {"entryRva": 0x31930, "vtableRva": [0x9F4F8],
                           "prologue": prologue}}
    profile["capture"].update(resolve.CONTRACT)
    return profile


def write(path, value):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh)


def main():
    if not os.path.exists(IMAGE):
        print("SKIP offline import: %s missing" % IMAGE)
        return 0
    guid, age, _name, _pe = pdb_symbols.read_rsds(IMAGE)
    key = guid + format(age, "X")
    failures = 0
    with tempfile.TemporaryDirectory() as work:
        good = os.path.join(work, "good.json")
        write(good, profile_for(key))
        selected = resolve.select_for_image("unused-table.json", IMAGE, work, profile_file=good)
        if selected["symbolKey"] == key and selected["capture"]["entryRva"] == 0x31930:
            print("OK   imported profile identity and capture survive selection")
        else:
            print("FAIL imported profile was altered")
            failures += 1

        for label, mutate in (
            ("identity mismatch", lambda p: p.update(symbolKey="000000000000000000000000000000000")),
            ("zero entry", lambda p: p["capture"].update(entryRva=0)),
            ("empty vtables", lambda p: p["capture"].update(vtableRva=[])),
            ("odd prologue", lambda p: p["capture"].update(prologue="abc")),
        ):
            bad = os.path.join(work, "bad.json")
            value = profile_for(key)
            mutate(value)
            write(bad, value)
            try:
                resolve.select_for_image("unused-table.json", IMAGE, work, profile_file=bad)
            except layout.LayoutError:
                print("OK   rejected %s" % label)
            else:
                print("FAIL accepted %s" % label)
                failures += 1

        pdb_dir = os.path.join(os.environ.get("TEMP", "."), "nsp-pdb-cache")
        cache = os.path.join(pdb_dir, guid + ".pdb")
        if not os.path.exists(cache):
            print("SKIP PDB import: no cached PDB at %s" % cache)
        else:
            with open(cache, "rb") as fh:
                data = fh.read()
            stream = pdb_symbols._msf_streams(data)
            identity = pdb_symbols.pdb_identity(data, stream)
            if identity["guid"] == guid:
                print("OK   cached PDB GUID matches the image (image age %X, PDB age %X)"
                      % (age, identity["age"]))
            else:
                print("FAIL cached PDB GUID mismatch")
                failures += 1
            selected = resolve.select_for_image("unused-table.json", IMAGE, pdb_dir)
            if selected["symbolKey"] == key and selected["capture"]["prologue"]:
                print("OK   PDB-backed selection keeps the image prologue")
            else:
                print("FAIL PDB-backed selection lost identity or prologue")
                failures += 1

            imported = os.path.join(work, "imported.pdb")
            with open(imported, "wb") as fh:
                fh.write(data)
            try:
                selected = resolve.select_for_image(
                    "unused-table.json", IMAGE, work, pdb_file=imported)
            except layout.LayoutError as exc:
                print("FAIL matching imported PDB was rejected: %s" % exc)
                failures += 1
            else:
                print("OK   imported PDB matching the image is accepted"
                      if selected["symbolKey"] == key else
                      "FAIL imported PDB lost the image identity")

            # A structurally valid PDB whose info stream carries another GUID
            # proves the identity check runs before symbol resolution.
            info = stream(1)
            body = bytearray(data)
            guid_at = bytes(info[12:28])
            guid_off = body.find(guid_at)
            if guid_off < 0:
                print("SKIP PDB mismatch: info stream GUID not found verbatim")
            else:
                body[guid_off:guid_off + 16] = bytes(b ^ 0xFF for b in guid_at)
                foreign = os.path.join(work, "foreign.pdb")
                with open(foreign, "wb") as fh:
                    fh.write(body)
                try:
                    resolve.select_for_image(
                        "unused-table.json", IMAGE, work, pdb_file=foreign)
                except layout.LayoutError as exc:
                    if "does not match" in str(exc):
                        print("OK   rejected PDB with another identity")
                    else:
                        print("FAIL unexpected error: %s" % exc)
                        failures += 1
                else:
                    print("FAIL accepted PDB with another identity")
                    failures += 1

    print("failures=%d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
