// Development-only. Built WITHOUT a reference to Hotkey Manager or game assemblies.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using BepInEx;
using BepInEx.Bootstrap;
using BepInEx.Configuration;
using GK2.HotkeyManager.Integration;
using UnityEngine;

[BepInPlugin("local.gk2.hotkeymanager.optional-smoke", "Optional Hotkeys Smoke Check", "1.2.0")]
[BepInDependency(OptionalHotkeys.PluginGuid, BepInDependency.DependencyFlags.SoftDependency)]
public sealed class OptionalSmokePlugin : BaseUnityPlugin
{
    OptionalHotkeyAction action, restricted;
    ConfigEntry<KeyboardShortcut> keyboard;
    ConfigEntry<string> controller;
    ConfigEntry<KeyCode> fallback, other, undeclared;
    bool allowed = true, expected;
    int calls, manualCalls;
    string output;
    readonly List<string> report = new List<string>();
    void Awake()
    {
        var args = Environment.GetCommandLineArgs();
        int flag = Array.IndexOf(args, "-gk2-optional-smoke");
        if (flag < 0 || flag + 1 >= args.Length) return;
        output = args[flag + 1]; expected = args.Contains("-gk2-manager-expected");
        keyboard = Config.Bind("Legacy", "Keyboard", new KeyboardShortcut(KeyCode.F11, KeyCode.LeftControl));
        controller = Config.Bind("Legacy", "Controller", "LB+Y", "Controller physical button chord.");
        fallback = Config.Bind("Legacy", "FallbackKey", KeyCode.F7);
        other = Config.Bind("Legacy", "OtherActionKey", KeyCode.F8);
        undeclared = Config.Bind("Legacy", "UndeclaredFallbackKey", KeyCode.F11);
        action = OptionalHotkeys.Register(this, "optional", "Optional lamp", new OptionalHotkeyOptions {
            KeyboardConfig = keyboard, ControllerConfig = controller, FallbackBindings = new ConfigEntryBase[] { fallback },
            Callback = () => calls++, Enabled = () => allowed, Context = HotkeyContext.NoMenusOpen,
            AllowWindow = w => w.GetType().Name == "UIMainMenuWindow", Description = "Optional ownership and shared binding check." });
    }
    void Start() { if (output != null) StartCoroutine(Run()); }
    void ManualStep(bool pressed) { if (!action.Managed && pressed) manualCalls++; }
    void Check(bool ok, string label) { report.Add((ok ? "PASS " : "FAIL ") + label); Logger.LogInfo(report.Last()); }
    static object Member(object obj, string name) => obj.GetType().GetProperty(name, BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Instance)?.GetValue(obj) ?? obj.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Instance)?.GetValue(obj);
    static void Invoke(object obj, string name, params object[] args) => obj.GetType().GetMethods(BindingFlags.NonPublic | BindingFlags.Instance).Single(m => m.Name == name && m.GetParameters().Length == args.Length).Invoke(obj, args);
    static object Handle(OptionalHotkeyAction wrapper) => typeof(OptionalHotkeyAction).GetField("handle", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(wrapper);
    static void Chord(object handle) { Invoke(handle, "Poll", 33024u, false); Invoke(handle, "Poll", 0u, false); } // LB + Y
    IEnumerator Run()
    {
        Directory.CreateDirectory(output);
        yield return new WaitForSecondsRealtime(expected && Environment.GetCommandLineArgs().Contains("-gk2-hotkey-smoke") ? 55 : 20);
        try
        {
            Check(!GetType().Assembly.GetReferencedAssemblies().Any(a => a.Name == "GK2.HotkeyManager"), "Optional plugin has no manager assembly reference");
            Check(action.Managed == expected, "Registration ownership matches installation");
            if (!expected)
            {
                Check(!action.WasTriggered && !action.IsHeld && action.UnavailableReason != null, "Absent manager returns usable fallback handle");
                ManualStep(true); Check(manualCalls == 1 && calls == 0, "Manual binding runs without manager");
            }
            else
            {
                var handle = Handle(action);
                Check(ReferenceEquals(Member(handle, "Keyboard"), keyboard) && ReferenceEquals(Member(handle, "Controller"), controller), "Both device bindings reuse existing config entries");
                Check(keyboard.Value.MainKey == KeyCode.F11 && controller.Value == "LB+Y" && fallback.Value == KeyCode.F7, "Registration preserves fallback config values");
                var manager = Chainloader.PluginInfos[OptionalHotkeys.PluginGuid].Instance;
                Invoke(manager, "Discover");
                var rows = ((IEnumerable)Member(manager, "Rows")).Cast<object>().Where(r => (string)Member(r, "Id") == Info.Metadata.GUID).ToArray();
                Check(rows.Length == 4, "Two API rows plus unconverted actions; declared fallback hidden");
                Check(rows.All(r => !ReferenceEquals(Member(r, "Entry"), fallback)), "Declared manual fallback excluded from import");
                Check(rows.Any(r => ReferenceEquals(Member(r, "Entry"), other)), "Partial API adoption keeps other legacy controls");
                Check(rows.Any(r => ReferenceEquals(Member(r, "Entry"), undeclared) && ((string)Member(r, "Details")).Contains("Possible undeclared fallback")), "Undeclared overlapping fallback diagnosed");
                ManualStep(true); Check(manualCalls == 0, "Managed action skips manual polling");
                Invoke(handle, "Poll", 0u, false); Chord(handle);
                Check(calls == 1 && action.WasTriggered, "Optional controller chord invokes registered action once");
                allowed = false; Invoke(handle, "Poll", 33024u, false); ManualStep(true);
                Check(action.Managed && calls == 1 && manualCalls == 0 && !action.IsHeld, "Disabled condition preserves ownership and blocks both paths");
                allowed = true; Chord(handle);
                Check(calls == 1, "Held buttons after context change require neutral input");
                Chord(handle); Check(calls == 2, "Fresh chord works after neutral input");
                restricted = OptionalHotkeys.Register(this, "gameplay", "Gameplay-only probe", new OptionalHotkeyOptions {
                    DefaultController = "LB+Y", Callback = () => calls++, Context = HotkeyContext.GameplayOnly });
                var restrictedHandle = Handle(restricted);
                Invoke(restrictedHandle, "Poll", 0u, false); Chord(restrictedHandle);
                Check(restricted.Managed && !restricted.IsHeld && calls == 2, "Gameplay context blocks main-menu input without losing ownership");
                var rejected = OptionalHotkeys.Register(this, "double-claim", "Double claim", new OptionalHotkeyOptions { FallbackBindings = new ConfigEntryBase[] { fallback } });
                Check(!rejected.Managed && rejected.UnavailableReason.Contains("already claimed") && action.Managed, "Failed registration falls back without stealing existing action"); rejected.Dispose();
                if (argsSupportsDeclarations()) manager.GetType().GetField("WaitForNeutral", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(manager, true);
                manager.enabled = false; ManualStep(true);
                Check(!action.Managed && manualCalls == 1, "Inactive manager releases ownership to fallback");
                if (argsSupportsDeclarations()) Check(!(bool)Member(manager, "BlockInput"), "Inactive manager releases capture suppression to fallback input");
                manager.enabled = true; Chord(handle);
                Check(action.Managed && calls == 2, "Reenabled manager waits for neutral buttons");
                Chord(handle); Check(calls == 3, "Reenabled manager resumes fresh activations");
                action.Dispose(); action.Dispose(); Invoke(manager, "Discover");
                var entries = ((IEnumerable)Member(manager, "Rows")).Cast<object>().Select(r => Member(r, "Entry")).ToArray();
                Check(!action.Managed && entries.Contains(fallback) && entries.Contains(keyboard), "Disposal restores declared fallback imports");
                ManualStep(true); Check(manualCalls == 2, "Disposed action returns to manual fallback");
            }
            var declarations = OptionalHotkeys.Register(this, "declarations", "Declared combat probe", new OptionalHotkeyOptions {
                DefaultController = "LB+Y", Conditions = new[] { "GameLoaded", "!Loading", "!Typing", "!InCombat" },
                CustomConditionDescription = "Test custom rule", Enabled = () => true });
            bool supportsDeclarations = expected && argsSupportsDeclarations();
            Check(declarations.Managed == supportsDeclarations, "V2 declaration ownership matches supported manager version");
            if (supportsDeclarations)
            {
                Invoke(Handle(declarations), "Poll", 0u, false); Chord(Handle(declarations));
                Check(!declarations.IsHeld && !declarations.WasTriggered && declarations.BlockedReason == "", "Declared gameplay conditions block unloaded save quietly");
            }
            else Check(declarations.UnavailableReason != null, "Unsupported declarations return a whole-action fallback");
            declarations.Dispose();
        }
        catch (Exception ex) { report.Add("FAIL " + ex); Logger.LogError(ex); }
        finally { File.WriteAllLines(Path.Combine(output, "optional-report.txt"), report); Logger.LogInfo("OPTIONAL HOTKEY SMOKE COMPLETE"); }
    }
    static bool argsSupportsDeclarations() => Environment.GetCommandLineArgs().Contains("-gk2-declarations-expected");
    void OnDestroy() { action?.Dispose(); restricted?.Dispose(); }
}
