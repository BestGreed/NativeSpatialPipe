param([string]$TargetDirectory='build/freesurround-v1',[ValidateSet(240,256,480,510,512,1024)][int]$Window=1024,[switch]$HalfHop,[string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin')
$ErrorActionPreference='Stop';$root=(Resolve-Path "$PSScriptRoot/../..").Path;$out=Join-Path $root $TargetDirectory
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
New-Item -ItemType Directory -Force $out|Out-Null
& "$llvm/clang.exe" -O2 -c "$PSScriptRoot/vendor/kissfft/kiss_fft.c" -o "$out/fft.o"
if($LASTEXITCODE){throw 'FFT build failed'}
& "$llvm/clang.exe" -O2 -c "$PSScriptRoot/vendor/kissfft/kiss_fftr.c" -o "$out/fftr.o"
if($LASTEXITCODE){throw 'Real FFT build failed'}
$flags=@('-DNSP_FS_WINDOW='+$Window)
if($HalfHop){$flags+='-DNSP_FS_HALF_HOP'}
& "$llvm/clang++.exe" -std=c++20 -O2 -DTARGET_WINDOWS @flags -shared "$PSScriptRoot/freesurround.cpp" "$PSScriptRoot/vendor/FreeSurroundDecoder.cpp" "$PSScriptRoot/vendor/ChannelMaps.cpp" "$out/fft.o" "$out/fftr.o" -static-libstdc++ -static-libgcc -o "$out/NSP.FreeSurround.dll"
if($LASTEXITCODE){throw 'FreeSurround build failed'}
Copy-Item "$llvm/libunwind.dll" $out -Force
