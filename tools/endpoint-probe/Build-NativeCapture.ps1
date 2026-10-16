param([switch]$TestFixtures)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$clang=if($env:NSP_LLVM_BIN){Join-Path $env:NSP_LLVM_BIN 'clang.exe'}else{Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin/clang.exe'}
$out=Join-Path $root 'build/native-capture'
New-Item -ItemType Directory -Force -Path $out|Out-Null
& $clang -O2 -fms-extensions -shared -Wall -Wextra "$PSScriptRoot/Native-Capture.c" -o "$out/nsp_native_capture.dll"
if($LASTEXITCODE){throw 'Native capture build failed'}
if($TestFixtures){
 foreach($delayed in @($false,$true)){
  $name=if($delayed){'capture-fixture-delay'}else{'capture-fixture'}
  $fixture=Join-Path $root ('build/'+$name)
  New-Item -ItemType Directory -Force -Path $fixture|Out-Null
  $flags=@('-O2','-shared');if($delayed){$flags+='-DNSP_TEST_BOUNDARY_DELAY'}
  & $clang @flags "$PSScriptRoot/Capture-Fixture.c" -o "$fixture/nsp_fixture.dll"
  if($LASTEXITCODE){throw 'Capture test fixture build failed'}
 }
}
