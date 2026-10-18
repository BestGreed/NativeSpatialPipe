param([Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$FullTiming,[ValidateSet('WindowsSonic','DTSXForHomeTheater')][string]$SpatialFormat='WindowsSonic',[switch]$Managed,[int]$OwnerPid=0,[string]$Endpoint='',[string]$SchedulerExecutable='',[string]$TelemetryPython='python.exe',[int]$TelemetryPort=8766,[string]$FrontendOrigin='http://127.0.0.1:8765',[switch]$LegacyPythonScheduler)
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location -LiteralPath $root
if($LegacyPythonScheduler -and $SchedulerExecutable){throw 'Choose either Zig or explicit legacy Python orchestration'}
if(-not $LegacyPythonScheduler){
 if(-not $SchedulerExecutable){$SchedulerExecutable=Join-Path $root 'build/zig-scheduler/nsp-scheduler.exe'}
 if(-not(Test-Path -LiteralPath $SchedulerExecutable -PathType Leaf)){throw 'Build the Zig scheduler first; Python fallback is disabled'}
 if(-not $Managed){$Managed=$true;if($OwnerPid -le 0){$OwnerPid=$PID}}
}
if($Managed -and $OwnerPid -le 0){throw 'Managed mode requires OwnerPid'}
$out=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
$stopFile="$out.stop"
if(Test-Path -LiteralPath $out){throw 'Use a new output directory'}
if(([IO.DriveInfo]::new([IO.Path]::GetPathRoot($out))).AvailableFreeSpace -lt 1GB){throw 'Need 1 GiB free'}
New-Item -ItemType Directory -Path $out|Out-Null
Start-Transcript -Path "$out/orchestrator.log" | Out-Null
$python=if($SchedulerExecutable){$null}else{(Get-Command python).Source}
if($SchedulerExecutable -and -not $Managed){throw 'Zig scheduler requires managed mode'}
if(-not $Endpoint){throw 'Pass an explicitly verified -Endpoint; no device is selected automatically'}
if($Endpoint -match '^\{0\.0\.0\.00000000\}\.\{[0-9a-fA-F-]+\}$'){
 $Endpoint='\\?\SWD#MMDEVAPI#'+$Endpoint+'#{e6327cad-dcec-4949-ae8a-991e976a79d2}'
}
$endpoint=$Endpoint
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$formatType=[Windows.Media.Audio.SpatialAudioFormatSubtype,Windows.Media.Audio,ContentType=WindowsRuntime]
$targetFormat=([guid]$formatType::$SpatialFormat).ToString('B')
$resultType=[Windows.Media.Audio.SetDefaultSpatialAudioFormatResult,Windows.Media.Audio,ContentType=WindowsRuntime]
function Await-Format($op){
 $m=[System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1}|Select-Object -First 1
 $m.MakeGenericMethod($resultType).Invoke($null,@($op)).GetAwaiter().GetResult()
}
$cfg=[Windows.Media.Audio.SpatialAudioDeviceConfiguration,Windows.Media.Audio,ContentType=WindowsRuntime]::GetForDeviceId($endpoint)
$record=[ordered]@{endpoint=$endpoint;spatialFormat=$SpatialFormat;targetFormat=$targetFormat;beforeSelected=$cfg.DefaultSpatialAudioFormat;beforeActive=$cfg.ActiveSpatialAudioFormat;startedUtc=[DateTime]::UtcNow.ToString('o')}
$record|ConvertTo-Json|Set-Content -LiteralPath "$out/endpoint-before.json" -Encoding UTF8
$hook=$null
try {
 if(Test-Path -LiteralPath $stopFile){throw 'Session cancelled before activation'}
 if(-not $cfg.IsSpatialAudioFormatSupported($targetFormat)){throw "$SpatialFormat unsupported"}
 if([guid]$cfg.DefaultSpatialAudioFormat -eq $targetFormat -and [guid]$cfg.ActiveSpatialAudioFormat -eq $targetFormat){$record.setStatus='AlreadyActive'}
 else{$record.setStatus="$( (Await-Format ($cfg.SetDefaultSpatialAudioFormatAsync($targetFormat))).Status )"}
 if($record.setStatus -notin @('Succeeded','AlreadyActive')){throw "$SpatialFormat activation failed"}
 Start-Sleep -Seconds 2
 $record.selectedDuring=$cfg.DefaultSpatialAudioFormat;$record.activeDuring=$cfg.ActiveSpatialAudioFormat
 if([guid]$record.selectedDuring -ne $targetFormat -or [guid]$record.activeDuring -ne $targetFormat){throw 'Requested spatial format is not active'}
 if(Test-Path -LiteralPath $stopFile){throw 'Session cancelled before Hook startup'}
 $record|ConvertTo-Json -Depth 6|Set-Content -LiteralPath "$out/endpoint-active.json" -Encoding UTF8
 $hosts=@(Get-Process audiodg)
 if($hosts.Count -ne 1){throw 'Require exactly one audiodg'}
 $env:NSP_RENDER_MMCSS='1'
 $dll=Join-Path $root 'build/telemetry-objects/release/nsp_roar_bridge.dll'
 $capture=Join-Path $root 'build/capture-scheduler-mmcss/nsp_native_capture.dll'
 $arguments='"'+$root+'/tools/endpoint-probe/Sync-Hook.py" "'+$out+'/observed" '+$hosts[0].Id+' --native-hook --native-send --native-capture --rust-receiver --receiver-mmcss --prefer-dynamic-streams --unmarked --stream --split-streams --duration 1200 --drain-ms 1 --waitable-drain --consumer-capacity 64 --stop-file "'+$out+'/stop.txt" --roar-gain passthrough --native-prewarm-frames 480 --capture-pack native --event-drain --compact-descriptor --stage-timing --native-roar-mode individual --native-roar-dll "'+$dll+'" --capture-worker-dll "'+$capture+'" --telemetry-service'
 $arguments=$arguments.Replace(' --stage-timing','')
 $arguments=$arguments.Replace(' --stop-file "'+$out+'/stop.txt"',' --stop-file "'+$stopFile+'"')
 if($FullTiming){$arguments+=' --stage-timing --full-timing'}
 if($Managed){if($OwnerPid -le 0){throw 'Managed mode requires OwnerPid'};$arguments+=' --managed --owner-pid '+$OwnerPid}
 if($SchedulerExecutable){
  $arguments='host --root "'+$root+'" --out "'+$out+'/observed" --pid '+$hosts[0].Id+' --owner-pid '+$OwnerPid+' --stop-file "'+$stopFile+'" --python "'+$TelemetryPython+'" --telemetry-port '+$TelemetryPort+' --origin "'+$FrontendOrigin+'"'
  if($FullTiming){$arguments+=' --full-timing'}
  $hostExecutable=$SchedulerExecutable
 }else{$hostExecutable=$python}
 $hook=Start-Process -FilePath $hostExecutable -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput "$out/host-stdout.txt" -RedirectStandardError "$out/host-stderr.txt"
 $null=$hook.Handle
 $record.hostPid=$hook.Id
 $deadline=[DateTime]::UtcNow.AddSeconds(50)
 while(-not(Test-Path "$out/observed/ready.txt") -and -not $hook.HasExited -and -not(Test-Path -LiteralPath $stopFile) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
 if(Test-Path -LiteralPath $stopFile){throw 'Session cancelled during startup'}
 if(-not(Test-Path "$out/observed/ready.txt")){throw 'Observer not ready'}
 Write-Output ('FRONTEND_CONNECT '+(Get-Content "$out/observed/telemetry-service.json" -Raw -ErrorAction SilentlyContinue))
 $record.readyUtc=[DateTime]::UtcNow.ToString('o')
 while(-not $hook.HasExited){
  if(Test-Path -LiteralPath "$out/stop.txt"){'stop'|Set-Content -LiteralPath $stopFile}
  Start-Sleep -Seconds 1
 }
} catch {
 if($_.Exception.Message -like 'Session cancelled*'){$record.cancelled=$true}
 else{$record.error=$_.Exception.ToString()}
}
finally {
 if($hook){'stop'|Set-Content -LiteralPath $stopFile;$record.hookCompleted=$hook.WaitForExit(30000);if($record.hookCompleted){$hook.WaitForExit();$record.hookExitCode=$hook.ExitCode}}
 else{$record.hookCompleted=$true;$record.hookExitCode=0}
 try {
  if(-not $record.hookCompleted){throw 'Hook host did not stop; endpoint restore deferred'}
  $record.restoreStatus="$( (Await-Format ($cfg.SetDefaultSpatialAudioFormatAsync($record.beforeSelected))).Status )"
  $record.finalSelected=$cfg.DefaultSpatialAudioFormat;$record.finalActive=$cfg.ActiveSpatialAudioFormat
  $record.restored=($record.finalSelected -eq $record.beforeSelected -and $record.finalActive -eq $record.beforeActive)
 }catch{$record.restoreError=$_.Exception.ToString();$record.restored=$false}
 $record.finishedUtc=[DateTime]::UtcNow.ToString('o')
 $record|ConvertTo-Json -Depth 6|Set-Content -LiteralPath "$out/endpoint.json" -Encoding UTF8
}
$record|ConvertTo-Json -Depth 6
if($record.error -or -not $record.restored -or $record.hookExitCode -ne 0){throw 'Game session failed; inspect reports'}
