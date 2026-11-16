param([ValidateSet('Start','Stop','EmergencyStop')][string]$Action='Start')
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$candidate=Join-Path $root 'build/fsa-integration/bin/FinalSpatialAudio.exe'
if(-not(Test-Path -LiteralPath $candidate)){throw 'Build the FSA integration candidate first'}
$command=@{Start='--start-nsp';Stop='--stop-nsp';EmergencyStop='--emergency-stop-nsp'}[$Action]
Start-Process -FilePath $candidate -ArgumentList $command -Verb RunAs -WindowStyle Hidden | Out-Null
