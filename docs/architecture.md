# Architecture and scope

Updated 2026-10-24. The [current architecture](core/current-architecture.md) is authoritative; earlier diagrams and validation snapshots retain their original dates.

The native MinHook adapter snapshots the verified internal object-submission boundary. A C worker sends bounded packets through shared memory and an event. A Rust receiver submits common input to ROAR (currently OLR mode) or native C++ LIRA. Interleaved output stays in the Rust pool for in-place Zig DSP/bypass, then crosses a bounded descriptor queue as leased blocks into ASIO submission. ASIO retains its internal copy, clock matching, SRC and device ring. The old Zig PCM staging/scratch copies are removed; this is not end-to-end zero-copy.

The default managed scheduler is Zig. Native FSA manages endpoints, rules and verified session control. Python still handles preparation supervision and isolated telemetry; Windows PowerShell/WinRT helpers perform system interop. Extra AudioSrv enumeration uses the same pinned MinHook engine and native controller source as capture, without Frida/JS injection. Selected audio threads register MMCSS; this does not make the entire process hard real-time. See [scheduler and lifecycle](scheduler.md).

The current descriptor/payload contract includes object identifiers, static types or dynamic positions, gain, frame counts, lifecycle state, sequence numbers and timing. Object identifiers represent the local observed lifecycle, not a game's internal sound-source identity. Unsupported frame counts, malformed descriptors and discontinuities fail closed according to the existing native validation.

## Technical limits

- The native hook depends on verified Windows binary hashes, offsets and layouts. Those checks are part of the implementation, not a stable public API.
- No custom Spatial Sound Provider registration is provided.
- The standard research entry is single-stream, 480 frames/48 kHz. The default managed path prewarms four internal renderer slots; those slots are not a Windows stream enumeration. Slot reclamation across arbitrary restarts remains incomplete.
- Historical render-only timing must not be confused with DSP completion, device submission, actual playback or acoustic latency; name the measured boundary.
- PCM and telemetry snapshot recording default off. PCM uses an optional bounded side recorder; disk IO no longer forwards playback to DSP/ASIO, and telemetry selection uses memory progress. Quotas, retention and cleanup remain pending.
- Extended gain/distance/EOS semantics, continuous long-duration runs, abnormal recovery and cross-build support remain incomplete.
- System-level injection does not establish anti-cheat compatibility. A previous game replay test was followed by a reported disconnection; causation and general compatibility have not been established. Start with owned fixtures rather than protected multiplayer sessions.
- Shared-memory integration tests require an execution environment allowed to reopen the named mapping/event. AppContainer access denial was reproduced and diagnosed; ordinary non-AppContainer runs pass. This does not establish capture, playback or long-duration acceptance. See [startup validation](receiver-startup.md).

The public source repository is [BestGreed/NativeSpatialPipe](https://github.com/BestGreed/NativeSpatialPipe), with `main` as its default branch and `alpha-dev` for development. It excludes private experiments, raw captures, game assets, complete third-party checkouts and local toolchains. Required extracted ASIO SDK headers and their license notices are included. Its history remains independent; updates are reviewed source exports, not private-history merges.
