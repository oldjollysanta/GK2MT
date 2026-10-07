$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Keep unrelated packages from the developer's Python out of the executable.
$buildPython = Join-Path $PSScriptRoot '.build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv .build-venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the build environment.' }
}
& $buildPython -m pip install --disable-pip-version-check -r requirements.txt pyinstaller==6.18.0
if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies.' }

& $buildPython -m PyInstaller --noconfirm --clean --onefile --windowed --name GK2MT `
    --add-data 'web;web' --add-data 'DECK-SETUP.md;.' `
    --add-data 'deck.py;.' --add-data 'deck_discovery.py;.' --add-data 'deck_proton.py;.' `
    --add-data 'deck_game_settings.py;.' `
    --copy-metadata pywebview --collect-data webview --hidden-import webview.platforms.winforms `
    --hidden-import webview.platforms.edgechromium app.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
Write-Host "Built: $PSScriptRoot\dist\GK2MT.exe"
