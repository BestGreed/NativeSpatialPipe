param([Parameter(Mandatory=$true)][uint32]$BrowserPid,[Parameter(Mandatory=$true)][uint32]$HostPid)
$ErrorActionPreference='Stop'
$root=Get-CimInstance Win32_Process -Filter "ProcessId=$BrowserPid"
if(!$root -or $root.Name -ne 'msedge.exe' -or $root.CommandLine -match '--type='){throw 'Invalid Edge root'}
$children=@(Get-CimInstance Win32_Process|Where-Object {$_.Name -eq 'msedge.exe' -and $_.ParentProcessId -eq $BrowserPid -and $_.CommandLine -match 'audio.mojom.AudioService'})
if($children.Count -ne 1){throw 'AudioService absent or ambiguous'}
$hostProcess=Get-Process -Id $HostPid
if($hostProcess.Name -ne 'audiodg'){throw 'Invalid audio host'}
$client=Get-Process -Id $children[0].ProcessId
$dll=$client.Modules|Where-Object ModuleName -eq msedge.dll
if(!$dll){throw 'Missing Edge module'}
@{clientPid=$client.Id;edgeBase=[string]$dll.BaseAddress;browserPid=$BrowserPid;browserStarted=[string]((Get-Process -Id $BrowserPid).StartTime.ToFileTimeUtc());clientStarted=[string]$client.StartTime.ToFileTimeUtc()}|ConvertTo-Json -Compress
