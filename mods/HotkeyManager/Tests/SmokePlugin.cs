// Development-only harness. Never shipped. Uses the main menu without opening a save.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using BepInEx;
using BepInEx.Configuration;
using GK2.HotkeyManager;
using LazyBearTechnology;
using UnityEngine;
using UnityEngine.UI;

[BepInPlugin("local.gk2.hotkeymanager.smoke", "Hotkey Manager Smoke Check", "1.1.0")]
[BepInDependency("local.gk2.hotkeymanager")]
public class SmokePlugin : BaseUnityPlugin
{
    HotkeyAction action;
    int calls;
    readonly List<string> report = new List<string>();
    void Awake() => action = Hotkeys.Register(this, "smoke.action", "Toggle smoke lamp", new KeyboardShortcut(KeyCode.F12, KeyCode.LeftControl), "RT+R3", () => calls++, TriggerMode.Release, "Turn the test lamp on or off. This action exercises the public registration API.");
    void Start()
    {
        var args = Environment.GetCommandLineArgs();
        int flag = Array.IndexOf(args, "-gk2-hotkey-smoke");
        if (flag >= 0 && flag + 1 < args.Length) StartCoroutine(Run(args[flag + 1]));
    }
    void Check(bool result, string label)
    {
        report.Add((result ? "PASS " : "FAIL ") + label);
        Logger.LogInfo(report.Last());
    }
    IEnumerator Run(string output)
    {
        Directory.CreateDirectory(output);
        yield return new WaitForSecondsRealtime(18);
        var manager = Plugin.Instance;
        var window = LazyUI.GetWindow<UIGameBindingSettingsWindow>();
        float timeScale = Time.timeScale;
        window.Open(null);
        yield return new WaitForSecondsRealtime(2);
        var controls = manager.Controls;
        if (controls == null) { File.WriteAllText(Path.Combine(output, "smoke-report.txt"), "FAIL native panel did not attach"); yield break; }
        var rows = controls.Rows;
        Check(window.IsShown && rows.Count > 20, "native Controls attached");
        Check(!GameplayContexts.NoMenusOpen, "Native Controls blocks NoMenusOpen");
        Check(!GameplayContexts.IsAllowed(ActionContext.NoMenusOpen, w => ReferenceEquals(w, window)), "Window exception cannot bypass underlying main menu");
        Check(GameplayContexts.IsAllowed(ActionContext.NoMenusOpen, w => true), "Explicit exceptions cover all native open windows");
        Check(!GameplayContexts.GameplayOnly, "Gameplay context blocks unloaded main menu");
        Check(rows.Count(r => r.Binding?.Id == Info.Metadata.GUID) == 2, "API exposes keyboard and controller once");
        Check(rows.All(r => r.Conditions != null), "Every native and mod row has a readonly Conditions cell");
        Check(window.transform.Find("GenericWIndowLayout").GetComponent<RectTransform>().rect.width >= 450, "Controls panel widened for Conditions column");
        Check(rows.Where(r => r.Binding?.Id == Info.Metadata.GUID).All(r => r.Binding.ConditionTags.Length != 0), "API condition tags populated");
        Check(manager.Rows.Where(r => r.Conditions == null && r.Mod != "Game").All(r => r.ConditionTags == "Not exposed"), "Imported condition metadata marked Not exposed");
        try { Hotkeys.Register(this, "smoke.action", "Duplicate", KeyboardShortcut.Empty); Check(false, "reject duplicate ID"); }
        catch (ArgumentException) { Check(true, "reject duplicate ID"); }
        try { Hotkeys.Register(this, "smoke.reserved", "Reserved", new KeyboardShortcut(KeyCode.F4, KeyCode.LeftAlt)); Check(false, "reject reserved API default"); }
        catch (ArgumentException) { Check(true, "reject reserved API default"); }
        report.Add("Mod bindings: " + manager.Rows.Count + "; game rows: " + rows.Count(r => !r.Modded));
        report.AddRange(manager.Rows.Select(r => r.Id + " | " + r.Kind + " | " + r.Label + " | " + r.Combined + "\n" + r.Details));
        report.AddRange(window.GetComponentsInChildren<RectTransform>(true).Take(100).Select(r => r.name + " rect=" + r.rect + " anchor=" + r.anchorMin + "/" + r.anchorMax + " pos=" + r.anchoredPosition));
        controls.SetFilter("Game");
        Check(rows.Where(r => r.Modded).All(r => !r.Element.gameObject.activeSelf) && rows.Where(r => !r.Modded).All(r => r.Element.gameObject.activeSelf), "Game filter");
        yield return new WaitForSecondsRealtime(1);
        UiCapture.Capture(Path.Combine(output, "game-screen.png"));
        yield return new WaitForSecondsRealtime(1);
        controls.SetFilter("Modded");
        Check(rows.Where(r => !r.Modded).All(r => !r.Element.gameObject.activeSelf) && rows.Where(r => r.Modded).All(r => r.Element.gameObject.activeSelf), "Modded filter");
        yield return new WaitForSecondsRealtime(1);
        UiCapture.Capture(Path.Combine(output, "modded-screen.png"));
        yield return new WaitForSecondsRealtime(1);
        var modRow = rows.First(r => r.Binding?.Id == "gk2.codex" && !r.Binding.IsPad);
        Check(modRow.Details.Contains("Read by:") && modRow.Details.Contains("Config field:"), "Imported tooltip identifies reader methods and field");
        controls.ShowTooltip(modRow);
        yield return new WaitForSecondsRealtime(1);
        UiCapture.Capture(Path.Combine(output, "tooltip-screen.png"));
        yield return new WaitForSecondsRealtime(1);
        controls.HideTooltip();
        controls.BeginCapture(modRow);
        Check(controls.IsCapturing && manager.BlockInput && !modRow.Button.interactable, "Capture locks input and row buttons");
        modRow.Binding.Draft = "F8+LeftControl";
        controls.Cancel();
        Check(!controls.IsCapturing && !modRow.Binding.Dirty && modRow.Button.interactable, "Cancel discards staged change and unlocks");
        var pad = rows.First(r => r.Binding?.Id == "gk2.codex" && r.Binding.IsPad).Binding;
        NativeControls.CaptureDraft(pad, new[] { "RT", "R3" }, "R3");
        Check(pad.Draft == "RightTrigger+RightStick", "Native editor translates game-action chord"); pad.Revert();
        controls.SetFilter("Both");
        Check(rows.All(r => r.Element.gameObject.activeSelf), "Both filter");
        yield return new WaitForSecondsRealtime(1);
        UiCapture.Capture(Path.Combine(output, "both-screen.png"));
        yield return new WaitForSecondsRealtime(1);
        // Exercise callback behavior with real registration/config but injected controller samples.
        action.Reset(); action.Poll(131200, false);
        Check(calls == 0, "Release callback waits while held");
        action.Poll(131072, false); Check(calls == 0, "Release callback waits for final button");
        action.Poll(0, false); action.Poll(0, false); Check(calls == 1, "Release callback fires once");
        action.Reset(); action.Poll(131200, true); action.Poll(0, false); Check(calls == 1, "Blocked API input cannot arm a callback");
        typeof(LazyInput).GetField("isGamepadActivityForcedByPlatform", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, true);
        yield return new WaitForSecondsRealtime(1);
        controls.SetFilter("Modded");
        var gamepadRow = rows.First(r => r.Binding?.Id == Info.Metadata.GUID && r.Binding.IsPad);
        Check(gamepadRow.Element.gameObject.activeInHierarchy, "Controller rows visible in native gamepad mode");
        var filterNav = window.GetComponentsInChildren<LazyButton>(true).First(b => b.name == "HotkeyManager_Game").GetComponentInChildren<GamepadNavigationItem>(true);
        Check(filterNav != null, "Filter has controller navigation");
        filterNav?.Select();
        Check(controls.Filter == "Game" && window.IsShown, "Controller filter Select does not invoke original OK");
        controls.SetFilter("Modded");
        var rowNav = gamepadRow.Element.GetComponentInChildren<GamepadNavigationItem>(true);
        Check(rowNav != null, "Mod row has controller navigation");
        rowNav?.Focus(); rowNav?.Select();
        Check(controls.IsCapturing, "Controller Select opens chord capture"); controls.Cancel();
        yield return new WaitForSecondsRealtime(1);
        Check(rows.Where(r => r.Binding?.IsPad == true && r.Element.gameObject.activeInHierarchy).All(r =>
            r.Action.rectTransform.rect.width >= 150 && r.Key.rectTransform.rect.width >= 100 && r.Conditions.rectTransform.rect.width >= 100 &&
            r.Action.rectTransform.position.x < r.Key.rectTransform.position.x && r.Key.rectTransform.position.x < r.Conditions.rectTransform.position.x),
            "Controller device switch preserves readable three-column geometry");
        report.AddRange(rows.Where(r => r.Binding?.IsPad == true).Take(8).Select(r => "PAD LAYOUT " + r.Binding.Label +
            " element=" + ((RectTransform)r.Element.transform).rect + " action=" + r.Action.rectTransform.rect +
            " actionPos=" + r.Action.rectTransform.position + " active=" + r.Action.gameObject.activeInHierarchy +
            " font=" + r.Action.fontSize + " color=" + r.Action.color + " key=" + r.Key.rectTransform.rect +
            " keyPos=" + r.Key.rectTransform.position + " font=" + r.Key.fontSize +
            " conditions=" + r.Conditions.rectTransform.rect + " pos=" + r.Conditions.rectTransform.position + " active=" + r.Conditions.gameObject.activeInHierarchy));
        UiCapture.Capture(Path.Combine(output, "controller-screen.png"));
        yield return new WaitForSecondsRealtime(1);
        // Apply only the harness's own config to test the real conflict/save transaction.
        var testRow = rows.First(r => r.Binding?.Id == Info.Metadata.GUID && !r.Binding.IsPad);
        string originalFile = File.ReadAllText(Config.ConfigFilePath);
        controls.BeginCapture(testRow); testRow.Binding.Draft = "F4"; controls.Propose();
        Check(controls.IsCapturing && action.Keyboard.Value.MainKey == KeyCode.F12, "Overlap waits for confirmation without saving");
        controls.Cancel(); Check(File.ReadAllText(Config.ConfigFilePath) == originalFile, "Cancelling conflict keeps config bytes");
        var backupsBefore = new HashSet<string>(Directory.GetDirectories(Path.Combine(Paths.ConfigPath, "HotkeyManagerBackups")));
        controls.BeginCapture(testRow); testRow.Binding.Draft = "F4"; controls.Propose(); controls.Save();
        Check(action.Keyboard.Value.MainKey == KeyCode.F4 && !controls.IsCapturing, "Confirmed conflict saves live original entry");
        var backups = Directory.GetDirectories(Path.Combine(Paths.ConfigPath, "HotkeyManagerBackups"));
        string backup = backups.First(d => !backupsBefore.Contains(d) && Directory.GetFiles(d, "*-local.gk2.hotkeymanager.smoke.cfg").Any(f => File.ReadAllText(f) == originalFile));
        report.Add("Harness backup: " + backup);
        Check(File.ReadAllText(Directory.GetFiles(backup, "*-local.gk2.hotkeymanager.smoke.cfg")[0]) == originalFile, "In-game save backs up exact original bytes");
        typeof(LazyInput).GetField("isGamepadActivityForcedByPlatform", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, false);
        typeof(LazyInput).GetField("isGamepadActive", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, false);
        window.Close(); window.Open(null);
        yield return new WaitForSecondsRealtime(1);
        Check(manager.Controls.Rows.Count == rows.Count, "Reopen has no duplicate rows");
        Check(manager.Controls.Rows.Where(r => r.Editable).All(r => r.Element.GetComponentsInChildren<GamepadNavigationItem>(true).Any(n => n.Active)), "Controller navigation survives pooled-row reuse");
        Check(window.GetComponentsInChildren<LazyButton>(true).Count(b => b.name == "HotkeyManager_Both") == 1, "Reopen has one filter bar");
        window.Close(); Check(!window.IsShown, "Panel closes normally");
        Check(Time.timeScale == timeScale, "Game time scale preserved");
        int inventoryCalls = 0, gameplayCalls = 0;
        object inventoryWindow = new object(); object[] liveWindows = new[] { inventoryWindow };
        using (var inventoryAction = Hotkeys.Register(this, "inventory.probe", "Inventory probe", new HotkeyOptions {
            DefaultController = "LB+Y", Trigger = TriggerMode.Press, Conditions = new[] { "WindowActive:Inventory", "!Typing" },
            Callback = () => { inventoryCalls++; liveWindows = new object[0]; } }))
        using (var gameplayAction = Hotkeys.Register(this, "nomenu.probe", "No-menu probe", new HotkeyOptions {
            DefaultController = "LB+Y", Trigger = TriggerMode.Press, Conditions = new[] { "!AnyMenuOpen", "!Typing" }, Callback = () => gameplayCalls++ }))
        {
            var snapshot = new ContextState { UiReady = true, Windows = liveWindows, ActiveWindow = inventoryWindow };
            snapshot.WindowIdentities[inventoryWindow] = new[] { "Inventory" };
            inventoryAction.Poll(0, false, snapshot, ""); gameplayAction.Poll(0, false, snapshot, "");
            inventoryAction.Poll(33024u, false, snapshot, ""); gameplayAction.Poll(33024u, false, snapshot, "");
            Check(inventoryCalls == 1 && gameplayCalls == 0, "Frame snapshot prevents menu-closing chord from passing to no-menu action");
            var next = new ContextState { UiReady = true, Windows = liveWindows };
            gameplayAction.Poll(33024u, false, next, "");
            Check(gameplayCalls == 0, "Context change cannot rearm buttons still held");
            gameplayAction.Poll(0, false, next, ""); gameplayAction.Poll(33024u, false, next, "");
            Check(gameplayCalls == 1, "Exclusive shared chord activates after neutral input in new context");
        }
        using (var declared = Hotkeys.Register(this, "combat.probe", "Out-of-combat probe", new HotkeyOptions {
            DefaultController = "LB+Y", Conditions = new[] { "GameLoaded", "!InCombat", "!Typing" }, Callback = () => calls++ }))
        {
            declared.Reset(); declared.Poll(33024u, false); declared.Poll(0, false);
            Check(!declared.IsHeld && calls == 1 && declared.BlockedReason == "", "Declared action fails closed at main menu without normal debug text");
            var debug = manager.Config.Bind("Debug", "Enabled", false); debug.Value = true;
            declared.Poll(33024u, false);
            Check(declared.BlockedReason.Length != 0, "Debug mode exposes why the declared action is blocked");
            debug.Value = false;
            Check(declared.BlockedReason == "", "Disabling debug immediately hides blocked reasons");
            Check(declared.Managed, "Condition restrictions preserve action ownership");
        }
        File.WriteAllLines(Path.Combine(output, "smoke-report.txt"), report);
        Logger.LogInfo("HOTKEY SMOKE COMPLETE");
    }
    void OnDestroy() => action?.Dispose();
}
