# AES67 RTP output transport

Status: implemented on the public `alpha-dev` base as the fourth output
transport (`outputBackend: "aes67"`) next to ASIO, native AoIP and WASAPI. It is
a **transport only** mode: rendering, spatial DSP and channel layout stay in the
existing pipeline. AES67 never downmixes, upmixes or reorders channels on its
own. The configuration page edits the `aes67` block (including the explicit
per-flow rows) and the signal-path panel renders the live sender
health/counters from the supported web status endpoint.

- Schema: [playback.schema.json](../modules/zig-scheduler/playback.schema.json)
- Example: [playback.aes67.example.json](../modules/zig-scheduler/playback.aes67.example.json)
- Runtime module: [aes67_config.zig](../modules/zig-scheduler/aes67_config.zig),
  [aes67_rtp.zig](../modules/zig-scheduler/aes67_rtp.zig),
  [aes67_tx.zig](../modules/zig-scheduler/aes67_tx.zig),
  [aes67_control.zig](../modules/zig-scheduler/aes67_control.zig),
  [aes67_playback.zig](../modules/zig-scheduler/aes67_playback.zig)
- Tests: [aes67_config_tests.zig](../modules/zig-scheduler/aes67_config_tests.zig),
  [aes67_rtp_tests.zig](../modules/zig-scheduler/aes67_rtp_tests.zig),
  [aes67_tx_tests.zig](../modules/zig-scheduler/aes67_tx_tests.zig),
  [playback_schema_tests.zig](../modules/zig-scheduler/playback_schema_tests.zig)

## 1. Scope

In scope:

- RTP/AVP payload type 96 (L24), 48 kHz, big-endian interleaved PCM, 48 frames
  per packet (1 ms).
- Unicast or class-D multicast UDP destinations, an explicit per-flow split
  (1–8 flows, each carrying 1–8 channels whose offsets are contiguous and sum
  to the layout channel count), per-flow/session source-interface bind, SDP
  generation.
- A bounded, paced sender fed by the same Rust output-pool leases as the other
  backends.
- Session-scoped health/report JSON, validated configuration, offline tests.
- Web configuration panel for the `aes67` block and live sender status in the
  signal-path panel of the existing web UI.

Out of scope for this step:

- PTP slave/timebase discipline, RTCP/SAP, SDP offer/answer, ST 2022-7
  redundancy, DSCP/QoS, sample rates other than 48 kHz.
- A persistent/resident AES67 process (the transport currently lives in the
  session host, like ASIO/WASAPI; resident AoIP is a separate design).
- Any layout, downmix or channel-mapping policy beyond the existing
  `outputLayout`/`routing` configuration.

## 2. Where it plugs in

```text
Rust renderer + common DSP (48 kHz, outputLayout channels)
  -> output_stream.Stream lease ring (unchanged)
  -> output_control.Worker(aes67_control.Backend) owner thread
       pump() pops up to 4 leases, applies the configured source-channel map,
       aes67_tx.submit() copies into the bounded float ring, releases the lease
  -> aes67_tx sender thread (absolute QPC timeline, 1 ms periods)
       build RTP header + L24 payload, sendto() UDP (unicast or multicast per flow)
  -> receiver
```

The hand-off rule is identical to ASIO/WASAPI: the lease is a borrowed pointer,
the backend copies it, and the renderer never waits on the network. Only the
device side changes.

## 3. Configuration

```json
{
  "outputBackend": "aes67",
  "bridgeDll": "C:/path/to/nsp_croksor.dll",
  "outputLayout": "7.1.4",
  "aes67": {
    "framesPerPacket": 48,
    "payloadType": 96,
    "ssrc": 0,
    "srcInterfaceIp": "192.0.2.1",
    "ptpDomain": 0,
    "ttl": 32,
    "ringPackets": 64,
    "prebufferFrames": 480,
    "flows": [
      {"destIp": "192.0.2.20", "port": 5004, "channels": 8, "channelOffset": 0,
       "ttl": 32, "srcInterfaceIp": "192.0.2.1"},
      {"destIp": "192.0.2.20", "port": 5006, "channels": 4, "channelOffset": 8,
       "ttl": 32, "srcInterfaceIp": "192.0.2.1"}
    ]
  }
}
```

`flows` is **mandatory** for every layout: 1–8 explicit flows, each carrying
1–8 channels whose `channelOffset` values are contiguous from 0 and sum to
the `outputLayout` channel count. `ceil(channels/8)` is only the minimal
split and the web UI's default proposal, not an enforced flow count. The
former single-flow fields `destIp`/`port` at the `aes67` level were removed
and any config carrying them fails with `Aes67LegacyMirrorFormat` — regenerate
such a config through the UI (no silent migration happens).

| Field | Default | Runtime rule |
| --- | --- | --- |
| `flows` | required | Explicit per-flow destinations; one `v=0` SDP session, socket and SSRC per flow. Flow rows: `destIp` (required), `port` (default 5004), `channels` (1–8), `channelOffset` (contiguous from 0, sum = layout channels), `payloadType` (omitted inherits the session value), `ssrc` (0 derives session base + flow index), `ttl` (0 inherits the session TTL), `srcInterfaceIp` (empty inherits the session value). |
| `framesPerPacket` | 48 | Must be 48 (1 ms) in this transport step. |
| `payloadType` | 96 | 96–127 dynamic range; session default that flow rows inherit. |
| `ssrc` | 0 | Session SSRC base; `0` uses the fixed default `0x4E535036` ("NSP6") and flow k derives base + k unless the row sets its own. |
| `srcInterfaceIp` | empty | Session-level local IPv4 used to bind the sending sockets and select the multicast egress interface. Must be unicast. Required when any flow destination is a multicast group and that flow carries no own `srcInterfaceIp`. |
| `ptpGmid` | empty | Must stay empty in Phase 1: the sender has no PTP lock source and rejects any configured value (`Aes67PtpGmidRequiresLock`). The SDP always advertises `a=ts-refclk:local` + `a=mediaclk:sender` (see §8). |
| `ptpDomain` | 0 | 0–127, SDP only. |
| `ringPackets` | 64 | 8–1024 sender ring slots in 1 ms packets. Overflow discards the oldest audio and increments `ringDroppedFrames`. |
| `prebufferFrames` | 480 | 48–48000 and must fit inside the ring. Frames buffered before the stream leaves the waiting state. |

ASIO device fields (`asioDll`, `driver`) are not required and are never opened
with `aes67`. `sourcePolicy` must be `fixed` for non-unified sessions (AES67
does not implement AoIP-style active stream following) and may be `unified`
for the combined spatial + PCM renderer path. The `aes67` object is validated even when another backend is
selected, so switching a saved profile cannot land on a bad transport block.
With `aes67`, `sampleRate` must be 48000 and the existing LSAC stereo downmix
adapter is not available (it requires WASAPI).

## 4. Wire format

| Field | Value |
| --- | --- |
| RTP version / padding / extension / CC / marker | 2 / 0 / 0 / 0 / 0 |
| Payload type | 96 (configurable 96–127) |
| Encoding | L24, big-endian, interleaved, 3 bytes/sample |
| Sample rate | 48000 Hz only |
| Frames per packet | 48 (1 ms, `a=ptime:1`) |
| Timestamp | advances by exactly 48 per packet; wraps as a 32-bit counter |
| Sequence | increments by exactly 1 per packet; wraps as a 16-bit counter |
| SSRC | per flow: base default `0x4E535036` + flow index unless the row overrides it; unique across flows |
| Packet size | `12 + 48 x channels x 3` bytes per flow (8 ch = 1164 B; the 1440 B RFC 3550/AES67 budget caps one flow at 8 channels) |

Float32 samples are clamped to [-1, 1], rounded and converted to signed 24-bit
inside the sender; NaN becomes digital silence. `+1.0` maps to `0x7FFFFF` and
`-1.0` to `0x800000`.

## 5. Sender, pacing and underruns

- `aes67_tx.zig` owns a bounded float32 ring (interleaved, `ringPackets x 48`
  frames) and a dedicated sender thread.
- Pacing uses an **absolute** QueryPerformanceCounter timeline: each period is
  due at `start + n x 1 ms`. When the OS delays a wake-up, whole periods are
  sent as a bounded catch-up burst (at most 64), so the average packet rate and
  RTP timeline stay correct instead of drifting. Beyond the burst budget the
  pacer resynchronizes and counts `lateTicks`.
- When the ring cannot supply a complete packet the sender transmits an L24
  silence packet and counts `silentPackets`; it never blocks the submit path.
- Channel order is the existing `sources()` mapping unless an explicit
  `aes67.channelMap` permutation is configured: transmitted channel `k` is
  renderer source channel `channelMap[k]` (default `sources()[k]`), and the SDP
  channel labels use the same order. `physicalChannels` has no meaning for a
  network sink and is not applied. The channel count is exactly
  `outputLayout`'s channel count (5.1.2 = 8, 5.1.4/7.1.2 = 10, 7.1.4 = 12,
  7.1.6/9.1.4 = 14, 9.1.6 = 16) and is sent through the configured flow list.
- Lifecycle uses the existing output state machine: `waiting` until
  `prebufferFrames` were submitted, `playing`, then `draining` after the
  renderer finishes. Drain waits until `framesSent >= framesSubmitted` or
  `drainTimeoutMs` expires.

## 6. SDP and health files

The session writes `<session output directory>/aes67.sdp` when the backend
opens (before the socket exists) and `aes67-output.json` once per second plus
at shutdown. Example SDP for an 8-channel 5.1.2 layout:

```text
v=0
o=- 1314082870 1314082870 IN IP4 0.0.0.0
s=NativeSpatialPipe AES67 output
c=IN IP4 192.0.2.20
t=0 0
m=audio 5004 RTP/AVP 96
a=rtpmap:96 L24/48000/8
a=ptime:1
a=sendonly
a=ts-refclk:local
a=mediaclk:sender
a=fmtp:96 channel-order=SMPTE2110.(51,2)
a=comment:layout=5.1.2
a=comment:channel-labels=L,R,C,LFE,Ls,Rs,Ltf,Rtf
```

Phase 1 always advertises `a=ts-refclk:local` + `a=mediaclk:sender`
(RFC 7273): the sender has no PTP lock source, so configuring `ptpGmid` is
rejected at `prepare()` (`Aes67PtpGmidRequiresLock`) instead of emitting a
false `a=ts-refclk:ptp=IEEE1588-2008:<gmid>:<domain>` claim. A future
PTP-disciplined build may emit that line; the historical `localmac=` spelling
is not RFC 7273 syntax and is never emitted (receivers keep tolerating it as
legacy).

`channel-order` and `channel-labels` are informative: AES67 has no normative
channel-order token. The authoritative order is the rtpmap channel count plus
the label comment, both derived from the existing layout/routing configuration.

`aes67-output.json` is the AES67 analogue of the AoIP resident health file and
the WASAPI report. It contains the state, network parameters, SDP path,
packets/frames/bytes counters, silent and dropped frames, send errors, pacing
`lateTicks`, buffered frames, the output-pool hand-off counters and the output
processing snapshot. Failures are reported through `health.failure` and the
process error detail without changing the capture path. The FSA web host
exposes the current session report through its existing `/api/web/status`
reply as a bounded projection (display fields plus a host-measured age); it
never serves the report's absolute SDP path and never scans historical
sessions. The signal-path panel renders the live health/counters from that
supported endpoint, with no extra resident process.

## 7. Validation and tests

Configuration is validated before any side effect. The example JSON is the
reference document; unknown fields are rejected by strict JSON parsing.

```powershell
# Builds nsp-scheduler.exe and runs the full Zig test set.
powershell -ExecutionPolicy Bypass -File modules/zig-scheduler/Build.ps1

# The new offline suites specifically:
#   playback_schema_tests   schema/backends/example consistency
#   aes67_config_tests      transport validation + playback integration
#   aes67_rtp_tests         RTP header/PT/timestamp/L24, ptime, SDP
#   aes67_tx_tests          ring, channel map, absolute pacer, UDP loopback
```

The sender suite includes a loopback UDP test that is skipped by default and
enabled with `NSP_AES67_LOOPBACK_TEST=1`. It binds a receiver on
`127.0.0.1`, verifies sequence/timestamp continuity, payload type, SSRC, L24
content of the mapped channels, silence packets after underrun, and the average
packet interval. No audio device or network hardware is required.

## 8. Differences from the earlier AES67 prototype (PR #1)

The earlier prototype lived in `tools/aes67-output` as a C DLL with a Zig
loader. This implementation ports the same transport semantics into the current
module tree as native Zig:

| Aspect | Prototype (PR #1) | This implementation |
| --- | --- | --- |
| Transport code | `aes67_tx.c` / `aes67_sdp.c` DLL loaded at runtime | `aes67_tx.zig` / `aes67_rtp.zig`, no extra DLL artifact |
| Config key | `network` object | `aes67` object next to the other backend blocks |
| Packet sizes | 48/96/192/480 frames | fixed 48 frames (1 ms) for this step |
| Addressing | unicast or multicast | unicast or class-D multicast, explicit per flow; a multicast flow without an egress interface fails closed |
| Route order | sinks | transmitted order applies `sources()`/`routing` consistently with labels |
| SDP layout token | Prototype-specific layouts | Existing public scheduler layouts |
| Tests | Python sender acceptance | Zig offline unit tests + gated UDP loopback |
| Health | final report only | periodic `aes67-output.json` health plus final report |
| Build | separate C DLL build step | compiled into the scheduler by `Build.ps1` |

The channel count is no longer hard-coded to 7 or 12: it always follows
`outputLayout`.

## 9. Remaining gaps

- PTP slave/discipline (Phase 1 advertises `a=ts-refclk:local` and rejects a
  configured GMID) and hardware timestamping.
- RTCP (SR/RR), SDP offer/answer, SAP announcement, ST 2022-7 redundancy.
- DSCP/QoS, IGMP membership and receiver-side flow aggregation beyond the
documented SDP.
- 44.1/96 kHz, encodings other than L24, AES67 discovery.
- Resident process / FSA hot-switch semantics: AES67 is currently session-scoped
  like ASIO/WASAPI. Creating a resident AES67 host (analogous to
  `aoip_resident.zig`) is a later step.
- Interoperability run against a real AES67 receiver or analyser (e.g. Wireshark
  RTP analysis) on an isolated network; Windows firewall allowances.

## 10. Multi-flow (1–8 explicit flows, at most 8 channels each) — Phase 1

Multi-flow is **layout-agnostic**: a layout is sent through an explicit list
of 1–8 flows, each carrying 1–8 channels; the channel offsets are contiguous
from 0 and sum to the layout channel count (8ch can use 1 flow, 10/12/14/16ch can
be 2 flows with widths 8+2 / 8+4 / 8+6 / 8+8; three or more narrower flows
are equally valid and the SDP generator handles any flow count).
`ceil(channels/8)` is the **minimal** split and the web UI's default
proposal, not a backend-mandated flow count. The canonical instance is the
16ch layout: one L24/48000/16 stream is 2304 B payload > the RFC 3550/AES67
1440 B budget, so 16ch must not be a single stream. The transport supports an
explicit multi-flow split in the same `aes67` block for every layout:

```json
"aes67": {
  "srcInterfaceIp": "192.0.2.63", "ringPackets": 512, "ttl": 32,
  "flows": [
    {"destIp": "239.1.0.1", "port": 5004, "channels": 8, "channelOffset": 0,
     "ttl": 32, "srcInterfaceIp": "192.0.2.63"},
    {"destIp": "239.1.0.2", "port": 5004, "channels": 8, "channelOffset": 8,
     "ttl": 32, "srcInterfaceIp": "192.0.2.63"}
  ],
  "channelMap": [6,7,2,3,8,9,4,5,0,1,10,11,14,15,12,13]
}
```

### 10.1 Per-flow destinations are explicit — flows are mandatory

Every flow entry carries its **own** destination tuple, persisted in the
config: `destIp` (required), `port` (default 5004), `ttl` (0 = inherit the
session TTL), `payloadType` (omitted = inherit the session value) and
`srcInterfaceIp` (empty = inherit the session value). The sender **never
derives a destination at runtime**: `prepare()` rejects a flow without an
explicit `destIp`.

**The single-flow mirror format is gone.** `flows` is required for every
layout and channel count; a config with
`flows` omitted fails with `Aes67RequiresFlows`, and any config still
carrying the removed top-level `destIp`/`port` pair fails with
`Aes67LegacyMirrorFormat` — those fields were a local transitional design
never merged upstream, so there is no compatibility obligation. The error is
deliberate and loud so the UI can tell the user to regenerate the config; no
silent migration happens.

The historical trap this removes: a saved config could carry
`flows[1].destIp` that the user never typed while the UI showed only a
single-IP field. Per-flow rows are now the only shape: the web save path
materializes any missing per-flow value into the saved file (below), the
`aes67-output.json` report exposes `network.flows[]` (destIp, port, channels,
channelOffset, ssrc, multicast, ttl, srcInterfaceIp per flow), and the SDP
keeps one `v=0` session per flow with its own `c=` group.

**Web configuration auto-allocation.** When the WebUI saves a config whose flow rows leave `destIp`
empty — the "I only typed one IP" flow — the backend fills them before
validation and writes the result to disk, so the generated addresses are
visible in the file and in the UI readback. Flow 0's explicit `destIp` is
the allocation base:

- flow `k` destination = flow-0 group **+ k** when the base is multicast
  (`allocateFlowGroup`, octet carry, must stay inside 224.0.0.0/4; flow 0
  keeps the typed group). One group per stream, per AES67 6.1.3.
- flow `k` destination = the same host when the base is unicast.
- missing `port` (multicast) = **5004** for every flow (Plan A default).
- missing `port` (unicast) = flow-0 port + 2k — a unique even-port
  continuation on the same host (RTP/RTCP pair convention).
- missing `ttl` (multicast) = the session TTL, frozen as an explicit value.
- missing `srcInterfaceIp` = the session value.

Saving preserves explicit destinations and ports. The form also offers an
explicit reallocation action based on flow 1. Hand-edited files loaded by the
sender must already contain their destinations. The sender validates the final
document.

### 10.2 Plan A (spec-preferred) and Plan B (unique ports)

- **Plan A — one group per flow, all on the default port**: distinct
  multicast groups, every flow on 5004. This is the AES67 6.3 default shape
  and needs no `port` entries at all. Receivers demux by group (per-group
  sockets with SO_REUSEADDR/IP_PKTINFO, or one socket per group).
  ```json
  "flows": [
    {"destIp": "239.1.0.1", "channels": 8, "channelOffset": 0},
    {"destIp": "239.1.0.2", "channels": 8, "channelOffset": 8}
  ]
  ```
- **Plan B — one group per flow, per-flow unique ports**: distinct groups
  with distinct ports, e.g. `239.1.0.1:5010 + 239.1.0.2:5012` (or
  `5004/5006` starting from the default). Useful when a receiver binds one
  flow per port and cannot share a port across groups.
  ```json
  "flows": [
    {"destIp": "239.1.0.1", "port": 5010, "channels": 8, "channelOffset": 0},
    {"destIp": "239.1.0.2", "port": 5012, "channels": 8, "channelOffset": 8}
  ]
  ```
- Rejected in both plans: two multicast flows sharing one group
  (`Aes67DuplicateFlowGroup`) — AES67 assumes a unique destination address
  per stream and a single sender per group. Same unicast host with distinct
  ports remains valid (the unicast lab shape).
- Legacy format: the removed top-level `destIp`/`port` pair and the
  flows-omitted shape are rejected (`Aes67LegacyMirrorFormat` /
  `Aes67RequiresFlows`) — see the mirror-format note below.

**SSRC convention.** With the flow rows' `ssrc` left at 0, flow k derives
`0x4E535036 + k` (the "NSP6" default pair for two flows: 0x4E535036 /
0x4E535037; the JSON fields stay integer machine format). Hand-filled
values are kept and must stay unique — two flows sharing one SSRC would mix
on the wire and are rejected (`Aes67DuplicateFlowSsrc`).

Spec references: AES67 6.1.3 (each stream a unique destination address,
single device per multicast group) and 6.3 (default ports 5004 RTP /
5005 RTCP; non-default ports allowed with reduced interop, 49152-65535
preferred).

### 10.3 Mirror-format removal and Phase 1 clock truth

The web form and runtime reject the old top-level `destIp` and `port`
fields. To repair an older file, remove those keys in the advanced JSON
editor and define `aes67.flows` explicitly. Loading a saved document preserves
its flow rows and optional channel map for review.

Clock truth (Phase 1): the sender has **no PTP lock source** — it free-runs
against QPC. Configuring `ptpGmid` therefore fails closed
(`Aes67PtpGmidRequiresLock`): an SDP that claims `a=ts-refclk:ptp=...`
while the clock is not disciplined is more dangerous than one that admits
`a=ts-refclk:local`. Emitted SDP is always `a=ts-refclk:local` +
`a=mediaclk:sender` (RFC 7273). The historical `localmac=` spelling is not
RFC 7273 syntax and is not emitted; receivers keep tolerating it as legacy.

- One sender thread, one shared RTP timestamp timeline: every 1 ms period sends
  all flows back-to-back with the **same** RTP timestamp; per-flow SSRC/seq are
  independent (the receiver aggregates by timestamp, AES67 has no bundling).
- Per flow: `frames x channels x 3 <= 1440` is enforced at prepare time; a flow
  may carry 1..8 channels and offsets must be contiguous from 0, summing to the
  outputLayout channel count.
- `channelMap` is an **optional** explicit wire-order permutation
  (transmitted channel k = renderer source channel channelMap[k]); without it
  the existing `sources()` order is used. The 16ch reference example maps the
  documented 9.1.6 renderer order onto the 16ch container order
  `L,R,C,LFE,Ls,Rs,Lrs,Rrs,Lw,Rw,Ltf,Rtf,Aux12,Aux13,Ltr,Rtr`
  (renderer indices 6,7,2,3,8,9,4,5,0,1,10,11,14,15,12,13); that is an
  example, not a fixed layout policy.
- SDP: one `v=0` session per flow in one file (`aes67.sdp`), each with
  `a=rtpmap:96 L24/48000/<channels>`, `a=comment:flow=<i>`,
  `a=comment:channel-offset=<n>` and that flow's channel labels; `c=` carries
  each flow's own group (with `/ttl` for multicast) and `m=audio` its own port.
- Phase 1 has no PTP: SDP declares `a=ts-refclk:local` and
  `a=mediaclk:sender` (RFC 7273). `localmac` is no longer emitted; receivers
  keep it as legacy tolerance. Reports must state `ptp=not-wired`.
- The single-flow mirror path no longer exists: `flows` is required for
  every layout (`Aes67RequiresFlows` when omitted,
  `Aes67LegacyMirrorFormat` when the removed top-level `destIp`/`port` are
  still present) and one flow may carry at most 8 channels / 1440 B.
- `aes67_net_tool.zig` is the offline-gated 16ch (2 x 8ch) acceptance
  sender (`NSP_AES67_NET_TEST=1`, `NSP_AES67_NET_DEST0/1`,
  `NSP_AES67_NET_PORT0/1`, `NSP_AES67_NET_SECONDS`, `NSP_AES67_NET_SDP`,
  `NSP_AES67_NET_JSON`). It builds the same Prepared plan and one Tx and streams
  per-channel sine tones; used for the pcap/host acceptance evidence.
- `aes67_mcast_tool.zig` is the multicast egress harness
  (`NSP_AES67_MCAST_TEST=1`, see its header). Its 16ch flow-1 destination
  defaults to the derived `DEST+1` group when unset, matching the documented
  allocation rule (an explicit `NSP_AES67_MCAST_DEST1` always wins).
