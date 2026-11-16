param(
    [string]$TargetDirectory = 'build/backend-relocation-20261022',
    [string]$LlvmBin = 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot/../..").Path
$out = if ([IO.Path]::IsPathRooted($TargetDirectory)) { $TargetDirectory } else { Join-Path $root $TargetDirectory }
$llvm = if ([IO.Path]::IsPathRooted($LlvmBin)) { $LlvmBin } else { Join-Path $root $LlvmBin }

& "$root/tools/spatial-renderer/Build.ps1" -TargetDirectory (Join-Path $out 'lira') -LlvmBin $llvm
if ($LASTEXITCODE) { throw 'LIRA build failed' }

$asioOut = Join-Path $out 'asio'
New-Item -ItemType Directory -Force -Path $asioOut | Out-Null
$versionHeader = Join-Path $asioOut 'nsp-build-version.h'
[IO.File]::WriteAllText($versionHeader, '#define SPATIALASIO_VERSION "nsp-dev16"', [Text.Encoding]::ASCII)
# Same production sources as NSP.AsioDevice plus NSP.AsioOutput in CMakeLists.txt.
$sources = @('asio_output_c.cpp', 'asio_backend.cpp', 'asio_format.cpp', 'diagnostics.cpp', 'sinc_resampler.cpp') |
    ForEach-Object { Join-Path $PSScriptRoot "src/$_" }
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -DNOMINMAX -DNSP_ASIO_BUILD -include $versionHeader -shared "-I$PSScriptRoot/src" "-I$PSScriptRoot/third_party/asio/common" @sources -lole32 -ladvapi32 -luser32 -luuid -lavrt -lwinmm -o "$asioOut/NSP.AsioOutput.dll"
if ($LASTEXITCODE) { throw 'ASIO build failed' }
foreach ($runtime in @('libc++.dll', 'libunwind.dll')) {
    Copy-Item -LiteralPath (Join-Path $llvm $runtime) -Destination $asioOut -Force
}
Write-Output "Built LIRA and ASIO backends: $out"
