$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildBuildStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$buildDistPath = Join-Path $PSScriptRoot "dist/collecte-cfe-$buildBuildStamp"
& './.venv/Scripts/python.exe' -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw 'Installation des outils de construction impossible.' }
& './.venv/Scripts/python.exe' -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Les tests doivent reussir avant la construction.' }
$buildOriginalPath = $env:Path
try {
    # Avoid collecting incompatible ICU libraries from PDF tools on PATH.
    $buildPythonDirectory = & './.venv/Scripts/python.exe' -c 'import sys; from pathlib import Path; print(Path(sys.base_prefix))'
    $env:Path = "$PSScriptRoot\.venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot;$buildPythonDirectory"
& './.venv/Scripts/python.exe' -m PyInstaller --noconfirm --windowed --onedir --add-data "collecte_cfe/assets;collecte_cfe/assets" --collect-data ddddocr --icon collecte_cfe/assets/app-icon.ico --name "Collecte CFE" --workpath build/collecte-cfe --distpath $buildDistPath collecte_cfe_windows.py
if ($LASTEXITCODE -ne 0) { throw 'Construction de Collecte CFE impossible.' }
# Files the application never loads: ddddocr reads common_old.onnx only (no beta model, no detection),
# and OpenCV's video module is never used. About 100 Mo less.
$buildInternal = Join-Path $buildDistPath 'Collecte CFE/_internal'
foreach ($buildUnused in @('ddddocr/common.onnx', 'ddddocr/common_det.onnx', 'cv2/opencv_videoio_ffmpeg*.dll')) {
    Get-ChildItem -Path (Join-Path $buildInternal $buildUnused) -ErrorAction SilentlyContinue | Remove-Item -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $buildInternal 'ddddocr/common_old.onnx'))) { throw 'Modele de lecture du CAPTCHA absent de la construction.' }
Write-Host "Executable disponible dans $buildDistPath/Collecte CFE/Collecte CFE.exe. Copier tout le dossier."

# Two ready-to-copy packages: one to unzip on the PC, one carried on a USB key with its data beside it.
$buildPackages = Join-Path $buildDistPath 'paquets'
New-Item -ItemType Directory -Force -Path $buildPackages | Out-Null
foreach ($buildPackage in @(@{Name='Collecte-CFE'; Portable=$false}, @{Name='Collecte-CFE-portable'; Portable=$true})) {
    $buildFolder = Join-Path $buildPackages $buildPackage.Name
    Copy-Item -LiteralPath (Join-Path $buildDistPath 'Collecte CFE') -Destination $buildFolder -Recurse
    if ($buildPackage.Portable) {
        Set-Content -LiteralPath (Join-Path $buildFolder 'portable.txt') -Encoding UTF8 -Value 'Ce fichier garde les donnees dans le dossier Donnees, a cote de Collecte CFE.exe. Supprimez-le pour les ranger sur le poste.'
    }
    Compress-Archive -Path $buildFolder -DestinationPath (Join-Path $buildPackages "$($buildPackage.Name).zip") -Force
}
Write-Host "Paquets prets dans $buildPackages : Collecte-CFE.zip (poste) et Collecte-CFE-portable.zip (cle USB)."

} finally { $env:Path = $buildOriginalPath }
