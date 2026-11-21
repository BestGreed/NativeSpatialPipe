<# SPDX-License-Identifier: Apache-2.0
.SYNOPSIS
  Builds nsp_aes67.dll, the loadable AES67 (RTP/L24) output backend used by the
  Zig scheduler's "aes67" outputBackend.
.DESCRIPTION
  Compiles aes67_tx.c + aes67_sdp.c with the pinned LLVM-MinGW clang into
  build/aes67-output/nsp_aes67.dll and exports the C ABI via --export-all-symbols.
  The standalone CLI sender (aes67-send.exe) is built separately by Build.ps1.
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools/aes67-output/Build-NspAes67Dll.ps1 -LlvmBin C:\work\llvm\bin
#>
param(
    [Parameter(Mandatory = $true)][string]$LlvmBin,
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = Split-Path -Parent (Split-Path -Parent $here)
if (-not $OutDir) { $OutDir = Join-Path $root "build/aes67-output" }
$OutDir = if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $root $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$clang = Join-Path $LlvmBin "clang.exe"
if (-not (Test-Path -LiteralPath $clang)) { throw "clang.exe missing under $LlvmBin" }
$dll = Join-Path $OutDir "nsp_aes67.dll"
$sources = @(
    (Join-Path $here "aes67_tx.c"),
    (Join-Path $here "aes67_sdp.c")
)
& $clang -std=c11 -O2 -Wall -Wextra -Wno-unused-parameter -shared '-Wl,--export-all-symbols' "-I$here" -o $dll @sources -lws2_32
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $dll)) { throw "nsp_aes67.dll build failed with exit code $LASTEXITCODE" }
$item = Get-Item $dll
Write-Output ("built {0} ({1:N0} bytes)" -f $item.FullName, $item.Length)