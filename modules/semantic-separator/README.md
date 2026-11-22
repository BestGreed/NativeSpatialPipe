# Streaming vocal separation backends

## Product PCM upmix

Enable `wasapiUpmix.enabled` in a unified ROAR OLR 7.1.4 profile with PCM capture. The product build with `-WasapiUpmix` builds FreeSurround 480/240 and the CPU Joint DLL. The Web form looks for `build/semantic-joint480-cpu/NSP.Joint480.dll`, `build/freesurround-mature480/NSP.FreeSurround.dll`, and `work/stemgen-vocal/vocals.onnx`.

Build CPU Joint with `Build-JointLive.ps1 -Cpu`. Its OpenVINO SDK defaults to `work/joint-cpu-deps/openvino`, with C headers in `include` and the import library, CPU runtime, and TBB DLLs in `libs`. The validated SDK is OpenVINO 2026.4.0. Use `tools/lsac/Extract-JointVocalModel.py` with ONNX to extract the Joint-compatible vocal branch from the published StemgenRT v0.7.0 independent graph. The tool verifies the source hash and tensor contract. Keep the upstream weights notice and attribution with the local model.

Build the general DirectML path with `Build-JointLive.ps1 -DirectML`, or use `-OrtCpu` for ORT CPU. Supply local ORT headers and DLLs through `-OrtInclude` and `-OrtRuntime`. DirectML selects a hardware adapter and uses ORT CPU if device/session initialization fails. Use `-Gpu` or `-PortableNpu` for explicit OpenVINO GPU/NPU builds; no backend switch retains the shared-output NPU variant. Select the primary library with `wasapiUpmix.dllPath` and the standby with `cpuFallbackDll`. The runtime reports the actual backend. Development dependencies are prepared separately from installer download and setup.

An explicitly configured `cpuFallbackDll` remains available for builds with a separate primary backend. Outstanding tickets retain their original backend, and blocks that miss the deadline use original PCM. See [playback configuration](../zig-scheduler/playback.schema.json) and [runtime architecture and validation](../../docs/runtime-details.md).

## Earlier diagnostic backends

The combined low-latency candidate now uses FS480/H240, dual workers, shared
NPU output leases and fixed bindings. Reproduce with the deadline runner's
`-Merged480Sweep`. See `docs/research/nsp-merged480-latency-20261104.md` for
correctness, source-clock deadlines versus post-arrival processing budgets,
and the current prototype/live-integration boundary.

Experimental joint-pipeline work now includes a second CPU residual/FS worker
and optional NPU-context-owned tensor/output-lease support. These are standalone
probe build flags, not a silent change to the loaded separator DLL. See
`docs/research/nsp-dual-worker-shared-npu-20261104.md` for measured scope and
memory-lifetime validation. The original live NPU failure below remains unexplained;
the user later explicitly selected the diagnostic NPU for continued audition.

**Earlier standalone live qualification failed and was rolled back to CPU.** The first real playback after deployment initially recorded 14 inference exceptions and 26 unavailable result blocks, increasing to 35 exceptions and 67 unavailable blocks by rollback, despite successful offline tests. Capture and WASAPI reported no errors. The original worker did not record exception text, so the root cause is not yet established. `build/stemgen-npu/live-failed-validation.json` preserves the live evidence; `cpu-recovery-validation.json` records recovery. Independent duplicate read-only observation of another 20 seconds of the same browser source passed without errors, and another 20-second run following 30 seconds idle also passed; neither invalidates the failed live run or establishes the cause. Do not treat NPU initialization success or the tests below as production qualification.

The existing C ABI remains unchanged. `Build.ps1` builds the isolated ORT 1.26 CPU backend. `Build-Npu.ps1` builds an explicit Intel NPU backend using OpenVINO 2026.4.0's C API. Both retain 48↔44.1 kHz continuous SRC, 128-sample stateful hops, MMCSS worker scheduling, frame/epoch stamps, and the same configured output delay. Runtime file IO and model compilation happen before audio processing.

The NPU backend uses the original vocal ONNX graph without retraining or INT8 quantization. It asserts that the execution device is NPU and prebinds two inference requests to alternating host state buffers. It uses LATENCY performance mode. **Do not enable EXECUTION_MODE_HINT=ACCURACY for this tested combination**: synthetic and music tests produced nonfinite output. Default mode passed the recorded tests. Nonfinite native output is rejected by the worker.

Rust explicitly preloads the colocated TBB, OpenVINO core and C shim by absolute path because the shim delay-loads the core; it does not change the process-wide DLL search policy. Handles remain owned until native worker shutdown. An optional fourth line in `semantic-config.txt` specifies a CPU DLL to use if primary backend initialization fails. Runtime missed results still use the existing same-frame original/residual fallback; the fourth line is **initialization fallback**, not a state-preserving live NPU→CPU switch.

Configuration lines:

1. Absolute primary separator DLL path.
2. Absolute vocal ONNX path.
3. Fixed 48 kHz delay in frames (3840 in the current audition).
4. Optional absolute CPU fallback separator DLL path.

Evidence from this machine (Intel AI Boost, driver 32.0.100.4724):

- `build/stemgen-npu/compile-probe.json`: explicit NPU compilation.
- `build/stemgen-npu/native-music-validation.json`: matching native CPU/NPU rendering of the same local music. NPU vocal RMS difference was about 2.52%; error relative to original input about −40.11 dB. This is not bit-identical inference or a perceptual transparency claim.
- `build/stemgen-npu/stream-60s.json`: 6000 paced 48 kHz blocks, epoch reset, zero result fallback/input drops/errors with 80 ms reserve. Other model tests ran during part of this test, so its mean is not a dedicated speed benchmark.
- `build/nsp-npu-live/route-validation.json`: Rust + NPU + neutral recombination/FreeSurround setup, aligned output error below 2e-9, no audio device opened.
- `build/stemgen-npu/fallback-validation.json`: invalid primary DLL selects CPU.

OpenVINO runtime is Apache-2.0; retain its packaged notices under `work/npu-python/openvino*`. Stemgen software/SRC and model weights retain their original, separate licenses (weights CC-BY-NC-SA-4.0). No combined binary redistribution is performed by this local audition. Lower system power or fan noise has not yet been established by these timing tests.
