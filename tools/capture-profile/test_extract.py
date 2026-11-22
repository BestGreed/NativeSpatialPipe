#!/usr/bin/env python3
"""Self-check for the fast-table image/update extractor.

Run directly:  python tools/capture-profile/test_extract.py
It builds synthetic cabinets with makecab (a full PE image, a nested MSU and a
delta-only package) and exercises the extractor without any real Windows media.
The ISO reader needs real media; only its WIM selection helper is covered here.
"""
import json
import os
import shutil
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_images

WORK = os.path.join(os.getcwd(), "build", "verify", "extract-test")


def synthetic_image(guid, age):
    size = 0x11000
    image = bytearray(size)
    image[0:2] = b"MZ"
    e_lfanew = 0x80
    struct.pack_into("<I", image, 0x3C, e_lfanew)
    image[e_lfanew:e_lfanew + 4] = b"PE\0\0"
    opt = e_lfanew + 24
    section_table = opt + 0xE0
    struct.pack_into("<HHIIIHH", image, e_lfanew + 4, 0x8664, 1, 0, 0, 0, 0xE0, 0x22)
    struct.pack_into("<H", image, opt, 0x20B)
    struct.pack_into("<I", image, opt + 108, 16)
    struct.pack_into("<II", image, opt + 112 + 6 * 8, 0x10000, 28)
    image[section_table:section_table + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", image, section_table + 8, 0x1000, 0x1000, 0x10000, 0x400)
    debug_raw = 0xF400
    struct.pack_into("<IIHHIIII", image, debug_raw, 0, 0, 0, 0, 2, 0x24, 0x10040, 0xF440)
    rsds = b"RSDS" + guid + struct.pack("<I", age) + b"fixture.pdb\0"
    image[0xF440:0xF440 + len(rsds)] = rsds
    return bytes(image)


def key_of(guid, age):
    d1, d2, d3 = struct.unpack("<IHH", guid[:8])
    return "%08X%04X%04X%s%X" % (d1, d2, d3, guid[8:16].hex().upper(), age)


def make_cab(directory, name, entries):
    ddf = os.path.join(directory, name + ".ddf")
    lines = [".Set CabinetNameTemplate=" + name, ".Set DiskDirectoryTemplate=" + directory,
             ".Set Cabinet=on", ".Set Compress=on"]
    for source, destination in entries:
        lines.append('"%s" "%s"' % (source, destination))
    with open(ddf, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    result = subprocess.run(["makecab", "/F", ddf], cwd=directory,
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError("makecab failed: %s" % (result.stderr or result.stdout))
    return os.path.join(directory, name)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def main():
    if shutil.which("makecab") is None or shutil.which("expand") is None:
        print("SKIP extractor: makecab/expand missing")
        return 0
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    failures = 0

    guid = bytes(range(0x10))
    image_bytes = synthetic_image(guid, 1)
    expected = key_of(guid, 1)
    full = os.path.join(WORK, "full-source.exe")
    write(full, image_bytes)

    full_cab = make_cab(WORK, "full.cab", [(full, "audiodg.exe")])
    out = os.path.join(WORK, "out-full")
    result = extract_images.main(["update", "--package", full_cab, "--out", out])
    manifest = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
    if result == 0 and manifest and manifest[0]["symbolKey"] == expected:
        print("OK   full-file cab yields the expected RSDS image")
    else:
        print("FAIL full-file cab extraction")
        failures += 1

    inner = make_cab(WORK, "inner.cab", [(full, "audiodg.exe")])
    outer = make_cab(WORK, "outer.cab", [(inner, "inner.cab")])
    msu = os.path.join(WORK, "update.msu")
    shutil.copyfile(outer, msu)
    out = os.path.join(WORK, "out-msu")
    result = extract_images.main(["update", "--package", msu, "--out", out])
    if result == 0 and os.path.isfile(os.path.join(out, expected, "audiodg.exe")):
        print("OK   nested MSU package is unwrapped")
    else:
        print("FAIL nested MSU extraction")
        failures += 1

    delta = os.path.join(WORK, "delta-source.exe")
    write(delta, b"not a pe image")
    delta_cab = make_cab(WORK, "delta.cab", [(delta, "audiodg.exe")])
    out = os.path.join(WORK, "out-delta")
    result = extract_images.main(["update", "--package", delta_cab, "--out", out])
    if result == 1 and not os.path.isdir(os.path.join(out, expected)):
        print("OK   delta-only package is rejected with guidance")
    else:
        print("FAIL delta-only package handling")
        failures += 1

    tree = os.path.join(WORK, "tree")
    write(os.path.join(tree, "a", "audiodg.exe"), image_bytes)
    write(os.path.join(tree, "b", "audiodg.exe"), image_bytes)
    out = os.path.join(WORK, "out-tree")
    result = extract_images.main(["scan", "--root", tree, "--out", out])
    manifest = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
    if result == 0 and len(manifest) == 1 and len(manifest[0]["sources"]) == 2:
        print("OK   tree scan deduplicates by RSDS and keeps both sources")
    else:
        print("FAIL tree scan dedupe")
        failures += 1

    members = ["boot/bcd", "sources/install.esd", "sources/install.wim"]
    if extract_images.select_wim_member(members) == "sources/install.esd":
        print("OK   ISO WIM selection prefers the first install.wim/esd member")
    else:
        print("FAIL ISO WIM selection")
        failures += 1

    shutil.rmtree(WORK, ignore_errors=True)
    print("failures=%d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
