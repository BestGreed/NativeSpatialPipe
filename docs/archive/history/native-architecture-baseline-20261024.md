> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

> Historical snapshot archived 2026-10-24. Superseded by [current architecture](../core/current-architecture.md). Paths, mandatory recording, performance numbers and build status below describe their original dates only.

# Current native pipeline

2026-10-24 Current path: C capture/shared memory → Rust receive/common input → ROAR (currently accessed OLR) or LIRA → Rust interleaved output pool → in-pool DSP/bypass → pool block lease → ASIO. The two backends share seven output layouts, and device internal queues, SRC and clock matching are preserved. Dedicated control thread for blocking device operations, native FSA for endpoint/session management, independent Zig Web Host read-only status and verify/save configuration. Python is still responsible for telemetry/testing, and the native chain does not rely on .NET.

No large PCM/snapshot files are written during normal operation. When explicitly enabled, dedicated bounded side recording and independent JSONL threads are entered respectively; file IO is not performed during playback delivery. Telemetry selection stream reads renderer memory count. The capacity/retention policy and installation directory migration for D15 are still to be done. See side notes (development reference is not provided with the open source code), layout (development reference is not provided with the open source code), playback configuration (development reference is not provided with the open source code) and merge status (development reference is not provided with the open source code).

Evidence of the early architecture remains below; the output queue and background file paths in the old diagram do not represent the current playback PCM path.

Historical note (2026-10-19): A one-minute Wwise comparison of Python/Zig was completed at that time. Python telemetry was still in the startup chain, and the complete running record was retained by default at that time. Logging defaults are now overridden by the 10/24 scenario; historical measurements do not automatically represent the full performance of the current build. The 10/16–10/17 baselines below retain evidence and are not intended to be indicative of current full capabilities.

> **2026-10-17 Target re-evaluation:** The current product scope is subject to the target baseline (development reference is not provided with the open source code): FSA user-end click rule matching software, asynchronous web services provide management panel and modern audio visualization; IAMF/OAR's OBR+OLR, high-performance native object pipeline and rendering, FPS low-latency practical acceptance; Lighthouse optical speaker layout input and native IKO All supported experimentally; product output is ASIO or native LAN streams only. If there is any conflict between the old routes and schedules below, the new baseline shall prevail, and the historical measurement conclusions remain unchanged.


2026-10-16.

The core performance boundary ends when ROAR rendering is completed; the target P99 is <2ms, and the EXP-018 three-thread MMCSS random load for three rounds of 1.436–1.493ms has reached the standard. DSP is transferred to maintenance, individual is retained, and grouped generalization is suspended. The background file writing to disk is a verification facility; the sound card buffering, driver and actual playback are personalized adaptations and are not included in the core module tuning and acceptance. Three-threaded MMCSS+individual has been connected to the default configuration of Run-Native.py, and follows the accepted scheduling strategy of EXP-018; this time only the control entry is repaired, the capture, receiving or rendering implementation is not changed, and the performance acceptance is not repeated.

```mermaid
flowchart LR
 A[Windows object submission] --> B[MinHook C snapshot copy]
 B --> C[C capture worker/MMCSS]
 C --> D[bounded shared memory and events]
 D --> E[Rust receive/MMCSS]
 E --> F [native rendering queue]
 F --> G[Metadata and ROAR DSP/MMCSS]
 G --> H[bounded output queue]
 H --> I[Native background file writing to disk]
```

Zig or retained Python control layer creates processes/maps, selects modes, and aggregates results; the core native receive path receives PCM packet-by-packet without going through Python. Python still runs bypass telemetry. Wwise+WAAPI is an experimental input control, not a product operation dependency. System audiodg only loads the native Hook, capture and send modules; the MinHook resident module retains the trampoline after the Hook is stopped.

## Run and build

Current capture entry: `python tools/endpoint-probe/Run-Native.py <output-directory> <audiodg-pid> --duration 90`. System version verification and H16/Sonic configuration must be completed before calling; the portal does not switch endpoints by itself. The three self-owned threads MMCSS for capturing, receiving, and rendering are enabled by default (explicitly set NSP_RENDER_MMCSS=1); `--normal-scheduling` explicitly turns off the MMCSS of the three threads for ordinary scheduling control, and `--full-timing` uses an independent diagnostic DLL. This entry is limited to single stream and 480 frames and does not enable raw NSP recording.

The H16/Wwise one-minute lab uses `Run-CaptureScheduler.ps1`; it includes endpoint setup/restore and full payload verification. Full-node normal/dual MMCSS comparison is compiled by `Run-FullLatencyComparison.ps1`. Check the Wwise instance and WAAPI listener ownership at startup.

Build scripts: `Build-NativeHook.ps1`, `Build-NativeCapture.ps1`, `Build-NativeSend.ps1`, `Build-CaptureScheduler.ps1` are located at `tools/endpoint-probe`; the two Rust build entries are `tools/native-receiver/Build.ps1` and `tools/roar-bridge/Build.ps1`. Use fixed Cargo.lock and offline caching. The standard DLL and the `*-full-timing` diagnostic DLL use different output directories, and the standard DLL is currently verified to be retained.

## Measurement boundaries

| node | current observable item |
|---|---|
| Hook / Snapshot | callbackMs, captureToCopy |
| source worker / handover | ownedCopyToSend, sendToAcquire |
| Rust receiver | descriptorParse, nativeSubmit, captureToSubmit |
| Native rendering queue | queueWaitMs |
| Rendering internals | decodeMs, metadataMs, dspMs, workMs |
| Diagnostic output | writerQueueMs, bufferedWriteMs |
| Direct cumulative timing | captureToRenderMs, captureToBufferedWriteMs |
| End disk writing | finalFlushSyncMs (flush+sync at the end) |

There is a nested relationship between node statistics and quantiles cannot be added directly. Directly accumulate timing and pass the same capture QPC; slow packets are attached with sequence and capture/rendering/writing timestamps. When more than 256 items are exceeded, the total number is retained and overflow is reported. The time taken to write to the disk refers to the return of the buffered write function, not the persistent disk write on a packet-by-package basis. Real speaker playback, device buffering and underrun feedback are not yet plugged in, so this project is currently unable to report full acoustic end-to-end latency.

## Receiver startup diagnostics (2026-10-17)

10/17 Locating and fixing (development reference not provided with open source code) Shared mapping/event access denial for AppContainer in controlled testing has been located. Shared memory integration tests run in a non-AppContainer environment; there is no requirement to change the system audio configuration or relax the share zone DACL for this purpose. Added `nsp_receiver_start_error()` In the startup control thread return phase/Win32 code, Python reports the actual DLL path; the standard DLL is not covered, and the new diagnostic verification build is located at `build/receiver-init-fixed`. Normal scheduling/MMCSS bit configuration and per-packet paths remain unchanged.
