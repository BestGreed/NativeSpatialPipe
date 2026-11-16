param([ValidateSet('select','notify','query')][string]$Action='query',[string]$Endpoint,[string]$Format='WindowsSonic',[string]$Formats)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$ids=@{WindowsSonic='{B53D940C-B846-4831-9F76-D102B9B725A0}';DolbyAtmosForHeadphones='{1459AC38-3875-49BF-BB59-0FE80F4D395D}';DolbyAtmosForSpeakers='{4C81E564-C8EF-4AD9-9F2C-9EF995533790}';DolbyAtmosForHomeTheater='{A289735D-FA3E-4E35-9D7D-B6F896ACB2E7}';DTSHeadphoneX='{4444ACB0-8DC0-4C2C-A0D8-2C76DB470F86}';DTSXUltra='{ADAFD3C6-AC4C-404A-836A-9615E9060564}';DTSXForHomeTheater='{10201B4A-3322-4967-BF40-2CAA9BAFCA44}'}
if($Action -eq 'notify'){
 $c=[Windows.Media.Audio.SpatialAudioFormatConfiguration,Windows.Media.Audio,ContentType=WindowsRuntime]::GetDefault()
 foreach($n in $Formats.Split(',')){
  if(-not $ids.ContainsKey($n)){throw 'Unknown format'}
  foreach($method in 'ReportLicenseChangedAsync','ReportConfigurationChangedAsync'){
   $op=$c.$method($ids[$n]);$m=[System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object {$_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.FullName -eq 'Windows.Foundation.IAsyncAction'}|Select-Object -First 1
   $task=$m.Invoke($null,@($op));if(-not $task.Wait(5000)){throw 'Notification timed out'};$null=$task.GetAwaiter().GetResult()
  }
 };return
}
if(-not $ids.ContainsKey($Format) -or $Endpoint -notmatch '^\{0\.0\.0\.00000000\}\.\{[0-9a-fA-F-]+\}$'){throw 'Invalid format/endpoint'}
$c=[Windows.Media.Audio.SpatialAudioDeviceConfiguration,Windows.Media.Audio,ContentType=WindowsRuntime]::GetForDeviceId(('\\?\SWD#MMDEVAPI#'+$Endpoint+'#{e6327cad-dcec-4949-ae8a-991e976a79d2}'))
$r=[ordered]@{format=$Format;supported=$c.IsSpatialAudioFormatSupported($ids[$Format]);before=[string]$c.ActiveSpatialAudioFormat}
if($Action -eq 'select'){
 $rt=[Windows.Media.Audio.SetDefaultSpatialAudioFormatResult,Windows.Media.Audio,ContentType=WindowsRuntime]
 $op=$c.SetDefaultSpatialAudioFormatAsync($ids[$Format]);$m=[System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1}|Select-Object -First 1
 $task=$m.MakeGenericMethod($rt).Invoke($null,@($op));if(-not $task.Wait(10000)){throw 'Mode selection timed out'}
 $r.result=[string]$task.GetAwaiter().GetResult().Status
}
$r.active=[string]$c.ActiveSpatialAudioFormat;$r.selected=[string]$c.DefaultSpatialAudioFormat
$r|ConvertTo-Json -Compress
if($Action -eq 'select' -and ($r.result -ne 'Succeeded' -or $r.active -ine $ids[$Format] -or $r.selected -ine $ids[$Format])){exit 1}
