param([string]$LlvmBin,[switch]$TestFixtures)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
if(-not $LlvmBin){$LlvmBin=$env:NSP_LLVM_BIN}
if(-not $LlvmBin){$LlvmBin=Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'}
$clang=Join-Path $LlvmBin 'clang.exe'
$mh=Join-Path $root 'work/minhook'
if((git -C $mh rev-parse HEAD) -ne 'c3fcafdc10146beb5919319d0683e44e3c30d537'){throw 'MinHook revision mismatch'}
$out=Join-Path $root 'build/enum-hook'
New-Item -ItemType Directory -Force $out | Out-Null
& $clang -O2 -fms-extensions -shared -Wall -Wextra -Werror -I "$mh/include" "$PSScriptRoot/Enum-Hook.c" "$mh/src/hook.c" "$mh/src/buffer.c" "$mh/src/trampoline.c" "$mh/src/hde/hde64.c" -o "$out/nsp_enum_hook.dll"
if($LASTEXITCODE){throw 'Enumeration hook build failed'}
& $clang -O2 -municode -Wall -Wextra -Werror -DNSP_ENUMERATION_CONTROL "$root/tools/endpoint-probe/Native-Hook-Control.c" -ladvapi32 -o "$out/nsp_enum_control.exe"
if($LASTEXITCODE){throw 'Enumeration controller build failed'}
if($TestFixtures){
 & $clang -O2 -shared -Wall -Wextra -Werror "$PSScriptRoot/Enum-Fixture.c" -o "$out/nsp_enum_fixture.dll"
 if($LASTEXITCODE){throw 'Enumeration fixture build failed'}
 & $clang -O2 -municode -Wall -Wextra -Werror "$PSScriptRoot/Enum-FixtureHost.c" -o "$out/nsp_enum_fixture_host.exe"
 if($LASTEXITCODE){throw 'Enumeration fixture host build failed'}
}
