param([switch]$Start, [string]$NodeExe)
$ErrorActionPreference='Stop'
if (-not $NodeExe) {
    $node = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $node) { throw 'Browser identity requires Node.js. Install it on PATH or pass -NodeExe.' }
    $NodeExe = $node.Source
}
$nodeFile = Get-Item -LiteralPath $NodeExe
if ($nodeFile.PSIsContainer -or $nodeFile.Extension -ne '.exe') {
    throw '-NodeExe must point to a Windows executable.'
}
$NodeExe = $nodeFile.FullName
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$build=Join-Path $root 'build/browser-identity-v1'
$settings=Join-Path $env:LOCALAPPDATA 'NativeSpatialPipe/playback.json'
$ini=Join-Path $root 'build/fsa-integration/final-spatial-audio.ini'
$backup=Join-Path $build 'deployment-backup';New-Item -ItemType Directory -Force $backup|Out-Null
foreach($pair in @(@($settings,'playback.json'),@($ini,'final-spatial-audio.ini'))){$dest=Join-Path $backup $pair[1];if(!(Test-Path -LiteralPath $dest)){Copy-Item -LiteralPath $pair[0] -Destination $dest}}
$required=@('NSP.PcmCapture.dll','NSP.PcmIngress.dll','NSP.BrowserIdentity.exe','nsp-scheduler-approval.exe','libunwind.dll')
foreach($file in $required){if(!(Test-Path -LiteralPath (Join-Path $build $file))){throw "Missing build $file"}}
$p=Get-Content -LiteralPath $settings -Raw|ConvertFrom-Json
$identity=[ordered]@{enabled=$true;nodeExe=$NodeExe;agentScript=(Join-Path $PSScriptRoot 'agent.mjs');backendExe=(Join-Path $build 'NSP.BrowserIdentity.exe');identityFile=(Join-Path $env:LOCALAPPDATA 'NativeSpatialPipe/browser-identity/identities.bin');devtoolsUrl='http://127.0.0.1:9222';devtoolsActivePortFile=(Join-Path $env:LOCALAPPDATA 'Microsoft/Edge/User Data/DevToolsActivePort');metadataPort=8784;extensionId=(Get-Content (Join-Path $PSScriptRoot 'extension-id.txt') -Raw).Trim();leaseMs=500;sourceTabId=0}
$p.pcmCapture.captureDll=Join-Path $build 'NSP.PcmCapture.dll';$p.pcmCapture.ingressDll=Join-Path $build 'NSP.PcmIngress.dll'
$p.pcmCapture.PSObject.Properties.Remove('browserIdentity')
$p|Add-Member -NotePropertyName browserIdentity -NotePropertyValue $identity -Force
# Preserve renderer, gain, filters, bass, endpoint selection and output mode.
$p|ConvertTo-Json -Depth 30|Set-Content -Encoding UTF8 -LiteralPath $settings
& (Join-Path $root 'modules/fsa-integration/Configure-Scheduler.ps1') -Configuration $ini -ControlExecutable (Join-Path $build 'nsp-scheduler-approval.exe') -PlaybackConfig $settings
@{settings=$settings;configuration=$ini;backup=$backup;browserIdentity=$identity}|ConvertTo-Json -Depth 5|Set-Content -Encoding UTF8 (Join-Path $build 'deployed.json')
if($Start){$exe=Join-Path $root 'build/fsa-integration/bin/FinalSpatialAudio.exe';$running=Get-Process FinalSpatialAudio -ErrorAction SilentlyContinue;if($running){$stop=Start-Process $exe -ArgumentList '--stop' -WindowStyle Hidden -PassThru;$null=$stop.WaitForExit(10000);for($i=0;$i -lt 80;$i++){if(!(Get-Process FinalSpatialAudio -ErrorAction SilentlyContinue)){break};Start-Sleep -Milliseconds 100};if(Get-Process FinalSpatialAudio -ErrorAction SilentlyContinue){throw 'FSA did not stop cleanly'}};Start-Process $exe -WindowStyle Hidden}
Get-Content (Join-Path $build 'deployed.json')
