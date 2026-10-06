# Hotkey action API — 1.3.0

Register a logical action once: keyboard and controller invoke the same callback and share trigger/condition rules. Stable action IDs identify saved settings. The manager supplies native Controls rows, capture, persistence, blacklist validation, condition-aware conflict warnings and input detection.

## Optional integration (recommended)

Copy **Integration/OptionalHotkeys.cs** into your own project and compile it into your DLL. Reference your normal BepInEx/Unity assemblies; **do not reference or bundle GK2.HotkeyManager.dll**. The source helper uses a versioned reflection bridge containing only system, BepInEx and Unity types. Add BepInEx's soft dependency so the manager initializes first when present:

```csharp
using BepInEx;
using BepInEx.Configuration;
using GK2.HotkeyManager.Integration;
using UnityEngine;

[BepInPlugin("example.gk2.lamp", "Example Lamp", "1.0.0")]
[BepInDependency(OptionalHotkeys.PluginGuid,
    BepInDependency.DependencyFlags.SoftDependency)]
public sealed class ExamplePlugin : BaseUnityPlugin
{
    ConfigEntry<KeyboardShortcut> keyboard;
    ConfigEntry<string> controller;
    OptionalHotkeyAction toggle;
    bool lampOn;

    void Awake()
    {
        // Keep existing section/key names to retain user settings.
        keyboard = Config.Bind("Controls", "Keyboard",
            new KeyboardShortcut(KeyCode.J, KeyCode.LeftControl));
        controller = Config.Bind("Controls", "Controller", "LB+Y",
            "Controller physical button chord.");
        toggle = OptionalHotkeys.Register(this, "toggle-lamp", "Toggle lamp",
            new OptionalHotkeyOptions {
                KeyboardConfig = keyboard,
                ControllerConfig = controller,
                Callback = ToggleLamp,
                Trigger = HotkeyTrigger.Release,
                Context = HotkeyContext.GameplayOnly,
                Description = "Turn the player's lamp on or off."
            });
    }

    void Update()
    {
        // Put ALL manual input paths for this action inside this guard.
        if (!toggle.Managed && keyboard.Value.IsDown()) ToggleLamp();
    }

    void ToggleLamp() { lampOn = !lampOn; /* Apply your lamp state here. */ }
    void OnDestroy() => toggle?.Dispose();
}
```

This minimal example's fallback is keyboard-only. Put your existing controller handler and gameplay checks inside the same ownership guard if your mod has them. Register on Unity's main thread after binding config entries. The returned handle is always usable, even when registration fails. `UnavailableReason` explains absence, an older manager or a rejected registration.

The helper uses the 1.2 `RegisterOptionalV1` bridge for context-only registrations. Supplying `Conditions` or `CustomConditionDescription` requires the 1.3 `RegisterOptionalV2` bridge. If that bridge is unavailable, the **whole action** remains unmanaged and uses your fallback; the helper never silently drops its conditions. Manager 1.1 lacks both optional bridges. Authors remain responsible for equivalent checks in their fallback.

**Managed means ownership, not permission to activate.** It stays true while menus, your `Enabled` condition, disabled owner, focus loss or capture block the action. Never choose fallback based on `WasTriggered`, `IsHeld` or a gameplay condition: that bypasses restrictions. Ownership becomes false after disposal or while the manager is inactive. Removing the manager takes effect on the next launch. Callback exceptions are logged and do not turn on fallback.

## Shared config and fallback declarations

`KeyboardConfig` accepts an existing `ConfigEntry<KeyboardShortcut>`; `ControllerConfig` accepts a `ConfigEntry<string>` of physical button names/chords. Registration preserves their current values and uses these exact entries for editing. Game action aliases and controller enums are not interchangeable with physical chord strings. Without shared entries, `DefaultKeyboard` / `DefaultController` initialize entries under `[Hotkeys.<actionId>]` in your own config.

Declare additional legacy settings for this action with `FallbackBindings = new ConfigEntryBase[] { oldKey, oldController }`. Shared entries are automatically claimed. While managed, claimed entries are excluded from automatic import and conflict checks; separate fallback values remain unchanged. Unconverted actions from the same mod still appear. Disposal releases the claims. Foreign config entries or bindings already claimed by another action are rejected.

An undeclared imported binding that overlaps an API action from the same plugin gets a tooltip/log diagnostic. This is a possible fallback, not proof of duplicate execution. Hiding settings cannot stop arbitrary polling code; the author's ownership guard prevents duplicate firing.

Reusing compatible settings lets ordinary rebinds survive removal. Separate fallback entries retain their previous values. Advanced chords and release activation continue without the manager only if the mod's own fallback implements them. This helper does not install a second input engine.

## Gameplay contexts

New registrations default to `GameplayOnly`. Optional enums are `HotkeyContext` / `HotkeyTrigger`; direct API enums are `ActionContext` / `TriggerMode`.

| Context | Condition |
| --- | --- |
| `GameplayOnly` | Loaded game/save/player, active controllable player, no open native windows, pause, loading overlay or focused text entry. Game control locks cover flow, cinematics, death, teleporting and other activities. |
| `NoMenusOpen` | Initialized UI, no open native windows or focused text entry. Independent of a loaded save and player control. |
| `Anywhere` | No additional gameplay restriction. Focus, capture, Controls-panel suppression, owner state and `Enabled` still apply. |

The native window stack includes inventory, crafting, settings and dialog windows, including windows underneath the top one. Ordinary HUD widgets are outside the stack. Custom IMGUI/non-native panels need an author-supplied `Enabled` condition. `NoMenusOpen` alone is not a cutscene/dialogue safety check; use `GameplayOnly` for gameplay actions.

`Enabled = () => YourModCondition()` adds an AND condition. It never overrides the built-in context or declared conditions. Exceptions cancel activation and are logged. Set `CustomConditionDescription` to explain this predicate in the Conditions tooltip. Arbitrary predicate code cannot establish that two actions are mutually exclusive.

To let a toggle close its own native window, retain its instance and set `AllowWindow = window => ReferenceEquals(window, myPanelWindow)`. Every open window must be permitted. An unrelated window underneath the panel still blocks. For `GameplayOnly`, an explicitly allowed window can exempt its modal-UI pause and `ByUI` control lock, while loading, text input and other control locks still block. Permit only your intended windows.

Blocking conditions cancel pending release and require each device's configured chord to become neutral before rearming. A held key cannot fire merely because a menu closes. Binding/trigger changes use the same cancellation rule.

Direct integrations may query `GameplayContexts.NoMenusOpen`, `GameplayContexts.GameplayOnly`, or `GameplayContexts.IsAllowed(context, allowWindow)` on Unity's main thread. These report game context only; action polling applies focus/capture suppression separately.

## Declarative conditions

Use `Conditions` on either `HotkeyOptions` or `OptionalHotkeyOptions` to declare when an action can run. All entries must pass (**AND**). `null`, the default, retains the `Context` preset. A non-null array **replaces** the preset; it does not add to it. An empty array explicitly removes context rules, while focus/capture suppression, owner state and `Enabled` still apply.

```csharp
Conditions = new[] {
    "GameLoaded", "!Loading", "!Paused", "!Typing",
    "!AnyMenuOpen", "!DialogueOrCutscene", "ControlsEnabled"
}
```

Use the `!` prefix for the opposite of a condition. There is no separate enum value to learn for each opposite:

| Condition | Meaning when positive |
| --- | --- |
| `GameLoaded` | The game is in-game with a save and player data. |
| `Loading` | The native loading overlay is shown. |
| `Paused` | The game reports a paused state. |
| `Typing` | A native Unity/TMP text input is focused. |
| `AnyMenuOpen` | At least one native window is in the window stack. |
| `InCombat` | The game's fight controller reports `ActiveFight`. This does not infer combat from an attack button or nearby enemies. |
| `DialogueOrCutscene` | A native dialogue/input-dialogue window, active dialogue bubble, cinematic UI or cinematic control lock is active. |
| `ControlsEnabled` | The player is active and its controls are enabled. |
| `WindowOpen:Inventory` | Inventory is open, even beneath another window. |
| `WindowClosed:Inventory` | Inventory is closed; an alias for `!WindowOpen:Inventory`. |
| `WindowActive:Inventory` | Inventory is the active native window/tab. |

Convenience names also include `NoMenusOpen`, `OutOfCombat`, `NotTyping`, `NotPaused`, `NotLoading`, `NoDialogueOrCutscene`, `ControlsDisabled` and `GameNotLoaded`. They are aliases for the corresponding negated tokens, not separate conditions. The `NoMenusOpen` token is only `!AnyMenuOpen`; the `NoMenusOpen` context preset also includes `!Typing`.

The window forms accept the case-insensitive aliases `Inventory`, `Crafting`, `Map`, or an exact, case-sensitive concrete native window type name, such as `UIMainMenuWindow`. Names are validated at registration. Inventory covers the character window's Main page and multi-inventory windows; Map covers its Map page and the standalone map window. Crafting covers the native craft and craft-selection window types. Active means the top native window, with the matching page for character tabs. `!WindowActive:Inventory` means inventory is not active; inventory may still be open underneath another window. It does not mean inventory is closed. `!AnyMenuOpen` means no native windows are open, which is stricter than closing one named window.

Unknown or unavailable game state fails both positive and negative checks. For example, `!Typing` does not treat an uninitialized UI as safely not typing. Invalid tokens, opposing tokens and incompatible active-window declarations are rejected at registration. A custom IMGUI panel is not automatically a native menu; use `Enabled` for mod-specific state.

To share a binding between inventory and normal play, give the inventory action `new[] { "GameLoaded", "WindowActive:Inventory", "!Typing" }` and the gameplay action `new[] { "GameLoaded", "!AnyMenuOpen", "!Typing" }`. Their native-window rules prove they cannot run together, so the manager omits a conflict warning. Include any additional loading, pause or control restrictions your action needs: declarations replace the context preset.

`AllowWindow` exempts permitted windows when checking `AnyMenuOpen`/`!AnyMenuOpen`. The `GameplayOnly` preset also permits an allowed window's UI-only pause and control lock; explicit `!Paused` and `ControlsEnabled` tokens remain literal. Use the preset when you want that toggle-window behavior.

The manager uses the declarations when comparing bindings, rather than whether an action happens to be blocked while Controls is open. Opposing rules and known window relationships can prove exclusivity. Imported bindings without declarations, custom predicates and unproven overlaps retain a possible-conflict warning. `AllowWindow` exceptions are considered conservatively. An `Enabled` predicate can further restrict a declared action; it cannot undo a proven contradiction between declarations.

The Conditions column is read-only. It shows compact tags; hover or focus a row to inspect the full declaration and custom description. Imported bindings show **Not exposed**; native game controls show **Game**. Direct actions expose immutable `HotkeyAction.Conditions` with `Tags`, `Description`, a copied `Tokens` array, `HasCustom` and `IsUnrestricted` for inspection.

Direct integrations can query `GameplayContexts.IsAllowed(ConditionSet.Parse(tokens))`, `GameplayContexts.IsWindowOpen("Inventory")`, or `GameplayContexts.IsWindowActive("Inventory")` on Unity's main thread. These report game state only. `GameplayContexts.InCombat` and `GameplayContexts.DialogueOrCutscene` return `bool?`; `null` means unavailable, so do not interpret it as an opposite condition.

Built-in game state is captured once per manager update for all actions. Blocking cancels an armed release and requires neutral input before that device can rearm, so closing a menu does not transfer the held chord to a newly allowed action.

## Debugging blocked actions

Set `[Debug] Enabled = true` in `BepInEx/config/local.gk2.hotkeymanager.cfg` to log why a complete API chord is blocked. Diagnostics report new attempts or a changed reason while held; they do not print on every blocked frame. `HotkeyAction.BlockedReason` and optional `OptionalHotkeyAction.BlockedReason` expose the latest reason only in debug mode and return an empty string normally. The manager displays no normal invalid-press overlay.

## Direct integration

Reference the manager DLL and use a required dependency on 1.3.0 when using declarative conditions. The options overload supports the same registration features:

```csharp
HotkeyAction toggle = Hotkeys.Register(this, "toggle-lamp", "Toggle lamp",
    new HotkeyOptions {
        DefaultKeyboard = new KeyboardShortcut(KeyCode.J, KeyCode.LeftControl),
        DefaultController = "LB+Y",
        Callback = ToggleLamp,
        Context = ActionContext.GameplayOnly,
        Description = "Turn the player's lamp on or off."
    });
```

`HotkeyOptions` also supports `Trigger`, `Conditions`, `CustomConditionDescription`, `Enabled`, `AllowWindow`, shared config entries and `FallbackBindings`. The original 1.1 overload below remains binary compatible and retains its original `Anywhere` menu behavior. Choose the options overload to use built-in contexts or declarations.

Reference `GK2.HotkeyManager.dll`, BepInEx and Unity's normal assemblies when building your plugin. Ship your own DLL; install Hotkey Manager as a dependency. Calls use Unity's main thread. Register in `Awake`, retain the handle, and dispose in `OnDestroy`.

```csharp
using BepInEx;
using BepInEx.Configuration;
using GK2.HotkeyManager;
using UnityEngine;

[BepInPlugin("example.gk2.panel", "Example Panel", "1.0.0")]
[BepInDependency("local.gk2.hotkeymanager", "1.1.0")]
public sealed class ExamplePlugin : BaseUnityPlugin
{
    HotkeyAction toggle;
    bool panelOpen;

    void Awake()
    {
        toggle = Hotkeys.Register(
            owner: this,
            actionId: "toggle-panel",
            label: "Toggle example panel",
            defaultKeyboard: new KeyboardShortcut(KeyCode.J, KeyCode.LeftControl),
            defaultController: "RT+R3",
            callback: TogglePanel,
            trigger: TriggerMode.Release,
            description: "Show or hide the example panel.");
    }

    void TogglePanel()
    {
        panelOpen = !panelOpen;
        Logger.LogInfo("Example panel open: " + panelOpen);
        // Update your panel here.
    }

    void OnDestroy() => toggle?.Dispose();
}
```

Registering one action creates a keyboard row and a controller row in the appropriate native Controls lists. The name, description, action ID and callback method are used for tooltips. Conditions apply to both devices and appear in the read-only Conditions column. Values live in **your plugin's** config, under `Hotkeys.<actionId>` with `Keyboard`, `Controller` and `Trigger` entries unless you supply shared entries. The manager excludes claimed entries from legacy auto-import, avoiding duplicates.

Use stable IDs containing letters, digits, `.`, `-` or `_`. Duplicate IDs for the same plugin throw. Labels must be readable and nonempty. Keyboard defaults are `KeyboardShortcut` values; `KeyboardShortcut.Empty` disables that input. Controller defaults use `None` or names joined by `+`: **A B X Y LB RB LT RT L3 R3 Back Start Up Down Left Right**. Equivalent names such as `RightTrigger` and `RightStick` normalize to RT and R3. Duplicate physical buttons and reserved OS keyboard defaults are rejected.

`Press` fires once when all members become held. `Release` arms only after the complete chord was held, then fires once when **every member is released**. Partial release does not fire; partial chords cannot arm. Additional held controls do not disqualify an API chord. Shorter and longer registered chords can overlap, so choose nonoverlapping bindings or confirm the panel's warning deliberately.

The manager evaluates API actions before ordinary plugin Update methods. Callbacks run on Unity's main thread; exceptions are logged without stopping other actions. Actions are inactive when their owner is disabled, Controls is open, capture/input suppression is active, or the application loses focus. The original overload accepts `enabled: () => YourGameplayContextAllowsHotkeys()` for custom conditions. Becoming inactive cancels pending activation and waits for neutral input.

For a polling integration, leave `callback: null` and inspect `toggle.WasTriggered` in `Update`; it is true during the triggering frame. `toggle.IsHeld` reports whether either complete chord is currently held, independently of the activation mode. Do not both register a callback and separately invoke the action from `WasTriggered` unless you intend two calls. The public `Keyboard`, `Controller` and `Trigger` entries remain available for inspection. Config changes reset pending input.

Controller input reads the first connected XInput controller. Windows and the Windows game running through Proton are the current targets. A controller chord uses physical buttons rather than game action aliases; Steam Input rear buttons are available only through the ordinary inputs they emit.

The blacklist is exposed as `ReservedKeys.Validate(IEnumerable<KeyCode>)`, which throws `ArgumentException` with an explanation for a reserved shortcut. User-facing editor validation runs before changing config. API runtime ignores forbidden keyboard values introduced by hand-editing config, while leaving an allowed controller binding available.
