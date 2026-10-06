"""Run the real WebView2 shell and setup workflows against an isolated fixture."""
import json
from pathlib import Path
import queue
import socket
import sys
import tempfile
import time
from unittest.mock import patch

import app
import desktop
import webview


def main():
    assert sys.platform == 'win32', 'The native desktop check requires Windows.'
    reports, errors, private_calls, setup_checkpoint = queue.Queue(), [], [], {}
    original_dispatch = app.dispatch

    def dispatch(action, body):
        if action == '__test_report':
            reports.put(body)
            return {}
        if action == '__test_private':
            private_calls.append(body)
            return {}
        if action == '__test_prepare_uninstall':
            assert app.DATA == data, 'The uninstall fixture escaped its temporary data folder.'
            executable.write_bytes(b'GK2MT test fixture; not an executable')
            plugin.parent.mkdir(parents=True)
            plugin.write_bytes(b'GK2MT test fixture; not an executable DLL')
            preserved.write_text('Personal fixture settings', encoding='utf-8')
            return {'id': 'plugins:fixtureuninstall', 'path': plugin.relative_to(game).as_posix()}
        if action == '__test_prepare_workshop_setup':
            assert app.DATA == data and app.settings()['game'] == str(game)
            for relative in app.BEPINEX_FILES:
                target = game / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'GK2MT inert foundation fixture')
            loader_config.parent.mkdir(parents=True, exist_ok=True)
            loader_config.write_bytes(b'Personal fixture loader config')
            loader_trust.write_bytes(b'# Personal fixture trust marker\n')
            return {}
        if action == '__test_download_workshop_loader':
            from test_workshop_setup import pe_dll
            assert app.DATA == data and not loader_target.exists()
            loader_source.parent.mkdir(parents=True)
            loader_source.write_bytes(pe_dll())
            return {}
        if action == '__test_workshop_checkpoint':
            assert app.DATA == data and loader_target.read_bytes() == loader_source.read_bytes()
            setup_checkpoint.update(hash=app.manager.digest(loader_target), mtime=loader_target.stat().st_mtime_ns)
            return {}
        return original_dispatch(action, body)

    def no_server(*args, **kwargs):
        raise AssertionError('The desktop app attempted to open a listening socket/server.')

    with tempfile.TemporaryDirectory(prefix='gk2mt-desktop-test-') as temporary:
        folder = Path(temporary)
        data, game, workshop = folder / 'data', folder / 'game', folder / '4358690'
        for path in (data, game, workshop):
            path.mkdir()
        settings = {'game': str(game), 'workshop': str(workshop), 'import_folder': str(folder / 'imports')}
        settings_file = data / 'settings.json'
        settings_file.write_text(json.dumps(settings), encoding='utf-8')
        saved_settings = settings_file.read_bytes()
        executable = game / 'GraveyardKeeper2.exe'
        plugin = game / 'BepInEx/plugins/FixtureUninstall/Main.dll'
        preserved = plugin.parent / 'settings.cfg'
        loader_target = game / 'BepInEx/patchers/GK2_WorkshopLoader.dll'
        loader_source = workshop / '3807346541/BepInEx/patchers/GK2_WorkshopLoader.dll'
        loader_config = game / 'BepInEx/config/GK2_WorkshopLoader.cfg'
        loader_trust = game / app.inventory.TRUST
        untouched = folder / 'untouched.txt'
        untouched.write_text('Outside the mod folder', encoding='utf-8')
        app.nexus_credentials.save(data / 'nexus-key.bin', 'native-desktop-dummy-key')
        expected = json.dumps({'game': str(game), 'workshop': str(workshop), 'data': str(data)})
        script = r"""
(() => {
  if (location.href.split('#')[0] !== 'https://gk2mt.invalid/index.html' ||
      !window.pywebview?.api?.request || typeof state === 'undefined' || !state || working ||
      window.__gk2mtNativeTest) return;
  window.__gk2mtNativeTest = true;
  (async () => {
    const expected = EXPECTED;
    const check = (condition, message) => { if (!condition) throw Error(message); };
    const reply = await pywebview.api.request('state');
    check(reply.ok && reply.result.mods.length === 0, 'Real state bridge failed');
    check(reply.result.settings.game === expected.game && reply.result.settings.workshop === expected.workshop &&
          reply.result.data_path === expected.data, 'State escaped the isolated fixture');
    check(!reply.result.game_found && !reply.result.loader_installed && !reply.result.deck_connection.connected,
          'Unexpected real game or Deck state');
    check(document.getElementById('total').textContent === '0', 'Mod inventory did not render');
    check(document.getElementById('game-path').value === expected.game, 'Settings did not render');
    document.querySelector('[data-page="updates"]').click();
    check(reply.result.nexus_connected && reply.result.nexus_saved, 'Saved Nexus key was not restored');
    check(document.getElementById('nexus-key').value === '' && !document.getElementById('nexus-forget').hidden &&
          !document.getElementById('check-updates').disabled, 'Remembered-key controls did not render');
    state.updates = {account: {premium: false}, updates: [{id: 'fixture-update', name: 'Fixture mod',
      nexus_mod_id: 10, file_id: 20, installed_version: '1.0', version: '2.0', known_version: true,
      manual_installable: true, downloadable: false, blocked_reason: 'Nexus Premium is required for automatic downloads',
      changelog: 'Fixed scrolling.\nShows <literal> text safely.', changelog_version: '2.0'}]};
    renderUpdates();
    const notes = document.querySelector('.changelog-notes');
    check(notes && notes.textContent.includes('Fixed scrolling.\nShows <literal> text safely.') &&
          !notes.querySelector('literal'), 'Changelog text did not render safely');
    check(getComputedStyle(notes).whiteSpace === 'pre-wrap' &&
          document.querySelector('[data-update-archive="fixture-update"]'), 'Changelog layout or update action missing');
    const forgotten = await pywebview.api.request('nexus-forget');
    check(forgotten.ok, 'Forget key request failed');
    await refresh();
    check(!state.nexus_connected && !state.nexus_saved && document.getElementById('nexus-forget').hidden,
          'Forgetting the saved key did not update the controls');
    check(getComputedStyle(document.querySelector('.page-heading')).backgroundImage.includes('/banner.png'),
          'Bundled stylesheet did not load');
    const banner = new Image();
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(Error('Banner load timed out')), 5000);
      banner.onload = () => { clearTimeout(timeout); resolve(); };
      banner.onerror = () => { clearTimeout(timeout); reject(Error('Bundled banner failed to load')); };
      banner.src = '/banner.png';
    });
    check(banner.naturalWidth > 1000, 'Banner image is empty');
    document.querySelector('[data-page="deck"]').click();
    check(!document.getElementById('page-deck').hidden && location.hash === '#deck', 'Desktop navigation failed');
    const invalid = await pywebview.api.request('state', []);
    check(!invalid.ok, 'Bridge accepted a non-object body');
    const guide = await pywebview.api.request('deck-guide');
    check(guide.ok && guide.result.text.includes('sshd.service'), 'Bundled Deck guide failed');
    // A raw message must not be able to address hidden Python attributes.
    chrome.webview.postMessage(['_backend.dispatch', JSON.stringify(['__test_private', {}]), 'private-probe']);
    await new Promise(resolve => setTimeout(resolve, 150));
    location.assign('https://example.invalid/gk2mt-blocked-navigation');
    await new Promise(resolve => setTimeout(resolve, 500));
    check(location.href === 'https://gk2mt.invalid/index.html#deck', 'External navigation was not blocked');
    const after = await pywebview.api.request('downloads');
    check(after.ok && after.result.downloads.length === 0, 'Bridge stopped working after blocked navigation');
    const prepared = await api('__test_prepare_uninstall', {});
    await refresh();
    page('mods');
    check(state.game_found && state.mods.length === 1 && state.mods[0].id === prepared.id &&
          state.mods[0].can_uninstall, 'The isolated manual mod cannot be uninstalled');
    const actionButton = label => [...document.querySelectorAll('#dialog-actions button')]
      .find(button => button.textContent === label);
    const waitUntil = async (condition, message) => {
      const deadline = Date.now() + 8000;
      while (!condition()) {
        if (Date.now() > deadline) throw Error(`${message}. Dialog: ${document.getElementById('dialog-title').textContent}. Toast: ${document.getElementById('toast').textContent}`);
        await new Promise(resolve => setTimeout(resolve, 30));
      }
    };
    check(!state.duplicates.length && document.getElementById('duplicates-banner').hidden,
          'A single manual fixture produced a duplicate warning');
    const fixtureName = state.mods[0].name;
    document.querySelector('[data-page="profiles"]').click();
    check(!document.getElementById('page-profiles').hidden && location.hash === '#profiles',
          'The profile page did not open');
    document.getElementById('profile-create').click();
    check(document.getElementById('dialog').open && document.getElementById('profile-name') &&
          actionButton('Save profile'), 'New profile did not open its name modal');
    document.getElementById('profile-name').value = 'Native fixture profile';
    actionButton('Save profile').click();
    await waitUntil(() => !working && state.profiles.length === 1 && profileComparison?.entries.length === 1,
                    'Saving and comparing the current profile did not finish');
    const profileId = state.profiles[0].id;
    check(document.getElementById('profile-select').value === profileId &&
          profileComparison.entries[0].installed_id === prepared.id &&
          !profileComparison.blockers.length && !profileComparison.changes.length,
          'The saved profile did not match the installed manual mod');
    const profileCard = document.getElementById('profile-entries');
    profileCard.scrollIntoView({block: 'center'});
    await new Promise(resolve => requestAnimationFrame(resolve));
    const cardBounds = profileCard.getBoundingClientRect(),entry = profileCard.querySelector('.profile-entry');
    check(entry && cardBounds.width > 400 && cardBounds.left >= 0 && cardBounds.right <= innerWidth &&
          cardBounds.top >= 0 && cardBounds.bottom <= innerHeight &&
          document.documentElement.scrollWidth <= innerWidth + 1 &&
          parseFloat(getComputedStyle(entry).fontSize) >= 11 &&
          profileCard.textContent.includes(fixtureName) && profileCard.textContent.includes('Manual'),
          'The native profile card overflowed the viewport or lacked readable mod details');
    await api('toggle', {id: prepared.id, enabled: false});
    await refresh();
    document.getElementById('profile-refresh').click();
    await waitUntil(() => !working && !profileLoading && !state.mods[0].enabled &&
                         profileComparison?.changes.some(change => change.id === prepared.id && change.enabled),
                    'Refreshing the profile did not propose restoring the disabled mod');
    check(!state.duplicates.length && document.getElementById('duplicates-banner').hidden &&
          profileCard.textContent.includes('Enabled in profile'),
          'The disabled profile fixture produced duplicate warnings or lost its desired state');
    document.getElementById('profile-apply').click();
    await waitUntil(() => !working && document.getElementById('dialog-title').textContent === 'Review profile changes',
                    'The profile review did not open');
    check(document.getElementById('dialog-body').textContent.includes('Enable ' + fixtureName) &&
          actionButton('Apply profile') && !actionButton('Apply profile').disabled,
          'The profile review did not show an actionable enable proposal');
    actionButton('Cancel').click();
    const profileCancelled = await api('state');
    check(!document.getElementById('dialog').open && !profileCancelled.mods[0].enabled &&
          !profileCancelled.active_profile, 'Cancelling the profile changed the disabled mod or active selection');
    document.getElementById('profile-apply').click();
    await waitUntil(() => !working && document.getElementById('dialog-title').textContent === 'Review profile changes',
                    'The second profile review did not open');
    actionButton('Apply profile').click();
    await waitUntil(() => !working && state.mods[0].enabled && state.active_profile === profileId &&
                         !profileLoading && profileComparison?.changes.length === 0,
                    'Confirmed profile apply did not restore the mod and refresh the comparison');
    await waitUntil(() => !working && document.getElementById('dialog-title').textContent === 'Profile applied',
                    'The profile completion result did not open');
    check(document.getElementById('dialog-body').textContent.includes('Your mod setup is ready') &&
          actionButton('Back to profile'), 'The profile completion did not explain the result and next action');
    actionButton('Back to profile').click();
    check(!document.getElementById('dialog').open &&
          document.getElementById('profile-select').selectedOptions[0].textContent.includes('last applied') &&
          document.getElementById('profile-apply').classList.contains('done') &&
          document.getElementById('profile-apply').disabled &&
          document.getElementById('profile-apply').textContent.includes('Enabled states match this profile') &&
          !state.duplicates.length && document.getElementById('duplicates-banner').hidden,
          'The applied profile did not render its final state cleanly');
    document.querySelector('[data-page="mods"]').click();
    document.querySelector(`[data-details="${prepared.id}"]`).click();
    let uninstall = actionButton('Uninstall mod');
    check(uninstall && !uninstall.disabled, 'Details did not offer enabled Uninstall mod');
    uninstall.click();
    await waitUntil(() => !working && document.getElementById('dialog-title').textContent === 'Review mod uninstall',
                    'Uninstall preview did not finish');
    check(document.getElementById('dialog-body').textContent.includes(prepared.path) &&
          document.getElementById('dialog-body').textContent.includes('settings.cfg'),
          'Uninstall review did not show removed and preserved files');
    actionButton('Cancel').click();
    check(!document.getElementById('dialog').open, 'Cancel did not close the review');
    const cancelled = await api('state');
    check(cancelled.mods.length === 1, 'Cancel removed the manual mod');
    document.querySelector(`[data-details="${prepared.id}"]`).click();
    actionButton('Uninstall mod').click();
    await waitUntil(() => !working && document.getElementById('dialog-title').textContent === 'Review mod uninstall',
                    'Second uninstall preview did not finish');
    actionButton('Uninstall mod').click();
    await waitUntil(() => !working && state.mods.length === 0 &&
                         document.getElementById('dialog-title').textContent === 'Mod uninstalled',
                    'Confirmed uninstall did not finish or refresh inventory');
    check(document.getElementById('total').textContent === '0' &&
          document.getElementById('dialog-body').textContent.includes('uninstall-backups'),
          'Uninstall did not render the empty inventory and backup location');
    actionButton('Done').click();
    await api('__test_prepare_workshop_setup', {});
    await refresh();
    document.querySelector('[data-page="settings"]').click();
    check(state.loader_installed && state.workshop_setup.state === 'waiting_workshop' &&
          !document.getElementById('workshop-setup-banner').hidden &&
          !document.getElementById('workshop-loader-install').hidden &&
          !document.getElementById('workshop-loader-install').disabled &&
          !document.getElementById('workshop-subscribe').hidden,
          'The first-run Workshop banner and actionable setup step were missing');
    const setupCard = document.getElementById('workshop-loader-install').closest('.card');
    setupCard.scrollIntoView({block: 'center'});
    await new Promise(resolve => requestAnimationFrame(resolve));
    const setupBounds = setupCard.getBoundingClientRect();
    check(setupCard.textContent.includes('STEP 2') && setupBounds.width > 400 &&
          setupBounds.left >= 0 && setupBounds.right <= innerWidth &&
          setupBounds.top >= 0 && setupBounds.bottom <= innerHeight,
          'The Workshop setup card did not fit the native viewport');
    await api('__test_download_workshop_loader', {});
    document.getElementById('workshop-loader-install').click();
    await waitUntil(() => !working && state.workshop_setup.installed && state.workshop_loader.kind === 'workshop',
                    'Refresh & install did not copy and detect the downloaded Workshop loader');
    check(document.getElementById('workshop-setup-banner').hidden &&
          document.getElementById('workshop-loader-install').hidden &&
          document.getElementById('workshop-subscribe').hidden &&
          state.mods.length === 1 && !state.duplicates.length &&
          document.getElementById('workshop-loader-status').textContent === state.workshop_setup.message,
          'The installed Workshop loader did not render a clean completed state');
    await api('__test_workshop_checkpoint', {});
    const repeatedSetup = await api('workshop-loader-setup', {});
    check(repeatedSetup.installed && !repeatedSetup.files, 'Repeated loader setup replaced an installed loader');
    await refresh();
    check(state.workshop_setup.installed && document.getElementById('workshop-setup-banner').hidden,
          'Repeated setup lost the installed state');
    await pywebview.api.request('__test_report', {ok: true, bannerWidth: banner.naturalWidth,
      profileApplied: true, uninstalled: true, workshopInstalled: true});
  })().catch(error => pywebview.api.request('__test_report', {ok: false, error: String(error.stack || error)}));
})();
""".replace('EXPECTED', expected)

        def started(window):
            try:
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    window.run_js(script)
                    try:
                        result = reports.get(timeout=0.25)
                        assert result.get('ok'), result.get('error', result)
                        assert result.get('profileApplied'), 'The real profile workflow did not complete.'
                        assert result.get('uninstalled'), 'The real uninstall workflow did not complete.'
                        assert result.get('workshopInstalled'), 'The real Workshop setup workflow did not complete.'
                        assert not private_calls, 'Raw messages reached a private Python method.'
                        assert webview.http.global_server is None, 'A web server was started.'
                        break
                    except queue.Empty:
                        continue
                else:
                    raise AssertionError('The desktop window did not complete its native checks.')
            except BaseException as error:
                errors.append(error)
            finally:
                # Reporting passes through the mutation lock; wait for the request to
                # finish before closing, so the normal busy-close guard is respected.
                with app.LOCK:
                    pass
                window.destroy()

        def fixture_game_stopped():
            # A live game may be open; only the inert temporary fixture is changed.
            assert app.DATA == data and app.settings()['game'] == str(game)
            assert executable.read_bytes() == b'GK2MT test fixture; not an executable'

        with patch.object(app, 'DATA', data), patch.object(app, 'dispatch', dispatch), \
                patch.object(app.manager, 'ensure_game_stopped', fixture_game_stopped), \
                patch.object(app.integrations, 'steam_workshop_titles', return_value={'3807346541': 'Fixture Workshop Loader'}), \
                patch.object(webview.http, 'start_global_server', no_server), \
                patch.object(webview.http, 'start_server', no_server), \
                patch.object(socket.socket, 'bind', no_server):
            app.restore_nexus()
            desktop.run(app, started=started)
        if errors:
            raise errors[0]
        assert app.WINDOW is None and app.DECK_SESSION is None and not app.NEXUS_KEY
        assert not (data / 'nexus-key.bin').exists()
        assert settings_file.read_bytes() == saved_settings, 'The read-only checks changed settings.'
        assert not plugin.exists(), 'Confirmed uninstall left the plugin installed.'
        assert preserved.read_text(encoding='utf-8') == 'Personal fixture settings', 'Uninstall removed mod settings.'
        assert executable.read_bytes() == b'GK2MT test fixture; not an executable', 'Uninstall changed the game file.'
        assert untouched.read_text(encoding='utf-8') == 'Outside the mod folder', 'Uninstall changed an unrelated file.'
        assert {path.relative_to(workshop).as_posix() for path in workshop.rglob('*') if path.is_file()} == {
            '3807346541/BepInEx/patchers/GK2_WorkshopLoader.dll'}, 'Setup changed unexpected Workshop files.'
        assert loader_target.read_bytes() == loader_source.read_bytes(), 'Setup did not copy the exact fixture loader.'
        assert app.manager.digest(loader_target) == setup_checkpoint['hash'] and loader_target.stat().st_mtime_ns == setup_checkpoint['mtime'], 'Repeated setup changed the installed loader.'
        assert loader_config.read_bytes() == b'Personal fixture loader config', 'Setup changed the user loader configuration.'
        assert loader_trust.read_bytes() == b'# Personal fixture trust marker\n', 'Setup changed the user loader trust file.'
        saved_profiles = list(data.glob('libraries/*/profiles.json'))
        assert len(saved_profiles) == 1, 'The native test did not persist exactly one profile store.'
        profile_store = json.loads(saved_profiles[0].read_text('utf-8'))
        assert len(profile_store['profiles']) == 1 and profile_store['active'] == profile_store['profiles'][0]['id']
        assert profile_store['profiles'][0]['profile']['name'] == 'Native fixture profile'
        backups = list(data.glob('libraries/*/uninstall-backups/*'))
        assert len(backups) == 1, 'Cancel wrote a backup or confirmed uninstall did not save one.'
        assert (backups[0] / 'files' / plugin.relative_to(game)).read_bytes() == b'GK2MT test fixture; not an executable DLL'
        assert {path.relative_to(game).as_posix() for path in game.rglob('*') if path.is_file()} == {
            'GraveyardKeeper2.exe', 'BepInEx/plugins/FixtureUninstall/settings.cfg',
            'BepInEx/patchers/GK2_WorkshopLoader.dll', 'BepInEx/config/GK2_WorkshopLoader.cfg',
            app.inventory.TRUST, *app.BEPINEX_FILES}, 'Setup or uninstall changed unexpected fixture files.'
    print('Native desktop checks passed: serverless bridge/navigation, profile save/review/cancel/apply/layout, uninstall backup/settings, first-run Workshop install/config preservation, and clean shutdown.')


if __name__ == '__main__':
    main()
