param(
    [ValidateSet('test', 'build', 'run')][string]$Action = 'test',
    [string]$RuntimeLibrary,
    [string]$ToolchainBin,
    [switch]$Release,
    [string[]]$DaemonArgs = @()
)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $RuntimeLibrary) {
    $RuntimeLibrary = Join-Path $taskRoot 'onnxruntime/onnxruntime-win-x64-1.31.1/lib/onnxruntime.dll'
}
$RuntimeLibrary = (Resolve-Path -LiteralPath $RuntimeLibrary).Path
if (-not $ToolchainBin) {
    $taskLocalToolchain = Join-Path $taskRoot '.rustup-test/toolchains/stable-x86_64-pc-windows-gnu/bin'
    if (Test-Path -LiteralPath $taskLocalToolchain) { $ToolchainBin = $taskLocalToolchain }
}
$taskEnvironmentNames = @('PATH', 'RUSTC', 'RUSTDOC', 'CARGO_TARGET_DIR', 'ORT_DYLIB_PATH')
$taskPreviousEnvironment = @{}
foreach ($taskName in $taskEnvironmentNames) {
    $taskPreviousEnvironment[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
}
try {
    $env:ORT_DYLIB_PATH = $RuntimeLibrary
    $taskCargo = 'cargo'
    if ($ToolchainBin) {
        $taskCargo = Join-Path $ToolchainBin 'cargo.exe'
        $env:RUSTC = Join-Path $ToolchainBin 'rustc.exe'
        $env:RUSTDOC = Join-Path $ToolchainBin 'rustdoc.exe'
        $env:CARGO_TARGET_DIR = Join-Path $taskRoot 'target-gnu'
    }
    $taskCompilerBin = Join-Path $taskRoot 'msys2/mingw64/bin'
    if (Test-Path -LiteralPath $taskCompilerBin) { $env:PATH = $taskCompilerBin + ';' + $env:PATH }
    $taskArguments = @($Action, '--manifest-path', (Join-Path $taskRoot 'Cargo.toml'))
    if ($Action -eq 'run') { $taskArguments += @('-p', 'memd') }
    else { $taskArguments += '--workspace' }
    if ($Release) { $taskArguments += '--release' }
    if ($Action -eq 'test') { $taskArguments += @('--', '--include-ignored') }
    if ($Action -eq 'run') { $taskArguments += @('--') + $DaemonArgs }
    Write-Host "Explicit ONNX Runtime: $RuntimeLibrary"
    & $taskCargo @taskArguments
    $taskExitCode = $LASTEXITCODE
}
finally {
    foreach ($taskName in $taskEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($taskName, $taskPreviousEnvironment[$taskName], 'Process')
    }
}
exit $taskExitCode
