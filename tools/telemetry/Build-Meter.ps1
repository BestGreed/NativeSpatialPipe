$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$clang=Join-Path $env:NSP_LLVM_BIN 'clang.exe'
$out=Join-Path $root 'build/telemetry-meter'
New-Item -ItemType Directory -Force $out|Out-Null
& $clang -O3 -ffp-contract=off -shared -Wall -Wextra -Werror "$PSScriptRoot/meter_native.c" -o "$out/nsp_telemetry_meter.dll"
if($LASTEXITCODE){throw 'Native telemetry meter build failed'}
