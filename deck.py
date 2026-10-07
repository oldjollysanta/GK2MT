"""Preview and verified PC -> Steam Deck mod sync over an authenticated SSH session."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sys
import tarfile
import tempfile
import time
import uuid

try:  # These helpers are prepended to the script sent over SSH.
    from deck_game_settings import local_preferences, remote_preferences, prepare_update
    from deck_proton import _proton_lock, _proton_idle, _proton_regular
except ImportError:
    pass


GAME_UNITS = (
    "BepInEx/core", "BepInEx/plugins", "BepInEx/patchers", "BepInEx/config",
    # Keep legacy locations in scope so stale Deck copies are backed up when absent on PC.
    "BepInEx/GK2.WorkshopLoader.dll",
    "winhttp.dll", "doorstop_config.ini", ".doorstop_version",
    "Languages/gk2notepad/language.json",
    "GraveyardKeeper2_Data/Managed/GK2Notepad.dll",
    "GraveyardKeeper2_Data/Managed/GK2Notepad.Core.dll",
)
WORKSHOP_TRUST = "BepInEx/config/GK2_WorkshopLoader.trust.txt"
DEFAULT_OPTIONS = {"mods": True, "configs": True, "approvals": True, "loader": True,
                   "game_settings": False}
GAME_SETTINGS_KEY = "settings/game-preferences.json"
NOTES = [
    'Run the Windows game through Proton. Use Enable BepInEx on Deck once to configure its loader, then sync the Windows BepInEx files.',
    "Only selected file types are mirrored from the PC. Sync does not enable blocked mods or install a different loader.",
    "When selected, Workshop files are copied; Steam subscriptions and appworkshop manifests are not changed.",
    "SHA256 verification covers selected mod files and optional audio/language settings, not a guarantee of save compatibility. Saves and game binaries are excluded.",
    "Close the game and pause Steam Workshop updates on both devices before syncing. Steam can subsequently change Workshop files.",
]


def normalize_options(options=None, allow_empty=False):
    """Normalize the selected scope; omitted options preserve the existing mirror."""
    if options is not None and (not isinstance(options, dict)
                                or set(options) - DEFAULT_OPTIONS.keys()
                                or any(type(value) is not bool for value in options.values())):
        raise ValueError("Choose valid Steam Deck sync options using checkboxes.")
    selected = DEFAULT_OPTIONS | (options or {})
    if not any(selected.values()) and not allow_empty:
        raise ValueError("Choose at least one type of file to sync.")
    return selected


def _settings(settings, require_paths=True):
    result = {k: str(settings.get(k, "")).strip() for k in
              ("host", "user", "key", "game_path", "workshop_path")}
    result["user"] = result["user"] or "deck"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", result["host"]):
        raise ValueError("Enter a hostname or IP address without SSH options or a user prefix.")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", result["user"]):
        raise ValueError("Invalid SSH user.")
    try:
        port = settings.get("port", 22)
        result["port"] = int(22 if port in (None, "") else port)
    except (TypeError, ValueError) as error:
        raise ValueError("SSH port must be between 1 and 65535.") from error
    if not 1 <= result["port"] <= 65535:
        raise ValueError("SSH port must be between 1 and 65535.")
    if result["key"]:
        key = Path(result["key"]).expanduser().resolve()
        if not key.is_file():
            raise ValueError("SSH private key file does not exist.")
        result["key"] = str(key)
    for field in ("game_path", "workshop_path"):
        value = result[field]
        if not value and not require_paths:
            continue
        path = PurePosixPath(value)
        if (not path.is_absolute() or len(path.parts) < 4 or ".." in path.parts
                or "\\" in value or any(ord(c) < 32 for c in value)):
            raise ValueError(f"{field} must be a full Linux directory path without '..'.")
        result[field] = str(path)
    if result["workshop_path"] and PurePosixPath(result["workshop_path"]).name != "4358690":
        raise ValueError("Deck Workshop path must end in the GK2 app ID, 4358690.")
    a, b = PurePosixPath(result["game_path"]), PurePosixPath(result["workshop_path"])
    if result["game_path"] and result["workshop_path"] and (a == b or a in b.parents or b in a.parents):
        raise ValueError("Game and Workshop directories must be separate.")
    return result


def _request(settings, request, archive=None, connection=None):
    if connection is None or not connection.alive or not connection.matches(settings):
        raise RuntimeError("Connect to this Deck in the Steam Deck tab first.")
    script = (Path(__file__).with_name('deck_discovery.py').read_bytes() + b"\n"
              + Path(__file__).with_name('deck_proton.py').read_bytes() + b"\n"
              + Path(__file__).with_name('deck_game_settings.py').read_bytes() + b"\n" + Path(__file__).read_bytes()
              + b"\ntry:\n _remote_main()\nexcept (ValueError, RuntimeError, OSError) as error:\n print(json.dumps({'gk2mt_error': str(error)}))\n")
    with tempfile.TemporaryFile() as stream:
        stream.write(str(len(script)).encode() + b"\n" + script)
        stream.write(json.dumps(request).encode() + b"\n")
        if archive:
            archive.seek(0)
            shutil.copyfileobj(archive, stream)
        stream.seek(0)
        command = "python3 -c " + shlex.quote(
            "import sys;exec(sys.stdin.buffer.read(int(sys.stdin.buffer.readline())))")
        output = connection.run(command, stream, timeout=900 if archive else 120)
    try:
        result = json.loads(output)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("Deck returned an invalid response; Python 3 is required on the Deck.") from error
    if isinstance(result, dict) and "gk2mt_error" in result:
        raise ValueError(result["gk2mt_error"])
    return result


def discover_remote(settings, connection):
    return _request(settings, {"action": "discover", "game": settings["game_path"],
                              "workshop": settings["workshop_path"]}, connection=connection)


def configure_proton(settings, connection, install=False):
    return _request(settings, {"action": "proton-setup" if install else "proton-status",
                              "game": settings["game_path"], "workshop": settings["workshop_path"]},
                    connection=connection)


def _safe_path(root, relative):
    parts = PurePosixPath(relative).parts
    if not parts or any(p in ("..", ".") for p in parts) or PurePosixPath(relative).is_absolute():
        raise ValueError("Unsafe relative mod path.")
    target = root.joinpath(*parts)
    current = root
    for part in parts:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError(f"Refusing linked mod path: {current}")
    return target


def _skip(path):
    return (any(p.lower() in ("cache", "logs", "__pycache__") for p in path.parts)
            or path.suffix.lower() == ".log" or path.name == "__folder_managed_by_vortex"
            or path.name.startswith("vortex.deployment."))


def _unit(name):
    parts = PurePosixPath(name).parts
    if len(parts) < 2 or any(p in ("..", ".") for p in parts) or "\\" in name:
        raise ValueError("Unsafe archive member.")
    if parts[0] == "game":
        for unit in GAME_UNITS:
            candidate = "game/" + unit
            if name == candidate or (unit.startswith("BepInEx/") and name.startswith(candidate + "/")):
                return candidate
    elif parts[0] == "workshop" and parts[1].isascii() and parts[1].isdigit() and len(parts) > 2:
        return "/".join(parts[:2])
    raise ValueError("Archive contains a path outside the supported mod locations: " + name)


def _category(name):
    if name == GAME_SETTINGS_KEY:
        return "game_settings"
    _unit(name)
    if name == "game/" + WORKSHOP_TRUST:
        return "approvals"
    if (name.startswith("workshop/") or name.startswith("game/BepInEx/plugins")
            or name.startswith("game/Languages/") or name.startswith("game/GraveyardKeeper2_Data/")):
        return "mods"
    if name.startswith("game/BepInEx/config"):
        return "configs"
    return "loader"


def _transfer_unit(name, options):
    unit = _unit(name)
    # Trust and ordinary configs share a directory. Replace files when only one is selected.
    if unit == "game/BepInEx/config" and options["configs"] != options["approvals"]:
        return name
    return unit


def _inventory(game, workshop, options=None, remote=False):
    options = normalize_options(options)
    game, workshop = Path(game).resolve(), Path(workshop).resolve()
    if not (game / "GraveyardKeeper2.exe").is_file():
        raise ValueError("Game path must contain GraveyardKeeper2.exe (the Windows/Proton game).")
    if workshop.name != "4358690" or game == workshop or game in workshop.parents or workshop in game.parents:
        raise ValueError("Use the separate Workshop content/4358690 directory.")
    roots = {"game": game, "workshop": workshop}
    candidates = ["game/" + name for name in GAME_UNITS
                  if options[_category("game/" + name)] or name == "BepInEx/config" and options["approvals"]]
    if options["mods"] and workshop.exists():
        candidates += ["workshop/" + p.name for p in sorted(workshop.iterdir())
                       if p.name.isascii() and p.name.isdigit()]
    units = [unit for unit in candidates if unit != "game/BepInEx/config"
             or options["configs"] and options["approvals"]]
    files, sizes, unsupported = {}, {}, []
    for unit in candidates:
        prefix, relative = unit.split("/", 1)
        path = _safe_path(roots[prefix], relative)
        if not path.exists():
            continue
        if unit == "game/BepInEx/config" and not path.is_dir():
            raise ValueError("BepInEx/config must be a directory before syncing configs or approvals.")
        paths = [path] if path.is_file() else sorted(path.rglob("*"))
        for item in paths:
            relative = item.relative_to(roots[prefix])
            key = prefix + "/" + relative.as_posix()
            if item.is_dir() or not options[_category(key)]:
                continue
            _safe_path(roots[prefix], relative.as_posix())
            if _skip(relative):
                continue
            if not item.is_file():
                raise ValueError(f"Not a regular mod file: {item}")
            files[key] = _hash(item)
            sizes[key] = item.stat().st_size
            transfer_unit = _transfer_unit(key, options)
            if transfer_unit not in units:
                units.append(transfer_unit)
            if prefix == "workshop" and "CopyToGameFolder" in relative.parts:
                copy_path = "/".join(relative.parts[relative.parts.index("CopyToGameFolder") + 1:])
                try:
                    _unit("game/" + copy_path)
                except ValueError:
                    unsupported.append(copy_path)
    result = {"files": files, "sizes": sizes, "units": sorted(units), "unsupported": sorted(set(unsupported))}
    if options["game_settings"]:
        settings = remote_preferences(game) if remote else {"preferences": local_preferences()}
        result["game_settings"] = settings
        encoded = json.dumps(settings["preferences"], sort_keys=True, separators=(",", ":")).encode()
        files[GAME_SETTINGS_KEY] = hashlib.sha256(encoded).hexdigest()
        sizes[GAME_SETTINGS_KEY] = len(encoded)
    return result


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _running():
    for process in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if b"graveyardkeeper2.exe" in process.read_bytes().lower():
                return True
        except (OSError, PermissionError):
            pass
    return False


def game_build(game, device="PC"):
    """Read the installed build, not Steam's pending TargetBuildID."""
    try:
        game = Path(game).resolve()
        if game.parent.name != "common" or game.parent.parent.name != "steamapps":
            raise ValueError("select the game folder inside its Steam library")
        manifest = game.parent.parent / "appmanifest_4358690.acf"
        with manifest.open(encoding="utf-8-sig") as stream:
            text = stream.read(2_000_001)
        if len(text) > 2_000_000:
            raise ValueError("the Steam manifest is too large")
        tokens, end = [], 0
        for match in re.finditer(r'\s+|//[^\r\n]*|"(?:\\.|[^"\\])*"|[{}]', text):
            if match.start() != end:
                raise ValueError("the Steam manifest is malformed")
            end = match.end()
            if not match[0].isspace() and not match[0].startswith("//"):
                tokens.append(match[0])
        if end != len(text):
            raise ValueError("the Steam manifest is malformed")
        position = 0

        def object_fields(depth=0):
            nonlocal position
            if depth > 20:
                raise ValueError("the Steam manifest is malformed")
            fields = {}
            while position < len(tokens):
                token = tokens[position]
                position += 1
                if token == "}":
                    if depth:
                        return fields
                    raise ValueError("the Steam manifest is malformed")
                if not token.startswith('"') or position == len(tokens):
                    raise ValueError("the Steam manifest is malformed")
                key = json.loads(token).casefold()
                value = tokens[position]
                position += 1
                if key in fields:
                    raise ValueError("the Steam manifest has duplicate fields")
                if value == "{":
                    fields[key] = object_fields(depth + 1)
                elif value.startswith('"'):
                    fields[key] = json.loads(value)
                else:
                    raise ValueError("the Steam manifest is malformed")
            if depth:
                raise ValueError("the Steam manifest is incomplete")
            return fields

        document = object_fields()
        state = document.get("appstate")
        if len(document) != 1 or not isinstance(state, dict) or state.get("appid") != "4358690":
            raise ValueError("the Steam manifest does not identify Graveyard Keeper 2")
        folder = state.get("installdir", "")
        if (not isinstance(folder, str) or folder in ("", ".", "..")
                or any(char in folder for char in "/\\:") or any(ord(char) < 32 for char in folder)
                or (game.parent / folder).resolve() != game):
            raise ValueError("the Steam manifest points to a different game folder")
        build = state.get("buildid", "")
        if not isinstance(build, str) or not re.fullmatch(r"[1-9][0-9]*", build):
            raise ValueError("the installed Steam build ID is missing or invalid")
        if state.get("stateflags") != "4":
            raise ValueError("let Steam finish installing, updating, or verifying the game first")
        if not (game / "GraveyardKeeper2.exe").is_file():
            raise ValueError("the selected folder does not contain GraveyardKeeper2.exe")
        return {"build_id": build}
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"Cannot verify the {device} game version: {error}. Open Steam on {device} and verify the installation, then compare again.") from error


def _require_build(game, expected, device):
    actual = game_build(game, device)
    if actual != expected:
        raise ValueError(f"The {device} game version changed (Steam build {actual['build_id']}). Compare again before syncing.")
    return actual


def preview(game: Path, workshop: Path, settings: dict, connection=None, options=None) -> dict:
    settings = _settings(settings)
    options = normalize_options(options)
    pc_build = game_build(game, "PC")
    deck_build = _request(settings, {"action": "game-build", "game": settings["game_path"],
                                     "workshop": settings["workshop_path"]}, connection=connection)
    if pc_build != deck_build:
        raise ValueError(f"Game versions differ: PC Steam build {pc_build['build_id']}, Deck Steam build {deck_build.get('build_id', 'unknown')}. Update both games in Steam to the same build, then compare again.")
    game_version = {"pc": pc_build, "deck": deck_build, "build_id": pc_build["build_id"], "matched": True}
    local = _inventory(game, workshop, options)
    if not local["files"]:
        raise ValueError("No PC files found in the selected sync options; nothing will be copied or removed.")
    remote = _request(settings, {"action": "preview", "game": settings["game_path"],
                                 "workshop": settings["workshop_path"], "build": deck_build,
                                 "options": options}, connection=connection)
    _require_build(game, pc_build, "PC")
    left, right = local["files"], remote["files"]
    additions = sorted(left.keys() - right.keys())
    changes = sorted(k for k in left.keys() & right.keys() if left[k] != right[k])
    extras = sorted(right.keys() - left.keys())
    unsupported = sorted(set(local["unsupported"] + remote["unsupported"]))
    categories = {key: {"selected": selected,
        "local_files": sum(_category(name) == key for name in left),
        "remote_files": sum(_category(name) == key for name in right),
        "additions": sum(_category(name) == key for name in additions),
        "changes": sum(_category(name) == key for name in changes),
        "extras": sum(_category(name) == key for name in extras)} for key, selected in options.items()}
    warnings = NOTES + (["Only the selected file types will match. Unselected Deck files stay unchanged."]
                        if options != DEFAULT_OPTIONS else [])
    return {"digest": _digest([settings, options, game_version, local, remote]), "game_version": game_version,
            "options": options, "categories": categories,
            "additions": additions, "changes": changes,
            "extras": extras, "counts": {"additions": len(additions), "changes": len(changes),
                "extras": len(extras), "local_files": len(left), "remote_files": len(right)},
            "local_files": left, "remote_files": right, "local": local, "remote": remote,
            "unsupported": unsupported, "warnings": warnings + (
                ["External CopyToGameFolder payloads are not synchronized: " + ", ".join(unsupported)] if unsupported else [])}


def sync(game: Path, workshop: Path, settings: dict, expected_digest: str, connection=None, options=None) -> dict:
    settings = _settings(settings)
    options = normalize_options(options)
    plan = preview(game, workshop, settings, connection=connection, options=options)
    if not expected_digest or expected_digest != plan["digest"]:
        raise ValueError("PC or Deck files/settings changed. Preview again before syncing.")
    if not (plan["additions"] or plan["changes"] or plan["extras"]):
        return {"ok": True, "parity": not plan["unsupported"], "verified_files": len(plan["local_files"]),
                "options": options, "categories": plan["categories"],
                "backups": [], "counts": {"files": len(plan["local_files"]), "quarantined_units": 0},
                "warnings": plan["warnings"]}
    roots = {"game": Path(game).resolve(), "workshop": Path(workshop).resolve()}
    with tempfile.TemporaryFile() as archive:
        with tarfile.open(fileobj=archive, mode="w", dereference=True) as tar:
            for name, digest in plan["local_files"].items():
                if name == GAME_SETTINGS_KEY:
                    continue
                prefix, relative = name.split("/", 1)
                source = _safe_path(roots[prefix], relative)
                if _hash(source) != digest:
                    raise ValueError("A local mod changed while staging; preview again.")
                tar.add(source, arcname=name, recursive=False)
        if _inventory(game, workshop, options) != plan["local"]:
            raise ValueError("Local files changed while staging; preview again.")
        _require_build(game, plan["game_version"]["pc"], "PC")
        result = _request(settings, {"action": "sync", "game": settings["game_path"],
            "workshop": settings["workshop_path"], "local": plan["local"],
            "remote": plan["remote"], "build": plan["game_version"]["deck"],
            "options": options}, archive, connection=connection)
    result["warnings"] = plan["warnings"]
    result["parity"] = result["parity"] and not plan["unsupported"]
    result.update(options=options, categories=plan["categories"])
    return result


def _apply(game, workshop, request, stream):
    options = normalize_options(request.get("options"))
    if options["game_settings"]:
        _require_build(Path(game).resolve(), request.get("build"), "Deck")
        if _running():
            raise RuntimeError("Close Graveyard Keeper 2 on the Deck before syncing.")
        registry = Path(remote_preferences(Path(game).resolve())["path"])
        with _proton_lock(registry.parent):
            return _apply_transaction(game, workshop, request, stream)
    return _apply_transaction(game, workshop, request, stream)


def _apply_transaction(game, workshop, request, stream):
    """Run on the Deck. Replaced directories/files remain in timestamped backups."""
    game, workshop = Path(game).resolve(), Path(workshop).resolve()
    options = normalize_options(request.get("options"))
    _require_build(game, request.get("build"), "Deck")
    if _running():
        raise RuntimeError("Close Graveyard Keeper 2 on the Deck before syncing.")
    if _inventory(game, workshop, options, remote=True) != request["remote"]:
        raise ValueError("Deck files changed after preview; preview again.")
    expected = request["local"]["files"]
    if not expected or any(not options[_category(name)] for name in expected):
        raise ValueError("Archive contains files outside the selected sync options.")
    expected_archive = expected.keys() - {GAME_SETTINGS_KEY}
    units = sorted(set(request["remote"]["units"]) | {
        _transfer_unit(name, options) for name in expected_archive})
    unit_set = set(units)
    overlap = next((str(parent) for unit in units for parent in PurePosixPath(unit).parents
                    if str(parent) in unit_set), None)
    if overlap:
        raise ValueError(f"{overlap} is a file on one device and a folder on the other. Rename the conflicting item, then compare again. No files were changed.")
    if options["configs"] != options["approvals"]:
        for unit in units:
            if unit.startswith("game/BepInEx/config/") and _safe_path(game, unit[5:]).is_dir():
                raise ValueError(f"{unit} is a file on PC but a folder on the Deck. Rename the conflicting Deck folder, then compare again. No files were changed.")
    lock = game / ".gk2mt-sync.lock"
    lock.mkdir()  # An interrupted operation deliberately requires inspection before retry.
    roots = {"game": game, "workshop": workshop}
    stages, backups, journal = {}, {}, []
    registry_stage = None
    recovered = True
    identifier = time.strftime("%Y%m%dT%H%M%SZ-", time.gmtime()) + uuid.uuid4().hex[:12]
    try:
        registry_update = None
        if options["game_settings"]:
            preferences = request["local"].get("game_settings", {}).get("preferences")
            if _digest(preferences) != expected.get(GAME_SETTINGS_KEY):
                raise ValueError("Game settings changed during transfer; compare again.")
            registry, original, updated = prepare_update(game, preferences)
            if hashlib.sha256(original).hexdigest() != request["remote"]["game_settings"]["registry_hash"]:
                raise ValueError("Deck game settings changed after preview; compare again.")
            if original != updated:
                registry_update = (registry, original, updated)
        for key in {name.split("/", 1)[0] for name in expected if name != GAME_SETTINGS_KEY} | {
                name.split("/", 1)[0] for name in request["remote"]["units"]}:
            root = roots[key]
            root.mkdir(parents=True, exist_ok=True)
            stages[key] = Path(tempfile.mkdtemp(prefix=".gk2mt-stage-", dir=root))
            backups[key] = _safe_path(root, ".gk2mt-backups/" + identifier)
        seen = set()
        with tarfile.open(fileobj=stream, mode="r|*") as archive:
            for member in archive:
                name = member.name
                _unit(name)
                if name not in expected_archive or name in seen or not member.isfile():
                    raise ValueError("Unexpected, duplicate or non-file archive member.")
                if member.size != request["local"]["sizes"][name]:
                    raise ValueError("Archive file size changed.")
                prefix, relative = name.split("/", 1)
                target = _safe_path(stages[prefix], relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                if _hash(target) != expected[name]:
                    raise ValueError("Archive hash verification failed.")
                seen.add(name)
        if seen != expected_archive:
            raise ValueError("Archive is incomplete.")
        _require_build(game, request.get("build"), "Deck")
        if _running() or _inventory(game, workshop, options, remote=True) != request["remote"]:
            raise ValueError("The Deck game started or files changed during transfer; preview again.")
        if registry_update:
            registry, original, updated = registry_update
            info = _proton_regular(registry)
            registry_identity = (info.st_dev, info.st_ino)
            backups["settings"] = _safe_path(registry.parent.parent, "gk2mt-backups/" + identifier)
            fd, temporary = tempfile.mkstemp(prefix=".gk2mt-settings-", suffix=".reg", dir=registry.parent)
            registry_stage = Path(temporary)
            with os.fdopen(fd, "wb") as output:
                os.chmod(registry_stage, info.st_mode & 0o777)
                output.write(updated)
                output.flush()
                os.fsync(output.fileno())
        (lock / "transaction.json").write_text(json.dumps({"backups": {k: str(v) for k, v in backups.items()},
            "roots": {k: str(v) for k, v in roots.items()}, "units": units,
            "game_settings": {"path": str(registry_update[0]),
                              "original_hash": hashlib.sha256(registry_update[1]).hexdigest(),
                              "updated_hash": hashlib.sha256(registry_update[2]).hexdigest()}
                             if registry_update else None}, indent=2), encoding="utf-8")
        for unit in units:
            prefix, relative = unit.split("/", 1)
            target = _safe_path(roots[prefix], relative)
            staged = _safe_path(stages[prefix], relative)
            backup = _safe_path(backups[prefix], relative)
            if (unit.startswith("game/BepInEx/config/") and options["configs"] != options["approvals"]
                    and target.is_dir()):
                raise ValueError(f"{unit} became a folder during transfer. Compare again before syncing.")
            had_old = target.exists()
            journal.append((target, backup, had_old))
            if had_old:
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            if staged.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
        if registry_update:
            registry, original, updated = registry_update
            _proton_idle()
            info = _proton_regular(registry)
            if ((info.st_dev, info.st_ino) != registry_identity or registry.read_bytes() != original
                    or remote_preferences(game) != request["remote"]["game_settings"]):
                raise ValueError("Deck game settings changed during transfer; compare again.")
            backup = _safe_path(registry.parent.parent, "gk2mt-backups/" + identifier + "/user.reg")
            backup.parent.mkdir(parents=True, exist_ok=True)
            journal.append((registry, backup, True))
            os.replace(registry, backup)
            os.replace(registry_stage, registry)
            if registry.read_bytes() != updated:
                raise RuntimeError("Game settings verification failed; restoring the previous setup.")
        if _inventory(game, workshop, options, remote=True)["files"] != expected:
            raise RuntimeError("Post-sync verification failed; restoring the previous setup.")
        _require_build(game, request.get("build"), "Deck")
        return {"ok": True, "parity": True, "verified_files": len(expected),
                "backups": [str(p) for p in backups.values() if p.exists()],
                "counts": {"files": len(expected), "quarantined_units": sum(old for _, _, old in journal)}}
    except BaseException:
        recovered = False
        for target, backup, had_old in reversed(journal):
            # Keep failed replacements too; do not delete any user's mod files.
            if target.exists() and (backup.exists() or not had_old):
                failed = target.parent / (target.name + ".gk2mt-failed-" + identifier)
                os.replace(target, failed)
            if backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
        recovered = True
        raise
    finally:
        for stage in stages.values():
            shutil.rmtree(stage)
        if registry_stage:
            registry_stage.unlink(missing_ok=True)
        if recovered:
            (lock / "transaction.json").unlink(missing_ok=True)
            lock.rmdir()


def _remote_main():
    request = json.loads(sys.stdin.buffer.readline())
    if request["action"] == "discover":
        result = discover()
        if request.get("game") and request.get("workshop") and (Path(request["game"]) / "GraveyardKeeper2.exe").is_file():
            result.update(found=True, game_path=request["game"], workshop_path=request["workshop"],
                          message="Using your saved Deck game folders.")
        print(json.dumps(result))
        return
    game, workshop = Path(request["game"]), Path(request["workshop"])
    if _running():
        raise RuntimeError("Close Graveyard Keeper 2 on the Deck first.")
    if request["action"] == "game-build":
        result = game_build(game, "Deck")
    elif request["action"] in ("proton-status", "proton-setup"):
        game_build(game, "Deck")
        result = proton_setup(game, discover()["libraries"], install=request["action"] == "proton-setup")
    elif request["action"] == "preview":
        _require_build(game, request.get("build"), "Deck")
        result = _inventory(game, workshop, request.get("options"), remote=True)
        _require_build(game, request.get("build"), "Deck")
    elif request["action"] == "sync":
        result = _apply(game, workshop, request, sys.stdin.buffer)
    else:
        raise ValueError("Unknown Deck operation.")
    print(json.dumps(result))
