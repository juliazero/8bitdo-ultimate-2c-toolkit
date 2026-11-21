# SPDX-License-Identifier: GPL-3.0-or-later
# Build a portable Windows EXE/ZIP with CPython 3.12 x64 and binary hidapi.
# Python 3.15 (or another default Python) is intentionally left untouched.
[CmdletBinding()]
param(
    [switch]$RecreateVenv,
    [switch]$SkipInstall,
    [string]$PythonPath,
    [switch]$SkipFirmwareRebuild
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venv = Join-Path $root '.venv-windows'
$venvPython = Join-Path $venv 'Scripts\python.exe'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    # Send external-command output to the host, not to the function result.
    # Otherwise winget logs are concatenated with the returned Python path.
    & $Executable @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

function Test-Python312 {
    param([string]$Executable)
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $false
    }
    # Avoid nested string literals: PowerShell 5.1 can corrupt quoted -c snippets.
    $probe = 'import sys; print(sys.version_info.major); print(sys.version_info.minor); print(64 if sys.maxsize > 2**32 else 32)'
    try {
        $result = @(& $Executable -c $probe 2>&1)
        if ($LASTEXITCODE -ne 0 -or $result.Count -ne 3) { return $false }
        $lines = @($result | ForEach-Object { ([string]$_).Trim() })
        return ($lines[0] -eq '3' -and $lines[1] -eq '12' -and $lines[2] -eq '64')
    } catch {
        return $false
    }
}

function Get-PythonCandidates {
    $paths = New-Object 'System.Collections.Generic.List[string]'
    if ($PythonPath) {
        $paths.Add($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PythonPath))
        return $paths
    }
    # Try the launcher explicitly, but never require it.
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        try {
            $resolved = @(& $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $resolved.Count -eq 1 -and $resolved[0]) {
                $paths.Add(([string]$resolved[0]).Trim())
            }
        } catch { }
    }
    # Ordinary 3.12 installs (user and all-users), including winget installations.
    foreach ($base in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $base) { continue }
        foreach ($relative in @('Programs\Python\Python312\python.exe', 'Python312\python.exe')) {
            $paths.Add((Join-Path $base $relative))
        }
    }
    # Windows Python install registration (when available).
    foreach ($registryPath in @('HKCU:\Software\Python\PythonCore\3.12\InstallPath',
                               'HKLM:\Software\Python\PythonCore\3.12\InstallPath',
                               'HKLM:\Software\WOW6432Node\Python\PythonCore\3.12\InstallPath')) {
        if (-not (Test-Path $registryPath)) { continue }
        try {
            $key = Get-Item $registryPath
            $executable = $key.GetValue('ExecutablePath')
            if ($executable) { $paths.Add([string]$executable) }
            $folder = $key.GetValue('')
            if ($folder) { $paths.Add((Join-Path ([string]$folder) 'python.exe')) }
        } catch { }
    }
    foreach ($name in @('python.exe', 'python3.exe')) {
        foreach ($command in @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue)) {
            if ($command.Source) { $paths.Add($command.Source) }
        }
    }
    return $paths.ToArray()
}

function Find-Python312 {
    $seen = @{}
    foreach ($candidate in @(Get-PythonCandidates)) {
        if (-not $candidate -or $seen.ContainsKey([string]$candidate)) { continue }
        $seen[[string]$candidate] = $true
        if (Test-Python312 -Executable $candidate) {
            Write-Host "Using Python 3.12 x64: $candidate" -ForegroundColor Green
            return [string]$candidate
        }
    }
    return $null
}

function Install-Python312 {
    Write-Host ''
    Write-Warning 'Python 3.12 x64 was not found. Python 3.15 will NOT be modified.'
    $choice = Read-Host 'Install Python 3.12 x64 with winget for building this project? [y/N]'
    if ($choice -notin @('y','Y','yes','YES','t','T','tak','TAK')) {
        throw 'Python 3.12 installation declined. You can also pass -PythonPath with a 3.12 x64 interpreter.'
    }
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $winget) {
        throw 'winget.exe is not available. Install Python 3.12 x64 manually from python.org and rerun.'
    }
    Write-Host 'Installing Python 3.12 x64 using winget...'
    Invoke-Checked -Executable $winget.Source -Arguments @('install','--id','Python.Python.3.12', '--exact',
        '--source','winget','--architecture','x64','--accept-package-agreements','--accept-source-agreements')
    # Installation may update PATH only for new shells; use the launcher and absolute paths instead.
    $selected = Find-Python312
    if (-not $selected) {
        throw 'Python 3.12 installation finished, but its executable could not be found. Reopen PowerShell or supply -PythonPath.'
    }
    return $selected
}

function Get-VenvVersion {
    if (-not (Test-Path -LiteralPath $venvPython)) { return $null }
    try {
        $version = @(& $venvPython -c 'import sys; print(sys.version_info.major); print(sys.version_info.minor); print(64 if sys.maxsize > 2**32 else 32)' 2>$null)
        if ($LASTEXITCODE -ne 0 -or $version.Count -ne 3) { return $null }
        return ($version -join '.')
    } catch { return $null }
}

Push-Location $root
try {
    $selected = Find-Python312
    if (-not $selected) {
        if ($PythonPath) { throw "-PythonPath is not a Python 3.12 x64 executable: $PythonPath" }
        $selected = Install-Python312
    }

    if ($RecreateVenv -and (Test-Path -LiteralPath $venv)) {
        Write-Host "Removing virtual environment: $venv"
        Remove-Item -LiteralPath $venv -Recurse -Force
    }
    $venvVersion = Get-VenvVersion
    if ((Test-Path -LiteralPath $venv) -and $venvVersion -ne '3.12.64') {
        Write-Host "Existing venv uses $venvVersion instead of Python 3.12 x64. Recreating..."
        Remove-Item -LiteralPath $venv -Recurse -Force
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "Creating virtual environment: $venv"
        Invoke-Checked -Executable $selected -Arguments @('-m','venv',$venv)
    }

    if (-not $SkipInstall) {
        Invoke-Checked -Executable $venvPython -Arguments @('-m','pip','install','--upgrade','pip','setuptools','wheel')
        # This package ships a Windows x64 CPython 3.12 wheel: never build it from source.
        Invoke-Checked -Executable $venvPython -Arguments @('-m','pip','install','--only-binary=:all:','hidapi==0.15.0')
        Invoke-Checked -Executable $venvPython -Arguments @('-m','pip','install','--only-binary=hidapi','-e','.[desktop]','pyinstaller==6.22.3','Pillow==12.3.0')
    }
    Invoke-Checked -Executable $venvPython -Arguments @('-c','import hid, webview, PyInstaller, PIL')
    # Firmware blobs are versioned in the repository and packaged as application data.
    # No TC32 compiler or private controller backup is needed for desktop builds.
    if ($SkipFirmwareRebuild) {
        Write-Host 'Using committed firmware tweak blobs (no rebuild is needed).'
    }
    Write-Host 'Building Windows EXE / ZIP...'
    Invoke-Checked -Executable $venvPython -Arguments @('tools/build_desktop.py')
    Write-Host "Finished. Output: $root\dist" -ForegroundColor Green
} catch {
    Write-Error $_
    exit 1
} finally {
    Pop-Location
}
