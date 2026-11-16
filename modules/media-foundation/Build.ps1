param(
 [switch]$Tests,[string]$Python="python.exe",
 [string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin',
 [string]$RustBin='work/rust/rustup/toolchains/1.97.1-x86_64-pc-windows-gnullvm/bin',
 [string]$CargoHome='work/rust/cargo',
 [string]$MinHookDirectory='work/minhook',
 [ValidateSet('x86_64-pc-windows-gnullvm','x86_64-pc-windows-msvc')][string]$RustTarget='x86_64-pc-windows-gnullvm',
 [string]$TargetDirectory='build/media-foundation'
)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
Push-Location $root
try {
 $llvm=(Resolve-Path -LiteralPath $LlvmBin).Path
 $rust=(Resolve-Path -LiteralPath $RustBin).Path
 $env:CARGO_HOME=(Resolve-Path -LiteralPath $CargoHome).Path
 $out=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TargetDirectory)
 $decoder=Join-Path $out 'decoder'
 $env:HARLETTY_BUILD_OMNIPHONY_COMMIT='nsp-vendored-bridge-api-0.6.0'
 $env:PATH="$rust;$llvm;"+$env:PATH
 $env:CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_LINKER="$llvm/clang.exe"
 & "$rust/cargo.exe" build --locked --offline --release --target $RustTarget --manifest-path modules/media-foundation/decoder/Cargo.toml --target-dir $decoder
 if($LASTEXITCODE){throw 'Source decoder build failed'}
 & $Python -B tools/mat-capture/GenerateNspMft.py "$llvm/../include/mftransform.h"
 if($LASTEXITCODE){throw 'MFT generation failed'}
 New-Item -ItemType Directory -Force $out | Out-Null
 Copy-Item -LiteralPath "$decoder/$RustTarget/release/nsp_mf_decoder.dll" -Destination $out
 Copy-Item "$llvm/libunwind.dll" $out
 & "$llvm/clang++.exe" -std=c++20 -shared -O2 -static-libstdc++ -static-libgcc -Wno-dll-attribute-on-redeclaration modules/media-foundation/src/mft.cpp -o "$out/NspDolbyMft.dll" -lmf -lmfplat -lmfuuid -lole32 -ladvapi32 -luuid -lavrt
 if($LASTEXITCODE){throw 'MFT build failed'}
 $mh=(Resolve-Path -LiteralPath $MinHookDirectory).Path
 if((git -C $mh rev-parse HEAD) -ne 'c3fcafdc10146beb5919319d0683e44e3c30d537'){throw 'MinHook revision mismatch'}
 & "$llvm/clang++.exe" -std=c++20 -shared -O2 -static-libstdc++ -static-libgcc -Wno-deprecated -I "$mh/include" modules/media-foundation/src/route.cpp "$mh/src/hook.c" "$mh/src/buffer.c" "$mh/src/trampoline.c" "$mh/src/hde/hde64.c" -o "$out/NspMftRoute.dll" -lmf -lmfplat -lmfuuid -lole32 -ladvapi32 -luuid -lavrt
 if($LASTEXITCODE){throw 'Route DLL build failed'}
 & "$llvm/clang++.exe" -std=c++20 -O2 -municode -static-libstdc++ -static-libgcc modules/media-foundation/src/route_control.cpp -o "$out/NspMftRouteControl.exe" -ladvapi32 -lole32
 if($LASTEXITCODE){throw 'Route controller build failed'}
 if($Tests){
  & "$llvm/clang++.exe" -std=c++20 -O2 -municode -static-libstdc++ -static-libgcc modules/media-foundation/src/test_host.cpp -o "$out/NspMftTest.exe" -lmf -lmfplat -lmfuuid -lole32 -ladvapi32 -luuid -lavrt
  if($LASTEXITCODE){throw 'MFT test host build failed'}
  & "$llvm/clang++.exe" -std=c++20 -O2 -municode -static-libstdc++ -static-libgcc modules/media-foundation/src/spatial_test.cpp -o "$out/NspSpatialTest.exe" -lmf -lmfplat -lmfuuid -lole32 -ladvapi32 -luuid -lavrt
  if($LASTEXITCODE){throw 'Spatial test host build failed'}
  & "$llvm/clang++.exe" -std=c++20 -O2 -municode -static-libstdc++ -static-libgcc modules/media-foundation/src/mixer_test.cpp -o "$out/NspMixerTest.exe" -lole32
  if($LASTEXITCODE){throw 'Mixer regression host build failed'}
  & "$llvm/clang++.exe" -std=c++20 -O2 -static-libstdc++ -static-libgcc modules/media-foundation/src/queue_test.cpp -o "$out/NspQueueTest.exe" -lmf -lmfplat -lmfuuid -lole32 -lavrt
  if($LASTEXITCODE){throw 'Queue ownership regression host build failed'}
 }
} finally { Pop-Location }
