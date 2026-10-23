param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=[IO.Path]::GetFullPath($OutputDirectory);New-Item -ItemType Directory -Force $out|Out-Null
$exe="$root/build/fsa-integration/bin/FinalSpatialAudio.exe"
$ini="$root/build/fsa-integration/final-spatial-audio.ini"
$py=(Get-Command python.exe).Source
$original=[IO.File]::ReadAllBytes($ini)
$report=[ordered]@{passed=$false;selections=@();error=$null}
$formats=@(
 @{name='WindowsSonic';command='--mode-sonic';guid='b53d940c-b846-4831-9f76-d102b9b725a0'},
 @{name='DolbyAtmosForHeadphones';command='--mode-dolby-headphones';guid='1459ac38-3875-49bf-bb59-0fe80f4d395d'},
 @{name='DolbyAtmosForSpeakers';command='--mode-dolby-speakers';guid='4c81e564-c8ef-4ad9-9f2c-9ef995533790'},
 @{name='DolbyAtmosForHomeTheater';command='--mode-atmos';guid='a289735d-fa3e-4e35-9d7d-b6f896acb2e7'},
 @{name='DTSHeadphoneX';command='--mode-dts-headphones';guid='4444acb0-8dc0-4c2c-a0d8-2c76db470f86'},
 @{name='DTSXUltra';command='--mode-dtsx-ultra';guid='adafd3c6-ac4c-404a-836a-9615e9060564'},
 @{name='DTSXForHomeTheater';command='--mode-dts';guid='10201b4a-3322-4967-bf40-2caa9bafca44'})
function Invoke-Fsa([string]$command){
 $p=Start-Process $exe -ArgumentList $command -WindowStyle Hidden -PassThru
 if(-not $p.WaitForExit(20000)){throw "FSA command timeout: $command"}
 if($p.ExitCode){throw "FSA command failed: $command"}
}
function Get-Configuration {
 [Windows.Media.Audio.SpatialAudioDeviceConfiguration,Windows.Media.Audio,ContentType=WindowsRuntime]::GetForDeviceId(('\\?\SWD#MMDEVAPI#'+$taskEndpoint+'#{e6327cad-dcec-4949-ae8a-991e976a79d2}'))
}
function Wait-Format($format){
 $deadline=[DateTime]::UtcNow.AddSeconds(25)
 do{
  Start-Sleep -Milliseconds 250
  $c=Get-Configuration
  $active=[string]$c.ActiveSpatialAudioFormat;$selected=[string]$c.DefaultSpatialAudioFormat
 }while(($active.Trim('{}') -ine $format.guid -or $selected.Trim('{}') -ine $format.guid) -and [DateTime]::UtcNow -lt $deadline)
 if($active.Trim('{}') -ine $format.guid -or $selected.Trim('{}') -ine $format.guid){throw "Not active: $($format.name); active=$active"}
 return @{format=$format.name;active=$active;selected=$selected}
}
try {
 if(Get-Process FinalSpatialAudio -ErrorAction SilentlyContinue){throw 'Existing FSA must be left untouched; run only when it is stopped'}
 Add-Type -AssemblyName System.Runtime.WindowsRuntime
 $taskEndpoint=(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$root/tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1"|ConvertFrom-Json).multimedia
 $before=Get-Configuration;$originalGuid=([string]$before.ActiveSpatialAudioFormat).Trim('{}')
 $restore=$formats|Where-Object {$_.guid -ieq $originalGuid}|Select-Object -First 1
 $report.endpoint=$taskEndpoint;$report.original=$originalGuid
 & "$root/build/pipeline-init/fsa-mode-state.exe" snapshot "$out/original-mode.bin" |Set-Content "$out/snapshot.json" -Encoding UTF8
 if($LASTEXITCODE){throw 'Cannot snapshot original endpoint'}
 $text=[Text.Encoding]::Unicode.GetString($original).TrimStart([char]0xfeff).Replace("`r`r`n","`r`n")
 foreach($key in @('PersistEnumerationHook','HookHeadphones','HookSpeakers')){
  if($text -match "(?m)^$key="){$text=[regex]::Replace($text,"(?m)^$key=[^`r`n]*","$key=1")}
  else{$text=$text.TrimEnd()+"`r`n$key=1`r`n"}
 }
 [IO.File]::WriteAllText($ini,$text,[Text.Encoding]::Unicode)
 $owner=Start-Process $exe -WindowStyle Hidden -PassThru
 $report.ownerPid=$owner.Id
 $deadline=[DateTime]::UtcNow.AddSeconds(30)
 do{Start-Sleep -Milliseconds 750;$resident=(& $py "$PSScriptRoot/manager.py" status|ConvertFrom-Json)}while(-not $resident.live -and [DateTime]::UtcNow -lt $deadline)
 if(-not $resident.live){throw 'FSA did not establish enumeration resident'}
 $report.residentBefore=$resident
 foreach($format in $formats){Invoke-Fsa $format.command;$report.selections+=@(Wait-Format $format)}
 $report.residentAfter=(& $py "$PSScriptRoot/manager.py" status|ConvertFrom-Json)
 $report.passed=($report.selections.Count -eq 7 -and $report.residentBefore.pid -eq $report.residentAfter.pid)
}catch{$report.error=$_.Exception.ToString()}
finally {
 try{
  if($owner){
   if($restore){Invoke-Fsa $restore.command;$report.restored=Wait-Format $restore}
   Invoke-Fsa '--stop'
   & "$root/build/pipeline-init/fsa-mode-state.exe" restore "$out/original-mode.bin" |Set-Content "$out/restore.json" -Encoding UTF8
   if($LASTEXITCODE){throw 'Original endpoint restore failed'}
   $report.recovery=(& $py "$PSScriptRoot/manager.py" recover|ConvertFrom-Json)
  }
 }catch{$report.restoreError=$_.Exception.ToString();$report.passed=$false}
 [IO.File]::WriteAllBytes($ini,$original)
 if($owner -and $owner.HasExited){$report.restartedPid=(Start-Process $exe -WindowStyle Hidden -PassThru).Id}
 $report|ConvertTo-Json -Depth 12|Set-Content "$out/result.json" -Encoding UTF8
}
if(-not $report.passed){exit 1}
