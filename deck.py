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


GAME_UNITS = (
    "BepInEx/core", "BepInEx/plugins", "BepInEx/patchers", "BepInEx/config",
    # Keep legacy locations in scope so stale Deck copies are backed up when absent on PC.
    "BepInEx/GK2.WorkshopLoader.dll",
    "winhttp.dll", "doorstop_config.ini", ".doorstop_version",
    "Languages/gk2notepad/language.json",
    "GraveyardKeeper2_Data/Managed/GK2Notepad.dll",
    "GraveyardKeeper2_Data/Managed/GK2Notepad.Core.dll",
)
NOTES = [
    'Run the Windows game through Proton. Use Enable BepInEx on Deck once to configure its loader, then sync the Windows BepInEx files.',
    "The installed loader, staged Workshop plugins, and trust decisions are mirrored from the PC; sync does not enable blocked mods or install a different loader.",
    "Workshop files are copied; Steam subscriptions and appworkshop manifests are not changed.",
    "SHA256 parity covers the supported mod files, not a guarantee of save compatibility. Saves and game binaries are excluded.",
    "Close the game and pause Steam Workshop updates on both devices before syncing. Steam can subsequently change Workshop files.",
]


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
              + Path(__file__).with_name('deck_proton.py').read_bytes() + b"\n" + Path(__file__).read_bytes()
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


def _inventory(game, workshop):
    game, workshop = Path(game).resolve(), Path(workshop).resolve()
    if not (game / "GraveyardKeeper2.exe").is_file():
        raise ValueError("Game path must contain GraveyardKeeper2.exe (the Windows/Proton game).")
    if workshop.name != "4358690" or game == workshop or game in workshop.parents or workshop in game.parents:
        raise ValueError("Use the separate Workshop content/4358690 directory.")
    roots = {"game": game, "workshop": workshop}
    units = ["game/" + name for name in GAME_UNITS]
    if workshop.exists():
        units += ["workshop/" + p.name for p in sorted(workshop.iterdir())
                  if p.name.isascii() and p.name.isdigit()]
    files, sizes, unsupported = {}, {}, []
    for unit in units:
        prefix, relative = unit.split("/", 1)
        path = _safe_path(roots[prefix], relative)
        if not path.exists():
            continue
        paths = [path] if path.is_file() else sorted(path.rglob("*"))
        for item in paths:
            relative = item.relative_to(roots[prefix])
            _safe_path(roots[prefix], relative.as_posix())
            if _skip(relative) or item.is_dir():
                continue
            if not item.is_file():
                raise ValueError(f"Not a regular mod file: {item}")
            key = prefix + "/" + relative.as_posix()
            files[key] = _hash(item)
            sizes[key] = item.stat().st_size
            if prefix == "workshop" and "CopyToGameFolder" in relative.parts:
                copy_path = "/".join(relative.parts[relative.parts.index("CopyToGameFolder") + 1:])
                try:
                    _unit("game/" + copy_path)
                except ValueError:
                    unsupported.append(copy_path)
    return {"files": files, "sizes": sizes, "units": units, "unsupported": sorted(set(unsupported))}


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


def preview(game: Path, workshop: Path, settings: dict, connection=None) -> dict:
    settings = _settings(settings)
    pc_build = game_build(game, "PC")
    deck_build = _request(settings, {"action": "game-build", "game": settings["game_path"],
                                     "workshop": settings["workshop_path"]}, connection=connection)
    if pc_build != deck_build:
        raise ValueError(f"Game versions differ: PC Steam build {pc_build['build_id']}, Deck Steam build {deck_build.get('build_id', 'unknown')}. Update both games in Steam to the same build, then compare again.")
    game_version = {"pc": pc_build, "deck": deck_build, "build_id": pc_build["build_id"], "matched": True}
    local = _inventory(game, workshop)
    if not local["files"]:
        raise ValueError("No supported local mod files found; refusing an empty mirror.")
    remote = _request(settings, {"action": "preview", "game": settings["game_path"],
                                 "workshop": settings["workshop_path"], "build": deck_build}, connection=connection)
    _require_build(game, pc_build, "PC")
    left, right = local["files"], remote["files"]
    additions = sorted(left.keys() - right.keys())
    changes = sorted(k for k in left.keys() & right.keys() if left[k] != right[k])
    extras = sorted(right.keys() - left.keys())
    unsupported = sorted(set(local["unsupported"] + remote["unsupported"]))
    return {"digest": _digest([settings, game_version, local, remote]), "game_version": game_version,
            "additions": additions, "changes": changes,
            "extras": extras, "counts": {"additions": len(additions), "changes": len(changes),
                "extras": len(extras), "local_files": len(left), "remote_files": len(right)},
            "local_files": left, "remote_files": right, "local": local, "remote": remote,
            "unsupported": unsupported, "warnings": NOTES + (
                ["External CopyToGameFolder payloads are not synchronized: " + ", ".join(unsupported)] if unsupported else [])}


def sync(game: Path, workshop: Path, settings: dict, expected_digest: str, connection=None) -> dict:
    settings = _settings(settings)
    plan = preview(game, workshop, settings, connection=connection)
    if not expected_digest or expected_digest != plan["digest"]:
        raise ValueError("PC or Deck files/settings changed. Preview again before syncing.")
    if not (plan["additions"] or plan["changes"] or plan["extras"]):
        return {"ok": True, "parity": not plan["unsupported"], "verified_files": len(plan["local_files"]),
                "backups": [], "counts": {"files": len(plan["local_files"]), "quarantined_units": 0},
                "warnings": plan["warnings"]}
    roots = {"game": Path(game).resolve(), "workshop": Path(workshop).resolve()}
    with tempfile.TemporaryFile() as archive:
        with tarfile.open(fileobj=archive, mode="w", dereference=True) as tar:
            for name, digest in plan["local_files"].items():
                prefix, relative = name.split("/", 1)
                source = _safe_path(roots[prefix], relative)
                if _hash(source) != digest:
                    raise ValueError("A local mod changed while staging; preview again.")
                tar.add(source, arcname=name, recursive=False)
        if _inventory(game, workshop) != plan["local"]:
            raise ValueError("Local files changed while staging; preview again.")
        _require_build(game, plan["game_version"]["pc"], "PC")
        result = _request(settings, {"action": "sync", "game": settings["game_path"],
            "workshop": settings["workshop_path"], "local": plan["local"],
            "remote": plan["remote"], "build": plan["game_version"]["deck"]}, archive, connection=connection)
    result["warnings"] = plan["warnings"]
    result["parity"] = result["parity"] and not plan["unsupported"]
    return result


def _apply(game, workshop, request, stream):
    """Run on the Deck. Replaced directories/files remain in timestamped backups."""
    game, workshop = Path(game).resolve(), Path(workshop).resolve()
    _require_build(game, request.get("build"), "Deck")
    if _running():
        raise RuntimeError("Close Graveyard Keeper 2 on the Deck before syncing.")
    if _inventory(game, workshop) != request["remote"]:
        raise ValueError("Deck files changed after preview; preview again.")
    lock = game / ".gk2mt-sync.lock"
    lock.mkdir()  # An interrupted operation deliberately requires inspection before retry.
    roots = {"game": game, "workshop": workshop}
    stages, backups, journal = {}, {}, []
    recovered = True
    identifier = time.strftime("%Y%m%dT%H%M%SZ-", time.gmtime()) + uuid.uuid4().hex[:12]
    try:
        for key, root in roots.items():
            root.mkdir(parents=True, exist_ok=True)
            stages[key] = Path(tempfile.mkdtemp(prefix=".gk2mt-stage-", dir=root))
            backups[key] = _safe_path(root, ".gk2mt-backups/" + identifier)
        expected = request["local"]["files"]
        seen = set()
        with tarfile.open(fileobj=stream, mode="r|*") as archive:
            for member in archive:
                name = member.name
                _unit(name)
                if name not in expected or name in seen or not member.isfile():
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
        if seen != expected.keys():
            raise ValueError("Archive is incomplete.")
        _require_build(game, request.get("build"), "Deck")
        if _running() or _inventory(game, workshop) != request["remote"]:
            raise ValueError("The Deck game started or files changed during transfer; preview again.")
        units = sorted(set(request["remote"]["units"]) | {_unit(name) for name in expected})
        (lock / "transaction.json").write_text(json.dumps({"backups": {k: str(v) for k, v in backups.items()},
            "roots": {k: str(v) for k, v in roots.items()}, "units": units}, indent=2), encoding="utf-8")
        for unit in units:
            prefix, relative = unit.split("/", 1)
            target = _safe_path(roots[prefix], relative)
            staged = _safe_path(stages[prefix], relative)
            backup = _safe_path(backups[prefix], relative)
            had_old = target.exists()
            journal.append((target, backup, had_old))
            if had_old:
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            if staged.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
        if _inventory(game, workshop)["files"] != expected:
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
        result = _inventory(game, workshop)
        _require_build(game, request.get("build"), "Deck")
    elif request["action"] == "sync":
        result = _apply(game, workshop, request, sys.stdin.buffer)
    else:
        raise ValueError("Unknown Deck operation.")
    print(json.dumps(result))
