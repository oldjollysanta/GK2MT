"""Run: python test_deck_discovery.py. Only temporary Steam libraries are read."""

from pathlib import Path
import tempfile

from deck_discovery import discover


def write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def install(library, folder="Graveyard Keeper 2"):
    write(library / "steamapps/appmanifest_4358690.acf", f'"AppState" {{ "installdir" "{folder}" }}')
    game = library / "steamapps/common" / folder
    write(game / "GraveyardKeeper2.exe")
    return game.resolve()


def check():
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary) / "home/deck"
        steam = home / ".local/share/Steam"
        micro_sd = Path(temporary) / "run/media/deck/My SD Card/SteamLibrary"
        assert not discover(home)["found"]
        game = install(micro_sd, "GK2 custom install")
        config = steam / "steamapps/libraryfolders.vdf"
        # New and old VDF forms; duplicate and non-library app records are ignored.
        write(config, '"libraryfolders" {\n'
              f'"0" {{ "path" "{steam.as_posix()}" "apps" {{ "4358690" "12345" }} }}\n'
              f'"1" {{ "path" "{micro_sd.as_posix()}" }}\n'
              f'"2" "{micro_sd.as_posix()}/../SteamLibrary"\n'
              '"3" "relative/path"\n}')
        result = discover(home)
        expected_workshop = micro_sd / "steamapps/workshop/content/4358690"
        assert result["found"] and result["game_path"] == str(game)
        assert result["workshop_path"] == str(expected_workshop.resolve())
        assert len(result["candidates"]) == 1
        assert not expected_workshop.exists()  # Discovery never creates directories.

        other_workshop = steam / "steamapps/workshop/content/4358690"
        other_workshop.mkdir(parents=True)
        assert discover(home)["workshop_path"] == str(other_workshop.resolve())
        expected_workshop.mkdir(parents=True)
        assert discover(home)["workshop_path"] == str(expected_workshop.resolve())

        second = install(steam)
        result = discover(home)
        assert not result["found"] and not result["game_path"] and not result["workshop_path"]
        assert {row["game_path"] for row in result["candidates"]} == {str(game), str(second)}
        (second / "GraveyardKeeper2.exe").unlink()
        assert discover(home)["found"]  # An incomplete second install is ignored.
        (game / "GraveyardKeeper2.exe").unlink()
        result = discover(home)
        assert not result["found"] and not result["candidates"]

        # Never follow an installdir traversal even if an executable exists there.
        write(micro_sd / "steamapps/appmanifest_4358690.acf", '"installdir" "../outside"')
        write(micro_sd / "steamapps/outside/GraveyardKeeper2.exe")
        assert not discover(home)["found"]

    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        steam = home / ".steam/steam"
        game = install(steam)
        assert discover(home)["game_path"] == str(game)  # Alternate Steam root.
        (steam / "steamapps/appmanifest_4358690.acf").unlink()
        assert discover(home)["found"]  # A standard folder still requires the EXE.
        extra_libraries = [home / "card one", home / "card two"]
        for library in extra_libraries:
            (library / "steamapps/workshop/content/4358690").mkdir(parents=True)
        write(steam / "steamapps/libraryfolders.vdf", "\n".join(
            f'"{index}" "{library.as_posix()}"' for index, library in enumerate(extra_libraries)))
        result = discover(home)
        assert not result["found"] and len(result["candidates"]) == 2
        assert all(row["game_path"] == str(game) for row in result["candidates"])
    print("Deck discovery checks passed.")


if __name__ == "__main__":
    check()
