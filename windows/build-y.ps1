# SPDX-License-Identifier: GPL-3.0-or-later
# Build the experimental Switch 2 (Y) firmware offline. Does NOT flash a device.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Backup,
    [string]$Compiler,
    [string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backupFile = (Resolve-Path -LiteralPath $Backup -ErrorAction Stop).Path
$build = Join-Path $root 'build\switch2-pairing'
$output = Join-Path $root 'build\switch2-trial'

if (-not $PythonPath) {
    $venvPython = Join-Path $root '.venv-windows\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) { $PythonPath = $venvPython }
    else {
        $candidate = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $candidate) { throw 'Python was not found. Run windows/build.ps1 first or supply -PythonPath.' }
        $PythonPath = $candidate.Source
    }
}
if (-not $Compiler) {
    $candidate = Get-Command tc32-elf-gcc.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($candidate) { $Compiler = $candidate.Source }
    else {
        $bundled = Join-Path $root 'build\tc32-tools\tc32\bin\tc32-elf-gcc.exe'
        if (Test-Path -LiteralPath $bundled) { $Compiler = $bundled }
    }
}
if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler)) {
    throw 'TC32 compiler not found. Supply -Compiler "C:\path\to\tc32-elf-gcc.exe".'
}
if ((Get-Item -LiteralPath $backupFile).Length -ne 524288) {
    throw 'Expected a complete 512-KiB controller backup. No firmware has been built.'
}
$previous = Get-Location
try {
    Set-Location -LiteralPath $root
    $commands = @(
        @('tools/build_switch2_pairing.py', '--compiler', $Compiler, '--output', $build),
        @('tools/build_switch2_gatt.py', '--build', $build),
        @('tools/build_switch2_trial.py', '--backup', $backupFile, '--build', $build, '--output', $output)
    )
    foreach ($commandArgs in $commands) {
        & $PythonPath @commandArgs
        if ($LASTEXITCODE -ne 0) { throw "Y firmware build failed: $($commandArgs[0]) ($LASTEXITCODE)" }
    }
    Write-Host "Experimental image: $output\controller-y-experimental-full.bin"
    Write-Warning 'This is experimental firmware. Verify the build report and keep your original backup before hardware testing.'
} finally { Set-Location $previous }
