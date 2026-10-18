param([Parameter(Mandatory=$true)][string]$LlvmBin)
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$env:NSP_LLVM_BIN=(Resolve-Path -LiteralPath $LlvmBin).Path
if(-not(Test-Path (Join-Path $env:NSP_LLVM_BIN 'clang.exe'))){throw 'LLVM clang.exe is missing'}
Push-Location -LiteralPath $root
try {
 & ./tools/endpoint-probe/Build-NativeSend.ps1
 & ./tools/endpoint-probe/Build-NativeCapture.ps1
 & ./tools/endpoint-probe/Build-CaptureScheduler.ps1
 & ./tools/endpoint-probe/Build-NativeHook.ps1 -Managed:$false -TestFixtures
 & ./tools/endpoint-probe/Build-NativeHook.ps1 -Managed -TestFixtures
 & ./tools/telemetry/Build-Meter.ps1
 & ./tools/native-receiver/Build.ps1
 & ./tools/native-receiver/Build.ps1 -TargetDirectory build/native-receiver-scheduler
 & ./tools/roar-bridge/Build.ps1 -LlvmBin $env:NSP_LLVM_BIN
 & ./tools/native-receiver/Build.ps1 -TargetDirectory build/native-receiver-routing
 & ./tools/roar-bridge/Build.ps1 -LlvmBin $env:NSP_LLVM_BIN -TargetDirectory build/telemetry-objects
} finally { Pop-Location }
