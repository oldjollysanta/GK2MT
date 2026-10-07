"""Read and prepare portable GK2 preferences; never writes a registry or save.

GK2's SaveSystem stores GameSettings JSON in PlayerPrefs' ``settings`` key.
Only audio and language preferences transfer; Deck display, controls, graphics,
progress flags, Steam identifiers, and Proton overrides remain device-specific.
"""

import hashlib
import json
from pathlib import Path
import re

# This source is also appended after these helpers in the SSH script.
if '_proton_prefix' not in globals():
    from deck_proton import _proton_prefix, _proton_regular
if 'discover' not in globals():
    from deck_discovery import discover


PREFERENCES_KEY = r'Software\Lazy Bear Games\Graveyard Keeper 2'
PREFERENCES_VALUE = 'settings_h1277500064'
PORTABLE_FIELDS = ('masterVolume', 'musicVolume', 'sfxVolume', 'speechVolume',
                   'language', 'voiceOverMode')
_GAME_REGISTRY_KEY = PREFERENCES_KEY.replace('\\', '\\\\').encode('ascii')
_GAME_MAX_JSON = 1_000_000
_GAME_MAX_REGISTRY = 16_000_000


def validate_preferences(preferences):
    """Validate the exact small payload accepted across the SSH boundary."""
    if not isinstance(preferences, dict) or set(preferences) != set(PORTABLE_FIELDS):
        raise ValueError('Game preferences must contain only audio, language, and voice settings.')
    result = {}
    for name in PORTABLE_FIELDS[:4]:
        value = preferences[name]
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise ValueError('Game audio levels must be numbers between 0 and 1.')
        result[name] = float(value)
    language = preferences['language']
    if (not isinstance(language, str) or not language.strip() or len(language) > 128
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in language)):
        raise ValueError('Game language is missing or invalid.')
    result['language'] = language
    voice = preferences['voiceOverMode']
    if type(voice) is not int or voice not in (0, 1):
        raise ValueError('Game voice setting is invalid.')
    result['voiceOverMode'] = voice
    return result


def _game_json(data, device):
    if not isinstance(data, bytes) or not data or len(data) > _GAME_MAX_JSON:
        raise ValueError(f'{device} game preferences have an unsupported format.')
    terminated = data.endswith(b'\0')
    if terminated:
        data = data[:-1]
    def unique_pairs(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('Duplicate game preference fields.')
            result[name] = value
        return result
    def invalid_number(value):
        raise ValueError('Invalid game preference number.')
    try:
        settings = json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs,
                              parse_constant=invalid_number)
        if not isinstance(settings, dict):
            raise ValueError('Game preferences are not an object.')
        preferences = validate_preferences({name: settings[name] for name in PORTABLE_FIELDS})
    except (UnicodeError, ValueError, KeyError, OverflowError, RecursionError) as error:
        raise ValueError(f'{device} game preferences have an unsupported format. '
                         'Launch the game once, save its settings, then close it and compare again.') from error
    return settings, preferences, terminated


def local_preferences():
    """Read this Windows account's six portable fields, without any registry write."""
    try:
        import winreg
    except ImportError as error:
        raise ValueError('Reading PC game preferences requires Windows.') from error
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PREFERENCES_KEY, 0, winreg.KEY_READ) as key:
            data, kind = winreg.QueryValueEx(key, PREFERENCES_VALUE)
    except FileNotFoundError as error:
        raise ValueError('PC game preferences were not found. Launch the game once, '
                         'save its settings, then close it and compare again.') from error
    if kind != winreg.REG_BINARY:
        raise ValueError('PC game preferences have an unsupported registry type.')
    return _game_json(data, 'PC')[1]


def _game_registry_value(data):
    """Locate only GK2's binary JSON value, including Wine hex continuations."""
    if (len(data) > _GAME_MAX_REGISTRY or b'\0' in data
            or not data.startswith((b'WINE REGISTRY Version 2\n', b'WINE REGISTRY Version 2\r\n'))):
        raise ValueError('The Deck Proton registry has an unsupported format.')
    headers = list(re.finditer(rb'^[ \t]*\[([^\r\n]*)\][^\r\n]*(?:\r?\n|$)', data, re.M))
    sections = [(header.end(), headers[index + 1].start() if index + 1 < len(headers) else len(data))
                for index, header in enumerate(headers) if header[1].lower() == _GAME_REGISTRY_KEY.lower()]
    if len(sections) > 1:
        raise ValueError('Duplicate Deck game preference sections; no settings were changed.')
    missing = ('Deck game preferences were not found. Launch the game once on the Deck, '
               'save its settings, then close it and compare again.')
    if not sections:
        raise ValueError(missing)
    section_start, section_end = sections[0]
    section = data[section_start:section_end]
    matches = list(re.finditer(rb'^[ \t]*"' + PREFERENCES_VALUE.encode('ascii')
                              + rb'"[ \t]*=[ \t]*[^\r\n]*(?:\r?\n|$)', section, re.M | re.I))
    if not matches:
        raise ValueError(missing)
    if len(matches) != 1:
        raise ValueError('Duplicate Deck game preference values; no settings were changed.')
    value = matches[0]
    start, end = value.start(), value.end()
    line = value[0].rstrip(b'\r\n')
    encoded = line.split(b'=', 1)[1].strip()
    while encoded.endswith(b'\\'):
        encoded = encoded[:-1]
        next_end = section.find(b'\n', end)
        next_end = len(section) if next_end == -1 else next_end + 1
        if end >= len(section):
            raise ValueError('Incomplete Deck game preference hex value.')
        encoded += section[end:next_end].strip()
        end = next_end
    match = re.fullmatch(rb'hex(?:\(3\))?:[ \t]*([0-9a-fA-F]{2}(?:[ \t]*,[ \t]*[0-9a-fA-F]{2})*)', encoded, re.I)
    if not match:
        raise ValueError('The Deck game preference value is not valid binary JSON.')
    payload = bytes.fromhex(match[1].replace(b',', b' ').decode('ascii'))
    if len(payload) > _GAME_MAX_JSON:
        raise ValueError('Deck game preferences are too large.')
    return payload, (section_start + start, section_start + end)


def _game_snapshot(game, libraries):
    game = Path(game).resolve()
    prefix = _proton_prefix(game, discover()['libraries'] if libraries is None else libraries)
    registry = prefix / 'user.reg'
    _proton_regular(registry)
    with registry.open('rb') as stream:
        original = stream.read(_GAME_MAX_REGISTRY + 1)
    payload, span = _game_registry_value(original)
    settings, preferences, terminated = _game_json(payload, 'Deck')
    return registry, original, settings, preferences, terminated, span


def remote_preferences(game, libraries=None):
    """Return portable fields plus a fingerprint for stale-preview protection."""
    registry, original, _, preferences, _, _ = _game_snapshot(game, libraries)
    return {'preferences': preferences, 'registry_hash': hashlib.sha256(original).hexdigest(),
            'path': str(registry)}


def prepare_update(game, preferences, libraries=None):
    """Prepare an exact registry edit; caller owns locking, backup, and transaction."""
    preferences = validate_preferences(preferences)
    registry, original, settings, current, terminated, span = _game_snapshot(game, libraries)
    if preferences == current:
        return registry, original, original
    settings.update(preferences)
    payload = json.dumps(settings, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if terminated:
        payload += b'\0'
    if len(payload) > _GAME_MAX_JSON:
        raise ValueError('Updated Deck game preferences are too large.')
    newline = b'\r\n' if b'\r\n' in original else b'\n'
    prefix = b'"' + PREFERENCES_VALUE.encode('ascii') + b'"=hex:'
    tokens = [f'{byte:02x}'.encode('ascii') for byte in payload]
    count = max(1, (76 - len(prefix) - 2) // 3)
    lines = [prefix + b','.join(tokens[:count])]
    for index in range(count, len(tokens), 24):
        lines.append(b'  ' + b','.join(tokens[index:index + 24]))
    replacement = (b',\\' + newline).join(lines)
    if original[span[0]:span[1]].endswith(b'\n'):
        replacement += newline
    updated = original[:span[0]] + replacement + original[span[1]:]
    # Validate the generated value before exposing it to the writing transaction.
    if _game_json(_game_registry_value(updated)[0], 'Deck')[1] != preferences:
        raise ValueError('Updated Deck game preferences could not be verified.')
    return registry, original, updated
