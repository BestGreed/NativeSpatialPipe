param([Parameter(Mandatory=$true)][string]$MonitorInstance)
$ErrorActionPreference='Stop'
if($MonitorInstance -notmatch '^[^\\/]+\\[^\\/]+$'){throw 'Invalid monitor instance'}
$parent=(Get-PnpDeviceProperty -InstanceId ("DISPLAY\"+$MonitorInstance) -KeyName DEVPKEY_Device_Parent).Data
$adapters=@(Get-PnpDevice -Class Display -PresentOnly | Where-Object InstanceId -eq $parent)
if($adapters.Count -ne 1 -or $parent -notlike 'PCI\*'){throw 'Monitor is not bound to an exact present physical display adapter'}
[pscustomobject]@{monitor=$MonitorInstance;adapter=$parent}|ConvertTo-Json -Compress
