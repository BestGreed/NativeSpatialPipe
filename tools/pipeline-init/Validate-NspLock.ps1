param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=[IO.Path]::GetFullPath($OutputDirectory);New-Item -ItemType Directory -Force $out|Out-Null
$exe="$root/build/fsa-integration/bin/FinalSpatialAudio.exe";$ini="$root/build/fsa-integration/final-spatial-audio.ini"
$py=(Get-Command python.exe).Source;$stateDir="$env:LOCALAPPDATA/NativeSpatialPipe"
$original=[IO.File]::ReadAllBytes($ini);[IO.File]::WriteAllBytes("$out/original.ini",$original)
$report=[ordered]@{passed=$false;stage='initial';error=$null};$owner=$trigger=$null;$touched=$false
function Invoke-Fsa([string]$command){$p=Start-Process $exe -ArgumentList $command -WindowStyle Hidden -PassThru;if(-not $p.WaitForExit(20000) -or $p.ExitCode){throw "FSA command failed: $command"}}
function Read-NspKey([string]$key){
 $text=[IO.File]::ReadAllText($ini);$body=[regex]::Match($text,'(?ms)^\[NSP\][^\r\n]*\r?\n(.*?)(?=^\[|\z)').Groups[1].Value
 return [regex]::Match($body,'(?mi)^'+[regex]::Escape($key)+'=([^\r\n]*)').Groups[1].Value
}
function Wait-Running([string]$different=''){
 $deadline=[DateTime]::UtcNow.AddSeconds(65)
 do{
  Start-Sleep -Milliseconds 500
  $session=Get-Content "$stateDir/session.json" -Raw|ConvertFrom-Json
  $hook=Get-Content "$stateDir/hook-status.json" -Raw|ConvertFrom-Json
 }while(($session.ownerPid -ne $owner.Id -or $session.state -ne 'running' -or $hook.code -ne 0 -or $session.id -eq $different) -and [DateTime]::UtcNow -lt $deadline)
 if($session.ownerPid -ne $owner.Id -or $session.state -ne 'running' -or $hook.code -ne 0 -or $session.id -eq $different){throw "NSp not verified running: state=$($session.state) code=$($hook.code)"}
 if($session.config.spatialFormat -ne 'DTSXUltra'){throw 'Spatial rule did not route to the locked DTSXUltra frontend'}
 return @{session=$session;hook=$hook}
}
try {
 if(Get-Process FinalSpatialAudio -ErrorAction SilentlyContinue){throw 'Existing FSA left untouched; fixture requires it stopped'}
 $resident=& $py "$PSScriptRoot/manager.py" status|ConvertFrom-Json;if($resident.live){throw 'Existing enumeration resident left untouched'}
 $id=(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$root/tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1"|ConvertFrom-Json).multimedia
 $report.endpoint=$id
 & "$root/build/pipeline-init/fsa-mode-state.exe" snapshot "$out/original-mode.bin"|Set-Content "$out/snapshot.json" -Encoding UTF8
 if($LASTEXITCODE){throw 'Original endpoint snapshot failed'}
 $touched=$true
 $text=[Text.Encoding]::Unicode.GetString($original).TrimStart([char]0xfeff).Replace("`r`r`n","`r`n")
 $text=[regex]::Replace($text,'(?ms)^\[Rule:[^\]]+\][^\r\n]*\r?\n.*?(?=^\[|\z)',[Text.RegularExpressions.MatchEvaluator]{param($m)[regex]::Replace($m.Value,'(?mi)^Enabled=[^\r\n]*','Enabled=0')})
 $text=[regex]::Replace($text,'(?ms)^\[NSP\][^\r\n]*\r?\n(?<body>.*?)(?=^\[|\z)',[Text.RegularExpressions.MatchEvaluator]{param($m)
  $body=$m.Groups['body'].Value
  foreach($key in @('NSpLock','PersistEnumerationHook','HookHeadphones','HookSpeakers')){
   if($body -match "(?m)^$key="){$body=[regex]::Replace($body,"(?m)^$key=[^`r`n]*","$key=0")}
   else{$body=$body.TrimEnd()+"`r`n$key=0`r`n"}
  }
  return "[NSP]`r`n"+$body
 })
 $text=$text.TrimEnd()+"`r`n`r`n[Rule:NspLockValidation]`r`nEnabled=1`r`nExe=NspLockRuleTrigger.exe`r`nScope=Running`r`nProfile=Atmos`r`nPriority=2147483000`r`n"
 [IO.File]::WriteAllText($ini,$text,[Text.Encoding]::Unicode)
 $report.stage='prepare-ultra'
 & $py "$PSScriptRoot/pipeline.py" prepare --endpoint $id --format DTSXUltra --persist --groups speakers|Set-Content "$out/prepare.json" -Encoding UTF8
 if($LASTEXITCODE){throw 'DTSXUltra preparation failed'}
 $before=& $py "$PSScriptRoot/manager.py" status|ConvertFrom-Json;$report.residentBefore=$before
 $owner=Start-Process $exe -WindowStyle Hidden -PassThru;Start-Sleep -Seconds 2
 Invoke-Fsa '--nsp-lock'
 $deadline=[DateTime]::UtcNow.AddSeconds(15)
 do{Start-Sleep -Milliseconds 250}while((Read-NspKey 'NSpLock') -ne '1' -and [DateTime]::UtcNow -lt $deadline)
 $report.snapshot=@{format=(Read-NspKey 'LockedFrontend');endpoint=(Read-NspKey 'LockEndpoint');hooks=(Read-NspKey 'LockHookMask');online=(Read-NspKey 'PersistEnumerationHook');speakers=(Read-NspKey 'HookSpeakers')}
 if($report.snapshot.format -ne 'DTSXUltra' -or $report.snapshot.endpoint -ne $id -or $report.snapshot.hooks -ne '4' -or $report.snapshot.online -ne '1' -or $report.snapshot.speakers -ne '1'){throw 'Live lock snapshot mismatch'}
 $report.stage='rule-start'
 $trigger=Start-Process "$root/build/pipeline-init/NspLockRuleTrigger.exe" -WindowStyle Hidden -PassThru
 Invoke-Fsa '--auto';$report.first=Wait-Running
 $report.stage='rule-fallback'
 $trigger.Kill();$trigger.WaitForExit();$trigger.Dispose();$trigger=$null
 $deadline=[DateTime]::UtcNow.AddSeconds(55)
 do{Start-Sleep -Milliseconds 500;$session=Get-Content "$stateDir/session.json" -Raw|ConvertFrom-Json;$hook=Get-Content "$stateDir/hook-status.json" -Raw|ConvertFrom-Json}while(($session.state -ne 'stopped' -or $hook.code -ne 10) -and [DateTime]::UtcNow -lt $deadline)
 if($session.state -ne 'stopped' -or $hook.code -ne 10){throw 'PCM fallback did not verify NSp stopped'}
 Add-Type -AssemblyName System.Runtime.WindowsRuntime
 $cfg=[Windows.Media.Audio.SpatialAudioDeviceConfiguration,Windows.Media.Audio,ContentType=WindowsRuntime]::GetForDeviceId(('\\?\SWD#MMDEVAPI#'+$id+'#{e6327cad-dcec-4949-ae8a-991e976a79d2}'))
 $deadline=[DateTime]::UtcNow.AddSeconds(15)
 do{Start-Sleep -Milliseconds 250;$active=[guid]$cfg.ActiveSpatialAudioFormat}while($active -ne [guid]::Empty -and [DateTime]::UtcNow -lt $deadline)
 if($active -ne [guid]::Empty){throw 'PCM fallback left a spatial format active'}
 $report.fallback=@{session=$session;hook=$hook;active=[string]$active}
 $report.stage='rule-reenter'
 $trigger=Start-Process "$root/build/pipeline-init/NspLockRuleTrigger.exe" -WindowStyle Hidden -PassThru
 $report.second=Wait-Running $report.first.session.id
 $report.residentAfter=(& $py "$PSScriptRoot/manager.py" status|ConvertFrom-Json)
 if($report.residentAfter.pid -ne $before.pid){throw 'Healthy enumeration hook was restarted during lock/routing'}
 $report.passed=$true;$report.stage='verified'
}catch{$report.error=$_.Exception.ToString()}
finally {
 try{
  if($trigger -and -not $trigger.HasExited){$trigger.Kill();$trigger.WaitForExit();$trigger.Dispose()}
  if($owner -and -not $owner.HasExited){Invoke-Fsa '--stop'}
  if($touched){
   $report.recovery=(& $py "$PSScriptRoot/manager.py" recover|ConvertFrom-Json)
   & "$root/build/pipeline-init/fsa-mode-state.exe" restore "$out/original-mode.bin"|Set-Content "$out/restore.json" -Encoding UTF8
   if($LASTEXITCODE){throw 'Original audio state restore failed'}
  }
 }catch{$report.restoreError=$_.Exception.ToString();$report.passed=$false}
 if(-not $owner -or $owner.HasExited){[IO.File]::WriteAllBytes($ini,$original)}else{$report.iniRestorationPending=$true}
 $report|ConvertTo-Json -Depth 20|Set-Content "$out/result.json" -Encoding UTF8
}
if(-not $report.passed){exit 1}
