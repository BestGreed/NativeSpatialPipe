# WASAPI device output and tracing

The optional native WASAPI sink accepts interleaved 48 kHz stereo float PCM from a host using the output-pool ABI. It can open an explicitly selected render endpoint in shared or exclusive mode. The sink keeps a bounded FIFO between PCM publication and device writes.

## Build

From the public checkout root, set `NSP_LLVM_BIN` as described in the [Windows build guide](build.md).

```powershell
./modules/audio-backends/Build-Wasapi.ps1 -LlvmBin $env:NSP_LLVM_BIN
./modules/audio-backends/Build-Wasapi.ps1 -LlvmBin $env:NSP_LLVM_BIN -DiagnosticTrace -TargetDirectory build/audio-backends/wasapi-trace
```

These commands build the standalone backend. Using it for playback requires a host that supplies the output-pool callbacks and a render endpoint. The product scheduler can select this backend for the stereo LSAC path when it is built and enabled in playback JSON.

## Read a trace

While a host is using the diagnostic DLL, pass that process ID to the reader.

```powershell
$hostPid = 1234
./build/audio-backends/wasapi-trace/Read-WasapiTrace.exe $hostPid > build/audio-backends/wasapi-trace/trace.csv
```

Replace `1234` with the host process ID. The bounded local shared ring records QPC ticks, thread and processor IDs, and frame counts. The device-side records cover FIFO publication, buffer requests and completed writes. Subtract the written frame count from the corresponding request size to find a zero-filled interval. The reader snapshots the ring while the host is running. The ring stores timing metadata only.
