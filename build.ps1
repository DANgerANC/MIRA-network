[CmdletBinding()]
param([switch]$DebugBuild)

$ErrorActionPreference = 'Stop'
$miraCargo = Join-Path $PSScriptRoot '.tools\cargo\bin\cargo.exe'
if (-not (Test-Path -LiteralPath $miraCargo)) {
    throw 'Local Rust toolchain is missing from .tools. Install Rust or use cargo build --release with your system toolchain.'
}

$miraPreviousCargoHome = $env:CARGO_HOME
$miraPreviousRustupHome = $env:RUSTUP_HOME
$miraPreviousPath = $env:PATH
try {
    $env:CARGO_HOME = Join-Path $PSScriptRoot '.tools\cargo'
    $env:RUSTUP_HOME = Join-Path $PSScriptRoot '.tools\rustup'
    $env:PATH = (Join-Path $PSScriptRoot '.tools\cargo\bin') + ';' + (Join-Path $PSScriptRoot '.tools\mingw64\bin') + ';' + $env:PATH
    $miraBuildArguments = @('build', '--locked', '--manifest-path', (Join-Path $PSScriptRoot 'Cargo.toml'))
    if (-not $DebugBuild) { $miraBuildArguments += '--release' }
    & $miraCargo @miraBuildArguments
    if ($LASTEXITCODE -ne 0) { throw "Cargo build failed with exit code $LASTEXITCODE" }
} finally {
    $env:CARGO_HOME = $miraPreviousCargoHome
    $env:RUSTUP_HOME = $miraPreviousRustupHome
    $env:PATH = $miraPreviousPath
}
