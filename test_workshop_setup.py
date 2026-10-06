"""Offline Workshop loader setup checks; inert PE fixtures and temporary files only."""
import struct
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import integrations
import manager
from test_manager import put


def pe_dll():
    """A minimal inert PE32 DLL image for static validation; never executable test code."""
    data = bytearray(1024)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HH', data, 132, 0x14c, 1)
    struct.pack_into('<HH', data, 148, 224, 0x2000)
    struct.pack_into('<H', data, 152, 0x10b)
    data[376:384] = b'.text\0\0\0'
    struct.pack_into('<II', data, 392, 512, 512)
    data[512:526] = b'inert fixture\0'
    return bytes(data)


def fixture(root, foundation=True, payload=True):
    game, workshop, data = root / 'game', root / 'workshop', root / 'data'
    put(game, 'GraveyardKeeper2.exe', 'test game')
    workshop.mkdir()
    if foundation:
        for relative in integrations.WORKSHOP_BEPINEX_FILES:
            put(game, relative, 'foundation marker')
    source = workshop / integrations.WORKSHOP_ID / integrations.WORKSHOP_TARGET
    if payload:
        source.parent.mkdir(parents=True)
        source.write_bytes(pe_dll())
    return game, workshop, data, source, game / integrations.WORKSHOP_TARGET


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def rejects(action):
    try:
        action()
    except (ValueError, OSError):
        return
    raise AssertionError('Unsafe loader setup succeeded.')


def first_install_preserves_everything(root):
    game, workshop, data, source, target = fixture(root)
    put(game, 'BepInEx/config/GK2_WorkshopLoader.trust.txt', '123 = BLOCKED # personal choice\n')
    put(game, 'BepInEx/plugins/Other.dll', 'unrelated plugin')
    before = files(game)
    status = integrations.workshop_loader_setup(game, workshop, data)
    assert status['state'] == 'ready' and status['can_install'] and status['files'] == 0
    assert status['hash'] == manager.digest(source) and files(game) == before and not data.exists()
    installed = integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert installed['state'] == 'installed' and installed['installed'] and installed['files'] == 1
    assert target.read_bytes() == pe_dll() and manager.digest(target) == installed['hash']
    assert {p: content for p, content in files(game).items() if p != integrations.WORKSHOP_TARGET} == before
    # A changed Workshop release must never overwrite the already installed loader.
    source.write_bytes(pe_dll() + b'another release')
    original = files(game)
    again = integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert again['state'] == 'installed' and again['files'] == 0 and files(game) == original
    assert not list(target.parent.glob('.gk2mt-workshop-*')) and not data.exists()


def waiting_and_missing_foundation(root):
    game, workshop, data, source, target = fixture(root, foundation=False)
    before = files(game)
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'missing_bepinex'
    assert files(game) == before
    for relative in integrations.WORKSHOP_BEPINEX_FILES:
        put(game, relative, 'foundation marker')
    source.unlink()
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'waiting_workshop'
    for bad in (b'', b'not a DLL', b'MZ' + b'\0' * 300, pe_dll()[:600]):
        source.write_bytes(bad)
        status = integrations.workshop_loader_setup(game, workshop, data, install=True)
        assert status['state'] == 'waiting_workshop' and not status['can_install'] and not target.exists()
    non_dll = bytearray(pe_dll())
    struct.pack_into('<H', non_dll, 150, 0)
    source.write_bytes(non_dll)
    assert integrations.workshop_loader_setup(game, workshop, data)['state'] == 'waiting_workshop'
    with source.open('wb') as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    assert integrations.workshop_loader_setup(game, workshop, data)['state'] == 'waiting_workshop'
    source.write_bytes(pe_dll())
    (game / 'GraveyardKeeper2.exe').unlink()
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    assert not target.exists()


def existing_loaders_are_not_changed(root):
    game, workshop, data, source, target = fixture(root)
    legacy = put(game, 'BepInEx/patchers/GK2.WorkshopAutoLoader.dll', 'legacy loader')
    before = files(game)
    status = integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert status['state'] == 'blocked' and 'old or conflicting' in status['message'] and files(game) == before
    target.write_bytes(pe_dll())
    before = files(game)
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    assert files(game) == before
    legacy.unlink()
    target.unlink()
    disabled = target.with_name(target.name + '.gk2mt-disabled')
    disabled.write_bytes(pe_dll())
    before = files(game)
    status = integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert status['state'] == 'disabled' and not status['can_install'] and files(game) == before
    disabled.unlink()
    nested = target.parent / 'OldDisabled' / disabled.name
    nested.parent.mkdir()
    nested.write_bytes(pe_dll())
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'disabled'
    nested.unlink()
    target.write_bytes(b'invalid installed loader')
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    assert target.read_bytes() == b'invalid installed loader'


def truncated_download_reads_wait_for_steam(root):
    game, workshop, data, source, target = fixture(root)
    original_open, before = Path.open, files(game)
    for length in (64, 24, 2, 40):
        class Truncated(BytesIO):
            def read(self, size=-1):
                value = super().read(size)
                return value[:-1] if size == length else value
        def truncated_open(path, *args, **kwargs):
            if path == source and args and args[0] == 'rb':
                return Truncated(pe_dll())
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', truncated_open):
            status = integrations.workshop_loader_setup(game, workshop, data, install=True)
        assert status['state'] == 'waiting_workshop' and 'incomplete' in status['message']
        assert files(game) == before and not target.exists()


def running_and_failed_verification(root):
    game, workshop, data, source, target = fixture(root)
    before = files(game)
    with patch.object(manager, 'ensure_game_stopped', side_effect=ValueError('Close Graveyard Keeper 2')):
        rejects(lambda: integrations.workshop_loader_setup(game, workshop, data, install=True))
    assert files(game) == before and not target.exists()
    digest = manager.digest
    def bad_verification(path):
        return '0' * 64 if Path(path) == target else digest(path)
    with patch.object(manager, 'digest', side_effect=bad_verification):
        rejects(lambda: integrations.workshop_loader_setup(game, workshop, data, install=True))
    assert files(game) == before and not target.exists()
    assert not list(target.parent.glob('.gk2mt-workshop-*'))
    with patch.object(manager, 'copy_atomic', side_effect=PermissionError('simulated locked path')):
        rejects(lambda: integrations.workshop_loader_setup(game, workshop, data, install=True))
    assert files(game) == before and not target.exists()


def concurrent_destination_is_preserved(root):
    game, workshop, data, source, target = fixture(root)
    copy_atomic = manager.copy_atomic
    def race_copy(src, destination):
        copy_atomic(src, destination)
        target.write_bytes(b'another manager created this file')
    with patch.object(manager, 'copy_atomic', side_effect=race_copy):
        rejects(lambda: integrations.workshop_loader_setup(game, workshop, data, install=True))
    assert target.read_bytes() == b'another manager created this file'
    assert not list(target.parent.glob('.gk2mt-workshop-*'))


def linked_paths_rejected(root):
    game, workshop, data, source, target = fixture(root)
    outside = root / 'outside.dll'
    outside.write_bytes(pe_dll())
    source.unlink()
    try:
        source.symlink_to(outside)
    except OSError:
        print('SKIP symlinks unavailable on this Windows account')
        return
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    source.unlink()
    source.write_bytes(pe_dll())
    target.parent.mkdir(exist_ok=True)
    target.symlink_to(outside)
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    target.unlink()
    foundation = game / integrations.WORKSHOP_BEPINEX_FILES[0]
    foundation.unlink()
    foundation.symlink_to(outside)
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    foundation.unlink()
    foundation.write_bytes(b'foundation')
    alias = root / 'game-alias'
    alias.symlink_to(game, target_is_directory=True)
    assert integrations.workshop_loader_setup(alias, workshop, data, install=True)['state'] == 'blocked'
    alias = root / 'workshop-alias'
    alias.symlink_to(workshop, target_is_directory=True)
    assert integrations.workshop_loader_setup(game, alias, data, install=True)['state'] == 'blocked'
    assert outside.read_bytes() == pe_dll() and not target.exists()


def main():
    with patch.object(manager, 'ensure_game_stopped'):
        for check in (first_install_preserves_everything, waiting_and_missing_foundation,
                      existing_loaders_are_not_changed, truncated_download_reads_wait_for_steam, running_and_failed_verification,
                      concurrent_destination_is_preserved, linked_paths_rejected):
            with TemporaryDirectory() as temporary:
                check(Path(temporary))
                print('PASS', check.__name__)
    print('Workshop setup checks passed; no live game files or credentials touched.')


if __name__ == '__main__':
    main()
