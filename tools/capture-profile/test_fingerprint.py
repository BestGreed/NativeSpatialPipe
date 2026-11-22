#!/usr/bin/env python3
"""Self-check for tier-1 prologue fingerprinting.

Run directly:  python tools/capture-profile/test_fingerprint.py
It uses the local System32 audiodg.exe and a copy of the shipped table with the
image's member removed, so the fingerprint tier is exercised without a network.
Fixtures live under build/verify so the check also works with a read-only TEMP.
"""
import json
import os
import shutil
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pdb_symbols
import resolve

IMAGE = r"C:\Windows\System32\audiodg.exe"
TABLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "modules", "native-capture", "profiles",
                     "capture-profiles.json")


def write_table(path, table):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(table, fh, indent=2, sort_keys=True)
        fh.write("\n")


def without_member(table, key):
    clone = json.loads(json.dumps(table))
    clone["members"].pop(key, None)
    return clone


def main():
    if not os.path.exists(IMAGE):
        print("SKIP fingerprint: %s missing" % IMAGE)
        return 0
    guid, age, _name, _pe = pdb_symbols.read_rsds(IMAGE)
    key = guid + format(age, "X")
    table = resolve.load_table(TABLE)
    expected = table["members"].get(key)
    if expected is None:
        print("SKIP fingerprint: local image %s is not in the shipped table" % key)
        return 0
    failures = 0
    work = os.path.join(os.getcwd(), "build", "verify", "fingerprint-%d" % os.getpid())
    os.makedirs(work, exist_ok=True)
    try:
        table_path = os.path.join(work, "unknown.json")
        write_table(table_path, without_member(table, key))
        cache = os.path.join(work, "empty-pdb-cache")
        try:
            profile = resolve.select_for_image(table_path, IMAGE, cache, offline=True)
        except layout.LayoutError as exc:
            print("FAIL unique prologue match was not used offline: %s" % exc)
            failures += 1
        else:
            if profile["layout"] == expected and profile.get("identitySource") == "fingerprint":
                print("OK   unique prologue match resolves offline without a PDB")
            else:
                print("FAIL fingerprint profile layout=%s source=%s" % (
                    profile.get("layout"), profile.get("identitySource")))
                failures += 1

        ambiguous = without_member(table, key)
        clone = json.loads(json.dumps(ambiguous["layouts"][expected]))
        clone["capture"]["vtableRva"] = [clone["capture"]["vtableRva"][0] + 0x10]
        alt = expected + "_alt"
        ambiguous["layouts"][alt] = clone
        ambiguous_path = os.path.join(work, "ambiguous.json")
        write_table(ambiguous_path, ambiguous)
        try:
            resolve.select_for_image(ambiguous_path, IMAGE, cache, offline=True)
        except layout.LayoutError as exc:
            if "ambiguous" in str(exc):
                print("OK   two matching layouts are reported as ambiguous")
            else:
                print("FAIL unexpected ambiguity error: %s" % exc)
                failures += 1
        else:
            print("FAIL ambiguous fingerprint was accepted")
            failures += 1

        version = resolve.file_version(IMAGE)
        assert version
        same_branch = ".".join(map(str, version[:3]))
        ambiguous["layouts"][alt]["fileVersions"] = [f"{same_branch}.{version[3] + 1}"]
        ambiguous["layouts"][expected]["fileVersions"] = [f"{same_branch}.{version[3] + 4}"]
        write_table(ambiguous_path, ambiguous)
        original = ambiguous["layouts"][expected]["capture"]
        addresses = {"entryRva": original["entryRva"], "primary": original["vtableRva"][0],
                     "contained": original["vtableCandidates"][1]}
        holdout = without_member(ambiguous, key)
        del holdout["layouts"][expected]
        holdout["members"] = {member: lid for member, lid in holdout["members"].items()
                              if lid != expected}
        holdout_path = os.path.join(work, "holdout.json")
        write_table(holdout_path, holdout)
        output = os.path.join(work, "capture-profile.json")
        args = ["--select", IMAGE, "--table", holdout_path, "--pdb-cache", cache,
                "--out", output]
        resolve.main(args + ["--offline", "--reuse-pdb-profile"])
        with open(output, encoding="utf-8") as fh:
            wrong = json.load(fh)
        assert wrong["layout"] == alt and wrong["identitySource"] == "fingerprint", wrong
        with patch.object(resolve, "resolve_addresses", side_effect=layout.LayoutError("PDB unavailable")):
            try:
                resolve.main(args + ["--verify-pdb"])
            except layout.LayoutError:
                pass
            else:
                raise AssertionError("a failed PDB lookup was accepted")
        with open(output, encoding="utf-8") as fh:
            assert json.load(fh) == wrong
        with patch.object(resolve, "resolve_addresses", return_value=addresses) as pdb:
            resolve.main(args + ["--verify-pdb"])
            pdb.assert_called_once()
        with open(output, encoding="utf-8") as fh:
            verified = json.load(fh)
        assert verified["identitySource"] == "pdb", verified
        assert verified["capture"]["vtableRva"] == [addresses["primary"]], verified
        with patch.object(resolve, "resolve_addresses", side_effect=AssertionError("startup looked up PDB")):
            resolve.main(args + ["--offline", "--reuse-pdb-profile"])
        with open(output, encoding="utf-8") as fh:
            assert json.load(fh) == verified
        for edit in (
            lambda p: p.update(symbolKey="0" * 32 + "1"),
            lambda p: p["capture"].update(prologue="00" * 16),
        ):
            invalid = json.loads(json.dumps(verified))
            edit(invalid)
            write_table(output, invalid)
            resolve.main(args + ["--offline", "--reuse-pdb-profile"])
            with open(output, encoding="utf-8") as fh:
                assert json.load(fh) == wrong
        print("OK   unique false match: intent verifies by PDB, startup reuses only a matching cache")

        with patch.object(resolve, "resolve_addresses", return_value=addresses) as pdb:
            selected = resolve.select_for_image(ambiguous_path, IMAGE, cache)
        if (pdb.call_count == 1 and selected["layout"] == expected and
                selected["identitySource"] == "pdb" and selected["fingerprintCandidates"] == [alt, expected]):
            print("OK   closer candidate sorts first; matching PDB overrides it")
        else:
            print("FAIL ranking or automatic PDB disambiguation: %r" % selected)
            failures += 1

        ambiguous["layouts"][expected]["fileVersions"] = [f"{same_branch}.{version[3] - 1}"]
        write_table(ambiguous_path, ambiguous)
        with patch.object(resolve, "resolve_addresses", return_value=addresses) as pdb:
            selected = resolve.select_for_image(ambiguous_path, IMAGE, cache)
        if pdb.call_count == 1 and selected["layout"] == expected and selected["identitySource"] == "pdb":
            print("OK   equal version distance still uses the PDB")
        else:
            print("FAIL equal version distance bypassed PDB disambiguation")
            failures += 1

        try:
            picked = resolve.select_for_image(ambiguous_path, IMAGE, cache,
                                              offline=True, pick=alt)
        except layout.LayoutError as exc:
            print("FAIL explicit pick was rejected: %s" % exc)
            failures += 1
        else:
            if picked["layout"] == alt and picked.get("identitySource") == "pick":
                print("OK   --pick selects the requested candidate")
            else:
                print("FAIL pick produced layout=%s source=%s" % (
                    picked.get("layout"), picked.get("identitySource")))
                failures += 1

        try:
            resolve.select_for_image(ambiguous_path, IMAGE, cache, offline=True,
                                     pick="L6D650_76870")
        except layout.LayoutError as exc:
            if "does not match" in str(exc):
                print("OK   --pick rejects a layout whose prologue does not match")
            else:
                print("FAIL unexpected pick error: %s" % exc)
                failures += 1
        else:
            print("FAIL non-matching pick was accepted")
            failures += 1

        no_match = without_member(table, key)
        for slot in no_match["layouts"].values():
            slot["capture"]["prologue"] = "00" * 16
        no_match_path = os.path.join(work, "no-match.json")
        write_table(no_match_path, no_match)
        try:
            resolve.select_for_image(no_match_path, IMAGE, cache, offline=True)
        except layout.LayoutError as exc:
            if "offline" in str(exc):
                print("OK   offline without a fingerprint match requires an import")
            else:
                print("FAIL unexpected offline error: %s" % exc)
                failures += 1
        else:
            print("FAIL offline selection without a match succeeded")
            failures += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("failures=%d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
