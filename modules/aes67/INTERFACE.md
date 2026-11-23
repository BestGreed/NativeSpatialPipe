# AES67 module C ABI

The session-scoped Windows DLL is nsp_aes67.dll. The DLL owns PTP, RTP, optional SAP, clocks, the PCM ring and its worker. The host supplies validated playback settings, submits PCM and handles lifecycle events. See [nsp_aes67.h](nsp_aes67.h).

## Configuration

Create with nsp_aes67_create(&session) and configure before starting. Set struct_size to sizeof(NspAes67Config). The module copies configuration, flows and labels during configure. Sample rate must be 48000. Channels must be 1–256. Optional channel_labels points to an array of channels NUL-terminated UTF-8 strings. NULL selects numbered labels. A NULL session_name selects “NativeSpatial Pipe AES67”.

- IPv4 fields hold four octets. Ports and SSRCs are host-order integers. An all-zero interface selects the system default. Flow spans are zero-based and must cover the whole channel layout once, with 1–8 channels per flow. Each destination endpoint and effective SSRC must be unique. TTL 0 uses default_ttl (default 1). SSRC 0 selects ssrc_base + flow_index (default base 0x4E535000).
- With flow_count=0 the module partitions the channels in groups of eight. The default port increases by two per flow. A zero default port selects 5004 and a zero default destination selects 239.69.0.1.
- Ring and prebuffer durations use milliseconds. Zero selects 100 ms and 10 ms respectively. Audio is interleaved float32. The sender emits 48 frames per 1 ms packet. Tail is a boolean annotation on a PCM block. Keep the session running until the host has finished submitting and allowing time for those packets to play.
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

Register nsp_aes67_submit_leased directly as the pool-output callback and pass the handle as its context. Its parameter order and C calling convention match Zig publishPool. The source buffer must contain frames * channels floats and remain readable during the call. A return of 0 transfers ownership and the module calls release_fn(token) exactly once, synchronously after copying into its ring. A negative return leaves ownership with the producer, including on saturation. The module retains neither the PCM pointer nor the lease token. Use monotonic host frame indices within one start.
