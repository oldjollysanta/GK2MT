"""Store credential text encrypted for the current Windows account using DPAPI."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, protect, *, label='Nexus API key'):
    if os.name != 'nt':
        raise RuntimeError(f'Remembering a {label} requires Windows.')
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    operation = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    operation.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    try:
        # UI forbidden; omitting LOCAL_MACHINE keeps protection bound to this Windows account.
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            error = ctypes.get_last_error()
            raise RuntimeError(f'Windows could not unlock or protect the {label} (error {error}).')
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(buffer, 0, len(buffer))
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel32.LocalFree(result.data)


def _validate(key, *, label='Nexus API key'):
    try:
        valid = (isinstance(key, str) and bool(key.strip()) and len(key.encode('utf-8')) <= 16384
                 and not any(c in key for c in '\0\r\n'))
    except UnicodeError:
        valid = False
    if not valid:
        raise ValueError(f'The {label} is empty or invalid.')
    return key


def save(path: Path, key: str, *, label='Nexus API key'):
    """Encrypt before writing; a failed replacement leaves the previous key intact."""
    encrypted = _crypt(_validate(key, label=label).encode('utf-8'), True, label=label)
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.gk2mt-key-', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        raise RuntimeError(f'Could not save the encrypted {label}. Check the app data folder permissions.') from None


def load(path: Path, *, label='Nexus API key') -> str:
    """Return an empty string only when no key has been saved."""
    try:
        with Path(path).open('rb') as stream:
            encrypted = stream.read(65537)
    except FileNotFoundError:
        return ''
    except OSError:
        raise RuntimeError(f'Could not read the saved {label}. Check the app data folder permissions.') from None
    try:
        if not encrypted or len(encrypted) > 65536:
            raise ValueError('Invalid encrypted key size.')
        return _validate(_crypt(encrypted, False, label=label).decode('utf-8'), label=label)
    except (RuntimeError, ValueError):
        raise RuntimeError(f'The saved {label} could not be unlocked for this Windows account. Connect it again.') from None


def forget(path: Path, *, label='Nexus API key'):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        raise RuntimeError(f'Could not remove the saved {label}. Check the app data folder permissions.') from None
