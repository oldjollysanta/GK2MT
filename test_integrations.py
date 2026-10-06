"""Offline integration checks: python test_integrations.py. Never touches a real game."""
import json
import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import integrations as api


def check_steam_workshop_titles():
    entry = {'publishedfileid': '10', 'consumer_app_id': 4358690, 'result': 1,
             'title': 'Talent & Tech Refund'}
    entries = [entry, entry | {'publishedfileid': '99', 'title': 'Unrequested'},
               entry | {'publishedfileid': '11', 'consumer_app_id': 1},
               entry | {'publishedfileid': '12', 'result': 9},
               entry | {'publishedfileid': '13', 'title': 'bad\nname'},
               entry | {'publishedfileid': '14', 'title': 'x' * 513},
               entry | {'publishedfileid': '15', 'title': '   '},
               {k: v for k, v in (entry | {'publishedfileid': '16'}).items() if k != 'consumer_app_id'}]
    content = json.dumps({'response': {'result': 1, 'publishedfiledetails': entries}}).encode()
    with patch.object(api.urllib.request, 'build_opener') as build:
        opener = build.return_value
        opener.open.return_value = io.BytesIO(content)
        assert api.steam_workshop_titles(['10', '10'] + [str(i) for i in range(11, 17)]) == {
            '10': 'Talent & Tech Refund'}
        request = opener.open.call_args.args[0]
        assert request.full_url == 'https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/'
        assert request.get_method() == 'POST' and opener.open.call_args.kwargs == {'timeout': 10}
        assert {key.lower() for key in request.headers} == {'user-agent', 'accept', 'content-type'}
        body = api.urllib.parse.parse_qs(request.data.decode())
        assert body['itemcount'] == ['7'] and body['publishedfileids[0]'] == ['10'] and 'key' not in body
        assert isinstance(build.call_args.args[0], api._Redirects) and not build.call_args.args[0].domains
        opener.open.reset_mock()
        assert api.steam_workshop_titles([]) == {}
        opener.open.assert_not_called()
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(content)
        api.steam_workshop_titles([str(i) for i in range(1, 102)])
        assert [api.urllib.parse.parse_qs(call.args[0].data.decode())['itemcount']
                for call in opener.open.call_args_list] == [['100'], ['1']]
        opener.open.side_effect = None
        for value in ('0', '-1', '01', '1/../../', '18446744073709551616', True, 1):
            try:
                api.steam_workshop_titles([value])
                assert False, 'Invalid Workshop ID was accepted.'
            except ValueError:
                pass
        for invalid in (b'broken JSON', b'{}', b'{"response":{"result":2}}', b'x' * (2 * 1024 * 1024 + 1)):
            opener.open.return_value = io.BytesIO(invalid)
            try:
                api.steam_workshop_titles(['10'])
                assert False, 'Invalid Steam response was accepted.'
            except RuntimeError as error:
                assert str(error) == 'Steam Workshop names are unavailable. Try Rescan again later.'
        for failure in (OSError('private proxy details'), ValueError('redirect rejected')):
            opener.open.side_effect = failure
            try:
                api.steam_workshop_titles(['10'])
                assert False, 'Network error was ignored.'
            except RuntimeError as error:
                assert str(error) == 'Steam Workshop names are unavailable. Try Rescan again later.'


def check_nexus_metadata():
    game_url = f'{api.API}/games/{api.GAME}.json'
    mod_url = f'{api.API}/games/{api.GAME}/mods/10.json'
    files_url = f'{api.API}/games/{api.GAME}/mods/10/files.json'
    payload = {'files': [{'file_id': 2, 'category_id': 1, 'version': '2.0'}],
               'file_updates': [{'old_file_id': 1, 'new_file_id': 2}]}
    replies = {game_url: {'categories': [{'category_id': 7, 'name': 'Gameplay'}]},
               mod_url: {'category_id': 7}, files_url: payload}
    row = {'id': 'old', 'name': 'Example', 'version': '1.0', 'nexus_mod_id': 10, 'file_id': 1}

    def request(url, key):
        reply = replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply

    with patch.object(api, '_account', return_value={'name': 'Test', 'premium': True}):
        with patch.object(api, '_json', side_effect=request):
            stale = row | {'version': '2.0', 'package_version': '1.0', 'package_version_stale': True}
            assert api.nexus_check('test-key', [stale], {})['updates'] == []
            # A replaced DLL also invalidates an old manually saved installed file ID.
            stale['version'] = '1.5'
            item = api.nexus_check('test-key', [stale], {})['updates'][0]
            assert item['installed_version'] == '1.5' and item['downloadable']
            stale.update(version='Unknown')
            item = api.nexus_check('test-key', [stale], {})['updates'][0]
            assert item['installed_version'] == 'Unknown' and not item['downloadable']
            assert api.installed_version(stale | {'nexus_mod_id': 48, 'version': '19.0'}) == 'Unknown'
            assert api.installed_version(row | {'nexus_mod_id': 48, 'version': '19.0'}) == 'Unknown'
            assert api.installed_version(row | {'nexus_mod_id': 48, 'version': '19.0', 'package_version': 'Unknown'}) == 'Unknown'
            assert api.installed_version(stale | {'package_version_stale': False}) == '1.0'
            assert api.installed_version(row | {'package_version': None}) == '1.0'
            assert api.installed_version(row | {'version': '0.3.0', 'package_version': '0.3.1'}) == '0.3.1'
        with patch.object(api, '_json', side_effect=request) as calls:
            result = api.nexus_check('test-key', [row, row | {'id': 'current', 'file_id': 2}], {})
            assert result['categories'] == {'old': 'Gameplay', 'current': 'Gameplay'}
            assert len(result['updates']) == 1 and not result['metadata_errors']
            assert result['updates'][0]['installed_version'] == '1.0' and result['updates'][0]['known_version']
            assert [call.args[0] for call in calls.call_args_list] == [game_url, mod_url, files_url]

        with patch.object(api, '_json', side_effect=request):
            for unknown in ('Unknown', '', 'N/A', None):
                item = api.nexus_check('test-key', [row | {'version': unknown}], {})['updates'][0]
                assert not item['downloadable'] and not item['known_version'] and 'Unknown version' in item['blocked_reason']
            payload['files'][0]['version'] = 'Unknown'
            item = api.nexus_check('test-key', [row], {})['updates'][0]
            assert not item['downloadable'] and not item['known_version']
            payload['files'][0]['version'] = 'release-b'
            item = api.nexus_check('test-key', [row | {'version': 'release-a'}], {})['updates'][0]
            assert item['downloadable'] and item['known_version']  # Author-declared successor supports nonnumeric releases.
            item = api.nexus_check('test-key', [row | {'version': 'release-a', 'file_id': None}], {})['updates'][0]
            assert not item['downloadable'] and item['known_version']
            payload['files'][0]['version'] = '2.0'
            for source in ({'source': 'Steam Workshop'}, {'source': 'Manual', 'workshop_id': '123'}):
                item = api.nexus_check('test-key', [row | source], {})['updates'][0]
                assert not item['downloadable'] and item['blocked_reason'] == 'Steam manages Workshop updates'

        replies[mod_url] = RuntimeError('API HTTP 500: request failed')
        with patch.object(api, '_json', side_effect=request) as calls:
            result = api.nexus_check('test-key', [row, row | {'id': 'duplicate'}], {})
            assert result['updates'][0]['downloadable'] and len(result['metadata_errors']) == 1
            assert result['categories'] == {} and not result['errors']
            assert [call.args[0] for call in calls.call_args_list].count(mod_url) == 1
        replies[game_url] = RuntimeError('API HTTP 500: request failed')
        with patch.object(api, '_json', side_effect=request):
            result = api.nexus_check('test-key', [row], {})
            assert result['updates'][0]['downloadable'] and result['metadata_errors']
        for code in (401, 403, 429):
            replies[game_url] = RuntimeError(f'API HTTP {code}: request failed')
            with patch.object(api, '_json', side_effect=request) as calls:
                result = api.nexus_check('test-key', [row], {})
                assert result['metadata_errors'] and result['updates'] == [] and calls.call_count == 1
        with patch.object(api, '_json') as calls:
            assert api.nexus_check('test-key', [], {})['categories'] == {}
            calls.assert_not_called()  # Connecting an account does not fetch mod metadata.

    replies[game_url] = {'categories': []}
    with patch.object(api, '_account', return_value={'name': 'Test', 'premium': False}), \
            patch.object(api, '_json', side_effect=request):
        item = api.nexus_check('test-key', [row], {})['updates'][0]
        assert not item['downloadable'] and 'Premium' in item['blocked_reason']


def check_nexus_visibility_and_changelog():
    rows = [{'id': str(i), 'name': f'Mod {i}', 'version': '1.0', 'nexus_mod_id': i} for i in (10, 11)]
    base = f'{api.API}/games/{api.GAME}'
    notes = '<h2>Changes</h2><ul><li>Fix &amp; polish</li><li>Faster<br>loading</li></ul>'
    notes += '<script>secret()</script><style>hidden</style><img src="x" onerror="bad()">'
    payload = {'files': [{'file_id': 2, 'category_id': 1, 'version': '2.0', 'changelog_html': notes},
                         {'file_id': 3, 'category_id': 3, 'version': '3.0-beta', 'changelog_html': 'Wrong variant'}]}
    replies = {base + '.json': {'categories': []},  # Availability must still work without categories.
               base + '/mods/10.json': {'status': 'hidden', 'available': False},
               base + '/mods/11.json': {'status': 'published', 'available': True},
               base + '/mods/10/files.json': RuntimeError('API HTTP 403: access denied'),
               base + '/mods/11/files.json': payload}

    def request(url, key):
        value = replies[url]
        if isinstance(value, Exception):
            raise value
        return value

    with patch.object(api, '_account', return_value={'name': 'Test', 'premium': True}), \
            patch.object(api, '_json', side_effect=request) as calls:
        result = api.nexus_check('test-key', rows, {})
        assert len(result['errors']) == 1 and 'hidden on Nexus' in result['errors'][0]
        assert 'Installed files are unchanged' in result['errors'][0]
        assert base + '/mods/10/files.json' not in [call.args[0] for call in calls.call_args_list]
        item = result['updates'][0]
        assert item['id'] == '11' and item['downloadable'] and item['file_id'] == 2
        assert item['changelog'] == 'Changes\n• Fix & polish\n• Faster\nloading'
        assert item['changelog_version'] == '2.0' and 'changelog_error' not in item

        # A per-mod files or metadata 403 must never suppress the next mod.
        for endpoint in ('files.json', 'metadata'):
            replies[base + '/mods/10.json'] = {'status': 'published', 'available': True}
            if endpoint == 'metadata':
                replies[base + '/mods/10.json'] = RuntimeError('API HTTP 403: access denied')
            calls.reset_mock()
            result = api.nexus_check('test-key', rows + [rows[0] | {'id': 'duplicate'}], {})
            assert [i['id'] for i in result['updates']] == ['11']
            assert len(result['errors']) == 1 and 'Nexus denied access to mod #10 (HTTP 403)' in result['errors'][0]
            assert 'may be hidden' in result['errors'][0]
            assert [c.args[0] for c in calls.call_args_list].count(base + '/mods/10.json') == 1
            assert [c.args[0] for c in calls.call_args_list].count(base + '/mods/10/files.json') <= 1

        # Authentication and rate limiting still stop all subsequent API calls.
        for code in (401, 429):
            for endpoint in ('/mods/10.json', '/mods/10/files.json'):
                replies[base + '/mods/10.json'] = {'status': 'published'}
                replies[base + endpoint] = RuntimeError(f'API HTTP {code}: request failed')
                calls.reset_mock()
                result = api.nexus_check('test-key', rows, {})
                assert not result['updates']
                assert base + '/mods/11.json' not in [c.args[0] for c in calls.call_args_list]

        # Missing notes and malformed notes stay distinct, and neither blocks updates.
        for value, unavailable in [(None, False), ('', False), ({'bad': '<script>x</script>'}, True)]:
            payload['files'][0]['changelog_html'] = value
            result = api.nexus_check('test-key', [rows[1]], {})
            item = result['updates'][0]
            assert item['downloadable'] and item['changelog'] == '' and not result['errors']
            assert ('changelog_error' in item) is unavailable
            assert '<script>' not in item.get('changelog_error', '')
        payload['files'][0]['changelog_html'] = '<![bogus]>'
        with patch.object(api._ChangelogText, 'feed', side_effect=AssertionError('malformed declaration')):
            item = api.nexus_check('test-key', [rows[1]], {})['updates'][0]
            assert item['downloadable'] and item['changelog_error'] and not item['changelog']
        assert api._changelog({'version': '2.0', 'description': 'Not release notes'})['changelog'] == ''


def check_archived_release_without_links():
    old = {'file_id': 825, 'category_id': 7, 'name': 'WeeklyOverview', 'version': '0.1.5'}
    latest = {'file_id': 1144, 'category_id': 1, 'name': 'WeeklyOverview', 'version': '0.1.6',
              'changelog_html': '<p>New release notes.</p>'}
    payload = {'files': [old, latest], 'file_updates': []}
    assert api._candidate(payload, 825, '0.1.5') == (latest, [])
    for installed in ('0.1.6', '0.2.0', 'Unknown', None):
        assert api._candidate(payload, 825, installed)[0] is None
    assert api._candidate(payload, 999, '0.1.5')[0] is None
    assert api._candidate(payload, 1144, '0.1.6') == (latest, [])
    for change in ({'category_id': 2}, {'name': 'Controller variant'}, {'name': ''},
                   {'version': 'Unknown'}, {'version': '0.1.5'}, {'version': '0.1.4'}):
        assert api._candidate({'files': [old, latest | change]}, 825, '0.1.5')[0] is None
    for category in (1, 2, 3):
        variant = latest | {'file_id': 1200, 'category_id': category, 'name': 'Other variant'}
        assert api._candidate({'files': [old, latest, variant]}, 825, '0.1.5')[0] is None
    assert api._candidate({'files': [old | {'category_id': 6}, latest]}, 825, '0.1.5')[0] is None
    assert api._candidate({'files': [old | {'version': 'Unknown'}, latest]}, 825, '0.1.5')[0] is None
    normalized = latest | {'name': ' weeklyoverview '}
    assert api._candidate({'files': [old, normalized]}, 825, '0.1.5')[0] == normalized
    zero = old | {'version': '0.0'}
    assert api._candidate({'files': [zero, latest]}, 825, '0.0')[0] == latest
    # Explicit branches and cycles must never fall through to a guessed MAIN.
    branch = payload | {'file_updates': [{'old_file_id': 825, 'new_file_id': 1144},
                                       {'old_file_id': 825, 'new_file_id': 1200}]}
    assert api._candidate(branch, 825, '0.1.5')[0] is None
    cycle = payload | {'file_updates': [{'old_file_id': 825, 'new_file_id': 1144},
                                      {'old_file_id': 1144, 'new_file_id': 825}]}
    try:
        api._candidate(cycle, 825, '0.1.5')
        raise AssertionError('Cyclic update chain was accepted')
    except ValueError:
        pass
    base = f'{api.API}/games/{api.GAME}'
    replies = {base + '.json': {'categories': []}, base + '/mods/131.json': {'available': True},
               base + '/mods/131/files.json': payload}
    row = {'id': 'weekly', 'name': 'Weekly Overview', 'version': '0.1.5',
           'file_id': 825, 'nexus_mod_id': 131}
    for premium in (False, True):
        with patch.object(api, '_account', return_value={'premium': premium}), \
             patch.object(api, '_json', side_effect=lambda url, key: replies[url]):
            item = api.nexus_check('test-key', [row], {})['updates'][0]
            assert item['version'] == '0.1.6' and item['file_id'] == 1144 and not item['choices']
            assert item['known_version'] and item['status'] == 'Update available'
            assert item['downloadable'] is premium and item['changelog'] == 'New release notes.'


def check_nexus_browser_identity():
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'original.zip'
        archive.write_bytes(b'original archive')
        checksum = hashlib.md5(archive.read_bytes()).hexdigest()
        mod = {'mod_id': 10, 'domain_name': api.GAME, 'name': 'Example mod', 'category_id': 7}
        details = {'file_id': 21, 'version': '1.2.0', 'mod_version': '9.9.9', 'name': 'Optional variant',
                   'file_name': 'original.zip', 'category_id': 3, 'size_in_bytes': archive.stat().st_size}
        match = {'mod': mod, 'file_details': details}
        with patch.object(api, '_json', return_value=[match]) as request:
            release = api.nexus_archive_release('test-key', archive)
            request.assert_called_once_with(f'{api.API}/games/{api.GAME}/mods/md5_search/{checksum}.json', 'test-key')
            assert release['nexus_mod_id'] == 10 and release['file_id'] == 21
            assert release['name'] == 'Example mod' and release['category_id'] == 7 and release['version'] == '1.2.0'
            assert api.nexus_archive_release('test-key', archive, 10, 21) == details
            assert api.nexus_archive_release('test-key', archive, 10)['nexus_mod_id'] == 10
            assert details['name'] == 'Optional variant'  # Browser metadata does not mutate exact-release details.
        with patch.object(api, '_json', return_value=[match, match]):
            assert api.nexus_archive_release('test-key', archive)['file_id'] == 21
        with patch.object(api, '_json', return_value=[match, {'mod': mod | {'mod_id': 11}, 'file_details': details}]):
            assert api.nexus_archive_release('test-key', archive, 10)['nexus_mod_id'] == 10
            try:
                api.nexus_archive_release('test-key', archive)
                assert False, 'Ambiguous browser download accepted.'
            except ValueError as error:
                assert 'multiple mod files' in str(error)
        with patch.object(api, '_json', return_value=[match | {'file_details': details | {'version': None}}]):
            assert api.nexus_archive_release('test-key', archive)['version'] == 'Unknown'
        for invalid in ([], {}, [None], [match | {'mod': mod | {'domain_name': 'othergame'}}],
                        [match | {'file_details': details | {'size_in_bytes': 1}}],
                        [match | {'file_details': details | {'size_in_bytes': True}}],
                        [match | {'file_details': details | {'file_id': True}}],
                        [match | {'mod': mod | {'mod_id': 0}}]):
            with patch.object(api, '_json', return_value=invalid):
                try:
                    api.nexus_archive_release('test-key', archive)
                    assert False, 'Invalid Nexus browser identity accepted.'
                except ValueError:
                    pass
        with patch.object(api, '_json', return_value=[match]):
            for ids in ((11, None), (10, 22)):
                try:
                    api.nexus_archive_release('test-key', archive, *ids)
                    assert False, 'A different selected mod/file was accepted.'
                except ValueError as error:
                    if ids[1] is not None:
                        assert 'not the selected Nexus update' in str(error)
        with patch.object(api, '_json') as request:
            for ids in ((None, 21), (True, None), (0, None), (10, '01')):
                try:
                    api.nexus_archive_release('test-key', archive, *ids)
                    assert False, 'Invalid hint accepted.'
                except ValueError:
                    pass
            request.assert_not_called()


def check():
    check_nexus_browser_identity()
    check_steam_workshop_titles()
    check_nexus_metadata()
    check_nexus_visibility_and_changelog()
    check_archived_release_without_links()
    files = [{'file_id': 1, 'category_id': 4, 'version': '1.0'},
             {'file_id': 2, 'category_id': 1, 'version': '2.0', 'name': 'Normal'},
             {'file_id': 3, 'category_id': 1, 'version': '2.0', 'name': 'Controller'}]
    payload = {'files': files, 'file_updates': [{'old_file_id': 1, 'new_file_id': 2}]}
    assert api._candidate(payload, 1)[0]['file_id'] == 2
    assert api._candidate(payload, None)[0] is None  # Do not arbitrarily pick a variant.
    assert api._candidate(payload, 3)[0]['file_id'] == 3
    assert api._version('v1.2.0') == api._version('1.2')
    assert api._version('preview') is None
    assert not api._trusted_url('https://nexusmods.com.attacker.test/a.zip', ('nexusmods.com',))
    assert not api._trusted_url('http://premium-files.nexusmods.com/a.zip', ('nexusmods.com',))
    assert not api._trusted_url('https://user:secret@nexusmods.com/a.zip', ('nexusmods.com',))
    assert api._trusted_url('https://premium-files.nexus-cdn.com/a.zip', ('nexus-cdn.com',))
    rows = [{'id': 'mod', 'name': 'Example', 'version': '1.0'}]
    with patch.object(api, '_account', return_value={'name': 'Test', 'premium': True}), \
            patch.object(api, '_json', return_value=payload):
        result = api.nexus_check('test-key', rows, {'mod': {'nexus_mod_id': 10, 'file_id': 1}})
        assert result['updates'][0]['downloadable'] and result['updates'][0]['file_id'] == 2
        result = api.nexus_check('test-key', rows, {'mod': {'nexus_mod_id': 10}})
        assert not result['updates'][0]['downloadable'] and len(result['updates'][0]['choices']) == 2
        result = api.nexus_check('test-key', [rows[0] | {'nexus_mod_id': 10, 'file_id': 1}], {})
        assert result['updates'][0]['file_id'] == 2  # Metadata may live on a managed package row.
    with patch.object(api, '_account', return_value={'name': 'Test', 'premium': True}), \
            patch.object(api, '_json', return_value={'files': [files[1]]}):
        result = api.nexus_check('test-key', [rows[0] | {'version': '19.0', 'package_version': '1.1', 'nexus_mod_id': 48}], {})
        assert result['updates'][0]['downloadable']  # Compare bundle version, not ConfigurationManager version.
    with patch.object(api, '_account', return_value={'name': 'Test', 'premium': False}), \
            patch.object(api, '_json') as request:
        try:
            api.nexus_download('test-key', 1, 2, Path('unused'))
            assert False, 'Free accounts must not receive automatic download requests.'
        except ValueError as error:
            assert 'Premium' in str(error)
        request.assert_not_called()

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        game, data = root / 'game', root / 'data'
        for name in api.GAME_MARKERS:
            path = game / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'game')
        config = game / 'BepInEx/config/personal.cfg'
        config.parent.mkdir(parents=True)
        config.write_text('keep my settings')
        loader = game / 'BepInEx/patchers/GK2_WorkshopLoader.dll'
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b'working workshop loader')
        mod = game / 'BepInEx/plugins/ShoppingList.dll'
        mod.parent.mkdir(parents=True)
        mod.write_bytes(b'working mod')
        vortex = root / 'vortex-staging.dll'
        vortex.write_bytes(b'original vortex file')
        os.link(vortex, game / 'winhttp.dll')
        package = root / 'release.zip'

        def bundle(content=b'new foundation', extra=None, omit=None):
            with zipfile.ZipFile(package, 'w') as archive:
                for name in api.REQUIRED:
                    if name != omit:
                        archive.writestr(name, content)
                archive.writestr('BepInEx/config/personal.cfg', 'factory defaults')
                archive.writestr('BepInEx/distribution/licenses/BepInEx.txt', 'license')
                archive.writestr('BepInEx/distribution/sources/UnityDoorstop-4.5.0-source.zip', b'source archive')
                archive.writestr('README.md', 'Setup instructions stay in staging.')
                if extra:
                    archive.writestr(extra, b'unexpected')

        with patch.object(api, '_json') as request, patch.object(api, '_account') as account, \
                patch.object(api, '_github_asset') as download:
            for key in ('', 'test-key'):
                preview = api.setup_installer(game, data, key=key, dry_run=True)
                assert preview['state'] == 'ready' and preview['source'] == 'GitHub'
                assert 'nexus_mod_id' not in preview and not data.exists()
            request.assert_not_called()
            account.assert_not_called()
            download.assert_not_called()
        bundle()
        with patch.object(api, '_json') as request, patch('manager.ensure_game_stopped'):
            installed = api.setup_installer(game, data, archive=package)
            request.assert_not_called()
        backup = Path(installed['backup'])
        assert not installed['requires_download']
        assert installed['package_version'] == 'Unknown'
        assert installed['installed_hashes']['winhttp.dll'] == hashlib.sha256(b'new foundation').hexdigest()
        assert 'doorstop_config.ini' not in installed['installed_hashes']
        assert not any(p.endswith('.cfg') for p in installed['installed_hashes'])
        assert (game / 'winhttp.dll').read_bytes() == b'new foundation'
        assert vortex.read_bytes() == b'original vortex file'  # No write through Vortex hardlink.
        assert (backup / 'winhttp.dll').read_bytes() == b'original vortex file'
        assert config.read_text() == 'keep my settings'
        assert not (game / 'README.md').exists()
        assert loader.read_bytes() == b'working workshop loader'
        assert mod.read_bytes() == b'working mod'
        assert (backup / 'BepInEx/config/personal.cfg').read_text() == 'keep my settings'
        assert json.loads((backup / 'backup.json').read_text())['game'] == str(game.resolve())
        assert all((game / name).stat().st_size for name in api.REQUIRED)

        with patch.object(api, '_account', return_value={'name': 'Test', 'premium': True}), \
                patch.object(api, '_json', return_value={'files': [files[1]]}), \
                patch.object(api, 'nexus_download', return_value=package) as download, \
                patch('manager.ensure_game_stopped'):
            installed = api.setup_installer(game, data, key='test-key', file_id=2)
            assert installed['version'] == '2.0' and installed['file_id'] == 2
            assert installed['package_version'] == '2.0'
            download.assert_called_once_with('test-key', 48, 2, data / 'downloads')

        configuration_manager = game / 'BepInEx/plugins/ConfigurationManager/ConfigurationManager.dll'
        disabled = configuration_manager.with_name(configuration_manager.name + '.gk2mt-disabled')
        configuration_manager.rename(disabled)
        bundle(b'updated disabled foundation')
        with patch('manager.ensure_game_stopped'):
            installed = api.setup_installer(game, data, archive=package)
        assert not configuration_manager.exists() and disabled.read_bytes() == b'updated disabled foundation'
        assert installed['installed_hashes'][disabled.relative_to(game).as_posix()] == hashlib.sha256(disabled.read_bytes()).hexdigest()
        assert str(configuration_manager.relative_to(game)).replace('\\', '/') not in installed['installed_hashes']
        assert (Path(installed['backup']) / disabled.relative_to(game)).read_bytes() == b'new foundation'

        snapshot = {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
        bundle(omit='BepInEx/core/BepInEx.Preloader.dll')
        with patch('manager.ensure_game_stopped'):
            try:
                api.setup_installer(game, data, archive=package)
                assert False, 'An incomplete BepInEx runtime must be rejected before game writes.'
            except ValueError as error:
                assert 'BepInEx.Preloader.dll' in str(error)
        assert snapshot == {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
        for unwanted in ('BepInEx/patchers/GK2.WorkshopAutoLoader.dll', 'BepInEx/plugins/GK2.Framework.dll',
                         'GK2ModInstaller.exe', '../outside.dll'):
            bundle(extra=unwanted)
            with patch('manager.ensure_game_stopped'):
                try:
                    api.setup_installer(game, data, archive=package)
                    assert False, 'Unsafe or unrelated payloads must not be installed.'
                except ValueError:
                    pass
            assert snapshot == {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}

        bundle(b'changed foundation')
        import manager
        real_copy = manager.copy_atomic

        def failing_copy(source, destination):
            if Path(destination).name == 'doorstop_config.ini' and 'payload' in Path(source).parts:
                raise OSError('Simulated interrupted install')
            return real_copy(source, destination)

        with patch('manager.ensure_game_stopped'), patch('manager.copy_atomic', side_effect=failing_copy):
            try:
                api.setup_installer(game, data, archive=package)
                assert False, 'An interrupted deployment must report failure.'
            except RuntimeError as error:
                assert 'Restore failures: []' in str(error)
        assert snapshot == {p.relative_to(game): p.read_bytes() for p in game.rglob('*') if p.is_file()}
    print('Integration checks passed (offline, temporary files only).')


if __name__ == '__main__':
    check()
