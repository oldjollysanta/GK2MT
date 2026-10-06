"""User-initiated Nexus updates and verified, missing-only GitHub setup."""
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path

API = 'https://api.nexusmods.com/v1'
GAME = 'graveyardkeeper2'
SETUP_MOD = 48
SETUP_URL = f'https://www.nexusmods.com/{GAME}/mods/{SETUP_MOD}?tab=files'
HEADERS = {'User-Agent': 'GK2MT/0.1.2', 'Application-Name': 'GK2MT',
           'Application-Version': '0.1.2', 'Accept': 'application/json'}
ROOT_FILES = {'winhttp.dll', 'doorstop_config.ini', '.doorstop_version', 'changelog.txt'}
REQUIRED = ['winhttp.dll', 'doorstop_config.ini', '.doorstop_version',
            'BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll'] + [
                'BepInEx/core/' + name for name in (
                    'BepInEx.dll', 'BepInEx.Preloader.dll', 'BepInEx.Harmony.dll', '0Harmony.dll',
                    '0Harmony20.dll', 'HarmonyXInterop.dll', 'Mono.Cecil.dll', 'Mono.Cecil.Mdb.dll',
                    'Mono.Cecil.Pdb.dll', 'Mono.Cecil.Rocks.dll', 'MonoMod.RuntimeDetour.dll', 'MonoMod.Utils.dll')]
GAME_MARKERS = ['GraveyardKeeper2.exe', 'UnityPlayer.dll', 'GraveyardKeeper2_Data/Managed/Assembly-CSharp.dll']
WORKSHOP_ID = '3807346541'
WORKSHOP_TARGET = 'BepInEx/patchers/GK2_WorkshopLoader.dll'
WORKSHOP_BEPINEX_FILES = ('BepInEx/core/BepInEx.dll', 'BepInEx/core/BepInEx.Preloader.dll',
                         'winhttp.dll', 'doorstop_config.ini')
GITHUB_DOMAINS = ('github.com', 'githubusercontent.com')
BEPINEX_ASSET = ('https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/'
                 'BepInEx_win_x64_5.4.23.5.zip',
                 '82f9878551030f54657792c0740d9d51a09500eeae1fba21106b0c441e6732c4')
CONFIGURATION_ASSET = ('https://github.com/BepInEx/BepInEx.ConfigurationManager/releases/download/v19.0/'
                       'BepInEx.ConfigurationManager_BepInEx5_v19.0.zip',
                       'eed83f6e6accbdd0c3771f3061a6ae05b979b9b9b8a00dead69aabc1bf5fe950')
WORKSHOP_ASSET = ('https://github.com/Zoriten/-GK2-WorkshopLoader/releases/download/v1.0.0/'
                  'GK2_WorkshopLoader.dll',
                  'd35b54d52324b52b2a7fb811ce5a4c982ef28ca8ab1bc3c28163b4ac689a4d8b')
BEPINEX_UPSTREAM_FILES = (set(ROOT_FILES) | {name for name in REQUIRED if name.startswith('BepInEx/core/')} | {
    'BepInEx/core/' + name for name in ('0Harmony.xml', 'BepInEx.Harmony.xml', 'BepInEx.Preloader.xml',
                                     'BepInEx.xml', 'MonoMod.RuntimeDetour.xml', 'MonoMod.Utils.xml')})
CONFIGURATION_FILES = {'BepInEx/plugins/ConfigurationManager/' + name for name in
                       ('ConfigurationManager.dll', 'ConfigurationManager.xml',
                        'ConfigurationManagerAttributes.cs', 'LICENSE', 'README.md')}


def _workshop_dll(path):
    """Check a bounded Windows DLL image without loading or executing it."""
    size = path.stat().st_size
    if not 256 <= size <= 64 * 1024 * 1024:
        raise ValueError('The Workshop loader download is empty, incomplete or too large.')
    with path.open('rb') as stream:
        def read(length):
            value = stream.read(length)
            if len(value) != length:
                raise ValueError('The Workshop DLL download is incomplete.')
            return value
        dos = read(64)
        if dos[:2] != b'MZ':
            raise ValueError('The Workshop download is not a Windows DLL.')
        offset = struct.unpack_from('<I', dos, 60)[0]
        if not 64 <= offset <= size - 24:
            raise ValueError('The Workshop DLL header is incomplete.')
        stream.seek(offset)
        header = read(24)
        machine, sections = struct.unpack_from('<HH', header, 4)
        optional, flags = struct.unpack_from('<HH', header, 20)
        if (header[:4] != b'PE\0\0' or machine not in (0x14c, 0x8664) or not 1 <= sections <= 96
                or not flags & 0x2000 or not 96 <= optional <= 4096
                or offset + 24 + optional + 40 * sections > size):
            raise ValueError('The Workshop DLL header is invalid or incomplete.')
        if struct.unpack('<H', read(2))[0] not in (0x10b, 0x20b):
            raise ValueError('The Workshop DLL image format is unsupported.')
        stream.seek(offset + 24 + optional)
        for _ in range(sections):
            section = read(40)
            length, start = struct.unpack_from('<II', section, 16)
            if length and (start < offset + 24 + optional + 40 * sections or start + length > size):
                raise ValueError('The Workshop DLL payload is incomplete.')


def _recognized_loaders(game, names=None):
    import inventory
    names = inventory.LOADERS if names is None else names
    copies, count = [], 0
    def unreadable(error):
        raise ValueError('The plugin or patcher tree cannot be read safely. Review it before loader setup.') from error
    for area in ('patchers', 'plugins'):
        root = game / 'BepInEx' / area
        if not inventory._safe(root, game):
            raise ValueError('A plugin or patcher folder contains a link. Review it before loader setup.')
        if not root.exists():
            continue
        for base, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
            count += len(dirs) + len(files)
            if count > 20000 or any(inventory._linked(Path(base) / name) for name in dirs):
                raise ValueError('The plugin or patcher tree is linked or too large to verify safely.')
            for name in files:
                if name.casefold().removesuffix(inventory.DISABLED) in names:
                    path = Path(base) / name
                    if not inventory._safe(path, game):
                        raise ValueError('A Workshop loader contains a linked path. Review it before setup.')
                    copies.append(path)
    return copies


def workshop_loader_setup(game, workshop, data_dir, install=False):
    """Install the pinned loader only when no recognized active or disabled copy exists."""
    import inventory
    from manager import copy_atomic, digest, ensure_game_stopped, safe_path

    game, workshop = Path(game).absolute(), Path(workshop).absolute()
    target = game / WORKSHOP_TARGET
    status = {'state': 'blocked', 'installed': False, 'can_install': False, 'workshop_id': WORKSHOP_ID,
              'message': '', 'source_path': WORKSHOP_ASSET[0], 'target_path': str(target), 'files': 0,
              'source': 'GitHub', 'version': '1.0.0'}
    def result(state, message, **values):
        return status | {'state': state, 'message': message} | values
    if not inventory._safe(game, Path(game.anchor)) or (os.path.lexists(workshop)
            and not inventory._safe(workshop, Path(workshop.anchor))):
        return result('blocked', 'Choose physical game and Workshop folders; linked folders cannot be used for loader setup.')
    if not inventory._safe(game / 'GraveyardKeeper2.exe', game) or not (game / 'GraveyardKeeper2.exe').is_file():
        return result('blocked', 'Set the Graveyard Keeper 2 game location before installing its Workshop loader.')
    for relative in WORKSHOP_BEPINEX_FILES:
        marker = game / relative
        if not inventory._safe(marker, game):
            return result('blocked', 'A BepInEx setup file contains a link. Select its physical game folder.')
    if not all((game / p).is_file() for p in WORKSHOP_BEPINEX_FILES):
        return result('missing_bepinex', 'Install BepInEx first, then refresh the Workshop loader setup.')
    if not inventory._safe(target, game) or not inventory._safe(Path(str(target) + inventory.DISABLED), game):
        return result('blocked', 'The Workshop loader destination contains a linked path.')
    # BepInEx recursively searches both areas. Preserve misplaced and disabled loaders too.
    try:
        recognized = _recognized_loaders(game)
    except ValueError as error:
        return result('blocked', str(error))
    active = [p for p in recognized if not p.name.casefold().endswith(inventory.DISABLED)]
    if len(active) > 1:
        return result('blocked', 'Multiple Workshop loaders are installed. Review them in My mods before changing loaders.')
    if active:
        existing = safe_path(game, active[0].relative_to(game).as_posix())
        if existing.relative_to(game).as_posix().casefold().startswith('bepinex/plugins/'):
            return result('blocked', 'An existing Workshop loader is in the plugins folder and was preserved. '
                          'Move it into BepInEx/patchers or resolve it in My mods before setup.', target_path=str(existing))
        try:
            _workshop_dll(existing)
        except (OSError, ValueError):
            return result('blocked', 'The installed Workshop loader is incomplete. Review it in My mods before replacing it.')
        checksum = digest(existing)
        name = inventory.LOADERS[existing.name.casefold()][1]
        return result('installed', name + ' is already installed and was preserved.', installed=True,
                      target_path=str(existing), hash=checksum, source='Existing installation',
                      version='1.0.0' if checksum == WORKSHOP_ASSET[1] else 'Unknown')
    disabled = [p for p in recognized if p.name.casefold().endswith(inventory.DISABLED)]
    if disabled:
        return result('disabled', 'The Workshop loader is disabled. Enable it in My mods when you want to use it.')
    if os.path.lexists(target):
        return result('blocked', 'The loader destination already exists. Review that file before installing.')
    checksum = WORKSHOP_ASSET[1]
    ready = result('ready', 'Install Workshop Loader 1.0.0 from GitHub. No account or Steam subscription is needed for setup.',
                   can_install=True, hash=checksum)
    if not install:
        return ready
    ensure_game_stopped()
    source = _github_asset(data_dir, WORKSHOP_ASSET, maximum=64 * 1024 * 1024)
    _workshop_dll(source)
    # Downloads can take time. A newly installed/disabled loader must win this race.
    recheck = workshop_loader_setup(game, workshop, data_dir)
    if recheck['state'] != 'ready':
        return recheck
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix='.gk2mt-workshop-', suffix='.tmp', dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    published = False
    try:
        copy_atomic(source, temporary)
        _workshop_dll(temporary)
        if digest(temporary) != checksum or digest(source) != checksum:
            raise ValueError('The Workshop loader download changed while being copied. Refresh and try again.')
        if (not inventory._safe(source, Path(data_dir).absolute()) or not inventory._safe(target, game)
                or os.path.lexists(Path(str(target) + inventory.DISABLED))):
            raise ValueError('Workshop loader paths changed. Refresh before installing.')
        recheck = workshop_loader_setup(game, workshop, data_dir)
        if recheck['state'] != 'ready':
            return recheck
        ensure_game_stopped()
        # Creating a hardlink publishes complete bytes atomically and cannot overwrite another file.
        os.link(temporary, target)
        published = True
        if digest(target) != checksum:
            raise ValueError('The Workshop loader copy could not be verified.')
        return result('installed', 'Workshop loader installed. It will ask for mod approvals on the next game launch.',
                      installed=True, hash=checksum, files=1)
    except BaseException as error:
        if published:
            if target.is_file() and os.path.samefile(target, temporary) and digest(target) == checksum:
                target.unlink()
            else:
                raise RuntimeError('Loader setup stopped, but another program changed the new loader file. Review ' + str(target)) from error
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _trusted_url(url, domains):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username
            and not parsed.password and any(parsed.hostname == d or
            (parsed.hostname or '').endswith('.' + d) for d in domains))


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, domains):
        self.domains = domains

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl, self.domains):
            raise ValueError('Download redirected outside the trusted HTTPS service.')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _json(url, key=''):
    headers = dict(HEADERS)
    if key:
        headers['apikey'] = key
    request = urllib.request.Request(url, headers=headers)
    # API redirects are unnecessary; never forward an API key to a redirected host.
    opener = urllib.request.build_opener(_Redirects(()))
    try:
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read(16 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        labels = {401: 'API key rejected', 403: 'access denied', 429: 'rate limit reached; try later'}
        raise RuntimeError(f'API HTTP {error.code}: {labels.get(error.code, "request failed")}') from None


def steam_workshop_titles(ids: list[str]) -> dict[str, str]:
    """Read public GK2 Workshop names without Steam or Nexus credentials."""
    if any(not isinstance(item, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', item)
           or int(item) > 18446744073709551615 for item in ids):
        raise ValueError('Steam Workshop IDs must be positive 64-bit integers.')
    ids = list(dict.fromkeys(ids))
    titles = {}
    # https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage#GetPublishedFileDetails
    opener = urllib.request.build_opener(_Redirects(()))
    for start in range(0, len(ids), 100):
        batch = ids[start:start + 100]
        body = {'itemcount': str(len(batch))}
        body.update({f'publishedfileids[{i}]': item for i, item in enumerate(batch)})
        request = urllib.request.Request(
            'https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/',
            data=urllib.parse.urlencode(body).encode('ascii'),
            headers={'User-Agent': 'GK2MT/0.1.1', 'Accept': 'application/json',
                     'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with opener.open(request, timeout=10) as response:
                content = response.read(2 * 1024 * 1024 + 1)
            if len(content) > 2 * 1024 * 1024:
                raise ValueError('Response too large.')
            result = json.loads(content)['response']
            if result.get('result') != 1 or not isinstance(result.get('publishedfiledetails'), list):
                raise ValueError('Invalid Steam response.')
            for item in result['publishedfiledetails']:
                if not isinstance(item, dict):
                    continue
                workshop_id, title = item.get('publishedfileid'), item.get('title')
                if (workshop_id in batch and item.get('result') == 1
                        and item.get('consumer_app_id') == 4358690
                        and isinstance(title, str) and 0 < len(title.strip()) <= 512
                        and not re.search(r'[\x00-\x1f\x7f-\x9f]', title)):
                    titles[workshop_id] = title.strip()
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise RuntimeError('Steam Workshop names are unavailable. Try Rescan again later.') from None
    return titles


def _id(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise ValueError('Nexus mod and file IDs must be positive integers.')
    return int(value)


def _account(key):
    if not isinstance(key, str) or not key.strip() or '\n' in key or '\r' in key:
        raise ValueError('Enter your personal Nexus Mods API key in Updates.')
    data = _json(API + '/users/validate.json', key.strip())
    return {'name': str(data.get('name', 'Nexus user')), 'premium': data.get('is_premium') is True}


def _version(value):
    value = str(value).strip().lower().lstrip('v')
    if not re.fullmatch(r'\d+(?:\.\d+)*', value):
        return None
    parts = [int(x) for x in value.split('.')]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _known_version(value):
    return isinstance(value, str) and value.strip().casefold() not in (
        '', 'unknown', 'unknown version', 'n/a', 'none', 'null', '?', '-', '--', 'unavailable', 'not detected')


def installed_version(row):
    """Ignore replaced Vortex files while retaining genuine archive/component versions."""
    package = row.get('package_version')
    if row.get('nexus_mod_id') == SETUP_MOD:
        # ConfigurationManager's plugin version does not identify the BepInEx bundle.
        return str(package) if not row.get('package_version_stale') and _known_version(package) else 'Unknown'
    if row.get('package_version_stale'):
        return str(row.get('version') or 'Unknown')
    return str(package if _known_version(package) else row.get('version') or 'Unknown')


def _candidate(payload, installed_file, installed_version=None):
    files = {int(f['file_id']): f for f in payload.get('files', [])}
    active = [f for f in files.values() if f.get('category_id') in (1, 2, 3)]
    if installed_file:
        current = _id(installed_file)
        visited = set()
        while current not in visited:
            visited.add(current)
            following = {int(u['new_file_id']) for u in payload.get('file_updates', [])
                         if int(u['old_file_id']) == current and int(u['new_file_id']) != current}
            if not following:
                result = files.get(current)
                if result and result in active:
                    return result, []
                # Some authors archive old releases without publishing update links.
                # Infer a replacement only for one active MAIN with the same name and newer versions.
                if result and result.get('category_id') in (4, 7) and len(active) == 1:
                    latest = active[0]
                    previous_name, latest_name = result.get('name'), latest.get('name')
                    old, local, new = (_version(value) for value in
                                       (result.get('version'), installed_version, latest.get('version')))
                    if (latest.get('category_id') == 1 and isinstance(previous_name, str)
                            and isinstance(latest_name, str) and previous_name.strip()
                            and ' '.join(previous_name.split()).casefold() == ' '.join(latest_name.split()).casefold()
                            and all(version is not None for version in (old, local, new))
                            and new > old and new > local):
                        return latest, []
                return None, active
            if len(following) != 1:
                return None, [f for f in active if int(f['file_id']) in following] or active
            current = following.pop()
        raise ValueError('Nexus file update history contains a cycle.')
    main = [f for f in active if f.get('category_id') == 1]
    return (main[0], []) if len(main) == 1 else (None, active)


class _ChangelogText(HTMLParser):
    """Display Nexus release notes as text, never as executable HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'iframe', 'object', 'template', 'head'):
            self.hidden += 1
        if not self.hidden and tag in ('br', 'p', 'div', 'li', 'ul', 'ol', 'h1', 'h2', 'h3'):
            self.parts.append('\n' + ('• ' if tag == 'li' else ''))

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'iframe', 'object', 'template', 'head'):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ('p', 'div', 'li', 'ul', 'ol', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _changelog(candidate):
    # Nexus's file response already matches changelog_html to this file's version.
    # https://github.com/Nexus-Mods/node-nexus-api/blob/master/src/types.ts (IFileInfo)
    result = {'changelog': '', 'changelog_version': candidate.get('version') or ''}
    content = candidate.get('changelog_html')
    if content is None:
        return result
    if not isinstance(content, str):
        return result | {'changelog_error': 'Changelog unavailable: Nexus returned an unexpected format.'}
    parser = _ChangelogText()
    try:
        parser.feed(content)
        parser.close()
    except (ValueError, TypeError, AssertionError):
        return result | {'changelog_error': 'Changelog unavailable: Nexus returned an unexpected format.'}
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', ''.join(parser.parts))
    result['changelog'] = '\n'.join(line for part in text.splitlines() if (line := ' '.join(part.split())))
    return result


def _mod_access_error(mod_id):
    return (f'Nexus denied access to mod #{mod_id} (HTTP 403). It may be hidden, restricted, or unavailable; '
            'check the mod ID and access to its Nexus page. Installed files are unchanged.')


def nexus_check(key: str, rows: list[dict], links: dict) -> dict:
    """Prefer declared successors; infer only an unambiguous newer replacement."""
    account = _account(key)
    result = {'updates': [], 'errors': [], 'metadata_errors': [], 'categories': {}, 'account': account}
    cache, mod_metadata, category_names = {}, {}, None
    for row in rows:
        link = {name: row.get(name) for name in ('nexus_mod_id', 'file_id')}
        link.update(links.get(row['id'], {}))
        if not link.get('nexus_mod_id'):
            continue
        try:
            mod_id = _id(link['nexus_mod_id'])
            try:
                if category_names is None:
                    category_names = {}
                    game = _json(f'{API}/games/{GAME}.json', key.strip())
                    category_names = {str(c['category_id']): c['name'] for c in game.get('categories', [])
                                      if isinstance(c.get('name'), str) and c['name'].strip()}
            except (ValueError, OSError, RuntimeError, KeyError, TypeError, AttributeError) as error:
                result['metadata_errors'].append(f'Nexus categories: {error}')
                if any(f'HTTP {code}' in str(error) for code in (401, 403, 429)):
                    break
            try:
                if mod_id not in mod_metadata:
                    mod_metadata[mod_id] = None  # Do not repeat failed metadata requests for duplicate rows.
                    metadata = _json(f'{API}/games/{GAME}/mods/{mod_id}.json', key.strip())
                    if not isinstance(metadata, dict):
                        raise ValueError('Nexus returned an invalid mod response.')
                    mod_metadata[mod_id] = metadata
            except (ValueError, OSError, RuntimeError, KeyError, TypeError, AttributeError) as error:
                if 'HTTP 403' in str(error):
                    cache[mod_id] = None
                    result['errors'].append(f'{row.get("name", "Mod")}: {_mod_access_error(mod_id)}')
                    continue
                result['metadata_errors'].append(f'{row.get("name", "Mod")} metadata: {error}')
                if any(f'HTTP {code}' in str(error) for code in (401, 429)):
                    break
            metadata = mod_metadata.get(mod_id) or {}
            unavailable = {'hidden': 'hidden', 'not_published': 'not published', 'under_moderation': 'under moderation',
                           'removed': 'removed', 'removed_by_staff': 'removed'}
            status = unavailable.get(str(metadata.get('status')))
            if status or metadata.get('available') is False:
                result['errors'].append(f'{row.get("name", "Mod")}: This mod is {status or "unavailable"} on Nexus; '
                                        'update checks are unavailable. Installed files are unchanged.')
                continue
            category = category_names.get(str(metadata.get('category_id')))
            if category:
                result['categories'][row['id']] = category
            if mod_id not in cache:
                cache[mod_id] = None
                cache[mod_id] = _json(f'{API}/games/{GAME}/mods/{mod_id}/files.json', key.strip())
            payload = cache[mod_id]
            if payload is None:
                continue
            current_version = installed_version(row | {'nexus_mod_id': mod_id})
            candidate, choices = _candidate(payload, link.get('file_id'), current_version)
            url = f'https://www.nexusmods.com/{GAME}/mods/{mod_id}?tab=files'
            item = {'id': row['id'], 'name': row['name'], 'nexus_mod_id': mod_id,
                    'url': url, 'downloadable': False, 'file_id': None, 'version': '', 'choices': [],
                    'installed_version': current_version, 'known_version': False}
            if choices:
                item.update(status='Choose the matching file variant', choices=[{
                    'file_id': f['file_id'], 'name': f.get('name', ''), 'version': f.get('version', ''),
                    'url': url + '&file_id=' + str(f['file_id'])} for f in choices])
            elif candidate:
                file_id = int(candidate['file_id'])
                installed = link.get('file_id')
                if installed and not row.get('package_version_stale') and _id(installed) == file_id:
                    continue
                old = _version(current_version)
                new = _version(candidate.get('version', ''))
                if (not installed or row.get('package_version_stale')) and old is not None and new is not None and new <= old:
                    continue
                item['known_version'] = _known_version(current_version) and _known_version(candidate.get('version'))
                verified = item['known_version'] and ((bool(installed) and not row.get('package_version_stale'))
                    or (old is not None and new is not None and new > old))
                item.update(file_id=file_id, version=candidate.get('version') or 'Unknown',
                            downloadable=account['premium'] and verified,
                            status='Update available' if verified else 'Confirm installed file/version',
                            url=url + '&file_id=' + str(file_id))
                item.update(_changelog(candidate))
            else:
                continue
            if str(row.get('source', '')).casefold() == 'steam workshop' or row.get('workshop_id'):
                item.update(downloadable=False, blocked_reason='Steam manages Workshop updates')
            elif not _known_version(current_version) or (candidate and not item['known_version']):
                item.update(downloadable=False, blocked_reason='Unknown version; automatic update skipped')
            elif choices:
                item['blocked_reason'] = 'Choose the matching file variant'
            elif not account['premium']:
                item['blocked_reason'] = 'Nexus Premium is required for automatic downloads'
            elif not item['downloadable']:
                item['blocked_reason'] = 'Confirm installed file/version'
            result['updates'].append(item)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError, AttributeError) as error:
            message = _mod_access_error(mod_id) if 'HTTP 403' in str(error) else str(error)
            result['errors'].append(f'{row.get("name", "Mod")}: {message}')
            if any(f'HTTP {code}' in str(error) for code in (401, 429)):
                break
    return result


def _download(url, destination, domains, maximum=2 * 1024**3, expected_sha256=None):
    if not _trusted_url(url, domains):
        raise ValueError('Download must use a trusted HTTPS host.')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        opener = urllib.request.build_opener(_Redirects(domains))
        request = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']})
        with opener.open(request, timeout=60) as response, tempfile.NamedTemporaryFile(
                dir=destination.parent, delete=False, suffix='.part') as output:
            temporary = Path(output.name)
            expected = int(response.headers.get('Content-Length', 0))
            if expected > maximum:
                raise ValueError('Download exceeds the size limit.')
            count = 0
            checksum = hashlib.sha256()
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > maximum:
                    raise ValueError('Download exceeds the size limit.')
                output.write(chunk)
                checksum.update(chunk)
            if not count or (expected and count != expected):
                raise ValueError('Download was empty or incomplete.')
            if expected_sha256 and checksum.hexdigest() != expected_sha256:
                raise ValueError('The GitHub download did not match its pinned SHA-256. Nothing was installed.')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _github_asset(data_dir, asset, maximum=64 * 1024 * 1024):
    """Reuse only verified cache bytes; publish a download after its hash is checked."""
    from manager import digest, safe_path
    url, checksum = asset
    target = safe_path(Path(data_dir), 'downloads/upstream/' + checksum + '-' + url.rsplit('/', 1)[-1])
    if target.is_file() and 0 < target.stat().st_size <= maximum and digest(target) == checksum:
        return target
    return _download(url, target, GITHUB_DOMAINS, maximum=maximum, expected_sha256=checksum)


def nexus_download(key, mod_id, file_id, destination: Path) -> Path:
    mod_id, file_id = _id(mod_id), _id(file_id)
    if not _account(key)['premium']:
        raise ValueError('Nexus Premium is required for automatic downloads. Use the Nexus file page, then import the ZIP.')
    base = f'{API}/games/{GAME}/mods/{mod_id}/files/{file_id}'
    details = _json(base + '.json', key.strip())
    name = str(details.get('file_name', ''))
    if not name.lower().endswith('.zip'):
        raise ValueError('This Nexus file is not a ZIP. Download it on Nexus and extract it with its original tool.')
    mirrors = _json(base + '/download_link.json', key.strip())
    valid = [m['URI'] for m in mirrors if _trusted_url(m.get('URI', ''), ('nexusmods.com', 'nexus-cdn.com'))]
    if not valid:
        raise ValueError('Nexus returned no trusted HTTPS download mirror.')
    # Keep the remote filename out of local paths; an ID-based name cannot traverse directories.
    target = Path(destination) / f'nexus-{mod_id}-{file_id}.zip'
    return _download(valid[0], target, ('nexusmods.com', 'nexus-cdn.com'))


def nexus_archive_release(key, archive, mod_id=None, file_id=None):
    """Verify an exact release, or identify one unambiguous GK2 browser download."""
    mod_id = _id(mod_id) if mod_id is not None else None
    file_id = _id(file_id) if file_id is not None else None
    if file_id is not None and mod_id is None:
        raise ValueError('Select the Nexus mod for this file.')
    exact = mod_id is not None and file_id is not None
    with Path(archive).open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'md5').hexdigest()
    archive_size = Path(archive).stat().st_size
    try:
        matches = _json(f'{API}/games/{GAME}/mods/md5_search/{checksum}.json', key)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError('Nexus could not verify this ZIP. Try again, or use Install ZIP with its actual version. ' + str(error)) from error
    if not isinstance(matches, list):
        raise ValueError('Nexus could not identify this ZIP. Use the original download or Install ZIP with its actual version.')
    identified = {}
    for match in matches:
        if not isinstance(match, dict):
            continue
        mod, details = match.get('mod'), match.get('file_details')
        if not isinstance(mod, dict) or not isinstance(details, dict) or mod.get('domain_name', GAME) != GAME:
            continue
        try:
            found_mod, found_file = _id(mod.get('mod_id')), _id(details.get('file_id'))
        except ValueError:
            continue
        if (mod_id is not None and found_mod != mod_id) or (file_id is not None and found_file != file_id):
            continue
        size = details.get('size_in_bytes')
        if size is not None and (isinstance(size, bool) or not re.fullmatch(r'[0-9]+', str(size)) or int(size) != archive_size):
            continue
        if exact:
            return details
        name = mod.get('name')
        identified[(found_mod, found_file)] = dict(details, nexus_mod_id=found_mod, file_id=found_file,
            name=name.strip() if isinstance(name, str) and name.strip() else f'Nexus mod #{found_mod}',
            category_id=mod.get('category_id'), version=details['version'].strip() if _known_version(details.get('version')) else 'Unknown')
    if len(identified) == 1:
        return next(iter(identified.values()))
    if not exact and len(identified) > 1:
        raise ValueError('Nexus found multiple mod files matching this ZIP. Download from its GK2 mod page, or use Install ZIP.')
    if exact:
        raise ValueError('This ZIP is not the selected Nexus update. Download that exact file, or use Install ZIP with its actual version.')
    raise ValueError('Nexus could not identify a Graveyard Keeper 2 mod in this ZIP. Use the original GK2 download or Install ZIP.')


def _target(game, relative):
    """Allow replacing Vortex file links, but never write through a linked directory."""
    target = game / relative
    for parent in target.parents:
        if parent == game:
            break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked game directories cannot be updated safely: ' + str(parent))
    if target.exists() and target.is_dir():
        raise ValueError('Expected a game file, found a directory: ' + str(target))
    return target


def _foundation_present(game):
    return all((game / name).is_file() for name in WORKSHOP_BEPINEX_FILES)


def _foundation_skip():
    return {'requires_download': False, 'already_installed': True, 'installed': True, 'state': 'installed',
            'files': 0, 'message': 'BepInEx is already installed. Its files and settings were preserved.'}


def _github_setup(game, data_dir, dry_run=False):
    import inventory
    from manager import copy_atomic, digest, ensure_game_stopped, safe_extract, safe_path
    if _foundation_present(game):
        return _foundation_skip()
    for relative in BEPINEX_UPSTREAM_FILES:
        if relative.endswith('.dll') and os.path.lexists(game / (relative + '.gk2mt-disabled')):
            raise ValueError('A BepInEx runtime file is disabled: ' + relative +
                             '. Enable the existing installation or review an explicit repair ZIP; it was preserved.')
    if not inventory._safe(game, Path(game.anchor)):
        raise ValueError('Choose the physical game folder; linked folders cannot be used for setup.')
    components = [
        {'name': 'BepInEx', 'version': '5.4.23.5', 'url': BEPINEX_ASSET[0], 'sha256': BEPINEX_ASSET[1],
         'source_url': 'https://github.com/BepInEx/BepInEx/tree/v5.4.23.5',
         'license_url': 'https://github.com/BepInEx/BepInEx/blob/v5.4.23.5/LICENSE'},
        {'name': 'Configuration Manager', 'version': '19.0.0', 'url': CONFIGURATION_ASSET[0],
         'sha256': CONFIGURATION_ASSET[1],
         'source_url': 'https://github.com/BepInEx/BepInEx.ConfigurationManager/tree/v19.0',
         'license_url': 'https://github.com/BepInEx/BepInEx.ConfigurationManager/blob/v19.0/LICENSE'},
        {'name': 'Unity Doorstop', 'version': '4.5.0', 'included_in': 'BepInEx',
         'source_url': 'https://github.com/NeighTools/UnityDoorstop/tree/v4.5.0',
         'license_url': 'https://github.com/NeighTools/UnityDoorstop/blob/v4.5.0/LICENSE'}]
    _recognized_loaders(game, {'configurationmanager.dll'})  # Verify the existing plugin tree before downloads.
    metadata = {'source': 'GitHub', 'url': BEPINEX_ASSET[0], 'components': components,
                'package_version': '5.4.23.5'}
    if dry_run:
        return metadata | {'requires_download': False, 'state': 'ready', 'files': 0, 'can_install': True,
                           'message': 'Install BepInEx 5.4.23.5 and Configuration Manager from GitHub. No account is needed.'}
    ensure_game_stopped()
    archives = [_github_asset(data_dir, asset) for asset in (BEPINEX_ASSET, CONFIGURATION_ASSET)]
    if _foundation_present(game):
        return _foundation_skip()
    folder = data_dir / 'installer' / uuid.uuid4().hex
    stage = folder / 'payload'
    for index, (archive, asset, allowed) in enumerate(zip(archives, (BEPINEX_ASSET, CONFIGURATION_ASSET),
                                                       (BEPINEX_UPSTREAM_FILES, CONFIGURATION_FILES))):
        verified = folder / ('verified-' + str(index) + '.zip')
        copy_atomic(archive, verified)
        if digest(verified) != asset[1]:
            raise ValueError('The GitHub cache changed or its SHA-256 is invalid. Nothing was installed.')
        unpacked = folder / ('upstream-' + str(index))
        safe_extract(verified, unpacked)
        actual = {p.relative_to(unpacked).as_posix() for p in unpacked.rglob('*') if p.is_file()}
        ignored = {'BepInEx/plugins/ConfigurationManager/ConfigurationManager.18.4.1.nupkg'} if index else set()
        if actual != allowed | ignored:
            raise ValueError('The pinned GitHub ZIP has an unexpected layout. Nothing was installed.')
        for relative in sorted(allowed):
            copy_atomic(unpacked / relative, stage / relative)
    components[1]['preserved'] = bool(_recognized_loaders(game, {'configurationmanager.dll'}))
    # The base release has no notice files. Keep its upstream/source/license references alongside
    # the Configuration Manager LICENSE/README supplied by its official archive.
    notice = stage / 'BepInEx/distribution/gk2mt-upstream.json'
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.write_text(json.dumps({'source': 'GitHub', 'components': components}, indent=2), encoding='utf-8')
    files = []
    for path in sorted(stage.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(stage).as_posix()
        target = safe_path(game, relative)
        _target(game, Path(relative))
        if relative.startswith('BepInEx/plugins/') and components[1]['preserved']:
            continue
        if target.exists():
            if (relative == 'doorstop_config.ini' or relative.startswith(('BepInEx/config/', 'BepInEx/distribution/',
                    'BepInEx/plugins/')) or digest(target) == digest(path)):
                continue
            raise ValueError('Existing BepInEx file differs: ' + relative +
                             '. It was preserved. Review the installation or use an explicit BepInEx ZIP to repair it.')
        files.append((path, Path(relative)))
    return _deploy_foundation(game, data_dir, files, 'BepInEx 5.4.23.5', metadata, missing_only=True)


def setup_installer(game: Path, data_dir: Path, key='', archive=None, file_id=None, dry_run=False) -> dict:
    """Use GitHub for missing-only setup; explicit Nexus files/ZIPs retain repair/update behaviour."""
    from manager import copy_atomic, digest, ensure_game_stopped, safe_extract

    game, data_dir = Path(game).absolute(), Path(data_dir).absolute()
    if not all((game / name).is_file() for name in GAME_MARKERS):
        raise ValueError('Select the Graveyard Keeper 2 folder containing its executable and Unity game data.')
    if archive is None and file_id is None:
        return _github_setup(game, data_dir, dry_run=dry_run)
    game, data_dir = game.resolve(), data_dir.resolve()
    version = 'Local Nexus mod 48 ZIP'
    package_version = 'Unknown'
    if archive:
        archive = Path(archive).expanduser().resolve()
        if archive.suffix.lower() != '.zip' or not archive.is_file():
            raise ValueError('Choose the downloaded Nexus mod 48 ZIP file.')
        file_id = None
    else:
        manual = {'requires_download': True, 'url': SETUP_URL, 'nexus_mod_id': SETUP_MOD,
                  'choices': [], 'message': 'Download the BepInEx bundle from Nexus mod 48, then choose its ZIP here. Automatic downloads require a Nexus Premium API key.'}
        if not key or not _account(key)['premium']:
            return manual
        payload = _json(f'{API}/games/{GAME}/mods/{SETUP_MOD}/files.json', key.strip())
        if file_id is not None:
            file_id = _id(file_id)
            selected = next((f for f in payload.get('files', []) if int(f['file_id']) == file_id
                             and f.get('category_id') == 1), None)
            if selected is None:
                raise ValueError('Choose an active MAIN file from Nexus mod 48.')
        else:
            selected, choices = _candidate(payload, None)
            if selected is None:
                manual['choices'] = [{'file_id': f['file_id'], 'name': f.get('name', ''),
                                      'version': f.get('version', '')} for f in choices if f.get('category_id') == 1]
                manual['message'] = 'Select the matching MAIN file on Nexus mod 48, download it, then choose its ZIP here.'
                return manual
        file_id = int(selected['file_id'])
        version = package_version = str(selected.get('version') or 'Unknown')
        archive = nexus_download(key, SETUP_MOD, file_id, data_dir / 'downloads')
    ensure_game_stopped()
    folder = data_dir / 'installer' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    stage = folder / 'payload'
    safe_extract(archive, stage)
    missing = [name for name in REQUIRED if not (stage / name).is_file() or not (stage / name).stat().st_size]
    if missing:
        raise ValueError('The ZIP is not a complete Nexus mod 48 BepInEx bundle. Missing: ' + ', '.join(missing))
    files = []
    for path in sorted(stage.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(stage).as_posix()
        if relative.lower() in ('readme.md', 'license', 'changelog.md'):
            continue
        allowed = (relative in ROOT_FILES or
                   relative.startswith('BepInEx/core/') and path.suffix.lower() in ('.dll', '.xml') or
                   relative.startswith('BepInEx/config/') and path.suffix.lower() == '.cfg' or
                   relative.startswith('BepInEx/distribution/') or
                   relative.startswith('BepInEx/plugins/ConfigurationManager/') and path.name in (
                       'ConfigurationManager.dll', 'ConfigurationManager.xml', 'ConfigurationManagerAttributes.cs',
                       'LICENSE', 'README.md'))
        if not allowed or 'workshop' in path.name.lower() or path.name.lower() == 'gk2.framework.dll':
            raise ValueError('Unexpected file in BepInEx setup ZIP: ' + relative + '. Workshop loaders and mods must be installed separately.')
        if relative.startswith('BepInEx/config/') and (game / relative).is_file():
            continue
        if (relative == 'BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll'
                and not (game / relative).exists() and (game / (relative + '.gk2mt-disabled')).is_file()):
            relative += '.gk2mt-disabled'
        files.append((path, Path(relative)))
    metadata = {'source': 'Nexus', 'url': SETUP_URL, 'nexus_mod_id': SETUP_MOD, 'file_id': file_id,
                'package_version': package_version}
    return _deploy_foundation(game, data_dir, files, version, metadata, dry_run=dry_run)


def _deploy_foundation(game, data_dir, files, version, metadata, dry_run=False, missing_only=False):
    """Shared backup/rollback; missing-only setup publishes files exclusively without replacement."""
    from manager import copy_atomic, digest, ensure_game_stopped, safe_path
    for path, relative in files:
        _target(game, relative)
    # Copy the entire existing setup before touching any real game file.
    existing = []
    bep = game / 'BepInEx'
    if bep.is_symlink() or (hasattr(bep, 'is_junction') and bep.is_junction()):
        raise ValueError('BepInEx is a linked directory; choose a regular game installation.')
    if bep.exists():
        for path in bep.rglob('*'):
            if path.is_dir() and (path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())):
                raise ValueError('Cannot back up a linked directory: ' + str(path))
            if path.is_file():
                existing.append(path)
    existing.extend(game / name for name in ROOT_FILES if (game / name).is_file())
    if dry_run:
        return {'files': len(files), 'conflicts': [relative.as_posix() for _, relative in files if (game / relative).exists()],
                'warnings': ['Existing configuration files, Workshop loader, and other mods will be preserved.']}
    backup = data_dir / 'backups' / ('loader-' + uuid.uuid4().hex)
    backup.mkdir(parents=True)
    for path in existing:
        target = backup / path.relative_to(game)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    manifest = {'game': str(game), 'release': version, **metadata,
                'files': [p.relative_to(game).as_posix() for p in existing],
                'written': [relative.as_posix() for _, relative in files]}
    (backup / 'backup.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    written = []
    new_files = {}
    installed_hashes = {}
    ensure_game_stopped()
    if missing_only and _foundation_present(game):
        return _foundation_skip()
    try:
        for path, relative in files:
            destination = _target(game, relative)
            if missing_only:
                safe_path(game, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix='.gk2mt-foundation-', dir=destination.parent)
                os.close(descriptor)
                temporary = Path(name)
                try:
                    copy_atomic(path, temporary)
                    ensure_game_stopped()
                    identity = temporary.stat()
                    expected = digest(path)
                    if relative.suffix.casefold() == '.dll' and os.path.lexists(Path(str(destination) + '.gk2mt-disabled')):
                        raise ValueError('A disabled copy appeared during setup: ' + str(relative) + '. It was preserved.')
                    if (relative.name.casefold() == 'configurationmanager.dll'
                            and _recognized_loaders(game, {'configurationmanager.dll'})):
                        raise ValueError('An existing Configuration Manager appeared during setup. It was preserved; refresh and try again.')
                    os.link(temporary, destination)
                    written.append(relative)
                    new_files[relative] = (identity.st_dev, identity.st_ino, expected)
                    if digest(destination) != expected:
                        raise ValueError('A new BepInEx file changed while being installed: ' + str(relative))
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                written.append(relative)
                copy_atomic(path, destination)
            if path.suffix.lower() not in ('.cfg', '.ini'):
                installed_hashes[relative.as_posix()] = digest(destination)
        if missing_only:
            for relative, expected in new_files.items():
                destination = safe_path(game, relative)
                identity = destination.stat()
                if ((identity.st_dev, identity.st_ino) != expected[:2] or digest(destination) != expected[2]
                        or relative.suffix.casefold() == '.dll' and
                        os.path.lexists(Path(str(destination) + '.gk2mt-disabled'))):
                    raise ValueError('A setup file changed or was disabled before setup finished: ' + str(relative))
    except Exception as error:
        failed = []
        for relative in reversed(written):
            try:
                destination = _target(game, relative)
                if missing_only:
                    safe_path(game, relative)
                    identity = destination.stat() if destination.is_file() else None
                    expected = new_files[relative]
                    if (identity and (identity.st_dev, identity.st_ino) == expected[:2]
                            and digest(destination) == expected[2]):
                        destination.unlink()
                    elif os.path.lexists(destination):
                        failed.append(str(relative) + ' changed by another program; preserved')
                elif (backup / relative).is_file():
                    copy_atomic(backup / relative, destination)
                else:
                    destination.unlink(missing_ok=True)
            except (OSError, ValueError):
                failed.append(str(relative))
        raise RuntimeError(f'Setup failed; backup: {backup}. Restore failures: {failed}. {error}') from error
    return {'requires_download': False, 'state': 'installed', 'installed': True, 'version': version,
            'backup': str(backup), 'files': len(files), **metadata, 'installed_hashes': installed_hashes,
            'message': 'BepInEx foundation installed from ' + metadata['source'] +
                       '. Existing Workshop loaders, other mods and configs were preserved; a backup was saved.'}
