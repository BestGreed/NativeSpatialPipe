param([string]$TargetDirectory='build/browser-identity-v1',[string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin',[string]$Python='',[string]$Table='modules/native-capture/profiles/capture-profiles.json')
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$out=if([IO.Path]::IsPathRooted($TargetDirectory)){$TargetDirectory}else{Join-Path $root $TargetDirectory}
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
$table=if([IO.Path]::IsPathRooted($Table)){$Table}else{Join-Path $root $Table}
# binding_core.h shares the generated PCM profile table with the capture module.
if(-not $Python){$command=Get-Command python -ErrorAction SilentlyContinue;if($command){$Python=$command.Source}}
if(-not $Python -or -not(Test-Path -LiteralPath $Python)){throw 'Python is required to generate profile.generated.h; pass -Python'}
& $Python (Join-Path $root 'tools/capture-profile/gen_pcm_header.py') --table $table --out "$root/modules/pcm-capture/profile.generated.h"
if($LASTEXITCODE){throw 'PCM profile generation failed'}
New-Item -ItemType Directory -Force $out|Out-Null
& "$llvm/clang++.exe" -std=c++20 -O2 -DUNICODE -D_UNICODE -D_WIN32_WINNT=0x0A00 -municode "$PSScriptRoot/backend.cpp" -lole32 -luuid -lbcrypt -lavrt -lpsapi -ladvapi32 -lws2_32 -static-libstdc++ -static-libgcc -o "$out/NSP.BrowserIdentity.exe"
if($LASTEXITCODE){throw 'Browser identity backend build failed'}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination $out -Force
