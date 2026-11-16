param(
 [Parameter(Mandatory=$true)][string]$Configuration,
 [ValidateSet('Zig')][string]$Scheduler='Zig',
 [string]$TelemetryPython='',
 [string]$ControlExecutable='',
 [string]$PlaybackConfig,
 [bool]$StartOnLaunch,
 [string]$FrontendFormat
)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$native=if($ControlExecutable){[IO.Path]::GetFullPath($ControlExecutable)}else{Join-Path $root 'build/nsp-product/nsp-scheduler.exe'}
if($Scheduler -eq 'Zig' -and -not(Test-Path -LiteralPath $native -PathType Leaf)){throw 'Build the Zig scheduler first; Python fallback is disabled'}
$path=(Resolve-Path -LiteralPath $Configuration).Path
$text=[IO.File]::ReadAllText($path)
$sections=[regex]::Matches($text,'(?ms)^\[NSP\][^\r\n]*\r?\n(?<body>.*?)(?=^\[|\z)')
if($sections.Count -gt 1){throw 'Duplicate NSP sections; preserve and repair configuration explicitly'}
$body=if($sections.Count){$sections[0].Groups['body'].Value}else{''}
if(-not $TelemetryPython){
 $old=[regex]::Match($body,'(?mi)^Python=([^\r\n]+)')
 $TelemetryPython=if($old.Success){$old.Groups[1].Value}else{(Get-Command python).Source}
}
# Retired Python control hosts are not a selectable runtime. Remove their
# stale key while preserving Python for endpoint preparation and telemetry.
$body=[regex]::Replace($body,'(?mi)^ControlScript=[^\r\n]*(?:\r?\n|$)','')
$values=[ordered]@{Scheduler=$Scheduler;ControlExecutable=$native;Python=$TelemetryPython;PipelineInitScript=(Join-Path $root 'modules/pipeline-init/pipeline.py')}
if($PSBoundParameters.ContainsKey('StartOnLaunch')){$values.StartOnLaunch=[int]$StartOnLaunch}
if($PSBoundParameters.ContainsKey('FrontendFormat')){
 if($FrontendFormat -notin @('WindowsSonic','DTSXForHomeTheater','DolbyAtmosForHomeTheater','DolbyAtmosForHeadphones','DolbyAtmosForSpeakers','DTSHeadphoneX','DTSXUltra')){throw 'Unknown spatial frontend'}
 $values.FrontendFormat=$FrontendFormat
}
if($PSBoundParameters.ContainsKey('PlaybackConfig')){
 if($PlaybackConfig){
  if($Scheduler -ne 'Zig' -or -not [IO.Path]::IsPathRooted($PlaybackConfig) -or -not(Test-Path -LiteralPath $PlaybackConfig -PathType Leaf)){throw 'PlaybackConfig requires Zig and an existing absolute JSON path'}
  $settings=[IO.File]::ReadAllText($PlaybackConfig)|ConvertFrom-Json
  if($settings.renderer -and $settings.renderer -notin @('roar','olr','lira')){throw 'Unknown playback renderer'}
  if($null -ne $settings.mode -and ($settings.renderer -eq 'lira' -or $settings.mode -ne 'olr')){throw 'Invalid playback renderer mode'}
 }
 $values.PlaybackConfig=$PlaybackConfig
}
foreach($key in $values.Keys){
 $value=[string]$values[$key]
 if($value -match '[\r\n"]'){throw 'Invalid scheduler configuration value'}
 $pattern='(?mi)^'+[regex]::Escape($key)+'=[^\r\n]*(?:\r?\n|$)'
 $line=$key+'='+$value+"`r`n"
 if([regex]::IsMatch($body,$pattern)){$body=[regex]::Replace($body,$pattern,[Text.RegularExpressions.MatchEvaluator]{param($m)$line})}
 else{$body=$body.TrimEnd("`r","`n")+"`r`n"+$line}
}
$replacement="[NSP]`r`n"+$body
if($sections.Count){$section=$sections[0];$text=$text.Substring(0,$section.Index)+$replacement+$text.Substring($section.Index+$section.Length)}
else{$text=$text.TrimEnd("`r","`n")+"`r`n`r`n"+$replacement}
[IO.File]::WriteAllText($path,$text,[Text.Encoding]::Unicode)
Write-Output "Configured FSA scheduler: $Scheduler"
