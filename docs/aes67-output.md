# AES67 RTP output transport

Status: implemented on the public `alpha-dev` base as the fourth output
transport (`outputBackend: "aes67"`) next to ASIO, native AoIP and WASAPI. It is
a **transport only** mode: rendering, spatial DSP and channel layout stay in the
existing pipeline. AES67 never downmixes, upmixes or reorders channels on its
own.

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
- Unicast UDP destination, optional source-interface bind, SDP generation.
- A bounded, paced sender fed by the same Rust output-pool leases as the other
  backends.
- Session-scoped health/report JSON, validated configuration, offline tests.

Out of scope for this step:

- PTP slave/timebase discipline, RTCP/SAP, SDP offer/answer, ST 2022-7
  redundancy, multicast, DSCP/QoS, sample rates other than 48 kHz.
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
       build RTP header + L24 payload, sendto() unicast UDP
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
    "destIp": "192.168.50.20",
    "port": 5004,
    "framesPerPacket": 48,
    "payloadType": 96,
    "srcInterfaceIp": "192.168.50.1",
    "ptpGmid": "00-11-22-FF-FE-33-44-55",
    "ptpDomain": 0,
    "ringPackets": 64,
    "prebufferFrames": 480
  }
}
```

| Field | Default | Runtime rule |
| --- | --- | --- |
| `destIp` | required | Dotted unicast IPv4. Multicast, broadcast, unspecified and hostnames are rejected before a socket exists. `127.0.0.1` is accepted for loopback tests. |
| `port` | 5004 | 1–65535, nonzero. |
| `framesPerPacket` | 48 | Must be 48 (1 ms) in this transport step. |
| `payloadType` | 96 | 96–127 dynamic range; 96 is the AES67 default. |
| `ssrc` | 0 | `0` uses the fixed default `0x4E535036` ("NSP6"). |
| `srcInterfaceIp` | empty | Optional local IPv4 used to bind the sending socket (egress interface). Must be unicast. |
| `ptpGmid` | empty | Optional IEEE 1588 grandmaster ID (8 hyphen-separated hex octets) advertised in SDP. Empty advertises `localmac` instead. The sender does not discipline its clock (see §8). |
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
| SSRC | fixed per session, default `0x4E535036` |
| Packet size | `12 + 48 x channels x 3` bytes (e.g. 1740 B for 12 channels) |

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
- Channel order is the existing `sources()` mapping: transmitted channel `k`
  is pool channel `sources()[k]`, and the SDP channel labels use the same
  order. `physicalChannels` has no meaning for a network sink and is not
  applied. The channel count is exactly `outputLayout`'s channel count
  (5.1.2 = 8, 5.1.4/7.1.2 = 10, 7.1.4 = 12, 7.1.6/9.1.4 = 14, 9.1.6 = 16).
- Lifecycle uses the existing output state machine: `waiting` until
  `prebufferFrames` were submitted, `playing`, then `draining` after the
  renderer finishes. Drain waits until `framesSent >= framesSubmitted` or
  `drainTimeoutMs` expires.

## 6. SDP and health files

The session writes `<session output directory>/aes67.sdp` when the backend
opens (before the socket exists) and `aes67-output.json` once per second plus
at shutdown. Example SDP for 7.1.4:

```text
v=0
o=- 1314082870 1314082870 IN IP4 0.0.0.0
s=NativeSpatialPipe AES67 output
c=IN IP4 192.168.50.20
t=0 0
m=audio 5004 RTP/AVP 96
a=rtpmap:96 L24/48000/12
a=ptime:1
a=sendonly
a=ts-refclk:ptp=IEEE1588-2008:00-11-22-FF-FE-33-44-55:0
a=mediaclk:direct=0
a=fmtp:96 channel-order=SMPTE2110.(71,4)
a=comment:layout=7.1.4
a=comment:channel-labels=L,R,C,LFE,Ls,Rs,Lrs,Rrs,Ltf,Rtf,Ltr,Rtr
```

`channel-order` and `channel-labels` are informative: AES67 has no normative
channel-order token. The authoritative order is the rtpmap channel count plus
the label comment, both derived from the existing layout/routing configuration.

`aes67-output.json` is the AES67 analogue of the AoIP resident health file and
the WASAPI report. It contains the state, network parameters, SDP path,
packets/frames/bytes counters, silent and dropped frames, send errors, pacing
`lateTicks`, buffered frames, the output-pool hand-off counters and the output
processing snapshot. Failures are reported through `health.failure` and the
process error detail without changing the capture path.

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
| Addressing | unicast or multicast | unicast only; multicast rejects fail-closed |
| Route order | sinks | transmitted order applies `sources()`/`routing` consistently with labels |
| SDP layout token | included 4.1.2 ("7 channels") | public layouts 5.1.2–9.1.6 only; no 4.1.2 transport mapping |
| Tests | Python sender acceptance | Zig offline unit tests + gated UDP loopback |
| Health | final report only | periodic `aes67-output.json` health plus final report |
| Build | separate C DLL build step | compiled into the scheduler by `Build.ps1` |

The channel count is no longer hard-coded to 7 or 12: it always follows
`outputLayout`.

## 9. Remaining gaps

- PTP slave/discipline (only `ts-refclk`/localmac is advertised) and hardware
  timestamping.
- RTCP (SR/RR), SDP offer/answer, SAP announcement, ST 2022-7 redundancy.
- Multicast, DSCP/QoS, IGMP and multi-stream fan-out.
- 44.1/96 kHz, encodings other than L24, AES67 discovery.
- Resident process / FSA hot-switch semantics: AES67 is currently session-scoped
  like ASIO/WASAPI. Creating a resident AES67 host (analogous to
  `aoip_resident.zig`) is a later step.
- Web configuration UI field for the `aes67` block and live status in the
  frontend panel.
- Interoperability run against a real AES67 receiver or analyser (e.g. Wireshark
  RTP analysis) on an isolated network; Windows firewall allowances.
