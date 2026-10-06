"""Offline Nexus panel pickup checks; all downloads and game files are temporary."""
import copy
import json
import shutil
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
import desktop
import integrations
import manager


class Panel:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -1


def zip_file(path, contents):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('BepInEx/plugins/Example.dll', contents)
    return path


@contextmanager
def fixture():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        game, workshop = root / 'game', root / '4358690'
        game.mkdir()
        workshop.mkdir()
        (game / 'GraveyardKeeper2.exe').touch()
        config = {'game': str(game), 'workshop': str(workshop)}
        panel = Panel()
        with patch.object(app, 'DATA', root / 'data'), patch.object(app, 'DOWNLOADS', {}), \
             patch.object(app, 'NEXUS_KEY', 'secret-free-account-key'), patch.object(app, 'NEXUS_SESSION', 0), \
             patch.object(app, 'UPDATES', None), patch.object(app, 'UPDATE_ARCHIVES', {}), \
             patch.object(app, 'PREVIEW', None), patch.object(manager, 'ensure_game_stopped'), \
             patch.object(app.nexus_panel, 'availability', return_value={'available': True, 'message': ''}), \
             patch.object(app.nexus_panel, 'launch', return_value=panel) as launch, \
             patch.object(app.threading, 'Thread'):
            manager.save_json(app.DATA / 'settings.json', config)
            library = app.package_manager(config)
            package = library.install(library.stage(zip_file(root / 'old.zip', 'v1'))['token'],
                                      {'name': 'Example', 'version': '1.0', 'nexus_mod_id': 10})
            target = game / 'BepInEx/plugins/Example.dll'
            release = zip_file(root / 'release.zip', 'v2')
            result = {'account': {'premium': False}, 'categories': {}, 'updates': [
                {'id': package['id'], 'name': 'Example', 'nexus_mod_id': 10, 'file_id': 2,
                 'version': '2.0', 'known_version': True, 'downloadable': False, 'status': 'Update available'}]}
            with patch.object(integrations, 'nexus_check', side_effect=lambda *a: copy.deepcopy(result)), \
                 patch.object(integrations, 'nexus_archive_release', return_value={'version': '2.0'}) as verify:
                app.dispatch('updates', {})
                yield {'root': root, 'config': config, 'id': package['id'], 'target': target,
                       'old_payload': Path(package['folder']) / 'BepInEx/plugins/Example.dll',
                       'release': release, 'panel': panel, 'launch': launch, 'verify': verify}


def start(data):
    public = app.dispatch('download-panel', {'id': data['id']})
    return app.DOWNLOADS[public['id']]


def downloaded(data, job):
    shutil.copyfile(data['release'], job['_folder'] / 'download.zip')
    manager.save_json(job['_folder'] / 'panel-status.json', {'state': 'downloaded', 'filename': '../../unrelated.zip'})


def rejects(action, expected):
    try:
        action()
        raise AssertionError('Expected rejection: ' + expected)
    except ValueError as exc:
        assert expected in str(exc), str(exc)


def main():
    with fixture() as data:
        job = start(data)
        assert job['_folder'].parent == app.DATA / 'nexus-downloads'
        assert 'Downloads' not in job['_folder'].relative_to(app.DATA).parts
        data['launch'].assert_called_once_with(job['_folder'], 10, 2)
        serialized = json.dumps(app.download_state())
        assert 'secret-free-account-key' not in serialized and '_signature' not in serialized and '_process' not in serialized
        assert app.snapshot()['downloads'][0]['update_id'] == data['id']
        rejects(lambda: start(data), 'Finish or cancel')
        # A partial file or a ZIP placed in the folder by hand is never picked up.
        (job['_folder'] / 'download.zip.part').write_bytes(data['release'].read_bytes())
        shutil.copyfile(data['release'], job['_folder'] / 'unrelated.zip')
        manager.save_json(job['_folder'] / 'panel-status.json', {'state': 'downloading', 'received': 10, 'total': 100})
        app.advance_download(job)
        assert job['state'] == 'downloading' and job['received'] == 10
        assert data['target'].read_text() == 'v1'
        data['verify'].assert_not_called()
        (job['_folder'] / 'download.zip.part').unlink()
        downloaded(data, job)
        app.advance_download(job)
        assert job['state'] == 'completed' and not job['cancellable'] and not job['retryable']
        assert data['target'].read_text() == 'v2' and data['panel'].poll() == -1
        assert not app.UPDATES['updates']
        library = app.package_manager(data['config'])
        assert library.state['packages'][0]['version'] == '2.0'
        assert data['old_payload'].read_text() == 'v1'
        data['verify'].assert_called_once()

    for change in ('game', 'connection', 'update', 'files'):
        with fixture() as data:
            job = start(data)
            downloaded(data, job)
            if change == 'game':
                manager.save_json(app.DATA / 'settings.json', data['config'] | {'game': str(data['root'] / 'other-game')})
            elif change == 'connection':
                app.dispatch('nexus-forget', {})
            elif change == 'update':
                app.UPDATES['updates'][0]['file_id'] = 3
            else:
                data['target'].write_text('external change')
            app.advance_download(job)
            assert job['state'] == 'error' and not job['retryable'], (change, job['message'])
            assert data['target'].read_text() == ('external change' if change == 'files' else 'v1')
            data['verify'].assert_not_called()

    with fixture() as data:
        job = start(data)
        downloaded(data, job)
        with patch.object(manager, 'ensure_game_stopped', side_effect=ValueError('Close Graveyard Keeper 2')):
            app.advance_download(job)
        assert job['state'] == 'waiting_game' and job['retryable'] and job['cancellable']
        assert data['target'].read_text() == 'v1'
        data['verify'].assert_not_called()
        app.dispatch('download-panel-retry', {'id': job['id']})
        assert job['state'] == 'completed' and data['target'].read_text() == 'v2'

    with fixture() as data:
        job = start(data)
        downloaded(data, job)
        data['verify'].side_effect = ValueError('ZIP is not the selected Nexus update.')
        app.advance_download(job)
        assert job['state'] == 'error' and job['retryable'] and 'selected Nexus update' in job['message']
        assert data['target'].read_text() == 'v1'
        data['verify'].side_effect = None
        app.dispatch('download-panel-retry', {'id': job['id']})
        assert job['state'] == 'completed' and data['verify'].call_count == 2

    for changed_session in (False, True):
        with fixture() as data:
            job = start(data)
            downloaded(data, job)
            library = app.package_manager(data['config'])
            original_state = library.state_path.read_bytes()
            retained = job['_folder'] / 'download.zip'
            retained_hash = manager.digest(retained)
            with patch.object(manager.Manager, 'deploy', side_effect=ValueError('Fixture deployment blocked')):
                app.advance_download(job)
            assert job['state'] == 'error' and job['retryable'], job['message']
            assert 'deployment blocked' in job['message']
            assert data['verify'].call_count == 1, 'The complete ZIP passed Nexus verification before deployment.'
            assert data['target'].read_text() == 'v1' and library.state_path.read_bytes() == original_state
            assert retained.is_file() and manager.digest(retained) == retained_hash
            if changed_session:
                app.NEXUS_SESSION += 1
            public = app.dispatch('download-panel-retry', {'id': job['id']})
            if changed_session:
                assert public['state'] == 'error' and not public['retryable'], public
                assert data['target'].read_text() == 'v1'
                assert data['verify'].call_count == 1, 'Retry checks the Nexus session before re-verifying or writing.'
            else:
                assert public['state'] == 'completed' and not public['retryable'], public
                assert data['target'].read_text() == 'v2'
                assert data['verify'].call_count == 2, 'Retry re-verifies the retained ZIP.'
            data['launch'].assert_called_once()

    for ending in ('cancel', 'closed', 'incomplete'):
        with fixture() as data:
            job = start(data)
            downloaded(data, job)
            if ending == 'cancel':
                app.dispatch('download-panel-cancel', {'id': job['id']})
            elif ending == 'closed':
                manager.save_json(job['_folder'] / 'panel-status.json', {'state': 'closed'})
            else:
                (job['_folder'] / 'download.zip.part').touch()
            app.advance_download(job)
            assert job['state'] in ('cancelled', 'closed', 'error') and not job['cancellable']
            assert data['target'].read_text() == 'v1'
            data['verify'].assert_not_called()

    # A watcher queued behind an operation must not resume after window shutdown.
    stopped = threading.Event()
    stopped.set()
    with patch.object(stopped, 'wait', return_value=False), patch.object(app, 'advance_download') as advance:
        app.watch_download({'_stop': stopped})
        advance.assert_not_called()

    # The desktop bridge can read progress while an installation holds the mutation lock.
    with patch.object(app, 'DOWNLOADS', {}), \
         patch.object(app.nexus_panel, 'availability', return_value={'available': True, 'message': ''}):
        bridge = desktop.Bridge(app)
        responses = []
        thread = threading.Thread(target=lambda: responses.append(bridge.request('downloads', None)), daemon=True)
        with app.LOCK:
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive(), 'Installation blocked progress polling'
        assert responses[0]['ok'] is True and responses[0]['result']['downloads'] == []
        bridge._closing = True
        assert bridge.request('downloads', None)['ok'] is False
    print('Download pickup passed: dedicated folder, completed-only install, identity/session guards, retry, cancel and desktop progress.')


if __name__ == '__main__':
    main()
