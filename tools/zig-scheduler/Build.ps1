param([string]$Zig='', [switch]$SkipTests)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
if(-not $Zig){$Zig=Join-Path $root 'work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe'}
if(-not(Test-Path -LiteralPath $Zig)){throw 'Zig 0.13.0 compiler required; pass -Zig or see README.md'}
if((& $Zig version) -ne '0.13.0'){throw 'This scheduler is pinned to Zig 0.13.0'}
$out=Join-Path $root 'build/zig-scheduler'
New-Item -ItemType Directory -Force $out | Out-Null
$env:ZIG_GLOBAL_CACHE_DIR=Join-Path $root 'build/zig-cache'
$env:ZIG_LOCAL_CACHE_DIR=Join-Path $out 'cache'
$flags=@('-target','x86_64-windows-gnu','-O','ReleaseSafe','-lc','-lws2_32','-ladvapi32','-I',"$root/tools/endpoint-probe",'-I',"$root/modules/audio-backends/src")
& $Zig build-exe "$PSScriptRoot/main.zig" @flags "-femit-bin=$out/nsp-scheduler.exe"
if($LASTEXITCODE){throw 'Zig scheduler build failed'}
if(-not $SkipTests){
 & $Zig test "$PSScriptRoot/routing.zig" -O ReleaseSafe
 if($LASTEXITCODE){throw 'Zig routing tests failed'}
 & $Zig test "$PSScriptRoot/control.zig" @flags "-femit-bin=$out/control-tests.exe"
 if($LASTEXITCODE){throw 'Zig orphan recovery tests failed'}
 & $Zig test "$PSScriptRoot/output_control_tests.zig" -target x86_64-windows-gnu -O ReleaseSafe -lc -I "$root/modules/audio-backends/src" "-femit-bin=$out/output-control-tests.exe"
 if($LASTEXITCODE){throw 'Output control worker tests failed'}
 & $Zig test "$PSScriptRoot/output_processing_tests.zig" -target x86_64-windows-gnu -O ReleaseSafe "-femit-bin=$out/output-processing-tests.exe"
 if($LASTEXITCODE){throw 'Common output processing tests failed'}
 & $Zig test "$PSScriptRoot/output_stream_tests.zig" -target x86_64-windows-gnu -O ReleaseSafe "-femit-bin=$out/output-stream-tests.exe"
 if($LASTEXITCODE){throw 'Reliable output stream tests failed'}
 & $Zig test "$PSScriptRoot/output_layout_tests.zig" -target x86_64-windows-gnu -O ReleaseSafe "-femit-bin=$out/output-layout-tests.exe"
 if($LASTEXITCODE){throw 'Output speaker mapping tests failed'}
}
Write-Output "Built native scheduler: $out/nsp-scheduler.exe"
