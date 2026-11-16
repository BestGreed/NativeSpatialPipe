param([string]$TargetDirectory='', [switch]$Cpu, [switch]$DirectML, [string]$OpenVino='', [string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin')
$ErrorActionPreference='Stop'
if($Cpu -and $DirectML){throw 'Choose one Joint inference backend'}
if(!$TargetDirectory){$TargetDirectory=if($DirectML){'build/semantic-joint480-directml'}elseif($Cpu){'build/semantic-joint480-cpu'}else{'build/semantic-joint480-live'}}
if(!$OpenVino){$OpenVino=if($Cpu){'work/joint-cpu-deps/openvino'}else{'work/npu-python/openvino'}}
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=Join-Path $root $TargetDirectory
$ov=if([IO.Path]::IsPathRooted($OpenVino)){$OpenVino}else{Join-Path $root $OpenVino}
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
New-Item -ItemType Directory -Force $out|Out-Null
$backend=if($DirectML){@('-DNSP_JOINT_DIRECTML','-I',"$root/work/joint-cpu-deps/dml-sdk",'-ldxgi')}else{
 @('-DNSP_VOCAL_OPENVINO','-I',"$ov/include","$ov/libs/openvino_c.lib",$(if($Cpu){'-DNSP_JOINT_CPU'}else{'-DNSP_VOCAL_SHARED_OUTPUT'}))
}
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -DNSP_JOINT_LIVE -DNSP_JOINT_BLOCK=480 -DNSP_EXPERIMENTAL_DIRECT48 -DNSP_JOINT_MATURE_FS -DNSP_JOINT_DUAL_WORKER -I "$PSScriptRoot/vendor" -shared "$PSScriptRoot/joint_live.cpp" "$PSScriptRoot/vendor/StemgenRT/StreamingSampleRateAdapter.cpp" @backend -lavrt -static-libstdc++ -static-libgcc -o "$out/NSP.Joint480.dll"
if($LASTEXITCODE){throw 'Live joint build failed'}
if($DirectML){
 foreach($name in @('onnxruntime.dll','DirectML.dll','onnxruntime_providers_shared.dll')){
  Copy-Item "$root/work/joint-cpu-deps/dml/onnxruntime/capi/$name" $out -Force
 }
 Copy-Item "$llvm/libunwind.dll" $out -Force
 return
}
$plugins=@('openvino_intel_cpu_plugin.dll')
# Windows may resolve both workers to the OpenVINO runtime loaded by the first
# worker. Its directory must also contain the standby CPU plugin.
if(-not $Cpu){$plugins+=@('openvino_intel_npu_plugin.dll','openvino_intel_npu_compiler.dll','openvino_intel_npu_compiler_loader.dll','openvino_intel_npu_vm_runtime.dll')}
foreach($name in @('openvino_c.dll','openvino.dll','openvino_onnx_frontend.dll','openvino_ir_frontend.dll','tbb12.dll','tbbbind_2_5.dll','tbbmalloc.dll','tbbmalloc_proxy.dll')+$plugins){Copy-Item "$ov/libs/$name" $out -Force}
Copy-Item "$llvm/libunwind.dll" $out -Force
