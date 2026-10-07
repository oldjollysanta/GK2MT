"""Isolated game-preference checks; no live registry, game, saves, or Deck writes."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import deck_game_settings as prefs


HEADER = b'WINE REGISTRY Version 2\n;; Private fixture\n#arch=win64\n'
PORTABLE = {'masterVolume': .8, 'musicVolume': .2, 'sfxVolume': .7, 'speechVolume': .9,
            'language': 'English', 'voiceOverMode': 1}
DECK_SETTINGS = dict(PORTABLE, resolutionConfig={'width': 1280, 'height': 800},
                     screenMode=1, graphicsTier=0, targetFrameRate=30,
                     gpuGraphicsDefaultApplied=True, steamDeckGraphicsDefaultApplied=True,
                     cursorMode=0, keyboardKeybindings=[{'gameKeyValue': 2, 'keyCode': 31}],
                     shownDlcStartupPopUps=[5], futureField={'keep': 'unchanged'})


def raises(call, *args):
    try:
        call(*args)
    except ValueError as error:
        return str(error)
    raise AssertionError(f'{call.__name__} should reject this input')


def binary(settings=DECK_SETTINGS, terminated=True):
    return json.dumps(settings, ensure_ascii=False).encode('utf-8') + (b'\0' if terminated else b'')


def registry(payload=None, newline=b'\n', variant=b'hex:'):
    payload = binary() if payload is None else payload
    parts = [b','.join(f'{byte:02x}'.encode() for byte in payload[index:index + 12])
             for index in range(0, len(payload), 12)]
    encoded = (b',\\\n  ').join(parts)
    return (HEADER + b'\n[Software\\\\Before] 123\n"keep"="secret fixture string"\n\n['
            + prefs._GAME_REGISTRY_KEY + b'] 789\n#time=123abc\n'
            + b'"unity_connect.session_id_h4145606137"=hex:01,02\n'
            + b'  "settings_h1277500064" = ' + variant + encoded + b'\n'
            + b'"Screenmanager Resolution Width_h182942802"=dword:00000500\n\n'
            + b'[Software\\\\Wine\\\\AppDefaults\\\\GraveyardKeeper2.exe\\\\DllOverrides]\n'
            + b'"winhttp"="native,builtin"\n').replace(b'\n', newline)


def fixture(root, data):
    game = root / 'steamapps/common/Graveyard Keeper 2'
    game.mkdir(parents=True)
    (game / 'GraveyardKeeper2.exe').touch()
    prefix = root / 'steamapps/compatdata/4358690/pfx'
    (prefix / 'drive_c/windows').mkdir(parents=True)
    (prefix / 'user.reg').write_bytes(data)
    (prefix / 'system.reg').write_bytes(HEADER)
    return game, prefix / 'user.reg'


def check_local():
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    calls = []
    def open_key(hive, key, reserved, access):
        calls.append((hive, key, reserved, access))
        return FakeKey()
    def query(key, name):
        assert name == prefs.PREFERENCES_VALUE
        return binary(), 3
    fake = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_READ=2, REG_BINARY=3,
                           OpenKey=open_key, QueryValueEx=query)
    with patch.dict(sys.modules, {'winreg': fake}):
        assert prefs.local_preferences() == PORTABLE
        assert calls == [(1, prefs.PREFERENCES_KEY, 0, 2)]
        fake.QueryValueEx = lambda *_: (binary(), 1)
        assert 'registry type' in raises(prefs.local_preferences)
        def missing(*_): raise FileNotFoundError()
        fake.OpenKey = missing
        assert 'Launch the game once' in raises(prefs.local_preferences)


def check_validation():
    assert prefs.validate_preferences(PORTABLE) == PORTABLE
    for bad in ({}, dict(PORTABLE, saveSlot=1), dict(PORTABLE, masterVolume=True),
                dict(PORTABLE, sfxVolume=-.1), dict(PORTABLE, musicVolume=2),
                dict(PORTABLE, musicVolume=10 ** 1000),
                dict(PORTABLE, speechVolume=float('nan')), dict(PORTABLE, speechVolume=float('inf')),
                dict(PORTABLE, language=''), dict(PORTABLE, language='x' * 129),
                dict(PORTABLE, language='English\n'), dict(PORTABLE, voiceOverMode=True),
                dict(PORTABLE, language='\ud800'),
                dict(PORTABLE, voiceOverMode=2)):
        raises(prefs.validate_preferences, bad)
    for bad in (b'{}', b'[]', b'not JSON', binary() + b'\0', b'\xff',
                b'{"masterVolume":0.5,"masterVolume":0.8}',
                binary().replace(b'0.8', b'NaN', 1), b'x' * 1_000_001):
        raises(prefs._game_json, bad, 'Fixture')
    raises(prefs._game_json, b'[' * 1200 + b'0' + b']' * 1200, 'Fixture')


def check_registry():
    for newline in (b'\n', b'\r\n'):
        for variant in (b'hex:', b'hex(3):'):
            original = registry(newline=newline, variant=variant)
            payload, span = prefs._game_registry_value(original)
            assert payload == binary() and original[span[0]:span[1]].startswith(b'  "settings_')
    original = registry()
    for bad in (b'REGEDIT4\n', HEADER + b'\0', HEADER + b'x' * 16_000_001,
                HEADER, original.replace(prefs._GAME_REGISTRY_KEY, b'Other\\\\Game'),
                original.replace(b'"settings_h1277500064"', b'"unexpected"'),
                original + b'\n[' + prefs._GAME_REGISTRY_KEY + b']\n',
                original.replace(b'#time=123abc\n', b'#time=123abc\n"SETTINGS_h1277500064"=hex:7b,7d\n'),
                original.replace(b'= hex:', b'= hex(1):', 1),
                original.replace(b'= hex:', b'= hex:zz,', 1),
                HEADER + b'\n[' + prefs._GAME_REGISTRY_KEY + b']\n"settings_h1277500064"=hex:7b,\\'):
        raises(prefs._game_registry_value, bad)


def check_prepare(root):
    for index, newline in enumerate((b'\n', b'\r\n')):
        original = registry(newline=newline)
        game, target = fixture(root / str(index), original)
        snapshot = prefs.remote_preferences(game, [])
        assert snapshot == {'preferences': PORTABLE,
                            'registry_hash': hashlib.sha256(original).hexdigest(), 'path': str(target)}
        assert prefs.prepare_update(game, PORTABLE, []) == (target, original, original)
        changed = dict(PORTABLE, language='Español', musicVolume=.55, voiceOverMode=0)
        path, before, after = prefs.prepare_update(game, changed, [])
        assert path == target and before == original and target.read_bytes() == original
        old_payload, old_span = prefs._game_registry_value(original)
        new_payload, new_span = prefs._game_registry_value(after)
        assert after[:new_span[0]] == original[:old_span[0]]
        assert after[new_span[1]:] == original[old_span[1]:]
        assert new_payload.endswith(b'\0')
        parsed = json.loads(new_payload[:-1])
        assert {key: parsed[key] for key in prefs.PORTABLE_FIELDS} == changed
        assert {key: value for key, value in parsed.items() if key not in prefs.PORTABLE_FIELDS} == {
            key: value for key, value in DECK_SETTINGS.items() if key not in prefs.PORTABLE_FIELDS}
        assert b'"winhttp"="native,builtin"' in after
        assert all(len(line) <= 78 for line in after[new_span[0]:new_span[1]].splitlines())
        # The caller owns committing/rolling back. Simulate only this private file.
        target.write_bytes(after)
        assert prefs.prepare_update(game, changed, []) == (target, after, after)
        assert prefs.remote_preferences(game, [])['preferences'] == changed
        target.write_bytes(HEADER)
        assert 'Launch the game once on the Deck' in raises(prefs.remote_preferences, game, [])
        target.write_bytes(registry(binary(terminated=False)))
        _, _, unterminated = prefs.prepare_update(game, changed, [])
        assert not prefs._game_registry_value(unterminated)[0].endswith(b'\0')
        target.write_bytes(original.replace(b'  "settings_h1277500064"', b'"settings_h1277500064"').rstrip(b'\n'))
        assert not prefs.prepare_update(game, changed, [])[2].endswith(b'\n')
        assert not (target.parent.parent / 'gk2mt-backups').exists()


def check_injected_helpers(root):
    game, target = fixture(root, registry())
    sources = [Path(__file__).with_name(name).read_text(encoding='utf-8')
               for name in ('deck_discovery.py', 'deck_proton.py', 'deck_game_settings.py')]
    script = '\n'.join(sources) + '\nprint(json.dumps(remote_preferences(' + repr(str(game)) + ', [])))\n'
    # Isolated Python cannot import project modules: this exercises the actual
    # prepended shared helpers used on a Deck without installing GK2MT there.
    result = subprocess.run([sys.executable, '-I', '-c', script], cwd=root,
                            capture_output=True, text=True, timeout=30, check=True)
    assert not result.stderr
    assert json.loads(result.stdout) == {'preferences': PORTABLE,
                                         'registry_hash': hashlib.sha256(target.read_bytes()).hexdigest(),
                                         'path': str(target)}


if __name__ == '__main__':
    check_local()
    check_validation()
    check_registry()
    with TemporaryDirectory(prefix='gk2mt-game-prefs-') as temporary:
        check_prepare(Path(temporary))
        check_injected_helpers(Path(temporary) / 'isolated-helper')
    print('Game preference fixtures passed; no live registry, saves, or Deck was changed.')
