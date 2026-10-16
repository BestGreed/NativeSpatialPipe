$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$clang=if($env:NSP_LLVM_BIN){Join-Path $env:NSP_LLVM_BIN 'clang.exe'}else{Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin/clang.exe'}
$out=Join-Path $root 'build/native-send'
New-Item -ItemType Directory -Force -Path $out|Out-Null
& $clang -O2 -shared -Wall -Wextra "$PSScriptRoot/Native-Send.c" -o "$out/nsp_native_send.dll" -ladvapi32
if($LASTEXITCODE){throw 'Native send build failed'}
