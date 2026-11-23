[CmdletBinding()]
param(
    [string]$TargetDirectory = 'build/nsp-product/runtime',
    [string]$LlvmBin = 'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$toolchain = '1.97.1-x86_64-pc-windows-gnullvm'

function Resolve-RepoPath([string]$TargetPath) {
    if ([IO.Path]::IsPathRooted($TargetPath)) { return $TargetPath }
    return Join-Path $repo $TargetPath
}

$cargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $repo 'work/rust/cargo' }
$rustupHome = if ($env:RUSTUP_HOME) { $env:RUSTUP_HOME } else { Join-Path $repo 'work/rust/rustup' }
$cargo = Join-Path $cargoHome 'bin/cargo.exe'
$llvm = Resolve-RepoPath $LlvmBin
$linker = Join-Path $llvm 'x86_64-w64-mingw32-clang.exe'
$target = Resolve-RepoPath $TargetDirectory
foreach ($dependency in @($cargo, $linker, (Join-Path $rustupHome ('toolchains/' + $toolchain + '/bin/rustc.exe')))) {
    if (!(Test-Path -LiteralPath $dependency -PathType Leaf)) { throw "Missing required tool: $dependency" }
}

$environment = @{
    CARGO_HOME = $cargoHome
    RUSTUP_HOME = $rustupHome
    CARGO_TARGET_DIR = $target
    CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_LINKER = $linker
    PATH = "$llvm;$env:PATH"
}
$previous = @{}
try {
    foreach ($key in $environment.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key)
        [Environment]::SetEnvironmentVariable($key, $environment[$key])
    }
    & $cargo "+$toolchain" build --release --locked --offline --manifest-path (Join-Path $PSScriptRoot 'Cargo.toml')
    if ($LASTEXITCODE -ne 0) { throw "AES67 cargo build failed ($LASTEXITCODE)" }
    $dll = Join-Path $target 'release/nsp_aes67.dll'
    if (!(Test-Path -LiteralPath $dll -PathType Leaf)) { throw "Expected AES67 DLL at $dll" }
    # LLVM-MinGW Rust targets may depend on the shared unwind runtime. Stage the
    # same file as the other native Rust modules so standalone loads also work.
    $unwind = Join-Path $llvm 'libunwind.dll'
    if (Test-Path -LiteralPath $unwind -PathType Leaf) {
        Copy-Item -LiteralPath $unwind -Destination (Join-Path $target 'release/libunwind.dll') -Force
    }
    Write-Output $dll
} finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key])
    }
}
