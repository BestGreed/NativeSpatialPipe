param([string]$TargetDirectory='build/semantic-vocal-npu',[switch]$Diagnostics)
$ErrorActionPreference='Stop';$root=(Resolve-Path "$PSScriptRoot/../..").Path;$out=Join-Path $root $TargetDirectory
$ov=Join-Path $root 'work/npu-python/openvino';$llvm=Join-Path $root 'work/lsac-toolchain/llvm-mingw-20261006-ucrt-x86_64/bin'
New-Item -ItemType Directory -Force $out|Out-Null
$flags=@('-std=c++20','-O2','-DNSP_VOCAL_OPENVINO','-DUNICODE','-D_UNICODE','-I',"$ov/include",'-I',"$PSScriptRoot/vendor",'-static-libstdc++','-static-libgcc')
if($Diagnostics){$flags+='-DNSP_NPU_DIAGNOSTICS'}
& "$llvm/clang++.exe" @flags -shared "$PSScriptRoot/vocal_separator.cpp" "$PSScriptRoot/vendor/StemgenRT/StreamingSampleRateAdapter.cpp" "$ov/libs/openvino_c.lib" -lavrt -o "$out/NSP.VocalSeparator.dll"
if($LASTEXITCODE){throw 'NPU separator build failed'}
$runtime=@('openvino_c.dll','openvino.dll','openvino_onnx_frontend.dll','openvino_ir_frontend.dll','openvino_intel_npu_plugin.dll','openvino_intel_npu_compiler.dll','openvino_intel_npu_compiler_loader.dll','openvino_intel_npu_vm_runtime.dll','tbb12.dll','tbbbind_2_5.dll','tbbmalloc.dll','tbbmalloc_proxy.dll')
foreach($name in $runtime){Copy-Item "$ov/libs/$name" $out -Force};Copy-Item "$llvm/libunwind.dll" $out -Force
& "$llvm/clang++.exe" @flags -municode "$PSScriptRoot/model_probe.cpp" "$ov/libs/openvino_c.lib" -o "$out/model-probe.exe"
if($LASTEXITCODE){throw 'NPU model probe build failed'}
& "$llvm/clang++.exe" @flags -municode "$PSScriptRoot/model_render_probe.cpp" "$ov/libs/openvino_c.lib" -lavrt -o "$out/model-render-probe.exe"
if($LASTEXITCODE){throw 'NPU music probe build failed'}
& "$llvm/clang++.exe" @flags -municode "$PSScriptRoot/stream_probe.cpp" "$PSScriptRoot/vendor/StemgenRT/StreamingSampleRateAdapter.cpp" -o "$out/stream-probe.exe"
if($LASTEXITCODE){throw 'NPU stream probe build failed'}
