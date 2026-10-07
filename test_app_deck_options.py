"""Saved Deck sync selections and fresh-preview guards; no real settings or SSH."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
import deck
import manager


def main():
    with TemporaryDirectory() as temporary:
        data = Path(temporary)
        saved = {'game': str(data / 'game'), 'workshop': str(data / '4358690'),
                 'quick_setup_completed': True, 'import_folder': 'keep-import-folder',
                 'deck': {'host': '192.0.2.50', 'user': 'deck', 'port': 22,
                          'game_path': '/home/deck/games/GK2',
                          'workshop_path': '/home/deck/workshop/4358690'}}
        manager.save_json(data / 'settings.json', saved)
        credential = data / 'deck-password.bin'
        credential.write_bytes(b'untouched encrypted fixture')
        choices = dict.fromkeys(deck.DEFAULT_OPTIONS, False) | {'configs': True}
        with patch.object(app, 'DATA', data), patch.object(app, 'PREVIEW', {'digest': 'old'}):
            assert app.settings()['deck_sync_options'] == deck.DEFAULT_OPTIONS
            original = (data / 'settings.json').read_bytes()
            try:
                app.dispatch('deck-sync-options', {'options': {'configs': 'yes'}})
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid options were saved.')
            assert (data / 'settings.json').read_bytes() == original
            assert app.PREVIEW == {'digest': 'old'}
            result = app.dispatch('deck-sync-options', {'options': choices})
            assert result['options'] == choices and app.PREVIEW is None
            current = app.settings()
            assert current['deck_sync_options'] == choices
            assert all(current[key] == value for key, value in saved.items() if key != 'deck')
            assert current['deck']['host'] == saved['deck']['host']
            assert credential.read_bytes() == b'untouched encrypted fixture'
            session = object()
            plan = {'digest': 'scoped-plan', 'options': choices}
            with patch.object(app, 'require_deck', return_value=session), \
                 patch.object(manager, 'ensure_game_stopped'), \
                 patch.object(deck, 'preview', return_value=plan) as compare, \
                 patch.object(deck, 'sync', return_value={'parity': True}) as synchronize:
                assert app.dispatch('deck-preview', {'options': choices}) == plan
                assert compare.call_args.kwargs == {'connection': session, 'options': choices}
                wrong = choices | {'mods': True}
                try:
                    app.dispatch('deck-sync', {'digest': plan['digest'], 'options': wrong})
                except ValueError as error:
                    assert 'selection changed' in str(error)
                else:
                    raise AssertionError('A different scope used an old preview.')
                assert app.PREVIEW is None
                synchronize.assert_not_called()
                app.dispatch('deck-preview', {'options': choices})
                assert app.dispatch('deck-sync', {'digest': plan['digest'], 'options': choices})['parity']
                assert synchronize.call_args.kwargs == {'connection': session, 'options': choices}
                assert app.PREVIEW is None
            empty = dict.fromkeys(deck.DEFAULT_OPTIONS, False)
            assert app.dispatch('deck-sync-options', {'options': empty})['options'] == empty
            try:
                app.dispatch('deck-preview', {'options': empty})
            except ValueError as error:
                assert 'at least one' in str(error)
            else:
                raise AssertionError('An empty comparison was accepted.')
    print('Deck sync bridge passed: saved scope, preserved settings/credentials, empty selection and changed-scope guards.')


if __name__ == '__main__':
    main()
