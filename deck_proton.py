"""Configure only GK2's existing Proton prefix; this source also runs over SSH."""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid


PROTON_KEY = rb'Software\\Wine\\AppDefaults\\GraveyardKeeper2.exe\\DllOverrides'
PROTON_VALUE = b'"winhttp"="native,builtin"'


def _proton_regular(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
        raise ValueError(f'Refusing linked, shared, or unowned Proton file: {path}')
    return info


def _proton_prefix(game, libraries):
    candidates = set()
    for library in {Path(p).resolve() for p in libraries} | {game.parent.parent.parent}:
        prefix = library / 'steamapps/compatdata/4358690/pfx'
        current = library
        for part in ('steamapps', 'compatdata', '4358690', 'pfx'):
            current /= part
            if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
                raise ValueError(f'Automatic setup cannot use a linked Proton folder: {current}. Use the launch-option fallback in the guide.')
        if prefix.is_dir():
            candidates.add(prefix)
    if not candidates:
        raise ValueError('Launch Graveyard Keeper 2 once on the Deck using Proton, close it, then try setup again. Its Proton environment does not exist yet.')
    if len(candidates) != 1:
        raise ValueError('Multiple Graveyard Keeper 2 Proton environments were found. Automatic setup cannot safely choose one; use the launch-option fallback in the guide.')
    prefix = candidates.pop()
    if (not all((prefix / name).exists() for name in ('user.reg', 'system.reg'))
            or not (prefix / 'drive_c/windows').is_dir()):
        raise ValueError('The Deck Proton environment is incomplete. Launch the game once through Steam, close it, then retry.')
    for name in ('user.reg', 'system.reg'):
        _proton_regular(prefix / name)
    return prefix


def _proton_registry(data):
    """Return an idempotent edit, preserving every unrelated registry byte."""
    if (len(data) > 16_000_000 or b'\x00' in data
            or not data.startswith((b'WINE REGISTRY Version 2\n', b'WINE REGISTRY Version 2\r\n'))):
        raise ValueError('The Proton user registry has an unsupported format; no settings were changed.')
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    # Wine writes escaped backslashes in section names. Reject duplicate sections/values.
    headers = list(re.finditer(rb'^[ \t]*\[([^\r\n]*)\][^\r\n]*(?:\r?\n|$)', data, re.M))
    sections = [(header.end(), headers[i + 1].start() if i + 1 < len(headers) else len(data))
                for i, header in enumerate(headers) if header[1].lower() == PROTON_KEY.lower()]
    if len(sections) > 1:
        raise ValueError('Duplicate GK2 Proton override sections; no settings were changed.')
    if not sections:
        return data + (b'' if data.endswith(newline) else newline) + newline + b'[' + PROTON_KEY + b']' + newline + PROTON_VALUE + newline
    start, end = sections[0]
    section = data[start:end]
    values = list(re.finditer(rb'^[ \t]*"winhttp"[ \t]*=[^\r\n]*(?:\r?\n|$)', section, re.M | re.I))
    if len(values) > 1:
        raise ValueError('Duplicate GK2 winhttp overrides; no settings were changed.')
    if values:
        value = values[0]
        parsed = re.fullmatch(rb'[ \t]*"winhttp"[ \t]*=[ \t]*"([^"\\\r\n]*)"[ \t]*(?:\r?\n)?', value[0], re.I)
        if not parsed:
            raise ValueError('The existing winhttp override is not a simple string; no settings were changed.')
        if parsed[1].lower() == b'native,builtin':
            return data
        section = section[:value.start()] + PROTON_VALUE + newline + section[value.end():]
    else:
        section = PROTON_VALUE + newline + section
    return data[:start] + (b'' if data[:start].endswith(newline) else newline) + section + data[end:]


def _proton_idle(proc=Path('/proc')):
    # ponytail: conservatively require all this user's Wine/Proton apps closed;
    # per-prefix filtering can replace this if simultaneous games become necessary.
    if not proc.is_dir():
        raise RuntimeError('Automatic Proton setup requires the Deck Linux process list.')
    for process in proc.glob('[0-9]*'):
        try:
            if process.stat().st_uid != os.getuid():
                continue
            command = (process / 'cmdline').read_bytes().split(b'\x00')
            names = [(process / 'comm').read_text().strip().lower()]
            names += [os.fsdecode(argument).rsplit('/', 1)[-1].lower() for argument in command if argument]
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as error:
            raise RuntimeError('Cannot verify that Proton is stopped. Close all Wine/Proton apps on the Deck and retry.') from error
        if any(name.startswith(('wine', 'proton')) or name.endswith('.exe') for name in names):
            raise RuntimeError('Close all Wine/Proton games and tools on the Deck, wait a few seconds, then retry setup. No processes were stopped by GK2MT.')


@contextmanager
def _proton_lock(prefix):
    import fcntl  # Linux only; uses the same lock as Steam's Proton launcher.
    path = prefix.parent / 'pfx.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        _proton_regular(path)
        if os.fstat(fd).st_ino != path.stat().st_ino:
            raise ValueError('Proton lock changed; retry setup.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Steam is preparing the Proton environment. Close the game and retry in a few seconds.') from error
        _proton_idle()
        yield
    finally:
        os.close(fd)  # Never unlink Proton's lock file.


def proton_setup(game, libraries, install=False):
    game = Path(game).resolve()
    prefix = _proton_prefix(game, libraries)
    registry = prefix / 'user.reg'
    missing = [name for name in ('winhttp.dll', 'doorstop_config.ini', 'BepInEx/core/BepInEx.dll',
                                 'BepInEx/core/BepInEx.Preloader.dll') if not (game / name).is_file()]
    result = {'prefix': str(prefix), 'loader_installed': not missing, 'changed': False}
    if not install:
        with registry.open('rb') as stream:
            data = stream.read(16_000_001)
        return result | {'configured': _proton_registry(data) == data}
    with _proton_lock(prefix):
        info = _proton_regular(registry)
        with registry.open('rb') as stream:
            data = stream.read(16_000_001)
        updated = _proton_registry(data)
        if data == updated:
            return result | {'configured': True}
        backups = prefix.parent / 'gk2mt-backups'
        if backups.is_symlink() or getattr(backups, 'is_junction', lambda: False)():
            raise ValueError('Refusing a linked Proton backup folder.')
        backups.mkdir(mode=0o700, exist_ok=True)
        backup = backups / ('user.reg-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.bak')
        with backup.open('xb') as stream:
            os.chmod(backup, stat.S_IMODE(info.st_mode))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        fd, temporary = tempfile.mkstemp(prefix='.gk2mt-user-', suffix='.reg', dir=prefix)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.chmod(temporary, stat.S_IMODE(info.st_mode))
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            _proton_idle()
            _proton_regular(registry)
            if registry.read_bytes() != data:
                raise ValueError('The Proton registry changed during setup; retry with Steam games closed.')
            os.replace(temporary, registry)
            if registry.read_bytes() != updated:
                raise RuntimeError(f'Proton setup could not be verified. Original registry backup: {backup}')
        finally:
            Path(temporary).unlink(missing_ok=True)
        return result | {'configured': True, 'changed': True, 'backup': str(backup)}
