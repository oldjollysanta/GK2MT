"""Download completion and URL boundaries; --native also exercises real WebView2."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
from unittest.mock import patch
import zipfile

import nexus_panel


class Event:
    def __iadd__(self, handler):
        return self


def checks():
    with TemporaryDirectory() as temporary:
        folder = Path(temporary)
        executable = folder / 'GK2MT.exe'
        (folder / 'pythonw.exe').touch()
        for frozen in (False, True):
            for mod_id, file_id in ((48, 123), (0, 0)):
                with patch.object(sys, 'frozen', frozen, create=True), \
                     patch.object(sys, 'executable', str(executable)), \
                     patch.object(nexus_panel, 'availability', return_value={'available': True}), \
                     patch.object(nexus_panel.subprocess, 'Popen') as launch:
                    nexus_panel.launch(folder, mod_id, file_id)
                    command = launch.call_args.args[0]
                    expected = [str(executable), '--nexus-panel'] if frozen else [str(folder / 'pythonw.exe'), str(Path(nexus_panel.__file__).resolve())]
                    assert command == expected + [str(folder.resolve()), str(mod_id), str(file_id)]
                    assert launch.call_args.kwargs['stdout'] == nexus_panel.subprocess.DEVNULL
                    assert launch.call_args.kwargs['creationflags'] == getattr(nexus_panel.subprocess, 'CREATE_NO_WINDOW', 0)
        with patch.object(nexus_panel, 'run') as run:
            nexus_panel.main([str(folder), '48', '123'])
            run.assert_called_once_with(folder, 48, 123)
            run.reset_mock()
            nexus_panel.main([str(folder), '0', '0'])
            run.assert_called_once_with(folder, 0, 0)
    assert nexus_panel.release_url(0, 0) == 'https://www.nexusmods.com/graveyardkeeper2/mods/'
    assert nexus_panel.release_url(48, 123).endswith('/48?tab=files&file_id=123')
    for pair in ((0, 1), (1, 0), (-1, 2), (True, 2), (1.5, 2), ('01', 2)):
        try:
            nexus_panel.release_url(*pair)
            assert False, 'Invalid release pair accepted.'
        except ValueError:
            pass
    assert nexus_panel.page_mod_id('https://www.nexusmods.com/graveyardkeeper2/mods/206?tab=files') == 206
    assert nexus_panel.page_mod_id('https://www.nexusmods.com/games/graveyardkeeper2/mods/206/') == 206
    for source in ('https://www.nexusmods.com/othergame/mods/206', 'https://evilnexusmods.com/graveyardkeeper2/mods/206',
                   'https://users.nexusmods.com/graveyardkeeper2/mods/206', 'https://www.nexusmods.com/graveyardkeeper2/mods/0',
                   'https://www.nexusmods.com/graveyardkeeper2/mods/206/other', 'https://www.nexusmods.com/graveyardkeeper2/mods/'):
        assert nexus_panel.page_mod_id(source) is None
    assert nexus_panel.allowed_url('https://www.nexusmods.com/graveyardkeeper2')
    assert nexus_panel.allowed_url('https://users.nexusmods.com/auth')
    assert nexus_panel.allowed_url('https://cf-files.nexusmods.com/test.zip', download=True)
    assert nexus_panel.allowed_url('https://files.nexus-cdn.com/test.zip', download=True)
    for url in ('http://www.nexusmods.com', 'https://nexusmods.com.evil.example',
                'https://evilnexusmods.com', 'https://user@nexusmods.com', 'https://nexusmods.com:444/',
                'file:///C:/test.zip', 'nxm://graveyardkeeper2/mods/1/files/2', 'http://127.0.0.1:47831'):
        assert not nexus_panel.allowed_url(url, download=True)
    with TemporaryDirectory() as temporary:
        folder = Path(temporary)
        download = nexus_panel.Download(folder)
        operation = SimpleNamespace(Uri='https://files.nexus-cdn.com/a.zip', TotalBytesToReceive=3,
                                    BytesReceived=3, BytesReceivedChanged=Event(), StateChanged=Event(),
                                    State='InProgress', Cancel=lambda: None)
        event = SimpleNamespace(DownloadOperation=operation, ResultFilePath='C:/downloads/test.zip', Handled=False, Cancel=False)
        download.start(SimpleNamespace(Source='https://www.nexusmods.com/graveyardkeeper2/mods/206?tab=files'), event)
        assert not event.Cancel and event.Handled and event.ResultFilePath == str(folder / 'download.zip.part')
        (folder / 'download.zip.part').write_bytes(b'zip')
        download.progress(None, None)
        assert not (folder / 'download.zip').exists()
        operation.State = 'Completed'
        download.changed(None, None)
        assert (folder / 'download.zip').read_bytes() == b'zip'
        download.closed()
        status = json.loads((folder / 'panel-status.json').read_text())
        assert status['state'] == 'downloaded' and status['mod_id'] == 206
        assert 'install the mod' in status['message']
        event.Cancel = False
        download.start(None, event)
        assert event.Cancel
    with TemporaryDirectory() as temporary:
        download = nexus_panel.Download(temporary)
        operation.State = 'Interrupted'
        download.operation = operation
        download.changed(None, None)
        assert download.state == 'error' and not (Path(temporary) / 'download.zip').exists()
    print('Panel checks passed: release/browser launch, native page hints, trusted URLs, completion-only pickup, partial/error handling.')


def native():
    """Navigate a local test page into a ZIP download; no real Nexus or game access."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        archive.writestr('Example.dll', 'temporary test bytes')
    data = payload.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', 'attachment; filename="fixture.zip"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/fixture.zip'
    with TemporaryDirectory() as temporary:
        folder = Path(temporary) / 'downloads' / 'job'
        with patch.object(nexus_panel, 'release_url', return_value=url), \
             patch.object(nexus_panel, 'allowed_url', side_effect=lambda value, **kwargs: str(value) == url):
            try:
                nexus_panel.run(folder, 0, 0)
                status = json.loads((folder / 'panel-status.json').read_text())
                assert status['state'] == 'downloaded', status
                assert (folder / 'download.zip').read_bytes() == data
            finally:
                server.shutdown()
                server.server_close()
    print('Native WebView2 browser check passed: ZIP captured without Save As, completed, staged, and panel closed.')


if __name__ == '__main__':
    checks()
    if '--native' in sys.argv:
        native()
