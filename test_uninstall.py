"""Uninstall recovery/ownership checks; temporary files only. Run python test_uninstall.py."""
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import inventory
import manager
from test_manager import archive, import_mod, put, rejects, setup


def physical(lib, identifier=None):
    rows = inventory.scan(lib.game, lib.data / 'no-workshop')
    return next(p for p in rows if identifier is None or p['id'] == identifier)


def fresh_remove(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'Mod', {'Mod/Main.dll': 'plugin', 'Mod/icon.png': 'asset'})
    before = copy.deepcopy(lib.state)
    disk = lib.state_path.read_bytes()
    preview = lib.uninstall(pkg, dry_run=True)
    assert len(preview['removed']) == 2 and preview['files'] == preview['removed']
    assert lib.state == before and lib.state_path.read_bytes() == disk
    assert not (lib.data / 'uninstall-backups').exists()
    result = lib.uninstall(pkg)
    assert not (game / 'BepInEx/plugins/Mod/Main.dll').exists()
    assert not lib.state['packages'] and not lib.state['deployed'] and not lib.state['baseline']
    backup = Path(result['backup'])
    assert (backup / 'files/BepInEx/plugins/Mod/Main.dll').read_text() == 'plugin'
    assert json.loads((backup / 'recovery.json').read_text())['state'] == before
    assert manager.Manager(lib.data, game).state == lib.state
    lib.deploy()
    assert not (game / 'BepInEx/plugins/Mod/Main.dll').exists()


def adopted_does_not_resurrect(root):
    lib, game = setup(root)
    put(game, 'BepInEx/plugins/Mod/Main.dll', 'manual v1')
    old = put(game, 'BepInEx/plugins/Mod/Old.dll', 'old helper')
    row = physical(lib)
    staged = lib.stage(archive(root, 'Update', {'Mod/Main.dll': 'v2'}))
    pkg = lib.update(staged['token'], row)
    assert not old.exists()
    result = lib.uninstall(pkg)
    assert not old.exists() and not old.with_name('Main.dll').exists()
    assert not result['restored']
    lib.deploy()
    assert not old.exists() and not old.with_name('Main.dll').exists()
    assert any(p.read_text() == 'manual v1' for p in (Path(result['backup']) / 'baseline').iterdir())


def shared_restore_and_rules(root):
    lib, game = setup(root)
    original = put(game, 'BepInEx/plugins/Shared.dll', 'original baseline')
    one = import_mod(lib, root, 'One', {'Shared.dll': 'one'})
    two = import_mod(lib, root, 'Two', {'Shared.dll': 'two'})
    lib.set_rules([{'before': one['id'], 'after': two['id']}])
    result = lib.uninstall(two)
    assert result['restored'] == ['BepInEx/plugins/Shared.dll'] and original.read_text() == 'one'
    assert not lib.state['rules'] and len(lib.state['packages']) == 1
    lib.uninstall(one)
    assert original.read_text() == 'original baseline'


def adopted_baseline_discarded_with_remaining_owner(root):
    lib, game = setup(root)
    target = put(game, 'BepInEx/plugins/Mod/Main.dll', 'manual v1')
    staged = lib.stage(archive(root, 'UpdateA', {'Mod/Main.dll': 'managed A v2'}))
    one = lib.update(staged['token'], physical(lib))
    two = import_mod(lib, root, 'OverlayB', {'Mod/Main.dll': 'managed B overlay'})
    assert target.read_text() == 'managed B overlay'
    result = lib.uninstall(one)
    assert target.read_text() == 'managed B overlay' and not result['files']
    assert lib.state['baseline']['bepinex/plugins/mod/main.dll'] is None
    assert any(p.read_text() == 'manual v1' for p in (Path(result['backup']) / 'baseline').iterdir())
    lib.uninstall(two)
    assert not target.exists(), 'uninstalling overlay resurrected an already uninstalled adopted mod'
    lib.deploy()
    assert not target.exists()


def backup_drift_rejected_before_mutation(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'Mod', {'Mod/Main.dll': 'reviewed DLL', 'Mod/asset.png': 'asset'})
    preview = lib.uninstall(pkg, dry_run=True)
    state, disk = copy.deepcopy(lib.state), lib.state_path.read_bytes()
    target = game / 'BepInEx/plugins/Mod/Main.dll'
    copy2 = manager.shutil.copy2
    def poison_backup(source, destination, *args, **kwargs):
        if Path(source) == target:
            target.write_text('unreviewed external edit')
            try:
                return copy2(source, destination, *args, **kwargs)
            finally:
                target.write_text('reviewed DLL')
        return copy2(source, destination, *args, **kwargs)
    with patch('manager.shutil.copy2', poison_backup):
        rejects(lambda: lib.uninstall(pkg, expected=preview))
    assert target.read_text() == 'reviewed DLL' and (target.parent / 'asset.png').read_text() == 'asset'
    assert state == lib.state and disk == lib.state_path.read_bytes()


def disabled_managed_and_settings(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'Mod', {'BepInEx/plugins/Mod/Main.dll': 'dll',
        'BepInEx/config/Mod.cfg': 'defaults', 'BepInEx/plugins/Mod/local.ini': 'local'})
    lib.set_enabled(pkg['id'], False)
    result = lib.uninstall(pkg)
    assert not result['files'] and not lib.state['packages']
    # Disabled package had no baseline settings; uninstall must not recreate them.
    assert not (game / 'BepInEx/config/Mod.cfg').exists()


def enabled_settings_and_disabled_adopted(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'Mod', {'BepInEx/plugins/Mod/Main.dll': 'dll',
        'BepInEx/config/Mod.cfg': 'defaults', 'BepInEx/plugins/Mod/local.ini': 'local'})
    config = game / 'BepInEx/config/Mod.cfg'
    config.write_text('personal settings')
    result = lib.uninstall(pkg)
    assert config.read_text() == 'personal settings'
    assert (game / 'BepInEx/plugins/Mod/local.ini').read_text() == 'local'
    assert len(result['preserved']) == 2 and not lib.state['packages']
    disabled = put(game, 'BepInEx/plugins/Manual.dll.gk2mt-disabled', 'disabled old')
    staged = lib.stage(archive(root, 'Updated', {'Manual.dll': 'updated'}))
    adopted = lib.update(staged['token'], physical(lib, 'plugins:manual.dll'))
    assert not disabled.exists()
    lib.uninstall(adopted)
    assert not disabled.exists() and not disabled.with_suffix('').exists()


def disabled_nexus_with_workshop_and_virtualized_library(root):
    lib, game = setup(root)
    local = put(game, 'BepInEx/plugins/QuickStash/QuickStash.dll', 'old nexus')
    staged = lib.stage(archive(root, 'QuickStash', {'QuickStash/QuickStash.dll': 'updated nexus'}))
    pkg = lib.update(staged['token'], physical(lib))
    lib.set_enabled(pkg['id'], False)
    steam = put(game, 'BepInEx/plugins/_Workshop/123/BepInEx/plugins/QuickStash/QuickStash.dll', 'steam copy')
    cfg = put(game, 'BepInEx/config/QuickStash.cfg', 'personal settings')
    before = lib.state_path.read_bytes()
    resolve = Path.resolve
    baseline = Path(lib.state['baseline']['bepinex/plugins/quickstash/quickstash.dll'])
    aliases = {lib.state_path: root / 'private-cache/packages.json',
               baseline: root / 'private-cache/originals' / baseline.name}
    # Mimic Windows resolving regular library files into its private AppData cache.
    with patch.object(Path, 'resolve', lambda path, *a, **kw:
                      aliases[path] if path in aliases else resolve(path, *a, **kw)):
        preview = lib.uninstall(pkg, dry_run=True)
        assert not preview['files'] and lib.state_path.read_bytes() == before
        result = lib.uninstall(pkg, expected=preview)
    assert not local.exists() and not lib.state['packages']
    assert steam.read_text() == 'steam copy' and cfg.read_text() == 'personal settings'
    assert Path(result['backup'], 'packages-before.json').read_bytes() == before
    assert any(p.read_text() == 'old nexus' for p in Path(result['backup'], 'baseline').iterdir())


def preserve_unrelated_winner(root):
    lib, game = setup(root)
    child = import_mod(lib, root, 'Child', {'Shared.dll': 'child'})
    sibling = import_mod(lib, root, 'Sibling', {'Shared.dll': 'sibling'})
    middle = import_mod(lib, root, 'Middle', {'Middle.dll': 'middle'})
    parent = import_mod(lib, root, 'Parent', {'Parent.dll': 'parent'})
    lib.set_rules([{'before': parent['id'], 'after': middle['id']},
                   {'before': middle['id'], 'after': child['id']},
                   {'before': parent['id'], 'after': sibling['id']}])
    shared = game / 'BepInEx/plugins/Shared.dll'
    assert shared.read_text() == 'child'
    lib.uninstall(middle)
    assert shared.read_text() == 'child'
    assert lib.conflicts()[0]['winner'] == child['id']
    lib.deploy()
    assert shared.read_text() == 'child'


def reviewed_plan_rejects_replacement_drift(root):
    lib, game = setup(root)
    one = import_mod(lib, root, 'One', {'Shared.dll': 'one'})
    two = import_mod(lib, root, 'Two', {'Shared.dll': 'two'})
    preview = lib.uninstall(two, dry_run=True)
    Path(one['folder']).joinpath('BepInEx/plugins/Shared.dll').write_text('changed replacement')
    rejects(lambda: lib.uninstall(two, expected=preview))
    assert (game / 'BepInEx/plugins/Shared.dll').read_text() == 'two'
    assert len(lib.state['packages']) == 2 and not (lib.data / 'uninstall-backups').exists()


def manual_disabled_assets_settings(root):
    lib, game = setup(root)
    dll = put(game, 'BepInEx/plugins/Mod/Main.dll.gk2mt-disabled', 'disabled')
    image = put(game, 'BepInEx/plugins/Mod/icon.png', 'asset')
    local = put(game, 'BepInEx/plugins/Mod/options.ini', 'personal')
    config = put(game, 'BepInEx/config/Mod.cfg', 'personal config')
    neighbor = put(game, 'BepInEx/plugins/Neighbor.dll', 'neighbor')
    row = physical(lib, 'plugins:mod')
    assert not row['enabled']
    result = lib.uninstall(row)
    assert not dll.exists() and not image.exists()
    assert local.read_text() == 'personal' and config.read_text() == 'personal config'
    assert neighbor.read_text() == 'neighbor' and result['preserved'] == ['BepInEx/plugins/Mod/options.ini']


def vortex_hardlink_and_localization(root):
    lib, game = setup(root)
    source = put(root, 'vortex/Main.dll', 'original')
    target = game / 'BepInEx/plugins/Main.dll'
    target.parent.mkdir(parents=True)
    os.link(source, target)
    asset = put(game, 'BepInEx/plugins/Localization/en/one.json', 'owned')
    neighbor = put(game, 'BepInEx/plugins/Localization/en/two.json', 'neighbor')
    records = {'bepinex/plugins/main.dll': 'one', 'bepinex/plugins/localization/en/one.json': 'one',
               'bepinex/plugins/localization/en/two.json': 'two'}
    with patch('inventory._vortex', return_value=records):
        row = physical(lib, 'plugins:main.dll')
        assert row['source'] == 'Vortex' and len(row['paths']) == 2
        result = lib.uninstall(row)
    assert not target.exists() and not asset.exists() and source.read_text() == 'original'
    assert neighbor.read_text() == 'neighbor' and result['warnings']


def unsafe_rejected(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'Mod', {'Mod/Main.dll': 'managed'})
    target = game / 'BepInEx/plugins/Mod/Main.dll'
    target.write_text('foreign deployment')
    before = copy.deepcopy(lib.state)
    rejects(lambda: lib.uninstall(pkg))
    assert target.read_text() == 'foreign deployment' and lib.state == before
    rejects(lambda: lib.uninstall({'id': 'workshop:1', 'workshop_id': '1', 'paths': []}))
    rejects(lambda: lib.uninstall(pkg | {'nexus_mod_id': 48}))
    foreign = put(game, 'BepInEx/plugins/Foreign.dll', 'foreign')
    row = physical(lib, 'plugins:foreign.dll')
    rejects(lambda: lib.uninstall(row | {'paths': ['BepInEx/plugins/Mod/Main.dll']}))
    rejects(lambda: lib.uninstall(row | {'paths': ['../foreign.dll']}))
    rejects(lambda: lib.uninstall(row | {'paths': ['winhttp.dll']}))
    assert foreign.exists() and not (lib.data / 'uninstall-backups').exists()
    # Simulate a mixed Vortex group without changing live files.
    with patch('inventory.scan', return_value=[row | {'source': 'Vortex', 'vortex_sources': ['one', 'two']}]):
        rejects(lambda: lib.uninstall(row))


def failed_save_rolls_back(root):
    lib, game = setup(root)
    pkg = import_mod(lib, root, 'Mod', {'Mod/Main.dll': 'dll', 'Mod/image.png': 'image'})
    before = copy.deepcopy(lib.state)
    disk = lib.state_path.read_bytes()
    save_json = manager.save_json
    def fail_state(path, value):
        if Path(path) == lib.state_path:
            raise OSError('simulated state failure')
        return save_json(path, value)
    with patch('manager.save_json', fail_state):
        rejects(lambda: lib.uninstall(pkg))
    assert (game / 'BepInEx/plugins/Mod/Main.dll').read_text() == 'dll'
    assert (game / 'BepInEx/plugins/Mod/image.png').read_text() == 'image'
    assert lib.state == before and lib.state_path.read_bytes() == disk
    assert len(list((lib.data / 'uninstall-backups').iterdir())) == 1


def linked_rejected(root):
    lib, game = setup(root)
    target = put(game, 'BepInEx/plugins/Mod.dll', 'mod')
    row = physical(lib)
    target.unlink()
    external = put(root, 'external.dll', 'external')
    try:
        target.symlink_to(external)
    except OSError:
        return
    rejects(lambda: lib.uninstall(row))
    assert external.read_text() == 'external'


def main():
    with patch('manager.ensure_game_stopped'):
        for check in (fresh_remove, adopted_does_not_resurrect, shared_restore_and_rules,
                      adopted_baseline_discarded_with_remaining_owner, backup_drift_rejected_before_mutation,
                      disabled_managed_and_settings, enabled_settings_and_disabled_adopted,
                      disabled_nexus_with_workshop_and_virtualized_library,
                      preserve_unrelated_winner, reviewed_plan_rejects_replacement_drift, manual_disabled_assets_settings,
                      vortex_hardlink_and_localization, unsafe_rejected, failed_save_rolls_back, linked_rejected):
            with TemporaryDirectory() as temporary:
                check(Path(temporary))
                print('PASS', check.__name__)
    print('Uninstall checks passed; no live game files touched.')


if __name__ == '__main__':
    main()
