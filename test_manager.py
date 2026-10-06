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
