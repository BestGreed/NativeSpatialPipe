param([Parameter(Mandatory=$true)][string]$Python,[switch]$MediaFoundation,[switch]$PcmCapture,[switch]$BrowserIdentity,[switch]$Wasapi,[switch]$WasapiUpmix,[switch]$SkipEnumHookBuild)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/..").Path
if(-not(Test-Path -LiteralPath $Python -PathType Leaf)){throw 'An explicit Python runtime is required'}
function Build-Step([string]$Script,[string[]]$Options=@()) {
 & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root $Script) @Options
 if($LASTEXITCODE){throw "Build failed: $Script"}
}
Build-Step 'modules/native-capture/Build-NativeHook.ps1' @('-Managed','-TestFixtures')
Build-Step 'modules/native-capture/Build-NativeCapture.ps1'
Build-Step 'modules/native-capture/Build-NativeSend.ps1'
Build-Step 'modules/native-capture/Build-CaptureScheduler.ps1'
Build-Step 'modules/native-receiver/Build.ps1' @('-TargetDirectory','build/nsp-product/receiver')
if($SkipEnumHookBuild){
 Write-Warning 'Reusing existing enumeration hook artifacts.'
}else{Build-Step 'modules/pipeline-init/Build-EnumHook.ps1'}
Build-Step 'modules/telemetry/Build-Meter.ps1'
Build-Step 'modules/audio-backends/Build.ps1'
if($MediaFoundation){Build-Step 'modules/media-foundation/Build.ps1' @('-Python',$Python)}
if($PcmCapture -or $BrowserIdentity -or $WasapiUpmix){Build-Step 'modules/pcm-capture/Build.ps1'}
if($BrowserIdentity){Build-Step 'modules/browser-identity/Build-Backend.ps1'}
if($Wasapi -or $WasapiUpmix){Build-Step 'modules/audio-backends/Build-Wasapi.ps1'}
if($WasapiUpmix){
 Build-Step 'modules/freesurround/Build.ps1' @('-TargetDirectory','build/freesurround-mature480','-Window','480','-HalfHop')
 Build-Step 'modules/semantic-separator/Build-JointLive.ps1' @('-Cpu')
}
Build-Step 'modules/croksor/Build.ps1' @('-TargetDirectory','build/nsp-product/runtime','-Features','aoip-output')
$frontend=Join-Path $root 'build/nsp-product/web'
New-Item -ItemType Directory -Force $frontend | Out-Null
Copy-Item -Path "$root/modules/visualization-frontend/*" -Destination $frontend -Recurse -Force
$readobj=Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin/llvm-readobj.exe'
function Assert-DllExports([string]$RelativePath,[string[]]$Names) {
 $dll=Join-Path $root $RelativePath
 $exports=& $readobj --coff-exports $dll
 if($LASTEXITCODE){throw "Cannot verify DLL exports: $dll"}
 foreach($name in $Names){
  if(-not ($exports -match ('^\s*Name:\s*'+[regex]::Escape($name)+'\s*$'))){throw "Missing DLL export ${name}: $dll"}
 }
}
Assert-DllExports 'build/nsp-product/receiver/release/nsp_native_receiver.dll' @('nsp_receiver_start_priority_leased','nsp_receiver_route_snapshot','nsp_receiver_route_status')
Assert-DllExports 'build/nsp-product/runtime/release/nsp_croksor.dll' @('nsp_pipeline_submit_input_leased','nsp_pipeline_create_unified_v3')
if($PcmCapture -or $BrowserIdentity -or $WasapiUpmix){
 Assert-DllExports 'build/pcm-capture-v1/NSP.PcmCapture.dll' @('nsp_pcm_capture_open_multi','nsp_pcm_capture_source_mask','nsp_pcm_capture_activate_source')
 Assert-DllExports 'build/pcm-capture-v1/NSP.PcmIngress.dll' @('nsp_pcm_ingress_open_leased')
}
Build-Step 'modules/zig-scheduler/Build.ps1' @('-TargetDirectory','build/nsp-product','-SkipTests')
$ini=Join-Path $root 'build/fsa-integration/final-spatial-audio.ini'
if(-not(Test-Path -LiteralPath $ini)){
 New-Item -ItemType Directory -Force (Split-Path $ini)|Out-Null
 Copy-Item -LiteralPath "$root/modules/fsa-integration/native/final-spatial-audio.example.ini" -Destination $ini
}
$fsaOptions=@('-FsaOnly');if($MediaFoundation){$fsaOptions+='-MediaFoundation'}
Build-Step 'modules/fsa-integration/Build-Integration.ps1' $fsaOptions
Build-Step 'modules/fsa-integration/Configure-Scheduler.ps1' @('-Configuration',$ini,'-TelemetryPython',$Python,'-ControlExecutable',"$root/build/nsp-product/nsp-scheduler.exe")
Write-Output 'Product build complete; no audio session started and no installed user configuration deployed.'
