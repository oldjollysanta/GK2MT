"""Offline end-to-end update actions; all game and app files are temporary."""
import copy
import hashlib
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
import integrations
import manager


def archive(root, name, files):
    path = root / (name + '.zip')
    with zipfile.ZipFile(path, 'w') as output:
        for relative, content in files.items():
            output.writestr(relative, content)
    return path


def rejects(action, text):
    try:
        action()
        raise AssertionError('Expected rejection: ' + text)
    except ValueError as error:
        assert text in str(error), str(error)


def manual_updates():
    with TemporaryDirectory() as temporary, patch.object(manager, 'ensure_game_stopped'):
        root = Path(temporary)
        game, workshop = root / 'game', root / '4358690'
        game.mkdir()
        workshop.mkdir()
        (game / 'GraveyardKeeper2.exe').touch()
        config = {'game': str(game), 'workshop': str(workshop)}
        with patch.object(app, 'DATA', root / 'data'), patch.object(app, 'NEXUS_KEY', 'free-key'), \
             patch.object(app, 'UPDATES', None), patch.object(app, 'UPDATE_ARCHIVES', {}), patch.object(app, 'PREVIEW', None):
            manager.save_json(app.DATA / 'settings.json', config)
            lib = app.package_manager(config)
            pkg = lib.install(lib.stage(archive(root, 'initial', {'BepInEx/plugins/Example.dll': 'v1'}))['token'],
                              {'name': 'Example', 'version': '1.0', 'nexus_mod_id': 10})
            release = archive(root, 'new-release', {'BepInEx/plugins/Example.dll': 'v2'})
            wrong = archive(root, 'wrong-mod', {'BepInEx/plugins/Wrong.dll': 'wrong'})
            old_release = archive(root, 'old-release', {'BepInEx/plugins/Example.dll': 'v1'})
            result = {'account': {'premium': False}, 'categories': {}, 'updates': [
                {'id': pkg['id'], 'name': 'Example', 'nexus_mod_id': 10, 'file_id': 2,
                 'version': '2.0', 'known_version': True, 'downloadable': False,
                 'status': 'Update available',
                 'blocked_reason': 'Nexus Premium is required for automatic downloads'}]}
            hashes = {}

            def known_zip(path, mod_id=10, file_id=2, version='2.0'):
                with path.open('rb') as stream:
                    checksum = hashlib.file_digest(stream, 'md5').hexdigest()
                hashes[checksum] = [{'mod': {'mod_id': mod_id},
                                     'file_details': {'file_id': file_id, 'version': version, 'size_in_bytes': path.stat().st_size}}]

            def metadata(url, key):
                assert '/mods/md5_search/' in url and key == 'free-key'
                return hashes.get(url.rsplit('/', 1)[-1].removesuffix('.json'), [])

            known_zip(release)
            known_zip(wrong, mod_id=11)
            known_zip(old_release, file_id=1, version='1.0')
            with patch.object(integrations, 'nexus_check', side_effect=lambda *args: copy.deepcopy(result)), \
                 patch.object(integrations, '_json', side_effect=metadata), \
                 patch.object(integrations, 'nexus_download') as download:
                app.dispatch('updates', {})
                assert app.UPDATES['updates'][0]['manual_installable']
                result['updates'][0]['status'] = 'Confirm installed file/version'
                app.dispatch('updates', {})
                assert not app.UPDATES['updates'][0]['manual_installable']
                result['updates'][0]['status'] = 'Update available'
                app.dispatch('updates', {})
                skipped = app.dispatch('download-updates', {})
                assert len(skipped['skipped']) == 1 and not skipped['updated']
                download.assert_not_called()
                preview_args = lambda path: {'id': pkg['id'], 'path': str(path)}
                rejects(lambda: app.dispatch('update-archive-preview', preview_args(wrong)), 'not the selected Nexus update')
                rejects(lambda: app.dispatch('update-archive-preview', preview_args(old_release)), 'not the selected Nexus update')
                # Even a bad publisher/API mapping cannot replace a different managed DLL.
                known_zip(wrong)
                rejects(lambda: app.dispatch('update-archive-preview', preview_args(wrong)), 'does not match')
                before = lib.state_path.read_bytes()
                preview = app.dispatch('update-archive-preview', preview_args(release))
                assert preview['version'] == '2.0' and preview['files'] == 1
                assert lib.state_path.read_bytes() == before
                assert (game / 'BepInEx/plugins/Example.dll').read_text() == 'v1'
                target = game / 'BepInEx/plugins/Example.dll'
                target.write_text('changed')
                rejects(lambda: app.dispatch('update-archive', {'token': preview['token']}), 'Installed files changed')
                target.write_text('v1')
                staged = app.UPDATE_ARCHIVES[preview['token']]['archive']
                original = staged.read_bytes()
                staged.write_bytes(b'changed archive')
                rejects(lambda: app.dispatch('update-archive', {'token': preview['token']}), 'staged ZIP changed')
                staged.write_bytes(original)
                app.PREVIEW = {'old': 'deck preview'}
                completed = app.dispatch('update-archive', {'token': preview['token'], 'id': 'ignored', 'version': '99'})
                assert completed['updated'][0]['id'] == pkg['id'] and completed['updated'][0]['version'] == '2.0'
                assert target.read_text() == 'v2' and app.PREVIEW is None and not app.UPDATES['updates']
                updated = app.package_manager(config).state['packages'][0]
                assert updated['file_id'] == 2 and updated['version'] == '2.0'
                rejects(lambda: app.dispatch('update-archive', {'token': preview['token']}), 'preview the update ZIP again')

                # A disabled external mod is adopted without enabling it or replacing its config.
                disabled = game / 'BepInEx/plugins/Disabled.dll.gk2mt-disabled'
                disabled.write_text('disabled old')
                user_config = game / 'BepInEx/config/disabled.cfg'
                user_config.parent.mkdir(parents=True)
                user_config.write_text('my settings')
                row = next(r for r in app.snapshot()['mods'] if disabled.relative_to(game).as_posix() in r['paths'])
                config['metadata'] = {row['id']: {'version': '1.0', 'nexus_mod_id': 20, 'file_id': 1}}
                manager.save_json(app.DATA / 'settings.json', config)
                release = archive(root, 'disabled-release', {'BepInEx/plugins/Disabled.dll': 'disabled new',
                                                            'BepInEx/config/disabled.cfg': 'defaults'})
                known_zip(release, mod_id=20)
                result['updates'][0].update(id=row['id'], name='Disabled', nexus_mod_id=20)
                app.dispatch('updates', {})
                preview = app.dispatch('update-archive-preview', {'id': row['id'], 'path': str(release)})
                assert disabled.read_text() == 'disabled old' and user_config.read_text() == 'my settings'
                completed = app.dispatch('update-archive', {'token': preview['token']})
                lib = app.package_manager(config)
                updated = next(p for p in lib.state['packages'] if p['id'] == completed['updated'][0]['id'])
                assert not updated['enabled'] and not disabled.exists() and not disabled.with_suffix('').exists()
                assert user_config.read_text() == 'my settings'
                assert Path(lib.state['baseline'][disabled.relative_to(game).as_posix().casefold()]).read_text() == 'disabled old'
                lib.set_enabled(updated['id'], True)
                assert disabled.with_suffix('').read_text() == 'disabled new'
                assert user_config.read_text() == 'my settings'
    print('Manual ZIP updates passed: Free account, exact release verification, wrong DLL, preview, stale guard, disabled/config/backups.')


def main():
    with TemporaryDirectory() as temporary, patch.object(manager, 'ensure_game_stopped'):
        root = Path(temporary)
        game, workshop = root / 'game', root / '4358690'
        game.mkdir()
        workshop.mkdir()
        (game / 'GraveyardKeeper2.exe').touch()
        config = {'game': str(game), 'workshop': str(workshop)}
        with patch.object(app, 'DATA', root / 'data'), patch.object(app, 'NEXUS_KEY', 'test-key'), \
             patch.object(app, 'UPDATES', None), patch.object(app, 'PREVIEW', None):
            manager.save_json(app.DATA / 'settings.json', config)
            assert app.settings()['import_folder'] == str(Path.home() / 'Downloads/GK2MT')
            lib = app.package_manager(config)
            known = lib.install(lib.stage(archive(root, 'known', {'BepInEx/plugins/Example.dll': 'v1'}))['token'],
                                {'name': 'Example', 'version': '1.0', 'nexus_mod_id': 10})
            unknown = lib.install(lib.stage(archive(root, 'unknown', {'BepInEx/plugins/Mystery.dll': 'mystery'}))['token'],
                                  {'name': 'Mystery', 'nexus_mod_id': 11})
            release = archive(root, 'release', {'BepInEx/plugins/Example.dll': 'v2'})
            result = {'account': {'premium': True}, 'errors': [], 'metadata_errors': [],
                      'categories': {known['id']: 'Gameplay', unknown['id']: 'Utilities'},
                      'updates': [
                          {'id': known['id'], 'name': 'Example', 'nexus_mod_id': 10, 'file_id': 2,
                           'version': '2.0', 'known_version': True, 'downloadable': True},
                          {'id': unknown['id'], 'name': 'Mystery', 'nexus_mod_id': 11, 'file_id': 3,
                           'version': '3.0', 'known_version': False, 'downloadable': True}]}
            with patch.object(integrations, 'nexus_check', side_effect=lambda *args: copy.deepcopy(result)), \
                 patch.object(integrations, 'nexus_download', return_value=release) as download:
                app.dispatch('updates', {})
                assert next(r for r in app.snapshot()['mods'] if r['id'] == known['id'])['category'] == 'Gameplay'
                completed = app.dispatch('download-updates', {})
                assert len(completed['updated']) == 1 and len(completed['skipped']) == 1 and not completed['errors'], completed
                download.assert_called_once()
                assert (game / 'BepInEx/plugins/Example.dll').read_text() == 'v2'
                rows = app.snapshot()['mods']
                row = next(r for r in rows if r['id'] == known['id'])
                assert row['file_id'] == 2 and row['version'] == '2.0' and row['category'] == 'Gameplay'
                assert len([r for r in rows if r.get('nexus_mod_id') == 10]) == 1
                assert all(u['id'] != known['id'] for u in app.UPDATES['updates'])
                assert (game / 'BepInEx/plugins/Mystery.dll').read_text() == 'mystery'

                # A second individual update uses the existing package, then stale files block download.
                result['updates'][0].update(version='3.0', file_id=4)
                app.dispatch('updates', {})
                completed = app.dispatch('update', {'id': known['id']})
                assert len(completed['updated']) == 1 and not completed['errors'], completed
                app.dispatch('updates', {})
                download.reset_mock()
                (game / 'BepInEx/plugins/Example.dll').write_text('external change')
                stale = app.dispatch('update', {'id': known['id']})
                assert stale['errors'] and 'changed' in stale['errors'][0]
                download.assert_not_called()
                assert (game / 'BepInEx/plugins/Example.dll').read_text() == 'external change'
                app.dispatch('settings', config)
                assert app.UPDATES is None

            # The banner detects an incomplete bootstrap and disappears for a complete one.
            assert not app.snapshot()['loader_installed']
            for name in app.BEPINEX_FILES:
                path = game / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('foundation')
            assert app.snapshot()['loader_installed']
            (game / 'winhttp.dll').unlink()
            assert 'winhttp.dll' in app.snapshot()['loader_missing']

            # Foundation updates preserve a disabled component and use the installed release identity.
            component = 'BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll'
            component_path = game / (component + '.gk2mt-disabled')
            component_path.parent.mkdir(parents=True)
            component_path.write_text('old disabled component')
            for name in integrations.GAME_MARKERS:
                path = game / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            foundation_id = next(r['id'] for r in app.snapshot()['mods'] if component + '.gk2mt-disabled' in r['paths'])
            config['metadata'] = {foundation_id: {'nexus_mod_id': 48, 'version': '1.0', 'package_version': '1.0', 'file_id': 1}}
            manager.save_json(app.DATA / 'settings.json', config)
            foundation_zip = archive(root, 'foundation', {name: 'new foundation' for name in integrations.REQUIRED})
            foundation_result = {'account': {'premium': True}, 'categories': {}, 'updates': [
                {'id': foundation_id, 'name': 'BepInEx', 'nexus_mod_id': 48, 'file_id': 20,
                 'version': '2.0', 'known_version': True, 'downloadable': True}]}
            with patch.object(integrations, 'nexus_check', side_effect=lambda *args: copy.deepcopy(foundation_result)), \
                 patch.object(integrations, 'nexus_download', return_value=foundation_zip) as download:
                app.dispatch('updates', {})
                (game / 'BepInEx/core/BepInEx.dll').write_text('changed root runtime')
                stale = app.dispatch('update', {'id': foundation_id})
                assert stale['errors'] and 'changed' in stale['errors'][0]
                download.assert_not_called()
                app.dispatch('updates', {})
                completed = app.dispatch('update', {'id': foundation_id})
                assert len(completed['updated']) == 1 and not completed['errors'], completed
                assert component_path.is_file() and not (game / component).exists()
                component_path.rename(game / component)
                foundation = next(r for r in app.snapshot()['mods'] if r['id'] == foundation_id)
                assert foundation['file_id'] == 20 and foundation['package_version'] == '2.0'
                app.dispatch('updates', {})
                app.PREVIEW = {'stale': True}
                with patch.object(manager, 'save_json', side_effect=PermissionError('tracking denied')):
                    completed = app.dispatch('update', {'id': foundation_id})
                assert len(completed['updated']) == 1 and not completed['errors'] and completed['warnings'], completed
                assert not app.UPDATES['updates'] and app.PREVIEW is None

                # Manual foundation updates use the same strict ZIP validator, with no preview writes.
                foundation_result['account']['premium'] = False
                foundation_result['updates'][0].update(file_id=21, version='3.0', downloadable=False, status='Update available')
                app.dispatch('updates', {})
                files_before = {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
                with patch.object(integrations, '_json', return_value=[{'mod': {'mod_id': 48},
                        'file_details': {'file_id': 21, 'version': '3.0'}}]):
                    preview = app.dispatch('update-archive-preview', {'id': foundation_id, 'path': str(foundation_zip)})
                assert files_before == {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
                completed = app.dispatch('update-archive', {'token': preview['token']})
                assert len(completed['updated']) == 1 and not completed['errors']
                assert next(r for r in app.snapshot()['mods'] if r['id'] == foundation_id)['package_version'] == '3.0'

            # Foundation setup must not bypass package ownership, including a disabled package.
            lib = app.package_manager(config)
            lib.state['packages'].append({'id': 'owned-foundation', 'name': 'Owned foundation', 'enabled': False,
                                          'source': 'GK2MT', 'paths': [component], 'version': '1.0', 'nexus_mod_id': 48})
            manager.save_json(lib.state_path, lib.state)
            with patch.object(integrations, 'setup_installer') as setup:
                try:
                    app.dispatch('setup', {})
                    raise AssertionError('Foundation package ownership must be protected')
                except ValueError as error:
                    assert 'imported package' in str(error)
                setup.assert_not_called()

    print('Update actions passed: categories, individual/bulk install, unknown skip, identity, stale guard, detection.')


if __name__ == '__main__':
    main()
    manual_updates()
