// Include this source in your mod. It has NO reference to GK2.HotkeyManager.dll.
using System;
using System.Reflection;
using BepInEx;
using BepInEx.Bootstrap;
using BepInEx.Configuration;

namespace GK2.HotkeyManager.Integration
{
    public enum HotkeyContext { GameplayOnly, NoMenusOpen, Anywhere }
    public enum HotkeyTrigger { Press, Release }

    public sealed class OptionalHotkeyOptions
    {
        public KeyboardShortcut DefaultKeyboard = KeyboardShortcut.Empty;
        public string DefaultController = "None";
        public Action Callback;
        public HotkeyTrigger Trigger = HotkeyTrigger.Release;
        public string Description = "";
        public HotkeyContext Context = HotkeyContext.GameplayOnly;
        public string[] Conditions;
        public string CustomConditionDescription = "";
        public Func<bool> Enabled;
        public Func<object, bool> AllowWindow;
        public ConfigEntry<KeyboardShortcut> KeyboardConfig;
        public ConfigEntry<string> ControllerConfig;
        public ConfigEntryBase[] FallbackBindings = new ConfigEntryBase[0];
    }

    public static class OptionalHotkeys
    {
        public const string PluginGuid = "local.gk2.hotkeymanager";
        static readonly BepInEx.Logging.ManualLogSource log = BepInEx.Logging.Logger.CreateLogSource("Optional Hotkeys");

        public static OptionalHotkeyAction Register(BaseUnityPlugin owner, string actionId, string label, OptionalHotkeyOptions options)
        {
            if (owner == null) throw new ArgumentNullException(nameof(owner));
            if (options == null) throw new ArgumentNullException(nameof(options));
            if (!Chainloader.PluginInfos.TryGetValue(PluginGuid, out var plugin) || plugin.Instance == null)
                return new OptionalHotkeyAction("Hotkey Manager is not installed.");
            var api = plugin.Instance.GetType().Assembly.GetType("GK2.HotkeyManager.Hotkeys");
            bool needsConditions = options.Conditions != null || !string.IsNullOrWhiteSpace(options.CustomConditionDescription);
            var method = api?.GetMethod(needsConditions ? "RegisterOptionalV2" : "RegisterOptionalV1", BindingFlags.Public | BindingFlags.Static);
            if (needsConditions && method == null)
                return new OptionalHotkeyAction("Hotkey Manager 1.3.0 or later is required for declarative conditions. Using the whole action's fallback.");
            if (method == null) return new OptionalHotkeyAction("Hotkey Manager 1.2.0 or later is required for optional integration.");
            IDisposable handle = null;
            try
            {
                var arguments = new object[] { owner, actionId, label,
                    options.DefaultKeyboard, options.DefaultController, options.Callback, options.Trigger.ToString(),
                    options.Description, options.Enabled, options.Context.ToString(), options.AllowWindow,
                    options.KeyboardConfig, options.ControllerConfig, options.FallbackBindings };
                if (needsConditions)
                {
                    var extended = new object[arguments.Length + 2];
                    Array.Copy(arguments, extended, arguments.Length);
                    extended[arguments.Length] = options.Conditions;
                    extended[arguments.Length + 1] = options.CustomConditionDescription;
                    arguments = extended;
                }
                handle = (IDisposable)method.Invoke(null, arguments);
                return new OptionalHotkeyAction(handle);
            }
            catch (Exception ex)
            {
                handle?.Dispose();
                string reason = (ex is TargetInvocationException invocation ? invocation.InnerException : ex)?.Message ?? ex.Message;
                log.LogWarning(owner.Info.Metadata.GUID + "/" + actionId + ": " + reason + ". Using this action's fallback.");
                return new OptionalHotkeyAction(reason);
            }
        }
    }

    public sealed class OptionalHotkeyAction : IDisposable
    {
        readonly IDisposable handle;
        readonly Func<bool> managed, triggered, held;
        readonly Func<string> blockedReason;
        bool disposed;
        public string UnavailableReason { get; }
        // Do not use WasTriggered/IsHeld/Enabled as a decision to run manual polling.
        public bool Managed => !disposed && (managed?.Invoke() ?? false);
        public bool WasTriggered => Managed && triggered();
        public bool IsHeld => Managed && held();
        public string BlockedReason => Managed ? blockedReason?.Invoke() ?? "" : "";
        internal OptionalHotkeyAction(string reason) { UnavailableReason = reason; }
        internal OptionalHotkeyAction(IDisposable handle)
        {
            this.handle = handle ?? throw new ArgumentNullException(nameof(handle));
            managed = Getter("Managed"); triggered = Getter("WasTriggered"); held = Getter("IsHeld");
            var reason = handle.GetType().GetProperty("BlockedReason");
            if (reason != null) blockedReason = (Func<string>)Delegate.CreateDelegate(typeof(Func<string>), handle, reason.GetGetMethod());
        }
        Func<bool> Getter(string name) => (Func<bool>)Delegate.CreateDelegate(typeof(Func<bool>), handle, handle.GetType().GetProperty(name).GetGetMethod());
        public void Dispose() { if (disposed) return; disposed = true; handle?.Dispose(); }
    }
}
