# Native Spatial Pipe

Windows spatial-audio object capture, native rendering and real-time visualization. Updated 2026-10-24 for `alpha-dev` and `main`. This is a build-specific research implementation, not a registered Windows Spatial Sound Provider or a Dolby/DTS bitstream decoder.

## Current implementation

```text
MinHook / C capture -> shared memory -> Rust receiver / common input
 -> ROAR (OLR mode) or C++ LIRA -> interleaved output pool
 -> in-place DSP / bypass -> leased pool blocks -> ASIO owner
Optional pre-DSP copy -> bounded recording pool -> dedicated file writer
Independent telemetry -> Python metering/history -> browser visualization
Native FSA -> Zig session coordination and independent Web configuration host
```

Both renderers support **5.1.2, 5.1.4, 7.1.2, 7.1.4, 7.1.6, 9.1.4 and 9.1.6**, using common channel order and speaker-identity calibration/routing migration. ROAR's 6DoF measurement slot is reserved and cannot be selected yet. See [output layouts](docs/core/output-layout-presets-20261023.md).

ASIO retains its device buffering, sample-rate conversion and clock matching. It remains active for the session and sends silence without input. DSP runs in the output pool; no Zig PCM staging queue or scratch copy is required before ASIO submission. The audio handoff thread does not write recording files.

PCM recording and telemetry JSONL archival are independent and **disabled by default**:

```json
"recording": {"pcm": false, "telemetrySnapshots": false}
```

Enabling PCM recording copies pre-DSP audio into a dedicated 32-block pool. A slow or failed recorder drops recording blocks and reports them without retaining playback leases. Live telemetry and in-memory history continue without archival. See [recording isolation](docs/core/recording-isolation-20261024.md).

## Configuration and entry points

FSA starts a separate Zig Web API Host at `http://127.0.0.1:8767/`; triple-click its tray icon to open the default browser. The configuration page reads, validates and saves playback/backend JSON. Saving does not apply settings automatically. Start/stop, mode changes, Hook and EDID actions remain exclusively with FSA. Python telemetry stays read-only and separate. Standalone visual preview uses port 8765.

Playback configuration defaults to `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`. Existing installation-adjacent `playback.json` is copied without deleting the original or overwriting an existing user file. Explicit custom `PlaybackConfig` paths remain supported. Default saves do not write the installation INI. See [migration](docs/core/playback-user-location-20261024.md) and [playback fields](docs/core/playback-json-configuration-20261022.md).

## Build

Prepare the pinned dependencies in [build instructions](docs/build.md), then run:

```powershell
./tools/Build-Alpha.ps1 -LlvmBin C:/toolchains/llvm-mingw/bin -Zig C:/toolchains/zig-0.13.0/zig.exe
```

Building does not open a driver, inject Hook, deploy or restart FSA. ASIO/LIRA modules require matching `libc++.dll` and `libunwind.dll` alongside them. The native audio path uses C/Rust/C++; Zig manages coordination and configuration, Python supplies telemetry and tests, and JavaScript runs in the browser. The product does not require .NET; the retired WPF frontend is excluded.

## Validation and remaining work

Development validation includes bounded recording/failure checks, four archive combinations with an owned 140-slot capture fixture, seven-layout renderer checks, Web configuration tests and actual FSA/ASIO idle start/stop. Earlier DVS 48 kHz/128-frame 12-channel network return and classic Wwise HT tests retain their original scope. Private captures and evidence payloads are not distributed here.

These checks do not establish all-layout physical playback, 14/16-channel network routing, acoustic latency or long-term stability. Storage quotas, retention/cleanup policies, 6DoF measurement handling, detailed startup error presentation and a native network output remain future work. Inferno is not integrated. ASIO-internal scheduling and latency optimization remain out of scope. An earlier output-session `OutputTimeout` still needs targeted sustained-input/stop validation; later idle success does not erase that evidence.

## Licensing and attribution

Original NSP contributions retain Apache-2.0. LIRA keeps its original Apache notices. ASIO device code and SDK headers retain GPL-3.0-only scope; a combined program containing them must not be described as Apache-only. See [license scope](LICENSING.md), [third-party notices](THIRD_PARTY_NOTICES.md) and [module licensing](modules/audio-backends/LICENSING.md). FSA code and artwork retain their separate terms.

Thanks to hikari-taritsu (rmj) for the Spatial ASIO / LIRA contributions. Original notices are preserved. Public `main` and `alpha-dev` carry the reviewed source export, without importing private Git history. See [provenance](docs/alpha-dev.md), [source manifest](docs/core-source-manifest.json) and [documentation index](docs/README.md).
