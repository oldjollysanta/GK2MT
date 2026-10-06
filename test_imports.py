"""Repeat-import and duplicate-review checks; temporary files and mocked Nexus only."""
import copy
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
import manager
from test_manager import archive, setup
from test_nexus_browser import fixture, start, downloaded


def rejects(action, message):
    try:
        action()
    except ValueError as error:
        assert message in str(error), str(error)
        return
    raise AssertionError('Expected rejection: ' + message)


def repeat_imports(root):
    library, game = setup(root)
    files = {'BepInEx/plugins/Example.dll': 'same DLL', 'BepInEx/config/example.cfg': 'defaults'}
    first = archive(root, 'First', files)
    package = library.install(library.stage(first)['token'], {'name': 'Original name', 'version': '1.0'})
    library.set_enabled(package['id'], False)
    before, disk = copy.deepcopy(library.state), library.state_path.read_bytes()
    game_files = {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
    rezipped = root / 'Renamed-and-rezipped.zip'
    with zipfile.ZipFile(rezipped, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for relative, content in reversed(list(files.items())):
            zipped.writestr(relative, content)
    assert manager.digest(first) != manager.digest(rezipped)
    for source in (first, rezipped):
        staged = library.stage(source)
        assert staged['status'] == 'already_installed' and staged['conflicts'] == []
        assert staged['existing'] == {'id': package['id'], 'name': 'Original name', 'enabled': False}
        result = library.install(staged['token'], {'name': 'Unwanted rename', 'version': '9.0'})
        assert result['import_status'] == 'already_installed' and result['id'] == package['id']
        assert not result['enabled'] and library.state == before and library.state_path.read_bytes() == disk
        assert game_files == {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
    assert library.conflicts() == []
    # Older libraries without fingerprints still compare their cached mapped bytes.
    for key in ('archive_sha256', 'payload_sha256'):
        library.state['packages'][0].pop(key)
    assert library.stage(rezipped)['status'] == 'already_installed'
    changed = library.stage(archive(root, 'Changed', {'BepInEx/plugins/Example.dll': 'new DLL'}))
    assert changed['status'] == 'ready'
    updated = library.update(changed['token'], library.state['packages'][0], {'version': '2.0'})
    assert updated['id'] == package['id'] and updated['version'] == '2.0' and not updated['enabled']
    assert library.stage(root / 'Changed.zip')['status'] == 'already_installed'
    rejects(lambda: library.stage(archive(root, 'Not-a-mod', {'holiday.txt': 'hello'})), 'No DLL mod payload')


def steam_copy(data, enabled=False):
    payload = Path(data['config']['workshop']) / '123/BepInEx/plugins/Example.dll'
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b'BepInPlugin\0 Steam copy')
    return {'id': 'workshop:123', 'name': 'Steam Example', 'enabled': enabled,
            'source': 'Steam Workshop', 'workshop_id': '123', 'nexus_mod_id': 10,
            'paths': [], 'workshop_paths': ['123/BepInEx/plugins/Example.dll']}


def import_review():
    with fixture() as data:
        row = steam_copy(data)
        actual_snapshot = app.snapshot
        def with_steam():
            value = actual_snapshot()
            value['mods'] = [mod for mod in value['mods'] if mod['id'] != row['id']] + [copy.deepcopy(row)]
            return value
        with patch.object(app, 'snapshot', side_effect=with_steam):
            staged = app.dispatch('stage', {'path': str(data['release'])})
            assert staged['requires_duplicate_ack'] and staged['duplicates'][0]['id'] == row['id']
            assert staged['duplicates'][0]['source'] == 'Steam' and not staged['duplicates'][0]['enabled']
            rejects(lambda: app.dispatch('install', {'token': staged['token']}), 'acknowledge')
            assert not data['target'].exists()
            row['enabled'] = True
            rejects(lambda: app.dispatch('install', {'token': staged['token'], 'acknowledge_duplicates': True}), 'changed')
            staged = app.dispatch('stage', {'path': str(data['release'])})
            result = app.dispatch('install', {'token': staged['token'], 'acknowledge_duplicates': True})
            assert result['status'] == 'installed' and data['target'].exists()
            staged = app.dispatch('stage', {'path': str(data['release'])})
            assert staged['status'] == 'already_installed' and not staged['requires_duplicate_ack']
            assert app.dispatch('install', {'token': staged['token']})['status'] == 'already_installed'
            assert len(app.package_manager(data['config']).state['packages']) == 1


def bulk_repeat():
    with fixture() as data:
        folder = data['root'] / 'imports'
        folder.mkdir()
        for filename in ('One.zip', 'Two.zip'):
            (folder / filename).write_bytes(data['release'].read_bytes())
        manager.save_json(app.DATA / 'settings.json', data['config'] | {'import_folder': str(folder)})
        preview = app.dispatch('stage-folder', {})
        results = [app.dispatch('install', {'token': package['token']}) for package in preview['packages']]
        assert [result['status'] for result in results] == ['installed', 'already_installed']
        again = app.dispatch('stage-folder', {})
        assert all(package['status'] == 'already_installed' for package in again['packages'])
        library = app.package_manager(data['config'])
        assert len(library.state['packages']) == 1 and not library.conflicts()


def browser_duplicate_review():
    with fixture() as data:
        row = steam_copy(data)
        actual_snapshot = app.snapshot
        def with_steam():
            value = actual_snapshot()
            value['mods'] = [mod for mod in value['mods'] if mod['id'] != row['id']] + [copy.deepcopy(row)]
            return value
        with patch.object(app, 'snapshot', side_effect=with_steam):
            job = start(data)
            downloaded(data, job)
            app.advance_download(job)
            public = app.download_job(job)
            assert job['state'] == 'awaiting_review' and public['review_required'] and public['duplicates']
            assert job['cancellable'] and not job['retryable'] and app.active_download() is job
            assert not data['target'].exists() and not app.package_manager(data['config']).state['packages']
            assert 'dummy-nexus-browser-key' not in json.dumps(public)
            rejects(lambda: app.dispatch('download-panel-approve', {'id': job['id']}), 'Acknowledge')
            row['enabled'] = True
            result = app.dispatch('download-panel-approve', {'id': job['id'], 'acknowledge_duplicates': True})
            assert result['state'] == 'awaiting_review' and not data['target'].exists()
            result = app.dispatch('download-panel-approve', {'id': job['id'], 'acknowledge_duplicates': True})
            assert result['state'] == 'completed' and data['target'].read_bytes() == b'new release'
            assert len(app.package_manager(data['config']).state['packages']) == 1
    with fixture() as data:
        row = steam_copy(data)
        with patch.object(app, 'import_duplicates', return_value=[row]):
            job = start(data)
            downloaded(data, job)
            app.advance_download(job)
            app.dispatch('download-panel-cancel', {'id': job['id']})
            rejects(lambda: app.dispatch('download-panel-approve', {'id': job['id'], 'acknowledge_duplicates': True}), 'no pending')
            assert not data['target'].exists()


def main():
    with patch.object(manager, 'ensure_game_stopped'):
        with TemporaryDirectory() as temporary:
            repeat_imports(Path(temporary))
        import_review()
        bulk_repeat()
        browser_duplicate_review()
    print('Imports passed: identical/re-zipped/disabled/legacy packages, bulk repeats, safe ZIP validation, duplicate review and cancellation.')


if __name__ == '__main__':
    main()
