# Cold bootstrap for a GitHub-hosted Windows runner. No machine audio changes.
param([ValidateSet('All','LLVM','Zig','Rust','Sources')][string]$Stage='All')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
Set-Location -LiteralPath $root
$tools=Join-Path $root 'work/ci-toolchains'
New-Item -ItemType Directory -Force $tools | Out-Null
function Move-Toolchain([string]$Source,[string]$Destination) {
    $allowed=[IO.Path]::GetFullPath((Join-Path $root 'work'))+[IO.Path]::DirectorySeparatorChar
    foreach($candidate in @($Source,$Destination)) {
        if(-not [IO.Path]::GetFullPath($candidate).StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)){throw 'Toolchain move escapes workspace work directory'}
    }
    if(Test-Path -LiteralPath $Destination){throw 'Toolchain destination already exists'}
    Move-Item -LiteralPath $Source -Destination $Destination
}
function Download-Verified([string]$Url,[string]$File,[string]$Sha256) {
    Write-Output "Downloading: $Url"
    & curl.exe --fail --location --silent --show-error --retry 2 --connect-timeout 30 --max-time 300 --output $File $Url
    if ($LASTEXITCODE) { throw "Download failed: $Url" }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash -ine $Sha256) {
        throw "Download hash mismatch: $Url"
    }
    Write-Output "Verified SHA256: $File"
}
if($Stage -in @('All','LLVM')) {
Download-Verified 'https://github.com/mstorsjo/llvm-mingw/releases/download/20261006/llvm-mingw-20261006-ucrt-x86_64.zip' "$tools/llvm.zip" '317492c456aa27ee607a5919f1d2d38dcdc1112516a24d0bf4b00d078f52d17a'
Write-Output 'Extracting LLVM-MinGW with Windows tar'
& tar.exe -xf "$tools/llvm.zip" -C $tools
if ($LASTEXITCODE) { throw 'LLVM extraction failed' }
$llvm=Join-Path $tools 'llvm-mingw-20261006-ucrt-x86_64/bin'
$layout=Join-Path $root 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64'
New-Item -ItemType Directory -Force (Split-Path $layout)|Out-Null
Move-Toolchain (Split-Path $llvm) $layout
$llvm=Join-Path $layout 'bin'
$env:NSP_LLVM_BIN=$llvm
$env:PATH="$llvm;$env:PATH"
"NSP_LLVM_BIN=$llvm" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
$llvm | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
& "$llvm/clang.exe" --version
if ($LASTEXITCODE) { throw 'LLVM clang is unavailable' }
}
if($Stage -in @('All','Zig')) {
Download-Verified 'https://ziglang.org/download/0.13.0/zig-windows-x86_64-0.13.0.zip' "$tools/zig.zip" 'd859994725ef9402381e557c60bb57497215682e355204d754ee3df75ee3c158'
Write-Output 'Extracting Zig with Windows tar'
& tar.exe -xf "$tools/zig.zip" -C $tools
if ($LASTEXITCODE) { throw 'Zig extraction failed' }
$zig=Join-Path $tools 'zig-windows-x86_64-0.13.0/zig.exe'
$layout=Join-Path $root 'work/zig-bootstrap/zig-windows-x86_64-0.13.0'
New-Item -ItemType Directory -Force (Split-Path $layout)|Out-Null
Move-Toolchain (Split-Path $zig) $layout
$zig=Join-Path $layout 'zig.exe'
$env:NSP_ZIG=$zig
"NSP_ZIG=$zig" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
& $zig version
if ($LASTEXITCODE) { throw 'Zig is unavailable' }
}

if($Stage -in @('All','Rust')) {
Download-Verified 'https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-gnu/rustup-init.exe' "$tools/rustup-init.exe" 'd2845ad914acf5add202100c0a7ddf19ab1c3e52521e12841bafff13888e49bb'
$env:RUSTUP_INIT_SKIP_PATH_CHECK='yes'
& "$tools/rustup-init.exe" -y --no-modify-path --profile minimal --default-host x86_64-pc-windows-gnullvm --default-toolchain 1.97.1
if ($LASTEXITCODE) { throw 'Pinned Rust installation failed' }
$cargo=Join-Path $env:CARGO_HOME 'bin/cargo.exe'
& $cargo --version
if ($LASTEXITCODE) { throw 'Cargo is unavailable' }
}

function Checkout-Pinned([string]$Url,[string]$Path,[string]$Revision) {
    New-Item -ItemType Directory -Force $Path | Out-Null
    & git -C $Path init -q
    if ($LASTEXITCODE) { throw 'Dependency init failed' }
    & git -C $Path remote add origin $Url
    if ($LASTEXITCODE) { throw 'Dependency remote failed' }
    & git -C $Path fetch --depth 1 origin $Revision
    if ($LASTEXITCODE) { throw "Dependency fetch failed: $Url" }
    & git -C $Path checkout --detach FETCH_HEAD
    if ($LASTEXITCODE) { throw 'Dependency checkout failed' }
    if ((& git -C $Path rev-parse HEAD).Trim() -ne $Revision) { throw 'Dependency revision mismatch' }
}
if($Stage -in @('All','Sources')) {
Checkout-Pinned 'https://github.com/TsudaKageyu/minhook.git' 'work/minhook' 'c3fcafdc10146beb5919319d0683e44e3c30d537'
Checkout-Pinned 'https://github.com/AOMediaCodec/roar.git' 'work/roar' 'b3051102efaab6a7c44399c9bc9f986a0f822a22'
& git clone --shared --no-checkout work/roar work/roar-product-1.1
if ($LASTEXITCODE) { throw 'ROAR product checkout failed' }
& git -C work/roar-product-1.1 checkout --detach b3051102efaab6a7c44399c9bc9f986a0f822a22
if ($LASTEXITCODE) { throw 'ROAR pin checkout failed' }
& git -C work/roar-product-1.1 apply --ignore-space-change "$root/modules/croksor/patches/roar-1.1-validated.patch"
if ($LASTEXITCODE) { throw 'ROAR product patch failed' }
$cargo=Join-Path $env:CARGO_HOME 'bin/cargo.exe'
foreach ($manifest in @('modules/native-receiver/Cargo.toml','modules/croksor/Cargo.toml','modules/media-foundation/decoder/Cargo.toml')) {
    $features=if($manifest -eq 'modules/croksor/Cargo.toml'){@('--features','aoip-output')}else{@()}
    $registry=if($manifest -eq 'modules/croksor/Cargo.toml'){@('--config',"$root/tools/aoip/cargo-official-index.toml")}else{@()}
    & $cargo +1.97.1-x86_64-pc-windows-gnullvm @registry fetch --locked --target x86_64-pc-windows-gnullvm --manifest-path $manifest @features
    if ($LASTEXITCODE) { throw "Locked dependency fetch failed: $manifest" }
}
}
