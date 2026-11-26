# AES67 network audio output

AES67 is a session-scoped output backend. The standalone module owns the PCM ring, PTP follower, media clock, RTP/L16/L24 packetization, sender thread, and network resources. NSP supplies validated playback configuration, connects the rendered audio pool, and handles lifecycle results. Rendering, DSP, and layout selection continue in the existing native pipeline.

## Standards baseline and tiers

| Tier | Item | Status in this branch |
| --- | --- | --- |
| Tier 0 | PTP slave + media clock + absolute RTP timestamps + ts-refclk/mediaclk | Supported (Rust module) |
| Tier 0 | 48 kHz, L16 + L24, 1 ms, 1-8 channels per flow, 1440-byte payload check | Supported |
| Tier 0 | Unique multicast group per flow, shared RTP port 5004, PTP gating | Supported |
| Tier 0 | SDP over ABI (static export) and RFC 2974 SAP with CRC-16 hash/TTL 255/30 s interval/deletion x3 | Supported |
| Tier 0 | DSCP AF41 for media, EF for PTP; media-clock pacing (bounded catch-up) | Supported |
| Tier 0 | Receiver side (IGMP join, jitter buffer, L16/L24 receive, 16ch aggregation) | Not in this branch (receiver lives in TSPI / aoip-aes67) |
| Tier 0 | 16-channel layout delivered as 2 x 8ch | Supported by flowMode=standard (default) |
| EXTENSION | 16/64-channel single flow (flowMode=extension) | Automatic in extension mode when samples x bytes x channels <= 1440 B; not AES67 media-profile conformance |
| Tier 1 | 44.1 / 96 kHz | Supported (44.1 kHz uses 48 samples per packet, L16 default) |
| Tier 1 | 125/250/333 us and 2/4 ms packet times | Supported |
| Tier 1 | HTTP/RTSP SDP service, mDNS advertisement/browse, 64ch as 8 x 8ch | 64ch splitting supported; HTTP/RTSP/mDNS not in this branch |
| Tier 2 | 176.4/192 kHz, RTCP, ST 2022-7 redundancy | 192/176.4 supported as extension; redundancy flag + port 5006 implemented; RTCP deferred |

AES67 mandates 48 kHz and the "1 millisecond" packet profile; 125/250/333 us and 4 ms are optional capabilities listed in AES67-2023 Table 2. Rates above 96 kHz, RAVENNA-style extensions and a 16-channel single stream are outside AES67 and are marked EXTENSION where supported.

## Configuration

Select AES67 in the playback configuration panel or set outputBackend to "aes67". Use "fixed" to select the configured sourceStream, or "unified" to use the unified ROAR pipeline. The renderer sampleRate must equal aes67.sampleRate.

    {
      "outputBackend": "aes67",
      "bridgeDll": "C:/path/to/nsp_croksor.dll",
      "outputLayout": "7.1.4",
      "sampleRate": 48000,
      "aes67": {
        "name": "NSP-Studio",
        "sampleRate": 48000,
        "ptimeUs": 1000,
        "codec": "L24",
        "flowMode": "auto",
        "groupBase": "239.69.0.1",
        "payloadType": 96,
        "ssrc": 0,
        "srcInterfaceIp": "192.0.2.1",
        "ptp": {"mode": "slave", "domain": 0, "lockTimeoutMs": 5000, "timestamping": "auto",
                "priority1": 128, "priority2": 128, "announceTimeoutMs": 6000,
                "gmCapable": false, "gmUtcOffset": null},
        "ttl": 32,
        "sourceFilter": true,
        "ringPackets": 64,
        "prebufferFrames": 480,
        "sapAddress": "",
        "sapIntervalMs": 30000
      }
    }

The playback schema and the 12-channel and 16-channel examples under modules/zig-scheduler show the available settings.

The web settings page separates deployment decisions from tuning. The basic panel keeps only
the device name, the source-interface binding and the start multicast group (flow 1 destination
and port). The collapsible advanced panel holds sample rate, packet time (ptime), payload type,
TTL, base SSRC, flow packing mode, port policy, SAP group/interval, send-buffer sizing, the PTP
mode/domain/timestamping block and a per-flow editor (destination, port, payload type, SSRC,
TTL). Changing ptimeUs or flowMode re-plans flow widths and keeps explicit per-flow overrides;
the legacy framesPerPacket key is no longer a form field and is removed on load (the scheduler
still accepts it when it equals the derived sample count). A save that fails validation opens
the advanced section and focuses the first invalid field.

The sample-rate pull-down currently enables only 48 kHz. The scheduler accepts the other schema
rates, but the NSP renderer path does not resample yet, so 44.1/88.2/96/176.4/192 kHz are shown
disabled. ptimeUs 125 us was verified on the reference sender with a 20 s 8-channel L24 capture:
8000 packets/s, timestamp step 6 samples, 144-byte payloads and zero loss in steady state; the
only drops are the usual start/stop boundary packets.

- name: optional device/session name (printable ASCII, trimmed length at most 63). Omitted or blank
  derives NSP-<COMPUTERNAME> when the session opens. The name appears in the SDP o= username,
  s= and i= lines; spaces survive in s=/i= and fold to '-' in the o= token, and CR/LF or other
  control characters are rejected by the configuration validator.
- sampleRate: 44100, 48000, 88200, 96000, 176400, 192000. Default 48000.
- ptimeUs: 125, 250, 333, 500, 1000, 2000, 4000. Default 1000. Samples per packet follow AES67-2023 Table 2: 6/12/16/24/48/96/192 at the 1FS rate of the sampling-rate family. The 1 ms profile is 48 samples at both 48 kHz and 44.1 kHz, so the 44.1 kHz packet lasts 1.088 ms (SDP a=ptime:1.09, 918.75 packets/s, timestamp step 48).
- codec: "L16" (2 bytes/sample) or "L24" (3 bytes/sample), both big-endian. Omitted selects L16 for the 44.1 kHz family (AES67 requirement) and L24 otherwise. framesPerPacket remains accepted as a legacy cross-check but must equal the derived sample count.
- flowMode: "standard" (default) keeps the AES67 1-8 channel profile (16ch = 2 x 8, 64ch = 8 x 8) and rejects a split that needs more than 8 flows or overflows 1440 B per flow. "extension" packs each flow to the 1440-byte payload limit first: max_ch = floor(1440 / (samples x bytes)), flow_count = ceil(total / max_ch), so a single 16ch flow is used when it fits (48k/250us/16ch L24 = 576 B, 48k/125us/16ch L24 = 288 B, 192k/125us/16ch L24 = 1152 B) and oversized payloads split (48k/1ms/16ch L24 = 2304 B -> 2 x 8ch). Flows wider than 8 channels are a product EXTENSION, not AES67 conformance; use standard for third-party interop.
- Provide either groupBase (derived planning: flow k = base + k) or explicit flows. standard flows carry 1-8 channels; extension may carry more (up to the product 16-channel layout) when the payload fits. Offsets are contiguous from zero and must cover the layout exactly. An optional channelMap supplies the wire order.
- Standard ports: every flow uses RTP port 5004; flows are distinguished by unique multicast groups. ST 2022-7 redundancy uses 5006 (primary + 2). portPolicy "explicit" allows even per-flow ports for migration/lab use; strict mode rejects them.
- ringPackets controls capacity in 1 ms packets. prebufferFrames must fit inside the ring and is at least one packet.
- SSRC 0 selects a random per-session base; flow k uses base + k unless a row sets its own.
- sapAddress empty selects RFC 2974 global scope 224.2.127.254:9875; the N150 deployment listens on 239.255.255.255 and can set it explicitly. sapIntervalMs is 1000-300000, default 30000.

## Migration: FLP01 sender and N150 sinks

The previous FLP01 sender carried 8ch flows on ports 5010/5012. AES67 uses one shared RTP port and unique groups, so migrate as follows:

1. Keep the existing groups (for example 239.69.16.150 and 239.69.16.151).
2. Set both flow ports to 5004. Remove the 5010/5012 entries; strict validation now rejects them with InvalidAes67Port.
3. Keep the per-flow SSRC pair unique (or leave 0 for the random base + index).
4. Recreate the N150 sinks from the sender's current SDP so the sink port matches 5004: PUT /api/sink/N with the SDP from nsp_aes67_sdp or the SAP announcement. Existing sinks that still say 5010/5012 will not receive after the migration.
5. Verify with RTP captures that both flows use 5004 and different group addresses, and that /api/sink/status/N shows receiving with no payload-type errors.

portPolicy "explicit" exists only to keep a lab receiver working during a staged rollout; it is not the production configuration.

## PTP topology modes and network discovery

The sender supports four PTP topology modes (DESIGN-TOPO.md section 2). Key names are frozen per
DESIGN audit D8: they live inside the nested aes67.ptp object.

| Setting | Accepted values | Default | Meaning |
| --- | --- | --- | --- |
| aes67.ptp.mode | slave, auto, gm, local | slave | slave: strict follower, fails/keeps DEGRADED exactly like before. auto: listen up to announceTimeoutMs, then follow an external GM or fall back to gm (when gmCapable) / local. gm: announce this sender as the grandmaster immediately. local: no PTP sockets; local media clock and a=ts-refclk:local. |
| aes67.ptp.domain | 0-127 | 0 | AES67 Annex A Table A.1 default 0. |
| aes67.ptp.lockTimeoutMs | 100-120000 ms | 5000 ms | Existing startup/lock deadline. |
| aes67.ptp.timestamping | auto, software, hardware | auto | No PHC is integrated in this Windows sender: software timestamps come from QPC at the socket boundaries, and explicit hardware reports TIMESTAMP_UNAVAILABLE at start. Intel I225/I226 advertises 1588 only for its Linux drivers, so a Windows hardware path needs another NIC/stack. |
| aes67.ptp.priority1 | 0-255 | 128 | GM BMCA priority1 (AES67 Annex A Table A.1 default). |
| aes67.ptp.priority2 | 0-255 | 128 | GM BMCA priority2 (AES67 Annex A Table A.1 default). |
| aes67.ptp.announceTimeoutMs | 1000-60000 ms | 6000 | AUTO listening window and foreign-master receipt timeout (announceReceiptTimeout 3 x 2 s). |
| aes67.ptp.gmCapable | boolean | false | auto mode may announce a GM when no external GM appears. gm mode implies true. |
| aes67.ptp.gmUtcOffset | null or 0-127 s | null | null = ARB (ptpTimescale=0, offsetValid=0). 0-127 = IEEE 1588-2008 8.2.3 currentUtcOffset seconds, announced with ptpTimescale=1 and currentUtcOffsetValid=1. |

When the PTP block is omitted, an existing top-level ptpDomain still supplies the domain and
mode stays slave, so existing configurations are unchanged.

### GM engine (modes auto/gm)

- Clock identity is the EUI-64 of the interface MAC (MAC[0..3] || FF FE || MAC[3..6], IEEE
  1588-2008 8.2.1). gm mode refuses to start with NSP_AES67_PTP_IDENTITY_UNAVAILABLE (-10) when the
  MAC cannot be read; it never invents a random identity.
- Announce every 2 s, Sync every 125 ms with the two-step flag, Follow_Up immediately after each
  Sync, Delay_Resp for every in-domain Delay_Req. Domain filtering drops other domains. DSCP EF and
  multicast TTL 16 match the validated board GM profile (TTL is a deployment/profile choice: RFC
  1112 sec 6.1 only defaults an unspecified multicast TTL to 1, and RFC 5771 sec 5 places 224.0.1.129
  in the Internetwork Control Block). Two-step and clockAccuracy 0xFE / offsetScaledLogVariance 0xFFFF
  are IMPLEMENTATION CHOICES matching that profile, not AES67 numeric requirements.
- Data set: clockClass 248 (AES67 Annex A Table A.2 default/ARB), stepsRemoved 0, timeSource 0xA0,
  timeProperties ARB unless gmUtcOffset is configured. GM time is a monotonic anchor at activation
  (ARB) or the Windows system UTC at activation plus the configured offset; no PHC is integrated, so the
  hardware-timestamping path remains NOT_RUN and GM accuracy is software-grade.
- BMCA-lite follows the IEEE 1588-2008 9.3.2 dataset comparison order (UNVERIFIED-LOCAL: the 1588
  text is not in this workspace). A better foreign GM makes us stop announcing immediately (auto:
  follow it as slave; gm: report passive). A worse foreign GM keeps us GM but arms a dual-GM
  watchdog that yields after two announce intervals rather than run two GMs. A passive GM reclaims
  after announceTimeoutMs without a foreign Announce.

### Slave behavior and SDP

The follower accepts ptpTimescale=0 (ARB) Announces for frequency-only locking. This is a documented
DEVIATION from AES67 section 5 (the media and network clocks must share the TAI epoch); ARB locking
therefore reports time_traceable=false and an arbitrary epoch. time_traceable comes from the
TIME_TRACEABLE flag (0x0010) or a traceable clockClass (6/7/166/187), never from ptpTimescale
(SPEC-REFS audit D3; the real board GM announces ptpTimescale=1 with timeTraceable=0).

The announced SDP follows the role: locked slave or active GM emits a=clock-domain:PTPv2 <domain>,
a=ts-refclk:ptp=IEEE1588-2008:<GMID>:<domain> and a=mediaclk:direct=0; local mode emits
a=ts-refclk:local and a=mediaclk:direct=0 with no ptp and no localmac. Every role change raises the
SDP version and re-announces, so receivers see a new o= version. Legacy ts-refclk:localmac is never
emitted; receivers that still need it during migration must run their own strict=false mode
(aes67_refclk_strict=false on the receiver side). mediaclk:sender is legal RFC 7273 but not
AES67-conformant, so it is not emitted (reserved EXTENSION behind a future local_mediaclk setting);
the default and only emitted form is a=mediaclk:direct=0.

### Status reporting

EXTENSION ABI export nsp_aes67_ptp_status (NspAes67PtpStatus) reports mode, role, source, domain,
time_traceable, ptp_timescale, gm_identity, a reason tag and the transition counter; the counters
struct stays size-compatible. The same data is written to the optional diagnostics log line
(nsp_aes67 ptp unix_ns=... mode=... role=... source=... gm=... domain=... time_traceable=...
ptp_timescale=... reason=...). Software timestamping uses Windows QPC at the socket boundaries; auto
uses software when hardware timestamping is unavailable.

The module supplies one SDP description per flow (RFC 4566, RFC 7273) while locked and during holdover, and enables SAP announcements for discovery (RFC 2974). SAP message identifiers are CRC-16 hashes of the SDP, so a version change is visible to browsers; deletions are repeated three times and announcements stop only when RTP stops. The NSP web interface retains the configuration panel. Static SDP can be exported through the ABI for HTTP or file delivery; the module does not run an HTTP/RTSP server.

### Device identity and channel metadata in SDP

Every SDP carries the session name in three places:

```
o=NSP-Studio 15008987692570773568 1 IN IP4 192.0.2.1
s=NSP-Studio flow0
m=audio 5004 RTP/AVP 97
i=NSP-Studio flow0 IN/8
a=rtpmap:97 L24/48000/8
a=fmtp:97 channel-order=SMPTE2110.(71)
```

- `o=` username is a single ASCII token: printable characters are kept, spaces, non-ASCII and
  control characters become '-', the token is capped at 64 characters and never empty.
- `i=` repeats the device name plus the flow index and channel count after the media line. It is
  free text (spaces allowed); control characters are dropped so a configured name cannot inject a
  new SDP line. RAVENNA tooling reads the media information line and the origin name, which is why
  both are emitted rather than only the private `a=comment` lines.
- `a=comment:channel-labels`, `a=comment:flow` and `a=comment:channel-offset` remain for diagnosis;
  they are private extensions and are not part of AES67.

`a=fmtp:<payload type> channel-order=SMPTE2110.(<groups>)` is emitted per media section when the
flow's exact label sequence identifies a SMPTE ST 2110-30 grouping symbol:

| Flow labels (exact order) | channel-order |
| --- | --- |
| M or C | SMPTE2110.(M) |
| L,R or FL,FR | SMPTE2110.(ST) |
| L,R,C,LFE,Ls,Rs | SMPTE2110.(51) |
| L,R,C,LFE,Ls,Rs,Lrs,Rrs | SMPTE2110.(71) |
| standard group followed by 1-8 user channels | `<group>,U<nn>` (e.g. 5.1.2 -> 51,U02) |
| 1-8 channels without a standard group | `U<nn>` (e.g. 7.1.4 height flow -> U04) |
| anything else (no standard prefix, or more than eight channels) | omitted |

A wrong channel-order makes a receiver decode the wrong speakers, so the sender omits the attribute
rather than guessing. NSP labels use `Ls/Rs` where ST 2110-30 says `Lss/Rss`; the sequences above
are matched on the project's label names.

## Pacing, QoS and packet format

RTP timestamps are absolute media-clock sample positions (mediaclk:direct=0) and advance by exactly the packet's sample count. The pacing deadline is computed with exact rational arithmetic from the media clock, so 44.1 kHz does not drift and 125 us packets are not batched into 1 ms bursts. Catch-up is bounded by NSP_AES67_CATCH_UP_SLOTS (default 8 slots).

RTP sockets are marked AF41 (DSCP 34) and PTP sockets EF (DSCP 46) per AES67-2023 sec 6.2. The RTP marker bit is always zero: AES67 assigns it no meaning here and the Merging RAVENNA LKM would read the byte as a wrong payload type. Payloads are L16/L24 big-endian; the 1440-byte RTP payload limit is checked before every send.

## Host lifecycle and lease ownership

The native binding registers the DLL submit function directly with the existing native output pool. On acceptance the DLL copies PCM into its ring and synchronously releases the token once. If submit rejects the block, the pool writer releases it. The module retains neither the PCM pointer nor the lease.

The host waits for module startup to reach PTP lock before publishing session readiness. It polls lifecycle events and surfaces a sender exit as a session failure. Shutdown joins the writer before stopping or closing the backend. The module ABI is documented in modules/aes67/INTERFACE.md.

## Receiver and interop notes

This branch is the sender. The AES67 receiver (IGMP join/leave, jitter buffer, L16/L24 receive, 16-channel aggregation) is implemented outside this worktree. The reference daemon (bondagit aes67-linux-daemon + Merging LKM) accepts 48 kHz L16/L24 1 ms streams; its codec field maps to the same L16/L24 names, and a daemon sink is created from the sender SDP, not by hand. Interop with the N150 daemon must be verified with the T1-T13 matrix (real SDP sink creation, SAP discovery, marker isolation, 44.1/96 kHz, short packet times, multi-flow) before claiming native AES67 operation.