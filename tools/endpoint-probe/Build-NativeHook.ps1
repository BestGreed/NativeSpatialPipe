param([switch]$TestFixtures,[switch]$Managed=$true)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$clang=Join-Path $env:NSP_LLVM_BIN 'clang.exe'
$mh=Join-Path $root 'work/minhook'
$out=Join-Path $root 'build/native-hook'
$hookName='nsp_native_hook.dll'
if($Managed){$out=Join-Path $root 'build/native-hook-managed-v2';$hookName='nsp_managed_hook_v2.dll'}
if((git -C $mh rev-parse HEAD) -ne 'c3fcafdc10146beb5919319d0683e44e3c30d537'){throw 'MinHook revision mismatch'}
New-Item -ItemType Directory -Force -Path $out|Out-Null
& $clang -O2 -fms-extensions -shared -Wall -Wextra -I "$mh/include" "$PSScriptRoot/Native-Hook.c" "$mh/src/hook.c" "$mh/src/buffer.c" "$mh/src/trampoline.c" "$mh/src/hde/hde64.c" -o "$out/$hookName"
if($LASTEXITCODE){throw 'Hook build failed'}
& $clang -O2 -municode -Wall -Wextra "$PSScriptRoot/Native-Hook-Control.c" -ladvapi32 -o "$out/nsp_hook_control.exe"
if($LASTEXITCODE){throw 'Controller build failed'}
& $clang -O2 -municode -Wall -Wextra "$PSScriptRoot/Native-FixtureHost.c" -o "$out/nsp_fixture_host.exe"
if($LASTEXITCODE){throw 'Fixture host build failed'}

if($TestFixtures){
 foreach($kind in @('capture-fixture','capture-fixture-delay','capture-fixture-abi')){
  $fixture=Join-Path $root ('build/'+$kind)
  New-Item -ItemType Directory -Force -Path $fixture|Out-Null
  $flags=@('-O2','-shared')
  if($kind -eq 'capture-fixture-delay'){$flags+='-DNSP_TEST_BOUNDARY_DELAY'}
  if($kind -eq 'capture-fixture-abi'){$flags+='-DNSP_TEST_HOOK_ABI'}
  & $clang @flags "$PSScriptRoot/Capture-Fixture.c" -o "$fixture/nsp_fixture.dll"
  if($LASTEXITCODE){throw 'Native hook fixture build failed'}
 }
}
