# Use only after the user has saved their work and authorized Edge restart.
param([string]$BackupDirectory='D:/Codex/LSAC/build/browser-identity-v1/ordinary-edge-backup')
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Force $BackupDirectory|Out-Null
$roots=@(Get-CimInstance Win32_Process|Where-Object {$_.Name -eq 'msedge.exe' -and $_.CommandLine -notmatch '--type=' -and $_.CommandLine -notmatch '--user-data-dir='})
if($roots.Count -gt 1){throw 'Ambiguous ordinary Edge roots'}
$before=Join-Path $BackupDirectory 'edge-before.json'
if(!(Test-Path -LiteralPath $before)){$roots|Select-Object ProcessId,CommandLine|ConvertTo-Json|Set-Content -Encoding UTF8 $before}
foreach($root in $roots){$p=Get-Process -Id $root.ProcessId -ErrorAction Stop;$null=$p.CloseMainWindow();Start-Sleep -Milliseconds 1200;if(!$p.HasExited){Stop-Process -Id $p.Id}}
# Closing the last window can hand off to a fresh startup-boost root. Stop that
# ordinary root too, then launch immediately rather than waiting on orphan helpers.
$remaining=@(Get-CimInstance Win32_Process|Where-Object {$_.Name -eq 'msedge.exe' -and $_.CommandLine -notmatch '--type=' -and $_.CommandLine -notmatch '--user-data-dir='})
foreach($r in $remaining){$p=Get-Process -Id $r.ProcessId;Stop-Process -Id $p.Id;$null=$p.WaitForExit(3000)}
$edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
$p=Start-Process $edge -ArgumentList @('--load-extension=D:/Codex/LSAC/modules/browser-identity/extension','--restore-last-session','--no-first-run') -WindowStyle Hidden -PassThru
# Ordinary profiles use Edge's user-enabled approval server. Command-line
# remote-debugging-port is rejected for this profile and suppresses that server.
$activePort=Join-Path $env:LOCALAPPDATA 'Microsoft/Edge/User Data/DevToolsActivePort'
$ready=$false
for($i=0;$i -lt 80;$i++){try{$lines=Get-Content -LiteralPath $activePort; $port=[int]$lines[0];if($port -gt 0 -and $port -le 65535 -and $lines[1] -match '^/devtools/browser/[a-f0-9-]+$' -and (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)){$ready=$true;break}}catch{};Start-Sleep -Milliseconds 250}
@{browserPid=$p.Id;debuggingReady=$ready;usesOrdinaryProfile=$true;extensionId=(Get-Content D:/Codex/LSAC/modules/browser-identity/extension-id.txt -Raw).Trim();at=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json|Set-Content -Encoding UTF8 (Join-Path $BackupDirectory 'result.json')
if(!$ready){throw 'Edge reopened normally. Enable Allow remote debugging for this browser instance in edge://inspect; no profile or security settings were changed'}
Get-Content (Join-Path $BackupDirectory 'result.json')
