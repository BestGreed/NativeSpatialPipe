# Refresh the Python/JSON/Web runtime files of an installed NSP application tree.
#
# Build outputs (DLLs, nsp-scheduler.exe) are separate artifacts. This script
# only syncs the *data* files that the scheduler loads at runtime, because they
# are neither compiled nor covered by a binary install step:
#   tools/telemetry/*.py               telemetry service (metering, contract use)
#   tools/visualization-contract/*.py  contract loaded by the telemetry service
#   contracts/output-layouts.json      output layout catalog
#   contracts/visualization/v0/*       JSON schema + fixtures served to the page
#   tools/visualization-frontend/*     Web page served on 8765
#
# Regression it prevents: an app tree whose catalog/contract pair disagrees with
# the source makes the telemetry service raise ValueError on the first snapshot
# flush and exit, which leaves the 8765 page without live data.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/Sync-AppRuntime.ps1 -Target <installed app> -Source <source tree>
#   ... -WhatIf   (list only)      ... -SkipVerify (after a manual review)
param(
  [Parameter(Mandatory=$true)][string]$Target,
  [string]$Source = '',
  [string]$Python = '',
  [switch]$WhatIf,
  [switch]$SkipVerify
)
$ErrorActionPreference='Stop'
if(-not $Source){ $Source = (Resolve-Path "$PSScriptRoot/..").Path }
$Source = (Resolve-Path -LiteralPath $Source).Path
if(-not (Test-Path -LiteralPath $Target -PathType Container)){ throw "Target not found: $Target" }
$Target = (Resolve-Path -LiteralPath $Target).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $Target "_runtime-backup\$stamp"

$dirSets = @(
  @{ Rel='tools/telemetry';              Patterns=@('*.py'); Exclude=@('test_*.py') },
  @{ Rel='tools/visualization-contract'; Patterns=@('*.py') },
  @{ Rel='tools/visualization-frontend'; Patterns=@('*');  Recurse=$true },
  @{ Rel='contracts/visualization/v0';   Patterns=@('schema.json','fixtures/*.json') }
)
$fileSets = @('contracts/output-layouts.json')

$changed = New-Object System.Collections.Generic.List[string]
function Sync-File([string]$src,[string]$rel){
  $dst = Join-Path $Target $rel
  $srcHash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
  if(Test-Path -LiteralPath $dst){
    $dstHash = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash
    if($srcHash -eq $dstHash){ return }
    $bak = Join-Path $backup $rel
    New-Item -ItemType Directory -Force (Split-Path -Parent $bak) | Out-Null
    if(-not (Test-Path -LiteralPath $bak)){ Copy-Item -LiteralPath $dst -Destination $bak -Force }
    if($WhatIf){ Write-Output ("WOULD UPDATE " + $rel); return }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    ($changed.Add($rel)) | Out-Null
    Write-Output ("UPDATED " + $rel)
  } else {
    if($WhatIf){ Write-Output ("WOULD ADD " + $rel); return }
    New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Force
    ($changed.Add($rel)) | Out-Null
    Write-Output ("ADDED " + $rel)
  }
}
foreach($set in $dirSets){
  $srcDir = Join-Path $Source $set.Rel
  if(-not (Test-Path -LiteralPath $srcDir)){ continue }
  $items = Get-ChildItem -LiteralPath $srcDir -File -Recurse:([bool]$set.Recurse) |
    Where-Object { $p=$_.FullName.Substring($srcDir.Length).TrimStart('\','/'); $p -notmatch '(^|[\\/])__pycache__([\\/]|$)' -and $p -notmatch '(^|[\\/])node_modules([\\/]|$)' } |
    Where-Object { $name=$_.Name; ($set.Patterns | Where-Object { $name -like $_ }).Count -gt 0 } |
    Where-Object { $name=$_.Name; ($set.Exclude | Where-Object { $name -like $_ }).Count -eq 0 }
  foreach($item in $items){
    $rel = $set.Rel + '/' + $item.FullName.Substring($srcDir.Length).TrimStart('\','/').Replace('\','/')
    Sync-File $item.FullName $rel
  }
}
foreach($rel in $fileSets){
  $srcFile = Join-Path $Source $rel
  if(-not (Test-Path -LiteralPath $srcFile)){ continue }
  Sync-File $srcFile $rel
}
# Stale bytecode would otherwise be reused if a source timestamp is preserved.
$pycache = Join-Path $Target 'tools/telemetry/__pycache__'
if(Test-Path -LiteralPath $pycache){
  if(-not $WhatIf){ Remove-Item -LiteralPath $pycache -Recurse -Force; Write-Output 'CLEARED tools/telemetry/__pycache__' }
}
if($WhatIf){
  Write-Output ("WHATIF complete: " + $changed.Count + " file(s) would change; backup root would be " + $backup)
  return
}
Write-Output ("SYNC complete: " + $changed.Count + " file(s) updated, backup at " + $backup)
foreach($rel in @('contracts/output-layouts.json','tools/visualization-contract/contract.py','contracts/visualization/v0/schema.json','tools/telemetry/layout_presets.py')){
  $p = Join-Path $Target $rel
  if(Test-Path -LiteralPath $p){ Write-Output ("SHA256 " + (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash + "  " + $rel) }
}
if($SkipVerify){ return }
if(-not $Python){
  $candidate = Join-Path $Target 'runtime/python/python.exe'
  if(Test-Path -LiteralPath $candidate){ $Python = $candidate } else { $Python = 'python' }
}
$verify = @'
import struct, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'tools' / 'telemetry'))
sys.path.insert(0, str(root / 'tools' / 'visualization-contract'))
from layout_presets import LAYOUTS
from engine import HEADER, contract
from soundscape import SoundscapeEngine
assert LAYOUTS, 'layout_presets is empty'
layouts = sorted(LAYOUTS)
messages = 0
max_channels = 0
for name in layouts:
    channels = LAYOUTS[name]
    assert len(channels) == len(set(channels)), name
    engine = SoundscapeEngine(output_layout=name)
    raw = HEADER.pack(b'NSPT', 1, len(channels), 480, 48000, 1, 0)
    raw += struct.pack('<' + 'f' * (len(channels) * 480), *([0.25] * (len(channels) * 480)))
    assert engine.ingest(raw), name
    engine.flush(True)
    for message in (engine.layout(), engine.capabilities(), engine.latest, engine.history(600)):
        contract.validate(message)
        messages += 1
    layout = engine.layout()
    assert [c['channelId'] for c in layout['channels']] == channels, name
    max_channels = max(max_channels, len(channels))
print('VERIFY_OK layouts=%d messages=%d maxChannels=%d' % (len(layouts), messages, max_channels))
'@
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('nsp-runtime-verify-' + [guid]::NewGuid().ToString('N') + '.py')
[IO.File]::WriteAllText($tmp, $verify, (New-Object Text.UTF8Encoding($false)))
try{
  Write-Output '== verification =='
  & $Python $tmp $Target
  if($LASTEXITCODE){ throw 'Installed runtime verification failed' }
} finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
Write-Output 'SYNC_VERIFIED'
