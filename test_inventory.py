"""Run with python test_inventory.py; only temporary directories are changed."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from inventory import scan, toggle, loader_info, duplicate_groups, _safe, DISABLED, TRUST


def put(root, relative, content="dll"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def check():
    rows = [
        {"id": "nexus:quickstash", "name": "Quick Stash", "source": "GK2MT", "nexus_mod_id": 123, "enabled": False,
         "paths": ["BepInEx/plugins/QuickStash/QuickStash.dll.gk2mt-disabled", "BepInEx/plugins/QuickStash/Helper.dll"]},
        {"id": "workshop:1", "name": "A friendlier Workshop title", "source": "Steam Workshop", "workshop_id": "1", "enabled": True,
         "paths": [], "workshop_paths": ["1/GK2Collection/QUICKSTASH.dll", "1/GK2Collection/Helper.dll"]},
        {"id": "workshop:2", "name": "Unrelated mod", "source": "Steam Workshop", "workshop_id": "2", "enabled": True,
         "paths": ["BepInEx/plugins/_Workshop/2/Other.dll", "BepInEx/plugins/_Workshop/2/Helper.dll", "BepInEx/plugins/_Workshop/2/0Harmony.dll"]},
        {"id": "manual:other", "name": "Another unrelated mod", "source": "Manual", "enabled": True,
         "paths": ["BepInEx/plugins/Different.dll", "BepInEx/plugins/Helper.dll", "BepInEx/plugins/0Harmony.dll"]},
        {"id": "workshop:foundation", "source": "Steam Workshop", "workshop_id": "3", "nexus_mod_id": 48, "enabled": True,
         "paths": ["BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll"]},
        {"id": "nexus:foundation", "source": "GK2MT", "nexus_mod_id": 48, "enabled": True,
         "paths": ["BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll"]},
    ]
    original = json.dumps(rows, sort_keys=True)
    groups = duplicate_groups(rows)
    assert groups == [{"row_ids": ["nexus:quickstash", "workshop:1"], "reason": "Matching plugin DLL: quickstash.dll",
                       "match": "dll", "enabled_count": 1, "disabled_count": 1}]
    assert json.dumps(rows, sort_keys=True) == original, "Duplicate warnings must not change mod metadata or enabled state."
    # Shared explicit identities beat DLL names and survive different plugin layouts.
    rows[1]["nexus_mod_id"] = "123"
    assert duplicate_groups(rows)[0]["match"] == "nexus"
    rows[1].pop("nexus_mod_id")
    rows[0]["plugin_guid"] = "org.test.quickstash"
    rows[1]["plugin_guids"] = ["ORG.TEST.QUICKSTASH"]
    rows[1]["workshop_paths"] = ["1/ChangedPluginName.dll"]
    assert duplicate_groups(rows)[0]["match"] == "guid"
    # Multiple copies form one warning; enabled duplicates remain distinguishable.
    rows.append(rows[1] | {"id": "workshop:4", "workshop_id": "4", "enabled": False})
    group = duplicate_groups(rows)[0]
    assert group["row_ids"] == ["nexus:quickstash", "workshop:1", "workshop:4"]
    assert group["enabled_count"] == 1 and group["disabled_count"] == 2
    assert duplicate_groups([row for row in rows if row.get("workshop_id")]) == []
    assert duplicate_groups([rows[0], rows[0] | {"id": "manual:second", "source": "Manual"}]) == []
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        data = root / 'library'
        state = put(data, 'packages.json', '{}')
        outside = root / 'redirected/packages.json'
        resolve = Path.resolve
        # Windows can redirect a regular AppData file without a filesystem link.
        with patch.object(Path, 'resolve', lambda path, *a, **kw:
                          outside if path == state else resolve(path, *a, **kw)):
            assert _safe(state, data)
        assert not _safe(root / 'outside.json', data)
        assert not _safe(data / '..' / 'outside.json', data)
        dangling = data / 'missing-link'
        try:
            dangling.symlink_to(root / 'missing', target_is_directory=True)
        except OSError:
            pass
        else:
            assert not dangling.exists() and not _safe(dangling / 'file.dll', data)
    with TemporaryDirectory() as temporary:
        game = Path(temporary) / 'game'
        dll = put(game, 'BepInEx/plugins/BagTweaks/BagTweaks.dll')
        original_time = dll.stat().st_mtime
        manifest = {'targetPath': str(game / 'BepInEx/plugins'), 'files': [{
            'relPath': 'BagTweaks/BagTweaks.dll', 'source': 'Bag Tweaks 197 1.0.0 2026-09-28T01-39Z abc',
            'time': original_time * 1000}]}
        put(game, 'BepInEx/plugins/vortex.deployment.json', json.dumps(manifest))
        put(game, 'BepInEx/LogOutput.log', 'Loading [Bag Tweaks 1.1.0]\n')
        row = scan(game, Path(temporary) / 'workshop')[0]
        assert not row['package_version_stale'] and row['package_version'] == '1.0.0'
        dll.write_text('updated DLL', encoding='utf-8')
        os.utime(dll, (original_time + 100, original_time + 100))
        row = scan(game, Path(temporary) / 'workshop')[0]
        assert row['package_version_stale'] and row['version'] == '1.1.0'
        assert row['source'] == 'Vortex' and row['nexus_mod_id'] == 197
        assert row['package_version'] == '1.0.0' and any('recorded version' in w for w in row['warnings'])
        dll.rename(str(dll) + DISABLED)
        assert scan(game, Path(temporary) / 'workshop')[0]['package_version_stale']
        (game / 'BepInEx/LogOutput.log').unlink()
        row = scan(game, Path(temporary) / 'workshop')[0]
        assert row['version'] == 'Unknown', 'A stale archive must not supply a known installed version.'
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        game, workshop = root / "game", root / "workshop"
        game.mkdir()
        workshop.mkdir()
        put(game, "BepInEx/plugins/Example/Example.dll")
        put(game, "BepInEx/plugins/Example/Helper.dll")
        put(game, "BepInEx/plugins/Example/readme.txt")
        put(game, "BepInEx/config/example.cfg", "## Settings file was created by plugin Example Mod v1.2.3\n## Plugin GUID: org.test.example\n")
        put(game, "BepInEx/plugins/Standalone.dll")
        put(game, "BepInEx/config/standalone.cfg", "## Settings file was created by plugin Standalone v19.0\n")
        put(game, "BepInEx/plugins/Standalone/sprite.png")
        put(game, "BepInEx/plugins/Installer/core/Ignore.dll")
        put(game, "BepInEx/plugins/vortex.deployment.json", json.dumps({"targetPath": str(game / "BepInEx/plugins"), "files": [
            {"relPath": "Standalone.dll", "source": "Standalone 42 2.0 2026-09-28T01-39Z abc"}]}))
        rows = scan(game, workshop)
        assert len(rows) == 2, rows
        example = next(row for row in rows if row["id"] == "plugins:example")
        assert example["name"] == "Example Mod" and example["version"] == "1.2.3"
        standalone = next(row for row in rows if row["id"] == "plugins:standalone.dll")
        assert standalone["source"] == "Vortex" and standalone["nexus_mod_id"] == 42
        assert standalone["package_version"] == "2.0"
        assert standalone["version"] == "19.0"  # Component and Nexus package versions are distinct.
        assert "BepInEx/plugins/Standalone/sprite.png" in standalone["paths"]
        disabled = toggle(game, workshop, example, False)
        assert not disabled["enabled"] and disabled["id"] == example["id"]
        assert (game / ("BepInEx/plugins/Example/Helper.dll" + DISABLED)).is_file()
        assert toggle(game, workshop, disabled, True)["enabled"]
        # A mid-group failure restores the first rename.
        rename = Path.rename
        def fail_second(source, target):
            if source.name == "Helper.dll":
                raise PermissionError("test failure")
            return rename(source, target)
        try:
            with patch.object(Path, "rename", fail_second):
                toggle(game, workshop, example, False)
        except PermissionError:
            pass
        else:
            raise AssertionError("rename failure was swallowed")
        assert (game / "BepInEx/plugins/Example/Example.dll").is_file()
        assert (game / "BepInEx/plugins/Example/Helper.dll").is_file()
        # User-provided paths cannot escape the inventory lookup.
        forged = dict(example, paths=["../outside.dll"])
        assert not toggle(game, workshop, forged, False)["enabled"]
        toggle(game, workshop, example, True)
        # Symlinked files and directory trees are omitted, hardlinked Vortex files remain.
        external = put(root, "external/Outside.dll")
        try:
            (game / "BepInEx/plugins/Linked.dll").symlink_to(external)
            (game / "BepInEx/plugins/LinkedDir").symlink_to(external.parent, target_is_directory=True)
        except OSError:
            pass  # Windows can require Developer Mode for symlink creation.
        os.link(external, game / "BepInEx/plugins/Hardlink.dll")
        rows = scan(game, workshop)
        assert not any("linked" in row["id"] for row in rows)
        assert any(row["id"] == "plugins:hardlink.dll" for row in rows)
        # One loader row, one staged Workshop row, one unsupported downloaded item.
        put(game, "BepInEx/patchers/GK2.WorkshopAutoLoader.dll")
        put(workshop, "100/BepInEx/patchers/GK2.WorkshopAutoLoader.dll")
        put(workshop, "200/BepInEx/plugins/WorkshopMod.dll")
        put(workshop, "300/Installer/BepInEx/core/Ignore.dll")
        put(workshop, "300/GK2Collection/CollectionMod.dll")
        put(game, "BepInEx/plugins/_Workshop/200/WorkshopMod.dll")
        approved_hash = "a" * 64
        put(game, TRUST, "# Preserve me\n200|" + approved_hash + "|yes|WorkshopMod|test\n")
        rows = scan(game, workshop)
        assert len([row for row in rows if row.get("workshop_id") == "100"]) == 1
        mod = next(row for row in rows if row["id"] == "workshop:200")
        assert mod["enabled"] and mod["can_toggle"]
        assert not toggle(game, workshop, mod, False)["enabled"]
        assert approved_hash + "|no|" in (game / TRUST).read_text()
        assert toggle(game, workshop, mod, True)["enabled"]
        assert "# Preserve me" in (game / TRUST).read_text()
        unsupported = next(row for row in rows if row["id"] == "workshop:300")
        assert unsupported["name"] == "CollectionMod" and not unsupported["can_toggle"]
        try:
            toggle(game, workshop, unsupported, True)
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported Workshop toggle was accepted")
        put(game, TRUST, "200|" + approved_hash + "|yes|WorkshopMod|test\n999|" + approved_hash + "|yes|Missing|test\n")
        missing = next(row for row in scan(game, workshop) if row["id"] == "workshop:999")
        assert not missing["enabled"] and not missing["paths"]
        # The replacement loader accepts flat/nested plugins and uses a different trust format.
        assert loader_info(game)["kind"] == "legacy"
        put(game, "BepInEx/patchers/GK2_WorkshopLoader.dll")
        assert loader_info(game)["kind"] == "conflict"
        conflict = next(row for row in scan(game, workshop) if row["id"] == "workshop:200")
        assert not conflict["can_toggle"]
        try:
            toggle(game, workshop, conflict, False)
        except ValueError:
            pass
        else:
            raise AssertionError("conflicting loaders allowed trust mutation")
        legacy_patcher = game / "BepInEx/patchers/GK2.WorkshopAutoLoader.dll"
        legacy_patcher.rename(str(legacy_patcher) + DISABLED)
        assert loader_info(game)["kind"] == "workshop"
        assert loader_info(game)["workshop_id"] == "3807346541"
        put(workshop, "400/Flat.dll", "BepInPlugin\0")
        put(workshop, "500/GK2Collection/StockLimits/StockLimits.dll", "BepInPlugin\0")
        put(workshop, "600/GK2Notepad.dll", "not a BepInEx plugin")
        put(workshop, "700/CopyToGameFolder/Languages/en.txt", "translation")
        put(game, "BepInEx/plugins/_Workshop/400/Flat.dll", "approved staged copy")
        # A legacy yes is never accepted as approval by the new loader.
        rows = scan(game, workshop)
        unapproved = next(row for row in rows if row["id"] == "workshop:200")
        assert unapproved["trust_state"] == "ask"
        put(game, TRUST, "# Keep comment\n400 = " + approved_hash + " # Flat\n500 = BLOCKED # Collection\n600 = not-a-hash\n")
        rows = scan(game, workshop)
        flat = next(row for row in rows if row["id"] == "workshop:400")
        assert flat["enabled"] and flat["can_toggle"] and flat["status"] == "Approved · deployed"
        collection = next(row for row in rows if row["id"] == "workshop:500")
        assert not collection["enabled"] and collection["can_toggle"] and collection["trust_state"] == "no"
        notepad = next(row for row in rows if row["id"] == "workshop:600")
        assert not notepad["enabled"] and not notepad["can_toggle"]
        assert notepad["status"] == "Skipped · no BepInEx plugin" and notepad["trust_state"] == "ask"
        translation = next(row for row in rows if row["id"] == "workshop:700")
        assert not translation["can_toggle"]
        assert not toggle(game, workshop, flat, False)["enabled"]
        assert "400 = BLOCKED" in (game / TRUST).read_text()
        pending = toggle(game, workshop, flat, True)
        assert not pending["enabled"] and pending["trust_state"] == "ask"
        assert pending["status"] == "Approval required on next launch"
        content = (game / TRUST).read_text()
        assert "400 =" not in content and approved_hash not in content
        assert "# Keep comment" in content and "500 = BLOCKED" in content
        toggle(game, workshop, collection, True)
        assert "500 =" not in (game / TRUST).read_text()
        try:
            toggle(game, workshop, notepad, True)
        except ValueError:
            pass
        else:
            raise AssertionError("non-BepInEx item was enabled")
    print("Inventory checks passed (discovery/inventory do not modify game files).")


if __name__ == "__main__":
    check()
