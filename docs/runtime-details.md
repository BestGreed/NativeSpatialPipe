# Runtime details and validation limits

This document holds implementation details that are intentionally kept out of the project homepage. For features, building and basic configuration, start with the [README](../README.md).

## Audio path and renderers

```text
MinHook / C capture -> shared memory -> Rust receiver / common input
 -> ROAR (OLR mode) or C++ LIRA -> interleaved output pool
 -> in-place DSP / bypass -> leased pool blocks -> ASIO owner
Optional pre-DSP copy -> bounded recording pool -> dedicated file writer
Independent telemetry -> Python/C metering and history -> browser visualization
Native FSA -> Zig session coordination and independent Web configuration host
```

OLR is the currently integrated speaker mode of ROAR, not a separate backend beside LIRA. Both renderers support seven output layouts with shared channel definitions and speaker-identity calibration/routing migration. LIRA retains its own room projection, clipping, smoothing and inherent 512-frame delay. ROAR's 6DoF measurement slot is reserved and cannot be selected yet.

ASIO retains internal device buffering, sample-rate conversion and clock matching. It remains active for a session and sends silence without input. DSP runs in the output pool; the former Zig PCM staging queue and scratch copy before ASIO submission are removed. A bounded descriptor queue remains, and ASIO still copies internally; this is not end-to-end zero-copy. The playback handoff does not write recording files.

See [current architecture](core/current-architecture.md), [output layouts](core/output-layout-presets-20261023.md) and [output-pool processing](core/output-pool-processing-20261022.md).

## Runtime dependencies and preparation

Extra AudioSrv enumeration now uses native C/MinHook and the same controller source and pinned engine as capture. The preparation supervisor remains Python, without a Frida/JavaScript injection agent. Python also runs isolated telemetry. PowerShell/WinRT helpers retain Windows system interop.

Explicit EDID preflight/HT Support and `inspect` still use the legacy C# endpoint probe with Windows .NET Framework. Its source and separate `tools/endpoint-probe/Build-EndpointProbe.ps1` build are included. Preparation is not yet fully native; this helper is outside the audio path and does not require the retired .NET 10/WPF desktop runtime.

ASIO/LIRA modules require matching `libc++.dll` and `libunwind.dll` alongside them. ROAR's unwind dependency must also match its build. Browser JavaScript does not require a Node service; Node is used for development tests. No WebView2 desktop shell is required.

See [build prerequisites](build.md), [native enumeration](core/native-enumeration-hook-20261024.md) and [stack audit](core/stack-architecture-audit-20261024.md).

## Configuration, control and recording

FSA starts a separate Zig Web API Host at `http://127.0.0.1:8767/`. Three clicks in quick succession on its tray icon open the page in the default browser; this is the only FSA UI shortcut, with no separate tray-menu item or additional button. Configuration is selected inside the opened Web page. The page reads, validates and saves playback/backend JSON. Saving does not apply settings to a running session. Start/stop, mode changes, Hook and EDID actions remain with FSA. Python telemetry is read-only and separate; standalone visual preview uses port 8765.

Playback configuration defaults to `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`. The old installation-adjacent default is copied without deleting the original or overwriting an existing user file. Explicit custom paths remain supported; default saves do not write the installation INI.

PCM recording and snapshot archival are independent and disabled by default:

```json
{
  "recording": {"pcm": false, "telemetrySnapshots": false}
}
```

PCM recording copies pre-DSP audio into a dedicated 32-block pool. Slow or failed recording drops recording blocks and reports them without retaining playback leases. Live telemetry and in-memory history continue without archival. Telemetry routing reads renderer memory progress rather than recording file growth.

See [configuration fields](core/playback-json-configuration-20261022.md), [user-directory migration](core/playback-user-location-20261024.md) and [recording isolation](core/recording-isolation-20261024.md).

## Validation scope and remaining work

Development checks include recording/failure isolation, archive combinations with an owned 140-slot capture fixture, seven-layout renderer tests, Web configuration checks, actual FSA/ASIO idle start/stop, and native enumeration enable/restore checks. Earlier DVS 48 kHz/128-frame 12-channel network-return and classic Wwise HT tests retain their original scope. Private captures and evidence payloads are not distributed here.

These checks do not establish all-layout physical playback, 14/16-channel network routing, acoustic latency or long-term stability. An earlier output-session `OutputTimeout` still requires targeted sustained-input/stop validation; later idle success does not erase that evidence. Native network output, 6DoF measurement handling, storage quotas/retention and installable packaging remain future work. Inferno is not integrated. ASIO-internal scheduling and latency optimization remain out of scope.

This is a Windows-build-specific implementation, not a registered Windows Spatial Sound Provider or a Dolby/DTS bitstream decoder. Historical designs and measurements are marked separately from current runtime contracts. Public `main` and `alpha-dev` contain a reviewed source export with independent history; see [progress and provenance](alpha-dev.md), [source manifest](core-source-manifest.json) and [documentation index](README.md).
