# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
  Builds the standalone AES67 test sender (branch 6).

.DESCRIPTION
  Compiles aes67_tx.c + aes67_sdp.c + aes67_send.c with MinGW gcc into
  build/aes67-output/aes67-send.exe. Regenerates aes67_layouts.h from
  contracts/output-layouts.json first when Python is available, so the layout
  table can never drift from the NSP single-source contract.

  This does NOT build NativeSpatialPipe itself (that needs the pinned
  LLVM-MinGW/Rust/Zig toolchain); it is an independent testable transmitter.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools/aes67-output/Build.ps1
#>
[CmdletBinding()]
param(
    [string]$Gcc = "gcc",
    [string]$OutDir = "",
    [switch]$NoGenerate
)
$ErrorActionPreference = "Stop"

$here = $PSScriptRoot
$repo = Split-Path -Parent (Split-Path -Parent $here)
if (-not $OutDir) { $OutDir = Join-Path $repo "build/aes67-output" }
$OutDir = if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $repo $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not $NoGenerate) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source (Join-Path $here "gen-layouts.py")
    } else {
        Write-Warning "python not found; using committed aes67_layouts.h (may be stale)"
    }
}

$sources = @(
    (Join-Path $here "aes67_tx.c"),
    (Join-Path $here "aes67_sdp.c"),
    (Join-Path $here "aes67_send.c")
)
$exe = Join-Path $OutDir "aes67-send.exe"
$common = @("-std=c11", "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter")
& $Gcc @common "-I$here" -o $exe @sources -lws2_32
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $exe)) {
    throw "gcc build failed with exit code $LASTEXITCODE"
}
$item = Get-Item $exe
Write-Host ("built {0} ({1:N0} bytes)" -f $item.FullName, $item.Length)

