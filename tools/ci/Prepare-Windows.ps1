# Cold bootstrap for a GitHub-hosted Windows runner. No machine audio changes.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
Set-Location -LiteralPath $root
$tools=Join-Path $root 'work/ci-toolchains'
New-Item -ItemType Directory -Force $tools | Out-Null
function Download-Verified([string]$Url,[string]$File,[string]$Sha256) {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $File
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash -ine $Sha256) {
        throw "Download hash mismatch: $Url"
    }
}
Download-Verified 'https://github.com/mstorsjo/llvm-mingw/releases/download/20261006/llvm-mingw-20261006-ucrt-x86_64.zip' "$tools/llvm.zip" '317492c456aa27ee607a5919f1d2d38dcdc1112516a24d0bf4b00d078f52d17a'
Expand-Archive -LiteralPath "$tools/llvm.zip" -DestinationPath $tools
$llvm=Join-Path $tools 'llvm-mingw-20261006-ucrt-x86_64/bin'
Download-Verified 'https://ziglang.org/download/0.13.0/zig-windows-x86_64-0.13.0.zip' "$tools/zig.zip" 'd859994725ef9402381e557c60bb57497215682e355204d754ee3df75ee3c158'
Expand-Archive -LiteralPath "$tools/zig.zip" -DestinationPath $tools
$zig=Join-Path $tools 'zig-windows-x86_64-0.13.0/zig.exe'
$env:NSP_LLVM_BIN=$llvm
$env:NSP_ZIG=$zig
$env:PATH="$llvm;$env:PATH"
"NSP_LLVM_BIN=$llvm" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
"NSP_ZIG=$zig" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
$llvm | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append

Download-Verified 'https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-gnu/rustup-init.exe' "$tools/rustup-init.exe" 'd2845ad914acf5add202100c0a7ddf19ab1c3e52521e12841bafff13888e49bb'
$env:RUSTUP_INIT_SKIP_PATH_CHECK='yes'
& "$tools/rustup-init.exe" -y --no-modify-path --default-host x86_64-pc-windows-gnullvm --default-toolchain 1.97.1
if ($LASTEXITCODE) { throw 'Pinned Rust installation failed' }
$cargo=Join-Path $env:CARGO_HOME 'bin/cargo.exe'
& $cargo --version
if ($LASTEXITCODE) { throw 'Cargo is unavailable' }

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
Checkout-Pinned 'https://github.com/TsudaKageyu/minhook.git' 'work/minhook' 'c3fcafdc10146beb5919319d0683e44e3c30d537'
Checkout-Pinned 'https://github.com/AOMediaCodec/roar.git' 'work/roar' '60c96e566c1dd2838468006b63ada716549f82b2'
foreach ($manifest in @('tools/native-receiver/Cargo.toml','tools/roar-bridge/Cargo.toml')) {
    & $cargo +1.97.1-x86_64-pc-windows-gnullvm fetch --locked --manifest-path $manifest
    if ($LASTEXITCODE) { throw "Locked dependency fetch failed: $manifest" }
}
