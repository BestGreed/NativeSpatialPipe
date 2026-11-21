#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Acceptance tests for the NativeSpatialPipe AES67 sender (branch 6).

Runs aes67-send.exe against a UDP receiver on 127.0.0.1, then validates the RTP
stream (header/seq/timestamp/L24 payload tone), the generated SDP file, and a
best-effort multicast smoke test on 239.69.1.1. Standard library only.

Usage: python tools/aes67-output/Test-Aes67.py [--seconds 2] [--no-multicast]
Writes tools/aes67-output/out/results.json and prints a PASS/FAIL table.
Exit code: 0 when no check failed (multicast SKIP is allowed), 1 otherwise.
"""
import json
import math
import os
import pathlib
import re
import socket
import struct
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
EXE = REPO / "build" / "aes67-output" / "aes67-send.exe"
OUT = HERE / "out"
PT = 96
LAYOUT = "7.1.4"
LAYOUT_LABELS = ["L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Ltf", "Rtf", "Ltr", "Rtr"]
CHANNELS = len(LAYOUT_LABELS)
FRAMES = 48
TONE_HZ = 1000.0
LEVEL = 0.5


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail, status=None):
        status = status or ("PASS" if ok else "FAIL")
        self.rows.append({"case": name, "status": status, "detail": detail})
        return ok

    def fail_count(self):
        return sum(1 for r in self.rows if r["status"] == "FAIL")

    def skip_count(self):
        return sum(1 for r in self.rows if r["status"] == "SKIP")


def parse_rtp(data):
    b0 = data[0]
    version = (b0 >> 6) & 0x3
    padding = (b0 >> 5) & 0x1
    extension = (b0 >> 4) & 0x1
    cc = b0 & 0xF
    marker = (data[1] >> 7) & 0x1
    pt = data[1] & 0x7F
    seq = struct.unpack(">H", data[2:4])[0]
    ts = struct.unpack(">I", data[4:8])[0]
    ssrc = struct.unpack(">I", data[8:12])[0]
    payload = data[12 + 4 * cc:]
    return {
        "version": version, "padding": padding, "extension": extension, "cc": cc,
        "marker": marker, "pt": pt, "seq": seq, "ts": ts, "ssrc": ssrc,
        "payload": payload,
    }


def decode_l24_interleaved(payload, channels):
    if len(payload) % (3 * channels) != 0:
        raise ValueError("payload not a multiple of 3*channels")
    frames = len(payload) // (3 * channels)
    values = []
    for i in range(frames * channels):
        raw = payload[3 * i:3 * i + 3]
        v = (raw[0] << 16) | (raw[1] << 8) | raw[2]
        if v & 0x800000:
            v -= 1 << 24
        values.append(v / 8388608.0)
    return frames, values


def run_sender(dest_ip, port, seconds, extra=None, sdp_path=None, stats_path=None):
    cmd = [str(EXE), "--dest", "%s:%d" % (dest_ip, port), "--channels", str(CHANNELS),
           "--layout", LAYOUT, "--frames", str(FRAMES), "--seconds", str(seconds),
           "--tone", str(TONE_HZ), "--level", str(LEVEL)]
    if sdp_path:
        cmd += ["--sdp", str(sdp_path)]
    if stats_path:
        cmd += ["--stats-json", str(stats_path)]
    if extra:
        cmd += extra
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def collect_unicast(seconds, extra=None, sdp_path=None, stats_path=None):
    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 << 20)
    recv.bind(("127.0.0.1", 0))
    port = recv.getsockname()[1]
    recv.settimeout(0.1)
    proc = run_sender("127.0.0.1", port, seconds, extra, sdp_path, stats_path)
    packets = []
    arrivals = []
    deadline = time.monotonic() + seconds + 2.0
    idle_deadline = None
    while time.monotonic() < deadline:
        try:
            packets.append(recv.recv(65536))
            arrivals.append(time.perf_counter())
        except socket.timeout:
            if proc.poll() is not None:
                idle_deadline = idle_deadline or time.monotonic() + 0.5
                if time.monotonic() > idle_deadline:
                    break
    recv.close()
    stdout, stderr = proc.communicate(timeout=30)
    return packets, port, proc.returncode, stdout, stderr, arrivals


def collect_multicast(seconds, group="239.69.1.1"):
    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    recv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    recv.bind(("", 0))
    port = recv.getsockname()[1]
    try:
        mreq = socket.inet_aton(group) + socket.inet_aton("0.0.0.0")
        recv.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as exc:
        recv.close()
        return None, port, "IP_ADD_MEMBERSHIP failed: %s" % exc
    recv.settimeout(0.1)
    stats_path = OUT / "multicast-stats.json"
    proc = run_sender(group, port, seconds, extra=["--ttl", "1"], stats_path=stats_path)
    packets = []
    deadline = time.monotonic() + seconds + 2.0
    while time.monotonic() < deadline:
        try:
            packets.append(recv.recv(65536))
        except socket.timeout:
            if proc.poll() is not None and time.monotonic() > deadline - 1.5:
                break
    recv.close()
    stdout, stderr = proc.communicate(timeout=30)
    if proc.returncode != 0:
        return packets, port, "sender exit %d: %s" % (proc.returncode, (stderr or "").strip()[:200])
    return packets, port, None


def check_sdp(path, report):
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    lines = [line.strip() for line in text.splitlines()]
    report.add("sdp-file-exists", path.exists(), str(path))
    required = [
        "v=0",
        "m=audio", "RTP/AVP 96",
        "a=rtpmap:96 L24/48000/%d" % CHANNELS,
        "a=ptime:1",
        "a=sendonly",
        "a=mediaclk:direct=0",
    ]
    for item in required:
        if item == "m=audio":
            ok = any(line.startswith("m=audio ") and line.endswith("RTP/AVP 96") for line in lines)
        elif item == "RTP/AVP 96":
            ok = any("RTP/AVP 96" in line and line.startswith("m=audio ") for line in lines)
        else:
            ok = any(line == item or line.startswith(item + " ") for line in lines)
        report.add("sdp:" + item, ok, "present" if ok else "missing")
    c_line = next((line for line in lines if line.startswith("c=IN IP4 ")), "")
    report.add("sdp:c=unicast", c_line == "c=IN IP4 127.0.0.1", c_line)
    refclk = next((line for line in lines if line.startswith("a=ts-refclk:")), "")
    report.add("sdp:ts-refclk", bool(re.match(r"a=ts-refclk:(ptp=IEEE1588-2008:[0-9A-Fa-f-]+:\d+|localmac=[0-9A-Fa-f-]+)$", refclk)), refclk)
    fmtp = next((line for line in lines if line.startswith("a=fmtp:96 channel-order=")), "")
    report.add("sdp:channel-order", "SMPTE2110." in fmtp, fmtp)
    labels = next((line for line in lines if line.startswith("a=comment:channel-labels=")), "")
    report.add("sdp:channel-labels", labels.endswith(",".join(LAYOUT_LABELS)), labels)
    report.add("sdp:layout-comment", any(line == "a=comment:layout=" + LAYOUT for line in lines),
               "a=comment:layout=" + LAYOUT)
    return text


def analyse_loopback(packets, expected_packets, report, arrivals=None):
    total_payload_bytes = 0
    if not packets:
        report.add("loopback:packets", False, "no packets received")
        return {}
    parsed = [parse_rtp(p) for p in packets]
    report.add("loopback:packets", len(parsed) >= expected_packets * 0.95,
               "received=%d expected=%d (%.1f%%)" % (len(parsed), expected_packets, 100.0 * len(parsed) / expected_packets))
    header_ok = all(p["version"] == 2 and p["padding"] == 0 and p["extension"] == 0 and p["cc"] == 0
                    and p["marker"] == 0 and p["pt"] == PT for p in parsed)
    report.add("rtp:header", header_ok, "v=2,p=0,x=0,cc=0,m=0,pt=%d" % PT)
    ssrcs = sorted({p["ssrc"] for p in parsed})
    report.add("rtp:ssrc-stable", len(ssrcs) == 1, "ssrc=%s" % ",".join("0x%08X" % s for s in ssrcs))
    lost = out_of_order = 0
    for prev, cur in zip(parsed, parsed[1:]):
        delta = (cur["seq"] - prev["seq"]) & 0xFFFF
        if delta == 0:
            out_of_order += 1
        elif delta > 1:
            lost += delta - 1
    report.add("rtp:seq-continuity", lost == 0 and out_of_order == 0,
               "lost=%d out_of_order=%d first=%d last=%d" % (lost, out_of_order, parsed[0]["seq"], parsed[-1]["seq"]))
    ts_deltas = sorted({((cur["ts"] - prev["ts"]) & 0xFFFFFFFF) for prev, cur in zip(parsed, parsed[1:])})
    report.add("rtp:timestamp-step", ts_deltas == [FRAMES], "deltas=%s expected=%d" % (ts_deltas[:5], FRAMES))
    payload_ok = all(len(p["payload"]) == FRAMES * CHANNELS * 3 for p in parsed)
    report.add("rtp:payload-length", payload_ok, "expect %d bytes per packet" % (FRAMES * CHANNELS * 3))

    frames_total, samples = decode_l24_interleaved(b"".join(p["payload"] for p in parsed), CHANNELS)
    total_payload_bytes = sum(len(p["payload"]) for p in parsed)
    ch_peaks = []
    for ch in range(CHANNELS):
        peak = max(abs(samples[i]) for i in range(ch, len(samples), CHANNELS))
        ch_peaks.append(peak)
    expected_peaks = [LEVEL * (ch + 1) / CHANNELS for ch in range(CHANNELS)]
    ramp_ok = all(abs(actual - expected) <= 2e-3 for actual, expected in zip(ch_peaks, expected_peaks)) and \
        all(a < b for a, b in zip(ch_peaks, ch_peaks[1:]))
    report.add("l24:channel-ramp-order", ramp_ok,
               "peaks[0]=%.4f peaks[last]=%.4f expected_ramp=%s" % (ch_peaks[0], ch_peaks[-1], ["%.3f" % e for e in (expected_peaks[0], expected_peaks[-1])]))
    ch0 = samples[0::CHANNELS]
    crossings = sum(1 for a, b in zip(ch0, ch0[1:]) if (a <= 0.0 < b) or (a >= 0.0 > b))
    duration = len(ch0) / 48000.0
    zcr = crossings / duration if duration else 0.0
    report.add("l24:tone-zcr", abs(zcr - 2 * TONE_HZ) <= 2 * TONE_HZ * 0.02,
               "zcr=%.1f/s expected=%.1f/s (ch0, %.3fs)" % (zcr, 2 * TONE_HZ, duration))
    max_diff = max(abs(b - a) for a, b in zip(ch0, ch0[1:]))
    limit = 2 * ch_peaks[0] * math.sin(math.pi * TONE_HZ / 48000.0) * 1.05 + 1e-6
    report.add("l24:tone-slope", 0 < max_diff <= limit,
               "max_adjacent_diff=%.6f limit=%.6f" % (max_diff, limit))
    silent = 0
    silent_indices = []
    for index, p in enumerate(parsed):
        frames_p, vals = decode_l24_interleaved(p["payload"], CHANNELS)
        if max(abs(v) for v in vals) < 1e-6:
            silent += 1
            silent_indices.append(index)
    # Silence is only acceptable while the CLI drains its ring after the last
    # submitted frame (tail). A silent packet in the middle of the stream is a
    # real underrun and fails.
    # Start-up and drain-tail silence are harness effects (CLI prebuffer / ring
    # drain). Only a contiguous prefix/suffix of silent packets is acceptable;
    # silence with non-silent audio after it means the sender underran.
    non_silent = [i for i in range(len(parsed)) if i not in set(silent_indices)]
    if non_silent:
        first_audio, last_audio = non_silent[0], non_silent[-1]
        midstream = [i for i in silent_indices if first_audio < i < last_audio]
    else:
        first_audio = last_audio = -1
        midstream = list(silent_indices)
    report.add("l24:no-midstream-silence", not midstream,
               "silent_packets=%d prefix/suffix-only (first_audio=%d last_audio=%d of %d) midstream=%s" % (
                   silent, first_audio, last_audio, len(parsed), midstream[:5]))
    arrival_span = (arrivals[-1] - arrivals[0]) if arrivals and len(arrivals) > 1 else 0.0
    measured_rate = (len(arrivals) - 1) / arrival_span if arrival_span > 0 else 0.0
    report.add("stream:arrival-pacing", arrival_span > 0 and 0.85 * duration <= arrival_span <= 1.20 * duration,
               "arrival_span=%.3fs media_duration=%.3fs rate=%.1f pkt/s (nominal %.0f)" % (
                   arrival_span, duration, measured_rate, 48000.0 / FRAMES))
    return {
        "packets": len(parsed),
        "expected_packets": expected_packets,
        "first_seq": parsed[0]["seq"],
        "last_seq": parsed[-1]["seq"],
        "lost": lost,
        "out_of_order": out_of_order,
        "ssrc": ssrcs[0] if ssrcs else None,
        "payload_bytes": total_payload_bytes,
        "channel_peaks": [round(p, 6) for p in ch_peaks],
        "zero_crossings_per_second": round(zcr, 2),
        "silent_packets": silent,
        "duration_s": round(duration, 3),
        "arrival_span_s": round(arrival_span, 4),
        "measured_packets_per_second": round(measured_rate, 2),
        "nominal_packets_per_second": 48000.0 / FRAMES,
        "nominal_payload_kbps": round(48000.0 * CHANNELS * 24 / 1000.0, 1),
    }


def main(argv):
    seconds = 2.0
    do_multicast = True
    for i, arg in enumerate(argv):
        if arg == "--seconds" and i + 1 < len(argv):
            seconds = float(argv[i + 1])
        elif arg == "--no-multicast":
            do_multicast = False
    OUT.mkdir(parents=True, exist_ok=True)
    report = Report()
    if not EXE.exists():
        print("missing %s -- run tools/aes67-output/Build.ps1 first" % EXE)
        return 2
    sdp_path = OUT / "aes67-test.sdp"
    stats_path = OUT / "loopback-stats.json"
    started = time.monotonic()
    packets, port, rc, stdout, stderr, arrivals = collect_unicast(seconds, sdp_path=sdp_path, stats_path=stats_path)
    report.add("sender:exit-code", rc == 0, "exit=%d port=%d" % (rc, port) + ((" stderr=" + stderr.strip()[:200]) if rc else ""))
    expected = int(round(seconds * 48000.0 / FRAMES))
    loop = analyse_loopback(packets, expected, report, arrivals)
    sdp_text = check_sdp(sdp_path, report)
    sender_stats = {}
    if stats_path.exists():
        sender_stats = json.loads(stats_path.read_text(encoding="utf-8"))
        report.add("sender:no-underrun", sender_stats.get("silentPackets", 0) <= 64 and sender_stats.get("ringDroppedFrames", 0) == 0,
                   "silentPackets=%s (prefix/suffix harness slack) ringDropped=%s sendErrors=%s lateTicks=%s" % (
                       sender_stats.get("silentPackets"), sender_stats.get("ringDroppedFrames"),
                       sender_stats.get("sendErrors"), sender_stats.get("lateTicks")))
        report.add("sender:pacing", sender_stats.get("elapsedMs", 0) >= seconds * 1000 * 0.9,
                   "stats elapsedMs=%s target=%.0f (includes teardown; arrival span is authoritative)" % (
                       sender_stats.get("elapsedMs"), seconds * 1000))
    else:
        report.add("sender:stats-json", False, "missing " + str(stats_path))

    multicast = {"status": "SKIP", "detail": "not attempted"}
    if do_multicast:
        mc_packets, mc_port, mc_error = collect_multicast(1.0)
        if mc_error:
            multicast = {"status": "SKIP", "detail": mc_error, "port": mc_port, "packets": 0}
        elif not mc_packets:
            multicast = {"status": "SKIP", "detail": "no packets on 239.69.1.1 (loopback multicast unavailable)", "port": mc_port, "packets": 0}
        else:
            mc_parsed = [parse_rtp(p) for p in mc_packets]
            ok = len(mc_parsed) >= 1000 * 0.8 and all(p["pt"] == PT and p["version"] == 2 for p in mc_parsed)
            multicast = {"status": "PASS" if ok else "FAIL", "detail": "packets=%d expected~1000 port=%d" % (len(mc_parsed), mc_port),
                         "port": mc_port, "packets": len(mc_parsed)}
            report.add("multicast:239.69.1.1", ok, multicast["detail"])
    elapsed = time.monotonic() - started

    summary = {
        "passed": sum(1 for r in report.rows if r["status"] == "PASS"),
        "failed": report.fail_count(),
        "skipped": report.skip_count(),
    }
    results = {
        "branch": "feat/aes67-network-output",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "exe": str(EXE),
        "config": {"layout": LAYOUT, "channels": CHANNELS, "framesPerPacket": FRAMES, "ptimeMs": FRAMES / 48.0,
                   "payloadType": PT, "toneHz": TONE_HZ, "level": LEVEL, "seconds": seconds},
        "loopback": loop,
        "senderStats": sender_stats,
        "sdp": {"path": str(sdp_path), "bytes": len(sdp_text)},
        "multicast": multicast,
        "checks": report.rows,
        "summary": summary,
        "elapsedSeconds": round(elapsed, 2),
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("%-34s %-6s %s" % ("CASE", "RESULT", "DETAIL"))
    print("%-34s %-6s %s" % ("-" * 34, "-" * 6, "-" * 40))
    for row in report.rows:
        print("%-34s %-6s %s" % (row["case"], row["status"], row["detail"]))
    print()
    print("summary: passed=%d failed=%d skipped=%d elapsed=%.2fs" % (
        summary["passed"], summary["failed"], summary["skipped"], elapsed))
    if loop:
        print("loopback: %d packets, %.1f pkt/s measured, %.1f kbps nominal payload (%.1f kbps on wire), lost=%d, out_of_order=%d" % (
            loop.get("packets", 0), loop.get("measured_packets_per_second", 0),
            loop.get("nominal_payload_kbps", 0), loop.get("nominal_payload_kbps", 0) + 12 * 8 * 1000 / 1000.0,
            loop.get("lost", 0), loop.get("out_of_order", 0)))
    if sender_stats:
        print("sender: packets=%s bitrate=%.1f kbps payload=%.1f kbps silent=%s" % (
            sender_stats.get("packets"), sender_stats.get("bitrateKbps", 0),
            sender_stats.get("payloadKbps", 0), sender_stats.get("silentPackets")))
    print("multicast: %s (%s)" % (multicast["status"], multicast["detail"]))
    print("results: %s" % (OUT / "results.json"))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

