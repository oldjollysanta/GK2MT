"""GK2MT: a Windows desktop mod manager."""
import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

import inventory
import manager
import integrations
import deck
import deck_ssh
import nexus_panel
import nexus_credentials
import profiles

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('GK2MT_DATA', str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'GK2MT')))
WINDOW = None
LOCK = threading.Lock()
NEXUS_KEY = ''
NEXUS_ERROR = ''
NEXUS_SESSION = 0
UPDATES = None
UPDATE_ARCHIVES = {}
DOWNLOADS = {}
IMPORT_REVIEWS = {}
PREVIEW = None
UNINSTALL_PREVIEW = None
PROFILE_PREVIEW = None
DECK_SESSION = None
DECK_MESSAGE = 'Enter your Deck address to connect.'
DECK_DEFAULTS = {'host': '', 'user': 'deck', 'port': 22, 'key': '', 'game_path': '', 'workshop_path': ''}
BEPINEX_FILES = integrations.WORKSHOP_BEPINEX_FILES


def settings():
    saved = manager.read_json(DATA / 'settings.json', {})
    if not saved.get('game'):
        found = inventory.discover()
        saved.setdefault('game', found.get('game', ''))
        saved.setdefault('workshop', found.get('workshop', ''))
    saved.setdefault('import_folder', str(Path.home() / 'Downloads/GK2MT'))
    saved['quick_setup_completed'] = saved.get('quick_setup_completed') is True
    saved['workshop_enabled'] = saved.get('workshop_enabled', True) is True
    saved['deck'] = {key: saved.get('deck', {}).get(key, default) for key, default in DECK_DEFAULTS.items()}
    saved['deck_sync_options'] = deck.normalize_options(saved.get('deck_sync_options'), allow_empty=True)
    return saved


def setup_check(config, values=None):
    """Read-only field feedback for Quick Setup, including folders Steam hasn't created yet."""
    selected = dict(config)
    for field in ('game', 'workshop', 'import_folder'):
        if field in (values or {}):
            selected[field] = str(values[field]).strip().strip('"')
    game = Path(selected.get('game') or '')
    if (not selected.get('workshop') and selected.get('game')
            and game.parent.name.casefold() == 'common' and game.parent.parent.name.casefold() == 'steamapps'):
        selected['workshop'] = str(game.parent.parent / 'workshop/content/4358690')
    fields = {}
    for field in ('game', 'workshop', 'import_folder'):
        value = selected.get(field, '')
        path = Path(value or '')
        physical = bool(value) and inventory._safe(path.absolute(), Path(path.absolute().anchor))
        valid = physical and (path.is_dir() if field != 'game' else
                             path.is_dir() and (path / 'GraveyardKeeper2.exe').is_file()
                             and inventory._safe(path / 'GraveyardKeeper2.exe', path.absolute()))
        message = ('Game found.' if field == 'game' else 'Folder found.') if valid else (
            'Select the folder containing GraveyardKeeper2.exe.' if field == 'game' else
            'Optional. Choose a folder when needed.' if not value else
            'Steam will create this folder after your first Workshop download.' if field == 'workshop' and not path.exists() else
            'Optional. Create or choose this folder before bulk imports.' if field == 'import_folder' and not path.exists() else
            'Choose a physical folder, not a file or linked folder.')
        pending_workshop = field == 'workshop' and physical and not path.exists()
        fields[field] = {'path': value, 'status': 'ready' if valid else 'empty' if pending_workshop else 'attention' if value or field == 'game' else 'empty',
                         'valid': valid, 'message': message}
    missing = [name for name in BEPINEX_FILES if not fields['game']['valid']
               or not (game / name).is_file() or not inventory._safe(game / name, game.absolute())]
    # The integration check does not create manager folders or save any settings.
    workshop_status = integrations.workshop_loader_setup(game, Path(selected.get('workshop') or ''), DATA)
    return fields | {'game_found': fields['game']['valid'], 'workshop_found': fields['workshop']['valid'],
                     'import_folder_found': fields['import_folder']['valid'], 'loader_installed': not missing,
                     'loader_missing': missing, 'workshop_setup': workshop_status}


def restore_nexus():
    global NEXUS_KEY, NEXUS_ERROR, NEXUS_SESSION, UPDATES
    NEXUS_KEY = NEXUS_ERROR = ''
    NEXUS_SESSION += 1
    UPDATES = None
    try:
        NEXUS_KEY = nexus_credentials.load(DATA / 'nexus-key.bin')
    except RuntimeError:
        NEXUS_ERROR = 'The saved Nexus key could not be restored. Paste your key again or choose Forget key.'


def disconnect_deck(message='Disconnected. Select Connect to Deck to reconnect.'):
    global DECK_SESSION, DECK_MESSAGE, PREVIEW
    session, DECK_SESSION = DECK_SESSION, None
    PREVIEW = None
    DECK_MESSAGE = message
    if session:
        session.close()


def saved_deck_password(connection_settings):
    try:
        identity = list(deck_ssh._identity(connection_settings)[:3])
    except ValueError:
        return ''
    encrypted = nexus_credentials.load(DATA / 'deck-password.bin', label='Deck password')
    if not encrypted:
        return ''
    try:
        saved = json.loads(encrypted)
        if not isinstance(saved, dict) or not isinstance(saved.get('password'), str) or not saved['password']:
            raise ValueError('Invalid saved credential.')
        return saved['password'] if saved.get('identity') == identity else ''
    except (ValueError, TypeError):
        raise RuntimeError('The saved Deck password could not be read. Forget it and save it again.') from None


def remember_deck_address(config, value):
    connection_settings = deck._settings(value, require_paths=False)
    config['deck'] = connection_settings
    manager.save_json(DATA / 'settings.json', config)
    return connection_settings


def deck_connection(config):
    if DECK_SESSION and (not DECK_SESSION.alive or not DECK_SESSION.matches(config['deck'])):
        disconnect_deck('Connection ended or the address changed. Connect to the Deck again.')
    password_saved, password_error = False, ''
    try:
        password_saved = bool(saved_deck_password(config['deck']))
    except RuntimeError as error:
        password_error = str(error)
    return {'connected': DECK_SESSION is not None, 'host': config['deck']['host'],
            'user': config['deck']['user'], 'message': DECK_MESSAGE,
            'password_saved': password_saved, 'password_error': password_error}


def require_deck(config):
    if not deck_connection(config)['connected']:
        raise ValueError('Connect to this Deck first using its address and password.')
    return DECK_SESSION


def package_manager(config):
    game = Path(config['game'])
    scope = hashlib.sha256(str(game.resolve()).casefold().encode()).hexdigest()[:16]
    return manager.Manager(DATA / 'libraries' / scope, game)


def foundation_metadata(library):
    installed = manager.read_json(library.data / 'foundation.json', {})
    if not installed.get('installed_hashes'):
        return {}
    try:
        for relative, expected in installed['installed_hashes'].items():
            path = manager.safe_path(library.game, relative)
            if not path.exists() and path.suffix.lower() == '.dll':
                path = manager.safe_path(library.game, relative + inventory.DISABLED)
            elif not path.exists() and relative.endswith(inventory.DISABLED):
                path = manager.safe_path(library.game, relative.removesuffix(inventory.DISABLED))
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    return {}
    except (OSError, ValueError):
        return {}
    if installed.get('source') == 'GitHub':
        component = next((item for item in installed.get('components', [])
                          if item.get('name') == 'Configuration Manager'), {})
        if not component.get('version') or component.get('preserved'):
            return {}
        return {'nexus_mod_id': None, 'file_id': None, 'source': 'Manual',
                'setup_source': 'GitHub', 'foundation': True,
                'version': component['version'], 'package_version': component['version'],
                'version_source': 'Verified GitHub release'}
    return {key: installed.get(key) for key in ('nexus_mod_id', 'file_id', 'package_version')}


def record_foundation(config, result):
    if result.get('already_installed') or result.get('files') == 0:
        return
    try:
        manager.save_json(package_manager(config).data / 'foundation.json',
                          {key: result.get(key) for key in ('source', 'components', 'nexus_mod_id',
                                                           'file_id', 'package_version', 'installed_hashes')})
    except OSError as exc:
        warning = f"BepInEx was installed, but update tracking could not be saved: {exc}. Backup: {result['backup']}"
        result.setdefault('warnings', []).append(warning)
        result['message'] = warning


def foundation_overlap(library):
    for package in library.state['packages']:
        for field in ('paths', 'retired_paths', 'adopted_paths'):
            for path in package.get(field, []):
                path = path.removesuffix(inventory.DISABLED).casefold()
                if path in integrations.ROOT_FILES or path.startswith((
                        'bepinex/core/', 'bepinex/distribution/', 'bepinex/plugins/configurationmanager/')):
                    return f"BepInEx files belong to imported package '{package['name']}'. Restore or migrate that package before using foundation setup."
    return ''


def workshop_title_cache():
    try:
        cache = manager.read_json(DATA / 'workshop-titles.json', {})
        return cache if isinstance(cache, dict) and isinstance(cache.get('titles', {}), dict) else {}
    except (OSError, ValueError):
        return {}


def discovery_cache(library):
    try:
        saved = manager.read_json(library.data / 'nexus-discovery.json', {})
        return saved if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def installed_hashes(game, paths):
    """Fingerprint tracked files consistently when a DLL is renamed to disable it."""
    result = {}
    for relative in paths:
        relative = relative.removesuffix(inventory.DISABLED)
        path = manager.safe_path(game, relative)
        if not path.exists() and relative.lower().endswith('.dll'):
            path = manager.safe_path(game, relative + inventory.DISABLED)
        result[relative] = manager.digest(path) if path.is_file() else None
    return result


def remember_discovery(library, rows):
    saved = discovery_cache(library)
    discovered = dict(saved)
    for row in rows:
        if not row.get('nexus_mod_id') or row.get('source') != 'Vortex':
            continue
        previous = discovered.get(row['id'], {})
        entry = (dict(previous) if isinstance(previous, dict) and type(previous.get('nexus_mod_id')) is int
                 and previous['nexus_mod_id'] > 0 else {})
        entry.setdefault('nexus_mod_id', row['nexus_mod_id'])
        if (not entry.get('installed_hashes') and entry['nexus_mod_id'] == row['nexus_mod_id']
                and not row.get('package_version_stale') and integrations._known_version(row.get('package_version'))):
            try:
                paths = (update_signature(row, library.game)['files'] if row['nexus_mod_id'] == integrations.SETUP_MOD
                         else [path for path in row.get('paths', [])
                               if path.removesuffix(inventory.DISABLED).lower().endswith('.dll')])
                hashes = installed_hashes(library.game, paths)
            except (OSError, ValueError):
                hashes = {}  # Keep the Nexus link even if this mod's files cannot be read.
            if hashes:
                entry.update(package_version=row['package_version'], installed_hashes=hashes)
        discovered[row['id']] = entry
    if discovered != saved:
        manager.save_json(library.data / 'nexus-discovery.json', discovered)


def discovered_metadata(library, row, saved):
    entry = saved.get(row['id'], {})
    if not isinstance(entry, dict) or type(entry.get('nexus_mod_id')) is not int or entry['nexus_mod_id'] < 1:
        return {}
    result = {'nexus_mod_id': entry['nexus_mod_id']}
    if row.get('version_source') == 'Vortex deployment record':
        result.update(version='Unknown', version_source='unknown', plugin_version='Unknown')
    if not integrations._known_version(entry.get('package_version')):
        result.update(package_version=None, package_version_stale=True)
        return result
    expected = entry.get('installed_hashes')
    try:
        if entry['nexus_mod_id'] == integrations.SETUP_MOD:
            paths = update_signature(row | result, library.game)['files']
            matches = isinstance(expected, dict) and bool(expected) and installed_hashes(library.game, paths) == expected
        else:
            current = {path.removesuffix(inventory.DISABLED) for path in row.get('paths', [])
                       if path.removesuffix(inventory.DISABLED).lower().endswith('.dll')}
            matches = (isinstance(expected, dict) and bool(expected) and current == set(expected)
                       and installed_hashes(library.game, expected) == expected)
    except (OSError, ValueError, TypeError, AttributeError):
        matches = False
    if matches and not integrations._known_version(result.get('version', row.get('version'))):
        result.update(version=entry['package_version'], version_source='GK2MT saved archive')
    return result | {'package_version': entry['package_version'], 'package_version_stale': not matches}


def rescan(config, force_workshop_titles=False):
    rows = inventory.scan(Path(config['game']), Path(config['workshop']))
    discovery_warning = ''
    try:
        remember_discovery(package_manager(config), rows)
    except (OSError, ValueError):
        discovery_warning = 'Installed mods were scanned, but their Nexus links could not be saved. Try Rescan again.'
    ids = sorted({str(row['workshop_id']) for row in rows if row.get('workshop_id')})
    cache = workshop_title_cache()
    checked = cache.get('checked_at', 0)
    checked_ids = cache.get('checked_ids', [])
    fresh = (isinstance(checked, (int, float)) and 0 <= time.time() - checked < 86400
             and isinstance(checked_ids, list) and all(isinstance(item, str) for item in checked_ids)
             and set(ids) <= set(checked_ids))
    warning = ''
    if ids and (force_workshop_titles or not fresh):
        try:
            titles = dict(cache.get('titles', {}))
            titles.update(integrations.steam_workshop_titles(ids))
            manager.save_json(DATA / 'workshop-titles.json',
                              {'titles': titles, 'checked_ids': ids, 'checked_at': time.time()})
        except (OSError, RuntimeError, ValueError):
            warning = 'Steam Workshop titles could not be refreshed. Showing cached or local names; try Rescan again later.'
    return snapshot() | {'workshop_title_warning': warning, 'nexus_discovery_warning': discovery_warning}


def uninstall_reason(row):
    if row.get('workshop_id') or row.get('source') == 'Steam Workshop':
        return 'Steam manages this mod. Unsubscribe through its Workshop page.'
    if row.get('foundation') or row.get('nexus_mod_id') == integrations.SETUP_MOD:
        return 'The BepInEx foundation cannot be uninstalled as a mod.'
    if row.get('source') not in ('GK2MT', 'Manual', 'Vortex') or not row.get('paths'):
        return 'This mod has no safely identifiable installed files.'
    if row.get('can_toggle') is False:
        return 'This folder has mixed ownership. Remove the imported package instead.'
    if len(row.get('vortex_sources', [])) > 1:
        return 'This folder has files from multiple packages. GK2MT cannot safely uninstall it as one mod.'
    return ''


def workshop_setup(config, install=False):
    status = integrations.workshop_loader_setup(Path(config['game']), Path(config['workshop']), DATA)
    if status['state'] in ('ready', 'waiting_workshop'):
        for package in package_manager(config).state['packages']:
            if any(Path(path.casefold().removesuffix(inventory.DISABLED)).name in inventory.LOADERS
                   for field in ('paths', 'retired_paths', 'adopted_paths') for path in package.get(field, [])):
                return status | {'state': 'blocked', 'can_install': False,
                                 'message': f"An imported package owns the Workshop loader: {package['name']}. Enable or resolve that package in My mods before installing another copy."}
    if install:
        return integrations.workshop_loader_setup(Path(config['game']), Path(config['workshop']), DATA, install=True)
    return status


def checked_rows(rows):
    """Bind update reports to inventory metadata without re-hashing files on every refresh."""
    return {row['id']: {key: row.get(key) for key in
            ('name', 'version', 'package_version', 'package_version_stale', 'nexus_mod_id', 'file_id', 'source', 'enabled', 'paths')}
            for row in rows}


def profile_store(config):
    path = package_manager(config).data / 'profiles.json'
    value = manager.read_json(path, {'profiles': [], 'active': None})
    if not isinstance(value, dict) or not isinstance(value.get('profiles'), list) or len(value['profiles']) > 100:
        raise ValueError('The saved profile list is invalid.')
    records = []
    for item in value['profiles']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or len(item['id']) != 32:
            raise ValueError('A saved profile is invalid.')
        records.append({'id': item['id'], 'profile': profiles.validate(item['profile'])})
    return path, {'profiles': records, 'active': value.get('active')}


def profile_record(config, identifier):
    _, store = profile_store(config)
    record = next((item for item in store['profiles'] if item['id'] == identifier), None)
    if not record:
        raise ValueError('Profile no longer exists. Refresh the profile list.')
    return record['profile']


def profile_rows(config):
    rows = snapshot()['mods']
    library = package_manager(config)
    packages = {p['id']: p for p in library.state['packages']}
    for row in rows:
        package = packages.get(row['id'])
        if package and not row.get('nexus_mod_id') and not row.get('workshop_id'):
            dlls = []
            for relative in package['paths']:
                if not relative.lower().endswith('.dll'):
                    continue
                path = manager.safe_path(library.game if package['enabled'] else Path(package['folder']), relative)
                if path.is_file():
                    dlls.append({'name': Path(relative).name.casefold(), 'sha256': manager.digest(path)})
            row['_profile_dlls'] = dlls
    return rows


def profile_signature(config, profile, rows):
    library = package_manager(config)
    value = {'game': str(Path(config['game']).resolve()), 'workshop': str(Path(config['workshop']).resolve()),
             'profile': profile, 'rows': checked_rows(rows), 'library': library.state,
             'files': {row['id']: installed_hashes(Path(config['game']), row.get('paths', [])) for row in rows}}
    value['payloads'] = {package['id']: {relative: manager.digest(manager.safe_path(Path(package['folder']), relative))
                                      for relative in package['paths']}
                         for package in library.state['packages']}
    trust = manager.safe_path(Path(config['game']), inventory.TRUST)
    value['trust'] = manager.digest(trust) if trust.is_file() else None
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def compare_profile(config, identifier):
    global PROFILE_PREVIEW
    profile = profile_record(config, identifier)
    rows = profile_rows(config)
    result = profiles.compare(profile, rows, Path(config['game']))
    token = secrets.token_hex(16)
    PROFILE_PREVIEW = {'id': identifier, 'token': token, 'signature': profile_signature(config, profile, rows)}
    return result | {'id': identifier, 'name': profile['name'], 'token': token}


def apply_profile(config, body):
    global PROFILE_PREVIEW, PREVIEW, UPDATES
    manager.ensure_game_stopped()
    if not (Path(config['game']) / 'GraveyardKeeper2.exe').is_file():
        raise ValueError('Set your game location before applying a profile.')
    if active_download():
        raise ValueError('Finish or cancel the current download before applying a profile.')
    reviewed, PROFILE_PREVIEW = PROFILE_PREVIEW, None
    if not reviewed or reviewed['id'] != body.get('id') or reviewed['token'] != body.get('token'):
        raise ValueError('Review this profile again before applying it.')
    profile = profile_record(config, body['id'])
    rows = profile_rows(config)
    if profile_signature(config, profile, rows) != reviewed['signature']:
        raise ValueError('The profile or installed mods changed. Refresh and review the profile again.')
    plan = profiles.compare(profile, rows, Path(config['game']))
    if plan['blockers']:
        raise ValueError('Finish installing or resolving this profile first. ' + ' '.join(plan['blockers']))
    if plan['mismatches'] and body.get('accept_versions') is not True:
        raise ValueError('Installed versions differ from this profile. Install the requested releases or explicitly accept the installed versions.')
    library = package_manager(config)
    original_state = copy.deepcopy(library.state)
    game, workshop = Path(config['game']), Path(config['workshop'])
    trust = manager.safe_path(game, inventory.TRUST)
    trust_original = trust.read_bytes() if trust.is_file() else None
    changed, managed_changed, managed_deployed = [], False, False
    by_id = {row['id']: row for row in rows}
    # Disable before enabling to avoid briefly loading both sources of one mod.
    try:
        for change in sorted(plan['changes'], key=lambda change: change['enabled']):
            row = by_id[change['id']]
            if row['source'] == 'GK2MT':
                next(p for p in library.state['packages'] if p['id'] == row['id'])['enabled'] = change['enabled']
                managed_changed = True
            else:
                inventory.toggle(game, workshop, row, change['enabled'])
                changed.append(row)
        if managed_changed:
            library.preserve_settings()
            library.deploy()
            managed_deployed = True
        path, store = profile_store(config)
        store['active'] = body['id']
        manager.save_json(path, store)
    except Exception as error:
        rollback_errors = []
        for row in reversed(changed):
            if row['id'].startswith('workshop:'):
                continue  # Restore the exact consent file below, including pending approvals.
            try:
                inventory.toggle(game, workshop, row, bool(row['enabled']))
            except Exception as rollback:
                rollback_errors.append(str(rollback))
        try:
            if trust_original is not None:
                inventory._atomic_text(trust, trust_original.decode('utf-8'))
            elif trust.is_file():
                trust.unlink()
            if managed_deployed:
                deployed, baseline = library.state['deployed'], library.state['baseline']
                library.state = original_state | {'deployed': deployed, 'baseline': baseline}
                library.preserve_settings()
                library.deploy()
        except Exception as rollback:
            rollback_errors.append(str(rollback))
        if rollback_errors:
            raise RuntimeError('Profile application stopped. Some changes could not be restored; rescan before launching. '
                               + ' '.join(rollback_errors)) from error
        raise
    PREVIEW = UPDATES = None
    current = snapshot()['mods']
    pending = [row['name'] for row in current if row.get('workshop_id') and any(
        entry['installed_id'] == row['id'] and entry['enabled'] for entry in plan['entries']) and not row['enabled']]
    return {'message': f"Profile '{profile['name']}' applied. Changes take effect on the next launch.",
            'pending': pending, 'warnings': ['Workshop mods still need approval or deployment on the next game launch: '
                                          + ', '.join(pending)] if pending else []}


def snapshot():
    config = settings()
    game, workshop = Path(config['game']), Path(config['workshop'])
    library = package_manager(config)
    rows = inventory.scan(game, workshop)
    owned = {p.casefold() for pkg in library.state['packages'] if pkg['enabled'] for p in pkg['paths'] if p.lower().endswith('.dll')}
    visible = []
    for row in rows:
        dlls = {p.casefold() for p in row.get('paths', []) if p.lower().endswith('.dll')}
        if dlls and dlls <= owned:
            continue
        if dlls & owned:
            row['can_toggle'] = False
            row.setdefault('warnings', []).append('This folder also contains GK2MT-managed DLLs; change the imported package instead.')
        visible.append(row)
    rows = visible
    foundation = foundation_metadata(library)
    discovery = discovery_cache(library)
    metadata = config.get('metadata', {})
    for row in rows:
        row['plugin_version'] = row.get('version')
        row.update(discovered_metadata(library, row, discovery))
        overrides = metadata.get(row['id'], {})
        row.update(overrides)
        if 'version' in overrides and 'package_version' in row:
            row.update(package_version=overrides['version'], package_version_stale=False)
        if foundation and any(p.removesuffix(inventory.DISABLED).casefold() ==
                              'bepinex/plugins/configurationmanager/configurationmanager.dll'
                              for p in row.get('paths', [])):
            row.update(foundation, package_version_stale=False)
            row['warnings'] = [warning for warning in row.get('warnings', [])
                               if not warning.startswith("Vortex's recorded version")]
        row.setdefault('can_toggle', bool(row.get('paths') or row.get('workshop_id')))
    for pkg in library.state['packages']:
        rows.append({k: v for k, v in pkg.items() if k != 'folder'} | {'can_toggle': True, 'warnings': [], 'status': 'Installed' if pkg['enabled'] else 'Disabled'})
    categories = manager.read_json(library.data / 'nexus-categories.json', {})
    workshop_titles = workshop_title_cache().get('titles', {})
    for row in rows:
        row['display_version'] = (integrations.installed_version(row) if row.get('nexus_mod_id')
                                  else row.get('version') or 'Unknown')
        title = workshop_titles.get(str(row.get('workshop_id')))
        if isinstance(title, str) and title.strip() and not metadata.get(row['id'], {}).get('name'):
            row.update(name=title, name_source='Steam Workshop')
        category = categories.get(str(row.get('nexus_mod_id')))
        if category:
            row.update(category=category, category_source='Nexus Mods')
        row['uninstall_reason'] = uninstall_reason(row)
        row['can_uninstall'] = not row['uninstall_reason']
    if UPDATES is not None and '_checked_rows' in UPDATES:
        current = checked_rows(rows) if UPDATES.get('_game') == str(game.resolve()) else {}
        if UPDATES['_checked_rows'] != current:
            UPDATES['_stale'] = True
            stale = {item['id'] for item in UPDATES.get('updates', [])
                     if item['id'] not in current or UPDATES['_checked_rows'].get(item['id']) != current[item['id']]}
            UPDATES['updates'] = [item for item in UPDATES.get('updates', []) if item['id'] not in stale]
            for identifier in stale:
                UPDATES.get('_signatures', {}).pop(identifier, None)
    missing_loader_files = [name for name in BEPINEX_FILES if not (game / name).is_file()]
    _, stored_profiles = profile_store(config)
    profile_list = [{'id': item['id'], 'name': item['profile']['name'],
                     'mod_count': len(item['profile']['mods']),
                     'enabled_count': sum(mod['enabled'] for mod in item['profile']['mods']),
                     'active': item['id'] == stored_profiles['active']} for item in stored_profiles['profiles']]
    return {'settings': config, 'mods': sorted(rows, key=lambda row: (row['name'].casefold(), row['id'])),
            'duplicates': inventory.duplicate_groups(rows), 'profiles': profile_list,
            'active_profile': stored_profiles['active'],
            'rules': library.state['rules'], 'conflicts': library.conflicts(),
            'locations_confirmed': (DATA / 'settings.json').is_file(),
            'quick_setup_completed': config.get('quick_setup_completed') is True,
            'loader_installed': not missing_loader_files, 'loader_missing': missing_loader_files,
            'workshop_loader': inventory.loader_info(game), 'workshop_setup': workshop_setup(config),
            'game_found': (game / 'GraveyardKeeper2.exe').exists(), 'nexus_connected': bool(NEXUS_KEY),
            'nexus_saved': (DATA / 'nexus-key.bin').is_file(), 'nexus_error': NEXUS_ERROR,
            'updates': UPDATES, 'data_path': str(DATA), 'version': '0.1.3',
            'deck_connection': deck_connection(config), **download_state()}


def row_by_id(identifier):
    return next((r for r in snapshot()['mods'] if r['id'] == identifier), None)


def update_signature(row, game):
    files = {}
    paths = set(row.get('paths', []))
    if row.get('nexus_mod_id') == integrations.SETUP_MOD:
        paths.update(integrations.REQUIRED)
        paths.update(integrations.ROOT_FILES)
        for area in ('BepInEx/core', 'BepInEx/distribution', 'BepInEx/plugins/ConfigurationManager'):
            paths.update(p.relative_to(game).as_posix() for p in (game / area).rglob('*') if p.is_file())
    for relative in sorted(paths):
        path = manager.safe_path(game, relative)
        files[relative] = manager.digest(path) if path.is_file() else None
    return {'files': files, 'mod': {key: row.get(key) for key in
            ('id', 'version', 'package_version', 'package_version_stale', 'nexus_mod_id', 'file_id', 'enabled', 'source')}}


def preview_uninstall(config, identifier):
    global UNINSTALL_PREVIEW
    UNINSTALL_PREVIEW = None
    if active_download():
        raise ValueError('Finish or cancel the current download before uninstalling a mod.')
    manager.ensure_game_stopped()
    row = row_by_id(identifier)
    if not row:
        raise ValueError('Mod no longer exists. Refresh the inventory.')
    reason = uninstall_reason(row)
    if reason:
        raise ValueError(reason)
    library = package_manager(config)
    plan = library.uninstall(row, dry_run=True)
    token = secrets.token_hex(16)
    UNINSTALL_PREVIEW = {'token': token, 'game': str(library.game.resolve()),
                         'row': copy.deepcopy(row), 'state': copy.deepcopy(library.state),
                         'signature': update_signature(row, library.game), 'plan': copy.deepcopy(plan)}
    return {key: value for key, value in plan.items() if not key.startswith('_')} | {'token': token}


def apply_uninstall(config, token):
    global UNINSTALL_PREVIEW, UPDATES, PREVIEW
    pending, UNINSTALL_PREVIEW = UNINSTALL_PREVIEW, None
    if not pending or pending['token'] != token:
        raise ValueError('Preview the mod uninstall again before confirming.')
    if active_download():
        raise ValueError('Finish or cancel the current download before uninstalling a mod.')
    manager.ensure_game_stopped()
    library = package_manager(config)
    row = row_by_id(pending['row']['id'])
    if (str(library.game.resolve()) != pending['game'] or row != pending['row']
            or library.state != pending['state'] or update_signature(row, library.game) != pending['signature']):
        raise ValueError('The mod or installed files changed. Preview the uninstall again.')
    result = library.uninstall(row, expected=pending['plan'])
    UPDATES = PREVIEW = None
    UPDATE_ARCHIVES.clear()
    return {key: value for key, value in result.items() if not key.startswith('_')} | {
        'message': f"{row['name']} uninstalled. Configuration files were kept; removed files were backed up."}


def check_nexus(config, key):
    rows = snapshot()['mods']
    result = integrations.nexus_check(key, rows, {})
    library = package_manager(config)
    categories = manager.read_json(library.data / 'nexus-categories.json', {})
    for row in rows:
        if row.get('nexus_mod_id') and row['id'] in result.get('categories', {}):
            categories[str(row['nexus_mod_id'])] = result['categories'][row['id']]
    if categories:
        manager.save_json(library.data / 'nexus-categories.json', categories)
    result['_game'] = str(Path(config['game']).resolve())
    result['_checked_rows'] = checked_rows(rows)
    result['_signatures'] = {}
    overlap = foundation_overlap(library)
    by_id = {row['id']: row for row in rows}
    for update in result.get('updates', []):
        row = by_id[update['id']]
        update['manual_installable'] = bool(update.get('known_version') and update.get('file_id')
            and (update.get('downloadable') or update.get('status') == 'Update available')
            and not update.get('choices') and integrations._known_version(update.get('version'))
            and integrations._known_version(integrations.installed_version(row)))
        if row.get('workshop_id') or row.get('source') == 'Steam Workshop':
            update.update(downloadable=False, manual_installable=False, blocked_reason='Steam manages Workshop updates.')
        elif not row.get('can_toggle') or not row.get('paths'):
            update.update(downloadable=False, manual_installable=False, blocked_reason='This mod has no safely identifiable installed files.')
        elif update.get('nexus_mod_id') == integrations.SETUP_MOD and overlap:
            update.update(downloadable=False, manual_installable=False, blocked_reason=overlap)
        if update.get('manual_installable'):
            result['_signatures'][row['id']] = update_signature(row, library.game)
    return result


def verified_update(config, identifier):
    if not UPDATES or UPDATES.get('_game') != str(Path(config['game']).resolve()):
        raise ValueError('Check for updates for this game first.')
    report = UPDATES
    update = next((u for u in report.get('updates', []) if u['id'] == identifier), None)
    if not update or not update.get('manual_installable'):
        raise ValueError('This mod has no safely identified update. Check for updates again.')
    row = row_by_id(identifier)
    if UPDATES is not report or update not in report.get('updates', []):
        raise ValueError('Installed mods changed since the update check. Check for updates again.')
    expected = report.get('_signatures', {}).get(identifier)
    if not row or not expected or update_signature(row, Path(config['game'])) != expected:
        raise ValueError('Installed files changed since the update check. Check for updates again.')
    if row.get('workshop_id') or row.get('source') == 'Steam Workshop':
        raise ValueError('Steam manages Workshop updates.')
    return update, row, expected


def install_update_archive(config, update, row, archive, dry_run=False):
    global PREVIEW
    manager.ensure_game_stopped()
    library = package_manager(config)
    if update['nexus_mod_id'] == integrations.SETUP_MOD:
        overlap = foundation_overlap(library)
        if overlap:
            raise ValueError(overlap)
        result = integrations.setup_installer(Path(config['game']), DATA, archive=archive, dry_run=dry_run)
        if dry_run:
            return result
        result.update(package_version=update['version'], file_id=update['file_id'])
        PREVIEW = None
        record_foundation(config, result)
        new_id = row['id']
    else:
        staged = library.stage(archive)
        package = library.update(staged['token'], row, {
            'name': row['name'], 'version': update['version'], 'category': row.get('category', 'Uncategorized'),
            'nexus_mod_id': update['nexus_mod_id'], 'file_id': update['file_id']}, dry_run=dry_run)
        if dry_run:
            return package
        new_id, result = package['id'], {}
    UPDATES['updates'] = [u for u in UPDATES['updates'] if u['id'] != update['id']]
    UPDATES['_signatures'].pop(update['id'], None)
    PREVIEW = None
    return {'id': new_id, 'name': row['name'], 'version': update['version']}, result.get('warnings', [])


def preview_update_archive(config, identifier, path):
    update, row, expected = verified_update(config, identifier)
    if not NEXUS_KEY:
        raise ValueError('Connect Nexus first so the downloaded ZIP can be verified.')
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != '.zip' or not source.is_file() or source.stat().st_size > 2 * 1024**3:
        raise ValueError('Choose an existing ZIP smaller than 2 GB.')
    token = secrets.token_hex(16)
    archive = package_manager(config).data / 'update-archives' / (token + '.zip')
    manager.copy_atomic(source, archive)
    try:
        archive_hash = manager.digest(archive)
        release = integrations.nexus_archive_release(NEXUS_KEY, archive, update['nexus_mod_id'], update['file_id'])
        if str(release.get('version', '')).strip() != str(update['version']).strip():
            raise ValueError('The Nexus file version changed. Check for updates again.')
        preview = install_update_archive(config, update, row, archive, dry_run=True)
        verified_update(config, identifier)
        if manager.digest(archive) != archive_hash:
            raise ValueError('The staged ZIP changed during verification. Choose it again.')
        UPDATE_ARCHIVES[token] = {'game': str(Path(config['game']).resolve()), 'archive': archive,
                                  'hash': archive_hash, 'update': dict(update), 'signature': expected}
        return dict(preview, token=token, name=row['name'], version=update['version'],
                    warnings=preview.get('warnings', []) + ['ZIP identity verified against the selected Nexus file.'])
    except BaseException:
        archive.unlink(missing_ok=True)
        raise


def apply_update_archive(config, token):
    pending = UPDATE_ARCHIVES.get(token)
    if not pending or pending['game'] != str(Path(config['game']).resolve()):
        raise ValueError('Choose and preview the update ZIP again.')
    update, row, expected = verified_update(config, pending['update']['id'])
    if update != pending['update'] or expected != pending['signature'] or manager.digest(pending['archive']) != pending['hash']:
        raise ValueError('The update or staged ZIP changed. Preview the update ZIP again.')
    updated, warnings = install_update_archive(config, update, row, pending['archive'])
    UPDATE_ARCHIVES.pop(token, None)
    return {'updated': [updated], 'skipped': [], 'errors': [], 'warnings': warnings,
            'message': 'Mod updated from the verified ZIP. Replaced files were backed up.'}


def apply_updates(config, identifier=None):
    global PREVIEW
    if not UPDATES or UPDATES.get('_game') != str(Path(config['game']).resolve()):
        raise ValueError('Check for updates for this game first.')
    if not NEXUS_KEY:
        raise ValueError('Connect Nexus and check for updates first.')
    selected = [u for u in UPDATES.get('updates', []) if identifier is None or u['id'] == identifier]
    if identifier is not None and not selected:
        raise ValueError('This update is no longer available. Check for updates again.')
    manager.ensure_game_stopped()
    updated, skipped, errors, warnings = [], [], [], []
    for update in selected:
        if not update.get('known_version') or not update.get('downloadable') or not update.get('file_id'):
            skipped.append({'id': update['id'], 'name': update['name'],
                            'reason': update.get('blocked_reason') or update.get('status') or 'No known update version.'})
            continue
        try:
            _, row, expected = verified_update(config, update['id'])
            archive = integrations.nexus_download(NEXUS_KEY, update['nexus_mod_id'], update['file_id'], DATA / 'downloads')
            _, current, _ = verified_update(config, row['id'])
            installed, notes = install_update_archive(config, update, current, archive)
            updated.append(installed)
            warnings.extend(notes)
        except Exception as exc:
            errors.append(update['name'] + ': ' + str(exc))
            if any(code in str(exc) for code in ('401', '403', '429')):
                break
    return {'updated': updated, 'skipped': skipped, 'errors': errors, 'warnings': warnings,
            'message': f'{len(updated)} mod(s) updated; {len(skipped)} skipped; {len(errors)} failed. Replaced files were backed up.'}


def import_duplicates(config, package, metadata=None, rows=None):
    """Use the library's existing Steam/local identity check before writing any files."""
    candidate = dict(package, id='incoming:' + package['id'], source='GK2MT')
    candidate.update({key: value for key, value in (metadata or {}).items()
                      if key in ('name', 'nexus_mod_id') and value})
    rows = snapshot()['mods'] if rows is None else rows
    by_id = {row['id']: row for row in rows}
    groups = inventory.duplicate_groups(rows + [candidate])
    result = []
    for group in groups:
        if candidate['id'] not in group['row_ids']:
            continue
        for identifier in group['row_ids']:
            if identifier == candidate['id']:
                continue
            row = by_id[identifier]
            steam = bool(row.get('workshop_id') or row.get('source') == 'Steam Workshop')
            result.append({'id': identifier, 'name': row['name'], 'source': 'Steam' if steam else
                           'Nexus' if row.get('nexus_mod_id') else 'Manual', 'enabled': bool(row.get('enabled')),
                           'workshop_id': row.get('workshop_id'), 'reason': group['reason'],
                           'paths': row.get('paths', []), 'workshop_paths': row.get('workshop_paths', [])})
    return sorted(result, key=lambda row: row['id'])


def preview_imports(config, tokens, metadata=None, remember=True):
    """Bind one read-only review to all selected files and matching Steam copies."""
    if (not isinstance(tokens, list) or not 1 <= len(tokens) <= 100
            or any(not isinstance(token, str) for token in tokens) or len(set(tokens)) != len(tokens)):
        raise ValueError('Choose between 1 and 100 distinct staged ZIP packages.')
    library = package_manager(config)
    plan = library.plan_install(tokens, metadata)
    rows = snapshot()['mods'] if any(p['status'] == 'ready' for p in plan['packages']) else []
    for summary in plan['packages']:
        duplicates = []
        if summary['status'] == 'ready':
            package = manager.read_json(library.data / 'staging' / summary['token'] / 'package.json', {})
            duplicates = import_duplicates(config, package, (metadata or {}).get(summary['token']), rows)
        summary.update(duplicates=duplicates, requires_duplicate_ack=bool(duplicates),
                       warnings=['Another Steam/local copy is already installed. Keep only one enabled copy.']
                                if duplicates else [])
    review = {'game': str(library.game.resolve()), 'tokens': tokens, 'plan_digest': plan['digest'],
              'duplicates': {p['token']: p['duplicates'] for p in plan['packages']}}
    digest = hashlib.sha256(json.dumps(review, sort_keys=True).encode()).hexdigest()
    if remember:
        IMPORT_REVIEWS[digest] = review
    return plan | {'digest': digest}


def install_import_batch(config, tokens, choices, digest, acknowledge_duplicates=False, metadata=None):
    reviewed = IMPORT_REVIEWS.get(digest) if isinstance(digest, str) else None
    if not reviewed or reviewed.get('tokens') != tokens:
        raise ValueError('Review these ZIPs before installing.')
    current = preview_imports(config, tokens, metadata, remember=False)
    if current['digest'] != digest:
        raise ValueError('Files or matching Steam/local copies changed. Review these ZIPs again before installing; no files changed.')
    if any(p['requires_duplicate_ack'] for p in current['packages']) and acknowledge_duplicates is not True:
        raise ValueError('A Steam/local copy is already installed. Review and acknowledge the duplicate warning first.')
    result = package_manager(config).install_batch(tokens, choices, reviewed['plan_digest'], metadata)
    IMPORT_REVIEWS.pop(digest, None)
    for token in tokens:
        IMPORT_REVIEWS.pop(token, None)
    return result | {'message': f"{result['installed']} mod(s) installed; {result['skipped']} ZIP(s) skipped. Replaced files were backed up."}


def stage_import(config, archive, metadata=None):
    preview = package_manager(config).stage(archive)
    plan = preview_imports(config, [preview['token']], {preview['token']: metadata} if metadata else None)
    summary = plan['packages'][0]
    IMPORT_REVIEWS[preview['token']] = {'digest': plan['digest']}
    return preview | {key: summary[key] for key in ('name', 'duplicates', 'requires_duplicate_ack', 'warnings')} | {
        'digest': plan['digest']}


def install_import(config, token, metadata=None, acknowledge_duplicates=False, choices=None, digest=None):
    library = package_manager(config)
    if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{32}', token):
        raise ValueError('Invalid staging token.')
    package = manager.read_json(library.data / 'staging' / token / 'package.json', None)
    if not package:
        raise ValueError('Staged package is missing. Choose the ZIP again.')
    existing = library.identical_package(package)
    if existing:
        return {'status': 'already_installed', 'id': existing['id'],
                'message': f"{existing['name']} is already installed. Skipped; its enabled state and files were kept."}
    digest = digest or IMPORT_REVIEWS.get(token, {}).get('digest')
    result = install_import_batch(config, [token], choices or {}, digest, acknowledge_duplicates,
                                  {token: metadata} if metadata else None)
    return result['results'][0] | {'message': result['message']}


def download_job(job):
    return {key: job[key] for key in ('id', 'update_id', 'mod_id', 'name', 'version', 'state',
                                     'message', 'received', 'total', 'retryable', 'cancellable')} | {
                                         'kind': job.get('kind', 'update'), 'profile_id': job.get('profile_id'),
                                         'review_required': job.get('state') == 'awaiting_review',
                                         'duplicates': job.get('duplicates', [])}


def download_state():
    return {'download_panel': dict(nexus_panel.availability(), folder=str(DATA / 'nexus-downloads')),
            'downloads': [download_job(job) for job in list(DOWNLOADS.values())]}


def active_download():
    return next((job for job in DOWNLOADS.values() if job['state'] in
                 ('waiting', 'downloading', 'verifying', 'installing', 'waiting_game', 'awaiting_review')), None)


def stop_download_panel(job):
    process = job.get('_process')
    try:
        if process and process.poll() is None:
            process.terminate()
    except OSError:
        pass  # Cancellation still prevents installation if the window is already closing.


def install_browser_archive(config, job, archive):
    global PREVIEW
    archive_hash = manager.digest(archive)
    requested = job.get('_requested_release')
    if requested:
        entry = next((entry for entry in profile_record(config, job['profile_id'])['mods']
                      if entry['key'] == job['_requested_entry']['key']), None)
        if entry != job['_requested_entry']:
            raise ValueError('The profile changed during this download. Start it again from the profile.')
        details = integrations.nexus_archive_release(NEXUS_KEY, archive, mod_id=requested['nexus_mod_id'],
                                                    file_id=requested['file_id'])
        if (integrations._known_version(requested['version'])
                and not profiles.same_version(str(details.get('version') or 'Unknown'), requested['version'])):
            raise ValueError('The verified Nexus file version differs from the requested profile release. No mod was installed.')
        release = dict(details, nexus_mod_id=requested['nexus_mod_id'], file_id=requested['file_id'],
                       name=requested['name'], version=details.get('version') or 'Unknown',
                       category_id=requested.get('category_id'))
    else:
        release = integrations.nexus_archive_release(NEXUS_KEY, archive, mod_id=job.get('_mod_hint'))
    if manager.digest(archive) != archive_hash:
        raise ValueError('The downloaded ZIP changed during verification. Download it again before installing.')
    mod_id, file_id = integrations._id(release['nexus_mod_id']), integrations._id(release['file_id'])
    if min(mod_id, file_id) <= 0:
        raise ValueError('Nexus returned an invalid mod identity.')
    name, version = str(release.get('name') or f'Nexus mod #{mod_id}'), str(release.get('version') or 'Unknown')
    job.update(mod_id=mod_id, name=name, version=version, state='installing',
               message='Installing the verified mod…', retryable=False)
    library = package_manager(config)
    notes = []
    category = manager.read_json(library.data / 'nexus-categories.json', {}).get(str(mod_id), 'Uncategorized')
    if category == 'Uncategorized' and release.get('category_id'):
        try:
            game = integrations._json(f'{integrations.API}/games/{integrations.GAME}.json', NEXUS_KEY)
            category = next((c['name'] for c in game.get('categories', [])
                             if str(c.get('category_id')) == str(release['category_id']) and c.get('name')), category)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError):
            notes.append('The mod category could not be fetched. Check for updates to refresh it later.')
    rows = [row for row in snapshot()['mods'] if str(row.get('nexus_mod_id')) == str(mod_id)]
    steam_rows = [row for row in rows if row.get('workshop_id') or row.get('source') == 'Steam Workshop']
    rows = [row for row in rows if row not in steam_rows]
    if len(rows) > 1:
        raise ValueError('Several installed copies link to this Nexus mod. Remove the unwanted copy before installing it again.')
    if manager.digest(archive) != archive_hash:
        raise ValueError('The verified ZIP changed. Download it again before installing.')
    if mod_id == integrations.SETUP_MOD:
        overlap = foundation_overlap(library)
        if overlap:
            raise ValueError(overlap)
        result = integrations.setup_installer(Path(config['game']), DATA, archive=archive)
        result.update(package_version=version, file_id=file_id)
        record_foundation(config, result)
        notes.extend(result.get('warnings', []))
    else:
        staged = library.stage(archive)
        if manager.digest(archive) != archive_hash:
            raise ValueError('The verified ZIP changed while extracting. No mod was installed.')
        metadata = {'name': name, 'version': version, 'category': category, 'nexus_mod_id': mod_id, 'file_id': file_id}
        path = library.data / 'staging' / staged['token'] / 'package.json'
        package = manager.read_json(path, {})
        duplicates = import_duplicates(config, package, metadata)
        if duplicates:
            review = {'archive_hash': archive_hash, 'duplicates': duplicates}
            if job.get('_duplicates_acknowledged') != review:
                job['_duplicate_review'] = review
                job.update(duplicates=duplicates)
                return {'requires_review': True,
                        'message': 'A matching Steam/local copy is already installed. Review the duplicate warning before installation.'}
            notes.append('A matching Steam/local copy is also installed. Keep only one enabled copy to avoid loading the mod twice.')
        if rows:
            library.update(staged['token'], rows[0], metadata)
        else:
            if library.identical_package(package):
                existing = library.install(staged['token'], metadata)
                return {'message': f"{existing['name']} is already installed. Skipped; its enabled state and files were kept.",
                        'warnings': notes}
            owned = {p.casefold() for pkg in library.state['packages']
                     for field in ('paths', 'adopted_paths', 'retired_paths') for p in pkg.get(field, [])}
            preserved = []
            for relative in package['paths']:
                settings_file = relative.casefold().startswith('bepinex/config/') or Path(relative).suffix.lower() in ('.cfg', '.ini')
                target = manager.safe_path(library.game, relative)
                if settings_file and target.is_file():
                    preserved.append(relative)
                elif (relative.casefold() in owned or target.exists()
                      or manager.safe_path(library.game, relative + inventory.DISABLED).exists()):
                    raise ValueError('This ZIP would overwrite another installed mod: ' + relative + '. Review or uninstall that copy first.')
                if (not relative.casefold().startswith(('bepinex/plugins/', 'bepinex/patchers/', 'bepinex/config/'))
                        or any(p.casefold() in ('_workshop', 'core', 'distribution', '_sync', 'installer')
                               for p in Path(relative).parts[2:-1])):
                    raise ValueError('This mod needs its documented custom installer. Only plugin or patcher ZIPs install automatically.')
            package['paths'] = [p for p in package['paths'] if p not in preserved]
            manager.save_json(path, package)
            library.install(staged['token'], metadata)
    PREVIEW = None
    UPDATE_ARCHIVES.clear()
    return {'message': f'{name} installed and added to My mods.', 'warnings': notes}


def install_panel_download(job):
    """Only install the release and game the user selected when opening this panel."""
    archive, archive_hash = None, None
    job.update(state='verifying', message='Verifying the downloaded Nexus file…', retryable=False, cancellable=False)
    try:
        config = settings()
        if str(Path(config['game']).resolve()) != job['_game'] or NEXUS_SESSION != job['_nexus_session'] or not NEXUS_KEY:
            raise ValueError('Game locations or the Nexus connection changed. Check for updates and start this download again.')
        browser = job.get('kind') == 'install'
        if not browser:
            update, _, signature = verified_update(config, job['update_id'])
            if update != job['_update'] or signature != job['_signature']:
                raise ValueError('The selected update or installed files changed. Check for updates and start this download again.')
        archive = manager.safe_path(job['_folder'], 'download.zip')
        if not archive.is_file() or (job['_folder'] / 'download.zip.part').exists():
            raise ValueError('The download is incomplete. Open the download panel again.')
        archive_hash = manager.digest(archive)
        try:
            manager.ensure_game_stopped()
        except ValueError:
            job.update(state='waiting_game', message='Download ready. Close Graveyard Keeper 2, then retry to install.',
                       retryable=True, cancellable=True)
            return
        job['retryable'] = True
        if browser:
            result = install_browser_archive(config, job, archive)
        else:
            preview = preview_update_archive(config, job['update_id'], str(archive))
            job.update(state='installing', message='Installing the verified update…', retryable=False)
            result = apply_update_archive(config, preview['token'])
        if result.get('requires_review'):
            job.update(state='awaiting_review', message=result['message'], retryable=False, cancellable=True)
            stop_download_panel(job)
            return
        message = result['message']
        if result.get('warnings'):
            message += ' ' + ' '.join(result['warnings'])
        job.update(state='completed', message=message, retryable=False)
        stop_download_panel(job)
    except Exception as exc:
        retryable = job['retryable']
        if job['state'] == 'installing' and archive_hash:
            try:
                retryable = (archive.is_file() and not (job['_folder'] / 'download.zip.part').exists()
                             and manager.digest(archive) == archive_hash)
            except OSError:
                pass
        job.update(state='error', message=str(exc), retryable=retryable, cancellable=False)


def advance_download(job):
    """Called under LOCK; a .part file or unrelated ZIP never triggers installation."""
    if job['state'] not in ('waiting', 'downloading'):
        return
    try:
        status_path = manager.safe_path(job['_folder'], 'panel-status.json')
        if status_path.exists() and status_path.stat().st_size > 16384:
            raise ValueError('The download panel returned an invalid status. Open it again.')
        status = manager.read_json(status_path, {})
        phase = status.get('state')
        if job.get('kind') == 'install' and type(status.get('mod_id')) is int and status['mod_id'] > 0:
            job['_mod_hint'] = status['mod_id']
        if phase in ('waiting', 'downloading'):
            job.update(state=phase, message=('Browse Nexus and choose Manual Download → Slow Download.'
                       if job.get('kind') == 'install' else 'Choose Free Download in the Nexus panel.') if phase == 'waiting'
                       else 'Downloading into GK2MT’s update folder…')
            for field in ('received', 'total'):
                value = status.get(field, 0)
                job[field] = max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0
        elif phase == 'downloaded':
            install_panel_download(job)
            return
        elif phase in ('closed', 'error'):
            job.update(state=phase, message=status.get('message') or 'The download panel closed before a file was ready.',
                       retryable=False, cancellable=False)
            return
        if job['_process'].poll() is not None:
            job.update(state='closed', message='The download panel closed before a file was ready.',
                       retryable=False, cancellable=False)
    except Exception as exc:
        job.update(state='error', message=str(exc), retryable=False, cancellable=False)
        stop_download_panel(job)


def watch_download(job):
    # ponytail: one active download and the existing mutation lock; split network work only if concurrent jobs are needed.
    while not job['_stop'].wait(0.5):
        with LOCK:
            if job['_stop'].is_set():
                return
            advance_download(job)
            if job['state'] not in ('waiting', 'downloading'):
                return


def start_download_panel(config, identifier=None, profile_release=None, profile_id=None, profile_entry=None, automatic=False):
    if active_download():
        raise ValueError('Finish or cancel the current download before opening another panel.')
    available = nexus_panel.availability()
    if not available['available'] and not automatic:
        raise ValueError(available['message'])
    if not NEXUS_KEY:
        raise ValueError('Connect Nexus first so downloaded mods can be verified.')
    browser = identifier is None
    if browser:
        if not (Path(config['game']) / 'GraveyardKeeper2.exe').is_file():
            raise ValueError('Set your game location before downloading mods.')
        update, row, signature = (profile_release or {'nexus_mod_id': 0, 'file_id': 0, 'version': ''}), {
            'name': profile_release['name'] if profile_release else 'Nexus mod browser'}, None
    else:
        update, row, signature = verified_update(config, identifier)
    job_id = secrets.token_hex(16)
    folder = manager.safe_path(DATA / 'nexus-downloads', job_id)
    folder.mkdir(parents=True, exist_ok=False)
    job = {'id': job_id, 'update_id': identifier, 'kind': 'install' if browser else 'update', 'mod_id': update['nexus_mod_id'],
           'name': row['name'], 'version': update['version'], 'state': 'waiting',
           'message': 'Browse Nexus and choose Manual Download → Slow Download.' if browser else 'Choose Free Download in the Nexus panel.', 'received': 0, 'total': 0,
           'retryable': False, 'cancellable': True, '_folder': folder,
           '_game': str(Path(config['game']).resolve()), '_nexus_session': NEXUS_SESSION,
           '_update': copy.deepcopy(update), '_signature': copy.deepcopy(signature), '_stop': threading.Event()}
    if profile_release:
        job.update(profile_id=profile_id, _requested_release=copy.deepcopy(profile_release),
                   _requested_entry=copy.deepcopy(profile_entry))
    DOWNLOADS[job_id] = job
    if automatic:
        try:
            manager.ensure_game_stopped()
            job.update(state='downloading', message='Downloading the requested profile release…', cancellable=False)
            archive = integrations.nexus_download(NEXUS_KEY, update['nexus_mod_id'], update['file_id'], folder)
            manager.copy_atomic(archive, folder / 'download.zip')
            install_panel_download(job)
        except Exception as error:
            job.update(state='error', message=str(error), retryable=False, cancellable=False)
        return download_job(job)
    try:
        job['_process'] = nexus_panel.launch(folder, update['nexus_mod_id'], update['file_id'])
    except Exception:
        job.update(state='error', message='Could not open the Nexus download panel. Check its setup and try again.', cancellable=False)
        raise
    threading.Thread(target=watch_download, args=(job,), daemon=True).start()
    return download_job(job)


def cancel_download_panel(identifier):
    job = DOWNLOADS.get(identifier)
    if not job or not job['cancellable']:
        raise ValueError('This download can no longer be cancelled.')
    job.update(state='cancelled', message='Download cancelled. No update was installed.', retryable=False, cancellable=False)
    job['_stop'].set()
    stop_download_panel(job)
    return download_job(job)


def retry_download_panel(identifier):
    job = DOWNLOADS.get(identifier)
    if not job or not job['retryable']:
        raise ValueError('Start this update again from the updates list.')
    if active_download() not in (None, job):
        raise ValueError('Finish or cancel the current download before retrying this update.')
    install_panel_download(job)
    return download_job(job)


def approve_download_panel(identifier, acknowledge_duplicates):
    job = DOWNLOADS.get(identifier)
    if not job or job['state'] != 'awaiting_review' or not job.get('_duplicate_review'):
        raise ValueError('This download has no pending duplicate review.')
    if acknowledge_duplicates is not True:
        raise ValueError('Acknowledge the duplicate warning before installing this copy.')
    job['_duplicates_acknowledged'] = copy.deepcopy(job['_duplicate_review'])
    install_panel_download(job)
    return download_job(job)


def download_profile_mod(config, identifier, key):
    if active_download():
        raise ValueError('Finish or cancel the current download first.')
    if not NEXUS_KEY:
        raise ValueError('Connect Nexus on Updates first.')
    profile = profile_record(config, identifier)
    entry = next((entry for entry in profile['mods'] if entry['key'] == key), None)
    if not entry or entry['source'] != 'Nexus' or not entry['enabled']:
        raise ValueError('Choose an enabled Nexus mod from this profile.')
    compared = profiles.compare(profile, profile_rows(config), Path(config['game']))
    current = next(row for row in compared['entries'] if row['key'] == key)
    if current['status'] == 'ambiguous':
        raise ValueError('Resolve the duplicate installed copies before downloading this mod.')
    if current['status'] == 'installed' and not current['version_mismatch']:
        raise ValueError('This profile mod is already installed. Refresh its status.')
    mod_id = entry['nexus_mod_id']
    payload = integrations._json(f'{integrations.API}/games/{integrations.GAME}/mods/{mod_id}/files.json', NEXUS_KEY)
    candidate = None
    if entry.get('file_id'):
        candidate = next((file for file in payload.get('files', [])
                          if str(file.get('file_id')) == str(entry['file_id'])), None)
    else:
        wanted = entry.get('version', 'Unknown')
        matching = [file for file in payload.get('files', []) if file.get('category_id') in (1, 2, 3)
                    and profiles.same_version(str(file.get('version') or 'Unknown'), wanted)] if integrations._known_version(wanted) else []
        if len(matching) == 1:
            candidate = matching[0]
        elif not integrations._known_version(wanted):
            candidate, _ = integrations._candidate(payload, None)
    if not candidate:
        raise ValueError('Nexus has no unambiguous downloadable file matching this profile. Choose its original ZIP or edit the profile from an installed setup.')
    if not str(candidate.get('file_name', '')).lower().endswith('.zip'):
        raise ValueError('This profile release is not a ZIP and needs its documented manual installer.')
    actual = str(candidate.get('version') or 'Unknown')
    if integrations._known_version(entry['version']) and not profiles.same_version(actual, entry['version']):
        raise ValueError('The Nexus file version differs from this profile. The requested release will not be replaced automatically.')
    release = {'nexus_mod_id': mod_id, 'file_id': integrations._id(candidate['file_id']),
               'name': entry['name'], 'version': actual}
    try:
        metadata = integrations._json(f'{integrations.API}/games/{integrations.GAME}/mods/{mod_id}.json', NEXUS_KEY)
        release['category_id'] = metadata.get('category_id')
        if isinstance(metadata.get('name'), str) and metadata['name'].strip():
            release['name'] = metadata['name'].strip()
    except (OSError, RuntimeError, ValueError, AttributeError):
        pass  # File identity remains authoritative when optional display metadata is unavailable.
    automatic = integrations._account(NEXUS_KEY)['premium']
    return start_download_panel(config, profile_release=release, profile_id=identifier,
                                profile_entry=entry, automatic=automatic)


def dispatch(action, body):
    global NEXUS_KEY, NEXUS_ERROR, NEXUS_SESSION, UPDATES, PREVIEW, PROFILE_PREVIEW, DECK_SESSION, DECK_MESSAGE
    config = settings()
    game, workshop = Path(config['game']), Path(config['workshop'])
    if action == 'setup-check':
        return setup_check(config, body)
    if action == 'quick-setup-complete':
        if 'workshop_enabled' in body and not isinstance(body['workshop_enabled'], bool):
            raise ValueError('Choose whether to set up Steam Workshop mods.')
        workshop_enabled = body.get('workshop_enabled', config.get('workshop_enabled', True))
        checks = setup_check(config)
        if not checks['game_found']:
            raise ValueError(checks['game']['message'])
        if not checks['loader_installed']:
            raise ValueError('Install BepInEx before completing Quick Setup.')
        if workshop_enabled and not workshop_setup(config)['installed']:
            raise ValueError('Finish the Workshop loader setup, or leave Workshop mods unchecked for now.')
        config.update(quick_setup_completed=True, workshop_enabled=workshop_enabled)
        manager.save_json(DATA / 'settings.json', config)
        return {'message': 'Quick Setup complete. You are ready to add mods.', 'quick_setup_completed': True}
    if action == 'rescan':
        return rescan(config, force_workshop_titles=body.get('force_workshop_titles') is True)
    if action == 'workshop-loader-setup':
        if active_download():
            raise ValueError('Finish or cancel the current mod download before installing the Workshop loader.')
        result = workshop_setup(config, install=True)
        if result.get('files'):
            PREVIEW = UPDATES = PROFILE_PREVIEW = None
        return result
    if action == 'settings':
        previous_game = config.get('game', '')
        for field in ('game', 'workshop', 'import_folder'):
            if field in body:
                config[field] = str(body[field]).strip().strip('"')
        checks = setup_check(config)
        if not checks['game_found']:
            raise ValueError(checks['game']['message'])
        config['workshop'] = checks['workshop']['path']
        for field in ('workshop', 'import_folder'):
            path = Path(config[field] or '')
            if config[field] and (not inventory._safe(path.absolute(), Path(path.absolute().anchor))
                                  or path.exists() and not path.is_dir()):
                raise ValueError(f"Choose a physical {field.replace('_', ' ')} folder.")
        if str(Path(previous_game).absolute()).casefold() != str(Path(config['game']).absolute()).casefold():
            config['quick_setup_completed'] = False
        if 'deck' in body:
            config['deck'] = {key: body['deck'].get(key, default) for key, default in DECK_DEFAULTS.items()}
        manager.save_json(DATA / 'settings.json', config)
        PREVIEW = UPDATES = None
        deck_connection(config)
        return {'message': 'Locations saved.'}
    if action == 'discover':
        return inventory.discover()
    if action == 'browse':
        return file_dialog('zip' if body.get('kind') == 'zip' else 'folder')
    if action == 'profile-save':
        profile = profiles.capture(body.get('name'), profile_rows(config), game)
        path, store = profile_store(config)
        identifier = body.get('id')
        if identifier:
            record = next((item for item in store['profiles'] if item['id'] == identifier), None)
            if not record:
                raise ValueError('This profile no longer exists.')
            record['profile'] = profile
        else:
            if len(store['profiles']) >= 100:
                raise ValueError('Keep 100 profiles or fewer. Export and remove an unused profile first.')
            identifier = secrets.token_hex(16)
            store['profiles'].append({'id': identifier, 'profile': profile})
        manager.save_json(path, store)
        return {'profile': {'id': identifier, 'name': profile['name']}, 'message': 'Current mod setup saved as a profile.'}
    if action == 'profile-import':
        chosen = file_dialog('profile')['path']
        if not chosen:
            return {'profile': None, 'message': 'Profile import cancelled.'}
        imported = Path(chosen)
        if imported.stat().st_size > 1024 * 1024:
            raise ValueError('Choose a GK2MT profile JSON file smaller than 1 MB.')
        profile = profiles.validate(json.loads(imported.read_text('utf-8-sig')))
        path, store = profile_store(config)
        if len(store['profiles']) >= 100:
            raise ValueError('Keep 100 profiles or fewer. Remove an unused profile first.')
        identifier = secrets.token_hex(16)
        store['profiles'].append({'id': identifier, 'profile': profile})
        manager.save_json(path, store)
        return {'profile': {'id': identifier, 'name': profile['name']},
                'message': 'Profile imported. Review its missing mods before applying it.'}
    if action == 'profile-export':
        profile = profile_record(config, body['id'])
        chosen = file_dialog('profile-export')['path']
        if not chosen:
            return {'message': 'Profile export cancelled.'}
        destination = Path(chosen)
        if destination.suffix.lower() != '.json':
            destination = Path(str(destination) + '.json')
        manager.save_json(destination, profile)
        return {'message': 'Profile exported. Share this JSON file; it contains mod references only.'}
    if action == 'profile-delete':
        if any(job.get('profile_id') == body['id'] and job['state'] in (
                'waiting', 'downloading', 'verifying', 'installing', 'waiting_game') for job in DOWNLOADS.values()):
            raise ValueError('Finish or cancel this profile download before deleting the profile.')
        path, store = profile_store(config)
        profile_record(config, body['id'])
        store['profiles'] = [item for item in store['profiles'] if item['id'] != body['id']]
        if store['active'] == body['id']:
            store['active'] = None
        manager.save_json(path, store)
        return {'message': 'Profile removed. Installed mods are unchanged.'}
    if action == 'profile-compare':
        return compare_profile(config, body['id'])
    if action == 'profile-apply':
        return apply_profile(config, body)
    if action == 'profile-download':
        return download_profile_mod(config, body['id'], body['key'])
    if action == 'steam-workshop':
        item_id = integrations._id(body.get('workshop_id'))
        webbrowser.open(f'steam://url/CommunityFilePage/{item_id}')
        return {'message': 'Workshop item opened in Steam. Subscribe, wait for Steam to finish downloading, then refresh the profile status.'}
    if action == 'launch':
        if not (game / 'GraveyardKeeper2.exe').exists():
            raise ValueError('Set your game location first.')
        webbrowser.open('steam://rungameid/4358690')
        return {'message': 'Launch requested through Steam.'}
    if action == 'steam':
        webbrowser.open('steam://downloads')
        return {'message': 'Steam Downloads opened. Steam handles Workshop updates; launch the game to approve new loader versions.'}
    if action == 'toggle':
        manager.ensure_game_stopped()
        row = row_by_id(body['id'])
        if not row:
            raise ValueError('Mod no longer exists. Refresh the inventory.')
        if row['source'] == 'GK2MT':
            package_manager(config).set_enabled(row['id'], body['enabled'])
        else:
            changed = inventory.toggle(game, workshop, row, bool(body['enabled']))
            if changed.get('approval_required'):
                PREVIEW = None
                return {'approval_required': True, 'mod_name': row['name'],
                        'message': row['name'] + ' is unblocked. Launch the game and approve it in the Workshop loader to finish enabling it.'}
            if body['enabled'] and changed.get('loader_kind') == 'workshop' and changed.get('trust_state') == 'yes':
                PREVIEW = None
                return {'message': row['name'] + ' is enabled for the next game launch. Its previous approval is kept; the loader will ask again only if the files changed.'}
        PREVIEW = None
        return {'message': 'Mod state saved. Workshop changes take effect when the game next starts.'}
    if action == 'uninstall-preview':
        return preview_uninstall(config, body['id'])
    if action == 'uninstall':
        return apply_uninstall(config, body['token'])
    if action == 'metadata':
        row = row_by_id(body['id'])
        if not row:
            raise ValueError('Unknown mod.')
        changes = {k: str(body[k]).strip() for k in ('name', 'version', 'category') if k in body}
        for field in ('nexus_mod_id', 'file_id'):
            if field in body:
                changes[field] = int(body[field]) if body[field] else None
                if changes[field] is not None and changes[field] < 1:
                    raise ValueError('Nexus IDs must be positive numbers.')
        if row['source'] == 'GK2MT':
            lib = package_manager(config)
            next(p for p in lib.state['packages'] if p['id'] == row['id']).update(changes)
            manager.save_json(lib.state_path, lib.state)
        else:
            config.setdefault('metadata', {}).setdefault(row['id'], {}).update(changes)
            manager.save_json(DATA / 'settings.json', config)
        UPDATES = None
        return {'message': 'Mod details saved.'}
    if action == 'stage':
        return stage_import(config, body['path'], body.get('metadata'))
    if action == 'stage-folder':
        folder = Path(config['import_folder'])
        if folder == Path.home() / 'Downloads/GK2MT':
            folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_dir():
            raise ValueError('Set an existing imports folder in Locations.')
        files = sorted(folder.glob('*.zip'))
        if len(files) > 100:
            raise ValueError('Use an imports folder with 100 ZIPs or fewer.')
        staged, errors = [], []
        for file in files:
            try:
                staged.append(stage_import(config, file))
            except Exception as exc:
                errors.append(file.name + ': ' + str(exc))
        return {'packages': staged, 'errors': errors}
    if action == 'install':
        result = install_import(config, body['token'], body.get('metadata'), body.get('acknowledge_duplicates'),
                                body.get('choices'), body.get('digest'))
        PREVIEW = PROFILE_PREVIEW = None
        return result
    if action == 'install-preview':
        return preview_imports(config, body.get('tokens'), body.get('metadata'))
    if action == 'install-batch':
        result = install_import_batch(config, body.get('tokens'), body.get('choices'), body.get('digest'),
                                      body.get('acknowledge_duplicates'), body.get('metadata'))
        PREVIEW = PROFILE_PREVIEW = None
        return result
    if action == 'rules':
        package_manager(config).set_rules(body['rules'])
        PREVIEW = None
        return {'message': 'Rules saved and imported packages redeployed.'}
    if action == 'setup':
        if not (game / 'GraveyardKeeper2.exe').is_file():
            raise ValueError('Set a valid game folder before setup.')
        if not body.get('archive') and body.get('file_id') is None and all(
                (game / name).is_file() and inventory._safe(game / name, game.absolute())
                for name in BEPINEX_FILES):
            return {'requires_download': False, 'already_installed': True, 'files': 0,
                    'message': 'Existing BepInEx detected. Your installation was kept.'}
        manager.ensure_game_stopped()
        overlap = foundation_overlap(package_manager(config))
        if overlap:
            raise ValueError(overlap)
        result = integrations.setup_installer(game, DATA, key=NEXUS_KEY,
                                              archive=body.get('archive'), file_id=body.get('file_id'))
        if not result.get('requires_download'):
            record_foundation(config, result)
            UPDATES = None
        PREVIEW = None
        return result
    if action == 'nexus-forget':
        nexus_credentials.forget(DATA / 'nexus-key.bin')
        NEXUS_KEY = NEXUS_ERROR = ''
        NEXUS_SESSION += 1
        UPDATES = None
        return {'message': 'Saved Nexus key removed. Nexus disconnected.'}
    if action == 'nexus-connect':
        key = str(body.pop('key', '')).strip()
        if not key:
            raise ValueError('Paste a Nexus API key to connect. Use Forget key to disconnect.')
        result = check_nexus(config, key)
        if body.get('remember_key', True) is True:
            nexus_credentials.save(DATA / 'nexus-key.bin', key)
        else:
            nexus_credentials.forget(DATA / 'nexus-key.bin')
        NEXUS_KEY = key
        NEXUS_ERROR = ''
        NEXUS_SESSION += 1
        UPDATES = result
        return result
    if action == 'updates':
        if not NEXUS_KEY:
            raise ValueError('Connect a Nexus personal API key first. Steam manages Workshop updates in Steam Downloads.')
        UPDATES = check_nexus(config, NEXUS_KEY)
        return UPDATES
    if action in ('update', 'download-updates'):
        return apply_updates(config, body['id'] if action == 'update' else None)
    if action == 'update-archive-preview':
        return preview_update_archive(config, body['id'], body['path'])
    if action == 'update-archive':
        return apply_update_archive(config, body['token'])
    if action == 'download-panel':
        return start_download_panel(config, body['id'])
    if action == 'nexus-browser':
        return start_download_panel(config)
    if action == 'download-panel-cancel':
        return cancel_download_panel(body['id'])
    if action == 'download-panel-retry':
        return retry_download_panel(body['id'])
    if action == 'download-panel-approve':
        return approve_download_panel(body['id'], body.get('acknowledge_duplicates'))
    if action == 'deck-probe':
        connection_settings = remember_deck_address(config, body.get('deck', config['deck']))
        return deck_ssh.probe(connection_settings, DATA / 'deck-known-hosts')
    if action == 'deck-connect':
        disconnect_deck('Connecting to the Deck…')
        try:
            connection_settings = remember_deck_address(config, body.get('deck', config['deck']))
            password = body.get('password', '')
            if not isinstance(password, str):
                raise ValueError("Enter the Deck's system password.")
            remember = body.get('remember_password') is True
            if remember and not password:
                password = saved_deck_password(connection_settings)
            DECK_SESSION = deck_ssh.connect(connection_settings, password,
                                            DATA / 'deck-known-hosts', expected_key=body.get('expected_key'))
            discovery = deck.discover_remote(connection_settings, DECK_SESSION)
            if discovery.get('found'):
                connection_settings.update(game_path=discovery['game_path'], workshop_path=discovery['workshop_path'])
                connection_settings = deck._settings(connection_settings)
            else:
                connection_settings.update(game_path='', workshop_path='')
            config['deck'] = connection_settings
            manager.save_json(DATA / 'settings.json', config)
            credential_warning = ''
            try:
                if remember and password and body.get('password'):
                    value = json.dumps({'identity': list(deck_ssh._identity(connection_settings)[:3]),
                                        'password': password})
                    nexus_credentials.save(DATA / 'deck-password.bin', value, label='Deck password')
                elif not remember:
                    nexus_credentials.forget(DATA / 'deck-password.bin', label='Deck password')
            except (RuntimeError, ValueError):
                credential_warning = ('Connected, but the password preference could not be saved. '
                                      'Use Forget password or enter the password again next time.')
            DECK_MESSAGE = 'Connected to your Deck.'
            return {'connected': True, 'deck': connection_settings, 'discovery': discovery,
                    'password_saved': deck_connection(config)['password_saved'], 'credential_warning': credential_warning}
        except Exception:
            disconnect_deck('Could not connect. Check the address, password, and that SSH is running on the Deck.')
            raise
        finally:
            body.pop('password', None)
            password = ''
    if action == 'deck-forget-password':
        nexus_credentials.forget(DATA / 'deck-password.bin', label='Deck password')
        return {'message': 'Saved Deck password removed. The current connection stays active.'}
    if action == 'deck-disconnect':
        disconnect_deck()
        return {'connected': False, 'message': DECK_MESSAGE}
    if action == 'deck-sync-options':
        options = deck.normalize_options(body.get('options'), allow_empty=True)
        if options != config['deck_sync_options']:
            PREVIEW = None
            config['deck_sync_options'] = options
            manager.save_json(DATA / 'settings.json', config)
        return {'options': options, 'message': 'Sync selection saved. Compare again to review these changes.'}
    if action in ('deck-proton-status', 'deck-proton-setup'):
        target = deck._settings(body.get('deck', config['deck']), require_paths=False)
        changed = target != config['deck']
        install = action == 'deck-proton-setup'
        if changed or install:
            PREVIEW = None
        config['deck'] = target
        connection = require_deck(config)
        if not config['deck']['game_path']:
            raise ValueError('Choose the Deck game folder or reconnect to find it automatically.')
        if changed or install:
            manager.save_json(DATA / 'settings.json', config)
        return deck.configure_proton(config['deck'], connection, install=install)
    if action == 'deck-preview':
        options = deck.normalize_options(body.get('options', config['deck_sync_options']))
        connection_settings = deck._settings(body.get('deck', config['deck']))
        config['deck'] = connection_settings
        config['deck_sync_options'] = options
        connection = require_deck(config)
        manager.ensure_game_stopped()
        PREVIEW = None
        manager.save_json(DATA / 'settings.json', config)
        PREVIEW = deck.preview(game, workshop, config['deck'], connection=connection, options=options)
        return PREVIEW
    if action == 'deck-sync':
        options = deck.normalize_options(body.get('options', config['deck_sync_options']))
        connection = require_deck(config)
        manager.ensure_game_stopped()
        if not PREVIEW or body.get('digest') != PREVIEW['digest']:
            raise ValueError('Compare PC and Deck again before syncing.')
        if options != deck.normalize_options(PREVIEW.get('options')):
            PREVIEW = None
            raise ValueError('Your sync selection changed. Compare PC and Deck again before syncing.')
        digest = PREVIEW['digest']
        PREVIEW = None
        return deck.sync(game, workshop, config['deck'], digest, connection=connection, options=options)
    raise ValueError('Unknown action.')


def file_dialog(kind):
    import webview
    if WINDOW is None:
        raise ValueError('Open GK2MT to choose a file or folder.')
    file_types = ('ZIP mod archive (*.zip)',) if kind == 'zip' else (
        'GK2MT mod profile (*.json)',) if kind.startswith('profile') else ()
    dialog_type = webview.FileDialog.SAVE if kind == 'profile-export' else (
        webview.FileDialog.OPEN if kind in ('zip', 'profile') else webview.FileDialog.FOLDER)
    paths = WINDOW.create_file_dialog(dialog_type, file_types=file_types,
                                     save_filename='GK2MT-profile.json' if kind == 'profile-export' else '')
    return {'path': paths[0] if paths else ''}


def shutdown():
    global NEXUS_KEY
    for job in DOWNLOADS.values():
        job['_stop'].set()
        stop_download_panel(job)
    disconnect_deck()
    NEXUS_KEY = ''


def main(argv=None):
    parser = argparse.ArgumentParser(description='Graveyard Keeper 2 Mod Toolkit')
    parser.add_argument('--nexus-panel', nargs=3, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.nexus_panel is not None:
        return nexus_panel.main(args.nexus_panel)
    import desktop
    restore_nexus()
    desktop.run(sys.modules[__name__])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if getattr(sys, 'frozen', False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), 'GK2MT could not start', 0x10)
        else:
            raise
