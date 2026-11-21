# SPDX-License-Identifier: GPL-3.0-or-later
# Rebuild the selectable Switch 2 Pro tweak from the firmware sources.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$Compiler,
    [Parameter(Mandatory=$true)][string]$StockBackup
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw "TC32 not found: $Compiler. Specify the TC32 executable with -Compiler."
}
if (-not (Test-Path -LiteralPath $StockBackup -PathType Leaf)) {
    throw "Stock backup not found: $StockBackup. Supply a verified stock image with -StockBackup."
}
& $PythonPath (Join-Path $root 'tools\rebuild_switch2_tweak.py') --compiler $Compiler --stock $StockBackup
if ($LASTEXITCODE -ne 0) { throw 'Failed to compile Switch 2 tweak; Windows EXE will not be built with stale firmware blobs.' }
& $PythonPath -m unittest tests.test_switch2_recipe tests.test_switch2_rumble
if ($LASTEXITCODE -ne 0) { throw 'Switch 2 patch tests failed; Windows EXE will not be built.' }
