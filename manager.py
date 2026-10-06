"""Local package staging and reversible file deployment. Standard library only."""
import hashlib
import copy
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path, default):
    return json.loads(path.read_text('utf-8')) if path.exists() else default


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.gk2mt-json-', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def safe_path(root, relative):
    raw = str(relative).replace('\\', '/')
    parts = PurePosixPath(raw).parts
    if not parts or raw.startswith('/') or any(p in ('.', '..') or ':' in p or p.endswith((' ', '.')) or
            re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', p, re.I) for p in parts):
        raise ValueError('Unsafe package path: ' + raw)
    root = Path(root).resolve()
    target = root.joinpath(*parts)
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked destination is not safe: ' + str(parent))
    if not target.resolve().is_relative_to(root):
        raise ValueError('Path leaves target directory.')
    return target


def safe_extract(archive, destination):
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        if len(members) > 20000 or sum(i.file_size for i in members) > 2 * 1024**3:
            raise ValueError('Archive exceeds the 2 GB / 20,000 file limit.')
        seen = set()
        for item in members:
            target = safe_path(destination, item.filename)
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Archive links are not supported.')
            key = str(target).casefold()
            if key in seen:
                raise ValueError('Duplicate archive path: ' + item.filename)
            seen.add(key)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)


def ensure_game_stopped():
    if os.name == 'nt':
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq GraveyardKeeper2.exe', '/FO', 'CSV', '/NH'],
                                capture_output=True, text=True, timeout=15, creationflags=0x08000000, check=True)
        if 'graveyardkeeper2.exe' in result.stdout.lower():
            raise ValueError('Close Graveyard Keeper 2 before changing mods or syncing.')


def copy_atomic(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.gk2mt-', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)  # Never write through Vortex hardlinks.
    finally:
        temporary.unlink(missing_ok=True)


def _settings_path(relative):
    relative = relative.replace('\\', '/')
    return relative.casefold().startswith('bepinex/config/') or PurePosixPath(relative).suffix.casefold() in ('.cfg', '.ini')


class Manager:
    def __init__(self, data, game):
        self.data, self.game = Path(data), Path(game)
        self.data.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data / 'packages.json'
        self.state = read_json(self.state_path, {'packages': [], 'rules': [], 'deployed': {}, 'baseline': {}})
        if self.state.get('game') and Path(self.state['game']).resolve() != self.game.resolve():
            raise ValueError('Package library belongs to a different game folder.')

    def payload_fingerprint(self, package):
        """Compare mapped bytes, so re-zipping/renaming an archive is still a repeat import."""
        return hashlib.sha256(json.dumps({relative.casefold(): digest(safe_path(Path(package['folder']), relative))
                                        for relative in sorted(package['paths'], key=str.casefold)},
                                       sort_keys=True).encode()).hexdigest()

    def identical_package(self, package):
        fingerprint = package.get('payload_sha256') or self.payload_fingerprint(package)
        for installed in self.state['packages']:
            if package.get('archive_sha256') and package['archive_sha256'] == installed.get('archive_sha256'):
                return installed
            try:
                known = installed.get('payload_sha256') or self.payload_fingerprint(installed)
            except (OSError, ValueError):
                continue  # A damaged cached payload must not be silently accepted as an identical import.
            if known == fingerprint:
                return installed
        return None

    def stage(self, archive):
        archive = Path(archive)
        if archive.suffix.lower() != '.zip' or not archive.is_file():
            raise ValueError('Choose an existing ZIP file. Extract RAR/7z archives with their original tool first.')
        token = uuid.uuid4().hex
        folder = self.data / 'staging' / token
        unpacked = folder / 'raw'
        safe_extract(archive, unpacked)
        files = [p for p in unpacked.rglob('*') if p.is_file()]
        roots = [p for p in unpacked.rglob('*') if p.is_dir() and p.name.lower() == 'bepinex']
        if len(roots) > 1:
            raise ValueError('Archive has multiple BepInEx layouts. Choose a single mod variant first.')
        mapped = {}
        if roots:
            root = roots[0]
            if any(p.lower() in ('installer', 'copytogamefolder') for p in root.relative_to(unpacked).parts[:-1]):
                raise ValueError('This package has a custom installer. Use its documented installer.')
            for path in files:
                if path.is_relative_to(root):
                    relative = 'BepInEx/' + path.relative_to(root).as_posix()
                    mapped[relative] = path
                elif path.parent == root.parent and path.name.lower() in ('winhttp.dll', 'doorstop_config.ini', '.doorstop_version'):
                    mapped[path.name] = path
        else:
            if any(p.lower() in ('copytogamefolder', 'managed', 'fomod', 'installer') for f in files for p in f.relative_to(unpacked).parts):
                raise ValueError('This mod needs a custom installation. Use its documented installer.')
            base = unpacked
            children = list(base.iterdir())
            if len(children) == 1 and children[0].is_dir() and any(
                    (children[0] / area).is_dir() for area in ('plugins', 'patchers')):
                base = children[0]
            if any((base / area).is_dir() for area in ('plugins', 'patchers')):
                mapped = {'BepInEx/' + p.relative_to(base).as_posix(): p for p in files
                          if p.relative_to(base).parts[0] in ('plugins', 'patchers', 'config')}
            else:
                # Bare packages retain their directory layout for relative asset lookups.
                mapped = {'BepInEx/plugins/' + p.relative_to(unpacked).as_posix(): p for p in files}
        if any(p.casefold() == 'bepinex/core/bepinex.dll' for p in mapped):
            raise ValueError('Use Locations & setup → Use downloaded ZIP for the BepInEx foundation.')
        if not any(p.lower().endswith('.dll') for p in mapped):
            raise ValueError('No DLL mod payload found in this ZIP.')
        reserved = {'vortex.deployment', '__folder_managed_by_vortex'}
        for relative, src in mapped.items():
            if any(marker in relative.lower() for marker in reserved) or relative.endswith('.gk2mt-disabled'):
                raise ValueError('Archive contains manager-owned files.')
            destination = safe_path(folder / 'payload', relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, destination)
        package = {'id': token, 'name': archive.stem, 'version': 'Unknown', 'category': 'Uncategorized',
                   'enabled': True, 'source': 'GK2MT', 'paths': sorted(mapped), 'folder': str(folder / 'payload'),
                   'archive_sha256': digest(archive)}
        package['payload_sha256'] = self.payload_fingerprint(package)
        save_json(folder / 'package.json', package)
        conflicts = [p for p in mapped if safe_path(self.game, p).exists()]
        existing = self.identical_package(package)
        return {'token': token, 'name': package['name'], 'files': len(mapped), 'conflicts': [] if existing else conflicts,
                'status': 'already_installed' if existing else 'ready',
                'existing': {key: existing[key] for key in ('id', 'name', 'enabled')} if existing else None,
                'validation': {'kind': 'BepInEx mod ZIP',
                               'message': 'Plugin or patcher DLL payload found. Installation will not run the mod.'}}

    def install(self, token, metadata=None):
        if not re.fullmatch('[a-f0-9]{32}', token):
            raise ValueError('Invalid staging token.')
        package = read_json(self.data / 'staging' / token / 'package.json', None)
        if not package:
            raise ValueError('Staged package is missing.')
        existing = self.identical_package(package)
        if existing:
            return dict(existing, import_status='already_installed')
        for key in ('name', 'version', 'category', 'nexus_mod_id', 'file_id'):
            if metadata and metadata.get(key):
                package[key] = metadata[key]
        previous = copy.deepcopy(self.state)
        try:
            self.state['packages'].append(package)
            self.deploy()
        except Exception:
            self.state = previous
            raise
        return package

    def update(self, token, row, metadata=None, dry_run=False):
        """Replace one release, retaining its identity and backing up adopted files."""
        if not re.fullmatch('[a-f0-9]{32}', token):
            raise ValueError('Invalid staging token.')
        incoming = read_json(self.data / 'staging' / token / 'package.json', None)
        if not incoming or any(p['id'] == token for p in self.state['packages']):
            raise ValueError('Choose a new staged update ZIP.')
        if row.get('workshop_id') or row.get('source') == 'Steam Workshop':
            raise ValueError('Steam manages Workshop updates; use Steam Downloads.')
        managed = next((p for p in self.state['packages'] if p['id'] == row.get('id')), None)
        if row.get('source') == 'GK2MT' and managed is None:
            raise ValueError('This package is no longer installed. Refresh the inventory.')
        if not managed and row.get('source') not in ('Manual', 'Vortex'):
            raise ValueError('Choose an installed manual, Vortex, or GK2MT mod to update.')
        previous = copy.deepcopy(self.state)
        old = managed or row
        disabled = '.gk2mt-disabled'
        active = lambda p: p.removesuffix(disabled)
        config = _settings_path
        old_paths = list(old.get('paths', []))
        old_files = [p for p in old_paths if not config(p)]
        old_dlls = [active(p) for p in old_files if active(p).lower().endswith('.dll')]
        new_dlls = [p for p in incoming['paths'] if p.lower().endswith('.dll') and not config(p)]
        allowed = lambda p: p.casefold().startswith(('bepinex/plugins/', 'bepinex/patchers/', 'bepinex/config/')) and not any(
            part.casefold() in ('_workshop', 'core', 'distribution', '_sync', 'installer')
            for part in PurePosixPath(p.replace('\\', '/')).parts[2:-1])
        if not old_dlls or not new_dlls or any(not allowed(active(p)) for p in old_paths + incoming['paths']):
            raise ValueError('This update needs a custom installer. Only plugin or patcher releases can be updated here.')
        if managed and not ({p.casefold() for p in old_dlls} & {p.casefold() for p in new_dlls}):
            raise ValueError('The update DLL does not match this installed mod; choose the correct release.')
        others = {active(p).casefold() for pkg in self.state['packages'] if pkg is not managed
                  for field in ('paths', 'retired_paths', 'adopted_paths') for p in pkg.get(field, [])}
        old_keys = {active(p).casefold() for p in old_files}
        if not managed and (len(row.get('vortex_sources', [])) > 1 or old_keys & others):
            raise ValueError('This mod shares ownership with another package. Update it in its original manager.')
        mapped = {p: p for p in incoming['paths']}
        if not managed:
            if any(not safe_path(self.game, p).is_file() for p in old_paths):
                raise ValueError('Installed mod files changed. Refresh the inventory before updating.')
            if any(p.endswith(disabled) == bool(old.get('enabled')) for p in old_files if active(p).lower().endswith('.dll')):
                raise ValueError('This mod has mixed enabled DLLs. Use its original manager to resolve them first.')
            def group(paths):
                groups = {tuple(PurePosixPath(p).parts[:2 if len(PurePosixPath(p).parts) == 3 else 3]) for p in paths}
                if len(groups) != 1:
                    raise ValueError('Update has multiple plugin groups; choose a single mod release.')
                return '/'.join(groups.pop())
            old_root, new_root = group(old_dlls), group(new_dlls)
            old_names = {PurePosixPath(p).name.casefold() for p in old_dlls}
            if not old_names & {PurePosixPath(p).name.casefold() for p in new_dlls}:
                raise ValueError('The update DLL does not match this installed mod; choose the correct release.')
            if old_root.split('/')[1].casefold() != new_root.split('/')[1].casefold():
                raise ValueError('A plugin cannot be replaced with a patcher automatically.')
            if len(new_root.split('/')) == 2 and len(new_dlls) > 1:
                raise ValueError('Update contains several loose DLLs; choose a single mod release.')
            for source in incoming['paths']:
                if source.casefold().startswith(new_root.casefold() + '/'):
                    mapped[source] = old_root + source[len(new_root):]
                elif not config(source) and source.casefold() not in old_keys:
                    raise ValueError('Update includes files outside this mod. Use its documented installer.')
        # Existing settings become user-owned; later deployments must not reset them.
        old_configs = {p.casefold() for field in ('paths', 'retired_paths', 'adopted_paths')
                       for p in old.get(field, []) if config(p)}
        preserved_configs = {p.casefold() for p in old_paths + list(mapped.values()) + list(old_configs)
                             if config(p) and safe_path(self.game, p).exists()}
        if (preserved_configs | old_configs) & others:
            raise ValueError('Another imported package owns this configuration. Resolve its rule first.')
        mapped = {src: dst for src, dst in mapped.items() if dst.casefold() not in preserved_configs}
        if len({p.casefold() for p in mapped.values()}) != len(mapped):
            raise ValueError('Update files map to the same installed path.')
        for destination in mapped.values():
            key = destination.casefold()
            if key not in old_keys and (key in others or safe_path(self.game, destination).exists()
                                      or safe_path(self.game, destination + disabled).exists()):
                raise ValueError('Update would overwrite another mod: ' + destination)
        if dry_run:
            return {'files': len(mapped), 'conflicts': [p for p in old_files if safe_path(self.game, p).is_file()],
                    'warnings': ['Existing configuration files will be preserved.'] if preserved_configs else []}
        folder = self.data / 'staging' / token / 'update-payload'
        for source, destination in mapped.items():
            copy_atomic(safe_path(Path(incoming['folder']), source), safe_path(folder, destination))
        package = copy.deepcopy(managed) if managed else {k: old[k] for k in
            ('name', 'version', 'category', 'nexus_mod_id', 'file_id') if k in old}
        package.update(id=managed['id'] if managed else token, source='GK2MT', enabled=bool(old.get('enabled')),
                       folder=str(folder), paths=sorted(mapped.values()), archive_sha256=incoming.get('archive_sha256'),
                       payload_sha256=incoming.get('payload_sha256') or self.payload_fingerprint(incoming))
        for key in ('name', 'version', 'category', 'nexus_mod_id', 'file_id'):
            if metadata and metadata.get(key) is not None:
                package[key] = metadata[key]
        if 'package_version' in package:
            package['package_version'] = package.get('version', 'Unknown')
        new_keys = {p.casefold() for p in package['paths']}
        package['retired_paths'] = sorted({p for p in package.get('retired_paths', []) if not config(p)} |
                                         {p for p in old_files if p.casefold() not in new_keys})
        if 'adopted_paths' in package:
            package['adopted_paths'] = [p for p in package['adopted_paths'] if not config(p)]
        if not managed:
            package['adopted_paths'] = old_files
            package['previous_source'] = old['source']
        try:
            for key in preserved_configs | old_configs:
                self.state['deployed'].pop(key, None)
                self.state['baseline'].pop(key, None)
            if managed:
                self.state['packages'][self.state['packages'].index(managed)] = package
            else:
                for relative in old_files:
                    self.state['deployed'][relative.casefold()] = {'path': relative, 'hash': digest(safe_path(self.game, relative))}
                self.state['packages'].append(package)
            self.deploy()
        except BaseException:
            self.state = previous
            raise
        return package

    def uninstall(self, row, dry_run=False, expected=None):
        """Remove known plugin files, retaining settings and a persistent recovery backup."""
        import inventory
        if row.get('workshop_id') or row.get('source') in ('Steam', 'Steam Workshop'):
            raise ValueError('Steam manages this mod. Unsubscribe from its Workshop page in Steam.')
        if row.get('nexus_mod_id') == 48:
            raise ValueError('The BepInEx foundation cannot be uninstalled as a mod.')
        if not inventory._safe(self.game, self.game) or not inventory._safe(self.state_path, self.data):
            raise ValueError('The game or package library contains a linked path.')
        if not (self.game / 'GraveyardKeeper2.exe').is_file():
            raise ValueError('The game folder must contain GraveyardKeeper2.exe.')
        previous = copy.deepcopy(self.state)
        if read_json(self.state_path, {'packages': [], 'rules': [], 'deployed': {}, 'baseline': {}}) != previous:
            raise ValueError('The package library changed. Refresh the inventory before uninstalling.')
        package = next((p for p in previous['packages'] if p['id'] == row.get('id')), None)
        if package and package.get('nexus_mod_id') == 48:
            raise ValueError('The BepInEx foundation cannot be uninstalled as a mod.')
        if not package:
            current = next((p for p in inventory.scan(self.game, self.data / 'no-workshop')
                            if p['id'] == row.get('id')), None)
            if not current or current['source'] not in ('Manual', 'Vortex') or (
                    {p.casefold() for p in current['paths']} != {p.casefold() for p in row.get('paths', [])}):
                raise ValueError('Installed mod files changed. Refresh the inventory before uninstalling.')
            if len(current.get('vortex_sources', [])) > 1:
                raise ValueError('This folder has files from multiple packages. GK2MT cannot safely uninstall it as one mod.')
            package = current
        managed = package in previous['packages']
        paths = {p.casefold(): p for field in ('paths', 'retired_paths', 'adopted_paths')
                 for p in package.get(field, [])}
        disabled = '.gk2mt-disabled'
        active = lambda p: p.removesuffix(disabled)
        config = _settings_path
        if not paths or not any(active(p).casefold().endswith('.dll') and
                p.casefold().startswith(('bepinex/plugins/', 'bepinex/patchers/')) for p in paths.values()):
            raise ValueError('No installed plugin or patcher payload was found.')
        for relative in paths.values():
            parts = PurePosixPath(active(relative).replace('\\', '/')).parts
            if len(parts) < 3 or parts[0].casefold() != 'bepinex' or parts[1].casefold() not in (
                    'plugins', 'patchers', 'config') or any(p.casefold() in ('_workshop', 'core', 'distribution', '_sync', 'installer')
                    for p in parts[2:-1]):
                raise ValueError('Only plugin or patcher files can be uninstalled: ' + relative)
            if not inventory._safe(safe_path(self.game, relative), self.game):
                raise ValueError('A mod path contains a link: ' + relative)
        remaining = [p for p in previous['packages'] if not managed or p['id'] != package['id']]
        other_paths = {p.casefold() for owner in remaining for field in ('paths', 'retired_paths', 'adopted_paths')
                       for p in owner.get(field, [])}
        if not managed and ({active(p) for p in paths} &
                            {active(p) for p in other_paths | previous['deployed'].keys()}):
            raise ValueError('This mod shares ownership with an imported package. Uninstall its package instead.')
        next_state = copy.deepcopy(previous)
        if managed:
            next_state['packages'] = remaining
            next_state['rules'] = [r for r in previous['rules'] if package['id'] not in (r['before'], r['after'])]
            # Removing a node must retain ordering dependencies that passed through it.
            parents = {r['before'] for r in previous['rules'] if r['after'] == package['id']}
            children = {r['after'] for r in previous['rules'] if r['before'] == package['id']}
            for before in sorted(parents):
                for after in sorted(children):
                    rule = {'before': before, 'after': after}
                    if rule not in next_state['rules']:
                        next_state['rules'].append(rule)
            # Preserve existing conflict winners when removing a rule changes tie order.
            old_order = [p for p in self.order() if p['id'] != package['id']]
            new_order = self.order(next_state['rules'], remaining)
            def winners(ordered):
                return {p.casefold(): owner['id'] for owner in ordered if owner['enabled'] for p in owner['paths']}
            old_winners, new_winners = winners(old_order), winners(new_order)
            for key, winner in old_winners.items():
                if new_winners.get(key) == winner:
                    continue
                for owner in old_order:
                    if owner['enabled'] and owner['id'] != winner and key in {p.casefold() for p in owner['paths']}:
                        rule = {'before': owner['id'], 'after': winner}
                        if rule not in next_state['rules']:
                            next_state['rules'].append(rule)
        desired = {}
        for owner in self.order(next_state['rules'], remaining):
            if owner['enabled'] and (not managed or owner['id'] != package['id']):
                for relative in owner['paths']:
                    if relative.casefold() in paths:
                        desired[relative.casefold()] = (relative, safe_path(Path(owner['folder']), relative))
        removed_by_other = {p.casefold() for owner in remaining for field in ('retired_paths', 'adopted_paths')
                            for p in owner.get(field, [])}
        obsolete = {p.casefold() for field in ('retired_paths', 'adopted_paths') for p in package.get(field, [])}
        changed, restored, removed, preserved, hashes, sources = {}, [], [], [], {}, {}
        baseline_sources, baseline_hashes, replacement_hashes, installed_hashes, final_sources = {}, {}, {}, {}, {}
        for key, relative in paths.items():
            target = safe_path(self.game, relative)
            if target.exists() and not target.is_file():
                raise ValueError('A mod file became a directory: ' + relative)
            current_hash = digest(target) if target.is_file() else None
            installed_hashes[relative] = current_hash
            if config(relative):
                if target.is_file():
                    preserved.append(relative)
                if key not in other_paths:
                    next_state['deployed'].pop(key, None)
                    next_state['baseline'].pop(key, None)
                continue
            baseline = previous['baseline'].get(key)
            if baseline:
                original = Path(baseline)
                if not inventory._safe(original, self.data) or not original.is_file():
                    raise ValueError('The original file backup is missing or linked: ' + relative)
                baseline_sources[key] = original
                baseline_hashes[key] = digest(original)
            record = previous['deployed'].get(key)
            expected_hash = record['hash'] if record else digest(baseline_sources[key]) if key in baseline_sources else None
            if managed and current_hash != expected_hash:
                raise ValueError('Another manager changed ' + relative + '. Refresh or restore its GK2MT version first.')
            if not managed and current_hash is None:
                raise ValueError('Installed mod files changed. Refresh the inventory before uninstalling.')
            if key in desired:
                source = desired[key][1]
                if not inventory._safe(source, self.data) or not source.is_file():
                    raise ValueError('A remaining package payload is missing or linked: ' + relative)
                next_state['deployed'][key] = {'path': relative, 'hash': digest(source)}
            else:
                source = None if not managed or key in obsolete or key in removed_by_other else baseline_sources.get(key)
                if key in removed_by_other:
                    next_state['deployed'][key] = {'path': relative, 'hash': None}
                else:
                    next_state['deployed'].pop(key, None)
            if key not in other_paths:
                next_state['baseline'].pop(key, None)
            elif key in obsolete:
                # Other owners must not resurrect this mod's adopted or retired release later.
                next_state['baseline'][key] = None
            source_hash = digest(source) if source else None
            replacement_hashes[relative] = source_hash
            final_sources[relative] = source
            if current_hash != source_hash:
                changed[relative] = source
                hashes[relative] = current_hash
                sources[relative] = source_hash
                (restored if source else removed).append(relative)
        warnings = []
        if package.get('source') == 'Vortex' or package.get('previous_source') == 'Vortex':
            warnings.append('Vortex may restore this mod when it deploys. Remove or disable it in Vortex too.')
        result = {'id': package['id'], 'name': row.get('name', package['name']), 'files': sorted(changed),
                  'removed': sorted(removed), 'restored': sorted(restored), 'preserved': sorted(preserved), 'warnings': warnings,
                  '_signature': {'installed': installed_hashes, 'sources': replacement_hashes, 'baselines': baseline_hashes}}
        if dry_run:
            return result
        if expected is not None and result != expected:
            raise ValueError('The uninstall plan changed. Review the mod again before uninstalling.')
        ensure_game_stopped()
        backup = self.data / 'uninstall-backups' / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex)
        if not inventory._safe(backup, self.data):
            raise ValueError('The uninstall backup folder contains a linked path.')
        backup.mkdir(parents=True)
        originals = {}
        for relative in changed:
            target = safe_path(self.game, relative)
            saved = safe_path(backup / 'files', relative) if target.is_file() else None
            if saved:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
                if digest(saved) != hashes[relative]:
                    raise ValueError('A mod changed while its backup was copied; no files were uninstalled: ' + relative)
            originals[relative] = saved
        for key, source in baseline_sources.items():
            saved = backup / 'baseline' / hashlib.sha256(key.encode()).hexdigest()
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, saved)
            if digest(saved) != baseline_hashes[key]:
                raise ValueError('An original backup changed while being copied; no files were uninstalled.')
        state_existed = self.state_path.exists()
        if state_existed:
            state_bytes = self.state_path.read_bytes()
            if json.loads(state_bytes) != previous:
                raise ValueError('The package library changed while uninstall was prepared; no files were uninstalled.')
            shutil.copy2(self.state_path, backup / 'packages-before.json')
            if (backup / 'packages-before.json').read_bytes() != state_bytes:
                raise ValueError('The package library changed while being backed up; no files were uninstalled.')
        save_json(backup / 'recovery.json', {'game': str(self.game.resolve()), 'state': previous,
            'state_existed': state_existed, 'files': {p: b.relative_to(backup).as_posix() if b else None
                                                    for p, b in originals.items()}})
        for relative, reviewed_hash in installed_hashes.items():
            target = safe_path(self.game, relative)
            if (digest(target) if target.is_file() else None) != reviewed_hash:
                raise ValueError('A mod file changed during backup; no files were uninstalled: ' + relative)
        for relative, source in final_sources.items():
            if source and digest(source) != replacement_hashes[relative]:
                raise ValueError('A replacement file changed during backup; no files were uninstalled: ' + relative)
        if state_existed != self.state_path.exists() or (state_existed and self.state_path.read_bytes() != state_bytes):
            raise ValueError('The package library changed during backup; no files were uninstalled.')
        done = []
        try:
            for relative, source in changed.items():
                target = safe_path(self.game, relative)
                if (digest(target) if target.is_file() else None) != hashes[relative] or (
                        source and digest(source) != sources[relative]):
                    raise ValueError('A mod file changed during uninstall: ' + relative)
                if source:
                    copy_atomic(source, target)
                else:
                    target.unlink()
                done.append(relative)
            if managed:
                save_json(self.state_path, next_state)
                self.state = next_state
        except BaseException as error:
            failures = []
            for relative in reversed(done):
                try:
                    saved = originals[relative]
                    target = safe_path(self.game, relative)
                    if saved:
                        copy_atomic(saved, target)
                    else:
                        target.unlink(missing_ok=True)
                except Exception as rollback_error:
                    failures.append(relative + ': ' + str(rollback_error))
            try:
                if state_existed:
                    copy_atomic(backup / 'packages-before.json', self.state_path)
                else:
                    self.state_path.unlink(missing_ok=True)
            except Exception as rollback_error:
                failures.append('packages.json: ' + str(rollback_error))
            self.state = previous
            if failures:
                raise RuntimeError('Uninstall rollback was incomplete. Recovery files are at ' + str(backup) + ': '
                                   + '; '.join(failures)) from error
            raise
        return result | {'backup': str(backup)}

    def order(self, rules=None, packages=None):
        packages = self.state['packages'] if packages is None else packages
        ids = [p['id'] for p in packages]
        edges = {i: set() for i in ids}
        for rule in rules if rules is not None else self.state['rules']:
            a, b = rule['before'], rule['after']
            if a not in edges or b not in edges or a == b:
                raise ValueError('Rules must reference two different imported packages.')
            edges[b].add(a)
        result = []
        while edges:
            ready = next((i for i, parents in edges.items() if not parents), None)
            if ready is None:
                raise ValueError('These rules form a cycle. Remove a conflicting rule.')
            result.append(next(p for p in packages if p['id'] == ready))
            del edges[ready]
            for parents in edges.values():
                parents.discard(ready)
        return result

    def conflicts(self):
        paths = {}
        for package in self.order():
            if package['enabled']:
                for p in package['paths']:
                    paths.setdefault(p.casefold(), []).append(package['id'])
        return [{'path': p, 'packages': owners, 'winner': owners[-1]} for p, owners in paths.items() if len(owners) > 1]

    def deploy(self):
        ensure_game_stopped()
        if not (self.game / 'GraveyardKeeper2.exe').is_file():
            raise ValueError('The game folder must contain GraveyardKeeper2.exe.')
        desired = {}
        removed = {relative.casefold(): relative for package in self.state['packages']
                   for field in ('retired_paths', 'adopted_paths') for relative in package.get(field, [])}
        for package in self.order():
            if package['enabled']:
                for relative in package['paths']:
                    desired[relative.casefold()] = (relative, safe_path(Path(package['folder']), relative))
        previous = self.state['deployed']
        planned = {key: {'path': relative, 'hash': digest(source)}
                   for key, (relative, source) in desired.items()}
        planned.update({key: {'path': relative, 'hash': None} for key, relative in removed.items() if key not in desired})
        affected = {key: value[0] for key, value in desired.items()}
        affected.update({key: record['path'] for key, record in previous.items()})
        affected.update(removed)
        # Unchanged packages may contain runtime edits or recreated retired files.
        # Leave their bytes and recorded expectations alone during unrelated changes.
        affected = {key: relative for key, relative in affected.items() if previous.get(key) != planned.get(key)}
        for key in affected.keys() & previous.keys():
            record = previous[key]
            target = safe_path(self.game, record['path'])
            if (target.exists() if record['hash'] is None else not target.is_file() or digest(target) != record['hash']):
                raise ValueError('This file changed outside GK2MT: ' + record['path'] + '. Review or restore this mod before changing it; no files changed.')
        for key in affected.keys() - previous.keys():
            if key in self.state['baseline']:
                target = safe_path(self.game, affected[key])
                original = self.state['baseline'][key]
                if (original and (not target.is_file() or digest(target) != digest(original))) or (not original and target.exists()):
                    raise ValueError('Another manager changed ' + affected[key] + ' while disabled; no files changed.')
        temp = Path(tempfile.mkdtemp(prefix='gk2mt-rollback-', dir=self.data))
        cleanup = True
        try:
            originals = {}
            for key, relative in affected.items():
                target = safe_path(self.game, relative)
                backup = temp / str(len(originals))
                if target.exists():
                    shutil.copy2(target, backup)
                    originals[relative] = backup
                else:
                    originals[relative] = None
                if key not in self.state['baseline']:
                    saved = self.data / 'originals' / hashlib.sha256(key.encode()).hexdigest()
                    if target.exists():
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, saved)
                    self.state['baseline'][key] = str(saved) if target.exists() else None
            save_json(temp / 'recovery.json', {'game': str(self.game.resolve()),
                'state_path': str(self.state_path.resolve()), 'state_existed': self.state_path.exists(),
                'files': {relative: backup.name if backup else None for relative, backup in originals.items()}})
            if self.state_path.exists():
                shutil.copy2(self.state_path, temp / 'packages-before.json')
            try:
                for key, relative in affected.items():
                    target = safe_path(self.game, relative)
                    source = desired[key][1] if key in desired else None if key in removed else self.state['baseline'][key]
                    if source:
                        copy_atomic(Path(source), target)
                    elif target.exists():
                        target.unlink()
                self.state['deployed'] = planned
                self.state['game'] = str(self.game.resolve())
                save_json(self.state_path, self.state)
            except BaseException as error:
                failures = []
                for relative, backup in originals.items():
                    try:
                        target = safe_path(self.game, relative)
                        if backup:
                            copy_atomic(backup, target)
                        elif target.exists():
                            target.unlink()
                    except Exception as rollback_error:
                        failures.append(relative + ': ' + str(rollback_error))
                if failures:
                    cleanup = False
                    raise RuntimeError('Deployment failed and rollback was incomplete. Original files and recovery.json '
                                       'are preserved at ' + str(temp) + '. Restore them before making further changes. '
                                       + '; '.join(failures)) from error
                raise
        finally:
            if cleanup:
                shutil.rmtree(temp)

    def preserve_settings(self):
        """Release existing shared settings in memory; the caller's deploy saves ownership."""
        candidates = {p.casefold(): p for package in self.state['packages']
                      for field in ('paths', 'retired_paths', 'adopted_paths') for p in package.get(field, [])}
        for record in self.state['deployed'].values():
            candidates.setdefault(record['path'].casefold(), record['path'])
        for key in self.state['baseline']:
            candidates.setdefault(key, key)
        preserved = {key: path for key, path in candidates.items()
                     if _settings_path(path) and safe_path(self.game, path).is_file()}
        for package in self.state['packages']:
            for field in ('paths', 'retired_paths', 'adopted_paths'):
                if field in package:
                    package[field] = [p for p in package[field] if p.casefold() not in preserved]
        for key in preserved:
            self.state['deployed'].pop(key, None)
            self.state['baseline'].pop(key, None)
        return sorted(preserved.values())

    def set_enabled(self, identifier, enabled):
        previous = copy.deepcopy(self.state)
        try:
            package = next(p for p in self.state['packages'] if p['id'] == identifier)
            package['enabled'] = bool(enabled)
            self.deploy()
        except Exception:
            self.state = previous
            raise

    def set_rules(self, rules):
        self.order(rules)
        previous = copy.deepcopy(self.state)
        try:
            self.state['rules'] = rules
            self.deploy()
        except Exception:
            self.state = previous
            raise
