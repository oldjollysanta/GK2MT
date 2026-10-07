"""Scoped Deck sync regressions. Temporary PC/Deck trees; no SSH or live settings."""

import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from unittest.mock import patch

import deck
import deck_game_settings as preferences
from test_deck import raises, steam_manifest, write
from test_deck_game_settings import DECK_SETTINGS, HEADER, PORTABLE, registry


def only(category):
    return {key: key == category for key in deck.DEFAULT_OPTIONS}


def fixture(root):
    local = root / "pc/steamapps/common/Graveyard Keeper 2"
    remote = root / "deck/steamapps/common/Graveyard Keeper 2"
    workshop, remote_workshop = root / "pc/4358690", root / "deck/4358690"
    for game in (local, remote):
        write(game, "GraveyardKeeper2.exe", b"game binary")
        steam_manifest(game)
    paths = {
        "mods": ["BepInEx/plugins/Main.dll", "GraveyardKeeper2_Data/Managed/GK2Notepad.dll"],
        "configs": ["BepInEx/config/settings.cfg", "BepInEx/config/custom/nested.json"],
        "approvals": [deck.WORKSHOP_TRUST],
        "loader": ["BepInEx/core/BepInEx.dll", "BepInEx/patchers/GK2_WorkshopLoader.dll", "winhttp.dll"],
    }
    for category, names in paths.items():
        for name in names:
            write(local, name, (category + " PC").encode())
            write(remote, name, (category + " Deck").encode())
    write(local, "BepInEx/plugins/New.dll", b"new mod")
    write(remote, "BepInEx/plugins/Extra.dll", b"Deck only")
    write(remote, "BepInEx/config/custom/extra.cfg", b"Deck only config")
    write(remote, "doorstop_config.ini", b"Deck only loader")
    write(workshop, "123/Main.dll", b"PC Workshop")
    write(remote_workshop, "123/Main.dll", b"Deck Workshop")
    write(remote_workshop, "456/Extra.dll", b"Deck only Workshop")
    write(remote, "UnityPlayer.dll", b"game stays untouched")
    settings = {"host": "steamdeck.local", "game_path": "/home/deck/steamapps/common/Graveyard Keeper 2",
                "workshop_path": "/home/deck/steamapps/workshop/content/4358690"}

    def request(_settings, message, archive=None, connection=None):
        if message["action"] == "game-build":
            return deck.game_build(remote, "Deck")
        if message["action"] == "preview":
            deck._require_build(remote, message["build"], "Deck")
            return deck._inventory(remote, remote_workshop, message.get("options"), remote=True)
        archive.seek(0)
        return deck._apply(remote, remote_workshop, message, archive)

    return local, remote, workshop, remote_workshop, settings, request


def scoped_categories():
    for category in ("mods", "configs", "approvals", "loader"):
        with tempfile.TemporaryDirectory() as temporary:
            local, remote, workshop, remote_workshop, settings, request = fixture(Path(temporary))
            options = only(category)
            with patch.object(deck, "_request", side_effect=request), patch.object(deck, "_running", return_value=False):
                plan = deck.preview(local, workshop, settings, options=options)
                assert plan["options"] == options
                assert all(deck._category(name) == category for name in plan["local_files"] | plan["remote_files"])
                assert plan["categories"][category]["selected"]
                assert sum(row["local_files"] for row in plan["categories"].values()) == len(plan["local_files"])
                before = deck._inventory(remote, remote_workshop)["files"]
                unchecked = {name: digest for name, digest in before.items() if deck._category(name) != category}
                if category in ("configs", "approvals"):
                    assert "game/BepInEx/config" not in plan["remote"]["units"]
                # Changes outside the chosen scope do not invalidate its compare token.
                other = "game/" + (deck.WORKSHOP_TRUST if category != "approvals" else "BepInEx/config/settings.cfg")
                prefix, relative = other.split("/", 1)
                write(local, relative, b"unchecked PC edit")
                write(remote, relative, b"unchecked Deck edit")
                unchecked[other] = deck._hash(remote / relative)
                assert deck.preview(local, workshop, settings, options=options)["digest"] == plan["digest"]
                result = deck.sync(local, workshop, settings, plan["digest"], options=options)
                assert result["ok"] and result["parity"] and result["options"] == options
                assert deck._inventory(local, workshop, options)["files"] == deck._inventory(remote, remote_workshop, options)["files"]
                after = deck._inventory(remote, remote_workshop)["files"]
                assert {name: after[name] for name in unchecked} == unchecked
                assert (remote / "UnityPlayer.dll").read_bytes() == b"game stays untouched"
                for backup in result["backups"]:
                    root = Path(backup)
                    prefix = "workshop/" if root.parent.parent == remote_workshop else "game/"
                    for item in root.rglob("*"):
                        if item.is_file():
                            assert deck._category(prefix + item.relative_to(root).as_posix()) == category
                same = deck.preview(local, workshop, settings, options=options)
                assert deck.sync(local, workshop, settings, same["digest"], options=options)["backups"] == []


def invalid_and_stale_scope():
    raises(ValueError, deck.normalize_options, {"mods": "yes"})
    raises(ValueError, deck.normalize_options, {"configs": 1})
    raises(ValueError, deck.normalize_options, {"unknown": True})
    raises(ValueError, deck.normalize_options, [])
    empty = {key: False for key in deck.DEFAULT_OPTIONS}
    raises(ValueError, deck.normalize_options, empty)
    assert deck.normalize_options(empty, allow_empty=True) == empty
    assert deck.normalize_options() == deck.DEFAULT_OPTIONS
    with tempfile.TemporaryDirectory() as temporary:
        local, remote, workshop, remote_workshop, settings, request = fixture(Path(temporary))
        with patch.object(deck, "_request", side_effect=request), patch.object(deck, "_running", return_value=False):
            plan = deck.preview(local, workshop, settings, options=only("configs"))
            before = deck._inventory(remote, remote_workshop)
            raises(ValueError, deck.sync, local, workshop, settings, plan["digest"], None, only("approvals"))
            assert deck._inventory(remote, remote_workshop) == before
            # Even a valid mod location must be rejected when its category was unchecked.
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w") as archive:
                item = tarfile.TarInfo("game/BepInEx/plugins/Injected.dll")
                item.size = 1
                archive.addfile(item, io.BytesIO(b"x"))
            payload.seek(0)
            raises(ValueError, deck._apply, remote, remote_workshop,
                   {"local": plan["local"], "remote": plan["remote"], "options": only("configs"),
                    "build": plan["game_version"]["deck"]}, payload)
            assert deck._inventory(remote, remote_workshop) == before
            assert not (remote / ".gk2mt-sync.lock").exists()
        malformed = local / "BepInEx/config"
        for item in sorted(malformed.rglob("*"), reverse=True):
            item.rmdir() if item.is_dir() else item.unlink()
        malformed.rmdir()
        malformed.write_bytes(b"not a config directory")
        raises(ValueError, deck._inventory, local, workshop, only("configs"))
        assert deck._inventory(local, workshop, only("mods"))["files"]


def game_preferences_transaction():
    for rollback in (False, True):
        with tempfile.TemporaryDirectory() as temporary:
            local, remote, workshop, remote_workshop, settings, request = fixture(Path(temporary))
            prefix = remote.parent.parent / "compatdata/4358690/pfx"
            (prefix / "drive_c/windows").mkdir(parents=True)
            target = write(prefix, "user.reg", registry())
            write(prefix, "system.reg", HEADER)
            original = target.read_bytes()
            pc_preferences = dict(PORTABLE, language="Español", musicVolume=.55, voiceOverMode=0)
            options = only("game_settings") | {"mods": rollback}
            held = []

            class FixtureLock:
                def __enter__(self):
                    assert not held
                    held.append(True)
                def __exit__(self, *_):
                    held.pop()

            original_prepare = deck.prepare_update
            def prepare(*args):
                assert held, "Registry preparation must hold the Proton prefix lock."
                return original_prepare(*args)

            with patch.object(deck, "_request", side_effect=request), patch.object(deck, "_running", return_value=False), \
                    patch.object(deck, "local_preferences", return_value=pc_preferences), \
                    patch.object(preferences, "discover", return_value={"libraries": []}), \
                    patch.object(deck, "_proton_lock", return_value=FixtureLock()), \
                    patch.object(deck, "_proton_idle"), patch.object(deck, "prepare_update", side_effect=prepare):
                before = deck._inventory(remote, remote_workshop)
                plan = deck.preview(local, workshop, settings, options=options)
                assert plan["categories"]["game_settings"]["changes"] == 1
                if not rollback:
                    assert plan["changes"] == [deck.GAME_SETTINGS_KEY]
                    assert plan["local"]["units"] == plan["remote"]["units"] == []
                    result = deck.sync(local, workshop, settings, plan["digest"], options=options)
                    assert result["ok"] and result["parity"] and result["verified_files"] == 1
                    assert deck._inventory(remote, remote_workshop) == before
                    assert len(result["backups"]) == 1
                    assert (Path(result["backups"][0]) / "user.reg").read_bytes() == original
                    payload, _ = preferences._game_registry_value(target.read_bytes())
                    parsed = json.loads(payload[:-1])
                    assert {key: parsed[key] for key in preferences.PORTABLE_FIELDS} == pc_preferences
                    assert {key: value for key, value in parsed.items() if key not in preferences.PORTABLE_FIELDS} == {
                        key: value for key, value in DECK_SETTINGS.items() if key not in preferences.PORTABLE_FIELDS}
                    identical = deck.preview(local, workshop, settings, options=options)
                    assert deck.sync(local, workshop, settings, identical["digest"], options=options)["backups"] == []
                    # A registry edit since comparison cannot be overwritten by an old token.
                    target.write_bytes(target.read_bytes().replace(b'#time=123abc', b'#time=999abc'))
                    changed = target.read_bytes()
                    raises(ValueError, deck.sync, local, workshop, settings, identical["digest"], None, options)
                    assert target.read_bytes() == changed
                else:
                    replace = os.replace
                    def fail_registry(source, destination):
                        if Path(source).name.startswith(".gk2mt-settings-"):
                            raise OSError("simulated settings publication failure")
                        return replace(source, destination)
                    with patch.object(deck.os, "replace", side_effect=fail_registry):
                        raises(OSError, deck.sync, local, workshop, settings, plan["digest"], None, options)
                    assert target.read_bytes() == original
                    assert deck._inventory(remote, remote_workshop) == before
                assert not held and not (remote / ".gk2mt-sync.lock").exists()
                assert not list(prefix.glob(".gk2mt-settings-*"))


def configuration_shape_conflicts():
    # Folder/file swaps must fail before publication. Otherwise overlapping backup
    # paths prevent rollback, and an approvals-only replacement could move configs.
    for case in ("deck-folder", "pc-folder", "unchecked-configs"):
        with tempfile.TemporaryDirectory() as temporary:
            local, remote, workshop, remote_workshop, settings, request = fixture(Path(temporary))
            options = only("approvals") if case == "unchecked-configs" else only("configs")
            conflict = (remote / deck.WORKSHOP_TRUST if case == "unchecked-configs" else
                        (local if case == "pc-folder" else remote) / "BepInEx/config/custom/nested.json")
            conflict.unlink()
            conflict.mkdir()
            (conflict / "child.cfg").write_bytes(b"existing config must survive")
            before = deck._inventory(remote, remote_workshop)
            with patch.object(deck, "_request", side_effect=request), patch.object(deck, "_running", return_value=False):
                plan = deck.preview(local, workshop, settings, options=options)
                with patch.object(deck.os, "replace", side_effect=AssertionError("Preflight must reject before any replacement.")):
                    error = raises(ValueError, deck.sync, local, workshop, settings, plan["digest"], None, options)
                assert "file" in error and "folder" in error and "No files were changed" in error
                assert deck._inventory(remote, remote_workshop) == before
                assert (conflict / "child.cfg").read_bytes() == b"existing config must survive"
                assert not (remote / ".gk2mt-sync.lock").exists()
                assert not (remote / ".gk2mt-backups").exists()
                assert not list(remote.glob(".gk2mt-stage-*"))


if __name__ == "__main__":
    scoped_categories()
    invalid_and_stale_scope()
    game_preferences_transaction()
    configuration_shape_conflicts()
    print("Deck scope checks passed: isolated categories, preserved unchecked files, scope tokens, backups, registry transactions and archive safety.")
