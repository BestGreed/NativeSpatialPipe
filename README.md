# Native Spatial Pipe

Capture Windows spatial-audio objects, render them for your speaker setup, and explore the sound through live visualization.

NSP brings application audio capture, selectable renderers, device output and FSA session management into one configurable workflow. The project is currently in alpha development.

## Features

- Capture spatial-audio object streams from supported Windows audio modes.
- Choose ROAR's OLR speaker rendering or the alternative LIRA renderer.
- Output through ASIO with speaker routing, per-channel calibration and optional DSP.
- Use **5.1.2, 5.1.4, 7.1.2, 7.1.4, 7.1.6, 9.1.4 or 9.1.6** speaker layouts.
- View object positions, movement and channel activity in the browser.
- Manage audio modes and sessions through FSA; edit playback settings in the Web configuration page.
- Enable optional PCM recording and telemetry archives when needed; both default off.

## Modules

| Module | Purpose |
|---|---|
| Capture and common input | Bring application object audio into a shared rendering pipeline. |
| ROAR / OLR | The primary speaker-rendering path. |
| LIRA | An alternative renderer with its own rendering behavior. |
| Output and DSP | ASIO device output, channel routing, calibration and optional processing. |
| FSA integration | Audio-mode selection, endpoint preparation and session control. |
| Web configuration and visualization | Playback settings, status, object views and channel meters. |

## Build

Follow the [Windows build guide](docs/build.md) to prepare the pinned dependencies, then run:

```powershell
./tools/Build-Alpha.ps1 -LlvmBin C:/toolchains/llvm-mingw/bin -Zig C:/toolchains/zig-0.13.0/zig.exe
```

For the FSA application, follow the [FSA setup guide](tools/fsa-integration/README.md). Building creates the artifacts; deployment and starting an audio session are separate steps.

## Quick configuration

1. Start the configured FSA application and triple-click its tray icon to open the Web page in your default browser.
2. Open **Configuration**, choose the renderer and speaker layout, then select your ASIO device and map the output channels to your wiring.
3. Save the settings, then start NSp through FSA. To apply later changes, stop and start the session again.

The Web page is available at `http://127.0.0.1:8767/`. It saves settings and shows status; FSA handles runtime control.

The default configuration file is `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`. You can also edit it directly; see the [example configuration](tools/zig-scheduler/playback.example.json) and [configuration reference](docs/core/playback-json-configuration-20261022.md).

## Direction

- Add native network-audio output alongside the current ASIO path.
- Develop custom speaker layouts and 6DoF support.
- Improve installation, configuration guidance and diagnostics.
- Extend long-running validation and recording-storage management.

These are planned directions, not currently available features. See [development status](docs/alpha-dev.md) for the current scope.

## Documentation

- [Build and prerequisites](docs/build.md)
- [Configuration reference](docs/core/playback-json-configuration-20261022.md)
- [Runtime details and validation limits](docs/runtime-details.md)
- [Architecture and module boundaries](docs/core/current-architecture.md)
- [Documentation index](docs/README.md)

## Latest update

<!-- Keep one brief entry here. Detailed progress belongs in docs/alpha-dev.md. -->

**2026-10-24:** Unified native endpoint enumeration, refreshed architecture documentation, and completed user-directory playback configuration and optional recording controls.

## License and credits

Original NSP code uses **Apache-2.0**. The included ASIO device code and SDK headers have **GPL-3.0-only** scope; a combined program containing them is not Apache-only. FSA and artwork retain their separate terms. See [license scope](LICENSING.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

Thanks to **hikari-taritsu (rmj)** for the Spatial ASIO / LIRA contributions. Original author and license notices are preserved; see [module licensing](modules/audio-backends/LICENSING.md) and [source provenance](docs/alpha-dev.md).
