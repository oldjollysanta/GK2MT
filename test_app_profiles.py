"""Offline profile actions/download integration; temporary data and dummy API responses only."""
import copy
import hashlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
import integrations
import inventory
import manager
import profiles
from test_downloads import Panel
from test_manager import archive, put


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def rejects(action, fragment=''):
    try:
        action()
    except ValueError as error:
        assert fragment in str(error), str(error)
        return
    raise AssertionError('Expected rejection: ' + fragment)


def manifest(entries=(), name='Shared selection'):
    return {'format': profiles.FORMAT, 'schema_version': 1, 'game_id': 4358690,
            'name': name, 'mods': list(entries)}


def nexus(**values):
    return {'name': 'Example', 'source': 'Nexus', 'nexus_mod_id': 20,
            'file_id': 21, 'version': '1.0', 'enabled': True} | values


@contextmanager
def fixture():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        game, workshop = root / 'game', root / '4358690'
        put(game, 'GraveyardKeeper2.exe', 'test game')
        workshop.mkdir()
        config = {'game': str(game), 'workshop': str(workshop),
                  'deck': {'host': 'private-deck-address'}, 'private_setting': 'dummy-settings-secret'}
        release = archive(root, 'release', {'BepInEx/plugins/Example.dll': 'profile release'})
        data = {'root': root, 'game': game, 'workshop': workshop, 'config': config, 'release': release,
                'files': [{'file_id': 21, 'version': '1.0', 'file_name': 'Example-1.zip', 'category_id': 1}],
                'matches': [], 'panel': Panel()}
        checksum = hashlib.md5(release.read_bytes()).hexdigest()
        data['matches'] = [{'mod': {'mod_id': 20, 'domain_name': integrations.GAME, 'name': 'Example'},
                            'file_details': {'file_id': 21, 'version': '1.0', 'size_in_bytes': release.stat().st_size}}]
        def api(url, key):
            assert key == 'dummy-profile-key'
            if '/mods/md5_search/' in url:
                assert checksum in url
                return copy.deepcopy(data['matches'])
            if url.endswith('/mods/20/files.json'):
                return {'files': copy.deepcopy(data['files']), 'file_updates': []}
            if url.endswith('/mods/20.json'):
                return {'name': 'Example API title', 'version': '1.0', 'category_id': 3, 'status': 'published'}
            if url.endswith('/games/' + integrations.GAME + '.json'):
                return {'categories': [{'category_id': 3, 'name': 'Gameplay'}]}
            raise AssertionError('Unexpected API URL: ' + url)
        with patch.object(app, 'DATA', root / 'data'), patch.object(app, 'NEXUS_KEY', 'dummy-profile-key'), \
             patch.object(app, 'NEXUS_SESSION', 8), patch.object(app, 'NEXUS_ERROR', ''), \
             patch.object(app, 'DOWNLOADS', {}), patch.object(app, 'UPDATES', None), \
             patch.object(app, 'UPDATE_ARCHIVES', {}), patch.object(app, 'PREVIEW', None), \
             patch.object(app, 'PROFILE_PREVIEW', None), patch.object(app, 'DECK_SESSION', None), \
             patch.object(manager, 'ensure_game_stopped'), patch.object(app.threading, 'Thread'), \
             patch.object(app.nexus_panel, 'availability', return_value={'available': True, 'message': ''}), \
             patch.object(app.nexus_panel, 'launch', return_value=data['panel']) as launch, \
             patch.object(integrations, '_account', return_value={'name': 'Fixture', 'premium': False}) as account, \
             patch.object(integrations, '_json', side_effect=api) as api_mock, \
             patch.object(integrations, 'nexus_download', return_value=release) as direct:
            manager.save_json(app.DATA / 'settings.json', config)
            data.update(launch=launch, account=account, api=api_mock, direct=direct)
            yield data


def managed(data, name, enabled=True, nexus_id=None, version='1.0', file_id=21):
    library = app.package_manager(data['config'])
    metadata = {'name': name, 'version': version}
    if nexus_id:
        metadata.update(nexus_mod_id=nexus_id, file_id=file_id)
    pkg = library.install(library.stage(archive(data['root'], name,
        {f'BepInEx/plugins/{name}.dll': name + ' plugin'}))['token'], metadata)
    if not enabled:
        library.set_enabled(pkg['id'], False)
    return pkg


def store(data, entries=(), name='Shared selection'):
    path, _ = app.profile_store(data['config'])
    identifier = 'a' * 32
    manager.save_json(path, {'profiles': [{'id': identifier, 'profile': profiles.validate(manifest(entries, name))}],
                             'active': None})
    return identifier


def change_entry(data, identifier, update):
    path, value = app.profile_store(data['config'])
    record = next(p for p in value['profiles'] if p['id'] == identifier)
    record['profile']['mods'][0].update(update)
    manager.save_json(path, value)


def start(data, identifier):
    key = app.profile_record(data['config'], identifier)['mods'][0]['key']
    public = app.dispatch('profile-download', {'id': identifier, 'key': key})
    return app.DOWNLOADS[public['id']]


def complete(data, job):
    shutil.copyfile(data['release'], job['_folder'] / 'download.zip')
    manager.save_json(job['_folder'] / 'panel-status.json', {'state': 'downloaded', 'mod_id': 999,
                                                           'filename': '../ignored.zip'})
    app.advance_download(job)


def metadata_actions_and_library_scope():
    with fixture() as data:
        pkg = managed(data, 'Local', enabled=False)
        before = files(data['game'])
        saved = app.dispatch('profile-save', {'name': 'PC selection'})
        identifier = saved['profile']['id']
        profile = app.profile_record(data['config'], identifier)
        entry = profile['mods'][0]
        assert entry['source'] == 'Manual' and not entry['enabled']
        payload = Path(pkg['folder']) / 'BepInEx/plugins/Local.dll'
        assert entry['dlls'][0]['sha256'] == manager.digest(payload)
        app.dispatch('profile-save', {'id': identifier, 'name': 'Renamed selection'})
        assert len(app.profile_store(data['config'])[1]['profiles']) == 1
        assert app.profile_record(data['config'], identifier)['name'] == 'Renamed selection'
        exported = data['root'] / 'share'
        with patch.object(app, 'file_dialog', return_value={'path': str(exported)}):
            app.dispatch('profile-export', {'id': identifier})
        export = Path(str(exported) + '.json')
        encoded = export.read_text('utf-8')
        assert not any(text in encoded for text in ('dummy-', 'private-deck', str(data['game']), 'rules', 'folder'))
        raw = json.loads(encoded) | {'password': 'dummy-extra-secret', 'settings': {'host': 'private-other'}}
        raw['mods'][0]['paths'] = ['C:/private/DLL.dll']
        manager.save_json(export, raw)
        with patch.object(app, 'file_dialog', return_value={'path': str(export)}):
            imported = app.dispatch('profile-import', {})['profile']['id']
        assert app.profile_record(data['config'], imported) == profiles.validate(raw)
        assert not any(text in json.dumps(app.profile_record(data['config'], imported)) for text in ('dummy-', 'private'))
        other = data['root'] / 'other-game'
        put(other, 'GraveyardKeeper2.exe', 'other game')
        other_config = data['config'] | {'game': str(other)}
        assert not app.profile_store(other_config)[1]['profiles']
        rejects(lambda: app.profile_record(other_config, identifier), 'no longer exists')
        app.dispatch('profile-delete', {'id': identifier})
        app.dispatch('profile-delete', {'id': imported})
        assert not app.profile_store(data['config'])[1]['profiles'] and files(data['game']) == before
        with patch.object(app, 'file_dialog', return_value={'path': ''}):
            assert app.dispatch('profile-import', {})['profile'] is None


def exact_selection_and_protected_foundation():
    with fixture() as data:
        local = put(data['game'], 'BepInEx/plugins/Local.dll', 'local plugin')
        hidden = managed(data, 'Hidden', enabled=False)
        known = managed(data, 'Nexus', nexus_id=30)
        protected = put(data['game'], 'BepInEx/plugins/Foundation.dll', 'protected foundation')
        loader = put(data['game'], 'BepInEx/patchers/GK2_WorkshopLoader.dll', 'loader')
        config = data['config'] | {'metadata': {'plugins:foundation.dll': {'nexus_mod_id': 48, 'version': '5.4.23'}}}
        manager.save_json(app.DATA / 'settings.json', config)
        identifier = app.dispatch('profile-save', {'name': 'Exact selection'})['profile']['id']
        path, value = app.profile_store(config)
        entries = value['profiles'][0]['profile']['mods']
        assert len(entries) == 3 and not any(e.get('nexus_mod_id') == 48 for e in entries)
        for entry in entries:
            entry['enabled'] = entry['name'] == 'Hidden'
        manager.save_json(path, value)
        reviewed = app.dispatch('profile-compare', {'id': identifier})
        assert not reviewed['blockers'] and len(reviewed['changes']) == 3
        app.dispatch('profile-apply', {'id': identifier, 'token': reviewed['token']})
        assert not local.exists() and Path(str(local) + inventory.DISABLED).read_text() == 'local plugin'
        state = app.package_manager(config).state
        assert next(p for p in state['packages'] if p['id'] == hidden['id'])['enabled']
        assert not next(p for p in state['packages'] if p['id'] == known['id'])['enabled']
        assert protected.read_text() == 'protected foundation' and loader.read_text() == 'loader'
        assert app.profile_store(config)[1]['active'] == identifier
        rejects(lambda: app.dispatch('profile-apply', {'id': identifier, 'token': reviewed['token']}), 'Review')


def stale_and_running_guards():
    for kind in ('token', 'profile', 'file', 'disabled-payload', 'running'):
        with fixture() as data:
            pkg = managed(data, 'Example', enabled=kind != 'disabled-payload', nexus_id=20)
            identifier = app.dispatch('profile-save', {'name': 'Guarded'})['profile']['id']
            change_entry(data, identifier, {'enabled': kind == 'disabled-payload'})
            reviewed = app.dispatch('profile-compare', {'id': identifier})
            body = {'id': identifier, 'token': reviewed['token']}
            if kind == 'token':
                body['token'] = 'invalid token'
            elif kind == 'profile':
                change_entry(data, identifier, {'name': 'Changed after review'})
            elif kind == 'file':
                (data['game'] / 'BepInEx/plugins/Example.dll').write_text('changed plugin')
            elif kind == 'disabled-payload':
                (Path(pkg['folder']) / 'BepInEx/plugins/Example.dll').write_text('changed staged plugin')
            before, state = files(data['game']), copy.deepcopy(app.package_manager(data['config']).state)
            if kind == 'running':
                with patch.object(manager, 'ensure_game_stopped', side_effect=ValueError('Close Graveyard Keeper 2')):
                    rejects(lambda: app.dispatch('profile-apply', body), 'Close')
            else:
                rejects(lambda: app.dispatch('profile-apply', body))
            assert files(data['game']) == before and app.package_manager(data['config']).state == state


def workshop_and_mixed_rollback():
    for failure in ('managed-copy', 'profile-save'):
        with fixture() as data:
            local = put(data['game'], 'BepInEx/plugins/Manual.dll', 'manual plugin')
            pkg = managed(data, 'Nexus', enabled=False, nexus_id=30)
            put(data['game'], 'BepInEx/patchers/GK2_WorkshopLoader.dll', 'loader')
            steam_bytes = b'BepInPlugin\0 Steam plugin'
            put(data['workshop'] / '123', 'Steam.dll', 'placeholder').write_bytes(steam_bytes)
            put(data['game'], 'BepInEx/plugins/_Workshop/123/Steam.dll', 'placeholder').write_bytes(steam_bytes)
            trust = put(data['game'], inventory.TRUST, '# preserve comment\n123 = ' + 'a' * 64 + ' # approved\n')
            trust.write_bytes(trust.read_bytes().replace(b'\n', b'\r\n'))
            identifier = app.dispatch('profile-save', {'name': 'Transactional'})['profile']['id']
            path, value = app.profile_store(data['config'])
            for entry in value['profiles'][0]['profile']['mods']:
                entry['enabled'] = entry['source'] == 'Nexus'
            manager.save_json(path, value)
            reviewed = app.dispatch('profile-compare', {'id': identifier})
            assert not reviewed['blockers'] and len(reviewed['changes']) == 3
            before, state, trust_bytes = files(data['game']), copy.deepcopy(app.package_manager(data['config']).state), trust.read_bytes()
            copy_atomic, save_json = manager.copy_atomic, manager.save_json
            failed = False
            def fail_copy(source, destination):
                nonlocal failed
                if Path(destination).name == 'Nexus.dll' and not failed:
                    failed = True
                    raise PermissionError('simulated managed install failure')
                return copy_atomic(source, destination)
            def fail_save(target, content):
                if Path(target) == path:
                    raise PermissionError('simulated active profile save failure')
                return save_json(target, content)
            with patch.object(manager, 'copy_atomic', side_effect=fail_copy if failure == 'managed-copy' else copy_atomic), \
                 patch.object(manager, 'save_json', side_effect=fail_save if failure == 'profile-save' else save_json):
                try:
                    app.dispatch('profile-apply', {'id': identifier, 'token': reviewed['token']})
                except PermissionError:
                    pass
                else:
                    raise AssertionError('Expected simulated transaction failure')
            assert files(data['game']) == before and trust.read_bytes() == trust_bytes
            assert app.package_manager(data['config']).state == state and local.exists()
            assert not next(p for p in state['packages'] if p['id'] == pkg['id'])['enabled']


def steam_safe_urls_and_release_selection():
    with fixture() as data, patch.object(app.webbrowser, 'open') as opened:
        app.dispatch('steam-workshop', {'workshop_id': '123'})
        opened.assert_called_once_with('steam://url/CommunityFilePage/123')
        for invalid in (0, -1, True, '', '1/../2', '123?x=1', 'https://unrelated'):
            rejects(lambda: app.dispatch('steam-workshop', {'workshop_id': invalid}))
        assert opened.call_count == 1
        identifier = store(data, [nexus()])
        job = start(data, identifier)
        data['launch'].assert_called_once_with(job['_folder'], 20, 21)
        assert job['_requested_release']['file_id'] == 21 and not data['direct'].called
        assert 'dummy-profile-key' not in json.dumps(app.download_state())
        rejects(lambda: app.dispatch('profile-delete', {'id': identifier}), 'Finish or cancel')
        app.dispatch('download-panel-cancel', {'id': job['id']})
        app.dispatch('profile-delete', {'id': identifier})
    for kind in ('version', 'ambiguous-version', 'unknown-ambiguous', 'not-zip', 'exact-unknown'):
        with fixture() as data:
            entry = nexus()
            if kind == 'version':
                data['files'][0]['version'] = '2.0'
            elif kind == 'ambiguous-version':
                entry.pop('file_id')
                data['files'].append(data['files'][0] | {'file_id': 22, 'file_name': 'other-variant.zip'})
            elif kind == 'unknown-ambiguous':
                entry.pop('file_id')
                entry['version'] = 'Unknown'
                data['files'].append(data['files'][0] | {'file_id': 22})
            elif kind == 'not-zip':
                data['files'][0]['file_name'] = 'release.7z'
            else:
                entry['version'] = 'Unknown'
            identifier = store(data, [entry])
            if kind == 'exact-unknown':
                assert start(data, identifier)['_requested_release']['file_id'] == 21
            else:
                rejects(lambda: start(data, identifier))
                assert not data['launch'].called and not app.DOWNLOADS


def requested_download_lifecycle():
    for kind in ('free', 'premium', 'wrong-mod', 'wrong-file', 'wrong-version', 'profile', 'connection', 'game', 'cancel'):
        with fixture() as data:
            identifier = store(data, [nexus()])
            if kind == 'premium':
                data['account'].return_value['premium'] = True
            job = start(data, identifier)
            if kind == 'premium':
                assert job['state'] == 'completed' and not data['launch'].called
                data['direct'].assert_called_once_with('dummy-profile-key', 20, 21, job['_folder'])
            else:
                if kind == 'wrong-mod':
                    data['matches'][0]['mod']['mod_id'] = 99
                elif kind == 'wrong-file':
                    data['matches'][0]['file_details']['file_id'] = 22
                elif kind == 'wrong-version':
                    data['matches'][0]['file_details']['version'] = '2.0'
                elif kind == 'profile':
                    change_entry(data, identifier, {'version': '2.0'})
                elif kind == 'connection':
                    app.NEXUS_KEY = ''
                elif kind == 'game':
                    other = data['root'] / 'other-game'
                    put(other, 'GraveyardKeeper2.exe', 'other game')
                    manager.save_json(app.DATA / 'settings.json', data['config'] | {'game': str(other)})
                elif kind == 'cancel':
                    app.dispatch('download-panel-cancel', {'id': job['id']})
                complete(data, job)
            target = data['game'] / 'BepInEx/plugins/Example.dll'
            if kind in ('free', 'premium'):
                assert job['state'] == 'completed' and target.read_text() == 'profile release', job['message']
                pkg = app.package_manager(data['config']).state['packages'][0]
                assert (pkg['nexus_mod_id'], pkg['file_id'], pkg['version']) == (20, 21, '1.0')
                compared = app.compare_profile(data['config'], identifier)
                assert not compared['missing'] and not compared['blockers']
            else:
                assert job['state'] in ('error', 'cancelled') and not target.exists(), (kind, job['message'])
                assert not app.package_manager(data['config']).state['packages']


def unresolved_installed_release_can_download():
    for changed in ('version', 'file-variant', 'unknown-version', 'unknown-file'):
        with fixture() as data:
            pkg = managed(data, 'Example', nexus_id=20, version='1.0')
            identifier = store(data, [nexus()])
            library = app.package_manager(data['config'])
            current = library.state['packages'][0]
            if changed in ('version', 'unknown-version'):
                current['version'] = '2.0' if changed == 'version' else 'Unknown'
            else:
                current['file_id'] = 22 if changed == 'file-variant' else None
            manager.save_json(library.state_path, library.state)
            compared = app.compare_profile(data['config'], identifier)
            assert compared['mismatches'], changed
            rejects(lambda: app.apply_profile(data['config'], {'id': identifier, 'token': compared['token']}), 'versions')
            job = start(data, identifier)
            complete(data, job)
            assert job['state'] == 'completed', (changed, job['message'])
            updated = app.package_manager(data['config']).state['packages'][0]
            assert updated['id'] == pkg['id'] and updated['file_id'] == 21 and updated['version'] == '1.0'


def steam_to_nexus_profile_handover():
    with fixture() as data:
        put(data['game'], 'BepInEx/patchers/GK2_WorkshopLoader.dll', 'loader')
        content = b'BepInPlugin\0 Steam Example'
        put(data['workshop'] / '123', 'Example.dll', 'placeholder').write_bytes(content)
        steam = put(data['game'], 'BepInEx/plugins/_Workshop/123/Example.dll', 'placeholder')
        steam.write_bytes(content)
        trust = put(data['game'], inventory.TRUST, '123 = ' + 'a' * 64 + ' # approved\n')
        manager.save_json(app.DATA / 'settings.json', data['config'] | {
            'metadata': {'workshop:123': {'nexus_mod_id': 20, 'version': '1.0', 'file_id': 21}}})
        identifier = store(data, [nexus()])
        job = start(data, identifier)
        complete(data, job)
        assert job['state'] == 'awaiting_review' and not (data['game'] / 'BepInEx/plugins/Example.dll').exists()
        app.dispatch('download-panel-approve', {'id': job['id'], 'acknowledge_duplicates': True})
        assert job['state'] == 'completed', job['message']
        assert steam.read_bytes() == content and 'BLOCKED' not in trust.read_text()
        compared = app.compare_profile(data['config'], identifier)
        assert not compared['blockers'] and not compared['missing']
        assert [{key: change[key] for key in ('id', 'enabled', 'source')} for change in compared['changes']] == [
            {'id': 'workshop:123', 'enabled': False, 'source': 'Steam'}]
        app.apply_profile(data['config'], {'id': identifier, 'token': compared['token']})
        assert '123 = BLOCKED' in trust.read_text() and steam.read_bytes() == content
        assert (data['game'] / 'BepInEx/plugins/Example.dll').read_text() == 'profile release'


def version_acceptance_is_explicit():
    with fixture() as data:
        managed(data, 'Example', nexus_id=20, version='2.0')
        identifier = store(data, [nexus()])
        original = files(data['game'])
        for accept in (None, 'true', False):
            compared = app.compare_profile(data['config'], identifier)
            assert compared['mismatches']
            rejects(lambda: app.apply_profile(data['config'], {'id': identifier, 'token': compared['token'],
                                                               'accept_versions': accept}), 'versions')
        compared = app.compare_profile(data['config'], identifier)
        app.apply_profile(data['config'], {'id': identifier, 'token': compared['token'], 'accept_versions': True})
        assert files(data['game']) == original and app.profile_store(data['config'])[1]['active'] == identifier
    with fixture() as data:
        entry = nexus(version='Unknown')
        entry.pop('file_id')
        identifier = store(data, [entry])
        job = start(data, identifier)
        assert job['_requested_release']['file_id'] == 21
        complete(data, job)
        assert job['state'] == 'completed', job['message']


def profile_settings_stay_shared_and_rollback():
    for failure in (False, True):
        with fixture() as data:
            library = app.package_manager(data['config'])
            pkg = library.install(library.stage(archive(data['root'], 'Settings', {
                'BepInEx/plugins/Example.dll': 'example DLL',
                'BepInEx/config/Example.cfg': 'cfg defaults',
                'BepInEx/plugins/Example/preferences.ini': 'ini defaults'}))['token'],
                {'name': 'Example', 'version': '1.0', 'nexus_mod_id': 20, 'file_id': 21})
            other = library.install(library.stage(archive(data['root'], 'OtherSettings', {
                'BepInEx/plugins/Other.dll': 'other DLL', 'BepInEx/config/Other.cfg': 'other defaults'}))['token'],
                {'name': 'Other', 'version': '1.0'})
            configs = ('BepInEx/config/Example.cfg', 'BepInEx/plugins/Example/preferences.ini', 'BepInEx/config/Other.cfg')
            for relative in configs:
                (data['game'] / relative).write_text('personal ' + relative)
            before = files(data['game'])
            identifier = app.dispatch('profile-save', {'name': 'Shared settings'})['profile']['id']
            path, value = app.profile_store(data['config'])
            for entry in value['profiles'][0]['profile']['mods']:
                if entry['name'] == 'Example':
                    entry['enabled'] = False
            manager.save_json(path, value)
            reviewed = app.compare_profile(data['config'], identifier)
            assert not reviewed['blockers'] and len(reviewed['changes']) == 1
            save_json = manager.save_json
            def fail_save(target, content):
                if Path(target) == path:
                    raise PermissionError('simulated active-profile save failure with edited settings')
                return save_json(target, content)
            if failure:
                with patch.object(manager, 'save_json', side_effect=fail_save):
                    try:
                        app.apply_profile(data['config'], {'id': identifier, 'token': reviewed['token']})
                    except PermissionError:
                        pass
                    else:
                        raise AssertionError('Expected profile-save failure')
                assert files(data['game']) == before
                assert app.profile_store(data['config'])[1]['active'] is None
            else:
                app.apply_profile(data['config'], {'id': identifier, 'token': reviewed['token']})
                assert not (data['game'] / 'BepInEx/plugins/Example.dll').exists()
            state = app.package_manager(data['config']).state
            assert next(p for p in state['packages'] if p['id'] == pkg['id'])['enabled'] is failure
            assert next(p for p in state['packages'] if p['id'] == other['id'])['enabled']
            assert (data['game'] / 'BepInEx/plugins/Other.dll').read_text() == 'other DLL'
            assert all((data['game'] / p).read_text() == 'personal ' + p for p in configs)
            assert not {p.casefold() for p in configs} & (state['deployed'].keys() | state['baseline'].keys())
            assert all(not manager._settings_path(p) for package in state['packages'] for field in ('paths', 'retired_paths', 'adopted_paths')
                       for p in package.get(field, []))
            if not failure:
                for entry in value['profiles'][0]['profile']['mods']:
                    if entry['name'] == 'Example':
                        entry['enabled'] = True
                manager.save_json(path, value)
                reviewed = app.compare_profile(data['config'], identifier)
                app.apply_profile(data['config'], {'id': identifier, 'token': reviewed['token']})
                assert files(data['game']) == before


def main():
    failures = []
    for check in (metadata_actions_and_library_scope, exact_selection_and_protected_foundation,
                  stale_and_running_guards, workshop_and_mixed_rollback, steam_safe_urls_and_release_selection,
                  requested_download_lifecycle, unresolved_installed_release_can_download,
                  steam_to_nexus_profile_handover, version_acceptance_is_explicit,
                  profile_settings_stay_shared_and_rollback):
        try:
            check()
            print('PASS', check.__name__)
        except Exception as error:
            failures.append((check.__name__, str(error)))
            print('FAIL', check.__name__, type(error).__name__, str(error))
    assert not failures, failures
    print('Profile backend integration passed; no real game files or credentials touched.')


if __name__ == '__main__':
    main()
