param([string]$TargetDirectory='build/semantic-vocal')
$ErrorActionPreference='Stop';$root=(Resolve-Path "$PSScriptRoot/../..").Path;$out=Join-Path $root $TargetDirectory
$ort=Join-Path $root 'work/stemgen-vocal/ort/onnxruntime-win-x64-1.26.0'
$llvm=Join-Path $root 'work/lsac-toolchain/llvm-mingw-20261006-ucrt-x86_64/bin'
New-Item -ItemType Directory -Force $out|Out-Null
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -I "$ort/include" -I "$PSScriptRoot/vendor" -shared "$PSScriptRoot/vocal_separator.cpp" "$PSScriptRoot/vendor/StemgenRT/StreamingSampleRateAdapter.cpp" "$ort/lib/onnxruntime.lib" -lavrt -static-libstdc++ -static-libgcc -o "$out/NSP.VocalSeparator.dll"
if($LASTEXITCODE){throw 'Vocal separator build failed'}
Copy-Item "$ort/lib/onnxruntime.dll" (Join-Path $out 'NSP.VocalOrt126.dll') -Force;Copy-Item "$llvm/libunwind.dll" $out -Force
