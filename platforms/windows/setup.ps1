# Native Windows environment, separate from WSL's .venv. No activation required.
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$VenvPath = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $VenvPath) { $VenvPath = Join-Path $repoRoot '.venv-windows' }
$VenvPath = [IO.Path]::GetFullPath($VenvPath)
$env:PYTHONUTF8 = '1'
& $Python -c "import sys, struct; assert sys.platform == 'win32', 'Use native Windows Python, not WSL'; assert sys.version_info >= (3, 12), 'Python 3.12+ required by pinned dependencies (3.13 tested)'; assert struct.calcsize('P') == 8, '64-bit Python required'"
if ($LASTEXITCODE -ne 0) { throw 'Select a native 64-bit Python 3.13 with -Python <path>.' }
if (-not (Test-Path -LiteralPath $VenvPath)) {
    & $Python -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the Windows virtual environment.' }
}
$venvPython = Join-Path $VenvPath 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw "Not a Windows venv: $VenvPath" }
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $venvPython -m pip install --disable-pip-version-check --no-deps -e $repoRoot
if ($LASTEXITCODE -ne 0) { throw 'MagView installation failed.' }
Write-Host "Ready: $venvPython -m magview"
