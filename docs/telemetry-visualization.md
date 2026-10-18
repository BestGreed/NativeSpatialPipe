# Telemetry and visualization

This source export includes the native bounded output/object taps, isolated Python telemetry service, JSON contracts, browser frontend and synthetic tests. It excludes private captures, Wwise projects, game-specific launchers, memory investigation scripts, experiment archives and local toolchains.

## Run a controlled demonstration

Build the core first (see build.md), then run from the repository root:

```powershell
python tools/telemetry/demo.py build/demo-source --seconds 60 --objects 16
python tools/visualization-frontend/serve.py
```

Use a new output directory. Open the printed frontend address and enter the demo's WebSocket URL in its data-source panel. This uses actual ROAR rendering with synthetic input; it does not start a system Hook or play through a device. JSON fixtures are explicitly synthetic.

DLL taps use bounded pools and perform no metering or networking. Host copy-out feeds a separate service that computes meters, history and object events, then serves read-only HTTP/WebSocket snapshots. Debug endpoints are disabled by default. Slow/missing consumers drop telemetry rather than block audio.

The frontend supports object navigation and output-channel spectrum views, source-relative motion candidates, bounded visual forecasting, camera presets, cloud/weak-field layers and selection details. Source units are not physically calibrated; motion candidates are not measured head poses or acoustic propagation.

## Receiver routing

The standard entry remains single-stream. `Sync-Hook.py` additionally supports `--rust-receiver --receiver-mmcss --prefer-dynamic-streams` using `build/native-receiver-routing/release/nsp_native_receiver.dll`. The selected policy retains streams that have shown dynamic objects and can discard static companion streams, including nonzero audio. It is an explicit lossy policy, not a general static-audio classifier. Renderers are prewarmed; the host uses four slots and a configurable 500 ms activity timeout. Unsupported extended static layouts are counted separately. Source sequence checks remain active. Automatic slot recycling across arbitrary application restarts is not implemented. The frontend connects to the primary slot, without a multi-stream mixdown.

The Python multi-stream distributor is included as a diagnostic alternative; it copies/distributes packets and does not have the Rust receiver's zero-copy behavior.

## Tests

```powershell
python -m unittest discover -s tools/telemetry -p 'test_*.py'
python -m unittest discover -s tools/visualization-contract -p 'test_*.py'
python tools/endpoint-probe/Test-PriorityReceiver.py
```

The telemetry suite needs locally built DLLs and localhost HTTP/UDP. Native tests must run outside restricted AppContainer environments. No private recording is required by these suites. Frontend pure-data/algorithm tests use Node.js; run individual `test-*.js` modules. Private recorded-replay tests and preparation tools are excluded. The frontend can display an externally supplied compatible replay, but this export ships no recorded replay.

Historical core performance measurements do not qualify the new routing/telemetry build. Long-run recovery, routing performance on real applications, final browser CPU/GPU use and actual device playback remain separate acceptance tasks.
