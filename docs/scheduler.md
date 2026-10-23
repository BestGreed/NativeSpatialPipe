# Native scheduling and Sonic NSp integration

<!-- alpha-dev-status -->
**2026-10-24 alpha-dev/main:** Seven output layouts, Web configuration, session-resident ASIO and optional isolated recording are integrated. Enumeration now uses native MinHook; preparation retains Python/PowerShell helpers. Earlier desktop, metering and recording paths are explicitly historical. Playback configuration defaults to the user directory; PCM and snapshot archives default off. Storage quotas, 6DoF implementation and long-term device validation remain pending. See [current progress](alpha-dev.md) and [source README](../README.md). Older validation below keeps its original scope.
<!-- /alpha-dev-status -->

The default Zig 0.13.0 scheduler manages backend sessions, process ownership, prewarming, telemetry forwarding, in-pool DSP and shutdown. Rendering is Rust ROAR (OLR mode) or C++ LIRA; C++ ASIO owns device transport. Python 3.12 still handles preparation supervision, isolated telemetry and development tools. FSA owns runtime actions; the separate Zig Web Host reads status and validates/saves configuration without exposing session controls.

The first pipeline is prepared before the Python telemetry handshake; the remaining pipelines and receiver are prepared before enabling capture Hook. Current Zig telemetry selection reads renderer memory progress, not recording file growth. Its control-side checks do not poll inside audio submission. Extra AudioSrv enumeration is separately supervised using native C/MinHook, with the same pinned engine/controller source as capture.

## Build

First follow [the core build instructions](build.md), including the pinned dependencies and `NSP_LLVM_BIN`. Then install the official Zig 0.13.0 Windows x64 compiler in `work/zig-bootstrap/zig-windows-x86_64-0.13.0`, or pass its path with `-Zig`.

```powershell
./tools/endpoint-probe/Build-NativeHook.ps1 -Managed -TestFixtures
./tools/pipeline-init/Build-EnumHook.ps1 -LlvmBin $env:NSP_LLVM_BIN
./tools/telemetry/Build-Meter.ps1
./tools/zig-scheduler/Build.ps1
```

The executable is `build/zig-scheduler/nsp-scheduler.exe`. Zig is a build dependency; the executable does not need a Zig runtime. Existing native DLL dependencies, including the Rust runtime layout, still apply. See [scheduler details](../tools/zig-scheduler/README.md) for the compiler archive hash, CLI, FSA configuration and shutdown contract.

To preview only the control UI, use an absolute Python executable path and a separate absolute state directory:

```powershell
build/zig-scheduler/nsp-scheduler.exe backend-start --home C:/path/to/trial-state --port 18765 --telemetry-port 18766 --python C:/path/to/python.exe
build/zig-scheduler/nsp-scheduler.exe request --home C:/path/to/trial-state --action status
build/zig-scheduler/nsp-scheduler.exe request --home C:/path/to/trial-state --action exit
```

Opening the UI does not enable Hook. Audio startup requires an explicitly verified endpoint and supported, unprotected audiodg instance. This remains a version-pinned research implementation. Configuration changes and deployment should happen after stopping the prior session normally and verifying Hook and endpoint restoration.

## FSA source and artwork

The integration source derives from [Final Spatial Audio](https://github.com/BestGreed/Final_Spatial_Audio), revision `decba9b`. Its original [Unlicense](../tools/fsa-integration/LICENSE) is retained. The source includes the Sonic NSp state adapter, administrator manifest, stereo spinner and verified-ready icon behavior.

FSA ICO artwork is included with the project owner's authorization. Its separate asset notice remains in effect; inclusion does not relicense artwork under the root Apache license or grant rights in third-party marks. Build the optional FSA UI with `Build-Integration.ps1 -FsaOnly`. The independent Zig scheduler does not need the artwork. The build updates scheduler configuration while preserving other rules and snapshots; it does not close or restart an existing FSA/backend process. FSA defaults to `Scheduler=Zig`; an empty `ControlExecutable` resolves the native path from the integration build layout. Missing Zig fails without implicit fallback. Legacy comparisons require explicit `Scheduler=Python` or the build option `-LegacyPythonScheduler`. The managed launcher also defaults to Zig and requires `-LegacyPythonScheduler` for old orchestration. Telemetry still uses the separately configured Python interpreter.

## Historical validation boundary (2026-10-19)

The source revision was checked with seven isolated scheduler integration tests, including default configuration migration and missing-Zig refusal, including ordinary and FullTiming owned-process fixtures, HTTP/authentication, state correlation and recovery refusal. Each fixture used 250 blocks with 128 dynamic objects plus 12 bed channels; its first eight PCM blocks matched the prior Python control path byte for byte. Existing backend and telemetry checks also passed. These results do not guarantee arbitrary game compatibility or end-to-end audio latency.

A single sequential Wwise comparison ran 60 seconds per implementation, using the same dedicated project and 6,000 position updates per run:

| Measurement | Python host | Zig host |
|---|---:|---:|
| Host CPU, whole machine | 2.13% | 1.76% |
| Telemetry CPU, whole machine | 1.34% | 1.16% |
| Host + telemetry | 3.47% | 2.92% |
| Renderer work P99 | 1.447 ms | 1.435 ms |

Both runs reported clean capture/receiver/render accounting, 128 dynamic objects plus 12 bed channels, restored Hook instructions and restored endpoint state. The observed combined CPU reduction was 15.8% for this pair. Host CPU includes native DSP, and renderer work time is not end-to-end latency. These are private controlled-machine measurements; raw audio, Wwise projects, logs and machine identifiers are not distributed.

The old Python entry remains available for comparisons. Legacy generation-1 Hook recovery must use its original controller. Existing Rust stream-binding reclamation limitations are unchanged. Zig is the default managed path; source updates do not automatically hand over another user's running backend.

## Recording policy and deferred storage work

Current defaults are `recording.pcm=false` and `recording.telemetrySnapshots=false`. Optional PCM recording copies pre-DSP samples to a separate bounded pool and file thread; playback handoff does not wait for recording. Snapshot archival has its own thread. A neutral or bypassed DSP does not scan/rewrite samples, while pool ownership and device checks remain.

Playback configuration defaults to `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`, with explicit custom paths supported. Remaining D15 work is quotas, retention and safe cleanup; recording isolation and memory-based telemetry selection are already implemented. ASIO remains active with silence during absent input, rather than applying legacy first-packet/rebuffer timeouts. See the [current project overview](../README.md) and [recording boundary](core/recording-isolation-20261024.md).
