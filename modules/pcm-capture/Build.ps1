param([string]$TargetDirectory='build/pcm-capture-v1',[string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin',[string]$Python='',[string]$Table='modules/native-capture/profiles/capture-profiles.json',[switch]$Test)
$ErrorActionPreference='Stop';$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=if([IO.Path]::IsPathRooted($TargetDirectory)){$TargetDirectory}else{Join-Path $root $TargetDirectory}
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
$table=if([IO.Path]::IsPathRooted($Table)){$Table}else{Join-Path $root $Table}
if(-not $Python){$command=Get-Command python -ErrorAction SilentlyContinue;if($command){$Python=$command.Source}}
if(-not $Python -or -not(Test-Path -LiteralPath $Python)){throw 'Python is required to generate profile.generated.h; pass -Python'}
& $Python (Join-Path $root 'tools/capture-profile/gen_pcm_header.py') --table $table --out "$PSScriptRoot/profile.generated.h"
if($LASTEXITCODE){throw 'PCM profile generation failed'}
New-Item -ItemType Directory -Force $out|Out-Null
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -D_WIN32_WINNT=0x0A00 -shared "$PSScriptRoot/pcm_capture.cpp" -lole32 -luuid -lavrt -static-libstdc++ -static-libgcc -o "$out/NSP.PcmCapture.dll"
if($LASTEXITCODE){throw 'PCM capture build failed'}
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -shared "$PSScriptRoot/pcm_ingress.cpp" -lavrt -static-libstdc++ -static-libgcc -o "$out/NSP.PcmIngress.dll"
if($LASTEXITCODE){throw 'PCM ingress build failed'}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination $out -Force
if($Test){
 & "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -D_WIN32_WINNT=0x0A00 "$PSScriptRoot/test_multi_capture.cpp" -lole32 -luuid -lavrt -static-libstdc++ -static-libgcc -o "$out/test_multi_capture.exe"
 if($LASTEXITCODE){throw 'PCM multi capture test build failed'}
 & "$out/test_multi_capture.exe"
 if($LASTEXITCODE){throw 'PCM multi capture test failed'}
 & "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE "$PSScriptRoot/profile_select_test.cpp" -static-libstdc++ -static-libgcc -o "$out/profile-select-test.exe"
 if($LASTEXITCODE){throw 'PCM profile test build failed'}
 & "$out/profile-select-test.exe"
 if($LASTEXITCODE){throw 'PCM profile test failed'}
}
