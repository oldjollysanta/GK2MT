"""Read-only Steam/BepInEx inventory and explicit, reversible mod switches."""
from __future__ import annotations

import json
import mmap
import os
from pathlib import Path
import re
import stat
import tempfile

APP_ID = "4358690"
DEFAULT_STEAM = Path(r"C:\Program Files (x86)\Steam")
DISABLED = ".gk2mt-disabled"
TRUST = "BepInEx/config/GK2_WorkshopLoader.trust.txt"
LOADERS = {
    "gk2_workshoploader.dll": ("workshop", "GK2 Workshop Loader", "3807346541"),
    "gk2.workshopautoloader.dll": ("legacy", "GK2 Workshop Auto-Loader (legacy)", "3807406994"),
}


def _linked(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return path.is_symlink()
    except OSError:
        return True


def _safe(path: Path, root: Path) -> bool:
    """Reject directory links/junctions; ordinary Vortex hardlinks are fine."""
    try:
        relative = path.absolute().relative_to(root.absolute())
        if _linked(root):
            return False
        current = root
        for part in relative.parts:
            if part in ("..", "."):
                return False
            current /= part
            if os.path.lexists(current) and _linked(current):
                return False
        # Lexical containment and reparse checks also handle Windows' virtualized
        # AppData files, whose resolved name can be outside the logical root.
        return True
    except (OSError, ValueError):
        return False


def _files(root: Path):
    if not root.is_dir() or _linked(root):
        return
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not _linked(Path(base) / d))
        for name in sorted(files):
            path = Path(base) / name
            if not _linked(path):
                yield path


def _text(path: Path, limit=2_000_000) -> str:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            return stream.read(limit)
    except OSError:
        return ""


def _active_name(path: str) -> str:
    return path[:-len(DISABLED)] if path.lower().endswith(DISABLED) else path


def _dll(path: Path) -> bool:
    return _active_name(path.name).lower().endswith(".dll")


def _normal(value: str) -> str:
    value = re.sub(r"graveyard[\s._-]*keeper[\s._-]*2|gk2|\bmod\b", "", value, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", value.lower())


def duplicate_groups(rows: list[dict]) -> list[dict]:
    """Warn about separate Steam/local copies without changing ownership or enabled state."""
    def identities(row):
        nexus = str(row.get("nexus_mod_id", ""))
        nexus = nexus if re.fullmatch(r"[1-9][0-9]*", nexus) else ""
        guids = set()
        for field in ("plugin_guid", "plugin_guids", "guid"):
            values = row.get(field, [])
            for value in [values] if isinstance(values, str) else values if isinstance(values, (list, tuple, set)) else []:
                if isinstance(value, str) and value.strip():
                    guids.add(value.strip().casefold())
        dlls = set()
        for value in list(row.get("paths", [])) + list(row.get("workshop_paths", [])):
            if not isinstance(value, str):
                continue
            parts = value.replace("\\", "/").casefold().split("/")
            name = _active_name(parts[-1])
            if (not name.endswith(".dll") or any(part in {"core", "installer", "managed", "loader", "_sync", "fomod"}
                                                 for part in parts[:-1])
                    or name in set(LOADERS) | {"bepinex.dll", "configurationmanager.dll", "helper.dll", "common.dll", "core.dll", "utils.dll"}
                    or name.startswith(("0harmony", "harmony", "system.", "microsoft.", "unity.", "unityengine.",
                                        "mono.", "monomod.", "bepinex.", "newtonsoft.", "netstandard."))):
                continue
            dlls.add(name)
        return nexus, guids, dlls

    eligible = {row["id"]: row for row in rows if isinstance(row.get("id"), str) and str(row.get("nexus_mod_id")) != "48"}
    steam = lambda row: bool(row.get("workshop_id") or row.get("source") == "Steam Workshop")
    features = {identifier: identities(row) for identifier, row in eligible.items()}
    links = {}
    # ponytail: local mod libraries are small; index identities if thousands of rows make pairwise matching slow.
    for first, first_row in eligible.items():
        if not steam(first_row):
            continue
        for second, second_row in eligible.items():
            if steam(second_row):
                continue
            nexus_a, guids_a, dlls_a = features[first]
            nexus_b, guids_b, dlls_b = features[second]
            if nexus_a and nexus_a == nexus_b:
                evidence = (0, "nexus", f"Same Nexus mod #{nexus_a}")
            elif guids_a & guids_b:
                evidence = (1, "guid", "Same plugin GUID: " + sorted(guids_a & guids_b)[0])
            elif dlls_a & dlls_b:
                evidence = (2, "dll", "Matching plugin DLL: " + sorted(dlls_a & dlls_b)[0])
            else:
                continue
            links.setdefault(first, []).append((second, evidence))
            links.setdefault(second, []).append((first, evidence))
    groups, seen = [], set()
    for identifier in sorted(links):
        if identifier in seen:
            continue
        pending, members, evidence = [identifier], set(), []
        while pending:
            current = pending.pop()
            if current in members:
                continue
            members.add(current)
            for other, reason in links[current]:
                pending.append(other)
                evidence.append(reason)
        seen.update(members)
        _, match, reason = min(evidence)
        enabled = sum(bool(eligible[member].get("enabled")) for member in members)
        groups.append({"row_ids": sorted(members), "reason": reason, "match": match,
                       "enabled_count": enabled, "disabled_count": len(members) - enabled})
    return groups


def discover() -> dict:
    """Find Steam libraries without scanning every drive or changing Steam."""
    roots = [DEFAULT_STEAM]
    if os.name == "nt":
        import winreg
        for hive, subkey, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    roots.insert(0, Path(winreg.QueryValueEx(key, value)[0]))
            except OSError:
                pass
    else:
        roots += [Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"]
    libraries = list(roots)
    for root in roots:
        for value in re.findall(r'"(?:path|\d+)"\s*"((?:\\.|[^"\\])*)"', _text(root / "steamapps/libraryfolders.vdf")):
            value = value.replace("\\\\", "\\").replace('\\"', '"')
            if "/" in value or "\\" in value:
                libraries.append(Path(value))
    candidates, seen = [], set()
    for library in libraries:
        key = str(library.absolute()).casefold()
        if key in seen:
            continue
        seen.add(key)
        manifest = _text(library / f"steamapps/appmanifest_{APP_ID}.acf")
        match = re.search(r'"installdir"\s*"([^"\r\n]+)"', manifest)
        folder = match.group(1) if match and not any(c in match.group(1) for c in "/\\:") else "Graveyard Keeper 2"
        game = library / "steamapps/common" / folder
        workshop = library / f"steamapps/workshop/content/{APP_ID}"
        if game.is_dir() or workshop.is_dir():
            candidates.append({"game": str(game), "workshop": str(workshop), "library": str(library),
                               "installed": (game / "GraveyardKeeper2.exe").is_file()})
    selected = next((row for row in candidates if row["installed"]), candidates[0] if candidates else {})
    return {"game": selected.get("game", str(DEFAULT_STEAM / "steamapps/common/Graveyard Keeper 2")),
            "workshop": selected.get("workshop", str(DEFAULT_STEAM / f"steamapps/workshop/content/{APP_ID}")),
            "candidates": candidates}


def _metadata(game: Path) -> dict:
    result = {}
    config = game / "BepInEx/config"
    for path in _files(config) if _safe(config, game) else []:
        if path.suffix.lower() != ".cfg":
            continue
        content = _text(path, 8192)
        match = re.search(r"Settings file was created by plugin (.+?) v([^\r\n]+)", content)
        if match:
            name, version = match.groups()
            guid = re.search(r"Plugin GUID:\s*([^\s]+)", content)
            for alias in [name, path.stem, guid.group(1).split(".")[-1] if guid else ""]:
                if alias:
                    result[_normal(alias)] = (name.strip(), version.strip(), "config header")
    log_path = game / "BepInEx/LogOutput.log"
    if _safe(log_path, game):
        for name, version in re.findall(r"(?:Loading |patcher method from )\[(.+?) ([0-9][^\]\s]*)\]", _text(log_path)):
            result[_normal(name)] = (name, version, "last game log")
    return result


def _vortex(game: Path, outdated=None) -> dict:
    owners = {}
    for parent in (game, game / "BepInEx", game / "BepInEx/plugins", game / "BepInEx/patchers"):
        if not _safe(parent, game):
            continue
        for manifest in parent.glob("vortex.deployment*.json"):
            if not _safe(manifest, game):
                continue
            try:
                data = json.loads(_text(manifest, 10_000_000))
                target = Path(data.get("targetPath") or parent)
                if not _safe(target, game):
                    continue
                for entry in data.get("files", []):
                    relative = str(entry.get("relPath", "")).replace("\\", "/")
                    path = target / relative
                    if relative and _safe(path, game) and entry.get("source"):
                        key = path.relative_to(game).as_posix().casefold()
                        owners[key] = str(entry["source"])
                        if outdated is not None:
                            outdated.discard(key)
                            current = path if path.is_file() else path.with_name(path.name + DISABLED)
                            recorded = entry.get("time")
                            # Vortex records each file's mtime in milliseconds, not its deployment date.
                            # ponytail: timestamp comparison detects replacements; hash provenance if manifests gain hashes.
                            if (isinstance(recorded, (int, float)) and not isinstance(recorded, bool)
                                    and _safe(current, game) and current.is_file()
                                    and abs(current.stat().st_mtime * 1000 - recorded) > 1):
                                outdated.add(key)
            except (ValueError, TypeError, OSError):
                continue
    return owners


def loader_info(game: Path) -> dict:
    """Identify enabled patchers without loading any assembly."""
    game = Path(game).absolute()
    base = game / "BepInEx/patchers"
    active = []
    for path in _files(base) if _safe(base, game) else []:
        if path.name.casefold() in LOADERS:
            kind, name, item_id = LOADERS[path.name.casefold()]
            active.append({"kind": kind, "name": name, "path": path.relative_to(game).as_posix(),
                           "workshop_id": item_id})
    if len(active) > 1:
        return {"kind": "conflict", "name": "Multiple Workshop loaders enabled", "loaders": active}
    return active[0] if active else {"kind": "none", "name": "No Workshop loader enabled"}


def _trust(game: Path, kind: str) -> dict:
    path = game / TRUST
    if not _safe(path, game):
        return {}
    result = {}
    for line in _text(path).splitlines():
        if kind == "workshop":
            match = re.fullmatch(r"\s*(\d+)\s*=\s*([0-9a-fA-F]{64}|BLOCKED)\s*(?:#(.*))?", line)
            if match:
                item_id, value, comment = match.groups()
                result[item_id] = [item_id, value, "no" if value == "BLOCKED" else "yes", comment or "", ""]
        elif kind == "legacy":
            parts = line.strip().split("|")
            if len(parts) >= 3 and parts[0].isdigit() and parts[2] in {"yes", "no", "ask"}:
                result[parts[0]] = parts
    return result


def _plugin_candidate(path: Path) -> bool:
    """Read the managed attribute name; the loader performs the authoritative check."""
    # ponytail: a helper can contain this name too; the loader validates attributes at launch.
    try:
        with path.open("rb") as stream:
            if not path.stat().st_size:
                return False
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                return data.find(b"BepInPlugin\0") != -1
    except (OSError, ValueError):
        return False


def _payload(path: Path) -> bool:
    return path.name != "__folder_managed_by_vortex" and not path.name.startswith("vortex.deployment")


def _row(key, paths, game, metadata, owners, category="Plugin", workshop_id=None, fallback=None, outdated=()):
    paths = sorted(set(paths), key=lambda path: str(path).casefold())
    dlls = [path for path in paths if _dll(path)]
    name = fallback or (Path(_active_name(dlls[0].name)).stem if dlls else key)
    sources = {owners.get(_active_name(path.relative_to(game).as_posix()).casefold()) for path in paths}
    sources.discard(None)
    source = "Steam Workshop" if workshop_id else "Vortex" if sources else "Manual"
    row = {"id": f"workshop:{workshop_id}" if workshop_id else key, "name": name, "version": "Unknown",
           "category": category, "source": source, "enabled": any(path.suffix.lower() == ".dll" for path in dlls),
           "paths": [path.relative_to(game).as_posix() for path in paths], "warnings": [],
           "can_toggle": bool(dlls), "status": "Installed", "version_source": "unknown"}
    if workshop_id:
        row["workshop_id"] = workshop_id
    for candidate in [name] + [Path(_active_name(path.name)).stem for path in dlls]:
        if _normal(candidate) in metadata:
            row["name"], row["version"], row["version_source"] = metadata[_normal(candidate)]
            break
    if sources:
        row["vortex_sources"] = sorted(sources)
        row["warnings"].append("Vortex can overwrite GK2MT changes when it deploys. Avoid deploying from both managers at once.")
        if len(sources) == 1:
            match = re.match(r"^(.+) (\d+) (\S+) \d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z \S+$", next(iter(sources)))
            if match:
                row["nexus_mod_id"] = int(match.group(2))
                row["package_version"] = match.group(3)
                row["package_version_stale"] = any(
                    _active_name(path.relative_to(game).as_posix()).casefold() in outdated for path in dlls)
                if row["package_version_stale"]:
                    row["warnings"].append("Vortex's recorded version predates changed plugin files; using the local detected version for update checks.")
                elif row["version"] == "Unknown":
                    row["version"], row["version_source"] = match.group(3), "Vortex deployment record"
    if dlls and any(path.name.lower().endswith(DISABLED) for path in dlls) and row["enabled"]:
        row["warnings"].append("Only some DLLs in this mod are enabled; toggle applies to the whole group.")
    if not row["enabled"]:
        row["status"] = "Disabled" if dlls else "Not deployed"
    return row


def scan(game: Path, workshop: Path) -> list[dict]:
    """Inventory physical game files plus downloaded Workshop items. Never executes a mod."""
    game, workshop = Path(game).absolute(), Path(workshop).absolute()
    if game.exists() and not _safe(game, game):
        raise ValueError("The game folder is a link or junction. Select its physical location.")
    loader = loader_info(game)
    outdated = set()
    metadata, owners, trust = _metadata(game), _vortex(game, outdated), _trust(game, loader["kind"])
    rows, staged, regular = [], {}, []
    for area, category in (("plugins", "Plugin"), ("patchers", "Patcher")):
        base = game / "BepInEx" / area
        if not _safe(base, game):
            continue
        files = [path for path in _files(base) if _payload(path)]
        groups = {}
        for path in files:
            relative = path.relative_to(base)
            if relative.parts[0] == "_Workshop":
                if len(relative.parts) >= 3 and relative.parts[1].isdigit():
                    staged.setdefault(relative.parts[1], []).append(path)
                continue
            # Installer copies and BepInEx's private libraries are not standalone mods.
            if any(part.casefold() in {"installer", "core", "distribution", "_sync"} for part in relative.parts[:-1]):
                continue
            key = _active_name(relative.parts[0])
            if len(relative.parts) > 1 or _dll(path):
                groups.setdefault(key, []).append(path)
        for key, paths in list(groups.items()):
            if key.lower().endswith(".dll"):
                companion = next((name for name in groups if name.casefold() == key[:-4].casefold()), None)
                if companion and not any(_dll(path) for path in groups[companion]):
                    paths += groups.pop(companion)
        for key, paths in groups.items():
            if not any(_dll(path) for path in paths):
                continue
            dll_sources = {owners.get(_active_name(path.relative_to(game).as_posix()).casefold()) for path in paths if _dll(path)} - {None}
            # Include shared localization files belonging to this package, not its neighbours.
            paths += [path for path in files if not _dll(path) and owners.get(path.relative_to(game).as_posix().casefold()) in dll_sources]
            paths = [path for path in paths if not owners.get(path.relative_to(game).as_posix().casefold())
                     or owners.get(path.relative_to(game).as_posix().casefold()) in dll_sources]
            regular.append(_row(f"{area}:{key.casefold()}", paths, game, metadata, owners, category, outdated=outdated))
    items = {}
    if workshop.is_dir() and not _linked(workshop):
        items = {path.name: path for path in workshop.iterdir() if path.is_dir() and path.name.isdigit() and not _linked(path)}
    for item_id in sorted(set(items) | set(staged) | set(trust)):
        item = items.get(item_id)
        payload = [path for path in _files(item) if _payload(path)] if item else []
        dlls = [path for path in payload if _dll(path) and not any(part.casefold() in
                {"installer", "core", "loader", "_sync"} for part in path.relative_to(item).parts[:-1])]
        parts = trust.get(item_id, [])
        fallback = next((path.stem for path in dlls if not path.stem.endswith(".Core")), None)
        fallback = fallback or (parts[3] if len(parts) > 3 else f"Workshop {item_id}")
        paths = list(staged.get(item_id, []))
        category = "Workshop plugin"
        supported = bool(item and any(path.is_relative_to(item / "BepInEx/plugins") for path in dlls))
        if loader["kind"] == "workshop":
            supported = any(_plugin_candidate(path) for path in dlls)
        game_folder = bool(loader["kind"] == "legacy" and item and ((item / "CopyToGameFolder").is_dir() or
                                    (item / "GraveyardKeeper2_Data").is_dir() or (item / "Languages").is_dir()))
        if game_folder:
            category, supported = "Game-folder mod", True
            manifest = game / "BepInEx/config/GK2_WorkshopLoader.backup" / item_id / "manifest.txt"
            if _safe(manifest, game):
                for line in _text(manifest).splitlines():
                    rel = line.split("|")[0].replace("\\", "/")
                    path = game / rel
                    if not line.startswith("#") and "|" in line and _safe(path, game) and path.is_file():
                        paths.append(path)
        # The auto-loader is a patcher copied once, not a second plugin under its Workshop item.
        patcher_names = {path.name.casefold() for path in dlls if item and path.is_relative_to(item / "BepInEx/patchers")}
        associated = [row for row in regular if row["category"] == "Patcher" and any(
            Path(_active_name(path)).name.casefold() in patcher_names for path in row["paths"])]
        if associated:
            for row in associated:
                row.update(source="Steam Workshop", workshop_id=item_id)
                row["warnings"].append("Disabling this patcher also stops its Workshop loader on the next launch.")
            continue
        row = _row(f"workshop:{item_id}", paths, game, metadata, owners, category, item_id, fallback)
        decision = parts[2].casefold() if len(parts) > 2 else "ask"
        row["trust_state"] = decision
        row["loader_kind"] = loader["kind"]
        row["can_toggle"] = loader["kind"] in {"workshop", "legacy"} and supported
        row["workshop_paths"] = [path.relative_to(workshop).as_posix() for path in payload]
        if decision == "no":
            row.update(enabled=False, status="Blocked · applies on next launch")
        elif decision == "yes" and (paths or game_folder):
            row.update(enabled=True, status="Approved" if not game_folder else "Approved · loaded from Workshop")
        elif not paths:
            row.update(enabled=False, status="Not deployed")
        if decision == "ask" and parts:
            row["status"] = "Approval required on next launch"
        if loader["kind"] == "workshop":
            if not supported:
                row.update(enabled=False, status="Skipped · no BepInEx plugin")
            elif decision == "ask":
                row.update(enabled=False, status="Approval required on next launch")
            elif decision == "yes":
                row.update(enabled=bool(paths), status="Approved · deployed" if paths else "Approved · deploys on next launch")
        elif loader["kind"] == "conflict":
            row["status"] = "Workshop loader conflict"
        if row["can_toggle"]:
            row["warnings"].append("Workshop changes take effect through the auto-loader on the next game launch.")
        elif loader["kind"] == "conflict":
            row["warnings"].append("Disable one Workshop loader before changing approvals; the loaders use incompatible trust formats.")
        elif loader["kind"] == "workshop":
            row["warnings"].append("This loader handles BepInEx plugins only. Items without a plugin are skipped.")
        else:
            row["warnings"].append("Enable a compatible Workshop loader or install this item's plugin manually. The downloaded layout may not be supported by the selected loader.")
        if not item:
            row["warnings"].append("Workshop download is missing; Steam must download it again.")
        if row["version"] == "Unknown":
            row["warnings"].append("No reliable local version metadata found.")
        rows.append(row)
    return sorted(regular + rows, key=lambda row: (row["name"].casefold(), row["id"]))


def _atomic_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".gk2mt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def toggle(game: Path, workshop: Path, row: dict, enabled: bool) -> dict:
    """Resolve the ID again, then rename DLLs or change the loader's consent record."""
    if not isinstance(enabled, bool):
        raise ValueError("Enabled must be true or false.")
    game, workshop = Path(game).absolute(), Path(workshop).absolute()
    current = next((entry for entry in scan(game, workshop) if entry["id"] == row.get("id")), None)
    if current is None:
        raise ValueError("This mod is no longer installed. Refresh the inventory.")
    if not current["can_toggle"]:
        raise ValueError("This mod cannot be switched safely here. Install its plugin manually or use the Workshop loader's approval dialog.")
    if current["id"].startswith("workshop:"):
        path = game / TRUST
        if not _safe(path, game):
            raise ValueError("Workshop trust path contains a link or leaves the game folder.")
        item_id = current["workshop_id"]
        if current["loader_kind"] == "workshop":
            # The new loader has no 'ask' token: remove BLOCKED to ask again.
            # Never create an approval hash or translate a legacy approval.
            if enabled and current["trust_state"] == "yes":
                return current
            lines = [line for line in _text(path).splitlines()
                     if not re.match(r"^\s*" + re.escape(item_id) + r"\s*=", line)]
            if not enabled:
                lines.append(f"{item_id} = BLOCKED # GK2MT: disabled on next launch")
        else:
            previous = _trust(game, "legacy").get(item_id, [item_id, "", "ask", current["name"], ""])
            previous += [""] * (5 - len(previous))
            # Preserve the approved hash: an updated download must still ask for consent.
            previous[2] = "yes" if enabled and re.fullmatch(r"[0-9a-fA-F]{64}", previous[1]) else "ask" if enabled else "no"
            previous[4] = "GK2MT: enable on next launch" if enabled else "GK2MT: disabled on next launch"
            lines = [line for line in _text(path).splitlines() if line.strip().split("|")[0] != item_id]
            lines.append("|".join(part.replace("\r", " ").replace("\n", " ") for part in previous))
        _atomic_text(path, "\n".join(lines) + "\n")
    else:
        changes = []
        for relative in current["paths"]:
            source = game / relative
            if not _dll(source):
                continue
            is_disabled = source.name.lower().endswith(DISABLED)
            if enabled == (not is_disabled):
                continue
            target = Path(str(source)[:-len(DISABLED)] if enabled else str(source) + DISABLED)
            if not _safe(source, game) or not _safe(target, game):
                raise ValueError("A mod path is linked or outside the game folder.")
            if not source.is_file() or target.exists():
                raise ValueError(f"Cannot switch {source.name}: source missing or destination already exists.")
            changes.append((source, target))
        done = []
        try:
            for source, target in changes:
                source.rename(target)
                done.append((source, target))
        except OSError:
            for source, target in reversed(done):
                target.rename(source)
            raise
    return next(entry for entry in scan(game, workshop) if entry["id"] == current["id"])
