"""Exercise the desktop bridge and read-only inventory with isolated app data."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import zipfile
from unittest.mock import MagicMock, patch

import app
import desktop
import webview
import inventory
import manager


def helper_actions():
    """The default launch opens the app; the panel helper stays separate."""
    with patch.object(desktop, 'run') as run, patch.object(app, 'DATA') as data, \
         patch.object(app, 'restore_nexus') as restore, \
         patch.object(app.nexus_panel, 'main') as panel:
        app.main(['--nexus-panel', 'panel-folder', '48', '123'])
        panel.assert_called_once_with(['panel-folder', '48', '123'])
        run.assert_not_called()
        data.mkdir.assert_not_called()
        restore.assert_not_called()
        app.main([])
        restore.assert_called_once_with()
        run.assert_called_once_with(app)
    window = MagicMock()
    with patch.object(app, 'WINDOW', window), \
         patch.object(app, 'settings', return_value={'game': '.', 'workshop': '.'}):
        window.create_file_dialog.return_value = ('C:/mods/caf\u00e9.zip',)
        assert app.dispatch('browse', {'kind': 'zip'}) == {'path': 'C:/mods/caf\u00e9.zip'}
        assert window.create_file_dialog.call_args.args[0] == webview.FileDialog.OPEN
        window.create_file_dialog.return_value = ('C:/mods',)
        assert app.file_dialog('folder') == {'path': 'C:/mods'}
        assert window.create_file_dialog.call_args.args[0] == webview.FileDialog.FOLDER
        window.create_file_dialog.return_value = None
        assert app.file_dialog('zip') == {'path': ''}


def shutdown_actions():
    job = {'_stop': threading.Event()}
    with patch.object(app, 'DOWNLOADS', {'job': job}), \
         patch.object(app, 'NEXUS_KEY', 'session-only-key'), \
         patch.object(app, 'stop_download_panel') as stop, \
         patch.object(app, 'disconnect_deck') as disconnect:
        app.shutdown()
        assert job['_stop'].is_set()
        stop.assert_called_once_with(job)
        disconnect.assert_called_once()
        assert app.NEXUS_KEY == ''


def nexus_actions():
    """A validated key survives closing; failed replacements preserve it; forgetting removes it."""
    with TemporaryDirectory() as temporary, patch.object(app, 'DATA', Path(temporary)), \
         patch.object(app, 'NEXUS_KEY', ''), patch.object(app, 'NEXUS_ERROR', ''), \
         patch.object(app, 'NEXUS_SESSION', 0), patch.object(app, 'UPDATES', None), \
         patch.object(app, 'DOWNLOADS', {}), patch.object(app, 'DECK_SESSION', None), \
         patch.object(app, 'check_nexus', return_value={'account': {'name': 'Test'}, 'updates': []}) as check:
        manager.save_json(app.DATA / 'settings.json', {
            'game': str(app.DATA / 'game'), 'workshop': str(app.DATA / 'workshop')})
        saved = app.DATA / 'nexus-key.bin'
        key = 'dummy-nexus-key-for-persistence-test'
        app.restore_nexus()
        assert app.NEXUS_KEY == '' and app.NEXUS_ERROR == ''
        body = {'key': key}
        app.dispatch('nexus-connect', body)
        assert not body and app.NEXUS_KEY == key
        check.assert_called_once_with(app.settings(), key)
        before = saved.read_bytes()
        assert key.encode() not in before
        state = app.snapshot()
        assert state['nexus_connected'] and state['nexus_saved']
        assert key not in json.dumps(state)
        assert key.encode() not in (app.DATA / 'settings.json').read_bytes()
        session = app.NEXUS_SESSION
        for failure in ('empty', 'validation', 'storage'):
            with patch.object(app, 'check_nexus', side_effect=ValueError('Key rejected') if failure == 'validation' else None), \
                 patch.object(app.nexus_credentials, 'save', side_effect=RuntimeError('Save failed')):
                try:
                    app.dispatch('nexus-connect', {'key': '' if failure == 'empty' else 'replacement-test-key'})
                    raise AssertionError('Invalid or unsaved replacement was accepted')
                except (ValueError, RuntimeError):
                    pass
            assert app.NEXUS_KEY == key and app.NEXUS_SESSION == session
            assert saved.read_bytes() == before
        app.shutdown()
        assert app.NEXUS_KEY == '' and saved.read_bytes() == before
        check.reset_mock()
        app.restore_nexus()
        assert app.NEXUS_KEY == key and app.UPDATES is None
        check.assert_not_called()  # Restoring works offline; refresh checks remain explicit.
        app.dispatch('updates', {})
        check.assert_called_once_with(app.settings(), key)
        app.dispatch('nexus-forget', {})
        assert not saved.exists() and app.NEXUS_KEY == '' and app.UPDATES is None
        app.restore_nexus()
        assert app.NEXUS_KEY == '' and not app.snapshot()['nexus_saved']
        saved.write_bytes(b'corrupt-encrypted-test-key')
        app.restore_nexus()
        state = app.snapshot()
        assert not state['nexus_connected'] and state['nexus_saved']
        assert 'could not be restored' in state['nexus_error']
        app.dispatch('nexus-forget', {})
        assert not saved.exists() and app.NEXUS_ERROR == ''


def deck_actions():
    """Settings omit secrets; remembered passwords are encrypted and bound to the Deck."""
    class Session:
        alive = True

        def matches(self, value):
            return value['host'] == 'steamdeck.local' and value['user'] == 'deck'

        def close(self):
            self.alive = False

    with TemporaryDirectory() as temporary, patch.object(app, 'DATA', Path(temporary)), \
         patch.object(app, 'DECK_SESSION', None), patch.object(app, 'PREVIEW', None), \
         patch.object(app, 'DECK_MESSAGE', ''), patch.object(app.manager, 'ensure_game_stopped'):
        config = {'game': str(Path(temporary) / 'pc'), 'workshop': str(Path(temporary) / '4358690')}
        manager.save_json(app.DATA / 'settings.json', config)
        deck_settings = {'host': 'steamdeck.local', 'user': 'deck'}
        found = {'found': True, 'game_path': '/home/deck/game/GK2',
                 'workshop_path': '/home/deck/workshop/content/4358690', 'candidates': []}
        before = (app.DATA / 'settings.json').read_bytes()
        with patch.object(app.deck_ssh, 'probe', return_value={'known': False, 'key': 'test-public-key'}) as probe:
            assert app.dispatch('deck-probe', {'deck': deck_settings})['known'] is False
            assert probe.call_args.args[0]['host'] == deck_settings['host']
        assert app.settings()['deck']['host'] == deck_settings['host']
        assert b'password' not in (app.DATA / 'settings.json').read_bytes()
        session = Session()
        body = {'deck': deck_settings | {'password': 'nested-secret'}, 'password': 'test-secret',
                'expected_key': 'test-public-key'}
        with patch.object(app.deck_ssh, 'connect', return_value=session) as connect, \
             patch.object(app.deck, 'discover_remote', return_value=found):
            result = app.dispatch('deck-connect', body)
            assert result['connected'] and result['deck']['game_path'] == found['game_path']
            assert connect.call_args.args[1] == 'test-secret'
            assert connect.call_args.kwargs['expected_key'] == 'test-public-key'
        assert 'password' not in body
        saved = app.settings()
        assert set(saved['deck']) == set(app.DECK_DEFAULTS)
        assert b'secret' not in (app.DATA / 'settings.json').read_bytes()
        assert app.deck_connection(saved)['connected']
        # Setup uses the existing authenticated session and does not require Workshop folders.
        with patch.object(app.deck, 'configure_proton', return_value={'configured': True}) as configure:
            app.PREVIEW = {'digest': 'stale'}
            for action, install in (('deck-proton-status', False), ('deck-proton-setup', True)):
                assert app.dispatch(action, {'deck': saved['deck'] | {'workshop_path': ''}})['configured']
                assert configure.call_args.args[1] is session
                assert configure.call_args.kwargs['install'] is install
                assert app.PREVIEW is None
            try:
                app.dispatch('deck-proton-setup', {'deck': saved['deck'] | {'game_path': ''}})
                raise AssertionError('Missing game path was accepted')
            except ValueError as error:
                assert 'game folder' in str(error)
            assert configure.call_count == 2
        with patch.object(app.deck, 'preview', return_value={'digest': 'preview-token'}) as preview:
            assert app.dispatch('deck-preview', {'deck': saved['deck']})['digest'] == 'preview-token'
            assert preview.call_args.kwargs['connection'] is session
            settings_path = app.DATA / 'settings.json'
            before_check = (settings_path.read_bytes(), settings_path.stat().st_mtime_ns)
            with patch.object(app.deck, 'configure_proton', return_value={'configured': True}), \
                 patch.object(app.deck, 'sync', return_value={'matched': True}) as sync:
                assert app.dispatch('deck-proton-status', {'deck': saved['deck']})['configured']
                assert app.PREVIEW['digest'] == 'preview-token'
                assert (settings_path.read_bytes(), settings_path.stat().st_mtime_ns) == before_check
                assert app.dispatch('deck-sync', {'digest': 'preview-token'})['matched']
                assert sync.call_args.args[3] == 'preview-token'
            app.dispatch('deck-preview', {'deck': saved['deck']})
            with patch.object(app.deck, 'configure_proton', return_value={'configured': True}):
                app.dispatch('deck-proton-status', {'deck': saved['deck'] | {'game_path': '/home/deck/game/Other'}})
                assert app.PREVIEW is None, 'Changing the target must invalidate the comparison.'
            app.dispatch('deck-preview', {'deck': saved['deck']})
        with patch.object(app.deck, 'sync', side_effect=ValueError('Game build changed since comparison.')):
            try:
                app.dispatch('deck-sync', {'digest': 'preview-token'})
                raise AssertionError('Changed game build was accepted')
            except ValueError as error:
                assert 'Game build changed' in str(error)
        assert app.PREVIEW is None, 'A failed sync requires a new comparison.'
        # Switching the target cannot reuse a previous comparison or authenticated session.
        try:
            app.dispatch('deck-preview', {'deck': saved['deck'] | {'host': 'different-deck'}})
            raise AssertionError('Changed target was accepted')
        except ValueError as error:
            assert 'Connect' in str(error)
        assert not session.alive and app.PREVIEW is None and app.DECK_SESSION is None
        # Missing game folders still allow login and manual path entry.
        session = Session()
        with patch.object(app.deck_ssh, 'connect', return_value=session), \
             patch.object(app.deck, 'discover_remote', return_value={'found': False, 'candidates': []}):
            result = app.dispatch('deck-connect', {'deck': deck_settings, 'password': 'test-secret'})
        assert result['connected'] and not result['deck']['game_path']
        assert app.dispatch('deck-disconnect', {})['connected'] is False
        assert not session.alive
        with patch.object(app.deck, 'configure_proton') as configure:
            try:
                app.dispatch('deck-proton-setup', {'deck': saved['deck']})
                raise AssertionError('Disconnected Proton setup was accepted')
            except ValueError as error:
                assert 'Connect' in str(error)
            configure.assert_not_called()
        app.PREVIEW = {'digest': 'old-token'}
        try:
            app.dispatch('deck-sync', {'digest': 'old-token'})
            raise AssertionError('Disconnected sync was accepted')
        except ValueError as error:
            assert 'Connect' in str(error)
        body = {'deck': deck_settings, 'password': 'wrong-secret'}
        with patch.object(app.deck_ssh, 'connect', side_effect=RuntimeError('Login rejected')):
            try:
                app.dispatch('deck-connect', body)
                raise AssertionError('Failed login was accepted')
            except RuntimeError:
                pass
        assert 'password' not in body and app.DECK_SESSION is None and app.PREVIEW is None
        # Opt-in encryption persists across sessions, preserving the actual password.
        secret = '  dummy Deck password \U0001f512  '
        body = {'deck': deck_settings, 'password': secret, 'remember_password': True}
        with patch.object(app.deck_ssh, 'connect', return_value=Session()) as connect, \
             patch.object(app.deck, 'discover_remote', return_value={'found': False}):
            result = app.dispatch('deck-connect', body)
            assert connect.call_args.args[1] == secret
        stored = app.DATA / 'deck-password.bin'
        encrypted = stored.read_bytes()
        assert secret.encode() not in encrypted
        assert secret not in json.dumps(result | app.deck_connection(app.settings()))
        assert result['password_saved'] and 'password' not in body
        assert secret.encode() not in (app.DATA / 'settings.json').read_bytes()
        app.dispatch('deck-disconnect', {})
        assert app.deck_connection(app.settings())['password_saved']
        with patch.object(app.deck_ssh, 'connect', return_value=Session()) as connect, \
             patch.object(app.deck, 'discover_remote', return_value={'found': False}):
            app.dispatch('deck-connect', {'deck': deck_settings, 'remember_password': True})
            assert connect.call_args.args[1] == secret
        # A saved secret must never authenticate a different address, user, or port.
        for change in ({'host': '192.168.1.51'}, {'user': 'other'}, {'port': 2222}):
            target = deck_settings | change
            body = {'deck': target, 'password': '', 'remember_password': True}
            with patch.object(app.deck_ssh, 'connect', side_effect=RuntimeError('No password')) as connect:
                try:
                    app.dispatch('deck-connect', body)
                    raise AssertionError('Connection should fail without a matching saved credential.')
                except RuntimeError:
                    pass
                assert connect.call_args.args[1] == ''
            assert stored.read_bytes() == encrypted
            assert not app.deck_connection(app.settings())['password_saved']
            assert 'password' not in body
        # A rejected replacement password cannot overwrite the previously saved one.
        with patch.object(app.deck_ssh, 'connect', side_effect=RuntimeError('Login rejected')):
            try:
                app.dispatch('deck-connect', {'deck': deck_settings, 'password': 'wrong', 'remember_password': True})
            except RuntimeError:
                pass
        assert stored.read_bytes() == encrypted and app.saved_deck_password(app.settings()['deck']) == secret
        with patch.object(app.deck_ssh, 'connect', return_value=Session()), \
             patch.object(app.deck, 'discover_remote', return_value={'found': False}):
            app.dispatch('deck-connect', {'deck': deck_settings, 'remember_password': True})
        session = app.DECK_SESSION
        app.dispatch('deck-forget-password', {})
        assert not stored.exists() and app.DECK_SESSION is session and session.alive
        assert not app.deck_connection(app.settings())['password_saved']
        # Storage failures leave a working SSH session, without plaintext fallback.
        with patch.object(app.deck_ssh, 'connect', return_value=Session()), \
             patch.object(app.deck, 'discover_remote', return_value={'found': False}), \
             patch.object(app.nexus_credentials, 'save', side_effect=RuntimeError('Storage failed')):
            result = app.dispatch('deck-connect', {'deck': deck_settings, 'password': secret, 'remember_password': True})
        assert result['connected'] and result['credential_warning'] and not result['password_saved']
        assert not stored.exists() and secret not in json.dumps(result)
        stored.write_bytes(b'corrupt saved credential')
        assert app.deck_connection(app.settings())['password_error']
        app.dispatch('deck-forget-password', {})
        # Last valid address survives an unreachable Deck and is restored offline.
        with patch.object(app.deck_ssh, 'probe', side_effect=RuntimeError('SSH unavailable')):
            try:
                app.dispatch('deck-probe', {'deck': deck_settings | {'host': '192.168.1.52'}})
            except RuntimeError:
                pass
        assert app.settings()['deck']['host'] == '192.168.1.52'
        assert b'password' not in (app.DATA / 'settings.json').read_bytes()
        app.disconnect_deck()


def workshop_actions():
    rows = [{'id': 'workshop:123', 'name': 'GK2 Respec', 'workshop_id': '123',
             'version': '1.0', 'source': 'Steam Workshop', 'paths': [], 'can_toggle': True},
            {'id': 'patchers:loader.dll', 'name': 'Loader.dll', 'workshop_id': '456',
             'version': '2.0', 'source': 'Steam Workshop', 'paths': [], 'can_toggle': True}]
    with TemporaryDirectory() as temporary, patch.object(app, 'DATA', Path(temporary)), \
         patch.object(app.inventory, 'scan', side_effect=lambda *_: [row.copy() for row in rows]), \
         patch.object(app.integrations, 'steam_workshop_titles',
                      return_value={'123': 'Talent & Tech Refund', '456': 'Workshop Loader'}) as fetch:
        config = {'game': str(app.DATA / 'game'), 'workshop': str(app.DATA / '4358690')}
        manager.save_json(app.DATA / 'settings.json', config)
        original = (app.DATA / 'settings.json').read_bytes()
        assert app.snapshot()['mods'][0]['name'] == 'GK2 Respec'
        fetch.assert_not_called()  # Routine snapshots and toggles stay offline.
        state = app.dispatch('rescan', {})
        by_id = {row['workshop_id']: row for row in state['mods']}
        assert by_id['123']['name'] == 'Talent & Tech Refund'
        assert by_id['456']['name'] == 'Workshop Loader'
        assert by_id['123']['source'] == 'Steam Workshop' and by_id['123']['version'] == '1.0'
        fetch.assert_called_once_with(['123', '456'])
        app.snapshot()
        app.dispatch('rescan', {})
        assert fetch.call_count == 1  # Fresh cache avoids startup requests.
        app.dispatch('rescan', {'force_workshop_titles': True})
        assert fetch.call_count == 2
        cache_file = app.DATA / 'workshop-titles.json'
        cached = cache_file.read_bytes()
        fetch.side_effect = RuntimeError('offline')
        state = app.dispatch('rescan', {'force_workshop_titles': True})
        assert state['workshop_title_warning'] and cache_file.read_bytes() == cached
        assert any(row['name'] == 'Talent & Tech Refund' for row in state['mods'])
        assert (app.DATA / 'settings.json').read_bytes() == original
        fetch.side_effect = None
        # Partial Steam responses preserve the last known title for missing/private items.
        fetch.return_value = {'456': 'New Workshop Loader'}
        state = app.dispatch('rescan', {'force_workshop_titles': True})
        assert {row['name'] for row in state['mods']} == {'Talent & Tech Refund', 'New Workshop Loader'}
        stale = manager.read_json(cache_file, {})
        stale['checked_at'] = 0
        manager.save_json(cache_file, stale)
        fetch.reset_mock()
        app.dispatch('rescan', {})
        fetch.assert_called_once()
        # New Workshop IDs refresh even when the rest of the cache is recent.
        rows.append(rows[0] | {'id': 'workshop:789', 'workshop_id': '789', 'name': 'New plugin'})
        app.dispatch('rescan', {})
        assert fetch.call_count == 2 and '789' in fetch.call_args.args[0]
        cache_file.write_text('broken cache', encoding='utf-8')
        assert any(row['name'] == 'GK2 Respec' for row in app.snapshot()['mods'])
        app.dispatch('rescan', {})
        assert manager.read_json(cache_file, {})['titles']['456'] == 'New Workshop Loader'
        rows.clear()
        fetch.reset_mock()
        app.dispatch('rescan', {'force_workshop_titles': True})
        fetch.assert_not_called()


def uninstall_actions():
    """Only a current, single-use review may remove an isolated fixture mod."""
    with TemporaryDirectory() as temporary, patch.object(app, 'DATA', Path(temporary) / 'data'), \
         patch.object(app, 'UNINSTALL_PREVIEW', None), patch.object(app, 'DOWNLOADS', {}), \
         patch.object(app, 'UPDATES', None), patch.object(app, 'UPDATE_ARCHIVES', {}), \
         patch.object(app, 'PREVIEW', None), patch.object(app.manager, 'ensure_game_stopped') as stopped:
        game, workshop = Path(temporary) / 'game', Path(temporary) / '4358690'
        plugin = game / 'BepInEx/plugins/Fixture/Fixture.dll'
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(b'original fixture mod')
        config_file = plugin.with_name('settings.cfg')
        config_file.write_bytes(b'user settings')
        neighbour = game / 'BepInEx/plugins/Other.dll'
        neighbour.write_bytes(b'unrelated mod')
        (game / 'GraveyardKeeper2.exe').touch()
        manager.save_json(app.DATA / 'settings.json', {'game': str(game), 'workshop': str(workshop)})
        row = next(row for row in app.snapshot()['mods'] if 'Fixture.dll' in str(row['paths']))
        assert row['can_uninstall'] and not row['uninstall_reason']

        def rejects(action, body, text):
            try:
                app.dispatch(action, body)
                raise AssertionError('Unsafe uninstall was accepted')
            except ValueError as error:
                assert text.lower() in str(error).lower(), str(error)
            assert plugin.is_file() and neighbour.read_bytes() == b'unrelated mod'

        preview = app.dispatch('uninstall-preview', {'id': row['id']})
        assert preview['removed'] == ['BepInEx/plugins/Fixture/Fixture.dll']
        assert preview['preserved'] == ['BepInEx/plugins/Fixture/settings.cfg']
        assert not any(key.startswith('_') for key in preview)
        assert plugin.read_bytes() == b'original fixture mod'
        assert not list(app.DATA.rglob('recovery.json')), 'Preview created an uninstall backup'
        rejects('uninstall', {'token': 'forged-token'}, 'preview')
        rejects('uninstall', {'token': preview['token']}, 'preview')
        with patch.object(app.manager, 'ensure_game_stopped', side_effect=ValueError('Close the game')):
            rejects('uninstall-preview', {'id': row['id']}, 'close')
        with patch.object(app, 'DOWNLOADS', {'job': {'state': 'waiting'}}):
            rejects('uninstall-preview', {'id': row['id']}, 'download')
        preview = app.dispatch('uninstall-preview', {'id': row['id']})
        plugin.write_bytes(b'external change')
        rejects('uninstall', {'token': preview['token']}, 'changed')
        assert plugin.read_bytes() == b'external change'
        plugin.write_bytes(b'original fixture mod')
        preview = app.dispatch('uninstall-preview', {'id': row['id']})
        with patch.object(app, 'DOWNLOADS', {'job': {'state': 'downloading'}}):
            rejects('uninstall', {'token': preview['token']}, 'download')
        preview = app.dispatch('uninstall-preview', {'id': row['id']})
        app.dispatch('metadata', {'id': row['id'], 'nexus_mod_id': 48})
        rejects('uninstall', {'token': preview['token']}, 'changed')
        protected = app.row_by_id(row['id'])
        assert not protected['can_uninstall'] and 'foundation' in protected['uninstall_reason']
        rejects('uninstall-preview', {'id': row['id']}, 'foundation')
        app.dispatch('metadata', {'id': row['id'], 'nexus_mod_id': 10})
        preview = app.dispatch('uninstall-preview', {'id': row['id']})
        changed = app.package_manager(app.settings())
        manager.save_json(changed.state_path, changed.state | {'changed': True})
        rejects('uninstall', {'token': preview['token']}, 'changed')
        changed.state_path.unlink()
        preview = app.dispatch('uninstall-preview', {'id': row['id']})
        other_game = Path(temporary) / 'another-game'
        other_game.mkdir()
        (other_game / 'GraveyardKeeper2.exe').touch()
        settings_file = app.DATA / 'settings.json'
        saved = manager.read_json(settings_file, {})
        manager.save_json(settings_file, saved | {'game': str(other_game)})
        rejects('uninstall', {'token': preview['token']}, 'changed')
        manager.save_json(settings_file, saved)
        preview = app.dispatch('uninstall-preview', {'id': row['id']})
        app.UPDATES, app.PREVIEW = {'stale': True}, {'digest': 'stale'}
        app.UPDATE_ARCHIVES['stale'] = {}
        result = app.dispatch('uninstall', {'token': preview['token'], 'id': 'other', 'files': ['Other.dll']})
        assert not plugin.exists() and config_file.read_bytes() == b'user settings'
        assert neighbour.read_bytes() == b'unrelated mod'
        assert (Path(result['backup']) / 'files/BepInEx/plugins/Fixture/Fixture.dll').read_bytes() == b'original fixture mod'
        assert not any(key.startswith('_') for key in result)
        assert app.UPDATES is None and app.PREVIEW is None and not app.UPDATE_ARCHIVES
        assert not any(mod['id'] == row['id'] for mod in app.snapshot()['mods'])
        plugin.write_bytes(b'reinstalled fixture mod')
        rejects('uninstall', {'token': preview['token']}, 'preview')
        with patch.object(app.inventory, 'scan', return_value=[row | {'source': 'Steam Workshop', 'workshop_id': '123'}]):
            steam = app.snapshot()['mods'][0]
            assert not steam['can_uninstall'] and 'Steam' in steam['uninstall_reason']
            rejects('uninstall-preview', {'id': steam['id']}, 'Steam')
        archive = Path(temporary) / 'managed.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('Managed.dll', b'managed mod')
        staged = app.dispatch('stage', {'path': str(archive)})
        app.dispatch('install', {'token': staged['token']})
        managed = app.row_by_id(staged['token'])
        assert managed['can_uninstall'] and managed['source'] == 'GK2MT'
        preview = app.dispatch('uninstall-preview', {'id': managed['id']})
        app.dispatch('uninstall', {'token': preview['token']})
        assert not (game / 'BepInEx/plugins/Managed.dll').exists()
        assert not app.package_manager(app.settings()).state['packages']
        assert stopped.call_count > 1


def cached_update_actions():
    """Local inventory changes remove stale update rows without cancelling other downloads."""
    with TemporaryDirectory() as temporary, patch.object(app, 'DATA', Path(temporary) / 'data'), \
         patch.object(app, 'UPDATES', None), patch.object(app, 'NEXUS_KEY', 'fixture-key'), \
         patch.object(app, 'DOWNLOADS', {}), patch.object(app.manager, 'ensure_game_stopped'):
        root = Path(temporary)
        game, workshop = root / 'game', root / '4358690'
        workshop.mkdir()
        plugins, configs = game / 'BepInEx/plugins', game / 'BepInEx/config'
        plugins.mkdir(parents=True)
        configs.mkdir()
        (game / 'GraveyardKeeper2.exe').touch()
        metadata = {}
        for index, name in enumerate(('Example', 'Neighbour'), 10):
            (plugins / (name + '.dll')).write_bytes(b'original ' + name.encode())
            (configs / (name + '.cfg')).write_text(
                f'## Settings file was created by plugin {name} v1.0\n', encoding='utf-8')
            metadata['plugins:' + name.lower() + '.dll'] = {'nexus_mod_id': index}
        config = {'game': str(game), 'workshop': str(workshop), 'metadata': metadata}
        manager.save_json(app.DATA / 'settings.json', config)

        def releases(key, rows, links):
            return {'account': {'premium': False}, 'categories': {}, 'updates': [
                {'id': row['id'], 'name': row['name'], 'nexus_mod_id': row['nexus_mod_id'],
                 'file_id': 100 + row['nexus_mod_id'], 'version': '9.0',
                 'installed_version': row['version'], 'known_version': True,
                 'downloadable': False, 'status': 'Update available'}
                for row in rows if row.get('nexus_mod_id')]}

        with patch.object(app.integrations, 'nexus_check', side_effect=releases) as check, \
             patch.object(app.integrations, 'steam_workshop_titles') as workshop_titles, \
             patch.object(app, 'stop_download_panel') as stop:
            report = app.dispatch('updates', {})
            old_id, neighbour_id = 'plugins:example.dll', 'plugins:neighbour.dll'
            assert report['_checked_rows'][old_id]['version'] == '1.0'
            neighbour_update = next(item for item in report['updates'] if item['id'] == neighbour_id)
            job = {key: None for key in ('mod_id', 'name', 'version', 'message', 'received', 'total',
                                        'retryable', 'cancellable')}
            job.update(id='fixture-download', update_id=neighbour_id, state='waiting', _stop=threading.Event())
            app.DOWNLOADS[job['id']] = job
            archive = root / 'Example.zip'
            with zipfile.ZipFile(archive, 'w') as zipped:
                zipped.writestr('Example.dll', b'new Example')
            metadata = {'name': 'Example', 'version': '2.0', 'nexus_mod_id': 10, 'file_id': 110}
            staged = app.dispatch('stage', {'path': str(archive), 'metadata': metadata})
            app.dispatch('install', {'token': staged['token'], 'metadata': metadata,
                                    'choices': {path: staged['token'] for path in staged['conflicts']}})
            check.reset_mock()
            # A former external row disappears when its files become package-owned.
            state = app.snapshot()
            assert not any(row['id'] == old_id for row in state['mods'])
            assert any(row['id'] == staged['token'] and row['version'] == '2.0' for row in state['mods'])
            assert state['updates']['_stale'] and state['updates']['updates'] == [neighbour_update]
            assert old_id not in state['updates']['_signatures']
            assert app.verified_update(config, neighbour_id)[0] == neighbour_update
            assert job['state'] == 'waiting' and not job['_stop'].is_set()
            stop.assert_not_called()
            check.assert_not_called()
            workshop_titles.assert_not_called()

            # Config/log version changes discovered by state or Rescan also prune old entries.
            for read in (lambda: app.snapshot(), lambda: app.dispatch('rescan', {})):
                app.dispatch('updates', {})
                current = app.snapshot()['mods']
                version = next(row['version'] for row in current if row['id'] == neighbour_id)
                (configs / 'Neighbour.cfg').write_text(
                    f'## Settings file was created by plugin Neighbour v{version}.1\n', encoding='utf-8')
                check.reset_mock()
                state = read()
                assert state['updates']['_stale']
                assert not any(item['id'] == neighbour_id for item in state['updates']['updates'])
                assert neighbour_id not in state['updates']['_signatures']
                check.assert_not_called()
            # The shared commit guard returns a useful error if a refresh invalidates its row.
            app.dispatch('updates', {})
            (configs / 'Neighbour.cfg').write_text(
                '## Settings file was created by plugin Neighbour v8.0\n', encoding='utf-8')
            try:
                app.verified_update(config, neighbour_id)
                raise AssertionError('A stale update row was accepted')
            except ValueError as error:
                assert 'Check for updates again' in str(error)
            # Fixture/legacy reports without a binding retain their existing behavior.
            legacy = {'updates': [{'id': neighbour_id}], '_game': str(game.resolve())}
            app.UPDATES = legacy
            assert app.snapshot()['updates'] is legacy
    print('Cached updates passed: adopted packages, external versions, offline pruning, active download preservation.')


def discovery_actions():
    """GK2MT remembers Nexus provenance independently of legacy deployment records."""
    with TemporaryDirectory() as temporary, patch.object(app, 'DATA', Path(temporary) / 'data'), \
         patch.object(app, 'UPDATES', None), patch.object(app.manager, 'ensure_game_stopped'), \
         patch.object(app.integrations, 'steam_workshop_titles') as fetch:
        root = Path(temporary)
        game, workshop = root / 'game', root / '4358690'
        workshop.mkdir()
        plugin = game / 'BepInEx/plugins/QueueCount.dll'
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(b'original QueueCount')
        (game / 'GraveyardKeeper2.exe').touch()
        config_file = game / 'BepInEx/config/queuecount.cfg'
        config_file.parent.mkdir()
        config_file.write_text('## Settings file was created by plugin Queue Count v0.3.0\n', encoding='utf-8')
        component = game / 'BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll'
        component.parent.mkdir()
        component.write_bytes(b'configuration component')
        stale_plugin = plugin.parent / 'Replaced.dll'
        stale_plugin.write_bytes(b'replaced legacy mod')
        (config_file.parent / 'configurationmanager.cfg').write_text(
            '## Settings file was created by plugin Configuration Manager v19.0\n', encoding='utf-8')
        manifest = plugin.parent / 'vortex.deployment.json'
        manager.save_json(manifest, {'targetPath': str(plugin.parent), 'files': [
            {'relPath': plugin.relative_to(plugin.parent).as_posix(),
             'source': 'Queue Count 74 0.3.1 2026-09-28T01-39Z abc', 'time': plugin.stat().st_mtime * 1000},
            {'relPath': component.relative_to(plugin.parent).as_posix(),
             'source': 'BepInEx 48 1.1 2026-09-28T01-39Z abc', 'time': component.stat().st_mtime * 1000},
            {'relPath': stale_plugin.name, 'source': 'Replaced 198 1.0 2026-09-28T01-39Z abc', 'time': 0}]})
        config = {'game': str(game), 'workshop': str(workshop)}
        manager.save_json(app.DATA / 'settings.json', config)
        library = app.package_manager(config)
        cache_path = library.data / 'nexus-discovery.json'
        before = {path: path.read_bytes() for path in (plugin, component, manifest, config_file)}
        app.snapshot()
        assert not cache_path.exists(), 'Routine snapshots must not import legacy metadata.'
        state = app.dispatch('rescan', {})
        assert not state['nexus_discovery_warning']
        saved = manager.read_json(cache_path, {})
        cached_bytes = cache_path.read_bytes()
        identifier = 'plugins:queuecount.dll'
        assert saved[identifier]['nexus_mod_id'] == 74 and saved[identifier]['package_version'] == '0.3.1'
        assert saved[identifier]['installed_hashes'][plugin.relative_to(game).as_posix()] == manager.digest(plugin)
        foundation_id = 'plugins:configurationmanager'
        assert saved[foundation_id]['package_version'] == '1.1'
        assert saved[foundation_id]['installed_hashes']['winhttp.dll'] is None
        assert saved['plugins:replaced.dll'] == {'nexus_mod_id': 198}
        assert {path: path.read_bytes() for path in before} == before
        # An ID-only app record cannot inherit another legacy package's release version.
        changed = json.loads(before[manifest])
        changed['files'][-1].update(source='Replaced 199 8.0 2026-09-28T01-39Z abc',
                                  time=stale_plugin.stat().st_mtime * 1000)
        manager.save_json(manifest, changed)
        app.dispatch('rescan', {})
        row = app.row_by_id('plugins:replaced.dll')
        assert row['nexus_mod_id'] == 198 and row['package_version'] is None
        assert row['display_version'] == 'Unknown' and row['plugin_version'] == 'Unknown'
        detected = config_file.parent / 'replaced.cfg'
        detected.write_text('## Settings file was created by plugin Replaced v1.2\n', encoding='utf-8')
        row = app.row_by_id('plugins:replaced.dll')
        assert row['display_version'] == '1.2' and row['package_version_stale']
        assert cache_path.read_bytes() == cached_bytes
        # A raw archive hint is never a detected plugin version, even with a known app archive.
        changed['files'][0].update(source='Queue Count 99 8.0 2026-09-28T01-39Z abc')
        manager.save_json(manifest, changed)
        config_file.unlink()
        row = app.row_by_id(identifier)
        assert row['display_version'] == '0.3.1' and row['plugin_version'] == 'Unknown'
        plugin.write_bytes(b'foreign replacement')
        changed['files'][0]['time'] = plugin.stat().st_mtime * 1000
        manager.save_json(manifest, changed)
        row = app.row_by_id(identifier)
        assert row['package_version_stale'] and row['display_version'] == 'Unknown'
        assert row['version'] == 'Unknown' and row['plugin_version'] == 'Unknown'
        plugin.write_bytes(before[plugin])
        config_file.write_bytes(before[config_file])
        manifest.unlink()
        state = app.dispatch('rescan', {})
        row = next(row for row in state['mods'] if row['id'] == identifier)
        assert row['source'] == 'Manual' and row['nexus_mod_id'] == 74
        assert row['version'] == '0.3.0' and app.integrations.installed_version(row) == '0.3.1'
        assert row['display_version'] == '0.3.1' and row['plugin_version'] == '0.3.0'
        assert not row['package_version_stale'] and 'installed_hashes' not in row
        assert cache_path.read_bytes() == cached_bytes
        # Reversible disable renames retain the tracked archive identity.
        disabled = plugin.with_name(plugin.name + inventory.DISABLED)
        plugin.rename(disabled)
        row = app.row_by_id(identifier)
        assert not row['enabled'] and app.integrations.installed_version(row) == '0.3.1'
        disabled.rename(plugin)
        plugin.write_bytes(b'replaced QueueCount')
        config_file.write_text('## Settings file was created by plugin Queue Count v0.4.0\n', encoding='utf-8')
        row = app.row_by_id(identifier)
        assert row['nexus_mod_id'] == 74 and row['package_version_stale']
        assert app.integrations.installed_version(row) == '0.4.0'
        # Foundation matching includes missing/root files, rather than only its component.
        assert app.integrations.installed_version(app.row_by_id(foundation_id)) == '1.1'
        (game / 'winhttp.dll').write_bytes(b'new foundation bootstrap')
        assert app.integrations.installed_version(app.row_by_id(foundation_id)) == 'Unknown'
        # Lingering legacy records are initial hints, never permission to rebind changed files.
        legacy = json.loads(before[manifest])
        legacy['files'][0]['time'] = plugin.stat().st_mtime * 1000
        manager.save_json(manifest, legacy)
        state = app.dispatch('rescan', {})
        assert cache_path.read_bytes() == cached_bytes
        row = next(row for row in state['mods'] if row['id'] == identifier)
        assert row['package_version_stale'] and row['display_version'] == '0.4.0'
        assert app.integrations.installed_version(app.row_by_id(foundation_id)) == 'Unknown'
        manifest.unlink()
        # Explicit links and verified foundation provenance take precedence over discovery.
        manager.save_json(app.DATA / 'settings.json', config | {'metadata': {
            identifier: {'nexus_mod_id': 99, 'file_id': 999, 'name': 'My Queue Count', 'version': '8.0'}}})
        row = app.row_by_id(identifier)
        assert row['nexus_mod_id'] == 99 and row['file_id'] == 999 and row['name'] == 'My Queue Count'
        assert row['version'] == '8.0'
        assert row['display_version'] == '8.0' and not row['package_version_stale']
        assert row['plugin_version'] == '0.4.0' and app.integrations.installed_version(row) == '8.0'
        # An explicit installed-version edit also overrides a still-present legacy hint.
        manager.save_json(manifest, {'targetPath': str(plugin.parent), 'files': [{
            'relPath': plugin.name, 'source': 'Queue Count 74 0.3.1 2026-09-28T01-39Z abc',
            'time': plugin.stat().st_mtime * 1000}]})
        assert app.row_by_id(identifier)['display_version'] == '8.0'
        manifest.unlink()
        edited = manager.read_json(app.DATA / 'settings.json', {})
        edited['metadata'][foundation_id] = {'version': '7.0'}
        manager.save_json(app.DATA / 'settings.json', edited)
        manager.save_json(library.data / 'foundation.json', {'nexus_mod_id': 48, 'file_id': 50,
            'package_version': '2.0', 'installed_hashes': {
                component.relative_to(game).as_posix(): manager.digest(component)}})
        row = app.row_by_id(foundation_id)
        assert row['file_id'] == 50 and app.integrations.installed_version(row) == '2.0'
        assert row['display_version'] == '2.0' and row['plugin_version'] == '19.0'
        # Imported packages have their own version/file identity and are authoritative.
        archive = root / 'QueueCount.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('QueueCount.dll', b'GK2MT tracked QueueCount')
        metadata = {'name': 'Queue Count', 'version': '0.5.0', 'nexus_mod_id': 74, 'file_id': 555}
        staged = app.dispatch('stage', {'path': str(archive), 'metadata': metadata})
        app.dispatch('install', {'token': staged['token'], 'metadata': metadata,
                                'choices': {path: staged['token'] for path in staged['conflicts']}})
        row = app.row_by_id(staged['token'])
        assert row['source'] == 'GK2MT' and row['file_id'] == 555
        assert app.integrations.installed_version(row) == '0.5.0'
        fetch.assert_not_called()
    print('Nexus discovery passed: independent links, hash provenance, disabled rename, foundation and managed precedence.')


def workshop_setup_actions():
    """First-run setup is explicit and cannot reclaim an imported package's loader."""
    from test_workshop_setup import fixture, files, pe_dll
    with TemporaryDirectory() as temporary:
        game, workshop, data, target = fixture(Path(temporary))
        config = {'game': str(game), 'workshop': str(workshop)}
        with patch.object(app, 'DATA', data), patch.object(app, 'DOWNLOADS', {}), \
             patch.object(app, 'PREVIEW', {'cached': True}), \
             patch.object(app, 'UPDATES', {'cached': True}), \
             patch.object(app, 'PROFILE_PREVIEW', {'cached': True}), \
             patch.object(app, 'NEXUS_KEY', ''), patch.object(app, 'DECK_SESSION', None), \
             patch.object(manager, 'ensure_game_stopped'), \
             patch.object(app.integrations, 'WORKSHOP_ASSET', ('https://github.com/fixture/loader.dll', hashlib.sha256(pe_dll()).hexdigest())), \
             patch.object(app.integrations, '_github_asset', return_value=data / 'fixture-download.dll') as download:
            manager.save_json(data / 'settings.json', config)
            before = files(game)
            assert app.snapshot()['workshop_setup']['state'] == 'ready'
            assert files(game) == before, 'Reading setup status modified the game.'
            download.assert_not_called()
            assert app.PREVIEW and app.UPDATES and app.PROFILE_PREVIEW

            library = app.package_manager(config)
            for field in ('paths', 'retired_paths', 'adopted_paths'):
                for relative in (app.integrations.WORKSHOP_TARGET,
                                 'BepInEx/plugins/Other/GK2_WorkshopLoader.dll.gk2mt-disabled',
                                 'BepInEx/patchers/Other/GK2.WorkshopAutoLoader.dll'):
                    library.state['packages'] = [{'name': 'Previously imported loader', field: [relative]}]
                    manager.save_json(library.state_path, library.state)
                    blocked = app.workshop_setup(config, install=True)
                    assert blocked['state'] == 'blocked' and 'imported package' in blocked['message']
                    assert files(game) == before
            (data / 'fixture-download.dll').write_bytes(pe_dll())
            assert app.workshop_setup(config, install=True)['state'] == 'blocked'
            download.assert_not_called()
            assert not target.exists()
            library.state['packages'] = []
            manager.save_json(library.state_path, library.state)

            with patch.object(app, 'DOWNLOADS', {'job': {'state': 'waiting'}}):
                try:
                    app.dispatch('workshop-loader-setup', {})
                    raise AssertionError('Loader setup raced an active download.')
                except ValueError as error:
                    assert 'download' in str(error)
                assert not target.exists()
            installed = app.dispatch('workshop-loader-setup', {})
            assert installed['installed'] and installed['files'] == 1
            assert target.read_bytes() == pe_dll()
            assert app.PREVIEW is None and app.UPDATES is None and app.PROFILE_PREVIEW is None
            assert app.snapshot()['workshop_setup']['installed']
            assert app.snapshot()['workshop_loader']['kind'] == 'workshop'
            stamp = target.stat().st_mtime_ns
            assert app.dispatch('workshop-loader-setup', {})['files'] == 0
            assert target.stat().st_mtime_ns == stamp
            assert download.call_count == 1, 'Detection should skip the second GitHub download.'
    print('Workshop first-run bridge passed: explicit install, managed ownership and download guards.')


def workshop_reenable_actions():
    from test_workshop_setup import fixture, pe_dll
    from test_inventory import put
    with TemporaryDirectory() as temporary:
        game, workshop, data, loader = fixture(Path(temporary))
        put(game, app.integrations.WORKSHOP_TARGET, '')
        loader.write_bytes(pe_dll())
        put(workshop, '123/MoveBuildings.dll', 'BepInPlugin\0')
        trust = put(game, inventory.TRUST, '# Keep other decisions\n123 = BLOCKED\n456 = BLOCKED\n')
        with patch.object(app, 'DATA', data), patch.object(app, 'DECK_SESSION', None), \
             patch.object(app, 'UPDATES', None), patch.object(app, 'PREVIEW', {'old': True}), \
             patch.object(manager, 'ensure_game_stopped'), patch.object(app.webbrowser, 'open') as launch:
            manager.save_json(data / 'settings.json', {'game': str(game), 'workshop': str(workshop)})
            result = app.dispatch('toggle', {'id': 'workshop:123', 'enabled': True})
            assert result['approval_required'] and result['mod_name'] == 'MoveBuildings'
            assert 'unblocked' in result['message'] and 'approve' in result['message']
            current = app.row_by_id('workshop:123')
            assert current['approval_required'] and not current['enabled'] and current['trust_state'] == 'ask'
            assert '123 =' not in trust.read_text() and '456 = BLOCKED' in trust.read_text()
            assert app.PREVIEW is None
            launch.assert_not_called()
            # Repeated enable requests do not grant consent or install any plugin.
            before = trust.read_bytes()
            assert app.dispatch('toggle', {'id': 'workshop:123', 'enabled': True})['approval_required']
            assert trust.read_bytes() == before and not current['paths']
            app.dispatch('toggle', {'id': 'workshop:123', 'enabled': False})
            assert app.row_by_id('workshop:123')['trust_state'] == 'no'
            assert not app.row_by_id('workshop:123')['approval_required']
            # Simulate the loader's explicit user approval and deployment after launch.
            trust.write_text('123 = ' + 'a' * 64 + '\n456 = BLOCKED\n')
            put(game, 'BepInEx/plugins/_Workshop/123/MoveBuildings.dll', 'BepInPlugin\0')
            current = app.row_by_id('workshop:123')
            assert current['enabled'] and not current['approval_required']
            app.dispatch('toggle', {'id': current['id'], 'enabled': False})
            assert 'previous-approved-sha256=' + 'a' * 64 in trust.read_text()
            (game / current['paths'][0]).unlink()
            result = app.dispatch('toggle', {'id': current['id'], 'enabled': True})
            assert not result.get('approval_required') and 'previous approval is kept' in result['message']
            current = app.row_by_id('workshop:123')
            assert current['trust_state'] == 'yes' and not current['approval_required'] and not current['paths']
            assert '123 = ' + 'a' * 64 in trust.read_text() and '456 = BLOCKED' in trust.read_text()
            launch.assert_not_called()
    print('Workshop re-enable passed: remembered original approval, no new content approval/launch, missing mirror, first approval guide and block again.')


def github_foundation_actions():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        game, data = root / 'game', root / 'data'
        game.mkdir()
        (game / 'GraveyardKeeper2.exe').touch()
        config = {'game': str(game), 'workshop': str(root / 'workshop'), 'deck': {'host': '192.0.2.50'}}
        with patch.object(app, 'DATA', data), patch.object(app, 'DECK_SESSION', None), \
             patch.object(app, 'NEXUS_KEY', 'dummy-existing-key'), patch.object(app, 'UPDATES', None):
            manager.save_json(data / 'settings.json', config)
            library = app.package_manager(config)
            for relative in app.BEPINEX_FILES:
                path = game / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'Existing foundation')
            tracking = library.data / 'foundation.json'
            manager.save_json(tracking, {'existing': 'provenance must survive'})
            before = {path: path.read_bytes() for base in (game, data) for path in base.rglob('*') if path.is_file()}
            with patch.object(app.integrations, 'setup_installer') as installer, \
                 patch.object(app.manager, 'ensure_game_stopped') as stopped, \
                 patch.object(app, 'package_manager') as ownership:
                result = app.dispatch('setup', {})
                assert result['already_installed'] and result['files'] == 0
                installer.assert_not_called()
                stopped.assert_not_called()
                ownership.assert_not_called()
            assert {path: path.read_bytes() for base in (game, data) for path in base.rglob('*') if path.is_file()} == before
            assert app.NEXUS_KEY == 'dummy-existing-key'
            component = game / 'BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll'
            component.parent.mkdir(parents=True)
            component.write_bytes(b'Verified fixture component')
            installed = {'source': 'GitHub', 'files': 1, 'backup': '', 'package_version': '5.4.23.5',
                         'components': [{'name': 'Configuration Manager', 'version': '19.0'}],
                         'installed_hashes': {component.relative_to(game).as_posix(): manager.digest(component)}}
            app.record_foundation(config, installed)
            row = {'id': 'plugins:configurationmanager', 'name': 'Configuration Manager',
                   'paths': [component.relative_to(game).as_posix()], 'enabled': True,
                   'source': 'Vortex', 'nexus_mod_id': 48, 'file_id': 12, 'version': '1.1',
                   'warnings': ["Vortex's recorded version is old."]}
            with patch.object(app.inventory, 'scan', return_value=[row]):
                current = app.snapshot()['mods'][0]
            assert current['display_version'] == '19.0' and current['source'] == 'Manual'
            assert current['setup_source'] == 'GitHub' and current['nexus_mod_id'] is None and current['file_id'] is None
            assert not current['can_uninstall'] and not current['warnings']
            assert not app.profiles.capture('Fixture', [current], game)['mods']
            installed['components'][0]['preserved'] = True
            app.record_foundation(config, installed)
            assert app.foundation_metadata(library) == {}, 'Preserved older plugins must keep their own provenance.'
    print('GitHub foundation bridge passed: detected skip preserves settings, honest component versions and profile protection.')


def main():
    helper_actions()
    nexus_actions()
    workshop_actions()
    workshop_setup_actions()
    workshop_reenable_actions()
    github_foundation_actions()
    uninstall_actions()
    cached_update_actions()
    discovery_actions()
    found = inventory.discover()
    game, workshop = Path(found["game"]), Path(found["workshop"])
    observed = [game / "BepInEx/config/GK2_WorkshopLoader.trust.txt",
                game / "BepInEx/plugins/GK2.Framework.dll",
                game / "vortex.deployment.gyk2-bepinex-layout.json"]
    def fingerprints():
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in observed if path.is_file()}
    before_files = fingerprints()
    with TemporaryDirectory() as temporary, patch.object(app, "DATA", Path(temporary)):
        saved = {"game": str(game), "workshop": str(workshop), "import_folder": str(Path(temporary) / "imports")}
        manager.save_json(app.DATA / "settings.json", saved)
        settings_before = (app.DATA / "settings.json").read_bytes()
        bridge = desktop.Bridge(app)
        for action, body in ((None, None), (123, {}), ('settings', []),
                             ('settings', 'text'), ('settings', 1),
                             ('settings', {'payload': 'x' * (1024 * 1024)})):
            response = bridge.request(action, body)
            assert response['ok'] is False and response['error'], (action, type(body))
        assert (app.DATA / "settings.json").read_bytes() == settings_before
        response = bridge.request('state', None)
        assert response['ok'] is True, response
        state = response['result']
        rows = inventory.scan(game, workshop)
        assert len(state["mods"]) == len(rows)
        assert {row["id"] for row in state["mods"]} == {row["id"] for row in rows}
        assert all("can_toggle" in row for row in state["mods"])
        assert all("can_uninstall" in row and "uninstall_reason" in row for row in state["mods"])
        assert state["workshop_loader"] == inventory.loader_info(game)
        print(f"Read-only live scan: {len(rows)} rows, {sum(row['enabled'] for row in rows)} enabled.")
        assert bridge.request('settings', {'game': str(Path(temporary) / 'missing-game')})['ok'] is False
        assert (app.DATA / "settings.json").read_bytes() == settings_before
        pending_workshop = Path(temporary) / 'missing-workshop'
        assert bridge.request('settings', {'workshop': str(pending_workshop)})['ok'] is True
        assert app.settings()['workshop'] == str(pending_workshop) and not pending_workshop.exists()
        assert not app.settings()['quick_setup_completed']
        manager.save_json(app.DATA / 'settings.json', saved)
        response = bridge.request('discover', {})
        assert response['ok'] is True and response['result']['game'] == found['game']
        response = bridge.request('deck-guide', None)
        assert response['ok'] is True and 'sshd.service' in response['result']['text']
        with patch.object(app.manager, "ensure_game_stopped"), \
             patch.object(app.integrations, "setup_installer", return_value={"requires_download": True}) as setup:
            response = bridge.request('setup', {'archive': 'test.zip', 'file_id': 123})
            assert response['ok'] is True and response['result']['requires_download'] is True
            setup.assert_called_once_with(game, app.DATA, key=app.NEXUS_KEY, archive="test.zip", file_id=123)
        bridge._closing = True
        with patch.object(app, 'dispatch') as dispatch:
            assert bridge.request('discover', {})['ok'] is False
            dispatch.assert_not_called()
        bridge._closing = False
        # A request queued before closing must not mutate after shutdown.
        with patch.object(app, 'LOCK') as lock, patch.object(app, 'dispatch') as dispatch:
            lock.__enter__.side_effect = lambda: setattr(bridge, '_closing', True)
            assert bridge.request('discover', {})['ok'] is False
            dispatch.assert_not_called()
    assert fingerprints() == before_files, "Desktop bridge checks modified a live game file"
    with TemporaryDirectory() as temporary:
        folder = Path(temporary)
        library = manager.Manager(folder / 'data', folder / 'game')
        dll = library.game / 'BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll'
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b'installed foundation')
        provenance = {'nexus_mod_id': 48, 'file_id': 12, 'package_version': '1.2',
                      'installed_hashes': {dll.relative_to(library.game).as_posix(): hashlib.sha256(dll.read_bytes()).hexdigest()}}
        (library.game / 'GraveyardKeeper2.exe').touch()
        with patch.object(app, 'settings', return_value={'game': str(library.game), 'workshop': str(folder / '4358690')}), \
             patch.object(app, 'package_manager', return_value=library), \
             patch.object(app.manager, 'ensure_game_stopped'), \
             patch.object(app.integrations, 'setup_installer', return_value={'requires_download': False, **provenance}):
            app.dispatch('setup', {'archive': 'fixture.zip'})
        assert app.foundation_metadata(library)['file_id'] == 12
        disabled = dll.with_name(dll.name + inventory.DISABLED)
        dll.rename(disabled)
        assert app.foundation_metadata(library)['package_version'] == '1.2'
        disabled.write_bytes(b'redeployed by another manager')
        assert app.foundation_metadata(library) == {}
    deck_actions()
    shutdown_actions()
    print("App bridge, uninstall preview/confirmation, and Deck checks passed; credentials excluded and live game files unchanged.")


if __name__ == "__main__":
    main()
