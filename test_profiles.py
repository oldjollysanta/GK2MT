"""Offline portable profile validation and exact selection planning."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import profiles


def manifest(mods=None):
    return {'format': 'GK2MT-profile', 'schema_version': 1, 'game_id': 4358690,
            'name': 'A shared selection', 'mods': mods or []}


def nexus(identifier=10, **values):
    return {'name': 'Example', 'source': 'Nexus', 'nexus_mod_id': identifier,
            'file_id': 20, 'version': '1.0', 'enabled': True} | values


def rejects(value):
    try:
        profiles.validate(value)
        raise AssertionError('Malformed profile accepted.')
    except ValueError:
        pass


def main():
    digest = hashlib.sha256(b'plugin bytes').hexdigest()
    manual = {'name': 'Local mod', 'source': 'Manual', 'version': 'Unknown', 'enabled': False,
              'dlls': [{'name': 'Example.DLL', 'sha256': digest.upper(), 'password': 'dummy-password'}]}
    raw = manifest([nexus(api_key='dummy-key', paths=['C:/private/mod.dll']), manual])
    raw.update(password='dummy-password', settings={'deck': {'host': 'private-host'}}, rules=[{'before': 'local-id'}])
    clean = profiles.validate(raw)
    encoded = json.dumps(clean)
    assert not any(secret in encoded for secret in ('dummy-', 'private-host', 'C:/private', 'local-id'))
    assert clean['mods'][0]['key'] == 'nexus:10'
    assert clean['mods'][1]['dlls'] == [{'name': 'example.dll', 'sha256': digest}]
    assert profiles.validate(clean) == clean
    assert profiles.same_version(' v1.0 ', '1.0.0') and profiles.same_version(' Release A ', 'release a')
    assert not profiles.same_version('1.0', '2.0') and not profiles.same_version('Release A', 'Release B')
    assert raw['mods'][0]['api_key'] == 'dummy-key', 'Validation must not mutate imported data.'

    for patch in ({'schema_version': True}, {'schema_version': 2}, {'game_id': True}, {'game_id': 1},
                  {'name': 'x' * 101}, {'mods': [nexus()] * 501}, {'mods': {}}, {'name': 'bad\x00name'}):
        rejects(manifest() | patch)
    for patch in ({'source': 'GK2MT'}, {'enabled': 1}, {'nexus_mod_id': True}, {'nexus_mod_id': -1},
                  {'nexus_mod_id': '0'}, {'file_id': False}, {'file_id': '1.5'}, {'key': 'nexus:999'}):
        rejects(manifest([nexus() | patch]))
    rejects(manifest([{'name': 'Steam', 'source': 'Steam', 'version': '1', 'enabled': True, 'workshop_id': True}]))
    for dll in ({'name': '../Example.dll', 'sha256': digest}, {'name': 'C:/Example.dll', 'sha256': digest},
                {'name': 'Example.dll', 'sha256': 'not a hash'}):
        rejects(manifest([manual | {'dlls': [dll]}]))
    rejects(manifest([manual | {'dlls': manual['dlls'] * 2}]))

    with TemporaryDirectory() as temporary:
        game = Path(temporary)
        disabled = game / 'BepInEx/plugins/Original/Example.dll.gk2mt-disabled'
        disabled.parent.mkdir(parents=True)
        disabled.write_bytes(b'plugin bytes')
        manual_row = {'id': 'plugins:original', 'name': 'Local name', 'source': 'Manual', 'version': 'Unknown',
                      'enabled': False, 'can_toggle': True, 'paths': ['BepInEx/plugins/Original/Example.dll']}
        nexus_row = {'id': 'managed-nexus', 'name': 'Nexus title', 'source': 'GK2MT', 'nexus_mod_id': 10,
                     'file_id': 20, 'version': '0.9', 'display_version': '1.0', 'enabled': True, 'can_toggle': True}
        steam_row = {'id': 'workshop:100', 'name': 'Steam title', 'source': 'Steam Workshop', 'workshop_id': '100',
                     'version': '2.0', 'enabled': False, 'can_toggle': True}
        selection = profiles.capture('Portable', [manual_row, nexus_row, steam_row], game)
        assert [entry['source'] for entry in selection['mods']] == ['Manual', 'Nexus', 'Steam']
        assert selection['mods'][0]['dlls'] == [{'name': 'example.dll', 'sha256': digest}]
        assert selection['mods'][1]['version'] == '1.0' and selection['mods'][2]['key'] == 'steam:100'
        assert not any(value in json.dumps(selection) for value in ('plugins:original', 'managed-nexus', 'Original/'))

        moved = game / 'BepInEx/plugins/Moved/Example.dll'
        moved.parent.mkdir(parents=True)
        moved.write_bytes(b'plugin bytes')
        portable_rows = [manual_row | {'id': 'different-local-id', 'name': 'Different title', 'paths': ['BepInEx/plugins/Moved/Example.dll']},
                         nexus_row | {'id': 'different-nexus-id', 'name': 'Renamed Nexus'}, steam_row | {'name': 'Renamed Steam'}]
        original = copy.deepcopy(portable_rows)
        result = profiles.compare(selection, portable_rows, game)
        assert not result['blockers'] and not result['changes'] and not result['mismatches']
        assert all(entry['status'] == 'installed' for entry in result['entries'])
        assert result['entries'][0]['installed_id'] == 'different-local-id'
        assert portable_rows == original

        changed_version = profiles.compare(selection, [manual_row, nexus_row | {'display_version': '2.0'}, steam_row], game)
        assert len(changed_version['mismatches']) == 1 and changed_version['mismatches'][0]['installed_version'] == '2.0'
        unknown = profiles.compare(selection, [manual_row, nexus_row | {'display_version': 'Unknown'}, steam_row], game)
        assert len(unknown['mismatches']) == 1 and 'cannot be verified' in unknown['mismatches'][0]['reason']
        equivalent = profiles.compare(selection, [manual_row, nexus_row | {'display_version': 'v1.0.0'}, steam_row], game)
        assert not equivalent['mismatches']
        variant = profiles.compare(selection, [manual_row, nexus_row | {'file_id': 21}, steam_row], game)
        assert len(variant['mismatches']) == 1 and variant['mismatches'][0]['installed_file_id'] == 21
        assert 'file variant' in variant['mismatches'][0]['reason']
        unknown_file = profiles.compare(selection, [manual_row, nexus_row | {'file_id': None}, steam_row], game)
        assert len(unknown_file['mismatches']) == 1 and 'cannot be verified' in unknown_file['mismatches'][0]['reason']
        loose = profiles.compare(manifest([nexus(version='Unknown', file_id=None)]),
                                 [nexus_row | {'display_version': 'Unknown', 'file_id': None}], game)
        assert not loose['mismatches'] and not loose['blockers']
        changed_source = profiles.compare(manifest([nexus()]), [steam_row | {'name': 'Example', 'workshop_id': 10, 'enabled': True}], game)
        assert changed_source['entries'][0]['status'] == 'missing' and changed_source['blockers']
        assert changed_source['changes'][0]['source'] == 'Steam' and not changed_source['changes'][0]['enabled']

        missing = profiles.compare(manifest([nexus(11), nexus(12, enabled=False)]), [], game)
        assert len(missing['missing']) == 2 and len(missing['blockers']) == 1 and not missing['changes']
        mismatch_manual = copy.deepcopy(selection)
        mismatch_manual['mods'] = [mismatch_manual['mods'][0] | {'enabled': True}]
        moved.write_bytes(b'changed DLL')
        result = profiles.compare(mismatch_manual, [portable_rows[0]], game)
        assert result['missing'] and result['blockers'], 'Manual matching requires the DLL hash, not its name.'

        duplicate = profiles.compare(manifest([nexus(), nexus(file_id=21)]), [nexus_row], game)
        assert all(entry['status'] == 'ambiguous' for entry in duplicate['entries']) and duplicate['blockers']
        assert not duplicate['changes']
        duplicate = profiles.compare(manifest([nexus()]), [nexus_row, nexus_row | {'id': 'another-copy'}], game)
        assert duplicate['entries'][0]['status'] == 'ambiguous' and duplicate['blockers'] and not duplicate['changes']

        disabled_managed = {'id': 'disabled-managed', 'name': 'Managed local mod', 'source': 'GK2MT', 'version': '1',
                            'enabled': False, 'can_toggle': True, 'paths': ['BepInEx/plugins/NotDeployed.dll'],
                            '_profile_dlls': [{'name': 'Example.dll', 'sha256': digest}]}
        own = profiles.capture('Disabled payload', [disabled_managed], game)
        assert '_profile_dlls' not in json.dumps(own) and own['mods'][0]['dlls'][0]['sha256'] == digest
        assert profiles.compare(own, [disabled_managed], game)['entries'][0]['status'] == 'installed'

        protected = [nexus_row | {'nexus_mod_id': 48},
                     {'id': 'loader', 'name': 'Workshop loader', 'source': 'Manual', 'enabled': True, 'can_toggle': True,
                      'paths': ['BepInEx/patchers/GK2_WorkshopLoader.dll.gk2mt-disabled']},
                     steam_row | {'id': 'unsupported', 'can_toggle': False, 'enabled': True}]
        assert not profiles.capture('Common setup', protected, game)['mods']
        result = profiles.compare(manifest(), protected + [nexus_row, manual_row], game)
        assert result['changes'] == [{'id': nexus_row['id'], 'name': nexus_row['name'], 'source': 'Nexus', 'enabled': False}]
        assert not result['blockers']
        assert not profiles.validate(manifest([nexus(48)]))['mods']

    print('Profiles passed: sanitized manifests, portable exact identities, versions, ambiguity, protected setup and toggle plans.')


if __name__ == '__main__':
    main()
