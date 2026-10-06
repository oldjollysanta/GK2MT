"""Run: python test_deck.py. Uses only temporary trees; never connects to a Deck."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch

import deck


def write(root, name, data=b"mod"):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def raises(exception, function, *args):
    try:
        function(*args)
    except exception as error:
        return str(error)
    raise AssertionError(f"Expected {exception.__name__}")


def steam_manifest(game, build="123456", **fields):
    data = {"appid": "4358690", "installdir": game.name, "buildid": build,
            "StateFlags": "4", "TargetBuildID": "0"} | fields
    manifest = game.parent.parent / "appmanifest_4358690.acf"
    manifest.write_text('"AppState"\n{\n' + '\n'.join(json.dumps(k) + ' ' + json.dumps(v)
                        for k, v in data.items()) + '\n}\n', encoding="utf-8")
    return manifest


def check():
    settings = {"host": "steamdeck.local", "game_path": "/home/deck/games/Graveyard Keeper 2",
                "workshop_path": "/home/deck/workshop/content/4358690"}
    normalized = deck._settings(settings)
    assert 'password' not in deck._settings(settings | {'password': 'not-persisted'})
    assert deck._settings({'host': 'steamdeck.local'}, require_paths=False)['game_path'] == ''
    for field, value in (("host", "deck; touch /tmp/oops"), ("host", "-oProxyCommand=bad"),
                         ("user", "deck root"), ("port", 0), ("game_path", "/"),
                         ("game_path", "/home/deck/../root"), ("workshop_path", "/home/deck")):
        raises(ValueError, deck._settings, settings | {field: value})

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        local = root / "pc/steamapps/common/Graveyard Keeper 2"
        remote = root / "deck/steamapps/common/Graveyard Keeper 2"
        workshop, remote_workshop = root / "pc/4358690", root / "deck/4358690"
        for game in (local, remote):
            write(game, "GraveyardKeeper2.exe", b"game binary")
            write(game, "UnityPlayer.dll", b"untouched game binary")
            steam_manifest(game)
        write(local, "winhttp.dll")
        write(local, "doorstop_config.ini", b"new BepInEx bootstrap")
        write(local, "BepInEx/patchers/GK2_WorkshopLoader.dll", b"new Workshop patcher")
        write(local, "BepInEx/plugins/_Workshop/1234/GK2Respec.dll", b"active root Workshop DLL")
        write(local, "BepInEx/plugins/example.dll")
        os.link(local / "BepInEx/plugins/example.dll", local / "BepInEx/plugins/hardlinked.dll")
        write(local, "BepInEx/plugins/off.dll.gk2mt-disabled")
        trust = b"1234 = 0123456789abcdef # GK2 Respec\n5678 = BLOCKED # GK2Notepad\n"
        write(local, "BepInEx/config/GK2_WorkshopLoader.trust.txt", trust)
        write(local, "BepInEx/config/settings.cfg", b"new configuration")
        write(local, "BepInEx/cache/ignored.bin")
        write(local, "BepInEx/LogOutput.log")
        write(workshop, "1234/GK2Respec.dll", b"active root Workshop DLL")
        write(workshop, "5678/CopyToGameFolder/Languages/gk2notepad/language.json", b'{}')
        write(workshop, "5678/CopyToGameFolder/GraveyardKeeper2_Data/Managed/GK2Notepad.dll", b"blocked payload")
        write(workshop, "1234/CopyToGameFolder/UnityPlayer.dll", b"unsupported")
        write(remote, "BepInEx/GK2.WorkshopLoader.dll", b"obsolete loader fallback")
        write(remote, "Languages/gk2notepad/language.json", b"obsolete Notepad localization")
        write(remote, "GraveyardKeeper2_Data/Managed/GK2Notepad.dll", b"obsolete Notepad DLL")
        write(remote, "BepInEx/plugins/extra.dll", b"deck only")
        write(remote, "BepInEx/config/settings.cfg", b"old configuration")
        write(remote_workshop, "9999/extra.dll", b"deck workshop only")

        def request(_settings, message, archive=None, connection=None):
            if message["action"] == "game-build":
                return deck.game_build(remote, "Deck")
            if message["action"] == "preview":
                deck._require_build(remote, message["build"], "Deck")
                return deck._inventory(remote, remote_workshop)
            archive.seek(0)
            return deck._apply(remote, remote_workshop, message, archive)

        with patch.object(deck, "_request", side_effect=request), patch.object(deck, "_running", return_value=False):
            # Unknown, malformed, stale, and mismatched builds fail before hashing any mods.
            for game, label in ((local, "PC"), (remote, "Deck")):
                manifest = steam_manifest(game)
                bad_manifests = [None, '"AppState" {', '"AppState" { "appid" "4358690" }',
                                 manifest.read_text().replace('"appid" "4358690"', '"appid" "1"'),
                                 manifest.read_text().replace('"buildid" "123456"', '"buildid" "0"'),
                                 manifest.read_text().replace('"buildid" "123456"', '"buildid" "123456" "buildid" "123456"'),
                                 manifest.read_text().replace('"StateFlags" "4"', '"StateFlags" "1026"'),
                                 manifest.read_text().replace('"installdir" "Graveyard Keeper 2"', '"installdir" "Other game"'),
                                 manifest.read_text().replace('"installdir" "Graveyard Keeper 2"', '"installdir" "../Graveyard Keeper 2"')]
                for malformed in bad_manifests:
                    if malformed is None:
                        manifest.unlink()
                    else:
                        manifest.write_text(malformed, encoding="utf-8")
                    with patch.object(deck, "_inventory", side_effect=AssertionError("inventory ran before version verification")):
                        assert label in raises(ValueError, deck.preview, local, workshop, settings)
                    steam_manifest(game)
            # Nested depot metadata and completed download counters do not alter installed build identity.
            manifest = steam_manifest(local, TargetBuildID="999999", BytesToDownload="29536", BytesDownloaded="29536")
            manifest.write_text(manifest.read_text().rsplit("}", 1)[0] +
                                '"InstalledDepots" { "4358691" { "manifest" "42" } }\n}\n', encoding="utf-8")
            assert deck.game_build(local) == {"build_id": "123456"}
            steam_manifest(remote, "654321")
            with patch.object(deck, "_inventory", side_effect=AssertionError("inventory ran before version verification")):
                error = raises(ValueError, deck.preview, local, workshop, settings)
                assert "PC Steam build 123456" in error and "Deck Steam build 654321" in error
            steam_manifest(remote)
            # An unchanged mod inventory cannot reuse a preview after both games update.
            initial = deck.preview(local, workshop, settings)
            before = deck._inventory(remote, remote_workshop)
            steam_manifest(local, "654321")
            steam_manifest(remote, "654321")
            assert initial["digest"] != deck.preview(local, workshop, settings)["digest"]
            raises(ValueError, deck.sync, local, workshop, settings, initial["digest"])
            assert deck._inventory(remote, remote_workshop) == before
            steam_manifest(local)
            steam_manifest(remote)

            # A PC game update during archive staging blocks the transfer.
            initial = deck.preview(local, workshop, settings)
            original_add = tarfile.TarFile.add

            def pc_updates_while_staging(archive, *args, **kwargs):
                original_add(archive, *args, **kwargs)
                steam_manifest(local, "654321")

            with patch.object(tarfile.TarFile, "add", autospec=True, side_effect=pc_updates_while_staging):
                assert "PC game version changed" in raises(ValueError, deck.sync, local, workshop, settings, initial["digest"])
            assert deck._inventory(remote, remote_workshop) == before
            steam_manifest(local)

            # Deck changes before receiving or during extraction preserve all destination mods.
            original_copy = deck.shutil.copyfileobj
            for during_transfer in (False, True):
                def deck_changes(_settings, message, archive=None, connection=None):
                    if message["action"] != "sync":
                        return request(_settings, message, archive, connection)
                    if not during_transfer:
                        steam_manifest(remote, "654321")
                        return request(_settings, message, archive, connection)

                    def change_after_copy(*args, **kwargs):
                        original_copy(*args, **kwargs)
                        steam_manifest(remote, "654321")

                    with patch.object(deck.shutil, "copyfileobj", side_effect=change_after_copy):
                        return request(_settings, message, archive, connection)

                with patch.object(deck, "_request", side_effect=deck_changes):
                    assert "Deck game version changed" in raises(ValueError, deck.sync, local, workshop, settings, initial["digest"])
                assert deck._inventory(remote, remote_workshop) == before
                assert not (remote / ".gk2mt-sync.lock").exists()
                assert not list(remote.glob(".gk2mt-stage-*"))
                assert not (remote / ".gk2mt-backups").exists()
                steam_manifest(remote)

            plan = deck.preview(local, workshop, settings)
            assert plan["game_version"] == {"pc": {"build_id": "123456"}, "deck": {"build_id": "123456"},
                                            "build_id": "123456", "matched": True}
            assert plan["changes"] == ["game/BepInEx/config/settings.cfg"]
            for relative in ("BepInEx/patchers/GK2_WorkshopLoader.dll",
                             "BepInEx/config/GK2_WorkshopLoader.trust.txt",
                             "BepInEx/plugins/_Workshop/1234/GK2Respec.dll", "winhttp.dll", "doorstop_config.ini"):
                assert "game/" + relative in plan["additions"]
            for relative in ("BepInEx/GK2.WorkshopLoader.dll", "Languages/gk2notepad/language.json",
                             "GraveyardKeeper2_Data/Managed/GK2Notepad.dll"):
                assert "game/" + relative in plan["extras"]
            assert "game/BepInEx/plugins/extra.dll" in plan["extras"]
            assert plan["unsupported"] == ["UnityPlayer.dll"]
            assert all("cache/" not in name and not name.endswith(".log") for name in plan["local_files"])
            write(local, "BepInEx/plugins/example.dll", b"changed")
            raises(ValueError, deck.sync, local, workshop, settings, plan["digest"])
            assert (remote / "BepInEx/plugins/extra.dll").exists()
            plan = deck.preview(local, workshop, settings)
            result = deck.sync(local, workshop, settings, plan["digest"])
            assert result["ok"] and not result["parity"]  # Unsupported external payload is disclosed.
            assert deck._inventory(local, workshop)["files"] == deck._inventory(remote, remote_workshop)["files"]
            assert not (remote / "BepInEx/plugins/extra.dll").exists()
            assert (remote / "UnityPlayer.dll").read_bytes() == b"untouched game binary"
            assert (remote / "BepInEx/config/GK2_WorkshopLoader.trust.txt").read_bytes() == trust
            assert not (remote / "BepInEx/GK2.WorkshopLoader.dll").exists()
            assert not (remote / "GraveyardKeeper2_Data/Managed/GK2Notepad.dll").exists()
            assert not (remote / "Languages/gk2notepad/language.json").exists()
            assert any((Path(p) / "BepInEx/plugins/extra.dll").exists() for p in result["backups"])
            assert any((Path(p) / "GraveyardKeeper2_Data/Managed/GK2Notepad.dll").exists() for p in result["backups"])
            assert any((Path(p) / "9999/extra.dll").exists() for p in result["backups"])
            identical = deck.preview(local, workshop, settings)
            assert deck.sync(local, workshop, settings, identical["digest"])["backups"] == []

            # A failed replacement restores the previous mod tree.
            before = deck._inventory(remote, remote_workshop)
            write(local, "BepInEx/plugins/example.dll", b"another version")
            plan = deck.preview(local, workshop, settings)
            real_replace = os.replace

            def fail_replace(source, target):
                if ".gk2mt-stage-" in str(source) and str(target).endswith("plugins"):
                    raise OSError("simulated replacement failure")
                return real_replace(source, target)

            with patch.object(deck.os, "replace", side_effect=fail_replace):
                raises(OSError, deck.sync, local, workshop, settings, plan["digest"])
            assert deck._inventory(remote, remote_workshop) == before
            assert not (remote / ".gk2mt-sync.lock").exists()

            # Even a crafted tar cannot escape the owned mod paths.
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w") as archive:
                item = tarfile.TarInfo("game/BepInEx/plugins/../../escape")
                item.size = 1
                archive.addfile(item, io.BytesIO(b"x"))
            payload.seek(0)
            raises(ValueError, deck._apply, remote, remote_workshop,
                   {"local": plan["local"], "remote": before, "build": plan["game_version"]["deck"]}, payload)
            assert deck._inventory(remote, remote_workshop) == before
            assert not (remote / "escape").exists()

        # Exercise the actual script framing through a local Python subprocess,
        # replacing only the SSH transport, so stdin consumption is checked too.
        class LocalTransport:
            alive = True

            def matches(self, value):
                return value == normalized

            def run(self, command, stream, timeout):
                assert command.startswith('python3 -c ')
                result = subprocess.run([sys.executable, "-c",
                    "import sys;exec(sys.stdin.buffer.read(int(sys.stdin.buffer.readline())))"],
                    stdin=stream, capture_output=True, timeout=timeout, check=True)
                return result.stdout

        answer = deck._request(normalized, {"action": "preview", "game": str(remote),
                                           "workshop": str(remote_workshop), "build": {"build_id": "123456"}}, connection=LocalTransport())
        assert answer == deck._inventory(remote, remote_workshop)
        answer = deck._request(normalized, {"action": "game-build", "game": str(remote),
                                           "workshop": str(remote_workshop)}, connection=LocalTransport())
        assert answer == {"build_id": "123456"}
        # The shipped helper includes the Proton code and safely scopes status to this game.
        prefix = remote.parent.parent / 'compatdata/4358690/pfx'
        write(prefix, 'user.reg', b'WINE REGISTRY Version 2\n')
        write(prefix, 'system.reg', b'WINE REGISTRY Version 2\n')
        (prefix / 'drive_c/windows').mkdir(parents=True)
        answer = deck._request(normalized, {"action": "proton-status", "game": str(remote),
                                           "workshop": ''}, connection=LocalTransport())
        assert answer['configured'] is False and answer['prefix'] == str(prefix)
        assert answer['loader_installed'] is False
        (remote.parent.parent / "appmanifest_4358690.acf").unlink()
        try:
            deck._request(normalized, {"action": "game-build", "game": str(remote),
                                      "workshop": str(remote_workshop)}, connection=LocalTransport())
        except ValueError as error:
            assert str(error).startswith("Cannot verify the Deck game version:") and "Traceback" not in str(error)
        else:
            raise AssertionError("Missing remote build must block comparison with a clear Deck error.")
        raises(RuntimeError, deck._request, normalized, {'action': 'preview'})
    print("Deck checks passed: build verification, new loader and blocked-mod parity, legacy cleanup, preview token, quarantine, rollback, tar safety, SSH framing.")


if __name__ == "__main__":
    check()
