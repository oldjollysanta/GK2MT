"""Isolated Proton setup checks; never connects to or changes a Steam Deck."""

from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import deck
import deck_proton as proton


HEADER = b'WINE REGISTRY Version 2\n;; Fixture registry\n\n#arch=win64\n'
KEY = rb'Software\\Wine\\AppDefaults\\GraveyardKeeper2.exe\\DllOverrides'
VALUE = b'"winhttp"="native,builtin"'


def raises(kind, call, *args, **kwargs):
    try:
        call(*args, **kwargs)
    except kind as error:
        return str(error)
    raise AssertionError(f'{call.__name__} should raise {kind.__name__}')


def fixture(library, registry=HEADER):
    game = library / 'steamapps/common/Graveyard Keeper 2'
    game.mkdir(parents=True, exist_ok=True)
    (game / 'GraveyardKeeper2.exe').touch()
    (game.parent.parent / 'appmanifest_4358690.acf').write_text(
        '"AppState" { "appid" "4358690" "installdir" "Graveyard Keeper 2" '
        '"buildid" "123456" "StateFlags" "4" }')
    prefix = library / 'steamapps/compatdata/4358690/pfx'
    (prefix / 'drive_c/windows').mkdir(parents=True)
    (prefix / 'user.reg').write_bytes(registry)
    (prefix / 'system.reg').write_bytes(HEADER)
    return game, prefix


def check_registry():
    unrelated = b'\n[Software\\\\Other] 123\n"winhttp"="builtin"\n"Name"="kept"\n'
    original = HEADER + unrelated
    changed = proton._proton_registry(original)
    assert changed.startswith(original) and b'[' + KEY + b']\n' in changed
    assert changed.count(VALUE) == 1 and proton._proton_registry(changed) == changed
    for newline in (b'\n', b'\r\n'):
        before = (HEADER + b'\n[' + KEY + b'] 123\n#time=abc\n'
                  b'"dinput8"="native"\n"winhttp"="builtin"\n' + unrelated).replace(b'\n', newline)
        expected = before.replace(b'"winhttp"="builtin"', VALUE, 1)
        assert proton._proton_registry(before) == expected
        assert proton._proton_registry(expected) == expected
        missing_value = before.replace(b'"winhttp"="builtin"' + newline, b'', 1)
        result = proton._proton_registry(missing_value)
        assert b'"dinput8"="native"' in result and result.endswith(unrelated.replace(b'\n', newline))
        assert proton._proton_registry(result) == result
    assert proton._proton_registry(HEADER + b'\n[' + KEY + b']').endswith(VALUE + b'\n')
    # Wine accepts indentation and whitespace around '=', so a later existing
    # value must not silently undo the newly inserted native override.
    spaced = HEADER + b'\n  [' + KEY + b'] 123\n\t"WiNhTtP" \t=  "builtin"\n'
    result = proton._proton_registry(spaced)
    assert b'"builtin"' not in result and result.count(KEY) == 1
    assert proton._proton_registry(result) == result
    for invalid in (b'REGEDIT4\n', HEADER + b'\x00', HEADER + b'x' * 16_000_001,
                    HEADER + b'\n[' + KEY + b']\n[' + KEY.upper() + b']\n',
                    HEADER + b'\n[' + KEY + b']\n"winhttp"="n"\n"WINHTTP"="b"\n',
                    HEADER + b'\n[' + KEY + b']\n"winhttp"=hex:11,22\n'):
        raises(ValueError, proton._proton_registry, invalid)


def check_processes(root):
    proc = root / 'proc'
    proc.mkdir()
    process = proc / '123'
    process.mkdir()
    # On Windows the synthetic files have uid 0; on Linux use their actual uid.
    with patch.object(os, 'getuid', return_value=process.stat().st_uid, create=True):
        for command, comm in ((b'/usr/bin/steam\0-silent\0', 'steam'),
                              (b'python3\0-c\0import sys;exec(...)\0', 'python3')):
            (process / 'cmdline').write_bytes(command)
            (process / 'comm').write_text(comm)
            proton._proton_idle(proc)
        for command, comm in ((b'/some/path/wineserver\0', 'wineserver'),
                              (b'python3\0/library/Proton/proton\0waitforexitandrun\0', 'python3'),
                              (b'Z:/game/GraveyardKeeper2.exe\0', 'GraveyardKeeper'),
                              (b'/wine64-preloader\0', 'wine64-preload')):
            (process / 'cmdline').write_bytes(command)
            (process / 'comm').write_text(comm)
            raises(RuntimeError, proton._proton_idle, proc)
        with patch.object(os, 'getuid', return_value=process.stat().st_uid + 1):
            proton._proton_idle(proc)
    raises(RuntimeError, proton._proton_idle, root / 'missing-proc')


def check_lock(prefix):
    calls = []
    fake_fcntl = SimpleNamespace(LOCK_EX=2, LOCK_NB=4,
                                flock=lambda fd, flags: calls.append((fd, flags)))
    lock = prefix.parent / 'pfx.lock'
    lock.write_bytes(b'preserve lock file')
    # Exercise real open/close and control flow; only Linux flock/idle are mocked.
    with patch.dict(sys.modules, {'fcntl': fake_fcntl}), \
         patch.object(os, 'O_NOFOLLOW', getattr(os, 'O_NOFOLLOW', 0), create=True), \
         patch.object(proton, '_proton_idle') as idle:
        with proton._proton_lock(prefix):
            assert calls[0][1] == 6 and os.fstat(calls[0][0])
            idle.assert_called_once_with()
        raises(OSError, os.fstat, calls[0][0])
        assert lock.read_bytes() == b'preserve lock file'
        fake_fcntl.flock = lambda *_: (_ for _ in ()).throw(BlockingIOError())
        def locked():
            with proton._proton_lock(prefix):
                raise AssertionError('Busy lock must not yield')
        raises(RuntimeError, locked)
        fake_fcntl.flock = lambda *_: None
        idle.side_effect = RuntimeError('Wine still running')
        raises(RuntimeError, locked)
        assert lock.read_bytes() == b'preserve lock file'


def check_setup(root):
    game, prefix = fixture(root / 'internal')
    registry = prefix / 'user.reg'
    sd_game = root / 'sd/steamapps/common/Graveyard Keeper 2'
    sd_game.mkdir(parents=True)
    (sd_game / 'GraveyardKeeper2.exe').touch()
    libraries = [root / 'internal', root / 'sd']
    assert proton._proton_prefix(sd_game, libraries) == prefix
    assert proton._proton_prefix(game, libraries + [root / 'internal']) == prefix
    status = proton.proton_setup(game, libraries)
    assert not status['configured'] and not status['loader_installed'] and not status['changed']
    assert not (prefix.parent / 'gk2mt-backups').exists()
    for name in ('winhttp.dll', 'doorstop_config.ini', 'BepInEx/core/BepInEx.dll', 'BepInEx/core/BepInEx.Preloader.dll'):
        path = game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    with patch.object(proton, '_proton_lock', return_value=nullcontext()), patch.object(proton, '_proton_idle'):
        result = proton.proton_setup(game, libraries, install=True)
        assert result['configured'] and result['changed'] and result['loader_installed']
        assert Path(result['backup']).read_bytes() == HEADER
        expected = registry.read_bytes()
        again = proton.proton_setup(game, libraries, install=True)
        assert again['configured'] and not again['changed'] and 'backup' not in again
        assert len(list((prefix.parent / 'gk2mt-backups').iterdir())) == 1
        assert registry.read_bytes() == expected
        registry.write_bytes(HEADER)
        with patch.object(proton.os, 'replace', side_effect=OSError('replace failed')):
            raises(OSError, proton.proton_setup, game, libraries, install=True)
        assert registry.read_bytes() == HEADER and not list(prefix.glob('.gk2mt-user-*'))
        assert all(path.read_bytes() == HEADER for path in (prefix.parent / 'gk2mt-backups').iterdir())
        with patch.object(proton, '_proton_idle', side_effect=RuntimeError('Wine running')):
            raises(RuntimeError, proton.proton_setup, game, libraries, install=True)
        assert registry.read_bytes() == HEADER and not list(prefix.glob('.gk2mt-user-*'))
        def concurrent_change():
            registry.write_bytes(HEADER + b'; changed by another writer\n')
        with patch.object(proton, '_proton_idle', side_effect=concurrent_change):
            raises(ValueError, proton.proton_setup, game, libraries, install=True)
        assert registry.read_bytes().endswith(b'; changed by another writer\n')
    check_lock(prefix)
    _, sd_prefix = fixture(root / 'sd')
    raises(ValueError, proton._proton_prefix, game, libraries)
    raises(ValueError, proton._proton_prefix, root / 'empty/steamapps/common/GK2', [])
    (sd_prefix / 'user.reg').unlink()
    assert 'incomplete' in raises(ValueError, proton._proton_prefix, sd_game, [root / 'sd'])
    # Hard links are available without Windows symbolic-link privileges.
    os.link(registry, root / 'linked-user.reg')
    raises(ValueError, proton._proton_regular, registry)
    (root / 'linked-user.reg').unlink()
    os.symlink(registry, root / 'symlink-user.reg')
    raises(ValueError, proton._proton_regular, root / 'symlink-user.reg')
    linked_prefix = root / 'linked-library/steamapps/compatdata/4358690/pfx'
    linked_prefix.parent.mkdir(parents=True)
    os.symlink(prefix, linked_prefix, target_is_directory=True)
    raises(ValueError, proton._proton_prefix, game, [root / 'linked-library'])
    linked_game, linked_backup_prefix = fixture(root / 'linked-backup-library')
    os.symlink(prefix.parent / 'gk2mt-backups', linked_backup_prefix.parent / 'gk2mt-backups', target_is_directory=True)
    with patch.object(proton, '_proton_lock', return_value=nullcontext()):
        raises(ValueError, proton.proton_setup, linked_game, [], install=True)
    return game, registry


def check_remote(game, registry):
    settings = {'game_path': str(game), 'workshop_path': str(game.parent.parent / 'workshop/content/4358690')}
    class LocalTransport:
        alive = True
        def matches(self, value):
            return value == settings
        def run(self, command, stream, timeout):
            assert command.startswith('python3 -c ')
            return subprocess.run([sys.executable, '-c',
                'import sys;exec(sys.stdin.buffer.read(int(sys.stdin.buffer.readline())))'],
                stdin=stream, capture_output=True, timeout=timeout, check=True).stdout
    status = deck.configure_proton(settings, LocalTransport())
    assert status['prefix'] == str(registry.parent) and not status['configured']
    registry.write_bytes(proton._proton_registry(registry.read_bytes()))
    assert deck.configure_proton(settings, LocalTransport())['configured']
    registry.write_bytes(b'not a Wine registry')
    error = raises(ValueError, deck.configure_proton, settings, LocalTransport())
    assert 'unsupported format' in error and 'Traceback' not in error


def check():
    check_registry()
    with TemporaryDirectory(prefix='gk2mt-proton-test-') as temporary:
        root = Path(temporary)
        check_processes(root)
        game, registry = check_setup(root)
        check_remote(game, registry)
    print('Proton checks passed: registry preservation, prefix discovery, backups, failure guards, locking contract and remote packaging.')


if __name__ == '__main__':
    check()
