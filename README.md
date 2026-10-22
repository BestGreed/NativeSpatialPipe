# Native Spatial Pipe - alpha-dev

Native capture, rendering, and real-time visualization of Windows spatial audio object streams. This is a development source snapshot dated 2026-10-22, not a registered Spatial Sound Provider or a Dolby/DTS bitstream decoder.

**N03-N12 integration is complete within its documented scope. Pool-side DSP/bypass and leased output are connected; the temporary WPF frontend is retired.** Workspace cleanup and source branch synchronization are complete. See [alpha-dev progress](docs/alpha-dev.md) for completed work and remaining tasks.

```text
C Hook / capture -> shared memory -> Rust receiver / common input conversion
 -> ROAR (OLR validated in this round) or LIRA -> Rust interleaved output pool
 -> in-place DSP / bypass -> leased pool blocks -> ASIO
Side path: current file recording -> Python telemetry -> web visualization
Control: native FSA endpoint management -> Zig session coordination / backend worker
```

ASIO retains its internal buffering, sample-rate conversion, and device clock matching. Pool leases replace the Zig PCM queue and scratch copies. Neutral DSP settings skip processing automatically; `outputProcessing.bypass=true` explicitly bypasses common DSP. See [playback JSON configuration](docs/core/playback-json-configuration-20261022.md); the example and schema are under `tools/zig-scheduler`.

Validation covers DVS at 48 kHz / 128 frames, a 12-channel digital network return, and the classic Wwise cases using Atmos Home Theater with ROAR/OLR and DTS:X Home Theater with LIRA. It does not establish compatibility with every driver, acoustic end-to-end latency, or long-term stability. The new output-pool path has produced audible output, but one session's final `OutputTimeout` still needs investigation. Historical performance figures do not automatically apply to this version.

## Build and entry points

Follow [dependencies and core build instructions](docs/build.md) to prepare the pinned MinHook, ROAR, Rust, LLVM/MinGW, and Zig 0.13.0 dependencies. This source repository does not bundle toolchains, private recordings, Wwise projects, or proprietary codec DLLs.

```powershell
./tools/Build-Alpha.ps1 -LlvmBin C:/toolchains/llvm-mingw/bin -Zig C:/toolchains/zig-0.13.0/zig.exe
```

This builds the core, LIRA and ASIO DLLs, and the Zig backend without opening a driver or injecting Hook. See [FSA integration](tools/fsa-integration/README.md) and [scheduler configuration](tools/zig-scheduler/README.md). Building does not deploy or restart FSA. Actual Hook operations requiring administrator privileges must be started explicitly.

The native audio and management path has no .NET runtime dependency. Python remains necessary for telemetry and development tests. The complete SpatialASIO project, temporary WPF frontend, and installer are excluded. A separate web API control host is planned but not implemented; existing telemetry remains read-only.

## Current limitations

Continuous PCM and telemetry recording still occurs. Active 12-channel Float32 PCM consumes about 8.29 GB/hour. D15 recording decoupling and the default recording policy remain pending. Configuration and session data normally reside under `%LOCALAPPDATA%/NativeSpatialPipe`; these runtime files are not included here.

Capture transport faults remain fatal. Temporary ASIO underruns allow bounded recovery, while device faults stop playback safely. LIRA retains its original room projection, clipping, and smoothing behavior. The default OLR mapping uses the full source radius with `scaled/reference=1`; this is a software coordinate contract, not a physical room calibration.

Other pending work includes FSA first-input waiting and error feedback, output-pool session closeout, web API design, and a future native network output path. Inferno has not been integrated. ASIO-internal transfer, scheduling, and latency optimization remain out of scope.

## Licensing and attribution

The root [LICENSE](LICENSE) applies Apache-2.0 to original NSP contributions. LIRA retains its original Apache notices. ASIO device code and the included SDK headers have a GPL-3.0-only scope; a combined program containing them must not be described as Apache-only. See [license scope](LICENSING.md), [third-party notices](THIRD_PARTY_NOTICES.md), and [module licensing](modules/audio-backends/LICENSING.md). FSA source and artwork retain their separate terms.

Thanks to hikari-taritsu (rmj) for the original Spatial ASIO / LIRA contributions. Original author and license notices are retained. See [provenance and export scope](docs/alpha-dev.md) and the [source mapping manifest](docs/core-source-manifest.json). Public `main` retains its earlier baseline; this update targets `alpha-dev`.

[Documentation index](docs/README.md)
