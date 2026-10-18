# Native Spatial Pipe

Windows Spatial Audio object-stream capture and external-rendering research, implemented in C and Rust with Python and optional Zig control hosts.

```text
Windows object submission → native capture worker → bounded shared memory
                         → Rust receiver → metadata/ROAR renderer → file writer
```

Default entry: **capture, receiver and renderer workers use MMCSS; rendering uses individual mode**. Python initializes, monitors and stops the native pipeline. It does not process audio packets in the default path.

This is a version-pinned research prototype. It captures uncompressed object PCM and runtime object state before spatial rendering. It does not implement Dolby/DTS bitstream decoding. The current hook uses an internal audiodg boundary and refuses protected or unrecognized system images. It is not a registered Spatial Sound Provider or a supported Windows extension contract.

## Build and run

See [build instructions](docs/build.md), [architecture and limitations](docs/architecture.md), and [dependency notices](THIRD_PARTY_NOTICES.md).

```powershell
python tools/endpoint-probe/Run-Native.py <new-output-directory> <verified-audiodg-pid> --duration 90
```

The endpoint and PID must be prepared and verified separately. The host may need elevation to access the verified, unprotected audiodg process. It does not switch endpoints or disable process protection. Unsupported versions fail closed; do not remove the version/protection checks to make an unsupported system run.

`--normal-scheduling` disables all three MMCSS registrations for comparisons. `--full-timing` selects separately built diagnostic DLLs; it is not needed to enable renderer MMCSS. The default requires one stream, 480 frames at 48 kHz, and does not archive raw NSP packets. File output is a validation facility; hardware playback and network output are not implemented here.

## Telemetry and visualization

The export now includes isolated output/object telemetry, versioned contracts, a browser frontend, synthetic tests and an optional dynamic-stream preference policy. See [module setup and limitations](docs/telemetry-visualization.md). Private recordings and game-specific orchestration are excluded.

## Status

The optional [Zig scheduler and Sonic NSp integration](docs/scheduler.md) now provide native orchestration, persistent backend state and managed lifecycle control. Telemetry still uses Python. This update also adds native metering, file-growth telemetry routing and frontend reconnect/diagnostic improvements. C/Rust remain the audio core.

The original controlled-machine experiment measured capture-to-render-completion P99 of 1.436–1.493 ms over three random-load runs, with 23,461 packets and no packet loss. The worst observed latency was 3.299 ms. These are historical measurements, not measurements from this source export or a hard deadline guarantee. Private raw game captures and the research archive are excluded.

The C/Rust sources in this branch match the research baseline hashes in [the source manifest](docs/core-source-manifest.json). Export checks do not constitute a new end-to-end audio acceptance run. Known limitations include single-build support, incomplete abnormal-recovery coverage, and restricted-environment startup requirements. The receiver initialization failure reproduced in an AppContainer was traced to shared-memory/event access denial; owned-process checks pass outside AppContainer. See [receiver startup validation](docs/receiver-startup.md).

`Run-Native.py` remains the standard capture entry; the synthetic telemetry demo and frontend preview are additional supported source-export tools. The shared host retains dormant historical branches; the Frida, legacy consumer and raw-recording backends referenced by those branches are not included or supported.

## License

Original project code and documentation: **Apache License 2.0**. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party code and the ROAR patch retain their upstream terms, including ROAR's separate patent terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
