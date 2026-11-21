# tools/aes67-output — standalone AES67 (RTP/L24) transmitter

Status: **implemented, tested standalone, and wired into the NSP runtime** as the
`outputBackend: "aes67"` output backend (see [INTEGRATION.md](INTEGRATION.md)).
The standalone CLI (below) remains the loopback/multicast acceptance tool.

Deliverables:

| File | Purpose |
|---|---|
| `aes67_tx.c/.h` | RTP/AVP L24 sender with bounded f32 ring and high-resolution paced thread; C ABI `nsp_aes67_create/start/submit/stop/destroy/get_stats` |
| `aes67_sdp.c/.h` | SDP generation (RTP/AVP 96, L24/48000/N, ptime, ts-refclk, mediaclk, informative channel-order) |
| `aes67_send.c` | `aes67-send.exe` CLI tone generator for loopback/multicast testing |
| `aes67_layouts.h` | generated from `contracts/output-layouts.json` by `gen-layouts.py` (single-source layout labels) |
| `gen-layouts.py` | regenerates the header (run automatically by Build.ps1) |
| `Build.ps1` | standalone gcc build into `build/aes67-output/` |
| `Build-NspAes67Dll.ps1` | builds `nsp_aes67.dll` (the NSP scheduler loads it at runtime) |
| `Test-Aes67.py` | stdlib acceptance test: RTP/seq/timestamp/L24/SDP/multicast; writes `out/results.json` |

Build and test (from the repo root):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/aes67-output/Build.ps1
python tools/aes67-output/Test-Aes67.py
```

CLI example:

```powershell
build/aes67-output/aes67-send.exe --dest 127.0.0.1:5004 --channels 12 --packet-ms 1 --seconds 2 --tone 1000 --sdp out.sdp --stats-json out/stats.json
```

Wire format: RTP version 2, marker 0, payload type 96, 12-byte header,
payload = interleaved **L24 big-endian** PCM at 48 kHz. The C ABI takes
**interleaved float32 in [-1, 1]** and converts internally; int32 input is not
offered (avoid two input variants in a prototype). Each packet carries
`frames_per_packet` frames (48/96/192/480 = 1/2/4/10 ms) and the RTP timestamp
advances by exactly that many frames per packet.

Documented behaviours (also covered by tests):

* **Underrun policy**: when the ring cannot supply a full packet the sender
  transmits a packet of L24 silence and increments `silent_packets`. It never
  blocks the submit path. The test only accepts silence as a contiguous
  prefix/suffix of the stream (start prebuffer / drain tail), never mid-stream.
* **Bounded ring**: `ring_packets` slots (library default 8, CLI uses 64). On
  overflow the **oldest** frames are dropped and counted (`ring_dropped_frames`)
  so latency stays bounded; playback-style "drop newest" is not used.
* **Pacing**: an absolute QPC timeline with bounded catch-up bursts (max 64
  packet periods) so OS wake-up delays under CPU contention do not accumulate
  into clock drift. `late_ticks` counts timeline resyncs beyond the burst bound.
* **Multicast**: `IP_MULTICAST_TTL`/`IP_MULTICAST_LOOP` are set; optional
  `IP_MULTICAST_IF` via `src_interface_ip`. Unicast uses plain `sendto`.

Not implemented here (see `docs/core/aes67-output.md` `8): PTP slave, RTCP,
ST 2022-7 redundancy, SDP/offer-answer negotiation, 44.1/96 kHz, DSCP, SAP.