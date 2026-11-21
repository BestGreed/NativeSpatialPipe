# Build and run the audiodg build-profile table contract tests (owned process only).
param([string]$LlvmBin)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
if(-not $LlvmBin){$LlvmBin=$env:NSP_LLVM_BIN}
if(-not $LlvmBin){$LlvmBin=Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'}
$clang=Join-Path $LlvmBin 'clang.exe'
if(-not(Test-Path -LiteralPath $clang)){throw 'clang.exe not found; pass -LlvmBin or set NSP_LLVM_BIN'}
$out=Join-Path $root 'build/native-hook'
New-Item -ItemType Directory -Force $out|Out-Null
& $clang -O2 -Wall -Wextra -Werror "$PSScriptRoot/Test-Audiodg-Builds.c" -o "$out/nsp_audiodg_builds_test.exe"
if($LASTEXITCODE){throw 'build-profile table test failed to compile'}
& "$out/nsp_audiodg_builds_test.exe"
if($LASTEXITCODE){throw 'build-profile table tests failed'}
