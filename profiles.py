"""Portable mod selections only; profiles never contain mod files or local settings."""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from inventory import LOADERS
from integrations import _version as _numeric_version
from manager import safe_path

FORMAT = 'GK2MT-profile'
GAME_ID = 4358690
DISABLED = '.gk2mt-disabled'
UNKNOWN = {'', 'unknown', 'unknown version', 'n/a', 'none', 'null', '?', '-', '--', 'unavailable', 'not detected'}


def _text(value, label, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum
            or re.search(r'[\x00-\x1f\x7f-\x9f]', value)):
        raise ValueError(f'{label} must be readable text of at most {maximum} characters.')
    return value.strip()


def _id(value, label):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]{0,19}', str(value)):
        raise ValueError(f'{label} must be a positive numeric ID.')
    return int(value)


def _dlls(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 500:
        raise ValueError('A manual mod needs between 1 and 500 DLL identities.')
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Invalid manual DLL identity.')
        name = _text(item.get('name'), 'DLL name', 255).casefold()
        digest = item.get('sha256')
        if (not name.endswith('.dll') or len(name) <= 4 or any(p in name for p in ('/', '\\', ':'))
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest)):
            raise ValueError('Manual DLL identities require a basename and SHA256 hash.')
        result.append({'name': name, 'sha256': digest.lower()})
    result.sort(key=lambda item: (item['name'], item['sha256']))
    if len({(item['name'], item['sha256']) for item in result}) != len(result):
        raise ValueError('A manual mod contains duplicate DLL identities.')
    return result


def _key(entry):
    if entry['source'] == 'Nexus':
        return 'nexus:' + str(entry['nexus_mod_id'])
    if entry['source'] == 'Steam':
        return 'steam:' + str(entry['workshop_id'])
    identity = json.dumps(entry['dlls'], sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'manual:' + hashlib.sha256(identity).hexdigest()


def validate(value):
    """Reject malformed identities and return only allowlisted shareable fields."""
    if (not isinstance(value, dict) or value.get('format') != FORMAT
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or type(value.get('game_id')) is not int or value['game_id'] != GAME_ID):
        raise ValueError('Choose a GK2MT profile for Graveyard Keeper 2 (schema version 1).')
    name = _text(value.get('name'), 'Profile name', 100)
    mods = value.get('mods')
    if not isinstance(mods, list) or len(mods) > 500:
        raise ValueError('A profile must contain a list of at most 500 mods.')
    entries = []
    for raw in mods:
        if not isinstance(raw, dict) or raw.get('source') not in ('Steam', 'Nexus', 'Manual'):
            raise ValueError('Each mod must use the Steam, Nexus or Manual source.')
        if type(raw.get('enabled')) is not bool:
            raise ValueError('Each mod needs a true or false enabled state.')
        entry = {'name': _text(raw.get('name'), 'Mod name', 200), 'source': raw['source'],
                 'version': _text(raw.get('version', 'Unknown'), 'Mod version', 100), 'enabled': raw['enabled']}
        if entry['source'] == 'Nexus':
            entry['nexus_mod_id'] = _id(raw.get('nexus_mod_id'), 'Nexus mod ID')
            if 'file_id' in raw and raw['file_id'] is not None:
                entry['file_id'] = _id(raw['file_id'], 'Nexus file ID')
        elif entry['source'] == 'Steam':
            entry['workshop_id'] = _id(raw.get('workshop_id'), 'Steam Workshop ID')
        else:
            entry['dlls'] = _dlls(raw.get('dlls'))
        entry['key'] = _key(entry)
        if 'key' in raw and raw['key'] != entry['key']:
            raise ValueError('The profile mod key does not match its source identity.')
        # The common BepInEx foundation is managed separately from mod selections.
        if entry.get('nexus_mod_id') != 48:
            entries.append(entry)
    return {'format': FORMAT, 'schema_version': 1, 'game_id': GAME_ID, 'name': name, 'mods': entries}


def _source(row):
    if row.get('workshop_id') or row.get('source') in ('Steam', 'Steam Workshop'):
        return 'Steam'
    if row.get('nexus_mod_id') or row.get('source') == 'Nexus':
        return 'Nexus'
    return 'Manual'


def _version(row):
    value = row.get('display_version')
    if value is None:
        package = row.get('package_version')
        value = package if not row.get('package_version_stale') and isinstance(package, str) and package.strip().casefold() not in UNKNOWN else row.get('version')
    return str(value or 'Unknown').strip() or 'Unknown'


def same_version(actual, wanted):
    """Treat v1.0 and 1.0.0 alike; preserve named-release distinctions."""
    left, right = _numeric_version(actual), _numeric_version(wanted)
    if left is not None and right is not None:
        return left == right
    return str(actual or '').strip().casefold() == str(wanted or '').strip().casefold()


def _normal(row):
    names = {PurePosixPath(str(path).replace('\\', '/').removesuffix(DISABLED)).name.casefold()
             for path in row.get('paths', [])}
    names.update(item.get('name', '').casefold() for item in row.get('_profile_dlls', []) if isinstance(item, dict))
    return (row.get('can_toggle') is not False and not names.intersection(LOADERS)
            and not (_source(row) == 'Nexus' and str(row.get('nexus_mod_id')) == '48'))


def _manual_dlls(row, game):
    if '_profile_dlls' in row:
        return _dlls(row['_profile_dlls'])
    result = []
    for relative in row.get('paths', []):
        active = str(relative).removesuffix(DISABLED)
        if not active.casefold().endswith('.dll'):
            continue
        path = safe_path(game, relative)
        if not path.is_file():
            path = safe_path(game, active if str(relative).endswith(DISABLED) else active + DISABLED)
        if not path.is_file():
            raise ValueError('A manual DLL is missing; refresh the mod inventory before using profiles.')
        with path.open('rb') as stream:
            result.append({'name': PurePosixPath(active.replace('\\', '/')).name,
                           'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
    return _dlls(result)


def _entry(row, game):
    source = _source(row)
    result = {'name': row.get('name'), 'source': source, 'version': _version(row), 'enabled': bool(row.get('enabled'))}
    if source == 'Nexus':
        result['nexus_mod_id'] = row.get('nexus_mod_id')
        if row.get('file_id') is not None:
            result['file_id'] = row['file_id']
    elif source == 'Steam':
        result['workshop_id'] = row.get('workshop_id')
    else:
        result['dlls'] = _manual_dlls(row, game)
    return validate({'format': FORMAT, 'schema_version': 1, 'game_id': GAME_ID, 'name': 'Selection', 'mods': [result]})['mods'][0]


def capture(name, rows, game):
    """Capture current mod states. BepInEx, configs, rules and secrets stay local."""
    return validate({'format': FORMAT, 'schema_version': 1, 'game_id': GAME_ID, 'name': name,
                     'mods': [_entry(row, Path(game)) for row in rows if _normal(row)]})


def compare(profile, rows, game):
    """Plan exact identity matches without installing files or changing any mod."""
    profile = validate(profile)
    entries, changes, missing, mismatches, blockers = [], [], [], [], []
    installed, desired = {}, {}
    for entry in profile['mods']:
        desired.setdefault(entry['key'], []).append(entry)
    normal = [row for row in rows if _normal(row)]
    for row in normal:
        try:
            entry = _entry(row, Path(game))
            installed.setdefault(entry['key'], []).append((row, entry))
        except (OSError, ValueError):
            blockers.append(f"Cannot safely identify installed mod '{row.get('name', 'Unnamed mod')}'. Refresh its files first.")
    for key, group in desired.items():
        matches = installed.get(key, [])
        for entry in group:
            result = dict(entry, status='missing', installed_id=None, installed_version=None,
                          installed_file_id=None, version_mismatch=False,
                          reason='This source and mod identity is not installed.')
            if len(group) > 1 or len(matches) > 1:
                result.update(status='ambiguous', reason='This source identity has multiple copies; remove the duplicate before applying.')
                blockers.append(f"Ambiguous profile mod: {entry['name']} ({entry['source']}).")
            elif matches:
                row, current = matches[0]
                different_file = (entry['source'] == 'Nexus' and entry.get('file_id') is not None
                                  and current.get('file_id') is not None and entry['file_id'] != current['file_id'])
                wanted_version = entry['version'].casefold() not in UNKNOWN
                current_version = current['version'].casefold() not in UNKNOWN
                unverified = (wanted_version and not current_version or entry['source'] == 'Nexus'
                              and entry.get('file_id') is not None and current.get('file_id') is None)
                mismatch = (entry['enabled'] and (unverified or different_file or wanted_version
                            and current_version and not same_version(current['version'], entry['version'])))
                result.update(status='installed', installed_id=row['id'], installed_version=current['version'],
                              installed_file_id=current.get('file_id'), version_mismatch=mismatch, reason='')
                if mismatch:
                    result['reason'] = ('The installed release cannot be verified against this profile; review before applying.'
                                        if unverified else 'The installed Nexus file variant differs from this profile; review before applying.'
                                        if different_file else 'The installed version differs from this profile; review before applying.')
                    mismatches.append(result)
                if bool(row.get('enabled')) != entry['enabled']:
                    if row.get('can_toggle') is False:
                        result['reason'] = 'This mod cannot be switched safely by GK2MT.'
                        blockers.append(f"Cannot change protected mod: {entry['name']}.")
                    else:
                        changes.append({'id': row['id'], 'name': row['name'], 'enabled': entry['enabled'], 'source': entry['source']})
            else:
                missing.append(result)
                if entry['enabled']:
                    blockers.append(f"Install missing {entry['source']} mod: {entry['name']}.")
            entries.append(result)
    for key, group in installed.items():
        if len(group) > 1 and key not in desired:
            blockers.append(f"Multiple installed copies share one source identity: {group[0][0]['name']}.")
        if key not in desired:
            for row, current in group:
                if row.get('enabled') and row.get('can_toggle') is not False:
                    changes.append({'id': row['id'], 'name': row['name'], 'enabled': False, 'source': current['source']})
    return {'entries': entries, 'changes': changes, 'missing': missing, 'mismatches': mismatches,
            'blockers': list(dict.fromkeys(blockers))}
