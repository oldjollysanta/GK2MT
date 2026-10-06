# GK2MT User Guide

**Graveyard Keeper 2 Mod Toolkit** brings Steam Workshop, Nexus, and manual mods into one Windows desktop app. Use it to set up mod support, manage your installed mods, switch profiles, and compare or sync your PC setup with a Steam Deck.

[Quick Setup](#quick-setup) · [Add mods](#add-mods) · [Manage mods](#manage-mods) · [Updates](#updates) · [Profiles](#profiles) · [Steam Deck](#steam-deck) · [Backups and privacy](#backups-and-privacy) · [Troubleshooting](#troubleshooting)

## Quick Setup

Download [GK2MT.exe](https://github.com/oldjollysanta/GK2MT/releases/latest/download/GK2MT.exe) from the [latest release](https://github.com/oldjollysanta/GK2MT/releases/latest), then double-click it. You need **Windows x64** and the **Microsoft Edge WebView2 Runtime**; no Python installation or source build is needed. Settings stay in `%LOCALAPPDATA%\GK2MT`, so moving the executable does not reset your setup.

Quick Setup opens on first use and continues to appear until you choose **Finish setup**. Green fields are ready, yellow fields need attention, and empty optional fields stay neutral. Reopen it anytime from **Locations & setup**.

1. **Confirm your folders.** GK2MT detects Steam libraries. Choose the game folder containing `GraveyardKeeper2.exe` and the Workshop folder ending in `steamapps\workshop\content\4358690`. The optional bulk ZIP import folder defaults to `%USERPROFILE%\Downloads\GK2MT`.
2. **Set up BepInEx.** Existing installations are detected. For a new setup, use the [BepInEx bundle on Nexus #48](https://www.nexusmods.com/graveyardkeeper2/mods/48) through **Download & set up**, or choose **Use downloaded ZIP**. Automatic download requires a connected Nexus Premium account. Existing mod configs and your Workshop loader are preserved; replaced files are backed up.
3. **Set up Workshop support if you want it.** Open the loader's Steam page from the setup controls, subscribe to **GK2 Workshop Loader**, and wait for Steam Downloads to finish. Choose **Refresh & install loader**; GK2MT copies its validated DLL into the game's patchers folder. Leave Workshop setup unchecked if you only want Nexus or manual mods.
4. **Add optional connections.** Expand Nexus to enter your personal API key, or Steam Deck to enter its connection details. You can do either later.
5. Choose **Finish setup**, then launch the game to confirm mod support works. Approve Workshop mods if the loader asks.

Close the game before installing, updating, enabling, disabling, uninstalling, or syncing mods. Scanning your installation does not install or replace a loader automatically.

## Add mods

### ZIP files

Drag one or more ZIPs onto GK2MT, or choose **Install ZIP** in **My mods**. To install a collection of downloaded ZIPs, put them in your configured import folder and choose **Import folder**.

GK2MT checks the archive layout and shows a review before installing. Standard BepInEx plugin and patcher layouts, including bare plugin DLLs, are supported. Identical archives and identical mod payloads are skipped, keeping the existing enabled state and settings.

If a matching Steam or local copy exists, the review shows it—even if that copy is disabled. Installing another copy requires an explicit acknowledgement. Keep only one copy enabled before launching.

ZIP validation checks format and paths; it does not prove a mod is trustworthy. Custom game-file installers, FOMOD packages, and RAR/7z archives need their documented installation method. Use the dedicated BepInEx setup controls for the foundation bundle.

### Nexus downloads

Connect your personal Nexus API key in **Quick Setup** or **Updates → Accounts & Steam**. Then choose **Browse Nexus → Open Nexus browser**.

The official Nexus website opens inside GK2MT. Search or browse, read the mod's requirements, and choose **Manual Download**. For a Free account, choose **Slow Download**. Sign in to the website if prompted; website login and the API connection are separate.

The completed ZIP goes into GK2MT's managed download folder, is verified against Nexus, and installs automatically when its layout is supported. The panel closes after the first completed ZIP, and the mod appears in **My mods**. Existing Nexus mods follow the update path. Duplicate copies pause for review.

Only one download session runs at a time. Use the download indicator or **Download activity & results** to see progress, cancel, review a duplicate, or retry installation.

### Steam Workshop

Subscribe to mods in Steam and wait for downloads to finish. Choose **Rescan** in **My mods** to refresh the inventory and published Workshop names. Launch the game and approve new or changed mods when the Workshop loader requests it.

Steam manages Workshop subscriptions and updates. GK2MT can open an item's Workshop page, but does not subscribe on your behalf.

## Manage mods

**My mods** shows each mod's enabled state, name, version, category, and **Steam**, **Nexus**, or **Manual** source. Search by name and filter by source, state, or category. Nexus categories are fetched when connecting or checking updates and remain cached offline.

- **Enable or disable:** use the checkbox. Workshop changes and loader approval take effect on the next game launch.
- **Inspect or edit details:** open the **⋯** action menu. Add a Nexus mod ID and, when needed, a file ID to track a release that was not detected automatically.
- **Uninstall:** choose **Uninstall mod**, review the exact removed or restored files, and confirm. Configs are kept and the result gives the backup location. Remove Steam mods by unsubscribing in Steam. The BepInEx foundation cannot be removed through this action.
- **Resolve file conflicts:** use **Conflict rules** for packages imported into GK2MT. Before/after rules determine which package supplies a shared file; later packages win. These are file overwrite rules, not BepInEx runtime plugin load-order rules.

GK2MT works independently of Vortex and can discover existing installations. Avoid having both managers deploy the same mods. Vortex may restore a mod you remove in GK2MT unless you also disable or remove it there. Rescan after another manager changes files.

## Updates

Open **Updates** and choose **Check for updates**. Each result shows the installed and available versions, release notes when published, and the relevant install action. Steam Workshop updates remain in Steam Downloads.

| Account or method | Update flow |
|---|---|
| Nexus Free | Choose **Download & update**, approve **Manual Download → Slow Download** in the GK2MT panel, then let GK2MT verify and install the ZIP. |
| Nexus Premium | Use an individual **Update** button or **Download & update all** for eligible releases. |
| Already downloaded ZIP | Choose **Update from ZIP**, select the archive, review it, and confirm installation. A Nexus connection is required to verify its identity. |

Unknown versions and ambiguous file variants are skipped rather than guessed. Choose the correct file variant or fix the mod's tracking details before updating. Missing changelogs are labelled; notes from a different release are not substituted.

Updates keep enabled states and existing configs, back up replaced files, and remove obsolete files from the old release. If installation fails after verification, **Retry installation** can reuse the completed ZIP while rechecking its identity and the installed files. If the game is running, close it before retrying.

Managed panel downloads live in `%LOCALAPPDATA%\GK2MT\nexus-downloads\<download-id>`, separate from the bulk import folder. Closing GK2MT ends the download job. After restarting, use **Update from ZIP** to reuse a completed `download.zip` in that folder; it will be verified again.

## Profiles

Profiles save a named mod selection and let you share it without sharing mod archives or private settings.

To save one, set your enabled mods in **My mods**, open **Mod profiles**, and choose **New profile**. Enter a name and save. Use **Import profile** for a shared JSON file. **Profile options & sharing** contains export, replacement, and deletion controls. Deleting a profile keeps its installed mods.

The three action cards guide you through a selection:

1. **Check mods** compares the profile with this installation.
2. **Get mods ready** brings missing mods, ambiguous copies, and release differences into view. Install each Nexus entry, open a Steam entry's Workshop page to subscribe, or import a manual ZIP. After Steam finishes downloading, use **Refresh Steam mods**. The profile is not applied automatically.
3. **Review & apply** shows every proposed enable/disable change. Missing required mods and pending downloads keep this step locked. Release differences require the requested version or explicit acceptance of the installed releases.

When Workshop approval remains, the final card becomes **Launch to approve**. Approve those mods in the game, close it, and return to **Check again**. The **last applied** label records the last selected profile; later mod changes can make its current setup differ.

Profiles retain source identity: a Steam copy does not satisfy a Nexus entry for the same mod. BepInEx, Workshop loaders, configs, and conflict rules stay shared across profiles. Exported profiles exclude game files, saves, personal paths, configs, API keys, and passwords. Compare the Deck again after switching profiles.

## Steam Deck

Use the Windows game through Proton on the Deck. GK2MT handles SSH on Windows; you do not need Windows OpenSSH, PowerShell commands, or a new SSH key.

### Enable SSH once

Keep both devices on the same network. On the Deck, choose **Steam → Power → Switch to Desktop**, then open **Konsole**.

Only if a system password is not already set, run:

```sh
passwd
```

Then enable SSH and find the Deck's address:

```sh
sudo systemctl enable sshd.service
sudo systemctl start sshd.service
ip -4 addr show
```

Use the Wi-Fi `inet` address, leaving off the `/24` suffix. Ignore `127.0.0.1`.

### Connect, compare, and sync

Open **Steam Deck** in GK2MT. Enter the address, username (normally `deck`), and system password. The top action cards unlock in order:

1. **Connect** checks the Deck's identity and detects game folders. On the first connection, compare the fingerprint using the command shown in the trust dialog. The last address is remembered; **Remember password on this Windows account** is optional. Saved settings fold away and remain editable.
2. **Compare** first verifies matching installed Steam game build IDs. Different, unknown, or unfinished builds stop comparison. Finish Steam updates on both devices and use the same game branch. Close both games before continuing.
3. **Install BepInEx**, when needed, prepares Windows BepInEx loading in the game's existing Proton environment. Launch the game through Proton once beforehand, then close all Wine or Proton games. GK2MT backs up the registry setting and refreshes the comparison automatically. Already configured loading skips this step.
4. **Deck Sync** backs up affected Deck files, copies the PC's supported mod setup, and verifies file hashes. Review the changes first. If files already match, use **Compare again** when needed; no transfer is required.

Sync includes supported BepInEx files, configs, disabled DLLs, Workshop folders, and loader approval settings. It does not copy saves or change Steam subscriptions. Unsupported custom payloads are reported, so a result with those items should not be treated as a complete match.

GK2MT configures the game's persistent `winhttp` override without changing Steam launch options. Linux BepInEx does not replace Windows BepInEx for the Windows game through Proton. See the [full Deck guide](../DECK-SETUP.md) for folder overrides, existing launch options, and recovery.

Matching mods helps consistency when switching devices, but does not guarantee save compatibility. Let Steam Cloud finish before moving between PC and Deck and keep your own save backup.

## Backups and privacy

App settings, package libraries, downloads, and PC recovery files live under `%LOCALAPPDATA%\GK2MT`. Each game installation has its own library. Keep that library while using its packages: original files needed for restoration are stored there.

Setup and uninstall results show their backup paths. Deck sync backups stay on the Deck under `.gk2mt-backups/<transaction-id>` in the target folders; BepInEx registry setup has a separate backup. If rollback cannot finish, GK2MT reports the recovery location.

Saved Nexus keys and optional Deck passwords are encrypted for the current Windows account. They are not included in profiles or mod sync. Use **Forget key** or **Forget password** to remove them. Website login is separate; sign out inside the Nexus panel to end that session.

GK2MT is open source; see the [project license](../LICENSE). No shared Nexus API key is bundled: connect your own account when you want Nexus integration. Third-party libraries and installed mods retain their own licenses.

## Troubleshooting

| What you see | What to do |
|---|---|
| BepInEx missing or incomplete | Reopen Quick Setup or use the BepInEx setup controls in Locations & setup. |
| Workshop mod missing | Wait for Steam Downloads, then Rescan. Confirm the Workshop loader is installed and approve the mod at launch. |
| Duplicate Steam/Nexus copies | Disable the unwanted copy and uninstall it through the appropriate source. Keep one enabled copy. |
| Unknown or ambiguous update | Check the Nexus mod/file IDs and select the matching release. GK2MT will not choose a variant automatically. |
| Installation failed | Read the warning, close the game, and resolve the named file or ownership issue. Use Retry installation when offered; a changed target may require a fresh update check. |
| Deck actions locked | Follow the next highlighted card: connect, find folders, match game builds, and check BepInEx loading. |
| Interrupted Deck sync | Follow the recovery details in the full Deck guide. Do not delete `.gk2mt-sync.lock` before inspecting its transaction and backups. |

GK2MT supports standard plugin and patcher packages. It does not install the game, distribute mod files, guarantee compatibility between mods, or replace each mod author's requirements and installation notes.
