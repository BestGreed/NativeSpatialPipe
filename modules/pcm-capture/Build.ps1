param([string]$TargetDirectory='build/pcm-capture-v1',[string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin',[switch]$Test)
$ErrorActionPreference='Stop';$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=if([IO.Path]::IsPathRooted($TargetDirectory)){$TargetDirectory}else{Join-Path $root $TargetDirectory}
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
New-Item -ItemType Directory -Force $out|Out-Null
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -D_WIN32_WINNT=0x0A00 -shared "$PSScriptRoot/pcm_capture.cpp" -lole32 -luuid -lbcrypt -lavrt -static-libstdc++ -static-libgcc -o "$out/NSP.PcmCapture.dll"
if($LASTEXITCODE){throw 'PCM capture build failed'}
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -shared "$PSScriptRoot/pcm_ingress.cpp" -lavrt -static-libstdc++ -static-libgcc -o "$out/NSP.PcmIngress.dll"
if($LASTEXITCODE){throw 'PCM ingress build failed'}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination $out -Force
if($Test){
 & "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -D_WIN32_WINNT=0x0A00 "$PSScriptRoot/test_multi_capture.cpp" -lole32 -luuid -lbcrypt -lavrt -static-libstdc++ -static-libgcc -o "$out/test_multi_capture.exe"
 if($LASTEXITCODE){throw 'PCM multi capture test build failed'}
 & "$out/test_multi_capture.exe"
 if($LASTEXITCODE){throw 'PCM multi capture test failed'}
}
