param([string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin',[string]$TargetDirectory='build/roar-native',[string]$Features='')
$ErrorActionPreference='Stop'
$project=if($PSScriptRoot){$PSScriptRoot}else{(Resolve-Path 'modules/croksor').Path}
$root=(Resolve-Path "$project/../..").Path
$env:CARGO_HOME=Join-Path $root 'work/rust/cargo'
$env:RUSTUP_HOME=Join-Path $root 'work/rust/rustup'
$env:CARGO_TARGET_DIR=Join-Path $root $TargetDirectory
$pin='b3051102efaab6a7c44399c9bc9f986a0f822a22'
$roar=Join-Path $root 'work/roar-product-1.1'
$patches=@('modules/croksor/patches/roar-1.1-validated.patch')
if(!(Test-Path -LiteralPath $roar)){
 & git -c core.autocrlf=false clone --shared --no-checkout (Join-Path $root 'work/roar') $roar
 if($LASTEXITCODE){throw 'Cannot create ROAR 1.1 product checkout'}
 & git -C $roar checkout --detach $pin
 if($LASTEXITCODE){throw 'Cannot check out the pinned ROAR 1.1 revision'}
}
if((& git -C $roar rev-parse HEAD) -ne $pin){throw 'ROAR product checkout differs from the validated pin'}
# Validate the complete patch stack on a disposable source copy.
$verify=Join-Path $root ('build/roar-patch-check-'+[Guid]::NewGuid().ToString('N'))
$allowed=(Join-Path $root 'build')+[IO.Path]::DirectorySeparatorChar
if(![IO.Path]::GetFullPath($verify).StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid verification path'}
New-Item -ItemType Directory -Path $verify -Force | Out-Null
try {
 & git -C $verify init -q
 if($LASTEXITCODE){throw 'Cannot initialize isolated patch verification'}
 Copy-Item -LiteralPath (Join-Path $roar 'src'),(Join-Path $roar 'Cargo.toml') -Destination $verify -Recurse
 $patched=$true
 for($i=$patches.Count-1;$i-ge 0;$i--){
  $saved=$ErrorActionPreference;$ErrorActionPreference='Continue'
  & git -C $verify apply --ignore-space-change --reverse (Join-Path $root $patches[$i]) 2>$null
  $ok=$LASTEXITCODE -eq 0;$ErrorActionPreference=$saved
  if(!$ok){$patched=$false;break}
 }
 if(!$patched){
  if(& git -C $roar status --porcelain){throw 'ROAR product checkout has unexpected changes or an incomplete patch stack'}
  foreach($patch in $patches){
   & git -C $roar apply --ignore-space-change (Join-Path $root $patch)
   if($LASTEXITCODE){throw "Failed to apply ROAR patch $patch"}
  }
 }
}finally{
 Remove-Item -LiteralPath $verify -Recurse -Force
}
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
if(-not(Test-Path "$llvm/clang.exe")){throw 'Portable LLVM/MinGW is missing'}
$env:PATH="$llvm;$env:PATH"
$cargoOptions=@()
if($Features){$cargoOptions+=@('--features',$Features)}
& "$env:CARGO_HOME/bin/cargo.exe" +1.97.1-x86_64-pc-windows-gnullvm --config "$root/tools/aoip/cargo-official-index.toml" build --release --locked --offline --manifest-path "$project/Cargo.toml" @cargoOptions
if($LASTEXITCODE){throw 'ROAR bridge build failed'}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination "$env:CARGO_TARGET_DIR/release/libunwind.dll" -Force
