"""Native WebView2 shell and local Python bridge. No HTTP server or listening port."""
import json
from pathlib import Path
from urllib.parse import urlsplit
import webbrowser


APP_URL = 'https://gk2mt.invalid/index.html'


def trusted_source(url):
    return str(url).split('#', 1)[0] == APP_URL


def native_drop_paths(source, objects, file_type):
    """Use WebView2's native File objects, never paths supplied by JavaScript."""
    if not trusted_source(source):
        return []
    files = [str(item.Path) for item in objects or () if isinstance(item, file_type)]
    if len(files) > 100:
        raise ValueError('Drop 100 ZIP files or fewer at a time.')
    if any(not Path(path).is_absolute() for path in files):
        raise ValueError('Windows could not identify the dropped file locations. Use Install ZIP instead.')
    return list(dict.fromkeys(files))


class Bridge:
    def __init__(self, backend):
        self._backend = backend
        self._closing = False

    def request(self, action, body=None):
        try:
            if (not isinstance(action, str) or len(action) > 80
                    or body is not None and not isinstance(body, dict)
                    or len(json.dumps(body)) > 1024 * 1024):
                raise ValueError('Invalid app request.')
            if self._closing:
                raise ValueError('GK2MT is closing.')
            app = self._backend
            if action == 'downloads':
                return {'ok': True, 'result': app.download_state()}
            with app.LOCK:
                if self._closing:
                    raise ValueError('GK2MT is closing.')
                if action == 'state':
                    result = app.snapshot()
                elif action == 'deck-guide':
                    result = {'text': (app.ROOT / 'DECK-SETUP.md').read_text('utf-8')}
                elif action == 'open-external':
                    url = str((body or {}).get('url', ''))
                    parsed = urlsplit(url)
                    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                            or parsed.port not in (None, 443) or any(ord(c) < 32 for c in url)):
                        raise ValueError('Only HTTPS website links can open outside GK2MT.')
                    webbrowser.open(url)
                    result = {}
                else:
                    result = app.dispatch(action, body or {})
                return {'ok': True, 'result': result}
        except PermissionError:
            return {'ok': False, 'error': 'Windows denied access. Close the game and Vortex. If needed, run GK2MT as administrator.'}
        except Exception as error:
            return {'ok': False, 'error': str(error)}


def run(app, started=None):
    import webview
    available = app.nexus_panel.availability()
    if not available['available']:
        raise RuntimeError(available['message'])
    app.DATA.mkdir(parents=True, exist_ok=True)
    bridge = Bridge(app)
    webview.settings.update(ALLOW_DOWNLOADS=False, ALLOW_FILE_URLS=False,
                            OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    # Tiny initial document: map the bundled assets before the first app navigation.
    window = webview.create_window('GK2MT · Graveyard Keeper 2 Mod Toolkit',
        html='<html><body style="background:#10191c;color:#eee;font:18px Segoe UI">Opening GK2MT…</body></html>',
        js_api=bridge, width=1440, height=940, min_size=(1040, 700), background_color='#10191c')
    app.WINDOW = window

    def attach():
        from Microsoft.Web.WebView2.Core import CoreWebView2File, CoreWebView2HostResourceAccessKind
        native, view = window.native, window.native.webview

        def message(sender, event):
            if trusted_source(event.Source):
                try:
                    payload = json.loads(str(event.WebMessageAsJson))
                    if payload == 'GK2MT:zip-drop':
                        try:
                            paths = native_drop_paths(event.Source, event.AdditionalObjects, CoreWebView2File)
                            detail = {'paths': paths} if paths else None
                        except ValueError as error:
                            detail = {'error': str(error)}
                        if detail:
                            # This callback runs on the WinForms UI thread; don't block it with evaluate_js.
                            view.CoreWebView2.ExecuteScriptAsync(
                                "window.dispatchEvent(new CustomEvent('gk2mt-drop',{detail:"
                                + json.dumps(detail) + '}));')
                    elif isinstance(payload, list) and len(payload) == 3 and payload[0] == 'request':
                        native.browser.on_script_notify(sender, event)
                except (ValueError, TypeError):
                    pass

        def navigate(sender, event):
            event.Cancel = not trusted_source(event.Uri)

        def popup(sender, event):
            event.Handled = True

        def cancel(sender, event):
            event.Cancel = True

        def loaded(sender, event):
            # The cancelled bootstrap navigation must not inject a second bridge
            # and erase callbacks belonging to the real app document.
            if event.IsSuccess and trusted_source(sender.Source):
                native.browser.on_navigation_completed(sender, event)

        def ready(sender, event):
            if not event.IsSuccess:
                from System.Windows.Forms import MessageBox
                MessageBox.Show('WebView2 could not start. Repair Microsoft Edge WebView2 Runtime and reopen GK2MT.', 'GK2MT')
                native.Close()
                return
            core = view.CoreWebView2
            core.SetVirtualHostNameToFolderMapping('gk2mt.invalid', str(app.ROOT / 'web'), CoreWebView2HostResourceAccessKind.Deny)
            view.WebMessageReceived -= native.browser.on_script_notify
            view.WebMessageReceived += message
            view.NavigationCompleted -= native.browser.on_navigation_completed
            view.NavigationCompleted += loaded
            core.NewWindowRequested -= native.browser.on_new_window_request
            core.NewWindowRequested += popup
            core.NavigationStarting += navigate
            core.LaunchingExternalUriScheme += cancel
            core.Settings.AreHostObjectsAllowed = False
            core.Settings.IsPasswordAutosaveEnabled = False
            core.Settings.AreDevToolsEnabled = False
            core.Navigate(APP_URL)

        view.CoreWebView2InitializationCompleted += ready
        if view.CoreWebView2:
            from types import SimpleNamespace
            ready(view, SimpleNamespace(IsSuccess=True))
        window._gk2mt_callbacks = (ready, message, navigate, popup, cancel, loaded)

    def closing():
        if not app.LOCK.acquire(blocking=False):
            from System.Windows.Forms import MessageBox
            MessageBox.Show('GK2MT is finishing an operation. Please wait for it to finish before closing.', 'GK2MT')
            return False
        try:
            bridge._closing = True
            app.shutdown()
        finally:
            app.LOCK.release()
        return True

    window.events.before_show += attach
    window.events.closing += closing
    try:
        webview.start((lambda: started(window)) if started else None, gui='edgechromium',
                      http_server=False, private_mode=True, debug=False)
    finally:
        with app.LOCK:
            bridge._closing = True
            app.shutdown()
        app.WINDOW = None
