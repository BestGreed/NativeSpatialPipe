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
- A zero lock timeout selects 8000 ms. A zero loss timeout selects 3000 ms. Start and post-loss recovery fail closed. Set sap=1 to enable RFC 2974 session announcements.

## Lifecycle and diagnostics

Serialize management calls on each handle. Submit may run concurrently with start, stop and event polling. Quiesce and unbind all callbacks before closing the handle. Successful close destroys it. If close times out, the handle remains valid for a retry. All functions return zero on success except event polling and SDP retrieval.

| Interface result | Meaning |
| --- | --- |
| start returns 0 | Worker reached PTP lock. A STARTED event is available. |
| start returns negative | Startup failed. Codes distinguish invalid state, socket I/O, unavailable timestamp backend and lock timeout. |
| poll returns 1 | A lifecycle event was copied to the output. |
| poll returns 0 | No pending event. |
| PTP_LOST | RTP pauses during rediscovery. |
| SENDER_EXITED | An active worker stopped unexpectedly. Inspect its code and detail. Detail is the OS error when applicable, otherwise zero. |
| stop returns 0 | On a running or finished worker, stop joined it, released its resources, and queued STOP_COMPLETE. Repeat stops are idempotent. |
| stop/close returns JOIN_TIMEOUT | Worker has not joined. Keep the handle alive and retry. |

Counters are diagnostic and scoped to the current or last start. Dropped and late count frames and the other counters count RTP flow datagrams or their bytes. Counters include actual timestamp mode, fit offset, rate and RMS. Retrieve one SDP document per zero-based flow with nsp_aes67_sdp while locked. A NULL buffer with capacity 0 queries required bytes including the terminating NUL. A sufficiently sized copy returns the same count.

## Native pool callback

Register nsp_aes67_submit_leased directly as the pool-output callback and pass the handle as its context. Its parameter order and C calling convention match Zig publishPool. The source buffer must contain frames * channels floats and remain readable during the call. A return of 0 transfers ownership and the module calls release_fn(token) exactly once, synchronously after copying into its ring or accounting for the dropped or late frames. During PTP reacquisition the module discards incoming frames and returns success with the lease released. A negative return leaves ownership with the producer, including on saturation. The module retains neither the PCM pointer nor the lease token. Use monotonic host frame indices within one start.

## Clocking and network discovery

RTP timestamps are absolute PTP epoch frames modulo 2^32. At the PTP millisecond grid, timestamp = floor(PTP epoch ns / 1,000,000) × 48. Every flow uses the same timestamp for that interval. A late wake skips stale intervals and sends at most one datagram per flow. A gap before prebuffer or an underrun is zero padded.

The follower uses two-step PTPv2 end-to-end Delay_Req exchanges over IPv4. It selects the first eligible announcing source, or the pinned GM if supplied, and clears the fit on identity change or expiry. It accepts time scale Announce messages and preserves the PTP epoch. The fit gates are eight exchanges spanning at least 750 ms, up to ±500 ppm, and an RMS residual up to 250 µs. The reported offset is PTP epoch nanoseconds minus local monotonic nanoseconds. A zero lock timeout selects 8000 ms and a zero loss timeout selects 3000 ms. Explicit timeout values must be 100–120000 ms.

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
