param([switch]$Start,[string]$NodeExe='')
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$build=Join-Path $root 'build/browser-identity-v1'
$settings=Join-Path $env:LOCALAPPDATA 'NativeSpatialPipe/playback.json'
$ini=Join-Path $root 'build/fsa-integration/final-spatial-audio.ini'
$backup=Join-Path $build 'deployment-backup';New-Item -ItemType Directory -Force $backup|Out-Null
foreach($pair in @(@($settings,'playback.json'),@($ini,'final-spatial-audio.ini'))){$dest=Join-Path $backup $pair[1];if(!(Test-Path -LiteralPath $dest)){Copy-Item -LiteralPath $pair[0] -Destination $dest}}
$required=@('build/pcm-capture-v1/NSP.PcmCapture.dll','build/pcm-capture-v1/NSP.PcmIngress.dll','build/browser-identity-v1/NSP.BrowserIdentity.exe','build/nsp-product/nsp-scheduler.exe')
foreach($file in $required){if(-not(Test-Path -LiteralPath (Join-Path $root $file))){throw "Missing product build $file"}}
if(-not $NodeExe){$NodeExe=(Get-Command node.exe -ErrorAction Stop).Source}
$NodeExe=(Resolve-Path -LiteralPath $NodeExe).Path
$p=Get-Content -LiteralPath $settings -Raw|ConvertFrom-Json
if(-not $p.pcmCapture -or -not $p.pcmCapture.enabled -or $p.sourcePolicy -ne 'unified'){throw 'Configure unified PCM capture before enabling browser identity'}
$identity=[ordered]@{enabled=$true;nodeExe=$NodeExe;agentScript=(Join-Path $PSScriptRoot 'agent.mjs');backendExe=(Join-Path $build 'NSP.BrowserIdentity.exe');identityFile=(Join-Path $env:LOCALAPPDATA 'NativeSpatialPipe/browser-identity/identities.bin');devtoolsUrl='http://127.0.0.1:9222';devtoolsActivePortFile=(Join-Path $env:LOCALAPPDATA 'Microsoft/Edge/User Data/DevToolsActivePort');metadataPort=8784;extensionId=(Get-Content (Join-Path $PSScriptRoot 'extension-id.txt') -Raw).Trim();leaseMs=500;sourceTabId=0}
$p.pcmCapture.captureDll=Join-Path $root 'build/pcm-capture-v1/NSP.PcmCapture.dll';$p.pcmCapture.ingressDll=Join-Path $root 'build/pcm-capture-v1/NSP.PcmIngress.dll'
$p.pcmCapture.PSObject.Properties.Remove('browserIdentity')
$p|Add-Member -NotePropertyName browserIdentity -NotePropertyValue $identity -Force
# Preserve renderer, gain, filters, bass, endpoint selection and output mode.
$p|ConvertTo-Json -Depth 30|Set-Content -Encoding UTF8 -LiteralPath $settings
& (Join-Path $root 'modules/fsa-integration/Configure-Scheduler.ps1') -Configuration $ini -ControlExecutable (Join-Path $root 'build/nsp-product/nsp-scheduler.exe') -PlaybackConfig $settings
@{settings=$settings;configuration=$ini;backup=$backup;browserIdentity=$identity}|ConvertTo-Json -Depth 5|Set-Content -Encoding UTF8 (Join-Path $build 'deployed.json')
if($Start){$exe=Join-Path $root 'build/fsa-integration/bin/FinalSpatialAudio.exe';$running=Get-Process FinalSpatialAudio -ErrorAction SilentlyContinue;if($running){$stop=Start-Process $exe -ArgumentList '--stop' -WindowStyle Hidden -PassThru;$null=$stop.WaitForExit(10000);for($i=0;$i -lt 80;$i++){if(!(Get-Process FinalSpatialAudio -ErrorAction SilentlyContinue)){break};Start-Sleep -Milliseconds 100};if(Get-Process FinalSpatialAudio -ErrorAction SilentlyContinue){throw 'FSA did not stop cleanly'}};Start-Process $exe -WindowStyle Hidden}
Get-Content (Join-Path $build 'deployed.json')
