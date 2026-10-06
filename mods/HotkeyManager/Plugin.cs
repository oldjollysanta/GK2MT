using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using BepInEx;
using BepInEx.Bootstrap;
using BepInEx.Configuration;
using HarmonyLib;
using LazyBearTechnology;
using UnityEngine;

namespace GK2.HotkeyManager
{
    [DefaultExecutionOrder(-9000)]
    [BepInPlugin("local.gk2.hotkeymanager", "GK2 Hotkey Manager", "1.3.0")]
    public sealed class Plugin : BaseUnityPlugin
    {
        internal static Plugin Instance;
        internal readonly List<Binding> Rows = new List<Binding>();
        internal readonly Dictionary<int, NativeKey> NativeKeys = new Dictionary<int, NativeKey>();
        internal NativeControls Controls;
        readonly Harmony harmony = new Harmony("local.gk2.hotkeymanager");
        readonly HashSet<MethodBase> guarded = new HashSet<MethodBase>();
        readonly HashSet<string> warnedFallbacks = new HashSet<string>();
        ConfigEntry<KeyboardShortcut> menuKey;
        ConfigEntry<string> menuPad;
        ConfigEntry<bool> debugMode;
        internal bool DebugMode => debugMode != null && debugMode.Value;
        internal uint Buttons, PreviousButtons;
        internal float SuppressUntil;
        internal bool WaitForNeutral;
        internal bool Capturing => Controls != null && Controls.IsCapturing;
        internal bool BlockInput => isActiveAndEnabled && (Capturing || WaitForNeutral || Time.unscaledTime < SuppressUntil);

        void Awake()
        {
            Instance = this;
            bool migrate = File.Exists(Config.ConfigFilePath) && !File.ReadAllText(Config.ConfigFilePath).Contains("OpenController =");
            menuKey = Config.Bind("Controls", "OpenKeyboard", new KeyboardShortcut(KeyCode.F10, KeyCode.LeftControl), "Open the game's Controls panel.");
            menuPad = Config.Bind("Controls", "OpenController", "Back+Start", "Controller chord to open the game's Controls panel.");
            debugMode = Config.Bind("Debug", "Enabled", false, "Expose blocked-action reasons and log blocked chord attempts. No press-time messages appear in normal mode.");
            // Bind legacy entries to read and preserve customized 1.0 controller controls.
            var oldMain = Config.Bind("Controls", "OpenGamepad", PadButton.Back, "Legacy 1.0 controller button.");
            var oldSecond = Config.Bind("Controls", "OpenGamepad2", PadButton.Start, "Legacy 1.0 second controller button.");
            if (migrate) menuPad.Value = oldMain.Value == PadButton.None ? "None" : Chords.NormalizeController(oldMain.Value + (oldSecond.Value == PadButton.None ? "" : "+" + oldSecond.Value));
            harmony.PatchAll(typeof(Plugin).Assembly);
            Logger.LogInfo("Native Controls integration ready. Ctrl+F10 or View+Menu opens Settings > Controls.");
        }
        void Update()
        {
            PreviousButtons = Buttons; Buttons = InputController.Read();
            if (WaitForNeutral && Buttons == 0 && !Input.anyKey) WaitForNeutral = false;
            if (!LazyInput.IsInitialized) return;
            EnsureNativeKeys();
            Controls?.Tick();
            bool blocked = BlockInput || (Controls != null && Controls.Window.IsShown) || !Application.isFocused;
            var context = GameplayContexts.Capture();
            string reason = !Application.isFocused ? "The game is not focused." : Capturing ? "A binding is being captured." :
                Controls != null && Controls.Window.IsShown ? "The Controls panel is open." : "Waiting for input to be released after editing.";
            foreach (var action in Hotkeys.Actions.ToArray())
            {
                try { action.Poll(Buttons, blocked, context, reason); }
                catch (Exception ex) { action.CancelPending(); ReportError("Hotkey " + action.Label, ex); }
            }
            if (Capturing || !Application.isFocused) return;
            uint mask = Chords.ControllerMask(menuPad.Value);
            bool padToggle = mask != 0 && (Buttons & mask) == mask && (PreviousButtons & mask) != mask;
            if (menuKey.Value.IsDown() || padToggle) LazyUI.GetWindow<UIGameBindingSettingsWindow>().Open(null);
        }
        internal void EnsureNativeKeys()
        {
            // GameSettings applies saved single keys first; chord overrides run afterward.
            if (!(bool)AccessTools.Field(typeof(GameSettings), "savedGameBindingsApplied").GetValue(GameSettings.Instance)) return;
            foreach (var key in LazyInput.GameBindings.keyBindings)
            {
                if (NativeKeys.ContainsKey(key.gameKey.value)) continue;
                string section = "NativeKeyboard." + key.gameKey.value;
                var managed = new NativeKey
                {
                    Source = key,
                    Shortcut = Config.Bind(section, "Shortcut", new KeyboardShortcut(key.keyCode, key.additionalKeyCodes ?? new KeyCode[0]), "Saved keyboard chord, used only when Enabled is true."),
                    Enabled = Config.Bind(section, "Enabled", false, "Enable this native control's saved chord/trigger override."),
                    Trigger = Config.Bind(section, "Trigger", TriggerMode.Press, "Press or release activation for this game control.")
                };
                NativeKeys.Add(key.gameKey.value, managed); managed.Apply();
            }
        }
        internal void Discover()
        {
            Rows.Clear();
            Rules.ActionNames = typeof(GameKey).GetFields(BindingFlags.Public | BindingFlags.Static)
                .Where(f => f.FieldType == typeof(GameKey)).Select(f => f.Name).OrderBy(n => n).ToArray();
            Rules.ActionButtons.Clear();
            var names = typeof(GameKey).GetFields(BindingFlags.Public | BindingFlags.Static)
                .Where(f => f.FieldType == typeof(GameKey)).ToDictionary(f => f.Name, f => (GameKey)f.GetValue(null));
            foreach (var binding in LazyInput.GameBindings.gamepadBindings)
            {
                var field = typeof(GamepadButton).GetFields(BindingFlags.Public | BindingFlags.Static)
                    .FirstOrDefault(f => f.FieldType == typeof(GamepadButton) && (GamepadButton)f.GetValue(null) == binding.gamepadButton);
                string physical;
                if (field == null || !Rules.PadNames.TryGetValue(field.Name, out physical) || physical.Length == 0) continue;
                foreach (var name in names.Where(p => p.Value == binding.gameKey).Select(p => p.Key)) Rules.ActionButtons[name] = physical;
            }
            foreach (var alias in LazyInput.GameBindings.bindingAliases)
            {
                var source = names.Where(p => p.Value == alias.gameKey1).Select(p => p.Key).FirstOrDefault();
                string physical;
                if (source != null && Rules.ActionButtons.TryGetValue(source, out physical))
                    foreach (var name in names.Where(p => p.Value == alias.gameKey2).Select(p => p.Key)) Rules.ActionButtons[name] = physical;
            }
            foreach (var action in Hotkeys.Actions.Where(a => a.Managed))
            {
                string function = action.Callback == null ? "Polling handle: HotkeyAction.WasTriggered" : action.Callback.Method.DeclaringType?.FullName + "." + action.Callback.Method.Name;
                foreach (var row in new[] { new Binding { Entry = action.Keyboard, Kind = BindingKind.Keyboard }, new Binding { Entry = action.Controller, Kind = BindingKind.PadChord } })
                {
                    row.Mod = action.Owner.Info.Metadata.Name; row.Id = action.Owner.Info.Metadata.GUID;
                    row.Title = action.Label; row.TriggerEntry = action.Trigger;
                    row.Conditions = action.Conditions; row.ApiActionId = action.Id;
                    row.ConditionDetails = action.Conditions.Description + (action.Enabled != null ? "\nCustom: " +
                        (string.IsNullOrWhiteSpace(action.CustomConditionDescription) ? "Mod supplied predicate; details not exposed." : action.CustomConditionDescription) : "");
                    row.Details = action.Description + "\nFunction: " + function + "\nAPI action: " + action.Id +
                        "\nConditions: " + row.ConditionDetails;
                    row.Revert(); Rows.Add(row);
                }
            }
            var apiRows = Rows.ToArray();
            var ownedEntries = Hotkeys.OwnedEntries;
            foreach (var plugin in Chainloader.PluginInfos.Values.OrderBy(p => p.Metadata.Name))
            {
                if (plugin.Instance == null) continue;
                var entries = plugin.Instance.Config.Select(p => p.Value).ToArray();
                var consumed = new HashSet<ConfigEntryBase>(ownedEntries);
                foreach (var entry in entries)
                {
                    if (consumed.Contains(entry) || (plugin.Instance == this && (entry.Definition.Section.StartsWith("NativeKeyboard.") || entry.Definition.Key == "OpenGamepad" || entry.Definition.Key == "OpenGamepad2"))) continue;
                    var kind = Binding.Detect(entry.SettingType, entry.Definition.Section, entry.Definition.Key, entry.Description.Description);
                    if (plugin.Instance == this && entry == menuPad) kind = BindingKind.PadChord;
                    if (!kind.HasValue) continue;
                    ConfigEntryBase second = null;
                    if (kind == BindingKind.PadEnum)
                    {
                        second = entries.FirstOrDefault(e => e.Definition.Section == entry.Definition.Section && e.Definition.Key == entry.Definition.Key + "2" && e.SettingType == entry.SettingType);
                        if (second != null) consumed.Add(second);
                    }
                    var row = new Binding { Mod = plugin.Metadata.Name, Id = plugin.Metadata.GUID, Entry = entry, Secondary = second, Kind = kind.Value, Details = BindingInfo.Describe(plugin.Instance, entry) };
                    row.Revert();
                    var overlap = apiRows.FirstOrDefault(a => a.Id == row.Id && Rules.Conflict(a, row) != "");
                    if (overlap != null)
                    {
                        string warning = "Possible undeclared fallback: this setting overlaps the same mod's API action '" + overlap.Label +
                            "'. Authors should declare FallbackBindings and guard manual polling with !action.Managed. This is a diagnostic, not proof of duplicate execution.";
                        row.Details += "\n" + warning;
                        if (warnedFallbacks.Add(row.Id + "/" + entry.Definition.Section + "/" + entry.Definition.Key)) Logger.LogWarning(warning + " Setting: " + entry.Definition);
                    }
                    Rows.Add(row);
                }
                if (plugin.Instance != this)
                    foreach (string methodName in new[] { "Update", "LateUpdate" })
                    {
                        var method = plugin.Instance.GetType().GetMethod(methodName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic, null, Type.EmptyTypes, null);
                        if (method == null || guarded.Contains(method)) continue;
                        try { harmony.Patch(method, prefix: new HarmonyMethod(typeof(CaptureGuard), nameof(CaptureGuard.PluginUpdate))); guarded.Add(method); }
                        catch (Exception ex) { ReportError("Capture guard " + plugin.Metadata.Name, ex); }
                    }
            }
            Logger.LogInfo("Imported " + Rows.Count + " mod bindings into native Controls.");
        }
        internal string Apply(Binding binding)
        {
            var edits = new List<Binding> { binding };
            NativeKey interaction, skip;
            if (NativeKeys.TryGetValue(GameKey.Interaction.value, out interaction) && binding.Entry == interaction.Shortcut && NativeKeys.TryGetValue(GameKey.SpeechSkip2.value, out skip))
            {
                var alias = new Binding { Entry = skip.Shortcut, Kind = BindingKind.Keyboard, TriggerEntry = skip.Trigger, ActivationEntry = skip.Enabled, Mod = "Game", Title = "Interaction / speech skip alias" };
                alias.Revert(); alias.Draft = binding.Draft; alias.TriggerDraft = binding.TriggerDraft; edits.Add(alias);
            }
            string backup = BindingStore.Apply(edits, Path.Combine(Paths.ConfigPath, "HotkeyManagerBackups"));
            foreach (var native in NativeKeys.Values) native.Apply();
            ControllerIconLibrary.UpdateStandaloneIcons();
            (AccessTools.Field(typeof(UIGameBindingSettingsWindow), "OnBtnUpdated").GetValue(null) as Action)?.Invoke();
            return backup;
        }
        internal void ReportError(string context, Exception ex) => Logger.LogError(context + ": " + ex);
        internal void TraceBlocked(HotkeyAction action, string reason)
        {
            if (DebugMode) Logger.LogInfo("[Debug] Blocked " + action.Owner.Info.Metadata.GUID + "/" + action.Id + ": " + reason);
        }
        void OnEnable() { foreach (var native in NativeKeys.Values) native.Apply(); }
        void OnDisable()
        {
            Controls?.Cancel();
            foreach (var action in Hotkeys.Actions.ToArray()) action.CancelPending();
            foreach (var native in NativeKeys.Values) native.Restore();
        }
        void OnDestroy()
        {
            Controls?.Dispose(); harmony.UnpatchSelf();
            foreach (var native in NativeKeys.Values) native.Restore();
            if (Instance == this) Instance = null;
        }
    }
    public enum PadButton { None, A, B, X, Y, LeftShoulder, RightShoulder, LeftTrigger, RightTrigger, LeftStick, RightStick, Back, Start, DPadUp, DPadDown, DPadLeft, DPadRight }
    internal sealed class NativeKey
    {
        internal KeyBinding Source;
        internal ConfigEntry<KeyboardShortcut> Shortcut;
        internal ConfigEntry<bool> Enabled;
        internal ConfigEntry<TriggerMode> Trigger;
        internal readonly ChordState State = new ChordState();
        KeyCode original;
        KeyCode[] originalAdditional;
        bool applied;
        internal void Apply()
        {
            if (!Enabled.Value) return;
            try { ReservedKeys.Validate(Chords.Keys(Shortcut.Value)); }
            catch (ArgumentException ex) { Restore(); Enabled.Value = false; Plugin.Instance?.ReportError("Native shortcut blocked", ex); return; }
            if (!applied) { original = Source.keyCode; originalAdditional = Source.additionalKeyCodes; applied = true; }
            Source.keyCode = Shortcut.Value.MainKey; Source.additionalKeyCodes = Shortcut.Value.Modifiers.ToArray(); State.Reset();
        }
        internal void Restore() { if (applied) { Source.keyCode = original; Source.additionalKeyCodes = originalAdditional; applied = false; State.Reset(); } }
        internal void ForgetOverride() { if (applied) Source.additionalKeyCodes = originalAdditional ?? new KeyCode[0]; applied = false; State.Reset(); }
    }
    [HarmonyPatch(typeof(KeyboardController), nameof(KeyboardController.Update))]
    internal static class NativeKeyboardRuntime
    {
        static void Postfix(KeyboardController __instance)
        {
            if (Plugin.Instance == null || !Plugin.Instance.isActiveAndEnabled) return;
            foreach (var native in Plugin.Instance.NativeKeys.Values.Where(n => n.Enabled.Value))
            {
                __instance.pressedKeys.Remove(native.Source.gameKey); __instance.holdedKeys.Remove(native.Source.gameKey);
                var keys = Chords.Keys(native.Shortcut.Value);
                bool full = keys.Length != 0 && keys.All(Input.GetKey), any = keys.Any(Input.GetKey);
                if (Plugin.Instance.BlockInput || !Application.isFocused) { native.State.Reset(); continue; }
                if (full) __instance.holdedKeys.Add(native.Source.gameKey);
                if (native.State.Update(full, any, native.Trigger.Value)) __instance.pressedKeys.Add(native.Source.gameKey);
            }
            if (Plugin.Instance.NativeKeys.Values.Any(n => n.Enabled.Value && (n.Source.gameKey == GameKey.Up || n.Source.gameKey == GameKey.Down || n.Source.gameKey == GameKey.Left || n.Source.gameKey == GameKey.Right)))
                AccessTools.Method(typeof(KeyboardController), "HandleDirection").Invoke(__instance, null);
        }
    }
    [HarmonyPatch]
    internal static class CaptureGuard
    {
        static IEnumerable<MethodBase> TargetMethods()
        {
            foreach (string name in new[] { "GetKey", "GetKeyDown" }) yield return AccessTools.Method(typeof(LazyInput), name);
            foreach (string name in new[] { "IsDown", "IsPressed", "IsUp" }) yield return AccessTools.Method(typeof(KeyboardShortcut), name);
        }
        static bool Prefix(ref bool __result)
        {
            if (Plugin.Instance == null || !Plugin.Instance.BlockInput) return true;
            __result = false; return false;
        }
        public static bool PluginUpdate() => Plugin.Instance == null || !Plugin.Instance.BlockInput;
    }
    [HarmonyPatch]
    internal static class CaptureDirectionGuard
    {
        static IEnumerable<MethodBase> TargetMethods()
        {
            yield return AccessTools.Method(typeof(LazyInput), "GetDirection");
            yield return AccessTools.Method(typeof(LazyInput), "GetDirection2");
        }
        static bool Prefix(ref Vector2 __result)
        {
            if (Plugin.Instance == null || !Plugin.Instance.BlockInput) return true;
            __result = Vector2.zero; return false;
        }
    }
}
