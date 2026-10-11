[CmdletBinding()]
param(
    [string]$NdkRoot = (Join-Path $PSScriptRoot '.tools\android\android-ndk-r30'),
    [ValidateRange(23, 100)][int]$ApiLevel = 28
)

$ErrorActionPreference = 'Stop'
$miraCargo = Join-Path $PSScriptRoot '.tools\cargo\bin\cargo.exe'
$miraLinker = Join-Path $NdkRoot "toolchains\llvm\prebuilt\windows-x86_64\bin\aarch64-linux-android$ApiLevel-clang.cmd"
if (-not (Test-Path -LiteralPath $miraCargo)) { throw 'Local Rust toolchain is missing.' }
if (-not (Test-Path -LiteralPath $miraLinker)) { throw "Android NDK linker is missing: $miraLinker" }

$miraEnvironmentNames = @('CARGO_HOME', 'RUSTUP_HOME', 'PATH', 'CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER')
$miraPreviousEnvironment = @{}
foreach ($miraName in $miraEnvironmentNames) {
    $miraPreviousEnvironment[$miraName] = [Environment]::GetEnvironmentVariable($miraName, 'Process')
}
try {
    $env:CARGO_HOME = Join-Path $PSScriptRoot '.tools\cargo'
    $env:RUSTUP_HOME = Join-Path $PSScriptRoot '.tools\rustup'
    $env:PATH = (Join-Path $PSScriptRoot '.tools\cargo\bin') + ';' + $env:PATH
    $env:CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER = $miraLinker
    & (Join-Path $PSScriptRoot '.tools\cargo\bin\rustup.exe') target add aarch64-linux-android
    if ($LASTEXITCODE -ne 0) { throw 'Android Rust target installation failed.' }
    & $miraCargo build --release --locked --target aarch64-linux-android --manifest-path (Join-Path $PSScriptRoot 'Cargo.toml')
    if ($LASTEXITCODE -ne 0) { throw "Android build failed with exit code $LASTEXITCODE" }
} finally {
    foreach ($miraName in $miraEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($miraName, $miraPreviousEnvironment[$miraName], 'Process')
    }
}
