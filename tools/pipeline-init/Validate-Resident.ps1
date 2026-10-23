param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=[IO.Path]::GetFullPath($OutputDirectory);New-Item -ItemType Directory -Path $out -Force|Out-Null
$py=(Get-Command python.exe).Source
$manager=Join-Path $PSScriptRoot 'manager.py';$pipeline=Join-Path $PSScriptRoot 'pipeline.py'
$defaults=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$root/tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1"|ConvertFrom-Json
$id=$defaults.multimedia
$before=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot/Spatial-Format.ps1" -Action query -Endpoint $id -Format WindowsSonic|ConvertFrom-Json
$report=[ordered]@{endpoint=$id;before=$before;selections=@();errors=@()}
try{
 & $py $manager status|Set-Content "$out/status-before.json" -Encoding UTF8
 & "$root/build/pipeline-init/memory-tag-test.exe" $id 2|Set-Content "$out/fsa-memory-reader.json" -Encoding UTF8
 $report.fsaReaderExitCode=$LASTEXITCODE
 foreach($format in 'DolbyAtmosForHeadphones','DTSHeadphoneX','DolbyAtmosForSpeakers','DTSXUltra'){
  $raw=& $py $pipeline prepare --endpoint $id --format $format --persist --groups headphones speakers
  $report.selections+=@{format=$format;exitCode=$LASTEXITCODE;result=($raw|ConvertFrom-Json)}
 }
}catch{$report.errors+=@($_.Exception.ToString())}
finally{
 $names=@{ 'b53d940c-b846-4831-9f76-d102b9b725a0'='WindowsSonic';'10201b4a-3322-4967-bf40-2caa9bafca44'='DTSXForHomeTheater' }
 $format=$names[$before.selected.Trim('{}').ToLower()]
 if($format){& $py $pipeline prepare --endpoint $id --format $format|Set-Content "$out/restore-mode.json" -Encoding UTF8}
 else{$report.errors+=@('Unknown initial selection; explicit restore needed')}
 & $py $manager recover|Set-Content "$out/recovery.json" -Encoding UTF8
 $report.recoveryExitCode=$LASTEXITCODE
 & $py $manager status|Set-Content "$out/status-after.json" -Encoding UTF8
 $report|ConvertTo-Json -Depth 12|Set-Content "$out/result.json" -Encoding UTF8
}
