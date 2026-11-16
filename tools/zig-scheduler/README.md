# NSp Zig Scheduling Layer

For the authoritative entrance to the current technology stack and documentation, see [Architecture](../../docs/archive/core/current-architecture.md). Zig is responsible for default scheduling and common DSP; Python is still used for preparation control and independent telemetry, and PowerShell/WinRT still performs system interop. The DEV16 split log below is for historical evidence only, where "unwired", fixed layout, old WPF or limited playback strategies do not constitute the current backlog and may not be reintroduced into the product chain.

2026-10-24 playback location: `%LOCALAPPDATA%/NativeSpatialPipe/playback.json` is now the default. The old installation-adjacent default is copied without replacing existing user data; explicit custom paths remain supported. See [migration](../../docs/archive/core/playback-user-location-20261024.md).

## Current implementation (2026-10-24)

The official host initializes ROAR/OLR or LIRA through `nsp_pipeline_create_live`, supporting seven output layouts, in-pool DSP and pool block leasing. `recording.pcm` and `recording.telemetrySnapshots` are turned off by default; side recording uses independent threads and 32 dedicated recording pools, and file IO is not executed during playback delivery. Telemetry candidate streams are selected by `nsp_pipeline_progress` memory counting and do not rely on recording files. Old Bridge is missing a live export that fails explicitly.

The independent `web-host` is started with FSA, and the default is `8767`. It only queries status, verifies/saves JSON, and does not provide run control. The default playback JSON is `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`, and the explicitly customized `PlaybackConfig` path continues to be valid; the default files in the old installation directory are migrated to user copies, retaining the originals and not overwriting existing targets. By default, save and revision locks do not write to the installation directory. After saving, it will take effect the next time you start the session; the backend status directory remains unchanged.

Default full radius mapping `scaled/1`. Permanent in the ASIO session, muted when no input; the old first packet/rebuffer timeout field is retained but does not take effect. The module runtime is loaded from the DLL directory. Limited actual machine start-up and stop, 14/16-channel actual wiring, 6DoF slot activation and long-term stability verification have been completed.

See [Layout](../../docs/archive/core/output-layout-presets-20261023.md), [Side Recording and ABI](../../docs/archive/core/recording-isolation-20261024.md), and this round of verification (development reference is not provided with the open source code). “Not Deployed/Not Wired” for N05–N12 below only describes the current status.

2026-10-23 Startup fix: Default render bridge, preflight and web candidate paths are unified to `build/roar-native/release/nsp_roar_bridge.dll`, supporting the current default full radius mapping. A startup failure without Hook identity record must be unlocked after process exit, endpoint recovery and real-time Hook baseline evidence, and the original failure report is retained. DTS:X HT real machine start/stop closed loop passed, see Repair and Verification (development reference is not provided with the open source code).

2026-10-23: Fixed [ASIO Session Lifetime](../../docs/archive/decisions/asio-session-lifetime-20261023.md): Continuously mute callbacks when input is absent, no longer trigger first packet wait, rebuffer timeout or count exit. Explicit stops still have a drain period, and device and capture/transmission failures still require a safe exit. The old timeout JSON field remains parsed compatible, the new effective policy is reported via `effectiveOutputLifecycle` / `playbackPolicy`. This update replaces the limited play strategy in the historical rounds below.

**Current status (2026-10-23, alpha-dev):** N03–N12 has completed limited development integration, FSA parameters have been connected, ROAR/OLR and LIRA share common output and ASIO; DVS 12-channel digital loopback and two sets of Wwise HT actual measurements have been completed. In-pool DSP/bypass and pool block leasing are on, temporary WPF is deprecated. ASIO has been changed to resident streaming within the session; independent Web API Host and graphics configuration page have been implemented, only read status, read and write configuration, and no operation control is provided. D15 and OG27QH EDID driver reloading and actual machine return are still tracked separately. The following descriptions of "not yet connected/not actually played" arranged by round only correspond to the current stage and cannot cover the current status.

Public DSP has been moved to the Rust output pool block and supports `outputProcessing.bypass` and neutral configuration auto-skipping. Device owner commits via pool block lease, removes Zig PCM queue and scratch replication; ASIO internal algorithm unchanged. See [Output Pool Record](../../docs/archive/core/output-pool-processing-20261022.md) for the set of candidates and verification.

DEV16 N11 had added standalone desktop client and optional session `playback` configuration; old FSA requests remain compatible with their new parameters wired in N12. **The temporary WPF desktop has been deprecated on 2026-10-22**, session and endpoint management are subject to FSA; public playback configuration and control interfaces continue to be retained. For reasons and historical boundaries, see Deprecated Temporary Desktop (development reference is not provided with the open source code) and N11 Delivery Records (development reference is not provided with the open source code).

DEV16 N10 has been connected to LIRA (Lazy Immersive Rendering Architecture): `--renderer lira --lira-renderer-dll <absolute DLL path> --render-bridge-dll <current bridge path>`. The old `spatial` parameters remain compatible. The default is still OLR; LIRA rejects the OLR-specific position parameter. Shared common inputs, reliable outputs, and ASIO paths, retaining 512-frame inherent latency and full tail; interface, build, and qualification verification (development reference not provided with open source code). This round of independent products is located at `build/dev16-n10-renderer/`, and the running directory has not been replaced.

DEV16 N05 adds `output_control.zig` dedicated backend worker and `asio_control.zig` life cycle adaptation; it will not automatically hook into the current session. See the N05 delivery for the call/generation/exit contract (development reference is not provided with the open source code). Independent complete check: `python tools/asio-output/Test-ControlWorker.py`, only analog backend with fake ASIO DLL; live output wired at N07.

N06 adds the `output_processing.zig` public PCM processor. For configuration and tail contract, see N06 delivery (development reference is not provided with the open source code). Offline check: `python tools/zig-scheduler/Test-OutputProcessing.py`. The default bit is consistent passthrough, and the limiter is turned off by default; the actual playback call is left at N07.

N07 has been connected to the reliable PCM and owner service loop through `output_stream.zig` / `output_playback.zig`, and the official host provides explicit ASIO enable parameters; see Wiring, Parameters and Testing (development reference is not provided with the open source code). `python tools/asio-output/Test-Playback.py` verifies the software closed loop from actual OLR to fake ASIO callback; at this stage, it has not been deployed and no real driver playback has been performed.

N09: The new DEV16 host defaults to `--position-mode scaled --position-reference 1`, maintaining the complete source radius value, only changing the axis, and no additional scaling. `--position-mode direction` retains the old direction baseline; reference only allows positive finite values, and direction only accepts 1. scaled needs to support the incremental export of the new bridge DLL. If it is missing, it will fail explicitly and will not fall back silently. See the N09 record for details and sound changes (development reference is not provided with the open source code). This is not a physical room calibration; it is now replaced with a binary undeployed one. N08 actual machine and network feedback evidence can be found in the corresponding records.

The migration object is the original Python management and orchestration layer, and the existing C Hooks, C capture/shared memory, and Rust receiving/rendering DLLs remain unchanged. Telemetry still starts first in the original order, and calculations and HTTP/WebSocket services continue to use the original Python implementation. Telemetry is not separated from the startup chain at this stage.

## Migrated

- `backend.zig`: Resident backend, single instance, configuration and session persistence, HTTP control and front-end file serving, child process monitoring, stop timeout and recovery lock.
- `control.zig`: FSA's preflight/start/status/stop/emergency-stop, target instance checks, associated status files, and freshness protocols.
- `native.zig`: Directly calls the existing pipeline and receiver C ABI, and calls the native Hook controller; without ctypes/Python orchestration.
- `host.zig`: Four native pipeline preheating, telemetry process management, native receiver startup, Hook life cycle, non-real-time telemetry forwarding, optional snapshot archiving and end draining.
- `routing.zig`: Select the telemetry candidate stream according to the renderer memory progress, polling for 100 ms, invalidation for 1 s, new candidates preempted after two growths; not affected by the recording switch.
- FSA defaults to `[NSP] Scheduler=Zig`; `ControlExecutable` can specify Zig EXE. If left blank, it will be parsed according to the formal build layout. The `Python` configuration is only passed to the telemetry service. There is no implicit fallback for missing Zig; the old management path must be explicitly set to `Scheduler=Python`.

Startup sequence: The first native pipeline is ready → Python telemetry is ready → The remaining three pipelines and the native receiver are ready → Hook is enabled. Failure to start telemetry prevents hooks from being enabled.

Exit sequence: Confirm Hook closed and resume instructions → Wait for captured packet reception → Receiver stops and confirms thread exit → Telemetry bridging/recording thread exit → Render drain and read last tap → Destroy pipeline → Close telemetry. Unable to confirm that resources are retained when the Hook/receiver is stopped and the on-site process is not forcibly killed.

The setting and recovery of space format still use the existing `Run-GameTelemetry.ps1` (Windows comes with PowerShell/WinRT); its native branch starts the Zig host directly and does not execute `Sync-Hook.py`. At this stage, only the official hosting mode will be migrated, and the historical Frida, offline experiments, and original Python testing tools will be retained.

## Build

Fixed use of Zig 0.13.0 to avoid compiler standard library version drift. The compiler is used only for building, no Zig installation is required for runtime.

- Official download: <https://ziglang.org/download/0.13.0/zig-windows-x86_64-0.13.0.zip>
- ZIP SHA-256:`d859994725ef9402381e557c60bb57497215682e355204d754ee3df75ee3c158`
- The default tool chain location: `work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe`, you can also pass `-Zig` to the build script.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/zig-scheduler/Build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/fsa-integration/Build-Integration.ps1 -FsaOnly
python tools/zig-scheduler/Test-Scheduler.py
```

Output: `build/zig-scheduler/nsp-scheduler.exe`. Existing native DLLs and their dependencies are still retained according to the current directory layout; there is no repackaging or rebuilding of the audio core at this stage.

## Live switching

Default paths are now unified to Zig; integration builds migrate FSA's scheduling configuration and preserve rules/snapshots, but do not restart live processes. Do not automatically restart the live backend for validation; existing Python management services must exit gracefully during an explicitly authorized downtime window before taking over.

In the shutdown window explicitly selected by the user, first stop the NSp normally through the original entrance and confirm the command and endpoint recovery, and then exit the old control backend. FSA exit must also be confirmed and completed, and then modify `final-spatial-audio.ini` at the upper level of the official build directory:

```ini
[NSP]
Scheduler=Zig
ControlExecutable=C:\path\to\NativeSpatialPipe\build\zig-scheduler\nsp-scheduler.exe
Python=<absolute path to existing telemetry Python>
```

After restarting the official FSA, the menu still uses the original interface. `ControlScript` can be reserved for explicit `Scheduler=Python` comparison, but Zig does not implement it by default. While the original Python management backend is still alive, Zig will refuse to take over and will not actively terminate it. Game hosting scripts and individual one-minute tests also default to Zig, Python controls must be explicitly selected; standard single-stream study scripts retain their historical measurement bounds.

You can also only start an independent control service without audio for viewing the interface (Hook will not be enabled):

```powershell
build/zig-scheduler/nsp-scheduler.exe backend-start --home build/zig-trial-state --port 18765 --telemetry-port 18766 --python <python.exe absolute path>
build/zig-scheduler/nsp-scheduler.exe request --home build/zig-trial-state --action status
build/zig-scheduler/nsp-scheduler.exe request --home build/zig-trial-state --action exit
```

`--home` should use an absolute path or be executed from the same working directory; FSA formal access uses the original `%LOCALAPPDATA%/NativeSpatialPipe` state directory. The port of the standalone UI preview does not automatically overwrite the telemetry address saved by the front end.

## Validation Scope and Limitations

The rendering selection of DEV16's new playback path is divided into two levels: `--renderer roar --renderer-mode olr`, or `--renderer lira --lira-renderer-dll <absolute path>`. The default is ROAR/OLR; the old `--renderer olr` is only a compatibility abbreviation. The JSON playback configuration corresponds to `renderer: "roar", mode: "olr"` or `renderer: "lira", mode: null`, and the report records `renderer` and `rendererMode` respectively. This round does not access other ROAR modes; `--asio-channel-order olr` only specifies the existing channel arrangement. For the responsibilities and historical scope, see [Boundary Correction](../../docs/archive/core/renderer-backend-boundary-20261021.md).

Isolation testing covers local HTTP, authentication, single instance, FSA native entry and associated disk status, configuration saving, failure recovery evidence, and two in-house fixture audio processes: normal/FullTiming. 250 packets per round, 128 dynamic objects plus 12 static channels, output 12 channels, confirm Hook recovery, capture and transmission library release, receive and render emptying, snapshot recording.

Detailed results are generated at `build/zig-scheduler/fixture-validation.json` and `full-timing-validation.json`. Fixtures are not game or device playback acceptance and do not represent completion of real-world game latency, long-term stability, or CPU benchmark testing. The native hypervisor's PE import checks should also be understood separately from existing DLL dependencies.

The two modes also compare the same input submitted by the original Python control interface, and the actual PCM bytes of the first 8 rendering blocks are exactly the same. The original main line queue capacity of 64, the remaining pipeline capacity of 8, MMCSS settings and telemetry 1 MiB UDP send buffer are retained.

`Benchmark-Control.py` only compares the FSA status query in the independent free directory: the median time of the last 20 samples of this machine is about 98.9 ms for Python and 23.1 ms for Zig. It covers program startup and disk status writing, not core audio or telemetry calculation performance conclusions. The abstract is located at `docs/scheduler.md`.

The problem with native Rust's handling of block binding recycling has not been modified in this migration. Failure recovery of the old hookGeneration=1 needs to be completed by the original control entry first; the new hosting session uses generation=2, and Zig returns unknown and remains locked for the unsupported old recovery state.

Python now only hosts the telemetry service in the default hosting path, lab/test scripts can still use Python. Zig is the default schedule; real session switching needs to be performed during user-authorized downtime.

## One-minute actual measurement and storage schedule (2026-10-19)

Completed dedicated Wwise project Python/Zig one minute comparison, 6,000 position updates per round, 128 dynamic objects + 12 bed channels, capture/receive/render checks and Hook/endpoint recovery passed. The CPU of the host plus telemetry machine is 3.47% → 2.92%, and the sequence of this group decreases by about 15.8%; the host contains a native DSP. Rendering work P99 is 1.447→1.435 ms, not device end-to-end latency. This result does not replace long-term, exception recovery, or cross-machine acceptance.

The development phase continues with full recording and new snapshot recording at approximately 10 Hz, supporting review and backtracking. 12-channel Float32/48 kHz PCM is about 8.29 GB/h, and snapshots have been observed about 10–12 GB/h; `sessions/` in the default user status directory also carries these large volumes of data. D15 disk placement management will be implemented after the audio interface actually produces sound. Recording or cleaning will not be automatically closed at this time. This storage behavior should be understood before formal deployment; complete logging is still required for current telemetry file growth and selection streams.

For the unified JSON fields, default values, channel order and read-only verification entry of DEV16 merge function, see [Playback Configuration Instructions](../../docs/archive/core/playback-json-configuration-20261022.md). Added DSP, radius, custom routing and existing output policy fields; use the client and host built in the same batch, and the existing running instance is not replaced.
## Web configuration host (2026-10-23)

`nsp-scheduler.exe web-host --fsa-config <absolute FSA INI> --owner-pid <FSA PID>` runs the separate loopback-only configuration/status server on `http://127.0.0.1:8767/`. FSA starts it without opening a browser; triple-click the tray icon to open the existing frontend. The new configuration page edits the existing playback and backend JSON contracts, validates and atomically saves with a revision check. Saving never starts/restarts a session or opens an audio device. Playback changes apply on the next session; backend defaults require backend restart. Runtime control is intentionally not exposed. See [the implemented contract](../../docs/archive/decisions/web-configuration-host-20261023.md).
