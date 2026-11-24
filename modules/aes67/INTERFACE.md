# AES67 module C ABI

The session-scoped Windows DLL is nsp_aes67.dll. The DLL owns PTP, RTP, optional SAP, clocks, the PCM ring and its worker. The host supplies validated playback settings, submits PCM and handles lifecycle events. See [nsp_aes67.h](nsp_aes67.h).

## Configuration

Create with nsp_aes67_create(&session) and configure before starting. Set struct_size to sizeof(NspAes67Config) (152 bytes). The first 128 bytes are compatible with the original 1 ms / 48 kHz ABI; a 128-byte struct_size is accepted and defaults the appended fields to 48 kHz, L24, 1 ms, port 5004 and SAP group 224.2.127.254. The module copies configuration, flows and labels during configure.

- **Sampling rate**: 44100, 48000, 88200, 96000, 176400 and 192000 Hz. 48 kHz is the AES67-mandatory rate; 44.1 and 96 kHz are recommended; 88.2/176.4/192 kHz are RAVENNA-family extensions. Zero selects 48000.
- **Packet time**: 125, 250, 333, 500, 1000, 2000 or 4000 microseconds; zero selects 1000. Sample counts follow AES67-2023 Table 2 and are derived from the rate family: the "1 millisecond" profile is 48 samples at both 48 kHz and 44.1 kHz (96 at 96 kHz, 192 at 192 kHz), so a 44.1 kHz 1 ms packet lasts 1.088 ms, is advertised as a=ptime:1.09 and paced at 918.75 packets/s. RTP timestamps advance by exactly samples-per-packet (48) on every packet; the pacing grid is exact rational media-clock time and never drifts.
- **Encoding**: bit_depth 0 = L24 (3 bytes/sample), 1 = L16 (2 bytes/sample), both big-endian two's complement (RFC 3551 4.5.11 / RFC 3190 clause 4). Omitted selects L24; the host defaults to L16 for the 44.1 kHz family because AES67 requires L16 support there.
- **Payload budget**: every RTP datagram is checked against the 1440-byte AES67 payload maximum (AES67-2023 sec 6.3).
- **Flow modes** (flow_mode): 0 standard (default) keeps the AES67 media profile of at most 8 channels per flow, so 16ch = 2 x 8ch and 64ch = 8 x 8ch; the 1440-byte budget is still checked for every flow (a standard split that needs more than 8 flows or overflows a flow is rejected). 1 extension packs each flow to the 1440-byte limit first: max_ch = floor(1440 / (samples x bytes)) and flow_count = ceil(total / max_ch), so a single 16ch flow is used when it fits (48k/250us/16ch L24 = 576 B, 48k/125us/16ch L24 = 288 B, 192k/125us/16ch L24 = 1152 B) and only oversized payloads split (48k/1ms/16ch L24 = 2304 B -> 2 x 8ch). Extension flows wider than 8 channels are a product EXTENSION, not AES67 media-profile conformance; use standard for third-party interop. With flow_count=0 the plan and destinations are derived (default_dest_ip + flow index, one unique class-D group per flow); explicit rows are used verbatim when supplied.
- **Ports**: the standard RTP port is 5004 for every flow; flows are distinguished by unique multicast groups. Redundant ST 2022-7 legs use port 5006 (primary + 2) and produce a second media section under a=group:DUP. port_policy 0 (strict, default) rejects any divergent per-flow port; port_policy 1 accepts even per-flow ports for migration/lab use. Media ports must be even. Legacy FLP01 sender ports 5010/5012 must migrate to 5004 with unique groups.
- Channels must be 1-256 in total. Optional channel_labels points to an array of channels NUL-terminated UTF-8 strings. NULL selects numbered labels. A NULL session_name selects "NativeSpatial Pipe AES67". Flow spans are zero-based and must cover the whole channel layout once. Each destination endpoint and effective SSRC must be unique; two multicast flows may never share a group (AES67 6.1.3).
- SSRC: a zero ssrc_base selects a random per-session base (RFC 3550 5.1); flow k uses base + k unless the row sets its own SSRC. RTP sequence numbers start at 0 per flow and are continuous except for signalled pacing gaps.
- Ring capacity is 8-1024 ms and prebuffer is at most 1000 ms or the ring capacity. Zero selects 100 ms and 10 ms. Audio is interleaved float32. A non-null channel_map is an array of channels zero-based source indices and may repeat indices. Set input_channels to the interleaved source width, or zero when it equals channels.
- Explicit flows may set payload_type to 96-127 or zero for the default. The default payload type is 96. The source_filter flag emits an RFC 4570 filter for multicast using the actual egress address; default_source_filter applies to generated flows (the scheduler default is on, matching the reference daemon).
- Timestamp mode values 0/1/2 select auto/software/hardware. Software samples the Windows monotonic QPC timebase at UDP boundaries. Hardware currently returns TIMESTAMP_UNAVAILABLE. Auto selects software when no hardware source is available.
- PTP domain is 0-127. An optional eight-octet GM identity is pinned when nonzero. An all-zero multicast address selects 224.0.1.129 and zero event/general ports select 319/320. A nonzero master IP selects unicast test mode. In this mode local event/general ports may request ephemeral bindings (zero) or explicit loopback test ports.
- A zero lock timeout selects 8000 ms. The legacy loss_timeout_ms ABI field remains accepted for compatibility.
- **SAP**: sap=1 enables RFC 2974 announcements. The group defaults to 224.2.127.254:9875 (global scope) and is configurable (for example 239.255.255.255 on the N150 deployment). sap_interval_ms defaults to 30000 and accepts 1000-300000; each repeat adds the RFC 2974 random offset. The message-id hash is a CRC-16 of the announced SDP, so any description change yields a new identity. SAP is sent with IP TTL 255 and deletions are repeated three times. Announcements continue during PTP holdover while RTP still flows, and are withdrawn only when RTP actually pauses or the session stops. Static SDP is available at any time through nsp_aes67_sdp while the session is locked or in holdover.
- **QoS**: RTP sockets are marked AF41 (DSCP 34) and PTP sockets EF (DSCP 46) per AES67-2023 sec 6.2 Table 1. Marking is best effort through IP_TOS.

## Lifecycle and diagnostics

Serialize management calls on each handle. Submit may run concurrently with start, stop and event polling. Quiesce and unbind all callbacks before closing the handle. Successful close destroys it. If close times out, the handle remains valid for a retry. All functions return zero on success except event polling and SDP retrieval.

| Interface result | Meaning |
| --- | --- |
| start returns 0 | Worker reached PTP lock. A STARTED event is available. |
| start returns negative | Startup failed. Codes distinguish invalid state, socket I/O, unavailable timestamp backend and lock timeout. |
| poll returns 1 | A lifecycle event was copied to the output. |
| poll returns 0 | No pending event. |
| PTP_LOST | PTP lock was lost. RTP and SAP continue on the previous fit during holdover; if the deadline expires RTP pauses and the SDP announcement is withdrawn. |
| SENDER_EXITED | An active worker stopped unexpectedly. Inspect its code and detail. |
| stop returns 0 | On a running or finished worker, stop joined it, released its resources, and queued STOP_COMPLETE. |
| stop/close returns JOIN_TIMEOUT | Worker has not joined. Keep the handle alive and retry. |

Counters are diagnostic and scoped to the current or last start. Dropped and late count frames and the other counters count RTP flow datagrams or their bytes. Counters include actual timestamp mode, fit offset, rate and RMS. Retrieve one SDP document per zero-based flow with nsp_aes67_sdp while locked. A NULL buffer with capacity 0 queries required bytes including the terminating NUL.

## Native pool callback

Register nsp_aes67_submit_leased directly as the pool-output callback and pass the handle as its context. Its parameter order and C calling convention match Zig publishPool. The source buffer must contain frames * channels floats and remain readable during the call. A return of 0 transfers ownership and the module calls release_fn(token) exactly once, synchronously after copying into its ring or accounting for the dropped or late frames. A negative return leaves ownership with the producer. The module retains neither the PCM pointer nor the lease token. Use monotonic host frame indices within one start.

## Clocking and network discovery

RTP timestamps are the absolute PTP-epoch media-clock sample position modulo 2^32 (mediaclk:direct=0). For 48 kHz / 1 ms packet k carries timestamp samples_per_packet * k. For 44.1 kHz the same 48-sample step applies while the exact rational deadline is 48/44100 s per packet. A late wake sends the oldest retained slots first, with one packet and one PCM slice per flow per slot. NSP_AES67_CATCH_UP_SLOTS caps the number of older slots per wake (1-10, default 8). If more slots are due, the oldest excess is discarded and RTP sequence/timestamp jump by the same number of slots. Before prebuffer completes and during underrun, each missing slice is zero padded.

The RTP marker bit is always zero. AES67 assigns it no meaning for these payloads (RFC 3550 5.3), and the Merging RAVENNA LKM compares RTP byte 1 literally against the payload type, so an M=1 packet would be dropped as a payload-type error.

The Windows worker requests MMCSS Pro Audio priority and uses a high-resolution waitable timer followed by a short QPC spin whose budget is one eighth of the packet period (capped at 0.5 ms). The worker releases timer and MMCSS resources when it exits.

The follower uses two-step PTPv2 end-to-end Delay_Req exchanges over IPv4. It selects the first eligible announcing source, or the pinned GM if supplied, and clears the fit on identity change or expiry. It accepts time scale Announce messages and preserves the PTP epoch. The monotonic Announce deadline is five times the qualified GM's advertised interval. A clock fit also needs an accepted update within five Announce intervals. The fit gates are eight exchanges spanning at least 750 ms and a rate up to +/-500 ppm. Phase residual RMS is limited to 2 ms with software timestamps or 250 us with hardware. Explicit lock_timeout_ms values must be 100-120000 ms.

NSP_AES67_HOLDOVER_MS accepts 1000-3000 ms and defaults to 2000 ms. During holdover the sender keeps using the last accepted fit, retains the PCM queue, keeps SAP/SDP announced, and withdraws the announcement only when RTP pauses at the holdover deadline. On recovery it re-anchors on the new fit and raises the SDP version (the CRC-16 hash changes, so receivers see a new session version rather than a delete/re-add flap).

Set NSP_AES67_PTP_DIAGNOSTICS=1 to write opt-in stderr snapshots once a second and on lock, holdover and pause transitions. The pacing line reports rate, ptime, samples_per_packet, packetsSent, framesSent, due/caught-up/dropped slots, wake lateness, silent packets and PCM consumption.

One SDP document is available per active flow; the serialized order is v=, o=, s=, c=, t=, optional a=group:DUP, then m= with a=rtpmap (L16 or L24), a=sendonly, a=ptime (milliseconds), a=source-filter, a=sync-time:0, a=framecount, a=clock-domain:PTPv2, a=ts-refclk:ptp=IEEE1588-2008:<uppercase GMID>:<domain>, a=mediaclk:direct=0, optional channel-order and channel-label comments. a=sync-time and a=framecount are RAVENNA conventions; a=clock-domain is an ST 2110 convention; AES67 itself requires ptime, ts-refclk and mediaclk. No SDP exists before the first lock. During holdover the last valid SDP remains available; ts-refclk is never claimed without a PTP-derived GM identity.

## Windows build and artifact path

Build.ps1 uses Rust 1.97.1 x86_64-pc-windows-gnullvm and LLVM-MinGW. The default target is build/nsp-product/runtime. The DLL is at release/nsp_aes67.dll beneath that target. With repo-relative tooling, the script defaults to work/rust/cargo, work/rust/rustup and work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin.

The portable tools used for loopback verification are selected as follows from the assigned worktree:

~~~powershell
$tools = 'C:/WorkSpace/Claw/scratch/lead/nsp-pr1-ci/wt/work'
$llvm = "$tools/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin"
$env:CARGO_HOME = "$tools/rust/cargo"
$env:RUSTUP_HOME = "$tools/rust/rustup"
$env:PATH = "$env:CARGO_HOME/bin;$llvm;$env:PATH"
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_LINKER = 'x86_64-w64-mingw32-clang'
$env:TEMP = $PWD.Path
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
cargo +1.97.1-x86_64-pc-windows-gnullvm test --offline --locked --manifest-path modules/aes67/Cargo.toml
powershell.exe -NoProfile -ExecutionPolicy Bypass -File modules/aes67/Build.ps1 -TargetDirectory modules/aes67/target/product -LlvmBin $llvm
~~~

The optional NSP_AES67_TEST_LOCK_TIMEOUT_MS environment variable sets the loopback fixture's acquire and reacquire deadlines. NSP_AES67_TEST_DLL selects the product DLL for the Windows DLL-loading smoke test. Without a DLL path, the remaining Rust tests run directly against the ABI implementation.