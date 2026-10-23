param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path;$py=(Get-Command python.exe).Source
$out=[IO.Path]::GetFullPath($OutputDirectory);New-Item -ItemType Directory -Path $out -Force|Out-Null
$defaults=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$root/tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1"|ConvertFrom-Json
$other='{0.0.0.00000000}.{441f7c79-46cd-4823-9304-e2ac70e409f5}'
if($defaults.multimedia -eq $other){throw 'Validation expects a different starting endpoint'}
try{
 & $py "$PSScriptRoot/manager.py" start --endpoint $defaults.multimedia --groups headphones --persist|Set-Content "$out/start.json" -Encoding UTF8
 if($LASTEXITCODE){throw 'Resident start failed'}
 & "$root/work/SetDefaultRenderEndpoint.exe" $other 1|Set-Content "$out/switch.json" -Encoding UTF8
 if($LASTEXITCODE){throw 'Default role change failed'}
 Start-Sleep -Seconds 3
 & $py "$PSScriptRoot/manager.py" status|Set-Content "$out/status-after-change.json" -Encoding UTF8
}finally{
 & "$root/work/SetDefaultRenderEndpoint.exe" $defaults.multimedia 1|Set-Content "$out/restore-role.json" -Encoding UTF8
 & $py "$PSScriptRoot/manager.py" recover|Set-Content "$out/recovery.json" -Encoding UTF8
 Get-Content (Join-Path $env:LOCALAPPDATA 'NativeSpatialPipe/pipeline-init/resident.json') -Raw|Set-Content "$out/resident-final.json" -Encoding UTF8
}
