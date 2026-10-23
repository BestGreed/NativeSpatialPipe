param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=[IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $out -Force|Out-Null
$py=(Get-Command python.exe).Source
$id=(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$root/tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1"|ConvertFrom-Json).multimedia
$owner=$null
$result=[ordered]@{endpoint=$id;passed=$false;error=$null}
try {
 $existing=& $py "$PSScriptRoot/manager.py" status|ConvertFrom-Json
 if($existing.live){throw 'An existing resident is active; leave it untouched'}
 $owner=Start-Process -FilePath $py -ArgumentList @('-c','"import time; time.sleep(60)"') -WindowStyle Hidden -PassThru
 $result.start=(& $py "$PSScriptRoot/manager.py" start --endpoint $id --groups headphones --owner-pid $owner.Id|ConvertFrom-Json)
 if($LASTEXITCODE -or -not $result.start.live){throw 'Resident did not become live'}
 # Only terminate the private sleeper created by this test.
 $owner.Kill();$owner.WaitForExit()
 $deadline=[DateTime]::UtcNow.AddSeconds(15)
 do {
  Start-Sleep -Milliseconds 500
  $record=Get-Content "$env:LOCALAPPDATA/NativeSpatialPipe/pipeline-init/resident.json" -Raw|ConvertFrom-Json
 } while($record.state -ne 'stopped' -and [DateTime]::UtcNow -lt $deadline)
 $result.record=$record
 $result.status=(& $py "$PSScriptRoot/manager.py" status|ConvertFrom-Json)
 $result.passed=($record.state -eq 'stopped' -and $record.reason -eq 'Owner exited' -and -not $result.status.live -and @($record.cleanup).Count -eq 3 -and @($record.cleanup|Where-Object {-not $_.restored}).Count -eq 0)
}catch{$result.error=$_.Exception.ToString()}
finally {
 if($owner -and -not $owner.HasExited){$owner.Kill();$owner.WaitForExit()}
 if($result.start){$result.recovery=(& $py "$PSScriptRoot/manager.py" recover|ConvertFrom-Json)}
 $result|ConvertTo-Json -Depth 12|Set-Content "$out/result.json" -Encoding UTF8
}
if(-not $result.passed){exit 1}
