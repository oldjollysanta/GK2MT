"""A visible, isolated Nexus WebView2 download window. No game access or API key."""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

MAXIMUM = 2 * 1024**3


def availability():
    if sys.platform != 'win32':
        return {'available': False, 'message': 'The download panel currently needs Windows WebView2. Use Update from ZIP on this system.'}
    setup_hint = ('Download a fresh copy of GK2MT.exe to restore the download panel support.'
                  if getattr(sys, 'frozen', False) else 'Run Start-GK2MT.cmd to install the tested download panel support.')
    if not importlib.util.find_spec('webview'):
        return {'available': False, 'message': setup_hint}
    try:
        if importlib.metadata.version('pywebview') != '6.2.1':
            raise importlib.metadata.PackageNotFoundError('pywebview')
    except importlib.metadata.PackageNotFoundError:
        return {'available': False, 'message': setup_hint}
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for prefix in ('SOFTWARE', r'SOFTWARE\WOW6432Node'):
            try:
                with winreg.OpenKey(hive, prefix + r'\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') as key:
                    if winreg.QueryValueEx(key, 'pv')[0] not in ('', '0.0.0.0'):
                        return {'available': True, 'message': 'Nexus downloads open in the GK2MT panel.'}
            except OSError:
                pass
    return {'available': False, 'message': 'Install Microsoft Edge WebView2 Runtime, then restart GK2MT. Update from ZIP is available meanwhile.'}


def release_url(mod_id, file_id):
    if any(isinstance(value, bool) or not re.fullmatch(r'0|[1-9][0-9]*', str(value))
           for value in (mod_id, file_id)):
        raise ValueError('Select a valid Nexus release.')
    mod_id, file_id = int(mod_id), int(file_id)
    if mod_id == file_id == 0:
        return 'https://www.nexusmods.com/graveyardkeeper2/mods/'
    if min(mod_id, file_id) <= 0:
        raise ValueError('Select a valid Nexus release.')
    return f'https://www.nexusmods.com/graveyardkeeper2/mods/{mod_id}?tab=files&file_id={file_id}'


def allowed_url(url, download=False):
    try:
        parsed = urlsplit(str(url))
        domains = ('nexusmods.com', 'nexus-cdn.com') if download else ('nexusmods.com',)
        return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username
                and not parsed.password and any(parsed.hostname == d or (parsed.hostname or '').endswith('.' + d) for d in domains))
    except ValueError:
        return False


def page_mod_id(url):
    """A native page address is only a hint; the app still verifies the ZIP with Nexus."""
    if not allowed_url(url):
        return None
    parsed = urlsplit(str(url))
    if parsed.hostname not in ('nexusmods.com', 'www.nexusmods.com'):
        return None
    match = re.fullmatch(r'/(?:games/)?graveyardkeeper2/mods/([1-9][0-9]*)/?', parsed.path)
    return int(match[1]) if match else None


def launch(folder, mod_id, file_id):
    release_url(mod_id, file_id)
    available = availability()
    if not available['available']:
        raise ValueError(available['message'])
    executable = Path(sys.executable)
    frozen = getattr(sys, 'frozen', False)
    if not frozen and executable.with_name('pythonw.exe').is_file():
        executable = executable.with_name('pythonw.exe')
    command = [str(executable), '--nexus-panel' if frozen else str(Path(__file__).resolve())]
    return subprocess.Popen(command + [str(Path(folder).resolve()),
                             str(mod_id), str(file_id)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class Download:
    """Only the native engine's Completed event can publish a finished archive."""
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.operation = None
        self.mod_id = None
        self.state = 'waiting'
        self.last_progress = 0
        self.write('waiting', 'Sign in to Nexus if needed, then choose Manual Download and Slow Download.')

    def write(self, state, message, received=0, total=0):
        self.state = state
        temporary = self.folder / 'panel-status.tmp'
        status = {'state': state, 'message': message, 'received': int(received), 'total': int(total)}
        if self.mod_id is not None:
            status['mod_id'] = self.mod_id
        temporary.write_text(json.dumps(status), encoding='utf-8')
        os.replace(temporary, self.folder / 'panel-status.json')

    def start(self, sender, event):
        event.Handled = True  # The app sets the location; no Save As or browser download popup.
        operation = event.DownloadOperation
        name = str(event.ResultFilePath)
        total = int(operation.TotalBytesToReceive or 0)
        if self.operation is not None or self.state in ('downloaded', 'error', 'closed'):
            event.Cancel = True
            return
        if not allowed_url(operation.Uri, download=True) or not name.lower().endswith('.zip'):
            event.Cancel = True
            self.write('waiting', 'Choose the original ZIP using Nexus Manual Download. Other file types are not installed.')
            return
        if total > MAXIMUM:
            event.Cancel = True
            self.write('error', 'This download exceeds the 2 GB ZIP limit.')
            return
        self.operation = operation
        self.mod_id = page_mod_id(getattr(sender, 'Source', ''))
        event.ResultFilePath = str(self.folder / 'download.zip.part')
        operation.BytesReceivedChanged += self.progress
        operation.StateChanged += self.changed
        self.write('downloading', 'Downloading the ZIP. Keep this panel open until it finishes.', total=total)

    def progress(self, sender, event):
        received, total = int(self.operation.BytesReceived), int(self.operation.TotalBytesToReceive or 0)
        if received > MAXIMUM:
            self.operation.Cancel()
            self.write('error', 'This download exceeds the 2 GB ZIP limit.')
        elif time.monotonic() - self.last_progress >= .5 and self.state == 'downloading':
            self.last_progress = time.monotonic()
            self.write('downloading', 'Downloading the ZIP. Keep this panel open until it finishes.', received, total)

    def changed(self, sender, event):
        if self.state in ('error', 'closed', 'downloaded'):
            return
        state = str(self.operation.State)
        if state == 'Completed':
            part = self.folder / 'download.zip.part'
            try:
                size = part.stat().st_size
                total = int(self.operation.TotalBytesToReceive or 0)
                if not 0 < size <= MAXIMUM or (total > 0 and total != size):
                    raise ValueError('Incomplete download')
                os.replace(part, self.folder / 'download.zip')
                self.write('downloaded', 'Download complete. GK2MT will verify and install the mod.', size, size)
            except (OSError, ValueError):
                self.write('error', 'The ZIP was incomplete or could not be saved. Start the download again.')
        elif state == 'Interrupted':
            self.write('error', 'The download was interrupted. Start it again from GK2MT.')

    def closed(self, *args):
        if self.state not in ('downloaded', 'error'):
            self.write('closed', 'Download panel closed before the ZIP finished.')
            if self.operation:
                self.operation.Cancel()


def run(folder, mod_id, file_id):
    start_url = release_url(mod_id, file_id)
    browsing = int(mod_id) == 0
    download = Download(folder)
    try:
        import webview
        webview.settings.update(ALLOW_DOWNLOADS=False, ALLOW_FILE_URLS=False,
                                OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
        window = webview.create_window('GK2MT · Nexus browser' if browsing else 'GK2MT · Nexus download', html='<html><body style="background:#111c20;color:#eee;font:18px sans-serif">Opening Nexus…</body></html>',
                                       width=1120, height=820, min_size=(760, 560), background_color='#111c20')

        def attach():
            # pywebview is pinned: replace its Save As handler with an app-owned destination.
            from System.Drawing import Color
            from System.Windows.Forms import ToolStrip, ToolStripButton, ToolStripLabel, Timer
            native, view = window.native, window.native.webview
            bar = ToolStrip()
            bar.BackColor, bar.ForeColor = Color.FromArgb(25, 35, 39), Color.WhiteSmoke
            back, release, reload = ToolStripButton('Back'), ToolStripButton('GK2 mods' if browsing else 'Selected release'), ToolStripButton('Reload')
            label = ToolStripLabel('Choose Manual Download → Slow Download. The ZIP installs automatically.')
            for item in (back, release, reload, label):
                bar.Items.Add(item)
            native.Controls.Add(bar)
            bar.BringToFront()
            back.Click += lambda *_: view.GoBack() if view.CanGoBack else None
            release.Click += lambda *_: view.CoreWebView2.Navigate(start_url)
            reload.Click += lambda *_: view.Reload()

            def navigate(sender, event):
                if not allowed_url(event.Uri, download=True):
                    event.Cancel = True
                    label.Text = 'Use Manual Download to keep this ZIP in GK2MT.' if str(event.Uri).startswith('nxm:') else 'Only Nexus pages open in this panel.'

            def popup(sender, event):
                event.Handled = True
                if allowed_url(event.Uri, download=True):
                    view.CoreWebView2.Navigate(event.Uri)

            def external_scheme(sender, event):
                event.Cancel = True
                label.Text = 'Choose Manual Download → Slow Download to keep this mod in GK2MT.'

            def ready(sender, event):
                if not event.IsSuccess:
                    download.write('error', 'WebView2 could not start. Repair Microsoft Edge WebView2 Runtime and retry.')
                    return
                core = view.CoreWebView2
                core.DownloadStarting -= native.browser.on_download_starting
                core.NewWindowRequested -= native.browser.on_new_window_request
                view.NavigationCompleted -= native.browser.on_navigation_completed
                view.WebMessageReceived -= native.browser.on_script_notify
                core.Settings.IsWebMessageEnabled = False
                core.Settings.AreHostObjectsAllowed = False
                core.Settings.IsPasswordAutosaveEnabled = False
                core.DownloadStarting += download.start
                core.NewWindowRequested += popup
                core.NavigationStarting += navigate
                core.LaunchingExternalUriScheme += external_scheme
                core.Navigate(start_url)

            view.CoreWebView2InitializationCompleted += ready
            if view.CoreWebView2:
                from types import SimpleNamespace
                ready(view, SimpleNamespace(IsSuccess=True))
            # Native event callbacks run on this UI thread, including atomic progress writes.
            timer = Timer()
            timer.Interval = 500

            def tick(*args):
                if download.state == 'downloaded':
                    timer.Stop()
                    native.Close()
                elif download.state == 'error':
                    label.Text = 'Download could not finish. Close this panel and retry from GK2MT.'

            timer.Tick += tick
            timer.Start()
            native.FormClosing += download.closed
            window._download_controls = (bar, timer, ready, popup, navigate, external_scheme, tick)

        window.events.before_show += attach
        profile = Path(folder).resolve().parent.parent / 'nexus-profile'
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(profile), debug=False)
        download.closed()
    except Exception:
        download.write('error', 'The Nexus panel could not start. Ensure Microsoft Edge WebView2 Runtime is installed, then restart GK2MT.')
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='GK2MT Nexus download panel')
    parser.add_argument('folder', type=Path)
    parser.add_argument('mod_id', type=int)
    parser.add_argument('file_id', type=int)
    args = parser.parse_args(argv)
    run(args.folder, args.mod_id, args.file_id)


if __name__ == '__main__':
    main()
