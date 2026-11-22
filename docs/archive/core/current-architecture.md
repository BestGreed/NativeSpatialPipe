> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# Current architecture and technology stack boundaries

2026-10-24. This page describes the current product operation entry; historical design, diagnostic tools, and future planning cannot replace the operation path on this page. Please see [Technology Stack and Documentation Review] (stack-architecture-audit-20261024.md) for verification details.

## audio data path

```text
windows object submission/audiodg
 → Native MinHook/C snapshot, capture worker
 → C bounded shared memory and events
 → Rust reception, public input conversion, warm-up renderer instances
 → ROAR (current OLR mode) or C++ LIRA
 → Rust interleaved output pool
 → Zig DSP callbacks are handled in-place/bypassed within the pool block
 → bounded pool block lease descriptor
 → ASIO owner synchronously submits the pool address
 → C++ ASIO internal queue, SRC, device clock, ring, physical channel conversion
```

Removed are the Zig PCM staging queue and scratch replication between the Rust pool and ASIO commits; there are still bounded descriptor queues, as well as ASIO internal replication. It cannot be called full-link zero-copy or hardware callbacks to pull directly from the Rust pool. The public DSP algorithms are still in Zig, executed through in-process C ABI callbacks, not a new Rust DSP.

PCM recording is located in front of the public DSP. When explicitly turned on, it is copied to an independent bounded recording pool and written to the disk by a dedicated thread; the queue is full or the disk fails without waiting or hijacking the playback pool. Object/output telemetry uses independent taps and bounded bypasses, with HTTP/WebSockets metered, aggregated, and served by independent Python services. Bypass still has copy and release costs, which does not mean there is no real-time overhead at all. Snapshot archiving is performed by a separate thread. PCM and snapshot archiving are turned off by default, the stream is selected to read the memory progress, and the recording file growth is not watched.

## Modules, languages and runtime dependencies

| Modules/entrances | Technology and process boundaries | Responsibilities that should not be mixed |
|---|---|---|
| `tools/fsa-integration/native` | Native C/Win32 FSA; endpoint snapshots, rules, patterns, session startup and backtesting | Does not host DSP, ASIO callbacks or second set of web control state machines |
| `tools/pipeline-init` | Python preparation control and enumeration supervision; PowerShell/WinRT query, selection, notification and explicit EDID assistance | Does not receive packet-by-packet PCM, does not continuously replace FSA management endpoints, does not automatically write EDID |
| `tools/zig-scheduler` | Zig scheduling, backend session, dedicated device control thread, public DSP | Callbacks do not execute blocking device open/close, network control or file recording |
| `tools/native-receiver`, `tools/roar-bridge` | Rust receive, adapt, output pool, and ROAR bridge; in-process C ABI | Public inputs do not introduce LIRA room projection, clipping, or slot GC |
| ROAR / LIRA | ROAR is the Rust backend, currently connected to OLR; LIRA is an independent C++ backend | OLR is the mode of ROAR, and is not listed as a backend alongside LIRA; ROAR OBR is not currently connected to the mode |
| `modules/audio-backends` | Extracted native C++ LIRA and ASIO; independent module C ABI stays in this directory | Does not depend on full Spatial ASIO project, WPF or .NET 10 |
| `tools/telemetry/standalone.py` | Standalone Python service, can load C metering DLL (also has Python reference implementation); history, HTTP/WebSocket (default 8766) | Not responsible for audio control, does not calculate the full set of metering in the renderer |
| `nsp-scheduler.exe web-host` | Independent Zig process, started with FSA; status, configuration verification/save, front-end static resources (default 8767) | Save is not automatically applied, no start/stop, mode, Hook, EDID or emergency stop API |
| `tools/visualization-frontend` | Browser native JS/HTML/CSS/WebGL; default browser opens | Node is used for development and testing; product does not require Node service, WebView2 shell or .NET desktop |

Python is not only used for telemetry but also for preparing modules. PowerShell's WinRT/Add-Type helper relies on Windows' native managed interop environment; explicit EDID `plan` / HT Support preflight and `inspect` also calls the old `work/OemEndpointProbe.exe` (C# / System .NET Framework), not just historical test code. So "native audio DLL without .NET" does not equal "all helper scripts without going through the CLR at all"; .NET 10/WPF installation is not required. This probe can be built separately from `tools/endpoint-probe/Build-EndpointProbe.ps1` without entering the audio hot path or adding the optional ClientProbe/WwiseProbe back to the default dependency. LIRA/ASIO built by LLVM-MinGW must have matching `libc++.dll` and `libunwind.dll`; ROAR's unwind dependency should also be checked with its construction materials.

## Configuration, lifecycle, and yet-to-be-implemented items

The playback configuration defaults to `%LOCALAPPDATA%/NativeSpatialPipe/playback.json` and is compatible with explicit custom paths. The default files in the old installation directory are only migrated to user copies and do not overwrite existing targets. The web saves the configuration for reading in the next session and does not perform hot application. Storage contracts for FSA configuration and backend status are not uniformly migrated as a result.

ASIO is resident with the NSP session and streams silently when there is no input; the session is no longer automatically terminated due to waiting for the first packet or repeated underload, and the old timeout field is only compatible with parsing. Capture/transmission critical failures and device failures should still result in a safe exit. The audio's own worker thread and the MMCSS registration of the device callback each have implementation boundaries, and the entire process cannot be claimed to be hard real-time based on this.

Seven output layouts are switched on: 5.1.2, 5.1.4, 7.1.2, 7.1.4, 7.1.6, 9.1.4, 9.1.6. Custom 6DoF slots are reserved only; the native AoIP/Inferno port is still an evaluation route and has not become the default output. WDM completion is not the current product input chain, and the headset capture mode of the OBR name does not mean that the ROAR OBR rendering is plugged in.

Detailed contract: [Output Pool](output-pool-processing-20261022.md), [Aside Recording](recording-isolation-20261024.md), [Layout](output-layout-presets-20261023.md), [Web](../decisions/web-configuration-host-20261023.md). [10/16–10/19 Original architecture page](../history/native-architecture-baseline-20261024.md) Only historical evidence is retained and is not used as a current dependency or performance commitment.
