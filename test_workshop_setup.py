"""Offline missing-only loader checks; inert PE fixtures and temporary files only."""
import hashlib
import struct
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import integrations
import manager
from test_manager import put as put_text


def put(root, relative, content='fixture'):
    path = put_text(root, relative, content if isinstance(content, str) else '')
    if isinstance(content, bytes):
        path.write_bytes(content)
    return path


def pe_dll():
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


class Response(BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.headers = {'Content-Length': str(len(content))}


def fixture(root, foundation=True):
    game, workshop, data = root / 'game', root / 'missing-workshop', root / 'data'
    put(game, 'GraveyardKeeper2.exe', 'inert game')
    if foundation:
        for relative in integrations.WORKSHOP_BEPINEX_FILES:
            put(game, relative, 'foundation marker')
    return game, workshop, data, game / integrations.WORKSHOP_TARGET


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def rejects(action):
    try:
        action()
    except (ValueError, OSError, RuntimeError):
        return
    raise AssertionError('Unsafe loader setup succeeded.')


def first_install_and_missing_workshop(root, opener):
    game, workshop, data, target = fixture(root)
    put(game, 'BepInEx/config/GK2_WorkshopLoader.trust.txt', '123 = BLOCKED # personal choice\n')
    put(game, 'BepInEx/plugins/Other.dll', 'unrelated plugin')
    before = files(game)
    status = integrations.workshop_loader_setup(game, workshop, data)
    assert status['state'] == 'ready' and status['can_install'] and status['source'] == 'GitHub'
    assert status['hash'] == integrations.WORKSHOP_ASSET[1] and files(game) == before and not data.exists()
    opener.open.assert_not_called()
    installed = integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert installed['installed'] and installed['files'] == 1 and target.read_bytes() == pe_dll()
    assert {p: b for p, b in files(game).items() if p != integrations.WORKSHOP_TARGET} == before
    assert not workshop.exists() and opener.open.call_count == 1
    original = files(game)
    again = integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert again['installed'] and again['files'] == 0 and files(game) == original
    assert opener.open.call_count == 1 and not list(target.parent.glob('.gk2mt-workshop-*'))


def existing_loaders_are_preserved(root, opener):
    game, workshop, data, target = fixture(root)
    for relative in ('BepInEx/patchers/GK2.WorkshopAutoLoader.dll',
                     'BepInEx/patchers/nested/GK2_WorkshopLoader.dll'):
        existing = put(game, relative, pe_dll())
        before = files(game)
        status = integrations.workshop_loader_setup(game, workshop, data, install=True)
        assert status['installed'] and status['files'] == 0 and files(game) == before
        existing.unlink()
    for relative in ('BepInEx/plugins/nested/GK2_WorkshopLoader.dll',
                     'BepInEx/plugins/GK2.WorkshopAutoLoader.dll'):
        existing = put(game, relative, pe_dll())
        before = files(game)
        status = integrations.workshop_loader_setup(game, workshop, data, install=True)
        assert status['state'] == 'blocked' and not status['installed'] and 'Move it' in status['message']
        assert files(game) == before
        existing.unlink()
    for relative in ('BepInEx/patchers/GK2.WorkshopAutoLoader.dll.gk2mt-disabled',
                     'BepInEx/plugins/nested/GK2_WorkshopLoader.dll.gk2mt-disabled'):
        existing = put(game, relative, pe_dll())
        before = files(game)
        assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'disabled'
        assert files(game) == before
        existing.unlink()
    put(game, 'BepInEx/patchers/GK2.WorkshopAutoLoader.dll', pe_dll())
    put(game, integrations.WORKSHOP_TARGET, pe_dll())
    before = files(game)
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    assert files(game) == before and not data.exists()
    opener.open.assert_not_called()


def cache_corrupt_download_and_missing_foundation(root, opener):
    game, workshop, data, target = fixture(root, foundation=False)
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'missing_bepinex'
    opener.open.assert_not_called()
    for relative in integrations.WORKSHOP_BEPINEX_FILES:
        put(game, relative, 'foundation')
    cache = integrations._github_asset(data, integrations.WORKSHOP_ASSET)
    assert opener.open.call_count == 1
    integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert opener.open.call_count == 1
    target.unlink()
    cache.write_bytes(b'corrupt cache')
    integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert opener.open.call_count == 2 and target.read_bytes() == pe_dll()
    target.unlink()
    cache.unlink()
    opener.open.side_effect = lambda *a, **kw: Response(pe_dll() + b'wrong version')
    before = files(game)
    rejects(lambda: integrations.workshop_loader_setup(game, workshop, data, install=True))
    assert files(game) == before and not cache.exists() and not list(cache.parent.glob('*.part'))


def races_and_copy_failure(root, opener):
    game, workshop, data, target = fixture(root)
    def downloaded(*args, **kwargs):
        put(game, 'BepInEx/plugins/GK2.WorkshopAutoLoader.dll.gk2mt-disabled', pe_dll())
        return Response(pe_dll())
    opener.open.side_effect = downloaded
    status = integrations.workshop_loader_setup(game, workshop, data, install=True)
    assert status['state'] == 'disabled' and not target.exists()
    (game / 'BepInEx/plugins/GK2.WorkshopAutoLoader.dll.gk2mt-disabled').unlink()
    real_copy = manager.copy_atomic
    def raced(src, dst):
        real_copy(src, dst)
        target.write_bytes(b'external manager file')
    with patch.object(manager, 'copy_atomic', side_effect=raced):
        assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    assert target.read_bytes() == b'external manager file'
    target.unlink()
    before = files(game)
    with patch.object(manager, 'copy_atomic', side_effect=PermissionError('fixture locked path')):
        rejects(lambda: integrations.workshop_loader_setup(game, workshop, data, install=True))
    assert files(game) == before and not list(target.parent.glob('.gk2mt-workshop-*'))


def linked_noncanonical_loader_is_blocked(root, opener):
    game, workshop, data, target = fixture(root)
    outside = put(root, 'outside.dll', pe_dll())
    linked = game / 'BepInEx/plugins/nested/GK2_WorkshopLoader.dll'
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(outside)
    except OSError:
        print('SKIP symlinks unavailable')
        return
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    assert outside.read_bytes() == pe_dll() and not target.exists() and not data.exists()
    opener.open.assert_not_called()


def invalid_existing_and_running_game(root, opener):
    game, workshop, data, target = fixture(root)
    put(game, integrations.WORKSHOP_TARGET, b'invalid existing loader')
    before = files(game)
    assert integrations.workshop_loader_setup(game, workshop, data, install=True)['state'] == 'blocked'
    assert files(game) == before
    target.unlink()
    with patch.object(manager, 'ensure_game_stopped', side_effect=ValueError('Close the game')):
        rejects(lambda: integrations.workshop_loader_setup(game, workshop, data, install=True))
    opener.open.assert_not_called()


def main():
    checks = (first_install_and_missing_workshop, existing_loaders_are_preserved,
              cache_corrupt_download_and_missing_foundation, races_and_copy_failure,
              linked_noncanonical_loader_is_blocked, invalid_existing_and_running_game)
    asset = (integrations.WORKSHOP_ASSET[0], hashlib.sha256(pe_dll()).hexdigest())
    with patch.object(manager, 'ensure_game_stopped'), patch.object(integrations, 'WORKSHOP_ASSET', asset), \
            patch.object(integrations, '_account', side_effect=AssertionError('No account calls')), \
            patch.object(integrations, '_json', side_effect=AssertionError('No API calls')):
        for check in checks:
            with TemporaryDirectory() as temporary, patch.object(integrations.urllib.request, 'build_opener') as build:
                opener = build.return_value
                opener.open.side_effect = lambda *a, **kw: Response(pe_dll())
                check(Path(temporary), opener)
                print('PASS', check.__name__)
    print('Workshop setup checks passed; no live game files or credentials touched.')


if __name__ == '__main__':
    main()
