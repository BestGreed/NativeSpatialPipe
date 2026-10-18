# Native scheduling and Sonic NSp integration

The optional Zig 0.13.0 scheduler replaces Python orchestration: backend HTTP control, persistent session/configuration files, FSA commands, process ownership, four-stream prewarming, receiver startup, telemetry forwarding, snapshots and orderly shutdown. Capture, transport and rendering remain C/Rust. Python 3.12 still runs telemetry and development tests; this update does not remove that dependency.

The first pipeline is prepared before the Python telemetry handshake. The remaining pipelines and receiver are then prepared before enabling Hook. Telemetry routing checks file growth every 100 ms outside the real-time audio path; two updates qualify a new candidate, with a one-second stale timeout. It does not change receiver routing or add checks to audio submission.

## Build

First follow [the core build instructions](build.md), including the pinned dependencies and `NSP_LLVM_BIN`. Then install the official Zig 0.13.0 Windows x64 compiler in `work/zig-bootstrap/zig-windows-x86_64-0.13.0`, or pass its path with `-Zig`.

```powershell
./tools/endpoint-probe/Build-NativeHook.ps1 -Managed -TestFixtures
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

FSA ICO artwork is included with the project owner's authorization. Its separate asset notice remains in effect; inclusion does not relicense artwork under the root Apache license or grant rights in third-party marks. Build the optional FSA UI with `Build-Integration.ps1 -FsaOnly`. The independent Zig scheduler does not need the artwork. The build does not close an existing FSA process or replace its active configuration.

## Validation boundary

The source revision was checked with six isolated scheduler integration tests, including ordinary and FullTiming owned-process fixtures, HTTP/authentication, state correlation and recovery refusal. Each fixture used 250 blocks with 128 dynamic objects plus 12 bed channels; its first eight PCM blocks matched the prior Python control path byte for byte. Existing backend and telemetry checks also passed. These results do not guarantee arbitrary game compatibility or end-to-end audio latency.

A single sequential Wwise comparison ran 60 seconds per implementation, using the same dedicated project and 6,000 position updates per run:

| Measurement | Python host | Zig host |
|---|---:|---:|
| Host CPU, whole machine | 2.13% | 1.76% |
| Telemetry CPU, whole machine | 1.34% | 1.16% |
| Host + telemetry | 3.47% | 2.92% |
| Renderer work P99 | 1.447 ms | 1.435 ms |

Both runs reported clean capture/receiver/render accounting, 128 dynamic objects plus 12 bed channels, restored Hook instructions and restored endpoint state. The observed combined CPU reduction was 15.8% for this pair. Host CPU includes native DSP, and renderer work time is not end-to-end latency. These are private controlled-machine measurements; raw audio, Wwise projects, logs and machine identifiers are not distributed.

The old Python entry remains available for comparisons. Legacy generation-1 Hook recovery must use its original controller. Existing Rust stream-binding reclamation limitations are unchanged. The scheduler is an optional candidate path; this export does not imply that a user's running deployment was migrated.
