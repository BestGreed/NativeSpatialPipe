param([string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin',[string]$TargetDirectory='build/roar-native')
$ErrorActionPreference='Stop'
$project=if($PSScriptRoot){$PSScriptRoot}else{(Resolve-Path 'tools/roar-bridge').Path}
$root=(Resolve-Path "$project/../..").Path
$env:CARGO_HOME=Join-Path $root 'work/rust/cargo'
$env:RUSTUP_HOME=Join-Path $root 'work/rust/rustup'
$env:CARGO_TARGET_DIR=Join-Path $root $TargetDirectory
$roar=Join-Path $root 'work/roar'
$patch=Join-Path $project 'patches/olr-block-mix.patch'
$head=(& git -C $roar rev-parse HEAD).Trim()
if($head -ne '60c96e566c1dd2838468006b63ada716549f82b2'){throw 'ROAR commit differs from the validated pin'}
& git -C $roar apply --reverse --check $patch 2>$null
if($LASTEXITCODE -ne 0){
 & git -C $roar apply --check $patch
 if($LASTEXITCODE -ne 0){throw 'ROAR patch conflicts; inspect local source'}
 & git -C $roar apply $patch
 if($LASTEXITCODE -ne 0){throw 'ROAR patch failed'}
}
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
if(-not(Test-Path "$llvm/clang.exe")){throw 'Portable LLVM/MinGW is missing'}
$env:PATH="$llvm;$env:PATH"
& "$env:CARGO_HOME/bin/cargo.exe" +1.97.1-x86_64-pc-windows-gnullvm build --release --locked --offline --manifest-path "$project/Cargo.toml"
if($LASTEXITCODE){throw 'ROAR bridge build failed'}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination "$env:CARGO_TARGET_DIR/release/libunwind.dll" -Force
