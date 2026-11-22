# Developer guide

The source uses C and C++ for native capture, Windows integration and device
backends; Rust for receiver, rendering and output processing; Zig 0.13.0 for
session orchestration and control; Python/PowerShell for preparation, telemetry
and tooling; and JavaScript for browser configuration and visualization.

## Build and ownership

Start with the [build guide](build.md) and use `tools/Build-Product.ps1`.
Production module ownership is under `modules/`. Supporting scripts under
`tools/` are not a second implementation of those modules.

| Owner | Responsibility |
| --- | --- |
| `modules/fsa-integration` | Tray, endpoint/mode control and user session lifecycle |
| `modules/zig-scheduler` | Startup/stop, owner lease, recovery proofs, output assembly, control and web host |
| `modules/native-capture` | audiodg Hook, controller and native capture/send |
| `modules/native-receiver` | Rust input transport and source handoff |
| `modules/croksor` | Input alignment, ROAR rendering, DSP and output delivery |
| `modules/inferno-windows` | Included AoIP server, network clock and send scheduling |
| `modules/audio-backends` | LIRA, ASIO and WASAPI device backends |
| `modules/lsac-adapter`, `modules/freesurround`, `modules/semantic-separator` | Optional stereo adaptation and upmix components |
| `modules/pipeline-init`, `tools/capture-profile` | Native endpoint preparation and capture-layout selection |
| `modules/telemetry`, `modules/visualization-frontend` | Asynchronous metering, status and web presentation |

The public preparation manager explicitly reports extra AudioSrv enumeration
as unsupported. Build scripts omit that private extension. Preserve the native
endpoint query/select/readback path when changing preparation code.

## Runtime contracts

The coordinator owns control state. Native and Rust threads process audio
blocks; Python does not run a per-block rendering loop. AoIP owns its network
clock and packet-send timing. Device FIFO waits describe output backpressure.

Preserve bounded input/output queues, block dimensions and lease ownership.
Stop and join receiver/bridge readers before releasing pipelines they may still
reference. Startup errors before Hook activation must release initialized
resources. Changes to native exports must update both consumers and build-time
ABI checks.

The current controller owns `owner.lock`. A newly started manager in
`runtime.json` is not an old-session process that must exit before orphan
retirement. Verify the old session's recorded processes and target instance;
do not claim that the original endpoint snapshot was restored when an orphan
was merely retired. Keep recovery reports and user-visible lock state aligned.

## Isolated checks

Joint backend builds use prepared development dependencies. Run
`modules/semantic-separator/Build-JointLive.ps1` with `-DirectML`, `-OrtCpu`,
`-Cpu` (OpenVINO CPU), `-Gpu` or `-PortableNpu`. Supply `-OrtInclude` and
`-OrtRuntime` for ORT headers and runtime DLLs, or `-OpenVino` for an SDK with
`include` and `libs`; `-LlvmBin` selects LLVM-MinGW. Current inputs are ORT
DirectML 1.24.4 and OpenVINO 2026.4.0. Prepare the vocal ONNX model and
FreeSurround DLL separately, then configure absolute primary, CPU fallback,
model and FreeSurround paths. The builder copies local runtime files.

DirectML selects a hardware adapter and falls back to an ORT CPU session on
initialization failure. Croksor retains the configured CPU standby for primary
initialization or worker failure and reports the actual backend. OpenVINO uses
the explicit build target. Runtime DLLs load from the Joint directory or an
absolute directory in its optional UTF-8 `runtime-path.txt`. Runtime download,
installation and first-use backend selection belong to release tooling.

The isolated Rust test
`runtime_selection_tests::external_runtime_and_initialization_fallback` takes
`NSP_JOINT_TEST_PRIMARY` (DirectML), `NSP_JOINT_TEST_CPU` (ORT CPU),
`NSP_JOINT_TEST_MODEL` and `NSP_JOINT_TEST_FS` as absolute paths. Run it with
`cargo test --release --locked --offline --manifest-path modules/croksor/Cargo.toml runtime_selection_tests -- --ignored`.

Build native fixtures and required DLLs before running scheduler checks.
`Build-Product.ps1` explicitly uses `-SkipTests` for its Zig compilation;
invoke the module builder without that switch for its unit tests.

```powershell
./modules/zig-scheduler/Build.ps1 -TargetDirectory build/nsp-product
$env:NSP_SCHEDULER_TEST_EXE = "$PWD/build/nsp-product/nsp-scheduler.exe"
$env:NSP_RENDER_BRIDGE_TEST_DLL = "$PWD/build/nsp-product/runtime/release/nsp_croksor.dll"
python modules/zig-scheduler/Test-Scheduler.py
python -m unittest discover -s modules/pipeline-init -p 'test_*.py'
python tools/Check-MarkdownEncoding.py .
```

Use temporary configuration homes and owned processes. Do not point isolated
fixtures at the user's audiodg or live service ports. The public CI runs the
applicable checks against its own checkout; see [CI scope](ci.md).

Real application capture, audible output, receiver delivery, device removal,
long runs and clean-machine package startup require separate evidence. Keep
measurements tied to their binary, settings and workload. A static risk or an
unreproduced request-ordering window is not a demonstrated functional failure.

## Documentation and releases

Maintain the existing public guides against the exported tree, with the user
guide available in English and Chinese and developer documentation in English. Do not
replace them with internal progress reports. The source manifest distinguishes
unchanged core files, declared public adaptations and independently maintained
public files. Refresh it after final documentation/CI changes, then compare the
core with the selected source revision. Follow the [release guide](release.md)
before distributing a runnable package.


The installed user flow is documented separately in the [user guide](user-guide.md).
The installer owns hardware selection, first-use configuration and upgrade
preservation. A user should not need these source-build commands, module paths
or deployment helpers to complete installation. Sources and development
artifacts are delivered separately from the consumer runtime.
