param([switch]$Fetch,[switch]$Test,[string]$TargetDirectory='build/native-receiver')
$ErrorActionPreference='Stop'
$project=if($PSScriptRoot){$PSScriptRoot}else{(Resolve-Path 'tools/native-receiver').Path}
$root=(Resolve-Path "$project/../..").Path
$env:CARGO_HOME=Join-Path $root 'work/rust/cargo'
$env:RUSTUP_HOME=Join-Path $root 'work/rust/rustup'
$env:CARGO_TARGET_DIR=Join-Path $root $TargetDirectory
$llvm=if($env:NSP_LLVM_BIN){$env:NSP_LLVM_BIN}else{Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'}
$env:PATH="$llvm;$env:PATH"
$mode=if($Test){'test'}else{'build'}
$cargoArgs=@('+1.97.1-x86_64-pc-windows-gnullvm',$mode,'--release','--manifest-path',"$project/Cargo.toml")
if(-not $Fetch){$cargoArgs+=@('--locked','--offline')}
& "$env:CARGO_HOME/bin/cargo.exe" @cargoArgs
if($LASTEXITCODE){throw 'Native receiver build/test failed'}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination "$env:CARGO_TARGET_DIR/release/libunwind.dll" -Force
