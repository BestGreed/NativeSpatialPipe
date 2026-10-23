param([Parameter(Mandatory=$true)][string]$InstanceId)
$ErrorActionPreference='Stop'
$devices=@(Get-PnpDevice -Class Display -PresentOnly|Where-Object InstanceId -eq $InstanceId)
if($devices.Count -ne 1 -or $InstanceId -notlike 'PCI\*'){throw 'An exact present physical display adapter is required'}
& pnputil.exe /restart-device $devices[0].InstanceId
if($LASTEXITCODE){throw 'Display reinitialization failed; do not assume EDID is effective'}
