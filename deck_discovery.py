"""Read-only Steam library discovery; also sent as source to the Steam Deck."""

from pathlib import Path
import re


def _deck_discovery_text(path):
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as stream:
            return stream.read(2_000_000)
    except OSError:
        return ""


def _deck_discovery_unescape(value):
    return value.replace(r"\\", "\\").replace(r'\"', '"')


def discover(home=None):
    """Find the Windows/Proton game in Steam's known libraries, including microSD."""
    home = Path.home() if home is None else Path(home)
    roots = [home / ".local/share/Steam", home / ".steam/steam"]
    libraries = list(roots)
    for root in roots:
        config = _deck_discovery_text(root / "steamapps/libraryfolders.vdf")
        for value in re.findall(r'"(?:path|\d+)"\s*"((?:\\.|[^"\\])*)"', config):
            value = _deck_discovery_unescape(value)
            if not any(ord(char) < 32 for char in value) and Path(value).is_absolute():
                libraries.append(Path(value))
    unique = []
    for library in libraries:
        try:
            library = library.resolve()
        except (OSError, RuntimeError):
            continue
        if library not in unique:
            unique.append(library)

    games, workshops = [], []
    for library in unique:
        workshop = library / "steamapps/workshop/content/4358690"
        if workshop.is_dir():
            workshops.append(workshop.resolve())
        manifest = _deck_discovery_text(library / "steamapps/appmanifest_4358690.acf")
        match = re.search(r'"installdir"\s*"((?:\\.|[^"\\])*)"', manifest, re.I)
        folder = _deck_discovery_unescape(match.group(1)) if match else "Graveyard Keeper 2"
        if folder in ("", ".", "..") or any(char in folder for char in "/\\:") or any(ord(char) < 32 for char in folder):
            continue
        game = library / "steamapps/common" / folder
        if (game / "GraveyardKeeper2.exe").is_file():
            pair = (game.resolve(), workshop.resolve())
            if not any(existing[0] == pair[0] for existing in games):
                games.append(pair)

    workshops = list(dict.fromkeys(workshops))
    candidates = []
    for game, workshop in games:
        # Prefer the game's library. Else reuse an existing Workshop library;
        # expose ambiguity instead of guessing where a sync should write.
        choices = [workshop] if workshop.is_dir() or not workshops else workshops
        for choice in choices:
            candidates.append({"game_path": str(game), "workshop_path": str(choice)})
    found = len(candidates) == 1
    message = ("Game and Workshop folders found." if found else
               "More than one Steam installation or Workshop location was found. Choose the folders in Advanced settings." if candidates else
               "Graveyard Keeper 2 was not found in your Steam libraries. Install the Windows/Proton game or enter its folders in Advanced settings.")
    return {"found": found, "game_path": candidates[0]["game_path"] if found else "",
            "workshop_path": candidates[0]["workshop_path"] if found else "",
            "candidates": candidates, "message": message, "libraries": [str(path) for path in unique]}
