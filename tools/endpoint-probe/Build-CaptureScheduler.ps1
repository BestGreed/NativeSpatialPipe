$ErrorActionPreference='Stop'
$root=(Get-Location).Path
$clang=if($env:NSP_LLVM_BIN){Join-Path $env:NSP_LLVM_BIN 'clang.exe'}else{Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin/clang.exe'}
foreach($mode in @('normal','mmcss')){
 $out=Join-Path $root ('build/capture-scheduler-'+$mode)
 New-Item -ItemType Directory -Force -Path $out|Out-Null
 $flags=@('-O2','-fms-extensions','-shared','-Wall','-Wextra')
 if($mode -eq 'mmcss'){$flags+='-DNSP_WORKER_MMCSS'}
 & $clang @flags tools/endpoint-probe/Native-Capture.c -lavrt -o "$out/nsp_native_capture.dll"
 if($LASTEXITCODE){throw 'Capture scheduler build failed'}
}
