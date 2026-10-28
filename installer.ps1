$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 ou plus recent est requis.' }
}
& './.venv/Scripts/python.exe' -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'Installation des dependances impossible.' }
& './.venv/Scripts/python.exe' -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Installation de Chromium impossible.' }
Write-Host 'Collecte CFE est installe. Double-cliquez sur Collecte CFE.cmd.'
