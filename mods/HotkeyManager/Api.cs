using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using BepInEx;
using BepInEx.Configuration;
using UnityEngine;
using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("Tests")]
[assembly: InternalsVisibleTo("GK2.HotkeyManager.Smoke")]

namespace GK2.HotkeyManager
{
    public enum TriggerMode { Press, Release }

    public enum ActionContext { GameplayOnly, NoMenusOpen, Anywhere }

    public sealed class HotkeyOptions
    {
        public KeyboardShortcut DefaultKeyboard = KeyboardShortcut.Empty;
        public string DefaultController = "None";
        public Action Callback;
        public TriggerMode Trigger = TriggerMode.Release;
        public string Description = "";
        public ActionContext Context = ActionContext.GameplayOnly;
        // Null uses Context. A non-null array replaces it with an AND of these rules.
        public string[] Conditions;
        public string CustomConditionDescription = "";
        public Func<bool> Enabled;
        // Receives each open native window, including windows underneath the top one.
        public Func<object, bool> AllowWindow;
        public ConfigEntry<KeyboardShortcut> KeyboardConfig;
        public ConfigEntry<string> ControllerConfig;
        public ConfigEntryBase[] FallbackBindings = new ConfigEntryBase[0];
    }

    /// <summary>Register on Unity's main thread, normally in your plugin's Awake. Keep and dispose the returned handle.</summary>
    public static class Hotkeys
    {
        internal static readonly List<HotkeyAction> Actions = new List<HotkeyAction>();
        internal static HashSet<ConfigEntryBase> OwnedEntries => new HashSet<ConfigEntryBase>(Actions
            .Where(a => a.Managed).SelectMany(a => a.ClaimedEntries));

        public static HotkeyAction Register(BaseUnityPlugin owner, string actionId, string label,
            KeyboardShortcut defaultKeyboard, string defaultController = "None", Action callback = null,
            TriggerMode trigger = TriggerMode.Release, string description = "", Func<bool> enabled = null)
            // Preserve the 1.1 binary signature and its unrestricted-menu behavior.
            => Register(owner, actionId, label, new HotkeyOptions { DefaultKeyboard = defaultKeyboard,
                DefaultController = defaultController, Callback = callback, Trigger = trigger,
                Description = description, Enabled = enabled, Context = ActionContext.Anywhere });

        public static HotkeyAction Register(BaseUnityPlugin owner, string actionId, string label, HotkeyOptions options)
        {
            if (owner == null) throw new ArgumentNullException(nameof(owner));
            if (options == null) throw new ArgumentNullException(nameof(options));
            if (Plugin.Instance == null || !Plugin.Instance.isActiveAndEnabled)
                throw new InvalidOperationException("Hotkey Manager is not active.");
            if (string.IsNullOrWhiteSpace(label)) throw new ArgumentException("Supply a readable action label.", nameof(label));
            if (string.IsNullOrWhiteSpace(actionId) || !Regex.IsMatch(actionId, @"^[a-zA-Z0-9_.-]+$"))
                throw new ArgumentException("Use a stable action ID containing letters, digits, dot, dash or underscore.", nameof(actionId));
            if (Actions.Any(a => a.Owner == owner && a.Id == actionId)) throw new ArgumentException("Action ID is already registered.");
            if (!Enum.IsDefined(typeof(ActionContext), options.Context) || !Enum.IsDefined(typeof(TriggerMode), options.Trigger))
                throw new ArgumentException("Choose a supported context and trigger.");
            ReservedKeys.Validate(Chords.Keys(options.DefaultKeyboard));
            Chords.NormalizeController(options.DefaultController);
            var claims = new[] { options.KeyboardConfig, (ConfigEntryBase)options.ControllerConfig }
                .Concat(options.FallbackBindings ?? new ConfigEntryBase[0]).Where(e => e != null).Distinct().ToArray();
            ValidateClaims(owner.Config, claims, OwnedEntries);
            var conditions = (options.Conditions == null ? ConditionSet.ForContext(options.Context, options.AllowWindow != null) :
                ConditionSet.Parse(options.Conditions).WithWindowExceptions(options.AllowWindow != null)).WithCustom(options.Enabled != null);
            var result = new HotkeyAction(owner, actionId, label, options, claims, conditions);
            Actions.Add(result);
            return result;
        }

        internal static void ValidateClaims(ConfigFile file, IEnumerable<ConfigEntryBase> entries, ISet<ConfigEntryBase> owned)
        {
            foreach (var entry in entries)
            {
                if (entry.ConfigFile != file) throw new ArgumentException("Fallback and shared bindings must belong to the registering plugin's config.");
                if (owned.Contains(entry)) throw new ArgumentException("A binding is already claimed by another API action.");
            }
        }

        // Versioned optional bridge: only BepInEx, Unity and system types cross the boundary.
        public static IDisposable RegisterOptionalV1(BaseUnityPlugin owner, string actionId, string label,
            KeyboardShortcut keyboard, string controller, Action callback, string trigger, string description,
            Func<bool> enabled, string context, Func<object, bool> allowWindow,
            ConfigEntry<KeyboardShortcut> keyboardConfig, ConfigEntry<string> controllerConfig, ConfigEntryBase[] fallbackBindings)
            => Register(owner, actionId, label, new HotkeyOptions { DefaultKeyboard = keyboard,
                DefaultController = controller, Callback = callback, Trigger = (TriggerMode)Enum.Parse(typeof(TriggerMode), trigger),
                Description = description, Enabled = enabled, Context = (ActionContext)Enum.Parse(typeof(ActionContext), context),
                AllowWindow = allowWindow, KeyboardConfig = keyboardConfig, ControllerConfig = controllerConfig,
                FallbackBindings = fallbackBindings });

        public static IDisposable RegisterOptionalV2(BaseUnityPlugin owner, string actionId, string label,
            KeyboardShortcut keyboard, string controller, Action callback, string trigger, string description,
            Func<bool> enabled, string context, Func<object, bool> allowWindow,
            ConfigEntry<KeyboardShortcut> keyboardConfig, ConfigEntry<string> controllerConfig, ConfigEntryBase[] fallbackBindings,
            string[] conditions, string customConditionDescription)
            => Register(owner, actionId, label, new HotkeyOptions { DefaultKeyboard = keyboard,
                DefaultController = controller, Callback = callback, Trigger = (TriggerMode)Enum.Parse(typeof(TriggerMode), trigger),
                Description = description, Enabled = enabled, Context = (ActionContext)Enum.Parse(typeof(ActionContext), context),
                AllowWindow = allowWindow, KeyboardConfig = keyboardConfig, ControllerConfig = controllerConfig,
                FallbackBindings = fallbackBindings, Conditions = conditions, CustomConditionDescription = customConditionDescription });
    }

    public sealed class HotkeyAction : IDisposable
    {
        public ConfigEntry<KeyboardShortcut> Keyboard { get; }
        public ConfigEntry<string> Controller { get; }
        public ConfigEntry<TriggerMode> Trigger { get; }
        /// <summary>Ownership, independent of whether a context currently allows activation.</summary>
        public bool Managed => !disposed && Owner != null && Plugin.Instance != null && Plugin.Instance.isActiveAndEnabled;
        public ActionContext Context { get; }
        public ConditionSet Conditions { get; }
        /// <summary>Most recent block reason, available only while the manager's debug mode is enabled.</summary>
        public string BlockedReason => Plugin.Instance != null && Plugin.Instance.DebugMode ? blockedReason : "";
        public bool IsHeld { get; private set; }
        /// <summary>True for the frame the action fires. The manager evaluates before ordinary plugin Update methods.</summary>
        public bool WasTriggered => Managed && FrameTriggered == Time.frameCount;
        internal readonly BaseUnityPlugin Owner;
        internal readonly string Id, Label, Description, CustomConditionDescription;
        internal readonly Action Callback;
        internal readonly Func<bool> Enabled;
        internal readonly Func<object, bool> AllowWindow;
        internal readonly HashSet<ConfigEntryBase> ClaimedEntries;
        internal readonly ChordState KeyboardState = new ChordState(), ControllerState = new ChordState();
        internal int FrameTriggered = -1;
        bool disposed;
        string blockedReason = "", reportedReason = "";
        bool blockedInputBefore;
        internal HotkeyAction(BaseUnityPlugin owner, string id, string label, HotkeyOptions options, ConfigEntryBase[] claims, ConditionSet conditions)
        {
            Owner = owner; Id = id; Label = label; Description = options.Description ?? ""; Callback = options.Callback;
            Enabled = options.Enabled; Context = options.Context; AllowWindow = options.AllowWindow;
            Conditions = conditions; CustomConditionDescription = options.CustomConditionDescription ?? "";
            string section = "Hotkeys." + id;
            Keyboard = options.KeyboardConfig ?? owner.Config.Bind(section, "Keyboard", options.DefaultKeyboard, Description);
            Controller = options.ControllerConfig ?? owner.Config.Bind(section, "Controller", Chords.NormalizeController(options.DefaultController), "Controller chord. " + Description);
            Trigger = owner.Config.Bind(section, "Trigger", options.Trigger, "Press: fire when the whole chord is held. Release: fire after a matched chord is fully released.");
            ClaimedEntries = new HashSet<ConfigEntryBase>(claims) { Keyboard, Controller, Trigger };
            CancelPending();
            owner.Config.SettingChanged += OnChanged;
        }
        void OnChanged(object sender, SettingChangedEventArgs args)
        {
            if (args.ChangedSetting == Keyboard || args.ChangedSetting == Controller || args.ChangedSetting == Trigger) CancelPending();
        }
        internal void Reset() { KeyboardState.Reset(); ControllerState.Reset(); IsHeld = false; FrameTriggered = -1; }
        internal void CancelPending() { Reset(); KeyboardState.Suppress(); ControllerState.Suppress(); }
        internal void Poll(uint buttons, bool blocked)
            => Poll(buttons, blocked, GameplayContexts.Capture(), "Controls are being edited or input is suppressed.");
        internal void Poll(uint buttons, bool blocked, ContextState context, string globalReason)
        {
            string reason = "";
            if (disposed || !Owner || !Owner.enabled) reason = "The action's plugin is inactive.";
            else if (blocked) reason = globalReason;
            else if (!Conditions.Evaluate(context, AllowWindow, out reason)) { }
            else if (Enabled != null && !Enabled()) reason = string.IsNullOrWhiteSpace(CustomConditionDescription) ?
                "The mod's custom condition is false." : "Custom condition: " + CustomConditionDescription;
            bool debug = Plugin.Instance != null && Plugin.Instance.DebugMode;
            blockedReason = debug ? reason : "";
            if (reason.Length != 0 && !debug) { CancelPending(); return; }
            var keyboard = Chords.Keys(Keyboard.Value);
            try { ReservedKeys.Validate(keyboard); }
            catch (ArgumentException) { keyboard = new KeyCode[0]; }
            bool keyboardFull = keyboard.Length != 0 && keyboard.All(Input.GetKey);
            bool keyboardAny = keyboard.Any(Input.GetKey);
            uint mask = Chords.ControllerMask(Controller.Value);
            bool controllerFull = mask != 0 && (buttons & mask) == mask;
            bool controllerAny = (buttons & mask) != 0;
            if (reason.Length != 0)
            {
                bool pressed = keyboardFull || controllerFull;
                if (pressed && (!blockedInputBefore || reason != reportedReason))
                {
                    Plugin.Instance.TraceBlocked(this, reason); reportedReason = reason;
                }
                blockedInputBefore = pressed; CancelPending(); return;
            }
            blockedInputBefore = false; reportedReason = "";
            IsHeld = (keyboardFull && !KeyboardState.IsSuppressed) || (controllerFull && !ControllerState.IsSuppressed);
            bool fireKey = KeyboardState.Update(keyboardFull, keyboardAny, Trigger.Value);
            bool firePad = ControllerState.Update(controllerFull, controllerAny, Trigger.Value);
            if (!fireKey && !firePad) return;
            FrameTriggered = Time.frameCount;
            try { Callback?.Invoke(); } catch (Exception ex) { Plugin.Instance?.ReportError("Hotkey callback " + Label, ex); }
        }
        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            Owner.Config.SettingChanged -= OnChanged;
            Hotkeys.Actions.Remove(this); Reset();
        }
    }

    /// <summary>One press per full-chord rising edge, or one release after the entire matched chord becomes neutral.</summary>
    public sealed class ChordState
    {
        bool fullBefore, armed, cancelled;
        internal bool IsSuppressed => cancelled;
        public bool Update(bool full, bool any, TriggerMode mode)
        {
            bool press = full && !fullBefore;
            if (full) armed = true;
            bool release = armed && !any;
            bool result = !cancelled && (mode == TriggerMode.Press ? press : release);
            fullBefore = full;
            if (!any) { armed = false; cancelled = false; }
            return result;
        }
        public void Suppress() { cancelled = true; }
        public void Reset() { fullBefore = armed = cancelled = false; }
    }

    public static class Chords
    {
        public static KeyCode[] Keys(KeyboardShortcut shortcut) => shortcut.MainKey == KeyCode.None ? new KeyCode[0] :
            new[] { shortcut.MainKey }.Concat(shortcut.Modifiers).Distinct().ToArray();
        public static string NormalizeController(string text)
        {
            if (Rules.IsNone(text)) return "None";
            var parts = text.Split('+').Select(s => s.Trim()).ToArray();
            var names = new List<string>();
            foreach (string part in parts)
            {
                string canonical;
                if (!Rules.PadNames.TryGetValue(part, out canonical) || canonical.Length == 0)
                    throw new ArgumentException("Use controller button names joined with +, e.g. RT+R3.");
                names.Add(canonical);
            }
            if (names.Distinct().Count() != names.Count) throw new ArgumentException("A chord cannot repeat the same physical button.");
            return string.Join("+", names);
        }
        public static uint ControllerMask(string text)
        {
            if (Rules.IsNone(text)) return 0;
            try { return NormalizeController(text).Split('+').Aggregate(0u, (mask, name) => mask | InputController.Mask(name)); }
            catch (ArgumentException) { return 0; }
        }
    }
}
