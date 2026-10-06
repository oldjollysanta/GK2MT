# GK2 Hotkey Manager 1.3.0

Manage mod bindings inside Graveyard Keeper 2's **Settings → Controls** panel. The manager reuses the game's rows, scrolling and controller navigation, imports recognized BepInEx config bindings, and offers a small registration API for mod authors.

## Install

Close the game. Install `GK2-Hotkey-Manager-1.3.0.zip` using **GK2MT → Install ZIP**, or extract it beside `GraveyardKeeper2.exe`. Replace the previous DLL; keep only one copy:

```text
BepInEx/plugins/HotkeyManager/GK2.HotkeyManager.dll
```

Requires the Windows Mono game and **BepInEx 5**. Verified on **game 1.008**, **Unity 6000.3.9f1**, **BepInEx 5.4.23.5**, Windows. Steam Deck uses the Windows game through Proton with your existing BepInEx setup. Physical Deck/controller testing remains outstanding. No GK2 Mod Framework or Configuration Manager dependency is required.

## Controls panel

Open **Settings → Controls**, **Ctrl+F10**, or **View + Menu / Back + Start**. Opening shortcuts can be changed in the mod rows or `BepInEx/config/local.gk2.hotkeymanager.cfg`.

- **Both / Game / Modded** filters affect the native keyboard and controller lists. The game switches between those lists when the input device changes.
- Hover a name for the description, owning plugin, config setting, identifiable config field and methods reading it. API rows show the registered callback. A reader method is evidence of where a setting is used, rather than a guarantee of the final downstream function.
- The read-only **Conditions** column shows rules declared by API mods. Hover its tags or focus a row for the full rules and custom description. Imported controls show **Not exposed** and native game controls show **Game**. Conditions are supplied by the author and cannot be edited here.
- Click the rebind arrow, release the controls used to open capture, hold a combination, then release every member. The editor records the largest set held together; sequential button presses cannot invent a chord. For imported controller chords, press the main button last.
- **Esc**, the **Cancel** button, or holding controller **B** for 1.2 seconds cancels capture. A short B press can still be bound.
- Overlapping bindings are orange. Hover for the conflicting names and whether the overlap is a conflict or a possible conflict. Proven mutually exclusive API rules remove the warning; an inventory-only action can share a chord with a no-menus action. Imported or custom conditions remain conservative when exclusivity cannot be proved. A proposed overlap requires **Keep conflict** / Enter / A, or **Cancel** / Esc / B. Existing conflicts do not prevent unrelated edits.
- **Suggest** saves an available binding supported by the selected setting. **Clear** disables it if the original mod permits an unbound value. **Trigger** switches Press / Release for game keyboard overrides and API actions; imported mods retain their own activation behavior.

Each successful edit saves immediately after any conflict confirmation, with a backup of the affected config. The native **OK** button closes the panel. Cancelled captures leave the original setting unchanged. Reopening rescans loaded mods. Restart for mods that cache config values.

The native restore-defaults button restores game keyboard controls and clears their chord/trigger overrides. It does not reset imported mod configs or API actions. Game keyboard chords survive restarts in this manager's config. Game controller rows retain the game's display-only behavior; mod controller rows are editable.

For native keyboard controls, Release changes `GetKeyDown` events. Held actions such as movement still run while the complete chord is held. Native overrides live in this mod's config, preserving the game's stored base bindings.

Controller navigation uses the game's focus controls: navigate with the D-pad and select a row/filter/button with A. Steam Input can map a rear Deck button to Ctrl+F10 or an unused keyboard chord. Rear buttons cannot be distinguished when they emit the same ordinary input.

## Reserved shortcuts

The editor, validation and API registration reject these system shortcuts, including left/right modifier variants and chords containing additional keys:

- Alt+F4; Ctrl+Alt+Delete; Ctrl+Shift+Esc.
- Alt+Tab; Alt+Esc; Ctrl+Esc; Alt+Space.
- Windows/Command keys and their combinations; Print Screen.

They are rejected before saving. API runtime also ignores blacklisted keyboard values loaded from manually edited config. This mod does not intercept operating-system shortcuts: physically pressing Alt+F4 can still close the game before capture receives it. Choose another combination when recording.

## Import support and limits

Recognizes `KeyboardShortcut`, Unity `KeyCode`, strings using game `GameKey` names for controller chords, recognized controller enums, paired enum fields, and documented joystick button indices. It edits the live original entries, preserving the source mod's validation and config callbacks. The local installation exposes **35 bindings across 12 plugins**, excluding the development harness.

An imported single-key or single-button setting keeps that limitation. The manager cannot transparently change hardcoded polling into multi-button logic. Mods that register with the API gain keyboard/controller chords, descriptions and press/release callbacks or polling handles; see **API.md**.

The API provides shared gameplay contexts (`GameplayOnly`, `NoMenusOpen`, `Anywhere`), exceptions for a mod's own windows and an optional integration helper. Version 1.3 adds composable rules for game loaded, loading, pause, typing, native menus/windows, dialogue/cutscenes, player controls and combat. Each has an opposite via `!`; named windows support open/closed/active checks. Combat uses the game's `ActiveFight` state. Unknown state blocks either direction rather than guessing.

Authors can register one action for both devices, reuse existing settings, and declare fallback bindings to exclude from import while managed. A per-action `Managed` guard prevents fallback polling even while menus block activation. Undeclared overlaps within an API-enabled plugin receive a diagnostic; arbitrary manual polling cannot be disabled automatically. The optional helper has no manager assembly reference. If requested 1.3 condition support is unavailable, it returns ownership to the whole action's fallback without dropping rules. See **API.md** and **Integration/OptionalHotkeys.cs**.

Conflicts compare physical buttons and subsets, then declared conditions. Unexposed conditions and unproven overlaps still warn. Gamepad action aliases are read from the live game bindings; raw joystick indices assume the common Xbox layout. Vanilla controller actions are not suppressed during ordinary play, and the displayed native controller rows are not included in automatic suggestions. A suggested mod chord can still overlap a vanilla action.

Capture suppresses normal game action/direction queries, BepInEx shortcut queries and loaded plugins' own Update/LateUpdate methods until the capture input is released. Input read by separate components or other hooks may still react. API actions pause while Controls is open, during capture, and when the game loses focus. New registrations default to gameplay context; existing 1.1 registrations retain their menu behavior. Built-in state is captured once per manager update. Blocking conditions cancel pending input and require release before rearming.

Disabled/unloaded mods, hardcoded bindings, separate loaders and unknown formats cannot be imported automatically. Descriptions and reader information depend on what each mod exposes. Mods adding custom `GameKey` entries to the native list are classified as modded controls.

## Debug mode

Blocked presses are silent during normal play. To diagnose an API action, set `[Debug] Enabled = true` in `BepInEx/config/local.gk2.hotkeymanager.cfg`. Complete blocked chords log their reason, with repeated frames suppressed. The API's `BlockedReason` property is populated only in debug mode. No invalid-press overlay is shown.

## Backups / uninstall

Changed configs are copied to `BepInEx/config/HotkeyManagerBackups/<UTC timestamp>-<unique suffix>/`. `restore-paths.txt` records the original locations. Close the game and copy the indicated `.cfg` files back to restore a backup. Failed config writes attempt to restore live values and original file bytes.

To uninstall, close the game and remove the DLL. Applied imported bindings remain in their original configs. Native keyboard controls return to the game's stored bindings; combination and release overrides require this plugin.

## Build / checks

With .NET SDK 8+ and your local game files:

```powershell
.\Build.ps1 -GameDir 'C:\Program Files (x86)\Steam\steamapps\common\Graveyard Keeper 2'
```

Builds the DLL, runs executable checks, and packages `dist/GK2-Hotkey-Manager-1.3.0.zip` in the GK2MT workspace, including optional integration source. Game/BepInEx assemblies and development harnesses are excluded. The game's reference assembly produces a System.IO.Compression version warning; the plugin builds successfully and does not use that API.

Release verification: **147 executable checks**, **45 native-panel checks**, and **23 optional-integration checks** passed. Compatibility runs passed **22 checks with manager 1.2** and **6 with no manager**. The keyboard and controller layouts were visually checked using an offscreen capture of the real native UI. Imported mod config hashes remained unchanged.

Checks cover discovery, type limits, physical and condition-aware conflicts, condition parsing/evaluation, suggestions, config backups/rollback, chord capture and activation, contexts, claim validation, rearming and the reserved-shortcut blacklist. `Tests/Smoke.csproj` is a development main-menu harness activated with `-gk2-hotkey-smoke <output-directory>`. `Tests/OptionalSmoke.csproj` builds without a manager/game-assembly reference and uses `-gk2-optional-smoke <output-directory>`; add `-gk2-manager-expected` when the manager is installed and `-gk2-declarations-expected` for manager 1.3 or later. The harnesses check native rows, contexts, callbacks, optional ownership/imports and controller focus without loading a save or applying user bindings.

Controller samples/focus and gameplay-state policy inputs are simulated; native windows and optional loading can be checked in-game. Physical controller, loaded-save activities and Steam Deck verification remain outstanding.
