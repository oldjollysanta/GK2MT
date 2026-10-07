"""Check the standalone GUI executable with isolated data and no Python on PATH."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent


def check_archive(executable):
    with pefile.PE(str(executable), fast_load=True) as binary:
        assert binary.OPTIONAL_HEADER.Subsystem == 2, 'Must be a Windows GUI executable, not a console server.'
    archive = CArchiveReader(str(executable))
    names = {name.replace('\\', '/'): name for name in archive.toc}
    for name in ('deck.py', 'deck_discovery.py', 'deck_proton.py', 'deck_game_settings.py', 'DECK-SETUP.md',
                 'web/index.html', 'web/app.js', 'web/setup.js', 'web/style.css',
                 'web/guided.css', 'web/banner.png'):
        assert archive.extract(names[name]) == (ROOT / name).read_bytes(), name + ' is stale or missing'
    for name in ('webview/js/api.js', 'webview/js/finish.js',
                 'webview/lib/Microsoft.Web.WebView2.Core.dll',
                 'webview/lib/Microsoft.Web.WebView2.WinForms.dll',
                 'webview/lib/runtimes/win-x64/native/WebView2Loader.dll',
                 'pythonnet/runtime/Python.Runtime.dll',
                 'clr_loader/ffi/dlls/amd64/ClrLoader.dll', 'pywebview-6.2.1.dist-info/METADATA'):
        assert name in names and archive.extract(names[name]), name + ' is missing'
    modules = archive.open_embedded_archive('PYZ.pyz').toc
    for name in ('desktop', 'profiles', 'nexus_credentials', 'paramiko', 'cryptography', 'webview.platforms.edgechromium', 'webview.platforms.winforms'):
        assert name in modules, name + ' is missing'


def powershell(code):
    executable = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(executable), '-NoProfile', '-Command', code], capture_output=True,
                            text=True, timeout=30, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout or '[]')


def windows_for(pids):
    windows = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback
    def found(handle, _):
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value in pids and ctypes.windll.user32.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(handle, title, 256)
            windows.append((handle, title.value))
        return True
    ctypes.windll.user32.EnumWindows(found, 0)
    return windows


def main():
    executable = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'dist/GK2MT.exe').resolve()
    check_archive(executable)
    with tempfile.TemporaryDirectory(prefix='gk2mt-desktop-exe-') as temporary:
        folder = Path(temporary)
        copied = folder / 'GK2MT.exe'
        shutil.copy2(executable, copied)
        data, game, workshop = folder / 'data', folder / 'game', folder / '4358690'
        for path in (data, game, workshop, folder / 'temp'):
            path.mkdir()
        settings = data / 'settings.json'
        settings.write_text(json.dumps({'game': str(game), 'workshop': str(workshop)}))
        original = settings.read_bytes()
        env = {key: value for key, value in os.environ.items() if key.upper() not in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV')}
        env.update(GK2MT_DATA=str(data), TEMP=str(folder / 'temp'), TMP=str(folder / 'temp'),
                   PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
        process = subprocess.Popen([str(copied)], cwd=folder, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert process.poll() is None, 'Desktop application exited during startup.'
                processes = powershell("@(Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('GK2MT.exe','msedgewebview2.exe') } | Select-Object ProcessId,ParentProcessId,ExecutablePath) | ConvertTo-Json -Compress")
                pids = {row['ProcessId'] for row in processes if row.get('ExecutablePath') == str(copied)}
                windows = windows_for(pids)
                assert not any('could not start' in title for _, title in windows), windows
                matches = [(handle, title) for handle, title in windows if title == 'GK2MT · Graveyard Keeper 2 Mod Toolkit']
                if matches and any(row['ParentProcessId'] in pids and row.get('ExecutablePath', '').endswith('msedgewebview2.exe') for row in processes):
                    break
                time.sleep(.2)
            else:
                raise AssertionError('No native GK2MT window with WebView2 appeared.')
            for _ in range(5):
                pids.update(row['ProcessId'] for row in processes if row['ParentProcessId'] in pids)
            ports = powershell('@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -in @(' + ','.join(map(str, pids)) + ') } | Select-Object LocalPort) | ConvertTo-Json -Compress')
            assert not ports, ('Desktop app opened a listening port', ports)
            assert settings.read_bytes() == original and not list(game.iterdir()) and not list(workshop.iterdir())
            # WM_CLOSE only targets this test-owned, isolated window: normal app shutdown.
            ctypes.windll.user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            ctypes.windll.user32.PostMessageW(matches[0][0], 0x0010, 0, 0)
            assert process.wait(timeout=20) == 0
        finally:
            if process.poll() is None:
                subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'), '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                process.wait(timeout=20)
            for attempt in range(50):
                try:
                    shutil.rmtree(folder / 'temp')
                    break
                except PermissionError:
                    if attempt == 49:
                        raise
                    time.sleep(.1)
    print('Desktop EXE passed: GUI subsystem, standalone native window, bundled assets, zero listening ports, clean window-close exit.')


if __name__ == '__main__':
    main()
