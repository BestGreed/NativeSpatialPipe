param([Parameter(Mandatory=$true)][string]$LlvmBin, [Parameter(Mandatory=$true)][string]$Zig)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/..").Path
$llvm=(Resolve-Path -LiteralPath $LlvmBin).Path
& "$PSScriptRoot/Build-Core.ps1" -LlvmBin $llvm
& "$PSScriptRoot/pipeline-init/Build-EnumHook.ps1" -LlvmBin $llvm
if($LASTEXITCODE){throw 'Enumeration hook build failed'}
& "$PSScriptRoot/spatial-renderer/Build.ps1" -LlvmBin $llvm
$out=Join-Path $root 'build/audio-backends'
New-Item -ItemType Directory -Force $out | Out-Null
$sources=@('asio_output_c.cpp','asio_backend.cpp','asio_format.cpp','diagnostics.cpp','sinc_resampler.cpp') | ForEach-Object {Join-Path $root "modules/audio-backends/src/$_"}
$versionHeader=Join-Path $out 'nsp-build-version.h'
[IO.File]::WriteAllText($versionHeader,'#define SPATIALASIO_VERSION "alpha-dev"',[Text.Encoding]::ASCII)
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -DNOMINMAX -DNSP_ASIO_BUILD -include $versionHeader -shared "-I$root/modules/audio-backends/src" "-I$root/modules/audio-backends/third_party/asio/common" @sources -lole32 -ladvapi32 -luser32 -luuid -lavrt -lwinmm -o "$out/NSP.AsioOutput.dll"
if($LASTEXITCODE){throw 'ASIO output build failed'}
foreach($runtime in @('libc++.dll','libunwind.dll')){Copy-Item -LiteralPath "$llvm/$runtime" -Destination "$out/$runtime" -Force}
& "$PSScriptRoot/zig-scheduler/Build.ps1" -Zig $Zig
Write-Output 'Built source artifacts only. No driver opened, no Hook injection or deployment.'
