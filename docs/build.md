# Building the source export

<!-- alpha-dev-status -->
**2026-10-24 alpha-dev/main:** Seven output layouts, Web configuration, session-resident ASIO and optional isolated recording are integrated. Enumeration now uses native MinHook; preparation retains Python/PowerShell helpers. Earlier desktop, metering and recording paths are explicitly historical. Playback configuration defaults to the user directory; PCM and snapshot archives default off. Storage quotas, 6DoF implementation and long-term device validation remain pending. See [current progress](alpha-dev.md) and [source README](../README.md). Older validation below keeps its original scope.
<!-- /alpha-dev-status -->

Run commands from the repository root on Windows x64. Required tools: Git, Python 3.12, PowerShell, Rust `1.97.1-x86_64-pc-windows-gnullvm`, and an LLVM/MinGW distribution providing clang.exe and libunwind.dll. The baseline used llvm-mingw `20261006-ucrt-x86_64`. Toolchains and dependency sources are installed separately.

The default managed control host additionally needs Zig 0.13.0 at build time. See [scheduler setup and validation](scheduler.md). Python remains required for preparation supervision and telemetry. Existing PowerShell/WinRT helpers use Windows-provided managed interop; the product does not require the retired .NET 10/WPF desktop runtime.

## Dependency layout

```powershell
git clone https://github.com/TsudaKageyu/minhook.git work/minhook
git -C work/minhook checkout c3fcafdc10146beb5919319d0683e44e3c30d537
git clone https://github.com/AOMediaCodec/roar.git work/roar
git -C work/roar checkout 60c96e566c1dd2838468006b63ada716549f82b2

$env:CARGO_HOME = Join-Path (Get-Location) 'work/rust/cargo'
$env:RUSTUP_HOME = Join-Path (Get-Location) 'work/rust/rustup'
$env:NSP_LLVM_BIN = 'C:/path/to/llvm-mingw/bin'
$env:PATH = "$env:NSP_LLVM_BIN;$env:CARGO_HOME/bin;$env:PATH"
```

Install rustup/Cargo and the specified toolchain into those locations using the official Rust tooling. Preserve the upstream license files. Do not point the scripts at an unrelated existing checkout with unreviewed local changes.

Fetch locked dependencies once while online; the ROAR build script then verifies/applies the included patch before compiling:

```powershell
cargo +1.97.1-x86_64-pc-windows-gnullvm fetch --locked --manifest-path tools/native-receiver/Cargo.toml
cargo +1.97.1-x86_64-pc-windows-gnullvm fetch --locked --manifest-path tools/roar-bridge/Cargo.toml
./tools/Build-Core.ps1 -LlvmBin $env:NSP_LLVM_BIN
```

Subsequent builds use `--locked --offline`. `Build-Core.ps1` builds the following runtime targets. It does not download SDKs or run audio capture.

For the current extracted LIRA/ASIO modules, native enumeration, Zig session/Web Host and their runtime dependencies, also run:

```powershell
./tools/Build-Alpha.ps1 -LlvmBin $env:NSP_LLVM_BIN -Zig C:/toolchains/zig-0.13.0/zig.exe
```

The enumeration artifacts are `build/enum-hook/nsp_enum_hook.dll` and `nsp_enum_control.exe`. They reuse the same pinned MinHook source as capture. A separate optional `tools/fsa-integration/Build-Integration.ps1 -FsaOnly` builds the native FSA UI after its prerequisites exist. These build steps do not deploy, inject Hook or restart FSA.

Explicit EDID preflight/HT Support and `inspect` still require the legacy `work/OemEndpointProbe.exe`. Build it separately with `powershell -File tools/endpoint-probe/Build-EndpointProbe.ps1`; this uses Windows' .NET Framework x64 compiler and the included existing C# sources. It is not part of the audio path, does not require .NET 10/WPF, and the build does not run the probe or write EDID.

| Runtime component | Output directory | Used by |
|---|---|---|
| Shared-memory transport | `build/native-send` | All native receiver paths |
| Capture worker | `build/native-capture`, `build/capture-scheduler-normal`, `build/capture-scheduler-mmcss` | Hook and standard scheduling options |
| Hook/controller | `build/native-hook` | Native capture host |
| Standard Rust receivers | `build/native-receiver/release`, `build/native-receiver-scheduler/release` | Standard single-stream entry |
| Dynamic-stream receiver | `build/native-receiver-routing/release` | Optional priority routing |
| Standard ROAR renderer | `build/roar-native/release` | Standard capture entry |
| Object/output tap renderer | `build/telemetry-objects/release` | Telemetry demo and object visualization |

Rust output directories also receive `libunwind.dll`. Keep that file beside the generated DLLs. For a controlled render/service/frontend run, see [telemetry and visualization](telemetry-visualization.md).

The LLVM argument may point outside the checkout; the C and receiver scripts use `NSP_LLVM_BIN`, which Build-Core sets from that argument. Rust/Cargo and upstream checkouts still use the dependency layout above. Install Node.js separately only if you want to run frontend JavaScript development checks; the frontend preview itself needs Python and a WebGL2 browser, without npm installation.

## Owned-process tests

```powershell
python tools/endpoint-probe/Test-NativeSend.py
python tools/endpoint-probe/Test-NativeHook.py
./tools/native-receiver/Build.ps1 -Test
python tools/endpoint-probe/Test-NativeReceiver.py
python tools/endpoint-probe/Test-NativeReceiver.py --mmcss
```

Run shared-memory integration tests outside AppContainer. In the reproduced restricted environment, mapping creation succeeded but reopening the mapping/event returned Win32 5 (access denied). An ordinary non-AppContainer user process passed; UAC elevation was not required for these owned-process tests. Do not broaden the transport ACL to bypass this restriction. Tests generate their own input and do not need game captures. See [startup diagnostics and validation](receiver-startup.md).

For `Run-Native.py --full-timing`, also build receiver and renderer into `build/native-receiver-full-timing` and `build/roar-full-timing` using their `-TargetDirectory` options. Source exports retain the timing-capable ABI. Performance claims must identify the actual built DLLs and measurement boundary.
