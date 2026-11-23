# AES67 network audio output

AES67 is a session-scoped output backend. The standalone module owns the PCM ring, PTP follower, media clock, RTP/L24 packetization, sender thread, and network resources. NSP supplies validated playback configuration, connects the rendered audio pool, and handles lifecycle results. Rendering, DSP, and layout selection continue in the existing native pipeline.

## Configuration

Select AES67 in the playback configuration panel or set `outputBackend` to `aes67`. Use `fixed` to select the configured `sourceStream`, or `unified` to use the unified ROAR pipeline. Automatic source switching is deferred. AES67 requires a 48 kHz layout and no ASIO device configuration.

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
    "ptp": {"domain": 0, "lockTimeoutMs": 5000, "timestamping": "auto"},
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

The [playback schema](../modules/zig-scheduler/playback.schema.json) and [16-channel example](../modules/zig-scheduler/playback.aes67.16ch.example.json) show the available settings.

- Provide one to eight explicit `flows`. Each flow carries one to eight channels. Offsets are contiguous from zero and must cover the layout exactly. An optional `channelMap` supplies the wire order. Otherwise the selected routing order applies.
- Each flow has its own destination. Multiple flows can share a unicast IP on distinct ports. Use a distinct group for every multicast flow. The same port (5004 by default) can be shared across groups. Set an explicit local IPv4 interface for multicast, either per flow or for the session.
- The sender uses 48 frames per packet. An omitted flow payload type inherits the session value, which defaults to 96. An omitted or zero flow SSRC is derived from the session base plus the flow index. The default base is `0x4E535036`. The flow SSRCs must be unique.
- `ringPackets` controls capacity in 1 ms packets. `prebufferFrames` must fit inside the ring and is rounded up to a whole packet when handed to the module.
- The configuration form can fill blank flow destinations from the first destination before saving. For multicast it increments the group address and uses port 5004 for blank ports. For unicast it reuses the host and adds two to the base port per flow. Explicit values survive the save. Hand-authored profiles must include every destination. Replace legacy top-level `destIp` and `port` with flow rows.

## PTP and network discovery

| Setting | Accepted values | Default |
| --- | --- | --- |
| `aes67.ptp.domain` | 0–127 | 0 |
| `aes67.ptp.lockTimeoutMs` | 100–120000 ms | 5000 ms |
| `aes67.ptp.timestamping` | `auto`, `software`, `hardware` | `auto` |

When the PTP block is omitted, an existing top-level `ptpDomain` still supplies the domain. Leave the legacy `ptpGmid` empty. The module derives the advertised clock identity from the locked grandmaster. Software timestamping uses Windows QPC at the socket boundaries. Auto uses software when hardware timestamping is unavailable. Explicit hardware mode reports startup failure on unsupported hardware.

The module supplies one SDP description per flow while locked (RFC 4566 and RFC 7273) and enables SAP announcements for discovery (RFC 2974). A loss of lock pauses RTP while the module attempts to recover. RTCP (RFC 3550) is deferred. The NSP web interface retains the configuration panel. Stream discovery and monitoring use the network protocols.

## Host lifecycle and lease ownership

The [native binding](../modules/zig-scheduler/native.zig) registers the DLL's submit function directly with the existing native output pool. On acceptance the DLL copies PCM into its ring and synchronously releases the token once. If submit rejects the block, the pool writer releases it. The module retains neither the PCM pointer nor the lease.

The [host](../modules/zig-scheduler/host.zig) waits for module startup to reach PTP lock before publishing session readiness. It polls lifecycle events and surfaces a sender exit as a session failure. Shutdown joins the writer before stopping or closing the backend. The host consumes the stop completion event, and retains the module handle through a join timeout until close succeeds. The module ABI is documented in [INTERFACE.md](../modules/aes67/INTERFACE.md).

The former periodic sender report could be missing or stale and its last snapshot could race startup or shutdown. The host now gets startup errors, unexpected exits, and confirmed shutdown directly from the DLL. The common web status endpoint has no AES67-specific runtime payload.
