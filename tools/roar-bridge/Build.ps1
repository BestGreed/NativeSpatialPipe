param([string]$LlvmBin='work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin',[string]$TargetDirectory='build/roar-native')
$ErrorActionPreference='Stop'
$project=if($PSScriptRoot){$PSScriptRoot}else{(Resolve-Path 'tools/roar-bridge').Path}
$root=(Resolve-Path "$project/../..").Path
$env:CARGO_HOME=Join-Path $root 'work/rust/cargo'
$env:RUSTUP_HOME=Join-Path $root 'work/rust/rustup'
$env:CARGO_TARGET_DIR=Join-Path $root $TargetDirectory
$roar=Join-Path $root 'work/roar'
$patches=@('olr-block-mix.patch','olr-zero-gain.patch','olr-silent-block.patch','olr-preserve-interp.patch','olr-gain-reuse.patch','olr-context-geometry.patch','olr-direct-single.patch','olr-output-layouts.patch')
$head=(& git -C $roar rev-parse HEAD).Trim()
if($head -ne '60c96e566c1dd2838468006b63ada716549f82b2'){throw 'ROAR commit differs from the validated pin'}
# Later patches may change an earlier patch's context. Check the complete
# stack in reverse order on a temporary source copy before per-patch checks.
# The real checkout is never reset or overwritten by this verification.
$stackApplied=$false
$verificationRoot=Join-Path $root ('build/roar-patch-check-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null
try {
 & git -C $verificationRoot init -q
 if($LASTEXITCODE -ne 0){throw 'Cannot initialize isolated patch verification'}
 Copy-Item -LiteralPath (Join-Path $roar 'src') -Destination $verificationRoot -Recurse
 $stackApplied=$true
 for($patchIndex=$patches.Count-1;$patchIndex -ge 0;$patchIndex--){
  $patch=Join-Path $project ('patches/'+$patches[$patchIndex])
  $savedErrorAction=$ErrorActionPreference
  $ErrorActionPreference='Continue'
  & git -C $verificationRoot apply --reverse $patch 2>$null
  $reverseSucceeded=$LASTEXITCODE -eq 0
  $ErrorActionPreference=$savedErrorAction
  if(-not $reverseSucceeded){$stackApplied=$false;break}
 }
} finally {
 $resolvedVerification=[IO.Path]::GetFullPath($verificationRoot)
 $allowedBuild=[IO.Path]::GetFullPath((Join-Path $root 'build'))+[IO.Path]::DirectorySeparatorChar
 if(-not $resolvedVerification.StartsWith($allowedBuild,[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid patch verification cleanup path'}
 Remove-Item -LiteralPath $resolvedVerification -Recurse -Force
}
if(-not $stackApplied){
foreach($patchName in $patches){
 $patch=Join-Path $project ('patches/'+$patchName)
 # A failed reverse check means a fresh patch, not a PowerShell failure.
 $savedErrorAction=$ErrorActionPreference
 $ErrorActionPreference='Continue'
 & git -C $roar apply --reverse --check $patch 2>$null
 $alreadyApplied=$LASTEXITCODE -eq 0
 $ErrorActionPreference=$savedErrorAction
 if(-not $alreadyApplied){
  & git -C $roar apply --check $patch
  if($LASTEXITCODE -ne 0){throw "ROAR patch conflicts: $patchName; inspect local source"}
  & git -C $roar apply $patch
  if($LASTEXITCODE -ne 0){throw "ROAR patch failed: $patchName"}
 }
}
}
$llvm=if([IO.Path]::IsPathRooted($LlvmBin)){$LlvmBin}else{Join-Path $root $LlvmBin}
if(-not(Test-Path "$llvm/clang.exe")){throw 'Portable LLVM/MinGW is missing'}
$env:PATH="$llvm;$env:PATH"
& "$env:CARGO_HOME/bin/cargo.exe" +1.97.1-x86_64-pc-windows-gnullvm build --release --locked --offline --manifest-path "$project/Cargo.toml"
if($LASTEXITCODE){throw 'ROAR bridge build failed'}
Copy-Item -LiteralPath "$llvm/libunwind.dll" -Destination "$env:CARGO_TARGET_DIR/release/libunwind.dll" -Force
