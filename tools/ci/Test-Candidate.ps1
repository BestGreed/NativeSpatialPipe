param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
Set-Location -LiteralPath $root
New-Item -ItemType Directory -Force build/ci-logs | Out-Null
New-Item -ItemType Directory -Force build/zig-scheduler | Out-Null
Start-Transcript build/ci-logs/contracts.txt
try {
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File modules/zig-scheduler/Build.ps1 -TargetDirectory build/nsp-product
 if($LASTEXITCODE){throw 'Zig unit tests failed'}
 $env:NSP_SCHEDULER_TEST_EXE=Join-Path $root 'build/nsp-product/nsp-scheduler.exe'
 $env:NSP_RENDER_BRIDGE_TEST_DLL=Join-Path $root 'build/nsp-product/runtime/release/nsp_croksor.dll'
 & $Python modules/zig-scheduler/Test-Scheduler.py
 if($LASTEXITCODE){throw 'Scheduler lifecycle contracts failed'}
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/ci/Test-WebHostUser.ps1 -Python $Python -HostExecutable $env:NSP_SCHEDULER_TEST_EXE
 if($LASTEXITCODE){throw 'Web host contracts failed'}
 foreach($directory in @('modules/pipeline-init','modules/telemetry')){
  & $Python -m unittest discover -s $directory -p 'test_*.py'
  if($LASTEXITCODE){throw "Python contracts failed: $directory"}
 }
 & $Python tools/capture-profile/test_extract.py
 if($LASTEXITCODE){throw 'Synthetic capture-profile extraction failed'}
 Push-Location modules/visualization-frontend
 try {
  foreach($script in @('test-data.js','test-configuration.js','test-configuration-page.js','test-i18n.js')){
   & node $script
   if($LASTEXITCODE){throw "Frontend contract failed: $script"}
  }
 } finally { Pop-Location }
 Write-Output 'Public candidate source/build/isolated checks completed; no device playback or package acceptance claimed.'
} finally { Stop-Transcript }
