#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the capture-profile table that ships with the product.

Table shape:

    {"layouts": {"L1": {"capture": {...}}},
     "members": {"<RSDS>": "L1"}}

A layout is identified by its resolved addresses, so two builds that land on the
same entry RVA and the same vtable set share one layout automatically. The
prologue attaches to the layout when an image is available and is inherited by
address-only builds of the same layout.

Addresses come from the public PDB alone (the PDB carries the COFF section
table). Only the prologue needs the matching image.
"""
import argparse
import ctypes
import json
import os
import struct
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pdb_symbols

ENTRY = ("?GetInputDataPointer@CSpatialCrossProcessServerInputEndpoint@@UEAAX"
         "PEAUAPO_CONNECTION_PROPERTY@@PEAUAE_CURRENT_POSITION@@@Z")
VT_OBJECT = ("??_7?$CComObject@VCSpatialCrossProcessServerInputEndpoint@@@ATL@@"
             "6BISpatialAudioProcessBlockRT@@@")
VT_CONTAINED = ("??_7?$CComContainedObject@VCSpatialCrossProcessServerInputEndpoint@@@ATL@@"
                "6BISpatialAudioProcessBlockRT@@@")
SYMBOL_URL = "https://msdl.microsoft.com/download/symbols/%s/%s/%s"
PROLOGUE_BYTES = 16


def fetch_pdb(guid, age, name, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "%s.pdb" % guid)
    if os.path.exists(path) and os.path.getsize(path) > 10000:
        return open(path, "rb").read()
    url = SYMBOL_URL % (name, guid + format(age, "X"), name)
    request = urllib.request.Request(url, headers={"User-Agent": "Microsoft-Symbol-Server/10.0.0.0"})
    data = urllib.request.urlopen(request, timeout=180).read()
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def prologue_of(pe, entry_rva, size=PROLOGUE_BYTES):
    e = struct.unpack_from("<I", pe, 0x3C)[0]
    nsec = struct.unpack_from("<H", pe, e + 6)[0]
    opt_size = struct.unpack_from("<H", pe, e + 20)[0]
    base = e + 24 + opt_size
    for i in range(nsec):
        o = base + i * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", pe, o + 8)
        if vaddr <= entry_rva < vaddr + max(vsize, rawsize):
            start = rawptr + (entry_rva - vaddr)
            return pe[start:start + size].hex()
    raise layout.LayoutError("entry RVA outside every section")


def resolve_addresses(guid, age, name, cache_dir, pdb_bytes=None):
    pdb = pdb_bytes if pdb_bytes is not None else fetch_pdb(guid, age, name, cache_dir)
    stream = pdb_symbols._msf_streams(pdb)
    if pdb_symbols.pdb_identity(pdb, stream)["guid"] != guid:
        raise layout.LayoutError("PDB GUID does not match image RSDS GUID %s" % guid)
    sections = layout.sections_from_pdb(stream)
    publics = pdb_symbols.public_symbols(pdb)
    spec = {"entry": [ENTRY], "primary": [VT_OBJECT], "contained": [VT_CONTAINED]}
    result = layout.address_level(publics, sections, spec)
    if result["missing"]:
        raise layout.LayoutError("symbols missing: %s" % result["missing"])
    # spec order is entry, primary, contained; the non-entry keys append in order.
    vtables = result["vtableRva"]
    return {"entryRva": result["entryRva"], "primary": vtables[0], "contained": vtables[1]}


def pdb_identity(pdb_bytes):
    stream = pdb_symbols._msf_streams(pdb_bytes)
    return pdb_symbols.pdb_identity(pdb_bytes, stream)


def profile_problem(profile, key):
    """Explain why a single-profile file cannot serve the image's RSDS."""
    if not isinstance(profile, dict):
        return "profile is not a JSON object"
    if profile.get("symbolKey") != key:
        return "profile identity %r does not match image RSDS %s" % (profile.get("symbolKey"), key)
    capture = profile.get("capture")
    if not isinstance(capture, dict):
        return "profile has no capture section"
    entry = capture.get("entryRva")
    if not isinstance(entry, int) or isinstance(entry, bool) or entry <= 0:
        return "capture.entryRva is missing or not a positive integer"
    vtables = capture.get("vtableRva")
    if not isinstance(vtables, list) or not vtables:
        return "capture.vtableRva is missing or empty"
    prologue = capture.get("prologue", "")
    if not isinstance(prologue, str) or len(prologue) % 2 or len(prologue) > 64:
        return "capture.prologue is not an even-length hex string of at most 32 bytes"
    return None


def fingerprint_candidates(table, pe):
    """Layouts whose recorded entry prologue matches the image at that RVA.

    A tier-1 match is evidence, not proof: the layout's vtable/window still go
    through the hook's prologue check and first-packet canary.
    """
    matches = []
    for lid, slot in table.get("layouts", {}).items():
        capture = slot.get("capture") or {}
        prologue = capture.get("prologue") or ""
        entry = capture.get("entryRva")
        if not prologue or not isinstance(entry, int) or entry <= 0 or len(prologue) % 2:
            continue
        try:
            got = prologue_of(pe, entry, len(prologue) // 2)
        except layout.LayoutError:
            continue
        if got.lower() == prologue.lower():
            matches.append(lid)
    return sorted(matches)


def file_version(image_path):
    """Read the fixed PE version used for same-branch candidate ranking."""
    if sys.platform != "win32":
        return None
    api = ctypes.windll.version
    path = ctypes.c_wchar_p(os.fspath(image_path))
    size = api.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(path, 0, size, data):
        return None
    value, count = ctypes.c_void_p(), ctypes.c_uint()
    if not api.VerQueryValueW(data, ctypes.c_wchar_p("\\"), ctypes.byref(value), ctypes.byref(count)) or count.value < 52:
        return None
    signature, _, high, low = struct.unpack("<4I", ctypes.string_at(value, 16))
    if signature != 0xFEEF04BD:
        return None
    return (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF)


def record_file_version(slot, version):
    if version:
        versions = slot.setdefault("fileVersions", [])
        text = ".".join(map(str, version))
        if text not in versions:
            versions.append(text)
            versions.sort()


def rank_fingerprint_candidates(table, candidates, version):
    """Order by the nearest sampled revision on the same component build."""
    if not version:
        return sorted(candidates)
    prefix = ".".join(map(str, version[:3])) + "."

    def rank(lid):
        revisions = (text[len(prefix):] for text in table["layouts"][lid].get("fileVersions", [])
                     if isinstance(text, str) and text.startswith(prefix))
        distance = min((abs(int(rev) - version[3]) for rev in revisions
                        if len(rev) <= 5 and rev.isascii() and rev.isdigit()), default=float("inf"))
        return distance, lid

    return sorted(candidates, key=rank)


def select_for_image(table_path, image_path, cache_dir, pdb_file=None, profile_file=None,
                     offline=False, pick=None, verify_pdb=False, cached_profile=None):
    """Materialise the single-profile file the runtime reads.

    Order: an explicitly imported profile -> exact member hit -> tier-1 prologue
    fingerprint -> resolve from the PDB. A unique fingerprint match needs no
    network and marks the profile `identitySource: fingerprint`, so a later
    canary failure can be traced to the guessed layout. Several matches are
    ranked by file-version distance and verified by PDB, or chosen explicitly
    with `--pick`. `--offline` allows an imported PDB without downloading.
    Explicit verification of an unknown RSDS skips the unique-fingerprint
    shortcut. Startup may reuse a matching PDB-derived profile.
    Prologue is taken from the image when the chosen layout has none yet.
    """
    guid, age, name, pe = pdb_symbols.read_rsds(image_path)
    key = guid + format(age, "X")
    if profile_file:
        with open(profile_file, encoding="utf-8-sig") as fh:
            profile = json.load(fh)
        problem = profile_problem(profile, key)
        if problem:
            raise layout.LayoutError(problem)
        profile.setdefault("schema", 1)
        return profile
    pdb_bytes = None
    if pdb_file:
        with open(pdb_file, "rb") as fh:
            pdb_bytes = fh.read()
        identity = pdb_identity(pdb_bytes)
        # Symbol-server PDBs republished for the same build can carry a newer
        # PDB age than the image's RSDS; the GUID is the build identity.
        if identity["guid"] != guid:
            raise layout.LayoutError(
                "PDB GUID %s does not match image RSDS GUID %s"
                % (identity["guid"], guid))
    table = load_table(table_path) if os.path.exists(table_path) else {
        "schema": 2, "layouts": {}, "members": {}}
    lid = table["members"].get(key)
    if lid is None and cached_profile and not (pdb_file or pick or verify_pdb):
        try:
            with open(cached_profile, encoding="utf-8") as fh:
                cached = json.load(fh)
            if not profile_problem(cached, key) and cached.get("identitySource") == "pdb":
                capture = cached["capture"]
                prologue = capture.get("prologue")
                if prologue and prologue_of(pe, capture["entryRva"], len(prologue) // 2) == prologue:
                    return cached
        except (OSError, ValueError, layout.LayoutError):
            pass
    source = None
    ranked = []
    if lid is None:
        candidates = fingerprint_candidates(table, pe)
        if pick:
            if pick not in table["layouts"]:
                raise layout.LayoutError("unknown layout %s" % pick)
            if pick not in candidates:
                raise layout.LayoutError(
                    "layout %s does not match the image prologue" % pick)
            lid, source = pick, "pick"
        elif len(candidates) == 1 and pdb_bytes is None and not verify_pdb:
            lid, source = candidates[0], "fingerprint"
        else:
            if len(candidates) > 1:
                ranked = rank_fingerprint_candidates(table, candidates, file_version(image_path))
            if offline and verify_pdb and pdb_bytes is None:
                try:
                    with open(os.path.join(cache_dir, guid + ".pdb"), "rb") as fh:
                        pdb_bytes = fh.read()
                except OSError as exc:
                    raise layout.LayoutError(
                        "PDB verification in offline mode requires a cached or imported PDB") from exc
            if offline and pdb_bytes is None:
                if ranked:
                    raise layout.LayoutError(
                        "ambiguous prologue fingerprint (%s); pass --pick or an imported PDB"
                        % ", ".join(ranked))
                raise layout.LayoutError("no prologue fingerprint match; offline needs an imported PDB or profile")
            addr = resolve_addresses(guid, age, name, cache_dir, pdb_bytes)
            lid = next((candidate for candidate in ranked
                        if table["layouts"][candidate]["capture"]["entryRva"] == addr["entryRva"]
                        and table["layouts"][candidate]["capture"]["vtableRva"] == [addr["primary"]]),
                       layout_id(addr["entryRva"], [addr["primary"]]))
            source = "pdb"
            if lid not in table["layouts"]:
                add_build(table, key, addr["entryRva"], addr["primary"], addr["contained"])
    capture = dict(table["layouts"][lid]["capture"])
    if source == "pdb" or not capture.get("prologue"):
        capture["prologue"] = prologue_of(pe, capture["entryRva"])
    profile = {"schema": 1, "symbolKey": key, "layout": lid, "capture": {}}
    if source:
        profile["identitySource"] = source
    if ranked:
        profile["fingerprintCandidates"] = ranked
    profile["capture"].update(CONTRACT)
    profile["capture"]["entryRva"] = capture["entryRva"]
    profile["capture"]["vtableRva"] = capture["vtableRva"]
    profile["capture"]["prologue"] = capture["prologue"]
    return profile



def layout_id(entry_rva, vtables):
    return "L%X_%s" % (entry_rva, "_".join("%X" % v for v in vtables))


def load_table(path):
    if not os.path.exists(path):
        return {"schema": 2, "layouts": {}, "members": {}}
    with open(path, encoding="utf-8") as fh:
        table = json.load(fh)
    if "layouts" not in table:  # migrate the earlier single-profile shape
        table = {"schema": 2, "layouts": {}, "members": table.get("entries", {})}
        for key, entry in table["members"].items():
            capture = entry.get("capture", entry)
            lid = layout_id(capture["entryRva"], sorted(capture.get("vtableRva", [])))
            table["layouts"][lid] = {"capture": capture}
            table["members"][key] = lid
    table.setdefault("layouts", {})
    table.setdefault("members", {})
    table["schema"] = 2
    return table


def save_table(path, table):
    table["members"] = {k: table["members"][k] for k in sorted(table["members"])}
    table["layouts"] = {k: table["layouts"][k] for k in sorted(table["layouts"])}
    text = json.dumps(table, indent=2, sort_keys=True) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def add_build(table, key, entry_rva, primary, contained, prologue=None):
    # Identity uses only what the runtime accepts; the contained form is recorded
    # as a candidate so builds that differ only there still share one layout.
    lid = layout_id(entry_rva, [primary])
    slot = table["layouts"].setdefault(lid, {"capture": {
        "entryRva": entry_rva,
        "vtableRva": [primary],
        "vtableCandidates": [primary, contained],
    }})
    if prologue and not slot["capture"].get("prologue"):
        slot["capture"]["prologue"] = prologue
    reused = table["members"].get(key) == lid
    table["members"][key] = lid
    return lid, reused


# Layout values that are version-independent contract constants. They are
# validated by the runtime canary once per session; the profile only has to
# describe where things are, not how they are laid out.
CONTRACT = {
    "blockSearchStart": -1536,
    "blockSearchEnd": -1280,
    "blockSearchStep": 8,
    "maxBufferIndex": 1,
    "flagOffset": 1496,
    "countOffset": 1492,
    "indexOffset": 1488,
    "descPtrOffset": 1296,
    "descPropSizeOffset": 12,
    "descStrideOffset": 20,
    "propsBase": 40,
    "pcmBase": 56,
    "propStride": 24,
    "frameCountOffset": 4,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", action="append", default=[], help="audiodg.exe (repeatable)")
    parser.add_argument("--rsds", action="append", default=[], help="GUID+age when no image is available")
    parser.add_argument("--pdb-cache", default=os.path.join(os.environ.get("TEMP", "."), "nsp-pdb-cache"))
    parser.add_argument("--table", default="modules/native-capture/profiles/capture-profiles.json")
    parser.add_argument("--select", metavar="EXE", help="write the single profile for this image")
    parser.add_argument("--pdb", help="offline PDB for --select; must match the image RSDS")
    parser.add_argument("--profile", help="offline single-profile JSON for --select; must match the image RSDS")
    parser.add_argument("--offline", action="store_true",
                        help="never fetch a PDB; require a prologue fingerprint match, --pick or --profile")
    parser.add_argument("--pick", metavar="LAYOUT",
                        help="resolve an unknown build to this layout when the image prologue matches")
    parser.add_argument("--out", help="output path for --select")
    parser.add_argument("--verify-pdb", action="store_true",
                        help="resolve unknown RSDS from PDB even with a unique fingerprint")
    parser.add_argument("--reuse-pdb-profile", action="store_true",
                        help="reuse a matching PDB-derived profile already stored at --out")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if (args.pdb or args.profile) and not args.select:
        parser.error("--pdb/--profile require --select")
    if args.pdb and args.profile:
        parser.error("--pdb and --profile are mutually exclusive")
    if (args.offline or args.pick) and not args.select:
        parser.error("--offline/--pick require --select")
    if args.verify_pdb and (not args.select or args.pick):
        parser.error("--verify-pdb requires --select and cannot be combined with --pick")
    if args.reuse_pdb_profile and not (args.select and args.out):
        parser.error("--reuse-pdb-profile requires --select and --out")

    if args.select:
        profile = select_for_image(args.table, args.select, args.pdb_cache,
                                   pdb_file=args.pdb, profile_file=args.profile,
                                   offline=args.offline, pick=args.pick,
                                   verify_pdb=args.verify_pdb,
                                   cached_profile=args.out if args.reuse_pdb_profile else None)
        text = json.dumps(profile, indent=2, sort_keys=True) + "\n"
        if args.out:
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                                 dir=os.path.dirname(os.path.abspath(args.out)),
                                                 delete=False) as fh:
                    tmp = fh.name
                    fh.write(text)
                os.replace(tmp, args.out)
            finally:
                if tmp is not None and os.path.exists(tmp):
                    os.unlink(tmp)
            print("wrote %s (layout %s%s)" % (
                args.out, profile["layout"],
                ", via %s" % profile["identitySource"] if profile.get("identitySource") else ""))
        else:
            print(text)
        return 0
    table = load_table(args.table)
    failures = 0
    for exe in args.exe:
        try:
            guid, age, name, pe = pdb_symbols.read_rsds(exe)
            addr = resolve_addresses(guid, age, name, args.pdb_cache)
            prologue = prologue_of(pe, addr["entryRva"])
            lid, reused = add_build(table, guid + format(age, "X"), addr["entryRva"],
                                    addr["primary"], addr["contained"], prologue)
            record_file_version(table["layouts"][lid], file_version(exe))
            print("OK   %s -> %s%s" % (guid + format(age, "X"), lid, " (existing)" if reused else ""))
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            print("FAIL %s: %s" % (exe, exc))
            failures += 1
    for key in args.rsds:
        try:
            guid, age = key[:-1], int(key[-1], 16)
            addr = resolve_addresses(guid, age, "AudioDG.pdb", args.pdb_cache)
            lid, reused = add_build(table, key, addr["entryRva"], addr["primary"], addr["contained"])
            print("OK   %s -> %s%s%s" % (key, lid, " (existing)" if reused else "",
                                         "" if table["layouts"][lid]["capture"].get("prologue")
                                         else " (no prologue yet)"))
        except Exception as exc:  # noqa: BLE001
            print("FAIL %s: %s" % (key, exc))
            failures += 1

    print("layouts=%d members=%d failures=%d" % (len(table["layouts"]), len(table["members"]), failures))
    if not args.dry_run and not failures:
        save_table(args.table, table)
        print("wrote %s" % args.table)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

