param([Parameter(ValueFromRemainingArguments = $true)][string[]]$MagViewArgs)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$venvPython = Join-Path $repoRoot '.venv-windows\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run platforms\windows\setup.ps1 first.' }
$env:PYTHONUTF8 = '1'
& $venvPython -m magview @MagViewArgs
exit $LASTEXITCODE
