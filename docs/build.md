# Building the source export

Run commands from the repository root on Windows x64. Required tools: Git, Python 3.12, PowerShell, Rust `1.97.1-x86_64-pc-windows-gnullvm`, and an LLVM/MinGW distribution providing clang.exe and libunwind.dll. The baseline used llvm-mingw `20261006-ucrt-x86_64`. Toolchains and dependency sources are installed separately.

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

Subsequent builds use `--locked --offline`. `Build-Core.ps1` builds both capture scheduling variants, both receiver output directories, native Hook/control/fixture binaries, the sender and the standard renderer. It does not download SDKs or run audio capture.

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
