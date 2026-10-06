"""Package safety/rollback checks; run python test_manager.py. Uses temporary files only."""
import copy
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

import manager


def put(root, relative, content="original"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def archive(root, name, files):
    path = root / (name + ".zip")
    with zipfile.ZipFile(path, "w") as target:
        for relative, content in files.items():
            target.writestr(relative, content)
    return path


def rejects(operation):
    try:
        operation()
    except (ValueError, OSError):
        return
    raise AssertionError("unsafe operation was accepted")


def setup(root):
    game = root / "game"
    put(game, "GraveyardKeeper2.exe", "test placeholder")
    return manager.Manager(root / "data", game), game


def import_mod(lib, root, name, files):
    staged = lib.stage(archive(root, name, files))
    return lib.install(staged["token"])


def conflicts_rules_originals(root):
    lib, game = setup(root)
    path = put(game, "BepInEx/plugins/Shared.dll", "original")
    a = import_mod(lib, root, "A", {"BepInEx/plugins/Shared.dll": "A"})
    b = import_mod(lib, root, "B", {"BepInEx/plugins/Shared.dll": "B"})
    assert path.read_text() == "B"
    assert lib.conflicts()[0]["winner"] == b["id"]
    lib.set_rules([{"before": b["id"], "after": a["id"]}])
    assert path.read_text() == "A"
    before = copy.deepcopy(lib.state)
    rejects(lambda: lib.set_rules([{"before": b["id"], "after": a["id"]},
                                   {"before": a["id"], "after": b["id"]}]))
    assert lib.state == before and path.read_text() == "A"
    lib.set_enabled(a["id"], False)
    assert path.read_text() == "B"
    lib.set_enabled(b["id"], False)
    assert path.read_text() == "original"
    lib.set_enabled(a["id"], True)
    assert path.read_text() == "A"


def preserve_directory(root):
    lib, _ = setup(root)
    result = lib.stage(archive(root, "MyMod", {"MyMod/MyMod.dll": "dll", "MyMod/icon.png": "image"}))
    pkg = manager.read_json(lib.data / "staging" / result["token"] / "package.json", {})
    assert "BepInEx/plugins/MyMod/icon.png" in pkg["paths"], pkg["paths"]


def patcher_layout(root):
    lib, _ = setup(root)
    result = lib.stage(archive(root, "Patcher", {"patchers/Start.dll": "patcher", "config/start.cfg": "settings"}))
    pkg = manager.read_json(lib.data / "staging" / result["token"] / "package.json", {})
    assert "BepInEx/patchers/Start.dll" in pkg["paths"], pkg["paths"]
    assert "BepInEx/config/start.cfg" in pkg["paths"], pkg["paths"]


def foundation_uses_setup(root):
    lib, game = setup(root)
    put(game, "BepInEx/config/BepInEx.cfg", "personal settings")
    before = {p.relative_to(game): p.read_bytes() for p in game.rglob("*") if p.is_file()}
    try:
        lib.stage(archive(root, "Foundation", {"BepInEx/Core/bepinex.dll": "core",
            "BepInEx/config/BepInEx.cfg": "factory defaults", "winhttp.dll": "bootstrap"}))
    except ValueError as error:
        assert "Locations & setup" in str(error) and "Use downloaded ZIP" in str(error)
    else:
        raise AssertionError("BepInEx foundation must use setup to preserve existing configs")
    assert before == {p.relative_to(game): p.read_bytes() for p in game.rglob("*") if p.is_file()}
    assert not lib.state["packages"]
    lib.stage(archive(root, "ModWithBootstrap", {"BepInEx/plugins/Mod.dll": "mod", "winhttp.dll": "bootstrap"}))


def hardlinks_and_active_drift(root):
    lib, game = setup(root)
    original = put(root, "vortex/Staged.dll", "Vortex original")
    target = game / "BepInEx/plugins/Hardlink.dll"
    target.parent.mkdir(parents=True)
    os.link(original, target)
    pkg = import_mod(lib, root, "Hardlink", {"BepInEx/plugins/Hardlink.dll": "managed"})
    assert original.read_text() == "Vortex original" and target.read_text() == "managed"
    target.write_text("external change")
    before = copy.deepcopy(lib.state)
    rejects(lambda: lib.set_enabled(pkg["id"], False))
    assert target.read_text() == "external change"
    assert lib.state == before, "failed toggle mutated in-memory package state"


def drift_after_disable(root):
    lib, game = setup(root)
    target = put(game, "BepInEx/plugins/Test.dll", "original")
    pkg = import_mod(lib, root, "Test", {"BepInEx/plugins/Test.dll": "managed"})
    lib.set_enabled(pkg["id"], False)
    assert target.read_text() == "original"
    target.write_text("Vortex redeployed a newer mod")
    before = copy.deepcopy(lib.state)
    rejects(lambda: lib.set_enabled(pkg["id"], True))
    assert target.read_text() == "Vortex redeployed a newer mod"
    assert lib.state == before


def failed_copy_rolls_back(root):
    lib, game = setup(root)
    first = put(game, "BepInEx/plugins/A.dll", "original")
    staged = lib.stage(archive(root, "Failure", {"BepInEx/plugins/A.dll": "new A", "BepInEx/plugins/B.dll": "new B"}))
    before = copy.deepcopy(lib.state)
    copy_atomic = manager.copy_atomic
    failed = False
    def fail_once(source, destination):
        nonlocal failed
        if destination.name == "B.dll" and not failed:
            failed = True
            raise PermissionError("simulated deployment failure")
        return copy_atomic(source, destination)
    with patch("manager.copy_atomic", fail_once):
        rejects(lambda: lib.install(staged["token"]))
    assert first.read_text() == "original"
    assert not (game / "BepInEx/plugins/B.dll").exists()
    assert lib.state == before, "failed install left packages/baseline/deployed records modified"


def duplicate_token(root):
    lib, _ = setup(root)
    staged = lib.stage(archive(root, "Duplicate", {"BepInEx/plugins/Duplicate.dll": "dll"}))
    lib.install(staged["token"])
    try:
        lib.install(staged["token"])
    except ValueError:
        pass
    assert len(lib.state["packages"]) == 1, "installing a token twice duplicated package identity"


def failed_state_save_rolls_back(root):
    lib, game = setup(root)
    target = put(game, "BepInEx/plugins/A.dll", "original")
    staged = lib.stage(archive(root, "StateFailure", {"BepInEx/plugins/A.dll": "new A"}))
    before = copy.deepcopy(lib.state)
    save_json = manager.save_json
    def fail_state(path, value):
        if Path(path) == lib.state_path:
            raise OSError("simulated state-file failure")
        return save_json(path, value)
    with patch("manager.save_json", fail_state):
        rejects(lambda: lib.install(staged["token"]))
    assert target.read_text() == "original" and lib.state == before
    assert not list(lib.data.glob("gk2mt-rollback-*"))


def failed_rollback_keeps_originals(root):
    lib, game = setup(root)
    target = put(game, "BepInEx/plugins/A.dll", "original A")
    other = put(game, "BepInEx/plugins/B.dll", "original B")
    staged = lib.stage(archive(root, "RollbackFailure", {"BepInEx/plugins/A.dll": "new A", "BepInEx/plugins/B.dll": "new B"}))
    copy_atomic = manager.copy_atomic
    def fail_copy(source, destination):
        if (destination.name == "B.dll" and "payload" in Path(source).parts) or (
                destination.name == "A.dll" and Path(source).parent.name.startswith("gk2mt-rollback-")):
            raise PermissionError("simulated locked destination")
        return copy_atomic(source, destination)
    with patch("manager.copy_atomic", fail_copy):
        try:
            lib.install(staged["token"])
        except RuntimeError as error:
            message = str(error)
        else:
            raise AssertionError("failed rollback was not reported")
    folders = list(lib.data.glob("gk2mt-rollback-*"))
    assert len(folders) == 1 and str(folders[0]) in message
    record = json.loads((folders[0] / "recovery.json").read_text())
    assert (folders[0] / record["files"]["BepInEx/plugins/A.dll"]).read_text() == "original A"
    assert target.read_text() == "new A" and other.read_text() == "original B"


def atomic_json_preserves_existing(root):
    target = put(root, "settings.json", '{"old": true}')
    protected = put(root, "unrelated.txt", "do not overwrite")
    os.link(protected, root / "settings.tmp")
    manager.save_json(target, {"new": True})
    assert protected.read_text() == "do not overwrite"
    before = target.read_bytes()
    with patch("manager.os.replace", side_effect=OSError("simulated replace failure")):
        rejects(lambda: manager.save_json(target, {"different": True}))
    assert target.read_bytes() == before
    assert not list(root.glob(".gk2mt-json-*"))


def update_replaces_release(root):
    lib, game = setup(root)
    original = put(game, 'BepInEx/plugins/Mod/Old.dll', 'pre-GK2MT original')
    pkg = import_mod(lib, root, 'Release1', {'BepInEx/plugins/Mod/Main.dll': 'v1',
        'BepInEx/plugins/Mod/Old.dll': 'old helper', 'BepInEx/config/mod.cfg': 'defaults'})
    pkg['package_version'] = '1'
    other = import_mod(lib, root, 'Other', {'Other.dll': 'other'})
    lib.set_rules([{'before': other['id'], 'after': pkg['id']}])
    config = game / 'BepInEx/config/mod.cfg'
    config.write_text('personal settings')
    baseline = copy.deepcopy(lib.state['baseline'])
    for version in (2, 3):
        staged = lib.stage(archive(root, 'Release' + str(version), {'BepInEx/plugins/Mod/Main.dll': 'v' + str(version),
            'BepInEx/config/mod.cfg': 'factory reset'}))
        pkg = lib.update(staged['token'], pkg, {'version': str(version), 'file_id': version, 'nexus_mod_id': 10})
        assert (game / 'BepInEx/plugins/Mod/Main.dll').read_text() == 'v' + str(version)
        assert not original.exists(), 'retired DLL was restored from its baseline'
        assert config.read_text() == 'personal settings'
        assert lib.state['rules'] == [{'before': other['id'], 'after': pkg['id']}]
        assert [p['id'] for p in lib.state['packages']] == [pkg['id'], other['id']]
        assert all(lib.state['baseline'][key] == value for key, value in baseline.items()
                   if key != 'bepinex/config/mod.cfg')
        assert 'bepinex/config/mod.cfg' not in lib.state['baseline']
        assert manager.Manager(lib.data, game).state == lib.state
    assert len(lib.state['packages']) == 2 and pkg['version'] == pkg['package_version'] == '3' and pkg['file_id'] == 3


def update_disabled_package(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'One', {'Mod/Main.dll': 'v1', 'Mod/Old.dll': 'old'})
    lib.set_enabled(pkg['id'], False)
    staged = lib.stage(archive(root, 'Two', {'Mod/Main.dll': 'v2'}))
    pkg = lib.update(staged['token'], pkg, {'version': '2'})
    assert not pkg['enabled'] and not (game / 'BepInEx/plugins/Mod/Main.dll').exists()
    lib.set_enabled(pkg['id'], True)
    assert (game / 'BepInEx/plugins/Mod/Main.dll').read_text() == 'v2'
    assert not (game / 'BepInEx/plugins/Mod/Old.dll').exists()


def update_leaves_unrelated_drift_untouched(root):
    lib, game = setup(root)
    locale = 'BepInEx/plugins/GK2.Framework/Localization/com.kysiin.gk2.zombiecompost/en.json'
    framework = import_mod(lib, root, 'FrameworkOld', {'BepInEx/plugins/GK2.Framework.dll': 'framework v1',
                                                     locale: 'old locale'})
    staged = lib.stage(archive(root, 'FrameworkNew', {'BepInEx/plugins/GK2.Framework.dll': 'framework v2'}))
    framework = lib.update(staged['token'], framework)
    assert not (game / locale).exists()
    selected = import_mod(lib, root, 'CodexOld', {'Codex.dll': 'codex v1'})
    locale_path = put(game, locale, 'recreated locale')
    framework_dll = put(game, 'BepInEx/plugins/GK2.Framework.dll', 'external framework release')
    expected = {key: copy.deepcopy(lib.state['deployed'][key]) for key in
                (locale.casefold(), 'bepinex/plugins/gk2.framework.dll')}
    untouched = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (locale_path, framework_dll)}
    touched = []
    copy_atomic = manager.copy_atomic
    def track_copy(source, destination):
        if destination.is_relative_to(game):
            touched.append(destination)
        return copy_atomic(source, destination)
    staged = lib.stage(archive(root, 'CodexNew', {'Codex.dll': 'codex v2'}))
    with patch('manager.copy_atomic', track_copy):
        selected = lib.update(staged['token'], selected, {'version': '2'})
    assert (game / 'BepInEx/plugins/Codex.dll').read_text() == 'codex v2'
    assert touched == [game / 'BepInEx/plugins/Codex.dll']
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == before for path, before in untouched.items())
    assert all(lib.state['deployed'][key] == record for key, record in expected.items())
    assert manager.Manager(lib.data, game).state == lib.state
    staged = lib.stage(archive(root, 'FrameworkThree', {'BepInEx/plugins/GK2.Framework.dll': 'framework v3'}))
    before, disk = copy.deepcopy(lib.state), lib.state_path.read_bytes()
    rejects(lambda: lib.update(staged['token'], framework))
    rejects(lambda: lib.set_enabled(framework['id'], False))
    assert lib.state == before and lib.state_path.read_bytes() == disk
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == original for path, original in untouched.items())


def update_disabled_retired_drift(root):
    lib, game = setup(root)
    old = put(game, 'BepInEx/plugins/Mod/Old.dll', 'original baseline')
    pkg = import_mod(lib, root, 'Before', {'Mod/Main.dll': 'v1', 'Mod/Old.dll': 'old release'})
    lib.set_enabled(pkg['id'], False)
    assert old.read_text() == 'original baseline'
    old.write_text('newer version deployed by another manager')
    staged = lib.stage(archive(root, 'After', {'Mod/Main.dll': 'v2'}))
    state, disk_state = copy.deepcopy(lib.state), lib.state_path.read_bytes()
    files = {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
    rejects(lambda: lib.update(staged['token'], pkg, {'version': '2'}))
    assert files == {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
    assert state == lib.state and disk_state == lib.state_path.read_bytes()


def update_external_mapping(root):
    lib, game = setup(root)
    vortex = put(root, 'vortex/Main.dll', 'v1')
    target = game / 'BepInEx/plugins/Existing/Main.dll'
    target.parent.mkdir(parents=True)
    os.link(vortex, target)
    old_asset = put(game, 'BepInEx/plugins/Existing/old.png', 'old asset')
    config = put(game, 'BepInEx/config/mod.cfg', 'personal')
    row = {'id': 'plugins:existing', 'name': 'Main', 'version': '1', 'source': 'Vortex', 'enabled': True,
           'paths': [str(p.relative_to(game)).replace('\\', '/') for p in (target, old_asset)], 'vortex_sources': ['one']}
    staged = lib.stage(archive(root, 'ExternalTwo', {'BepInEx/plugins/Release/Main.dll': 'v2',
        'BepInEx/plugins/Release/new.png': 'new asset', 'BepInEx/config/mod.cfg': 'reset'}))
    pkg = lib.update(staged['token'], row, {'version': '2'})
    assert target.read_text() == 'v2' and vortex.read_text() == 'v1' and config.read_text() == 'personal'
    assert not old_asset.exists() and not (game / 'BepInEx/plugins/Release').exists()
    assert (target.parent / 'new.png').read_text() == 'new asset'
    assert Path(lib.state['baseline']['bepinex/plugins/existing/main.dll']).read_text() == 'v1'
    lib.set_enabled(pkg['id'], False)
    assert not target.exists(), 'disabled adopted mod resurrected its old DLL'
    lib.set_enabled(pkg['id'], True)
    assert target.read_text() == 'v2'
    assert len(lib.state['packages']) == 1


def update_external_disabled(root):
    lib, game = setup(root)
    target = put(game, 'BepInEx/plugins/Main.dll.gk2mt-disabled', 'disabled v1')
    row = {'id': 'plugins:main.dll', 'name': 'Main', 'source': 'Manual', 'enabled': False,
           'paths': ['BepInEx/plugins/Main.dll.gk2mt-disabled']}
    staged = lib.stage(archive(root, 'DisabledTwo', {'Main.dll': 'v2'}))
    pkg = lib.update(staged['token'], row)
    assert not pkg['enabled'] and not target.exists() and not target.with_suffix('').exists()
    lib.set_enabled(pkg['id'], True)
    assert target.with_suffix('').read_text() == 'v2' and not target.exists()


def update_rolls_back(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'First', {'Mod/Main.dll': 'v1', 'Mod/Old.dll': 'old'})
    staged = lib.stage(archive(root, 'Second', {'Mod/Main.dll': 'v2', 'Mod/New.dll': 'new'}))
    state = copy.deepcopy(lib.state)
    disk_state = lib.state_path.read_bytes()
    files = {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
    save_json = manager.save_json
    def fail_state(path, value):
        if Path(path) == lib.state_path:
            raise OSError('simulated update state failure')
        return save_json(path, value)
    with patch('manager.save_json', fail_state):
        rejects(lambda: lib.update(staged['token'], pkg, {'version': '2'}))
    assert state == lib.state and disk_state == lib.state_path.read_bytes()
    assert files == {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}


def update_rejects_ambiguous(root):
    lib, game = setup(root)
    put(game, 'BepInEx/plugins/Main.dll', 'v1')
    foreign = put(game, 'BepInEx/plugins/Other.dll', 'unrelated')
    row = {'id': 'plugins:main.dll', 'name': 'Main', 'source': 'Manual', 'enabled': True,
           'paths': ['BepInEx/plugins/Main.dll']}
    staged = lib.stage(archive(root, 'Ambiguous', {'Main.dll': 'v2', 'Other.dll': 'overwrite'}))
    rejects(lambda: lib.update(staged['token'], row))
    row['source'] = 'Steam Workshop'
    rejects(lambda: lib.update(staged['token'], row))
    assert foreign.read_text() == 'unrelated' and not lib.state['packages']


def update_external_rolls_back(root):
    lib, game = setup(root)
    target = put(game, 'BepInEx/plugins/Mod/Main.dll', 'external original')
    old = put(game, 'BepInEx/plugins/Mod/old.txt', 'external old asset')
    row = {'id': 'plugins:mod', 'name': 'Main', 'source': 'Manual', 'enabled': True,
           'paths': ['BepInEx/plugins/Mod/Main.dll', 'BepInEx/plugins/Mod/old.txt']}
    staged = lib.stage(archive(root, 'ExternalFailure', {'Mod/Main.dll': 'v2', 'Mod/new.txt': 'new'}))
    before = copy.deepcopy(lib.state)
    save_json = manager.save_json
    def fail_state(path, value):
        if Path(path) == lib.state_path:
            raise OSError('simulated adoption state failure')
        return save_json(path, value)
    with patch('manager.save_json', fail_state):
        rejects(lambda: lib.update(staged['token'], row))
    assert target.read_text() == 'external original' and old.read_text() == 'external old asset'
    assert not (old.parent / 'new.txt').exists() and not lib.state_path.exists()
    assert lib.state == before


def update_preserves_plugin_settings(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'Old', {'Example/Example.dll': 'old DLL',
        'Example/preferences.ini': 'ini defaults', 'Example/preferences.cfg': 'cfg defaults'})
    ini, cfg = game / 'BepInEx/plugins/Example/preferences.ini', game / 'BepInEx/plugins/Example/preferences.cfg'
    ini.write_text('personal ini settings')
    cfg.write_text('personal cfg settings')
    staged = lib.stage(archive(root, 'New', {'Example/Example.dll': 'new DLL',
        'Example/preferences.ini': 'new ini defaults', 'Example/preferences.cfg': 'new cfg defaults'}))
    preview = lib.update(staged['token'], pkg, dry_run=True)
    assert preview['files'] == 1 and preview['warnings']
    pkg = lib.update(staged['token'], pkg)
    assert ini.read_text() == 'personal ini settings' and cfg.read_text() == 'personal cfg settings'
    assert (ini.parent / 'Example.dll').read_text() == 'new DLL'
    for path in (ini, cfg):
        key = path.relative_to(game).as_posix().casefold()
        assert key not in lib.state['deployed'] and key not in lib.state['baseline']
        assert key not in {p.casefold() for field in ('paths', 'retired_paths', 'adopted_paths') for p in pkg.get(field, [])}
    lib.set_enabled(pkg['id'], False)
    lib.set_enabled(pkg['id'], True)
    assert ini.read_text() == 'personal ini settings' and cfg.read_text() == 'personal cfg settings'


def update_rejects_disabled_neighbor_and_reserved_layout(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'Old', {'Example.dll': 'old DLL'})
    unrelated = put(game, 'BepInEx/plugins/Other.dll.gk2mt-disabled', 'unrelated disabled DLL')
    staged = lib.stage(archive(root, 'New', {'Example.dll': 'new DLL', 'Other.dll': 'unrelated overwrite'}))
    before, disk = copy.deepcopy(lib.state), lib.state_path.read_bytes()
    rejects(lambda: lib.update(staged['token'], pkg, dry_run=True))
    rejects(lambda: lib.update(staged['token'], pkg))
    assert lib.state == before and lib.state_path.read_bytes() == disk
    assert unrelated.read_text() == 'unrelated disabled DLL'
    assert (game / 'BepInEx/plugins/Example.dll').read_text() == 'old DLL'
    for reserved in ('_Workshop', 'core', 'distribution', '_sync', 'installer'):
        # A BepInEx-root archive reaches update validation without bare-layout installer filtering.
        staged = lib.stage(archive(root, 'Reserved' + reserved, {'BepInEx/plugins/Example.dll': 'new DLL',
            f'BepInEx/plugins/{reserved}/Other.dll': 'reserved payload'}))
        rejects(lambda: lib.update(staged['token'], pkg))
        assert lib.state == before and lib.state_path.read_bytes() == disk
        assert not (game / 'BepInEx/plugins' / reserved).exists()


def preserve_shared_settings_metadata_only(root):
    lib, game = setup(root)
    first = import_mod(lib, root, 'One', {'BepInEx/plugins/One.dll': 'one DLL',
        'BepInEx/config/One.cfg': 'defaults', 'BepInEx/plugins/One/local.ini': 'ini defaults'})
    other = import_mod(lib, root, 'Other', {'BepInEx/plugins/Other.dll': 'other DLL',
        'BepInEx/config/Other.cfg': 'other defaults'})
    configs = ('BepInEx/config/One.cfg', 'BepInEx/plugins/One/local.ini', 'BepInEx/config/Other.cfg')
    for relative in configs:
        (game / relative).write_text('personal ' + relative)
    first['adopted_paths'] = ['BepInEx/config/One.cfg']
    first['retired_paths'] = ['BepInEx/plugins/One/local.ini']
    before, disk = {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}, lib.state_path.read_bytes()
    assert {p.casefold() for p in lib.preserve_settings()} == {p.casefold() for p in configs}
    assert disk == lib.state_path.read_bytes(), 'preserve_settings must only prepare in-memory ownership'
    assert before == {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
    for package in lib.state['packages']:
        assert all(not manager._settings_path(p) for field in ('paths', 'adopted_paths', 'retired_paths')
                   for p in package.get(field, []))
    assert not {p.casefold() for p in configs} & (lib.state['deployed'].keys() | lib.state['baseline'].keys())
    lib.set_enabled(first['id'], False)
    assert (game / 'BepInEx/plugins/Other.dll').read_text() == 'other DLL'
    assert next(p for p in lib.state['packages'] if p['id'] == other['id'])['enabled']
    assert all((game / p).read_text() == 'personal ' + p for p in configs)
    lib.set_enabled(first['id'], True)
    assert all((game / p).read_text() == 'personal ' + p for p in configs)


def preserve_settings_keeps_never_installed_defaults(root):
    lib, game = setup(root)
    staged = lib.stage(archive(root, 'InitiallyDisabled', {'BepInEx/plugins/Mod.dll': 'mod DLL',
        'BepInEx/config/Mod.cfg': 'first-run defaults'}))
    staged_path = lib.data / 'staging' / staged['token'] / 'package.json'
    package = manager.read_json(staged_path, {})
    package['enabled'] = False
    manager.save_json(staged_path, package)
    package = lib.install(staged['token'])
    assert not (game / 'BepInEx/config/Mod.cfg').exists()
    assert not lib.preserve_settings() and 'BepInEx/config/Mod.cfg' in package['paths']
    lib.set_enabled(package['id'], True)
    assert (game / 'BepInEx/config/Mod.cfg').read_text() == 'first-run defaults'


def unsafe_paths(root):
    lib, game = setup(root)
    for index, name in enumerate(("../escape.dll", "C:/escape.dll", "/escape.dll", "plugins/NUL.dll", "plugins/odd./Test.dll")):
        rejects(lambda name=name, index=index: lib.stage(archive(root, "Unsafe" + str(index), {name: "payload"})))
    linked = root / "linked.zip"
    with zipfile.ZipFile(linked, "w") as target:
        item = zipfile.ZipInfo("Link.dll")
        item.create_system = 3
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        target.writestr(item, "../outside")
    rejects(lambda: lib.stage(linked))
    outside = put(root, "outside/External.dll")
    (game / "BepInEx").mkdir(exist_ok=True)
    try:
        (game / "BepInEx/plugins").symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        return  # Windows symlink creation may require Developer Mode.
    rejects(lambda: lib.stage(archive(root, "DestinationLink", {"BepInEx/plugins/External.dll": "overwrite"})))
    assert outside.read_text() == "original"


def batch_file_choices(root):
    lib, game = setup(root)
    tokens = [lib.stage(archive(root, name, files))['token'] for name, files in (
        ('First', {'BepInEx/plugins/First.dll': 'first', 'BepInEx/plugins/shared.json': 'first asset'}),
        ('Second', {'BepInEx/plugins/Second.dll': 'second', 'BepInEx/Plugins/Shared.json': 'second asset'}))]
    before = copy.deepcopy(lib.state)
    plan = lib.plan_install(tokens)
    assert lib.state == before and not lib.state_path.exists()
    assert len(plan['conflicts']) == 1
    conflict = plan['conflicts'][0]
    assert conflict['requires_choice'] and not conflict['identical'] and not conflict['owners']
    assert {c['value'] for c in conflict['choices']} == set(tokens)
    rejects(lambda: lib.install_batch(tokens, {}, plan['digest']))
    rejects(lambda: lib.install_batch(tokens, {conflict['path']: 'unknown'}, plan['digest']))
    assert lib.state == before and not (game / 'BepInEx').exists()
    with patch.object(lib, 'deploy', wraps=lib.deploy) as deploy:
        result = lib.install_batch(tokens, {conflict['path']: tokens[1]}, plan['digest'])
    assert deploy.call_count == 1 and result['installed'] == 2 and result['skipped'] == 0
    first, second = lib.state['packages']
    assert first['excluded_paths'] == ['BepInEx/plugins/shared.json']
    assert 'BepInEx/plugins/shared.json' not in first['paths']
    assert (game / 'BepInEx/Plugins/Shared.json').read_text() == 'second asset'
    assert not lib.conflicts()
    repeated = lib.plan_install(tokens)
    assert all(p['status'] == 'already_installed' for p in repeated['packages'])
    assert lib.install_batch(tokens, {}, repeated['digest'])['installed'] == 0


def batch_same_bytes_and_repeat(root):
    lib, game = setup(root)
    old = import_mod(lib, root, 'Old', {'BepInEx/plugins/Old.dll': 'old',
                                      'BepInEx/plugins/shared.json': 'shared asset'})
    files = {'BepInEx/plugins/New.dll': 'new', 'BepInEx/plugins/shared.json': 'shared asset'}
    tokens = [lib.stage(archive(root, name, files))['token'] for name in ('New', 'Repacked')]
    plan = lib.plan_install(tokens)
    assert [p['status'] for p in plan['packages']] == ['ready', 'already_selected']
    assert len(plan['conflicts']) == 1 and plan['conflicts'][0]['identical']
    assert not plan['conflicts'][0]['requires_choice']
    shared = game / 'BepInEx/plugins/shared.json'
    original_time = shared.stat().st_mtime_ns
    result = lib.install_batch(tokens, {}, plan['digest'])
    assert result['installed'] == 1 and result['skipped'] == 1
    assert result['results'][1]['status'] == 'already_selected'
    assert shared.stat().st_mtime_ns == original_time
    assert lib.conflicts()[0]['identical']
    lib.set_enabled(old['id'], False)
    assert shared.read_text() == 'shared asset'
    lib.set_enabled(tokens[0], False)
    repeated = lib.plan_install([tokens[0]])
    assert repeated['packages'][0]['status'] == 'already_installed'
    result = lib.install_batch([tokens[0]], {}, repeated['digest'])
    assert not lib.state['packages'][-1]['enabled'] and result['installed'] == 0


def batch_disabled_retired_and_physical(root):
    lib, game = setup(root)
    old = import_mod(lib, root, 'Old', {'BepInEx/plugins/Old.dll': 'old',
                                      'BepInEx/plugins/shared.json': 'old asset'})
    lib.set_enabled(old['id'], False)
    old['retired_paths'] = ['BepInEx/plugins/retired.json']
    old['adopted_paths'] = ['BepInEx/plugins/adopted.json']
    manager.save_json(lib.state_path, lib.state)
    sibling = put(game, 'BepInEx/plugins/New.dll.gk2mt-disabled', 'external disabled copy')
    token = lib.stage(archive(root, 'New', {'BepInEx/plugins/New.dll': 'new',
        'BepInEx/plugins/shared.json': 'new asset', 'BepInEx/plugins/retired.json': 'retired',
        'BepInEx/plugins/adopted.json': 'adopted'}))['token']
    plan = lib.plan_install([token])
    conflicts = {Path(c['path']).name: c for c in plan['conflicts']}
    assert set(conflicts) == {'New.dll', 'shared.json', 'retired.json', 'adopted.json'}
    owner = next(o for o in conflicts['shared.json']['owners'] if o['kind'] == 'package')
    assert not owner['enabled'] and owner['hash'] and owner['ownership'] == ['paths']
    assert any(o['kind'] == 'disabled_file' for o in conflicts['New.dll']['owners'])
    assert any(o.get('ownership') == ['retired_paths'] for o in conflicts['retired.json']['owners'])
    assert any(o.get('ownership') == ['adopted_paths'] for o in conflicts['adopted.json']['owners'])
    decisions = {c['path']: token if c is conflicts['New.dll'] else 'keep_existing' for c in plan['conflicts']}
    result = lib.install_batch([token], decisions, plan['digest'])
    assert result['installed'] == 1 and sibling.read_text() == 'external disabled copy'
    assert (game / 'BepInEx/plugins/New.dll').read_text() == 'new'
    assert len(lib.state['packages'][-1]['excluded_paths']) == 3 and not old['enabled']


def batch_keeps_existing_whole_mod(root):
    lib, game = setup(root)
    old = import_mod(lib, root, 'Old', {'BepInEx/plugins/Shared.dll': 'old'})
    token = lib.stage(archive(root, 'New', {'BepInEx/plugins/Shared.dll': 'new',
                                         'BepInEx/plugins/new-asset.json': 'new asset'}))['token']
    plan = lib.plan_install([token]); conflict = plan['conflicts'][0]
    state_before, bytes_before = copy.deepcopy(lib.state), lib.state_path.read_bytes()
    result = lib.install_batch([token], {conflict['path']: 'keep_existing'}, plan['digest'])
    assert result['installed'] == 0 and result['skipped'] == 1
    assert result['results'][0]['status'] == 'kept_existing'
    assert lib.state == state_before and lib.state_path.read_bytes() == bytes_before
    assert (game / 'BepInEx/plugins/Shared.dll').read_text() == 'old'
    assert not (game / 'BepInEx/plugins/new-asset.json').exists()
    assert lib.state['packages'][0]['id'] == old['id']


def batch_stale_guards(root):
    for changed in ('disk', 'disabled_sibling', 'payload', 'metadata', 'owner_cache', 'owner_state', 'nexus_metadata'):
        folder = root / changed
        lib, game = setup(folder)
        old = import_mod(lib, folder, 'Old', {'BepInEx/plugins/Old.dll': 'old',
                                             'BepInEx/plugins/shared.json': 'old asset'})
        token = lib.stage(archive(folder, 'New', {'BepInEx/plugins/New.dll': 'new',
                                                'BepInEx/plugins/shared.json': 'new asset'}))['token']
        metadata = {token: {'name': 'Named mod', 'nexus_mod_id': 42}}
        plan = lib.plan_install([token], metadata)
        choices = {c['path']: token for c in plan['conflicts']}
        if changed == 'disk':
            put(game, 'BepInEx/plugins/New.dll', 'appeared after review')
        elif changed == 'disabled_sibling':
            put(game, 'BepInEx/plugins/New.dll.gk2mt-disabled', 'disabled after review')
        elif changed == 'payload':
            put(lib.data / 'staging' / token / 'payload', 'BepInEx/plugins/New.dll', 'changed staged DLL')
        elif changed == 'metadata':
            path = lib.data / 'staging' / token / 'package.json'
            package = manager.read_json(path, {}); package['name'] = 'Changed name'; manager.save_json(path, package)
        elif changed == 'owner_cache':
            put(Path(old['folder']), 'BepInEx/plugins/shared.json', 'changed cached owner')
        elif changed == 'owner_state':
            other = manager.Manager(lib.data, game); other.set_enabled(old['id'], False)
        else:
            metadata[token]['nexus_mod_id'] = 43
        bytes_before = {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
        state_before = lib.state_path.read_bytes()
        rejects(lambda: lib.install_batch([token], choices, plan['digest'], metadata))
        assert {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()} == bytes_before, changed
        assert lib.state_path.read_bytes() == state_before, changed


def batch_rollback_and_rules(root):
    lib, game = setup(root)
    a = import_mod(lib, root, 'Old A', {'BepInEx/plugins/OldA.dll': 'A', 'BepInEx/plugins/old-shared.json': 'A'})
    b = import_mod(lib, root, 'Old B', {'BepInEx/plugins/OldB.dll': 'B', 'BepInEx/plugins/old-shared.json': 'B'})
    lib.set_rules([{'before': b['id'], 'after': a['id']}])
    tokens = [lib.stage(archive(root, name, {f'BepInEx/plugins/{name}.dll': name}))['token'] for name in ('First', 'Second')]
    plan = lib.plan_install(tokens)
    state_before, state_bytes = copy.deepcopy(lib.state), lib.state_path.read_bytes()
    bytes_before = {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
    original = manager.copy_atomic
    def fail_second(source, target):
        if target.name == 'Second.dll':
            raise OSError('simulated second-package write failure')
        return original(source, target)
    with patch('manager.copy_atomic', side_effect=fail_second):
        rejects(lambda: lib.install_batch(tokens, {}, plan['digest']))
    assert lib.state == state_before and lib.state_path.read_bytes() == state_bytes
    assert {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()} == bytes_before
    result = lib.install_batch(tokens, {}, plan['digest'])
    assert result['installed'] == 2 and lib.state['rules'] == state_before['rules']
    assert (game / 'BepInEx/plugins/old-shared.json').read_text() == 'A'


def batch_rejects_skipped_file_winner(root):
    lib, game = setup(root)
    tokens = [lib.stage(archive(root, name, files))['token'] for name, files in (
        ('A', {'BepInEx/plugins/Shared.dll': 'A', 'BepInEx/plugins/shared.dat': 'A'}),
        ('B', {'BepInEx/plugins/Shared.dll': 'B'}),
        ('C', {'BepInEx/plugins/C.dll': 'C', 'BepInEx/plugins/shared.dat': 'C'}))]
    plan = lib.plan_install(tokens)
    decisions = {c['path']: tokens[1] if c['path'].endswith('Shared.dll') else tokens[0] for c in plan['conflicts']}
    with patch.object(lib, 'deploy', wraps=lib.deploy) as deploy:
        try:
            lib.install_batch(tokens, decisions, plan['digest'])
        except ValueError as error:
            assert 'no DLL files left' in str(error) and 'another file copy' in str(error)
        else:
            raise AssertionError('Accepted a file winner from a skipped package')
    assert not deploy.called and not lib.state['packages'] and not lib.state_path.exists()
    assert not (game / 'BepInEx').exists()
    decisions[next(c['path'] for c in plan['conflicts'] if c['path'].endswith('shared.dat'))] = tokens[2]
    result = lib.install_batch(tokens, decisions, plan['digest'])
    assert result['installed'] == 2 and result['results'][0]['status'] == 'kept_existing'
    assert (game / 'BepInEx/plugins/shared.dat').read_text() == 'C'


def main():
    failures = []
    checks = (conflicts_rules_originals, preserve_directory, patcher_layout, foundation_uses_setup, hardlinks_and_active_drift,
              drift_after_disable, failed_copy_rolls_back, duplicate_token, failed_state_save_rolls_back,
              failed_rollback_keeps_originals, atomic_json_preserves_existing, unsafe_paths)
    checks += (update_replaces_release, update_disabled_package, update_leaves_unrelated_drift_untouched,
               update_disabled_retired_drift, update_external_mapping, update_external_disabled,
               update_rolls_back, update_rejects_ambiguous, update_external_rolls_back,
               update_preserves_plugin_settings, update_rejects_disabled_neighbor_and_reserved_layout,
               preserve_shared_settings_metadata_only, preserve_settings_keeps_never_installed_defaults)
    checks += (batch_file_choices, batch_same_bytes_and_repeat, batch_disabled_retired_and_physical,
               batch_keeps_existing_whole_mod, batch_stale_guards, batch_rollback_and_rules,
               batch_rejects_skipped_file_winner)
    with patch("manager.ensure_game_stopped"):
        for check in checks:
            with TemporaryDirectory() as temporary:
                try:
                    check(Path(temporary))
                    print("PASS", check.__name__)
                except Exception as exc:
                    failures.append((check.__name__, str(exc)))
                    print("FAIL", check.__name__, str(exc))
    assert not failures, failures
    print("All package checks passed; no live game files touched.")


if __name__ == "__main__":
    main()
