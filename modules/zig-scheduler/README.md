# NSP native scheduling and configuration host

Updated on 2026-11-08. For the current module responsibilities and validation status, see [Active Progress](../../README.md#goals-support-and-progress). Phase N05–N12 wiring and old deployment statements have been moved to Historical Snapshot (reference outside this source tree).

## Responsibilities

Zig is responsible for FSA control protocol, session, owner and stop attestation, configuration verification, ASIO owner control, coordination of the persistent AoIP process and general permissions Web Host. PCM is processed by the C/Rust/C++ native module; the public DSP is in the Rust writer, `output_processing.zig` only retains the configuration/timing/ABI, and does not process PCM with block-by-block callbacks.

Native FSA is the product entry point, and the scheduler only supports Zig. The old Python control backend and its diagnostics have been fully archived, and the FSA and game hosting portals no longer accept old scheduling selections. Python/PowerShell are still used for preparation/telemetry and front-end WinRT format selection/recovery respectively; "Zig scheduling" cannot be said to mean that the entire management chain has been native.

## Build

- Existing ASIO host: `powershell -ExecutionPolicy Bypass -File modules/zig-scheduler/Build.ps1`, output `build/nsp-product/nsp-scheduler.exe`.
- AoIP supporting host/bridge: `powershell -File tools/Build-Product.ps1 -Python <python.exe>`.
- Zig is fixed in 0.13.0; ABI mismatch must be rejected, host and bridge are used as a set. Inferno uses modules/inferno-windows in the warehouse, and the C header files come from modules/native-capture; ROAR and the tool chain still have local preparation dependencies, and are not released installers. Network diagnostics are in tools/network-diagnostics, not the AoIP product startup step.

The new version first builds an independent candidate, and then replaces it after shutting down FSA/NSP normally; it does not hot overwrite the loaded DLL. Different build directories do not mean the same capabilities or deployment.

## Run entry

| Command/Module | Current Use |
| --- | --- |
| Control CLI/`serve` | FSA sessions, status, protected stop and resume; mainly implements `control.zig/backend.zig` |
| `host` | Capture, rendering and output; ASIO/WASAPI uses session host, unifies WASAPI to connect four space entrances and one PCM to the same ROAR instance |
| `media-foundation` | FSA control portal, press play JSON to call the MFT controller, and write out the user directory `mft-status.json` |
| `standby-start` / `aoip-resident` | AoIP resident network host owned by FSA, mute without PCM |
| `resident-session` | Control the waiter and hook the capture session to the resident host; do not transmit PCM |
| `web-host` | Common permission status/configuration/static front end, default 127.0.0.1:8767, does not expose audio start and stop |
| `validate-playback --playback-config <absolute-json>` | Configuration verification and valid configuration instructions of the device without opening |
| Backend `select-output` action | AoIP stream selection when running, does not equal backend hot switch; needs to match owner/session |

For configuration, see [JSON Schema](playback.schema.json), [Module Switch](../../docs/archive/core/playback-json-configuration-20261022.md#radius-recovery-and-new-switches) and [WASAPI Example](playback.wasapi.example.json). `validate-playback` reports core `outputLayout` and physical `deviceOutputLayout` respectively. Re-establish the corresponding session or persistent AoIP process after modifying the output path. AoIP runtime selects streams using independent interfaces.

The general user directory is `%LOCALAPPDATA%/NativeSpatialPipe`, and the default playback file is `playback.json`; the explicit FSA INI PlaybackConfig can be overridden, and the current AoIP candidate uses the same directory `output.json`. `--home` is used for explicit isolation, and the isolation test status cannot be used to pretend to be an actual user session.

## Validation and outstanding items

The existing configuration/routing/control/output pool test will continue to be retained. For specific commands and fixtures, see the same directory test, `tools/integration-tests` and `tools/aoip/Test-Configuration.py`. Software testing, FSA limited live network, and user confirmation are recorded separately and do not replace each other.

P0 still includes fixed hash/profile cross-system compatibility, Hook stability, deterministic capture binding, Inferno dependency closure, output fault diagnosis and resident startup failure recovery; WDM, long-term SRC, configuration loaded revision and telemetry fault isolation have not yet been completed. See Repair Plan (reference outside this source tree) for the sequence. The historical "Missed N07/Only waiting for the first round of real audio" is no longer the current status.
