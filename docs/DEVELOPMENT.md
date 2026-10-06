# Developing GK2MT

GK2MT is a Windows desktop application built with Python, pywebview, and a local HTML/CSS/JavaScript interface. Python owns file operations, Nexus integration, and SSH; the interface calls the native bridge. The application does not need a local web server.

For normal use, download [GK2MT.exe from the latest release](https://github.com/oldjollysanta/GK2MT/releases/latest/download/GK2MT.exe). It needs Windows x64 and WebView2, with no Python installation. The source, test, and build instructions below are optional for contributors and advanced users; see the [user guide](USER-GUIDE.md) to get started with the app.

[Run from source](#run-from-source) · [Tests](#tests) · [Build the executable](#build-the-executable) · [Optional HotkeyManager](#optional-hotkeymanager) · [Repository hygiene](#repository-hygiene)

## Requirements

- Windows x64 and **Python 3.11+**, available as `python`.
- **Microsoft Edge WebView2 Runtime** for the desktop window and Nexus panel.
- **Node.js** to run the JavaScript checks; no npm install is needed.
- **.NET SDK 8+** only if building the optional HotkeyManager plugin.

Run the following commands from the repository root in PowerShell.

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:GK2MT_DATA = Join-Path $env:TEMP 'gk2mt-development'
.\.venv\Scripts\python.exe app.py
```

`GK2MT_DATA` redirects settings, credentials, package libraries, downloads, and backups. **It does not redirect the game or Workshop folders.** For experiments, select disposable fixture folders in Quick Setup before using any operation that writes files. Do not reuse your normal app-data directory for development. The environment override lasts for the current PowerShell session; the release default is `%LOCALAPPDATA%\GK2MT`.

The application is split across:

| Area | Files |
|---|---|
| Native bridge and operations | `desktop.py`, `app.py` |
| Inventory, package deployment, profiles | `inventory.py`, `manager.py`, `profiles.py` |
| Nexus, Workshop, credentials, download panel | `integrations.py`, `nexus_credentials.py`, `nexus_panel.py` |
| Deck connection, comparison, and Proton setup | `deck_ssh.py`, `deck.py`, `deck_discovery.py`, `deck_proton.py` |
| Desktop interface | `web/index.html`, `web/app.js`, `web/setup.js`, and stylesheets |

## Tests

Root Python checks are standalone scripts using temporary fixtures and mocked external responses. SSH checks use a local test server. They do not require personal Nexus credentials or a physical Deck. Use the fixtures to reproduce issues rather than copying a live game installation or app-data directory into the repository.

Run the Python suite, excluding the separate native-window and packaged-executable checks:

```powershell
Get-ChildItem -File test_*.py |
    Where-Object { $_.Name -notin @('test_desktop.py', 'test_exe.py') } |
    ForEach-Object {
        & .\.venv\Scripts\python.exe $_.FullName
        if ($LASTEXITCODE -ne 0) { throw "Failed: $($_.Name)" }
    }

node test_web.js
node test_quick_setup_web.js
node test_guided_web.js
```

For a focused change, run the relevant scripts first. For example, package deployment affects `test_manager.py`, `test_updates.py`, and `test_imports.py`; profile UI changes affect `test_app_profiles.py`, `test_web.js`, and `test_guided_web.js`.

These additional checks open real WebView2 test windows and still use isolated fixtures:

```powershell
.\.venv\Scripts\python.exe test_desktop.py
.\.venv\Scripts\python.exe test_nexus_panel.py --native
```

Fixture tests do not establish compatibility with every mod, live Nexus website behaviour, or a physical Steam Deck. Describe any manual validation separately and state what was actually tested.

## Build the executable

```powershell
powershell -ExecutionPolicy Bypass -File .\Build-GK2MT.ps1
.\.build-venv\Scripts\python.exe test_exe.py dist\GK2MT.exe
```

The build script creates its own `.build-venv`, installs the required build tools, and packages `dist/GK2MT.exe` as one windowed executable. It includes the interface, banner, Deck guide, and Deck helper sources. The target PC needs WebView2 but does not need Python or the source checkout.

Keep builds and generated files out of source commits. Rebuild after changing a bundled asset; `test_exe.py` checks that the packaged sources match the checkout.

## Optional HotkeyManager

The C# plugin under `mods/HotkeyManager` is a separate build and is not required to run GK2MT. It references a locally owned Graveyard Keeper 2 installation with BepInEx already installed.

```powershell
$gameFolder = 'D:\SteamLibrary\steamapps\common\Graveyard Keeper 2'
powershell -ExecutionPolicy Bypass -File .\mods\HotkeyManager\Build.ps1 -GameDir $gameFolder
```

Replace the example path with your own installation. The script builds the plugin, runs its executable checks, and writes its ZIP under the repository's `dist` folder. Game and BepInEx reference assemblies are local build inputs; do not commit or distribute them. Do not commit the generated DLL, ZIP, `bin`, or `obj` folders.

See the [HotkeyManager guide](../mods/HotkeyManager/README.md) and [integration API](../mods/HotkeyManager/API.md) for plugin-specific development and optional in-game harnesses.

## Repository hygiene

Commit source, documentation, and small synthetic fixtures. Exclude game assemblies, installed mods, third-party mod binaries, downloaded archives, saves, app-data folders, browser profiles, SSH trust/credential files, and personal API keys. Never add a shared Nexus key to make builds work; users connect their own account and tests use dummy credentials.

GK2MT source is licensed **GPL-3.0-only**; see [LICENSE](../LICENSE). Dependencies and third-party mods retain their own licenses. Read [CONTRIBUTING.md](../CONTRIBUTING.md) before submitting changes.
