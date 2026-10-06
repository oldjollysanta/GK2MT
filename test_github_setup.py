"""Pinned setup safety checks: mocked HTTP, inert ZIPs and isolated temporary games."""
import hashlib
import json
import os
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import integrations as api
import manager
from test_workshop_setup import Response, files, put


def zip_bytes(names):
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name in sorted(names):
            archive.writestr(name, ('inert upstream file: ' + name).encode())
    return stream.getvalue()


BASE = zip_bytes(api.BEPINEX_UPSTREAM_FILES)
CONFIG = zip_bytes(api.CONFIGURATION_FILES | {
    'BepInEx/plugins/ConfigurationManager/ConfigurationManager.18.4.1.nupkg'})
ASSETS = ((api.BEPINEX_ASSET[0], hashlib.sha256(BASE).hexdigest()),
          (api.CONFIGURATION_ASSET[0], hashlib.sha256(CONFIG).hexdigest()))
CONTENTS = {ASSETS[0][0]: BASE, ASSETS[1][0]: CONFIG}


def fixture(root):
    game, data = root / 'game', root / 'data'
    for relative in api.GAME_MARKERS:
        put(game, relative, 'inert game')
    return game, data


def failure(action):
    try:
        action()
    except (ValueError, OSError, RuntimeError) as error:
        return str(error)
    raise AssertionError('Unsafe setup unexpectedly succeeded.')


def clean_install_and_repeat(root, opener):
    game, data = fixture(root)
    put(game, 'BepInEx/config/personal.cfg', 'personal settings')
    put(game, 'BepInEx/plugins/Other.dll', 'personal plugin')
    put(game, 'BepInEx/patchers/GK2_WorkshopLoader.dll', 'existing loader')
    before = files(game)
    def downloaded(request, **kwargs):
        assert files(game) == before, 'Both archives must complete before any game writes.'
        return Response(CONTENTS[request.full_url])
    opener.open.side_effect = downloaded
    result = api.setup_installer(game, data, key='invalid-unneeded-key')
    assert result['source'] == 'GitHub' and result['package_version'] == '5.4.23.5'
    assert 'nexus_mod_id' not in result and 'file_id' not in result and opener.open.call_count == 2
    assert result['components'][1]['preserved'] is False
    assert result['components'][2]['name'] == 'Unity Doorstop'
    assert all((game / name).is_file() for name in api.REQUIRED)
    assert all((game / name).read_bytes() == b for name, b in before.items())
    assert not list(game.rglob('*.nupkg'))
    notice = json.loads((game / 'BepInEx/distribution/gk2mt-upstream.json').read_text())
    assert len(notice['components']) == 3 and 'license_url' in notice['components'][2]
    assert (game / 'BepInEx/plugins/ConfigurationManager/LICENSE').is_file()
    snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
    assert api.setup_installer(game, data)['already_installed']
    assert api.setup_installer(game, data, dry_run=True)['files'] == 0
    assert snapshot == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
    assert opener.open.call_count == 2


def installed_and_disabled_skip_before_download(root, opener):
    game, data = fixture(root)
    for relative in api.WORKSHOP_BEPINEX_FILES:
        put(game, relative, 'existing runtime')
    before = files(game)
    assert api.setup_installer(game, data)['already_installed'] and not data.exists()
    assert files(game) == before
    (game / 'winhttp.dll').unlink()
    put(game, 'winhttp.dll.gk2mt-disabled', 'intentionally disabled runtime')
    before = files(game)
    assert 'disabled' in failure(lambda: api.setup_installer(game, data))
    assert files(game) == before and not data.exists()
    opener.open.assert_not_called()


def partial_runtime_and_plugin_preservation(root, opener):
    game, data = fixture(root)
    core = put(game, 'BepInEx/core/BepInEx.dll', 'different existing runtime')
    before = files(game)
    assert 'explicit BepInEx ZIP' in failure(lambda: api.setup_installer(game, data))
    assert files(game) == before
    core.unlink()
    put(game, 'doorstop_config.ini', 'personal doorstop configuration')
    disabled = put(game, 'BepInEx/plugins/Custom/ConfigurationManager.dll.gk2mt-disabled', 'older disabled plugin')
    result = api.setup_installer(game, data)
    assert result['components'][1]['preserved']
    assert disabled.read_text() == 'older disabled plugin'
    assert not (game / 'BepInEx/plugins/ConfigurationManager').exists()
    assert (game / 'doorstop_config.ini').read_text() == 'personal doorstop configuration'
    # Verified assets are reused for the second attempt, despite the earlier blocked deployment.
    assert opener.open.call_count == 2


def network_hash_and_size_fail_without_game_writes(root, opener):
    game, data = fixture(root)
    before = files(game)
    def second_failed(request, **kwargs):
        if request.full_url == ASSETS[1][0]:
            raise OSError('simulated download interruption')
        return Response(BASE)
    opener.open.side_effect = second_failed
    failure(lambda: api.setup_installer(game, data))
    assert files(game) == before
    opener.open.side_effect = lambda *a, **kw: Response(CONFIG + b'wrong hash')
    assert 'SHA-256' in failure(lambda: api.setup_installer(game, data))
    assert files(game) == before and not list(data.rglob('*.part'))
    output = data / 'bounded.zip'
    opener.open.side_effect = lambda *a, **kw: Response(BASE)
    assert 'size limit' in failure(lambda: api._download(ASSETS[0][0], output, api.GITHUB_DOMAINS, maximum=10))
    assert not output.exists()


def cache_corruption_and_download_race(root, opener):
    game, data = fixture(root)
    cache = api._github_asset(data, ASSETS[0])
    assert opener.open.call_count == 1
    assert api._github_asset(data, ASSETS[0]) == cache and opener.open.call_count == 1
    cache.write_bytes(b'corrupt cache')
    assert api._github_asset(data, ASSETS[0]).read_bytes() == BASE and opener.open.call_count == 2
    def raced(request, **kwargs):
        for relative in api.WORKSHOP_BEPINEX_FILES:
            put(game, relative, 'concurrent installed runtime')
        return Response(CONTENTS[request.full_url])
    opener.open.side_effect = raced
    result = api.setup_installer(game, data)
    assert result['already_installed'] and result['files'] == 0
    assert all((game / p).read_text() == 'concurrent installed runtime' for p in api.WORKSHOP_BEPINEX_FILES)
    assert not (game / 'BepInEx/plugins').exists()


def rollback_and_external_same_bytes_identity(root, opener):
    game, data = fixture(root)
    before = files(game)
    real_copy = manager.copy_atomic
    def interrupted(src, dst):
        if 'payload' in Path(src).parts and Path(src).name == 'BepInEx.Preloader.dll' and Path(dst).name.startswith('.gk2mt-foundation-'):
            raise OSError('simulated interrupted commit')
        real_copy(src, dst)
    with patch.object(manager, 'copy_atomic', side_effect=interrupted):
        assert 'Restore failures: []' in failure(lambda: api.setup_installer(game, data))
    assert files(game) == before and not list(game.rglob('.gk2mt-foundation-*'))
    kept = game / 'BepInEx/core/0Harmony.dll'
    def externally_replaced(src, dst):
        if 'payload' in Path(src).parts and Path(src).name == '0Harmony20.dll' and Path(dst).name.startswith('.gk2mt-foundation-'):
            replacement = root / 'external.dll'
            replacement.write_bytes(kept.read_bytes())
            os.replace(replacement, kept)
            raise OSError('external replacement then commit failure')
        real_copy(src, dst)
    with patch.object(manager, 'copy_atomic', side_effect=externally_replaced):
        assert 'changed by another program; preserved' in failure(lambda: api.setup_installer(game, data))
    assert kept.read_bytes() == ('inert upstream file: BepInEx/core/0Harmony.dll').encode()
    assert {p: b for p, b in files(game).items() if p != 'BepInEx/core/0Harmony.dll'} == before


def late_disabled_copy_and_destination_are_preserved(root, opener):
    game, data = fixture(root)
    before = files(game)
    real_copy = manager.copy_atomic
    disabled = game / 'BepInEx/plugins/OtherFolder/ConfigurationManager.dll.gk2mt-disabled'
    def disabled_race(src, dst):
        real_copy(src, dst)
        if Path(src).name == 'ConfigurationManager.dll' and Path(dst).name.startswith('.gk2mt-foundation-'):
            put(game, disabled.relative_to(game), 'concurrent disabled plugin')
    with patch.object(manager, 'copy_atomic', side_effect=disabled_race):
        assert 'preserved' in failure(lambda: api.setup_installer(game, data))
    assert disabled.read_text() == 'concurrent disabled plugin'
    assert {p: b for p, b in files(game).items() if p != disabled.relative_to(game).as_posix()} == before
    disabled.unlink()
    target = game / 'BepInEx/core/0Harmony.dll'
    def target_race(src, dst):
        real_copy(src, dst)
        if Path(src).name == '0Harmony.dll' and Path(dst).name.startswith('.gk2mt-foundation-'):
            put(game, target.relative_to(game), 'concurrent target')
    with patch.object(manager, 'copy_atomic', side_effect=target_race):
        failure(lambda: api.setup_installer(game, data))
    assert target.read_text() == 'concurrent target'


def main():
    checks = (clean_install_and_repeat, installed_and_disabled_skip_before_download,
              partial_runtime_and_plugin_preservation, network_hash_and_size_fail_without_game_writes,
              cache_corruption_and_download_race, rollback_and_external_same_bytes_identity,
              late_disabled_copy_and_destination_are_preserved)
    with patch.object(api, 'BEPINEX_ASSET', ASSETS[0]), patch.object(api, 'CONFIGURATION_ASSET', ASSETS[1]), \
            patch.object(manager, 'ensure_game_stopped'), \
            patch.object(api, '_json', side_effect=AssertionError('Default setup must not call Nexus')), \
            patch.object(api, '_account', side_effect=AssertionError('Default setup must not validate accounts')):
        for check in checks:
            with TemporaryDirectory() as temporary, patch.object(api.urllib.request, 'build_opener') as build:
                opener = build.return_value
                opener.open.side_effect = lambda request, **kw: Response(CONTENTS[request.full_url])
                check(Path(temporary), opener)
                print('PASS', check.__name__)
    print('GitHub setup checks passed; only test-owned temporary files and mocked HTTP were used.')


if __name__ == '__main__':
    main()
