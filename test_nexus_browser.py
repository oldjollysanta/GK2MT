"""Offline browsing/download/install checks; dummy credentials and temporary game files only."""
import json
import shutil
import zipfile
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
import integrations
import manager
from test_downloads import Panel


def archive(path, files):
    with zipfile.ZipFile(path, 'w') as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
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
        release = archive(root / 'release.zip', {
            'BepInEx/plugins/Example.dll': b'new release',
            'BepInEx/config/example.cfg': b'default settings'})
        with patch.object(app, 'DATA', root / 'data'), patch.object(app, 'DOWNLOADS', {}), \
             patch.object(app, 'NEXUS_KEY', 'dummy-nexus-browser-key'), patch.object(app, 'NEXUS_SESSION', 7), \
             patch.object(app, 'UPDATES', None), patch.object(app, 'UPDATE_ARCHIVES', {}), \
             patch.object(app, 'PREVIEW', None), patch.object(manager, 'ensure_game_stopped'), \
             patch.object(app.nexus_panel, 'availability', return_value={'available': True, 'message': ''}), \
             patch.object(app.nexus_panel, 'launch', return_value=panel) as launch, \
             patch.object(app.threading, 'Thread'), \
             patch.object(integrations, 'nexus_archive_release', return_value={
                 'nexus_mod_id': 10, 'file_id': 2, 'name': 'Example', 'version': '2.0', 'category_id': 3}) as verify, \
             patch.object(integrations, '_json', return_value={
                 'categories': [{'category_id': 3, 'name': 'Gameplay'}]}):
            manager.save_json(app.DATA / 'settings.json', config)
            yield {'root': root, 'game': game, 'config': config, 'release': release,
                   'target': game / 'BepInEx/plugins/Example.dll', 'panel': panel,
                   'launch': launch, 'verify': verify}


def start(data):
    public = app.dispatch('nexus-browser', {})
    assert public['kind'] == 'install' and public['update_id'] is None
    job = app.DOWNLOADS[public['id']]
    assert job['_folder'].parent == app.DATA / 'nexus-downloads'
    data['launch'].assert_called_with(job['_folder'], 0, 0)
    assert 'dummy-nexus-browser-key' not in json.dumps(app.download_state())
    return job


def downloaded(data, job, mod_id=10):
    shutil.copyfile(data['release'], job['_folder'] / 'download.zip')
    manager.save_json(job['_folder'] / 'panel-status.json', {
        'state': 'downloaded', 'mod_id': mod_id, 'filename': '../../unrelated.zip'})


def rejects(action, message):
    try:
        action()
        raise AssertionError('Expected rejection: ' + message)
    except ValueError as error:
        assert message in str(error), str(error)


def main():
    with fixture() as data:
        personal = data['game'] / 'BepInEx/config/example.cfg'
        personal.parent.mkdir(parents=True)
        personal.write_bytes(b'personal settings')
        job = start(data)
        rejects(lambda: app.dispatch('nexus-browser', {}), 'Finish or cancel')
        downloaded(data, job)
        app.advance_download(job)
        assert job['state'] == 'completed', job['message']
        rows = [row for row in app.snapshot()['mods'] if row.get('nexus_mod_id') == 10]
        assert len(rows) == 1 and rows[0]['source'] == 'GK2MT'
        assert rows[0]['name'] == 'Example' and rows[0]['version'] == '2.0' and rows[0]['file_id'] == 2
        assert rows[0]['category'] == 'Gameplay'
        assert data['target'].read_bytes() == b'new release' and personal.read_bytes() == b'personal settings'
        assert 'BepInEx/config/example.cfg' not in rows[0]['paths']
        assert data['panel'].poll() == -1
        data['verify'].assert_called_once()
        assert data['verify'].call_args.kwargs.get('mod_id') == 10

    with fixture() as data:
        library = app.package_manager(data['config'])
        old = archive(data['root'] / 'old.zip', {'BepInEx/plugins/Example.dll': b'old release'})
        package = library.install(library.stage(old)['token'], {
            'name': 'Example', 'version': '1.0', 'nexus_mod_id': 10, 'file_id': 1})
        library.set_enabled(package['id'], False)
        job = start(data)
        downloaded(data, job, mod_id=None)
        app.advance_download(job)
        assert job['state'] == 'completed', job['message']
        rows = [row for row in app.snapshot()['mods'] if row.get('nexus_mod_id') == 10]
        assert len(rows) == 1 and rows[0]['id'] == package['id'] and not rows[0]['enabled']
        assert rows[0]['version'] == '2.0' and rows[0]['file_id'] == 2 and not data['target'].exists()
        updated = app.package_manager(data['config']).state['packages'][0]
        assert (Path(updated['folder']) / 'BepInEx/plugins/Example.dll').read_bytes() == b'new release'

    with fixture() as data:
        library = app.package_manager(data['config'])
        local_settings = ('BepInEx/plugins/Example/preferences.cfg', 'BepInEx/plugins/Example/preferences.ini')
        old_files = {'BepInEx/plugins/Example/Example.dll': b'old release'}
        old_files.update({path: b'old defaults' for path in local_settings})
        package = library.install(library.stage(archive(data['root'] / 'old.zip', old_files))['token'], {
            'name': 'Example', 'version': '1.0', 'nexus_mod_id': 10})
        for path in local_settings:
            (data['game'] / path).write_bytes(b'personal settings')
        new_files = {'BepInEx/plugins/Example/Example.dll': b'new release'}
        new_files.update({path: b'new defaults' for path in local_settings})
        archive(data['release'], new_files)
        job = start(data)
        downloaded(data, job)
        app.advance_download(job)
        assert job['state'] == 'completed', job['message']
        state = app.package_manager(data['config']).state
        assert len(state['packages']) == 1 and state['packages'][0]['id'] == package['id']
        assert (data['game'] / 'BepInEx/plugins/Example/Example.dll').read_bytes() == b'new release'
        for path in local_settings:
            assert (data['game'] / path).read_bytes() == b'personal settings'
            assert path not in state['packages'][0]['paths'] and path.casefold() not in state['deployed']

    for conflict in ('disabled-manual', 'workshop-directory'):
        with fixture() as data:
            library = app.package_manager(data['config'])
            old = archive(data['root'] / 'old.zip', {'BepInEx/plugins/Example.dll': b'old release'})
            library.install(library.stage(old)['token'], {'nexus_mod_id': 10, 'version': '1.0'})
            extra = ('BepInEx/plugins/Other.dll' if conflict == 'disabled-manual'
                     else 'BepInEx/plugins/_Workshop/123/Other.dll')
            disabled = data['game'] / (extra + '.gk2mt-disabled')
            if conflict == 'disabled-manual':
                disabled.write_bytes(b'unrelated disabled mod')
            archive(data['release'], {'BepInEx/plugins/Example.dll': b'new release', extra: b'extra DLL'})
            before = library.state_path.read_bytes()
            job = start(data)
            downloaded(data, job)
            app.advance_download(job)
            assert job['state'] == 'error', conflict
            assert library.state_path.read_bytes() == before
            assert data['target'].read_bytes() == b'old release' and not (data['game'] / extra).exists()
            if conflict == 'disabled-manual':
                assert disabled.read_bytes() == b'unrelated disabled mod'

    for conflict in ('physical', 'disabled-owned'):
        with fixture() as data:
            library = app.package_manager(data['config'])
            if conflict == 'physical':
                data['target'].parent.mkdir(parents=True)
                data['target'].write_bytes(b'unrelated mod')
            else:
                old = archive(data['root'] / 'other.zip', {'BepInEx/plugins/Example.dll': b'another package'})
                other = library.install(library.stage(old)['token'], {'nexus_mod_id': 11, 'version': '1.0'})
                library.set_enabled(other['id'], False)
            before = library.state_path.read_bytes() if library.state_path.exists() else None
            job = start(data)
            downloaded(data, job)
            app.advance_download(job)
            assert job['state'] == 'error', conflict
            assert (library.state_path.read_bytes() if library.state_path.exists() else None) == before
            assert data['target'].read_bytes() == b'unrelated mod' if conflict == 'physical' else not data['target'].exists()

    for reason in ('ZIP does not match the selected Nexus mod.', 'Nexus archive identity is ambiguous.'):
        with fixture() as data:
            job = start(data)
            downloaded(data, job)
            data['verify'].side_effect = ValueError(reason)
            app.advance_download(job)
            assert job['state'] == 'error' and reason in job['message']
            assert not data['target'].exists() and not app.package_manager(data['config']).state['packages']

    with fixture() as data:
        job = start(data)
        downloaded(data, job)
        verified_release = dict(data['verify'].return_value)
        def replace_verified_zip(key, path, **kwargs):
            archive(path, {'BepInEx/plugins/Example.dll': b'unverified replacement'})
            return verified_release
        data['verify'].side_effect = replace_verified_zip
        app.advance_download(job)
        assert job['state'] == 'error', 'A replaced ZIP must not be installed under verified Nexus metadata.'
        assert not data['target'].exists() and not app.package_manager(data['config']).state['packages']

    with fixture() as data:
        job = start(data)
        (job['_folder'] / 'download.zip.part').write_bytes(b'unfinished')
        manager.save_json(job['_folder'] / 'panel-status.json', {'state': 'downloading', 'received': 1, 'total': 20})
        app.advance_download(job)
        assert job['state'] == 'downloading' and not data['target'].exists()
        data['verify'].assert_not_called()
        downloaded(data, job)
        app.advance_download(job)
        assert job['state'] == 'error' and not data['target'].exists()
        data['verify'].assert_not_called()

    for change in ('session', 'game', 'cancel'):
        with fixture() as data:
            job = start(data)
            downloaded(data, job)
            if change == 'session':
                app.NEXUS_SESSION += 1
            elif change == 'game':
                manager.save_json(app.DATA / 'settings.json', data['config'] | {'game': str(data['root'] / 'another-game')})
            else:
                app.dispatch('download-panel-cancel', {'id': job['id']})
            app.advance_download(job)
            assert job['state'] == ('cancelled' if change == 'cancel' else 'error')
            assert not data['target'].exists()
            data['verify'].assert_not_called()

    with fixture() as data:
        job = start(data)
        downloaded(data, job)
        with patch.object(manager, 'ensure_game_stopped', side_effect=ValueError('Close Graveyard Keeper 2')):
            app.advance_download(job)
        assert job['state'] == 'waiting_game' and job['retryable'] and job['cancellable']
        assert not data['target'].exists()
        rejects(lambda: app.dispatch('nexus-browser', {}), 'Finish or cancel')
        app.dispatch('download-panel-retry', {'id': job['id']})
        assert job['state'] == 'completed' and data['target'].read_bytes() == b'new release'

    with fixture() as data, patch.object(app, 'NEXUS_KEY', ''):
        rejects(lambda: app.dispatch('nexus-browser', {}), 'Connect Nexus')
        data['launch'].assert_not_called()
        assert not app.DOWNLOADS
    print('Nexus browser pickup passed: installs, preserved settings, disabled/reserved collisions, archive identity and session guards.')


if __name__ == '__main__':
    main()
