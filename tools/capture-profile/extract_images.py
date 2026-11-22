#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract audiodg.exe images for the capture-profile fast table.

Two inputs are supported, matching the release pipeline:

    iso     Windows install media (Windows 10/11). The ISO is read with bsdtar
            (no mount, no admin), then audiodg.exe is taken from install.wim or
            install.esd. WIM/ESD needs wimlib-imagex, or DISM with elevation.
    update  A cumulative-update package: .msu, .cab, or a UUP package directory.
            Only full-file packages are usable; LCU cabs that contain forward/
            reverse differentials (`f\\audiodg.exe`, `r\\audiodg.exe`) cannot
            produce an image without the matching base (use a UUP set instead).

Every image is stored under `<out>/<RSDS>/audiodg.exe` with a source record, and
a combined `manifest.json` is written. The extractor never guesses an identity:
a candidate must be a PE image with a CodeView RSDS record.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pdb_symbols

WIM_NAMES = ("install.wim", "install.esd")
IMAGE_RELATIVE = os.path.join("Windows", "System32", "audiodg.exe")
MIN_IMAGE_BYTES = 64 * 1024
MAX_CAB_DEPTH = 3
MACHINES = {"x64": 0x8664, "arm64": 0xAA64, "x86": 0x14C}


class ExtractError(Exception):
    pass


def tool(name):
    path = shutil.which(name)
    if not path:
        raise ExtractError("required tool not found: %s" % name)
    return path


def run(args, timeout=900, cwd=None):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def image_identity(path):
    """Return the RSDS key of a full PE image, or None for deltas/other files."""
    try:
        if os.path.getsize(path) < MIN_IMAGE_BYTES:
            return None
        with open(path, "rb") as fh:
            head = fh.read(0x40)
        if len(head) < 0x40 or head[:2] != b"MZ":
            return None
        offset = int.from_bytes(head[0x3C:0x40], "little")
        with open(path, "rb") as fh:
            fh.seek(offset)
            if fh.read(4) != b"PE\0\0":
                return None
        guid, age, _name, _pe = pdb_symbols.read_rsds(path)
        return guid + format(age, "X")
    except (OSError, layout.LayoutError, ValueError):
        return None


def pe_machine(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(0x40)
            offset = int.from_bytes(head[0x3C:0x40], "little")
            fh.seek(offset + 4)
            return int.from_bytes(fh.read(2), "little")
    except OSError:
        return 0


def arch_matches(path, arch):
    return arch is None or pe_machine(path) == MACHINES[arch]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def store(out, image_path, source, manifest):
    key = image_identity(image_path)
    if not key:
        raise ExtractError("not a full PE image: %s" % image_path)
    digest = sha256(image_path)
    record = {
        "symbolKey": key,
        "sha256": digest,
        "bytes": os.path.getsize(image_path),
        "image": os.path.join(key, "audiodg.exe"),
        "sources": [source],
        "extractedAt": datetime.now(timezone(timedelta(hours=8))).isoformat(),
    }
    existing = next((item for item in manifest if item["symbolKey"] == key), None)
    if existing:
        if existing["sha256"] != digest:
            raise ExtractError("RSDS %s produced two different images" % key)
        existing["sources"].append(source)
        return False
    target = os.path.join(out, key)
    os.makedirs(target, exist_ok=True)
    shutil.copyfile(image_path, os.path.join(target, "audiodg.exe"))
    with open(os.path.join(target, "source.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    manifest.append(record)
    print("OK   %s <- %s" % (key, source.get("package", source.get("kind"))))
    return True


def cab_entries(cab):
    """Member names as stored in the cabinet (`expand -D` prefixes a path)."""
    expand = tool("expand")
    result = run([expand, "-D", cab], timeout=300)
    if result.returncode:
        return []
    names = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        _head, separator, tail = line.rpartition(": ")
        name = tail.strip().strip('"') if separator else ""
        if name and name not in names:
            names.append(name)
    return names


def unpack(cab, dest, members=None):
    """Expand cabinet members to individual files.

    `expand` names the output after the cabinet when it sees a single-member
    cabinet and a directory destination, so every member gets its own
    destination file path and a wildcard fallback for that case.
    """
    expand = tool("expand")
    os.makedirs(dest, exist_ok=True)
    selected = members if members is not None else cab_entries(cab)
    extracted = []
    for index, member in enumerate(selected):
        folder = os.path.join(dest, "member-%d" % index)
        os.makedirs(folder, exist_ok=True)
        name = os.path.basename(member.replace("\\", "/"))
        target = os.path.join(folder, name)
        result = run([expand, "-F:" + member, cab, target], timeout=1800)
        if result.returncode or not os.path.isfile(target):
            result = run([expand, "-F:*", cab, folder], timeout=1800)
            files = [os.path.join(base, item) for base, _dirs, names in os.walk(folder)
                     for item in names]
            if result.returncode or not files:
                raise ExtractError("expand failed for %s in %s: %s" % (
                    member, cab, (result.stderr or result.stdout).strip()[:300]))
            target = files[0]
        extracted.append((member, target))
    return extracted


def scan_update(path, out, manifest, arch="x64"):
    """Extract audiodg.exe from a .msu/.cab/UUP directory."""
    work = os.path.join(out, ".work-update")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    scans = [0]

    def scan_dir():
        scans[0] += 1
        path = os.path.join(work, "scan-%d" % scans[0])
        os.makedirs(path, exist_ok=True)
        return path
    try:
        delta_only = []
        found = []

        def scan_cab(cab, depth):
            if depth > MAX_CAB_DEPTH:
                return []
            entries = cab_entries(cab)
            images = []
            candidates = [name for name in entries
                          if os.path.basename(name.replace("\\", "/")).lower() == "audiodg.exe"]
            if candidates:
                target = scan_dir()
                for member, extracted in unpack(cab, target, candidates):
                    key = image_identity(extracted)
                    if not key:
                        delta_only.append(member)
                    elif arch_matches(extracted, arch):
                        images.append((extracted, key))
            # A package cab can contain further cabs; recurse within the limit.
            nested = [name for name in entries if name.lower().endswith(".cab")]
            if nested:
                sub = scan_dir()
                for _member, extracted in unpack(cab, sub, nested):
                    if extracted.lower().endswith(".cab"):
                        images.extend(scan_cab(extracted, depth + 1))
            return images

        packages = []
        if os.path.isdir(path):
            packages = [os.path.join(path, name) for name in sorted(os.listdir(path))
                        if name.lower().endswith(".cab")]
        elif path.lower().endswith(".msu"):
            nested = [name for name in cab_entries(path) if name.lower().endswith(".cab")]
            if not nested:
                raise ExtractError("MSU contains no .cab package")
            packages = [target for _member, target in unpack(path, work, nested)]
        elif path.lower().endswith(".cab"):
            packages = [path]
        else:
            raise ExtractError("update input must be .msu, .cab or a UUP cab directory")
        if not packages:
            raise ExtractError("no .cab packages found in %s" % path)
        for cab in packages:
            found.extend(scan_cab(cab, 0))
        if not found:
            if delta_only:
                raise ExtractError(
                    "only differential audiodg.exe found (%s); LCU deltas need the "
                    "matching base image - use a UUP full-file package instead"
                    % ", ".join(sorted(set(delta_only))[:3]))
            raise ExtractError("no audiodg.exe in %s" % path)
        for candidate, _key in found:
            store(out, candidate, {"kind": "update", "package": path,
                                   "member": os.path.relpath(candidate, work)}, manifest)
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


def iso_members(iso):
    tar = tool("tar")
    result = run([tar, "-tf", iso], timeout=300)
    if result.returncode:
        raise ExtractError("cannot list ISO with bsdtar: %s" % (
            result.stderr or result.stdout).strip()[:300])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def extract_wim(wim, index, dest):
    os.makedirs(dest, exist_ok=True)
    wimlib = shutil.which("wimlib-imagex")
    if wimlib:
        for member in ("/Windows/System32/audiodg.exe", "Windows\\System32\\audiodg.exe"):
            result = run([wimlib, "extract", wim, str(index), "--dest-dir", dest, member],
                         timeout=1800)
            candidate = os.path.join(dest, "Windows", "System32", "audiodg.exe")
            if result.returncode == 0 and os.path.isfile(candidate):
                return "wimlib"
        raise ExtractError("wimlib could not extract audiodg.exe from %s" % wim)
    dism = tool("dism")
    mount = dest + "-mount"
    os.makedirs(mount, exist_ok=True)
    try:
        result = run([dism, "/Mount-Image", "/ImageFile:" + wim, "/Index:" + str(index),
                      "/MountDir:" + mount, "/ReadOnly", "/Quiet"], timeout=1800)
        if result.returncode:
            raise ExtractError(
                "DISM cannot mount the WIM (elevation required, or install wimlib-imagex): %s"
                % (result.stdout or result.stderr).strip()[:400])
        source = os.path.join(mount, "Windows", "System32", "audiodg.exe")
        if not os.path.isfile(source):
            raise ExtractError("audiodg.exe missing inside WIM index %d" % index)
        target = os.path.join(dest, "Windows", "System32")
        os.makedirs(target, exist_ok=True)
        shutil.copyfile(source, os.path.join(target, "audiodg.exe"))
        return "dism"
    finally:
        if os.path.isdir(mount):
            run([dism, "/Unmount-Image", "/MountDir:" + mount, "/Discard", "/Quiet"], timeout=600)
            shutil.rmtree(mount, ignore_errors=True)


def select_wim_member(members):
    for name in members:
        if os.path.basename(name.replace("\\", "/")).lower() in WIM_NAMES:
            return name
    return None


def scan_iso(iso, out, index, manifest, arch="x64"):
    members = iso_members(iso)
    selected = select_wim_member(members)
    if not selected:
        raise ExtractError("ISO has no sources/install.wim or install.esd")
    work = os.path.join(out, ".work-iso")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    try:
        result = run([tool("tar"), "-xf", iso, "-C", work, selected], timeout=1800)
        if result.returncode:
            raise ExtractError("cannot extract %s from ISO: %s" % (
                selected, (result.stderr or result.stdout).strip()[:300]))
        wim = os.path.join(work, selected.replace("/", os.sep))
        if not os.path.isfile(wim):
            raise ExtractError("ISO extraction produced no %s" % selected)
        extracted = os.path.join(work, "extracted")
        os.makedirs(extracted, exist_ok=True)
        method = extract_wim(wim, index, extracted)
        image = os.path.join(extracted, IMAGE_RELATIVE)
        if not os.path.isfile(image):
            raise ExtractError("WIM extraction produced no audiodg.exe")
        store(out, image, {"kind": "iso", "package": iso, "member": selected,
                           "index": index, "method": method}, manifest)
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


def scan_tree(root, out, manifest, arch="x64"):
    found = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            if name.lower() != "audiodg.exe":
                continue
            path = os.path.join(base, name)
            if not image_identity(path) or not arch_matches(path, arch):
                continue
            if store(out, path, {"kind": "tree", "package": path}, manifest):
                found += 1
    if not found and not manifest:
        raise ExtractError("no audiodg.exe images under %s" % root)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("iso", "update"):
        item = sub.add_parser(name)
        item.add_argument("--package", required=True)
        item.add_argument("--out", required=True)
        item.add_argument("--index", type=int, default=1)
        item.add_argument("--arch", choices=("x64", "arm64", "x86"), default="x64")
    scan = sub.add_parser("scan")
    scan.add_argument("--root", required=True)
    scan.add_argument("--out", required=True)
    scan.add_argument("--arch", choices=("x64", "arm64", "x86"), default="x64")
    args = parser.parse_args(argv)

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    manifest_path = os.path.join(out, "manifest.json")
    manifest = []
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    try:
        if args.command == "iso":
            scan_iso(args.package, out, args.index, manifest, args.arch)
        elif args.command == "update":
            scan_update(args.package, out, manifest, args.arch)
        else:
            scan_tree(args.root, out, manifest, args.arch)
    except ExtractError as exc:
        print("FAIL %s" % exc)
        result = {"ok": False, "command": args.command,
                  "package": getattr(args, "package", None) or getattr(args, "root", ""),
                  "error": str(exc), "images": [item["symbolKey"] for item in manifest]}
        print(json.dumps(result, indent=2))
        return 1
    manifest.sort(key=lambda item: item["symbolKey"])
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    result = {"ok": True, "command": args.command,
              "package": getattr(args, "package", None) or getattr(args, "root", ""),
              "images": [{"symbolKey": item["symbolKey"], "bytes": item["bytes"],
                          "sources": len(item["sources"])} for item in manifest],
              "manifest": manifest_path}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
