param([string]$TargetDirectory='build/joint-deadline-npu',[ValidateSet(120,128,240,480)][int]$SourceBlockFrames=120,[switch]$ExperimentalDirect48,[switch]$MatureFs,[switch]$DualWorker,[switch]$SharedOutput)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=Join-Path $root $TargetDirectory
$ov=Join-Path $root 'work/npu-python/openvino'
$llvm=Join-Path $root 'work/lsac-toolchain/llvm-mingw-20261006-ucrt-x86_64/bin'
New-Item -ItemType Directory -Force $out|Out-Null
$flags=@();if($ExperimentalDirect48){$flags+='-DNSP_EXPERIMENTAL_DIRECT48'}
if($MatureFs){$flags+='-DNSP_JOINT_MATURE_FS'}
if($DualWorker){$flags+='-DNSP_JOINT_DUAL_WORKER'}
if($SharedOutput){if(-not $DualWorker -or -not $ExperimentalDirect48){throw 'SharedOutput requires direct48 dual-worker mode'};$flags+='-DNSP_VOCAL_SHARED_OUTPUT'}
& "$llvm/clang++.exe" -std=c++20 -O2 -DNSP_VOCAL_OPENVINO "-DNSP_JOINT_BLOCK=$SourceBlockFrames" @flags -DUNICODE -D_UNICODE -I "$ov/include" -I "$PSScriptRoot/vendor" -static-libstdc++ -static-libgcc -municode "$PSScriptRoot/joint_deadline_probe.cpp" "$PSScriptRoot/vendor/StemgenRT/StreamingSampleRateAdapter.cpp" "$ov/libs/openvino_c.lib" -lavrt -o "$out/joint-deadline-probe.exe"
if($LASTEXITCODE){throw 'Joint deadline probe build failed'}
foreach($name in @('openvino_c.dll','openvino.dll','openvino_onnx_frontend.dll','openvino_ir_frontend.dll','openvino_intel_npu_plugin.dll','openvino_intel_npu_compiler.dll','openvino_intel_npu_compiler_loader.dll','openvino_intel_npu_vm_runtime.dll','tbb12.dll','tbbbind_2_5.dll','tbbmalloc.dll','tbbmalloc_proxy.dll')){Copy-Item "$ov/libs/$name" $out -Force}
Copy-Item "$llvm/libunwind.dll" $out -Force
if($SharedOutput){
 & "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -I "$ov/include" -static-libstdc++ -static-libgcc -municode "$PSScriptRoot/shared_model_probe.cpp" "$ov/libs/openvino_c.lib" -o "$out/shared-model-probe.exe"
 if($LASTEXITCODE){throw 'Shared model parity probe build failed'}
}
