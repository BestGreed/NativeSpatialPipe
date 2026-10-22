param([string]$TargetDirectory='build/spatial-renderer', [string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin')
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=Join-Path $root $TargetDirectory
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
New-Item -ItemType Directory -Force $out | Out-Null
$sources=@('spatial_renderer_c.cpp','native_scene.cpp','metadata_renderer.cpp','point_geometry.cpp','speaker_identity.cpp') | ForEach-Object {Join-Path $root "modules/audio-backends/src/$_"}
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -DNOMINMAX -DNSP_SPATIAL_BUILD -shared "-I$root/modules/audio-backends/src" @sources -o "$out/NSP.SpatialRenderer.dll"
if($LASTEXITCODE){throw 'Independent spatial renderer build failed'}
foreach($runtime in @('libc++.dll','libunwind.dll')) {
 Copy-Item -LiteralPath "$llvm/$runtime" -Destination "$out/$runtime" -Force
}
Write-Output "Built independent renderer: $out/NSP.SpatialRenderer.dll"
