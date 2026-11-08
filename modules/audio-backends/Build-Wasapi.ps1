param([string]$TargetDirectory='build/audio-backends/wasapi-exclusive-v1',[string]$LlvmBin='work/lsac-toolchain/llvm-mingw-20261006-ucrt-x86_64/bin',[switch]$DiagnosticTrace)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=if([IO.Path]::IsPathRooted($TargetDirectory)){$TargetDirectory}else{Join-Path $root $TargetDirectory}
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
New-Item -ItemType Directory -Force $out|Out-Null
if($DiagnosticTrace -and $TargetDirectory -eq 'build/audio-backends/wasapi-exclusive-v1'){throw 'Use a separate diagnostic output directory'}
$traceFlags=@()
if($DiagnosticTrace){$traceFlags+= '-DNSP_WASAPI_TRACE'}
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -DNOMINMAX @traceFlags -shared "$PSScriptRoot/src/wasapi_pool.cpp" -lole32 -luuid -lavrt -static-libstdc++ -static-libgcc -o "$out/NSP.WasapiOutput.dll"
if($LASTEXITCODE){throw 'WASAPI output build failed'}
if($DiagnosticTrace){
    & "$llvm/clang++.exe" -std=c++20 -O2 -DNOMINMAX -static "$root/tools/endpoint-probe/Read-WasapiTrace.cpp" -o "$out/Read-WasapiTrace.exe"
    if($LASTEXITCODE){throw 'WASAPI trace reader build failed'}
}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination $out -Force
Write-Output "Built WASAPI output: $out"
