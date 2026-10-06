"""Windows DPAPI roundtrip check: python test_nexus_credentials.py. Uses dummy keys only."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import nexus_credentials as credentials


def check():
    if os.name != 'nt':
        print('Nexus credential check requires Windows.')
        return
    first = 'gk2mt-dummy-key-one-not-a-real-credential'
    second = 'gk2mt-dummy-key-two-not-a-real-credential'
    with tempfile.TemporaryDirectory(prefix='gk2mt-credentials-test-') as folder:
        path = Path(folder) / 'data' / 'nexus-key.bin'
        assert credentials.load(path) == ''
        credentials.forget(path)
        credentials.save(path, first)
        original = path.read_bytes()
        assert original and first.encode() not in original
        assert credentials.load(path) == first
        subprocess.run([sys.executable, '-c',
                        'from pathlib import Path; import sys, nexus_credentials as c; '
                        'assert c.load(Path(sys.argv[1])) == sys.argv[2]', str(path), first],
                       cwd=Path(__file__).resolve().parent, check=True)

        with patch.object(credentials.os, 'replace', side_effect=OSError(second)):
            try:
                credentials.save(path, second)
                raise AssertionError('Failed replacement was accepted.')
            except RuntimeError as error:
                assert second not in str(error)
        assert path.read_bytes() == original and credentials.load(path) == first
        assert list(path.parent.iterdir()) == [path]
        for invalid in ('', ' ', first + '\n', '\ud800', '\U0001f512' * 5000):
            try:
                credentials.save(path, invalid)
                raise AssertionError('Invalid key was accepted.')
            except ValueError as error:
                assert str(error) == 'The Nexus API key is empty or invalid.'
            assert path.read_bytes() == original

        credentials.save(path, second)
        assert credentials.load(path) == second and second.encode() not in path.read_bytes()
        credentials.forget(path)
        credentials.forget(path)
        assert not path.exists() and credentials.load(path) == ''
        for invalid in (b'', b'not a DPAPI blob', b'x' * 65537):
            path.write_bytes(invalid)
            try:
                credentials.load(path)
                raise AssertionError('Invalid saved key was accepted.')
            except RuntimeError as error:
                assert 'could not be unlocked' in str(error)
        credentials.forget(path)
        deck_path = path.parent / 'deck-password.bin'
        password = '  p\u00e4ss\U0001f512\t\n '
        deck_value = json.dumps({'endpoint': 'deck@192.0.2.1:22', 'password': password}, ensure_ascii=False)
        credentials.save(deck_path, deck_value, label='Deck password')
        assert deck_value.encode('utf-8') not in deck_path.read_bytes()
        assert json.loads(credentials.load(deck_path, label='Deck password'))['password'] == password
        try:
            credentials.save(deck_path, '', label='Deck password')
            raise AssertionError('Empty Deck credential was accepted.')
        except ValueError as error:
            assert str(error) == 'The Deck password is empty or invalid.'
        deck_path.write_bytes(b'not a DPAPI blob')
        try:
            credentials.load(deck_path, label='Deck password')
            raise AssertionError('Invalid saved Deck credential was accepted.')
        except RuntimeError as error:
            assert 'saved Deck password could not be unlocked' in str(error) and 'Nexus' not in str(error)
        with patch.object(credentials.os, 'replace', side_effect=OSError(password)):
            try:
                credentials.save(deck_path, deck_value, label='Deck password')
                raise AssertionError('Failed Deck credential replacement was accepted.')
            except RuntimeError as error:
                assert 'encrypted Deck password' in str(error) and 'Nexus' not in str(error)
                assert password not in str(error)
        with patch.object(Path, 'unlink', side_effect=OSError(password)):
            try:
                credentials.forget(deck_path, label='Deck password')
                raise AssertionError('Failed Deck credential removal was accepted.')
            except RuntimeError as error:
                assert 'saved Deck password' in str(error) and 'Nexus' not in str(error)
                assert password not in str(error)
        credentials.forget(deck_path, label='Deck password')
        assert credentials.load(deck_path, label='Deck password') == ''
    print('Nexus credential encryption, restart, replacement, failure, and forget checks passed.')


if __name__ == '__main__':
    check()
