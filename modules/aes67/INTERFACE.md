# AES67 module C ABI

The session-scoped Windows DLL is nsp_aes67.dll. The DLL owns PTP, RTP, optional SAP, clocks, the PCM ring and its worker. The host supplies validated playback settings, submits PCM and handles lifecycle events. See [nsp_aes67.h](nsp_aes67.h).

## Configuration

Create with nsp_aes67_create(&session) and configure before starting. Set struct_size to sizeof(NspAes67Config). The module copies configuration, flows and labels during configure. Sample rate must be 48000. Channels must be 1–256. Optional channel_labels points to an array of channels NUL-terminated UTF-8 strings. NULL selects numbered labels. A NULL session_name selects “NativeSpatial Pipe AES67”.

- IPv4 fields hold four octets. Ports and SSRCs are host-order integers. An all-zero interface selects the system default. Flow spans are zero-based and must cover the whole channel layout once, with 1–8 channels per flow. Each destination endpoint and effective SSRC must be unique. TTL 0 uses default_ttl (default 1). SSRC 0 selects ssrc_base + flow_index (default base 0x4E535000).
- With flow_count=0 the module partitions the channels in groups of eight. The default port increases by two per flow. A zero default port selects 5004 and a zero default destination selects 239.69.0.1.
- Ring capacity is 8–1024 ms and prebuffer is at most 1000 ms or the ring capacity, whichever is smaller. Zero selects 100 ms and 10 ms respectively, with prebuffer clamped to the ring capacity. Audio is interleaved float32. The sender emits 48 frames per 1 ms packet. Tail is a boolean annotation on a PCM block. Keep the session running until the host has finished submitting and allowing time for those packets to play.
- A non-null channel_map is an array of channels zero-based source indices and may repeat indices. A null map selects channels in input order. Set input_channels to the interleaved source width, or zero when it equals channels. The callback receives the source width.
- Explicit flows may set payload_type to 96–127 or zero for the default. The default payload type is 96 when the config leaves it zero. The source_filter flag emits an RFC 4570 filter for multicast using the actual egress address. default_source_filter applies to generated flows.
- Timestamp mode values 0/1/2 select auto/software/hardware. Software samples the Windows monotonic QPC timebase at UDP boundaries. Hardware currently returns TIMESTAMP_UNAVAILABLE. Auto selects software when no hardware source is available.
- Use PTP domain 0–127. An optional eight-octet GM identity is pinned when nonzero. An all-zero multicast address selects 224.0.1.129 and zero event/general ports select 319/320. A nonzero master IP selects unicast test mode. In this mode local event/general ports may request ephemeral bindings (zero) or explicit loopback test ports. For multicast, zero local ports select the corresponding event/general ports.
- A zero lock timeout selects 8000 ms. The legacy loss_timeout_ms ABI field remains accepted for compatibility. Loss timing comes from the GM’s Announce cadence. Set sap=1 to enable RFC 2974 session announcements.

## Lifecycle and diagnostics

Serialize management calls on each handle. Submit may run concurrently with start, stop and event polling. Quiesce and unbind all callbacks before closing the handle. Successful close destroys it. If close times out, the handle remains valid for a retry. All functions return zero on success except event polling and SDP retrieval.

| Interface result | Meaning |
| --- | --- |
| start returns 0 | Worker reached PTP lock. A STARTED event is available. |
| start returns negative | Startup failed. Codes distinguish invalid state, socket I/O, unavailable timestamp backend and lock timeout. |
| poll returns 1 | A lifecycle event was copied to the output. |
| poll returns 0 | No pending event. |
| PTP_LOST | SDP/SAP availability is withdrawn. RTP continues on the previous fit during holdover and pauses if the deadline expires. |
| SENDER_EXITED | An active worker stopped unexpectedly. Inspect its code and detail. Detail is the OS error when applicable, otherwise zero. |
| stop returns 0 | On a running or finished worker, stop joined it, released its resources, and queued STOP_COMPLETE. Repeat stops are idempotent. |
| stop/close returns JOIN_TIMEOUT | Worker has not joined. Keep the handle alive and retry. |

Counters are diagnostic and scoped to the current or last start. Dropped and late count frames and the other counters count RTP flow datagrams or their bytes. Counters include actual timestamp mode, fit offset, rate and RMS. Retrieve one SDP document per zero-based flow with nsp_aes67_sdp while locked. A NULL buffer with capacity 0 queries required bytes including the terminating NUL. A sufficiently sized copy returns the same count.

## Native pool callback

Register nsp_aes67_submit_leased directly as the pool-output callback and pass the handle as its context. Its parameter order and C calling convention match Zig publishPool. The source buffer must contain frames * channels floats and remain readable during the call. A return of 0 transfers ownership and the module calls release_fn(token) exactly once, synchronously after copying into its ring or accounting for the dropped or late frames. After holdover expires, the paused module discards incoming frames during PTP reacquisition and returns success with the lease released. A negative return leaves ownership with the producer, including on saturation. The module retains neither the PCM pointer nor the lease token. Use monotonic host frame indices within one start.

## Clocking and network discovery

RTP timestamps are absolute PTP epoch frames modulo 2^32. At the PTP millisecond grid, timestamp = floor(PTP epoch ns / 1,000,000) × 48. Every flow uses the same timestamp for that interval. A late wake sends the oldest retained slots first, with one packet and one PCM slice per flow per slot. Set NSP_AES67_CATCH_UP_SLOTS to 1–10 to cap the number of older slots per wake. It defaults to 8, allowing up to 9 packets per flow including the current slot. If more slots are due, discard only the oldest excess, advance the PCM cursor for active playback, and advance RTP sequence and timestamp by the same number of slots. The first retained packet sets the RTP marker. Before prebuffer completes and during underrun, each missing slice is zero padded.

The Windows worker requests MMCSS Pro Audio priority and uses a high-resolution waitable timer followed by up to 0.5 ms of QPC spin. If the high-resolution timer is unavailable, the worker requests a 1 ms timer period and uses the thread sleep fallback. The worker releases timer and MMCSS resources when it exits.

The follower uses two-step PTPv2 end-to-end Delay_Req exchanges over IPv4. It selects the first eligible announcing source, or the pinned GM if supplied, and clears the fit on identity change or expiry. It accepts time scale Announce messages and preserves the PTP epoch. The monotonic Announce deadline is five times the qualified GM's advertised interval (2^logMessageInterval seconds). A clock fit also needs an accepted update within five Announce intervals. Bad fits retain the last accepted mapping while this budget remains. Delay_Resp must match the selected source, domain, requestingPortIdentity and sequenceId within one second of Delay_Req. The fit gates are eight exchanges spanning at least 750 ms and a rate up to ±500 ppm. Phase residual RMS is limited to 2 ms with software timestamps or 250 µs with hardware. The reported offset is PTP epoch nanoseconds minus local monotonic nanoseconds. Explicit lock_timeout_ms values must be 100–120000 ms.

The process environment variable NSP_AES67_HOLDOVER_MS accepts 1000–3000 ms and defaults to 2000 ms. During holdover, the sender keeps using the last accepted fit, retains the PCM queue, and withdraws PTP declarations. On recovery it uses the newly accepted fit. It preserves the packet grid when the same GM returns within 1 ms of the retained fit and marks a discontinuity when it must re-anchor. After the holdover deadline RTP pauses and lock_timeout_ms bounds further reacquisition.

Set NSP_AES67_PTP_DIAGNOSTICS=1 to write opt-in stderr snapshots once a second and on lock, holdover and pause transitions. The snapshots include state, actual timestamping mode, offset_ns, rate_ppm, fit_rms_ns, rms_limit_ns, rate_limit_ppm, holdover_ms, and announce_interval_ns. Each of announce, sync, follow_up, delay_req and delay_resp has received, matched and rejected counters. For Sync and Follow_Up, matched counts accepted components. For Delay_Resp, matched counts responses tied to a request before path and fit quality validation. The snapshots include delay_req_sent, delay_req_icmp_unreachable, delay_resp_timeout, invalid_packets, last_decode_error, last_delay_reject, last_fit_reject, and last_lost_reason. Loss reasons include announce_timeout, clock_fit_stale and grandmaster_changed.

Each diagnostic sample also emits a pacing line with Unix epoch nanoseconds (unix_ns), the active timer and priority, max_catch_up_slots, packetsSent (successful flow datagrams), framesSent (48 frames per successful flow datagram), due_slots, caught_up_slots (older retained grid slots dispatched), dropped_slots (excess grid slots), max_wake_late_us, p99_wake_late_us, ring_dropped (PCM frames), and send_errors (RTP datagram send failures). The pacing line also reports silent_packets (flow datagrams whose 1 ms PCM slice was zero padded because the ring was not prebuffered or had underrun), ring_frames (PCM frames buffered when the line is emitted), and the PCM consumed since the previous line: consumed_frames, nonzero_frames (frames with at least one nonzero channel sample), peak_abs (largest absolute sample, 3 decimals) and the cumulative nonzero_frames_total. Slot counts are shared across flows. Wake lateness is measured against the oldest due slot for each dispatch. The session-wide p99 uses 10 µs buckets and reports the maximum if the percentile exceeds the histogram range.

The UDP follower accepts default-profile PTPv2.0 (IEEE 1588-2008, version byte 0x02) and PTPv2.1 (IEEE 1588-2019, 0x12). The two versions share the base message offsets used here. The follower skips well-formed trailing TLVs and rejects unsupported versions and SDO IDs. Outgoing Delay_Req uses the interoperable v2.0 encoding.

One SDP document is available per active flow. Optional SAP announces on UDP 9875, with 239.255.255.255 for administratively scoped RTP groups and 224.2.127.254 otherwise. The module withdraws announcements on loss or stop. RTCP is deferred.

## Windows build and artifact path

Build.ps1 uses Rust 1.97.1 x86_64-pc-windows-gnullvm and LLVM-MinGW. The default target is build/nsp-product/runtime. The DLL is at release/nsp_aes67.dll beneath that target. Pass -TargetDirectory to set a different output root and -LlvmBin to override LLVM discovery. With repo-relative tooling, the script defaults to work/rust/cargo, work/rust/rustup and work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin.

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
