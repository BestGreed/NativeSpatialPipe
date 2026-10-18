# Native Spatial Pipe

Windows Spatial Audio object-stream capture, native rendering and real-time visualization research. **C and Rust remain the audio core; an optional Zig scheduler now handles orchestration. Python still runs telemetry and development tools.**

Updated **2026-10-19**. Capture → native receiver → ROAR rendering → telemetry → browser visualization is implemented. Own-device ASIO playback is still pending. This is a version-pinned research prototype, not a registered Spatial Sound Provider or a Dolby/DTS bitstream decoder.

## Architecture

```text
Windows object submission
  → MinHook / C capture worker
  → bounded shared memory and events
  → Rust receiver → metadata / ROAR renderer
  → background file writer + bounded object/output telemetry taps
                                      → Python telemetry → HTTP / WebSocket
                                      → browser navigation / spectrum views

FSA Sonic NSp → Zig or Python control host → lifecycle and persistent state
```

Capture, receiver and renderer workers use the MMCSS + individual baseline. The Zig path replaces management and orchestration, not C/Rust DSP or transport. Windows spatial-format setup/restoration still uses PowerShell / WinRT.

Managed Zig startup prepares the first pipeline, waits for Python telemetry readiness, prepares the remaining three pipelines and native receiver, then enables Hook. Telemetry remains part of that startup chain. Building Zig does not switch an existing FSA installation or running session to it.

## Included modules

| Module | Available | Remaining work |
|---|---|---|
| Native audio core | C capture/transport, Rust receiver and ROAR renderer, bounded telemetry taps | Stream-binding reclamation, long-running recovery and broader Windows compatibility |
| Zig scheduler | Persistent backend, HTTP control, FSA commands, ownership, four-pipeline prewarm, telemetry bridge, snapshots and shutdown | Long-running deployment and abnormal recovery acceptance |
| Sonic NSp / FSA | Verified Sonic + Hook state, start/stop/emergency stop, guarded mode transitions, elevation manifest and stereo spinner | Independent deployment compatibility; integration changes have not been merged into upstream FSA main |
| Telemetry | Python service, native C metering with Python fallback, history, contracts, HTTP/WS | Incremental overhead and recovery validation across supported workloads |
| Telemetry routing | Non-real-time file-growth selection every 100 ms; two updates qualify a candidate, one second without progress marks it stale | Remove the file dependency in a later milestone; this policy does not change native receiver selection |
| Frontend | Navigation/spectrum views, object details, cameras, source-relative motion estimates, reconnect intent and bounded diagnostics | Final management UI, connection diagnostics and long-running resource validation |
| Device output | Planned ASIO integration | Own-device playback, physical loopback and end-to-end latency measurement |

The four pipeline slots are internal prewarmed processing resources, not a Windows public stream enumeration. Service availability or an object count alone does not establish fresh audio input.

## Build

Follow [build instructions](docs/build.md) for pinned MinHook/ROAR sources, Rust, LLVM/MinGW and the dependency layout. Toolchains and dependency sources are installed separately. Build commands do not start capture.

```powershell
./tools/Build-Core.ps1 -LlvmBin C:/path/to/llvm-mingw/bin
```

The optional scheduler needs **Zig 0.13.0 at build time**, with no Zig runtime installation required:

```powershell
./tools/zig-scheduler/Build.ps1 -Zig C:/path/to/zig-0.13.0/zig.exe
./tools/fsa-integration/Build-Integration.ps1 -FsaOnly
```

`Build-Core.ps1` sets `NSP_LLVM_BIN` in the current PowerShell session; set it explicitly if building FSA in another session. Outputs are `build/zig-scheduler/nsp-scheduler.exe` and `build/fsa-integration/bin/FinalSpatialAudio.exe`. FSA is optional; it is not needed to build the scheduler. Preserve native runtime dependencies such as `libunwind.dll` beside the relevant generated DLLs.

See [scheduler setup, CLI and FSA configuration](docs/scheduler.md) for compiler verification and deployment details. Python 3.12 is still required for telemetry. Wwise is a development test source, not a normal runtime requirement; private Wwise projects and recorded acceptance inputs are not included.

## Run and inspect

For a frontend-only preview:

```powershell
python tools/visualization-frontend/serve.py
```

Open the printed URL. The standalone server provides synthetic fixtures without enabling Hook. See [telemetry and visualization](docs/telemetry-visualization.md) for a controlled native demo and [frontend setup](tools/visualization-frontend/README.md).

The persistent backend normally serves `http://127.0.0.1:8765/`; telemetry normally uses `ws://127.0.0.1:8766/v1/telemetry`. Actual discovery files take precedence. These services start through their respective entry points; an open page is not evidence of live audio.

The original single-stream research entry remains available:

```powershell
python tools/endpoint-probe/Run-Native.py <new-output-directory> <verified-audiodg-pid> --duration 90
```

It requires a separately prepared endpoint and a verified, unprotected audiodg instance. The current Hook checks an internal system boundary and rejects unsupported images; it is not a supported Windows extension contract. The baseline is one stream, 480 frames at 48 kHz, without raw NSP packet archiving. `--normal-scheduling` disables all three MMCSS registrations for comparison; `--full-timing` requires separately built diagnostic DLLs.

For managed Sonic NSp, use the documented backend/FSA path. The included `Run-GameTelemetry.ps1` requires an explicit verified endpoint; it does not choose a private machine's recorded device. Stop normally and verify Hook instruction and endpoint restoration before changing deployment. No build command automatically restarts the user's backend or migrates FSA configuration. Legacy generation-1 recovery still requires its original controller.

## Validation and limits

- The scheduler was checked with six isolated integration tests and a routing unit test. Ordinary and FullTiming owned-process fixtures each used 250 blocks, 128 dynamic objects plus 12 bed channels; the first eight PCM blocks matched the prior Python control path byte for byte.
- The exported source also passed 31 telemetry and 17 backend tests, the Zig build, and FSA resource/manifest/state/spinner checks. Shared-memory/socket integration checks need an ordinary Windows process environment; AppContainer restrictions have caused access-denied failures. See [startup validation](docs/receiver-startup.md).
- One sequential Wwise comparison ran 60 seconds and 6,000 position updates per implementation. Host + telemetry whole-machine CPU was **3.47% with Python → 2.92% with Zig**, an observed 15.8% decrease for that pair. Host CPU includes native DSP. Renderer work P99 was **1.447 → 1.435 ms**, not end-to-end playback latency. See [measurement boundaries](docs/scheduler.md).
- Historical capture-to-render-completion testing measured P99 **1.436–1.493 ms**, 23,461 packets without loss, and a 3.299 ms maximum. These historical figures do not constitute acceptance of every current feature, machine or long-running workload.

Source provenance and public build/package adaptations are recorded in [the manifest](docs/core-source-manifest.json). Private recordings and the complete research archive are excluded. Some recorded-replay tests need private inputs and are not standalone acceptance tests for this export. Dormant historical Frida/legacy branches are not supported export paths. Hardware playback, native LAN audio and full cross-build compatibility remain incomplete.

## Development recording and storage roadmap

**Continuous full recording is intentionally retained during development** for debugging and retrospective inspection. One continuously active 12-channel, 48 kHz Float32 render writes **8.29 GB/hour** of PCM. Full snapshots with roughly 140 objects added several GB/hour in observed sessions, for about **10–12 GB/hour combined**. Multiple active rendered streams increase PCM output; quiet PCM does not shrink automatically.

The default managed state directory is `%LOCALAPPDATA%/NativeSpatialPipe`, with session output under `sessions/`. Developer tools may use explicit alternative output directories. Old payloads may be manually cleaned; historical metadata does not guarantee raw files remain available.

**D15 storage governance is pending, after D05 audio-interface playback works.** Planned work includes keeping an explicit full-recording mode, adding a lightweight mode and configurable recording directories/limits, protecting active/recovery sessions, and removing telemetry routing's dependence on file growth outside the real-time core. Those limits and automatic cleanup are not implemented or enabled by this release.

## License and FSA assets

Original Native Spatial Pipe code and documentation use **Apache License 2.0**; see [LICENSE](LICENSE), [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.md). ROAR retains its own license and separate patent terms.

The integrated [Final Spatial Audio](https://github.com/BestGreed/Final_Spatial_Audio) source retains its [Unlicense](tools/fsa-integration/LICENSE). FSA artwork is included with the project owner's authorization and is governed by a [separate asset notice](tools/fsa-integration/native/assets/LICENSE.txt), not Apache-2.0 or the source-code public-domain dedication. Source licensing does not grant permission to reuse the artwork in another product or grant rights in Dolby, DTS or other third-party marks. Obtain the applicable permissions before reusing those assets; inclusion does not imply endorsement.

FSA's NSp-specific integration is maintained in this repository for now, without an upstream FSA main-branch merge. Private recordings, Wwise projects, local credentials, toolchains and compiled binaries are not part of this source export.
