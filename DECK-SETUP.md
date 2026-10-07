# Connect GK2MT to your Steam Deck

Keep your PC and Deck on the same network and install Graveyard Keeper 2 on the Deck. GK2MT handles the Windows connection: no PowerShell commands, SSH key creation, or Windows SSH service setup is needed.

## 1. Turn on SSH on the Deck

Choose **Steam → Power → Switch to Desktop**, then open **Konsole**.

**Only if you have not set a system password yet**, run:

```sh
passwd
```

If you already have a password, use that one. Characters do not appear while typing. Valve describes Desktop Mode and `passwd` in its [Desktop FAQ](https://help.steampowered.com/en/faqs/view/671A-4453-E8D2-323C).

Then run:

```sh
sudo systemctl enable sshd.service
sudo systemctl start sshd.service
ip -4 addr show
```

Enter your system password when requested. The first command enables SSH after restarts; the second starts it now. Find your Wi-Fi interface’s `inet` address, such as `192.168.1.50`. Ignore `127.0.0.1` and leave off the `/24` suffix.

## 2. Connect in GK2MT

Open **Steam Deck** and enter:

| Field | Value |
|---|---|
| Deck IP address | The address from Konsole, such as `192.168.1.50` |
| Username | `deck`, unless you changed it |
| Deck system password | Your existing Deck password |

Select **Connect to Deck**. On the first connection, GK2MT asks you to trust the Deck’s identity and shows its fingerprint. To verify it, use the Konsole command shown in that dialog and compare the SHA256 fingerprints. Later connections check the saved identity automatically; a changed identity stops the connection.

GK2MT remembers the last address, username, and port between launches, even if a connection attempt fails. Password saving is off by default. Select **Remember password on this Windows account** before connecting to encrypt your password for this Windows account. On later launches, the password field stays blank and says **Saved password**; select **Connect to Deck** to use it. A saved password only applies to the same address, username, and port. If the Deck’s IP changes, enter the password again for the new address.

Select **Forget password**, or uncheck Remember, to remove the saved password; this also works while disconnected. **Disconnect** ends the current connection and keeps an opted-in saved password. Passwords are never written in plaintext to settings or included in mod sync. Without Remember, enter your password again after restarting GK2MT.

GK2MT finds the game and Workshop folders in the Deck’s Steam libraries. If multiple installations are found, or an installation cannot be found, open **Advanced connection & folders** to choose the paths. Select **Auto-detect on next connection** to clear folder overrides and connect again. The optional private-key field is for users who already have SSH keys; normal password connections do not need it.

Saved connection details are folded under **Deck settings**. Expand it to change the address, enter a password, or use Advanced options. Successful connection saves the details and folds the panel again. The Quick setup guide stays prominent while setup is incomplete.

Typical internal-storage paths are:

```text
/home/deck/.local/share/Steam/steamapps/common/Graveyard Keeper 2
/home/deck/.local/share/Steam/steamapps/workshop/content/4358690
```

For a custom or microSD installation, open **Graveyard Keeper 2 → Properties → Installed Files → Browse** in Deck desktop Steam. Copy the folder path from Dolphin’s address bar (`Ctrl+L`). The game folder must contain `GraveyardKeeper2.exe`; the Workshop folder ends in `steamapps/workshop/content/4358690`. Enter Linux paths here, not the PC’s `C:\...` paths.

## 3. Compare, then sync

Expand **What to sync** below the main buttons to choose **Mods**, **Mod configs**, **Workshop approvals**, **BepInEx & loaders**, and optional **Game preferences**. GK2MT saves these choices automatically and restores them next time you open the app. Changing a choice requires a fresh comparison. Unchecked categories stay unchanged on the Deck, including their extra files.

**Game preferences** copies audio volumes, language, and voice settings. The Deck keeps its display settings and controls. Launch the game once on both devices and save its settings before using this option. Game saves are never copied.

Close the game on both devices and pause Workshop downloads during transfer. Select **Compare PC & Deck**. GK2MT first reads each selected installation's Steam manifest and compares its installed build ID. Different builds, an unreadable/missing manifest, or an unfinished Steam installation stop the comparison before mod files are scanned. Finish Steam updates on both devices and use the same game branch; for an unknown build, confirm the game paths point to their Steam installations. The verified build appears above the mod preview.

The four main buttons stay together: **Connect to Deck → Compare PC & Deck → Install BepInEx → Deck Sync**. Each unlocks when its prerequisites are ready. If BepInEx loading needs setup, Install BepInEx unlocks after matching game builds are verified; instructions appear directly below it. Already configured loading skips that installation step. Setup automatically refreshes the comparison before syncing.

Review additions, changed files, and extra Deck files, then select **Deck Sync**. Game builds are checked again before syncing and on the Deck before files are replaced, so a Steam update invalidates an old comparison. GK2MT also checks mod hashes and refuses stale previews. Build IDs verify Steam's recorded installed version; they do not replace Steam's file-integrity verification. When managed files already match, no sync is needed.

By default, the transfer includes BepInEx core/plugins/patchers/config, disabled DLLs, Workshop loader trust settings, root loader files, and Workshop item folders. It preserves the PC’s current loader and approval decisions. Selected categories are mirrored, so extra Deck files in those categories are backed up and removed. Legacy loader and GK2Notepad paths remain in their respective categories so old Deck copies can be backed up and removed when absent on the PC. Sync does not enable Notepad. Other `CopyToGameFolder` payloads are reported as unsupported; do not treat that result as complete parity. A successful partial sync means the selected categories match.

## Game setup: enable BepInEx through Proton

This is separate from connecting SSH. GK2MT copies the PC’s Windows mods to the Windows game running through Proton.

In the Deck game’s **Properties → Compatibility**, use the Windows build with a Steam-provided Proton compatibility tool. Launch the game once so Steam creates its Proton environment, then close it and all other Wine or Proton games on the Deck.

After connecting SSH and comparing matching game versions, select **Install BepInEx** if loading is not configured. GK2MT finds this game's existing Proton environment and sets its persistent `winhttp` DLL override to `native,builtin`. It backs up the original `user.reg` under `steamapps/compatdata/4358690/gk2mt-backups`, then verifies the saved setting. Setup stops if the environment cannot be identified safely or Wine is still using a registry. It does not install Protontricks or change Steam launch options.

**Check BepInEx setup**, under Deck settings → Advanced connection & folders, reads the setting without changing it. The result separately reports whether the override is configured and Windows BepInEx files are installed. If the files are missing, compare and sync the PC mod setup. Launch the game afterward to confirm the mods load. Linux BepInEx is not a replacement for Windows BepInEx when running the Windows game through Proton.

An existing `WINEDLLOVERRIDES="winhttp=n,b"` launch option becomes redundant. Conflicting `WINEDLLOVERRIDES` values can override the registry setting: remove only the conflicting `winhttp` entry, keeping any other launch options you use.

If you prefer manual setup, the equivalent option in **Properties → General → Launch Options** is:

```text
WINEDLLOVERRIDES="winhttp=n,b" %command%
```

Preserve any launch options you already use. Both methods select BepInEx’s Windows loader; see [BepInEx’s Proton documentation](https://docs.bepinex.dev/master/articles/advanced/steam_interop.html#adding-the-dll-override).

## Backups and limits

Backups are under `.gk2mt-backups/<transaction-id>` inside each Deck target folder. Original relative paths are preserved. Failed replacements are rolled back where possible. An interrupted operation leaves `.gk2mt-sync.lock/transaction.json` in the game folder: inspect its backup locations and restore the previous files before removing that lock. Do not blindly delete the lock to retry.

Loader configuration has a separate registry backup, shown under **Setup details** after enabling BepInEx. Game preference sync also backs up `user.reg` under `steamapps/compatdata/4358690/gk2mt-backups/<transaction-id>` and preserves unrelated registry settings. To restore that whole registry file, close all Wine or Proton games first and keep a copy of the current `user.reg`; restoring an older registry also reverts any later registry changes in that game's Proton environment.

GK2MT does not copy saves, install the game, modify Steam subscriptions, or transfer Steam Workshop manifests. Subscribe to matching items in Steam if you want Steam to keep managing them. Later Steam/Vortex updates can change parity, so compare again after updates. Keep the same game version on both devices, allow Steam Cloud to finish before switching devices, and retain a save backup. Equal mod hashes help consistency; they do not guarantee save compatibility. Password connections and streaming have been tested against a local SSH server; sync checks use temporary files. A physical Deck test still needs your connection details.

To turn SSH off later, run `sudo systemctl disable --now sshd.service` in Deck Konsole.
