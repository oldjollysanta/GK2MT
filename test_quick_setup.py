"""Quick Setup and native-drop checks; inert files, dummy keys and temporary settings only."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
import desktop
import manager
from test_workshop_setup import pe_dll


def rejects(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError('An incomplete or invalid setup was accepted.')


def setup(root):
    game = root / 'Steam/steamapps/common/Graveyard Keeper 2'
    workshop = root / 'Steam/steamapps/workshop/content/4358690'
    game.mkdir(parents=True)
    (game / 'GraveyardKeeper2.exe').write_bytes(b'inert game marker')
    with patch.object(app, 'DATA', root / 'settings'), patch.object(app, 'DECK_SESSION', None), \
         patch.object(app.inventory, 'discover', return_value={'game': str(game), 'workshop': str(workshop)}):
        assert app.settings()['quick_setup_completed'] is False
        checks = app.dispatch('setup-check', {'workshop': '', 'import_folder': ''})
        assert checks['game']['status'] == 'ready' and checks['game_found']
        assert checks['workshop']['path'] == str(workshop)
        assert checks['workshop']['status'] == 'empty' and not checks['workshop_found']
        assert checks['import_folder']['status'] == 'empty' and not checks['loader_installed']
        assert not app.DATA.exists() and not workshop.exists(), 'Read-only check wrote files.'
        app.dispatch('settings', {'game': str(game), 'workshop': '', 'import_folder': ''})
        assert not app.settings()['quick_setup_completed'] and not workshop.exists()
        rejects(lambda: app.dispatch('quick-setup-complete', {'workshop_enabled': False}))
        assert not app.settings()['quick_setup_completed']
        for relative in app.BEPINEX_FILES:
            target = game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'inert BepInEx fixture')
        rejects(lambda: app.dispatch('quick-setup-complete', {'workshop_enabled': True}))
        result = app.dispatch('quick-setup-complete', {'workshop_enabled': False})
        assert result['quick_setup_completed'] and app.settings()['quick_setup_completed']
        assert not app.settings()['workshop_enabled']
        assert manager.read_json(app.DATA / 'settings.json', {})['quick_setup_completed'] is True
        assert app.snapshot()['quick_setup_completed'] is True
        app.dispatch('settings', {'game': str(game), 'quick_setup_completed': False})
        assert app.settings()['quick_setup_completed'], 'Saving unchanged directories reset completion.'
        rejects(lambda: app.dispatch('quick-setup-complete', {'workshop_enabled': True}))
        loader = game / app.integrations.WORKSHOP_TARGET
        loader.parent.mkdir(parents=True)
        loader.write_bytes(pe_dll())
        app.dispatch('quick-setup-complete', {'workshop_enabled': True})
        assert app.settings()['workshop_enabled']
        assert not workshop.exists(), 'GitHub loader setup must not require a Steam download.'
        invalid = root / 'not-a-directory'
        invalid.write_text('inert marker')
        original = (app.DATA / 'settings.json').read_bytes()
        rejects(lambda: app.dispatch('settings', {'workshop': str(invalid)}))
        assert (app.DATA / 'settings.json').read_bytes() == original
        other = root / 'other-game'
        other.mkdir()
        (other / 'GraveyardKeeper2.exe').write_bytes(b'inert marker')
        app.dispatch('settings', {'game': str(other)})
        assert not app.settings()['quick_setup_completed'], 'Changing game must require its setup check.'
        rejects(lambda: app.dispatch('quick-setup-complete', {'workshop_enabled': False}))


def session_only_key(root):
    with patch.object(app, 'DATA', root / 'key-state'), patch.object(app, 'NEXUS_KEY', ''), \
         patch.object(app, 'NEXUS_ERROR', ''), patch.object(app, 'NEXUS_SESSION', 0), \
         patch.object(app, 'UPDATES', None), \
         patch.object(app.inventory, 'discover', return_value={'game': str(root / 'game'), 'workshop': str(root / 'workshop')}), \
         patch.object(app, 'check_nexus', return_value={'account': {'name': 'Fixture'}, 'updates': []}):
        saved = app.DATA / 'nexus-key.bin'
        app.dispatch('nexus-connect', {'key': 'fixture-saved-key'})
        assert saved.is_file() and b'fixture-saved-key' not in saved.read_bytes()
        app.dispatch('nexus-connect', {'key': 'fixture-session-key', 'remember_key': False})
        assert app.NEXUS_KEY == 'fixture-session-key' and not saved.exists()
        app.restore_nexus()
        assert app.NEXUS_KEY == '', 'Session-only key survived restart.'
        assert 'fixture-session-key' not in json.dumps(app.settings())


def drops(root):
    class NativeFile:
        def __init__(self, path):
            self.Path = str(path)
    class InventedFile:
        Path = str(root / 'invented.zip')
    source = desktop.APP_URL + '#mods'
    file = NativeFile(root / 'mod.zip')
    assert desktop.native_drop_paths(source, [file, file], NativeFile) == [file.Path]
    assert desktop.native_drop_paths(source, [InventedFile(), file.Path, {'Path': file.Path}], NativeFile) == []
    assert desktop.native_drop_paths('https://example.invalid', [file], NativeFile) == []
    assert desktop.native_drop_paths(source, None, NativeFile) == []
    rejects(lambda: desktop.native_drop_paths(source, [NativeFile('relative.zip')], NativeFile))
    rejects(lambda: desktop.native_drop_paths(source, [file] * 101, NativeFile))


if __name__ == '__main__':
    with TemporaryDirectory(prefix='gk2mt-quick-setup-') as temporary:
        root = Path(temporary)
        setup(root)
        session_only_key(root)
        drops(root)
    print('Quick Setup completion, optional paths, session-only key and native drop trust checks passed.')
