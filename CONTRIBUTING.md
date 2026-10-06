# Contributing to GK2MT

Useful contributions include reproducible bug reports, clearer setup flows, compatibility fixes, and small improvements to mod management. Keep each change focused on a concrete user problem.

## Report a problem

Describe what you tried, the steps to reproduce it, the expected result, and the actual result. Include the GK2MT version, Windows version, relevant mod names and versions, and whether they came from Steam, Nexus, or a manual ZIP. For Deck issues, include the game build IDs and Proton version if relevant.

Screenshots and short, relevant error excerpts help. Before posting them, remove API keys, passwords, website cookies, SSH key material, personal usernames and folder paths, Deck IP addresses, and private save information. Do not upload your app-data directory, browser profile, game assemblies, installed mod files, or downloaded mod archives. Link to the mod's public page instead.

Never use real credentials in a reproduction. If a credential was accidentally exposed, remove it from the report and replace or revoke it at its source.

## Submit a change

1. Read the [development guide](docs/DEVELOPMENT.md), create a branch, and reproduce the issue with disposable fixtures.
2. Make the smallest change that fixes the behaviour. Follow the existing Python and JavaScript style; add a dependency only when it is needed.
3. Run the relevant root tests. Add a focused behaviour check when a regression needs coverage, using synthetic files and mocked services.
4. Update user documentation if controls, setup steps, or limitations change.
5. Open a pull request explaining the problem, the resulting behaviour, and the checks you ran. Distinguish fixture tests from live website, game, or physical Deck validation.

UI changes should remain usable with a keyboard and in a compact window. Check focus, scroll position, disabled actions, loading/error states, and the next step shown to the user. Include a screenshot when it helps review the change, using dummy mod names and connection details.

## Protect user files

Maintain the existing review, version, identity, host-trust, and stale-state checks. Install, update, uninstall, and sync operations must preserve backups and avoid writing outside their intended folders. A scan or ordinary refresh should not silently install or deploy files. Download retries must revalidate the cached ZIP and the selected installation.

Development app-data isolation alone does not isolate the game folders. Use the test fixtures and never point automated tests at a working game or Steam Deck. Explain any change to file ownership, rollback, or credentials in the pull request.

## What belongs in a source commit

Include project source, documentation, and small synthetic test fixtures. Exclude executables, generated DLLs or ZIPs, game assemblies, third-party mod binaries, downloads, saves, personal settings, encrypted credential files, browser profiles, and `bin`/`obj` build output. The optional HotkeyManager uses locally installed game references; those references stay on your machine.

The repository has no bundled shared Nexus key. Keep credentials supplied by users outside the source tree; dummy test values should be clearly identified.

## License

GK2MT is licensed **GPL-3.0-only** under [LICENSE](LICENSE). Contributions must be your own work or material you have permission to contribute under that license. Preserve applicable notices and identify any third-party code and its license. Third-party dependencies, game files, and mods are not relicensed by this project.
