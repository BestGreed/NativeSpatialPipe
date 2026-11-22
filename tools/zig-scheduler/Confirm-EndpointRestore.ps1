param(
 [Parameter(Mandatory=$true)][string]$SessionFile,
 [Parameter(Mandatory=$true)][string]$Scheduler,
 [string]$Root=(Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
)
$ErrorActionPreference='Stop'
# Reconcile a transient restore failure only against the original snapshot.
# This never selects a format or fabricates a successful native restore call.
$sessionJson=Get-Content -LiteralPath $SessionFile -Raw
$session=$sessionJson | ConvertFrom-Json
$reportPath=Join-Path $session.outputDirectory 'endpoint.json'
$original=Get-Content -LiteralPath $reportPath -Raw
$report=$original | ConvertFrom-Json
$marker=Get-Content -LiteralPath (Join-Path $session.outputDirectory 'observed/hook-session.json') -Raw | ConvertFrom-Json
function Assert-Exited([int]$TargetPid) {
 if($TargetPid -le 0 -or (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)){throw "Process $TargetPid has not been verified exited"}
}
Assert-Exited $session.orchestratorPid
Assert-Exited $report.hostPid
function Get-VerifiedTarget {
 $sessionHome=Split-Path (Resolve-Path -LiteralPath $SessionFile).Path -Parent
 $proof=& $Scheduler verify-hook-baseline --root $Root --home $sessionHome
 if($LASTEXITCODE){throw 'Current audio host baseline was not verified'}
 $target=$proof | ConvertFrom-Json
 if($target.baselineVerified -ne $true -or $target.pid -le 0 -or $target.created -le 0){throw 'Missing native baseline evidence'}
 $oldTarget=Get-Process -Id $marker.pid -ErrorAction SilentlyContinue
 if($oldTarget){
  if($oldTarget.StartTime.ToUniversalTime().ToFileTimeUtc() -eq [long]$marker.created -and
     ($target.pid -ne $marker.pid -or [long]$target.created -ne [long]$marker.created)){
   throw 'Old audio target is not the verified current host'
  }
 }
 return $target
}
if(-not $report.hookCompleted -or $report.hookExitCode -ne 0){throw 'Native capture did not finish cleanly'}
$before=Get-VerifiedTarget
$formatScript=Join-Path $Root 'modules/pipeline-init/Spatial-Format.ps1'
$query=& "$env:SystemRoot/System32/WindowsPowerShell/v1.0/powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $formatScript -Action query -Endpoint $session.endpoint -Format $report.spatialFormat
if($LASTEXITCODE){throw 'Current endpoint query failed'}
$actual=$query | ConvertFrom-Json
if($actual.selected -ine $report.beforeSelected -or $actual.active -ine $report.beforeActive){throw 'Current endpoint still differs from original snapshot'}
Assert-Exited $session.orchestratorPid
Assert-Exited $report.hostPid
$after=Get-VerifiedTarget
if($before.pid -ne $after.pid -or [long]$before.created -ne [long]$after.created){throw 'Audio host changed during endpoint query'}
$evidence=[ordered]@{checkedUtc=[DateTime]::UtcNow.ToString('o');previousRestoreStatus=$report.restoreStatus;currentSelected=$actual.selected;currentActive=$actual.active;captureProcessesExited=$true;currentHostBaselineVerified=$true;currentTargetPid=$after.pid;currentTargetCreated=$after.created;formatChanged=$false}
$nonce=[guid]::NewGuid().ToString('N')
$backup=$reportPath+'.before-reconfirmation-'+$nonce+'.json'
$report | Add-Member -NotePropertyName restoreReconfirmation -NotePropertyValue $evidence -Force
$report | Add-Member -NotePropertyMembers @{finalSelected=$actual.selected;finalActive=$actual.active;restored=$true} -Force
$temporary=$reportPath+'.reconfirm-'+$nonce+'.tmp'
try {
 [IO.File]::WriteAllText($temporary,($report | ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
 if((Get-Content -LiteralPath $SessionFile -Raw) -cne $sessionJson -or
    (Get-Content -LiteralPath $reportPath -Raw) -cne $original){throw 'Recovery inputs changed during verification'}
 [IO.File]::Replace($temporary,$reportPath,$backup)
} finally {
 if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}
}
$evidence | ConvertTo-Json -Compress
