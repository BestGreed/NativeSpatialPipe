param([string]$TargetDirectory='build/browser-identity-v1')
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=if([IO.Path]::IsPathRooted($TargetDirectory)){$TargetDirectory}else{Join-Path $root $TargetDirectory}
$llvm=Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
New-Item -ItemType Directory -Force $out|Out-Null
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -D_WIN32_WINNT=0x0A00 -municode "$PSScriptRoot/backend.cpp" -lole32 -luuid -lbcrypt -lavrt -lpsapi -ladvapi32 -lws2_32 -static-libstdc++ -static-libgcc -o "$out/NSP.BrowserIdentity.exe"
if($LASTEXITCODE){throw 'Browser identity backend build failed'}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination $out -Force
