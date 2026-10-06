using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BepInEx.Configuration;
using GK2.HotkeyManager;
using UnityEngine;

enum GamepadShortcut { None, A, B, LeftStick, RightStick, LeftTrigger, RightTrigger }
enum PadButton { None, L1, R1, L2, R2 }

class Program
{
    static int checks;
    static void Check(bool ok, string label)
    {
        if (!ok) throw new Exception(label);
        checks++;
    }
    static Binding Row(ConfigEntryBase entry, BindingKind kind, string mod = "test", ConfigEntryBase second = null)
    {
        var row = new Binding { Entry = entry, Kind = kind, Mod = mod, Secondary = second };
        row.Revert(); return row;
    }
    static void Main(string[] args)
    {
        string temp = Path.Combine(Path.GetTempPath(), "GK2HotkeyTests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temp);
        try
        {
            var file = new ConfigFile(Path.Combine(temp, "test.cfg"), false);
            var keyboard = Row(file.Bind("Keys", "Open", new KeyboardShortcut(KeyCode.F2), "open"), BindingKind.Keyboard);
            var shifted = Row(file.Bind("Keys", "Hud", new KeyboardShortcut(KeyCode.F2, KeyCode.LeftShift), "hud"), BindingKind.Keyboard);
            var keycode = Row(file.Bind("Keys", "Plain", KeyCode.F2, "open"), BindingKind.Key);
            Check(Rules.Conflict(keyboard, shifted) == "", "BepInEx modifier exclusion");
            Check(Rules.Conflict(keycode, shifted) == "Possible key overlap", "Plain key does not exclude modifiers");
            var pad = Row(file.Bind("Gamepad", "Open", "LeftStick", "GameKey binding"), BindingKind.GameAction);
            var chord = Row(file.Bind("Gamepad", "Hud", "RightTrigger+LeftStick", "GameKey chord"), BindingKind.GameAction);
            var primary = file.Bind("Controls", "GamepadToggle", GamepadShortcut.LeftStick, "controller button");
            var secondary = file.Bind("Controls", "GamepadToggle2", GamepadShortcut.None, "second controller button");
            var pair = Row(primary, BindingKind.PadEnum, "weekly", secondary);
            Rules.ActionButtons["LeftStick"] = "L3";
            Rules.ActionButtons["RightStick"] = "R3";
            Rules.ActionButtons["RightTrigger"] = "RT";
            Rules.ActionNames = new[] { "None", "LeftStick", "RightStick", "RightTrigger" };
            Check(Rules.Conflict(pad, pair) == "Same binding", "Cross-mod enum/action duplicate");
            Check(Rules.Conflict(pad, chord) == "Possible chord overlap", "Single stick/chord overlap");
            pair.SecondaryDraft = "RightTrigger";
            Check(Rules.Conflict(pair, chord) == "Same binding", "Paired controller fields form one chord");
            pair.SecondaryDraft = "None";
            var index = Row(file.Bind("Keys", "PanelGamepad", 8, "Joystick button index"), BindingKind.PadIndex);
            Check(Rules.Conflict(index, pad) == "Same binding", "Raw joystick index aliases physical stick");
            var rawKey = Row(file.Bind("Keys", "Joystick", KeyCode.JoystickButton8, "controller key"), BindingKind.Key);
            Check(Rules.Conflict(index, rawKey) == "Same binding", "Unity joystick key aliases raw index");
            object value; string error;
            Check(!pad.TryValue("MadeUpButton", pad.Entry, out value, out error), "Reject unknown game action");
            Check(!pad.TryValue("None+LeftStick", pad.Entry, out value, out error), "Reject None chord");
            Check(!chord.TryValue("LeftStick+LeftStick", chord.Entry, out value, out error), "Reject repeated action");
            Check(!index.TryValue("20", index.Entry, out value, out error), "Reject raw index outside range");
            Check(!keyboard.TryValue("nonsense", keyboard.Entry, out value, out error), "Reject invalid keyboard key");
            Check(!pair.TryValue("RightTrigger+RightStick", pair.Entry, out value, out error), "Enum cannot store a chord");
            Check(Binding.Detect(typeof(bool), "Gamepad", "Enabled", "controller") == null, "Do not edit boolean settings");
            Check(Binding.Detect(typeof(float), "Gamepad", "RepeatDelay", "controller") == null, "Do not edit repeat timing");
            Check(Binding.Detect(typeof(PadButton), "Gamepad", "IncreaseButton", "Gamepad button") == BindingKind.PadEnum, "QuantityStep enum detection");
            Check(Binding.Detect(typeof(string), "Keys", "GamepadStash", "Controller chord: names of the game's own actions") == BindingKind.GameAction, "Quick Stash string detection");
            Check(Binding.Detect(typeof(int), "Keys", "PanelGamepad", "Joystick button index") == BindingKind.PadIndex, "Zombie HQ index detection");
            var list = new List<Binding> { keyboard, shifted, keycode, pad, chord, pair, index, rawKey };
            Check(Rules.Suggest(keyboard, list) && !list.Any(r => r != keyboard && Rules.Conflict(keyboard, r) != ""), "Suggest a free keyboard binding");
            string oldPad = pad.Draft;
            Check(Rules.Suggest(pad, list), "Suggest a controller chord");
            Check(pad.Draft != oldPad && pad.Draft.Contains("+"), "Controller suggestion keeps chord format");
            string oldIndex = index.Draft;
            Check(!Rules.Suggest(index, list) && index.Draft == oldIndex, "No fabricated chord for raw indices");
            file.Save();
            string before = File.ReadAllText(file.ConfigFilePath);
            string backup = BindingStore.Apply(list, Path.Combine(temp, "backups"));
            Check(Directory.GetFiles(backup, "*.cfg").Length == 1, "Back up each config once");
            Check(File.ReadAllText(Directory.GetFiles(backup, "*.cfg")[0]) == before, "Exact original bytes backed up");
            Check(list.All(r => !r.Dirty), "Drafts match saved live entries");
            var reload = new ConfigFile(file.ConfigFilePath, false);
            Check(reload.Bind("Keys", "Open", KeyboardShortcut.Empty).Value.ToString() == keyboard.Draft, "Persisted key reloads");
            keyboard.Draft = "DefinitelyInvalid";
            string saved = File.ReadAllText(file.ConfigFilePath);
            try { BindingStore.Apply(list, Path.Combine(temp, "backups")); throw new Exception("invalid save accepted"); }
            catch (ArgumentException) { Check(File.ReadAllText(file.ConfigFilePath) == saved, "Validate every edit before changing files"); }
            keyboard.Revert();
            // Force failure during saving, after backup and a successful earlier file write.
            var otherFile = new ConfigFile(Path.Combine(temp, "other.cfg"), false);
            var other = Row(otherFile.Bind("Keys", "Open", KeyCode.F6), BindingKind.Key);
            var all = new[] { keyboard, other };
            string old = keyboard.Draft;
            keyboard.Draft = "F11"; other.Draft = "F12";
            using (var readLock = new FileStream(otherFile.ConfigFilePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                try { BindingStore.Apply(all, Path.Combine(temp, "backups")); throw new Exception("locked save accepted"); }
                catch (IOException) { Check(keyboard.Original == old && File.ReadAllText(file.ConfigFilePath) == saved, "Save failure restores earlier file and live values"); }
            }
            Check(file.SaveOnConfigSet && otherFile.SaveOnConfigSet, "Always restore automatic save setting");
            var state = new ChordState();
            Check(!state.Update(false, true, TriggerMode.Release), "Partial chord cannot arm release");
            Check(!state.Update(false, false, TriggerMode.Release), "Unmatched release does not fire");
            Check(!state.Update(true, true, TriggerMode.Release), "Release waits for key up");
            Check(!state.Update(false, true, TriggerMode.Release), "Release waits for every chord member");
            Check(state.Update(false, false, TriggerMode.Release) && !state.Update(false, false, TriggerMode.Release), "Matched release fires once");
            Check(state.Update(true, true, TriggerMode.Press) && !state.Update(true, true, TriggerMode.Press), "Press fires once per rising edge");
            state.Reset(); state.Update(true, true, TriggerMode.Release); state.Suppress();
            Check(!state.Update(false, false, TriggerMode.Release), "Suppressed chord cannot fire");
            var capture = new ChordCapture();
            Check(!capture.Sample(new[] { "A" }, new[] { "A" }, false) && capture.Peak.Length == 0, "Ignore button opening editor");
            capture.Sample(new string[0], new string[0], true);
            capture.Sample(new[] { "RT" }, new[] { "RT" }, false);
            capture.Sample(new[] { "RT", "R3" }, new[] { "R3" }, false);
            capture.Sample(new[] { "RT" }, new string[0], false);
            Check(capture.Sample(new string[0], new string[0], true) && capture.Peak.SequenceEqual(new[] { "RT", "R3" }) && capture.Main == "R3", "Capture simultaneous peak on final release");
            capture = new ChordCapture { WaitingForNeutral = false };
            capture.Sample(new[] { "RT" }, new[] { "RT" }, false);
            capture.Sample(new[] { "R3" }, new[] { "R3" }, false);
            Check(capture.Peak.Length == 1, "Sequential inputs do not fabricate a chord");
            Check(Chords.NormalizeController("RightTrigger+RightStick") == "RT+R3", "Normalize controller aliases");
            Check(Chords.ControllerMask("RT+R3") == 131200 && Chords.ControllerMask("invalid") == 0, "Controller mask validation");
            try { Chords.NormalizeController("R3+RightStick"); throw new Exception("duplicate accepted"); }
            catch (ArgumentException) { Check(true, "Reject repeated physical button aliases"); }
            NativeControls.CaptureDraft(chord, new[] { "RT", "R3" }, "R3");
            Check(chord.Draft == "RightTrigger+RightStick", "Capture converts physical controls to GameKey actions");
            NativeControls.CaptureDraft(pair, new[] { "RT", "L3" }, "L3");
            Check(pair.Draft == "LeftStick" && pair.SecondaryDraft == "RightTrigger", "Capture converts paired enum fields");
            NativeControls.CaptureDraft(index, new[] { "R3" }, "R3");
            Check(index.Draft == "9", "Capture converts joystick index");
            NativeControls.CaptureDraft(keyboard, new[] { "LeftControl", "J" }, "J");
            Check(keyboard.Draft == "J+LeftControl", "Capture stores keyboard main key first");
            try { NativeControls.CaptureDraft(keycode, new[] { "LeftControl", "J" }, "J"); throw new Exception("unsupported chord accepted"); }
            catch (ArgumentException) { Check(true, "Keep single-key mod limitation explicit"); }
            keyboard.TriggerEntry = file.Bind("Keys", "Trigger", TriggerMode.Press);
            keyboard.ActivationEntry = file.Bind("Keys", "Enabled", false);
            keyboard.Revert(); keyboard.TriggerDraft = "Release";
            BindingStore.Apply(new[] { keyboard }, Path.Combine(temp, "backups"));
            Check((TriggerMode)keyboard.TriggerEntry.BoxedValue == TriggerMode.Release && keyboard.ActivationEntry.Value, "Persist trigger-only native override atomically");
            foreach (string reserved in new[] { "F4+LeftAlt", "F4+RightAlt+LeftShift", "Delete+LeftControl+RightAlt", "Escape+LeftControl+LeftShift", "Tab+RightAlt", "Escape+LeftAlt", "Escape+RightControl", "Space+LeftAlt", "LeftWindows", "J+RightCommand", "Print" })
                Check(!keyboard.TryValue(reserved, keyboard.Entry, out value, out error) && error.Contains("Reserved shortcut"), "Reject system shortcut " + reserved);
            Check(keyboard.TryValue("F4+LeftControl", keyboard.Entry, out value, out error), "Ctrl+F4 is not mistaken for Alt+F4");
            Check(keyboard.TryValue("J+LeftControl", keyboard.Entry, out value, out error), "Normal chords remain allowed");
            var source = new LazyBearTechnology.KeyBinding { keyCode = KeyCode.I, additionalKeyCodes = new[] { KeyCode.LeftShift } };
            var native = new NativeKey { Source = source, Shortcut = file.Bind("Native", "Shortcut", new KeyboardShortcut(KeyCode.J, KeyCode.LeftControl)), Enabled = file.Bind("Native", "Enabled", true), Trigger = file.Bind("Native", "Trigger", TriggerMode.Release) };
            native.Apply();
            Check(source.keyCode == KeyCode.J && source.additionalKeyCodes.SequenceEqual(new[] { KeyCode.LeftControl }), "Apply native chord to game's binding object");
            native.Restore();
            Check(source.keyCode == KeyCode.I && source.additionalKeyCodes.SequenceEqual(new[] { KeyCode.LeftShift }), "Restore native primary and original additional keys");
            native.Apply(); source.keyCode = KeyCode.W; native.ForgetOverride();
            Check(source.keyCode == KeyCode.W && source.additionalKeyCodes.SequenceEqual(new[] { KeyCode.LeftShift }), "Clearing override preserves newly reset game primary key");
            state.Reset(); state.Suppress();
            Check(!state.Update(true, true, TriggerMode.Press), "Held input cannot rearm after blocking context");
            Check(!state.Update(false, true, TriggerMode.Press), "Partial release still cannot rearm");
            Check(!state.Update(false, false, TriggerMode.Press) && state.Update(true, true, TriggerMode.Press), "Neutral input rearms a fresh press");
            state.Reset(); state.Update(true, true, TriggerMode.Release); state.Reset(); state.Suppress();
            Check(!state.Update(false, false, TriggerMode.Release), "Opening a menu cancels an armed release");
            Check(!state.Update(true, true, TriggerMode.Release) && state.Update(false, false, TriggerMode.Release), "Fresh chord release works after context cancellation");
            var context = new ContextState { UiReady = true, Loaded = true, Controls = true, ControlsExceptUI = true };
            Check(GameplayContexts.Evaluate(ActionContext.GameplayOnly, context), "Ready gameplay permits actions");
            context.Loaded = false;
            Check(!GameplayContexts.Evaluate(ActionContext.GameplayOnly, context) && GameplayContexts.Evaluate(ActionContext.NoMenusOpen, context), "NoMenusOpen is independent of a loaded save");
            context.Loaded = true; context.Loading = true;
            Check(!GameplayContexts.Evaluate(ActionContext.GameplayOnly, context), "Gameplay blocked while loading");
            context.Loading = false; context.Controls = context.ControlsExceptUI = false;
            Check(!GameplayContexts.Evaluate(ActionContext.GameplayOnly, context), "Flow, cinematics and other player control locks block gameplay");
            context.Controls = context.ControlsExceptUI = true; context.Paused = true;
            Check(!GameplayContexts.Evaluate(ActionContext.GameplayOnly, context), "Pause without an allowed modal blocks gameplay");
            object own = new object(), otherWindow = new object();
            context.Windows = new[] { own }; context.ModalPause = true; context.Controls = false;
            Check(GameplayContexts.Evaluate(ActionContext.GameplayOnly, context, w => w == own), "Own-window exception permits its UI pause and control lock");
            context.ControlsExceptUI = false;
            Check(!GameplayContexts.Evaluate(ActionContext.GameplayOnly, context, w => w == own), "Own window cannot bypass a cinematic or other control lock");
            context.ControlsExceptUI = true; context.Loading = true;
            Check(!GameplayContexts.Evaluate(ActionContext.GameplayOnly, context, w => w == own), "Own window cannot bypass loading");
            context.Loading = false; context.Windows = new[] { otherWindow, own };
            Check(!GameplayContexts.Evaluate(ActionContext.NoMenusOpen, context, w => w == own), "Underlying disallowed window still blocks own-window exception");
            context.Windows = new[] { own };
            Check(!GameplayContexts.Evaluate(ActionContext.NoMenusOpen, context) && GameplayContexts.Evaluate(ActionContext.NoMenusOpen, context, w => w == own), "Menu restriction and explicit exception");
            context.TextInput = true;
            Check(!GameplayContexts.Evaluate(ActionContext.NoMenusOpen, context, w => true), "Focused text entry blocks restricted contexts");
            Check(GameplayContexts.Evaluate(ActionContext.Anywhere, context), "Anywhere is independent of gameplay restrictions");
            context.TextInput = false; context.UiReady = false;
            Check(!GameplayContexts.Evaluate(ActionContext.NoMenusOpen, context), "Unavailable UI state fails closed");
            Hotkeys.ValidateClaims(file, new[] { keyboard.Entry }, new HashSet<ConfigEntryBase>());
            Check(true, "Allow compatible fallback entries in owner config");
            try { Hotkeys.ValidateClaims(file, new[] { other.Entry }, new HashSet<ConfigEntryBase>()); throw new Exception("foreign config accepted"); }
            catch (ArgumentException) { Check(true, "Reject claiming another plugin's config"); }
            try { Hotkeys.ValidateClaims(file, new[] { keyboard.Entry }, new HashSet<ConfigEntryBase> { keyboard.Entry }); throw new Exception("shared claim accepted"); }
            catch (ArgumentException) { Check(true, "Reject two actions claiming the same fallback"); }
            var absent = new GK2.HotkeyManager.Integration.OptionalHotkeyAction("not installed");
            Check(!absent.Managed && !absent.WasTriggered && !absent.IsHeld && absent.UnavailableReason == "not installed", "Optional absent handle exposes fallback ownership without input");
            absent.Dispose(); absent.Dispose();
            Check(!absent.Managed, "Optional dispose is idempotent");
            Check(absent.BlockedReason == "", "Absent optional action has no debug reason");
            ConditionChecks.Run(Check);
            keyboard.Conditions = ConditionSet.Parse(new[] { "WindowActive:Inventory" });
            shifted.Conditions = ConditionSet.Parse(new[] { "!AnyMenuOpen" });
            keyboard.Draft = shifted.Draft = "F2";
            Check(Rules.Conflict(keyboard, shifted) == "" && !Rules.Conflicts(new[] { keyboard, shifted }).Any(), "Exclusive declared conditions suppress physical duplicate warning");
            shifted.Conditions = ConditionSet.Parse(new[] { "WindowOpen:Inventory" });
            Check(Rules.ConflictWarning(keyboard, shifted).StartsWith("Conflict:"), "Compatible declarations report exact duplicate conflict");
            shifted.Conditions = shifted.Conditions.WithCustom(true);
            Check(Rules.ConflictWarning(keyboard, shifted).Contains("custom condition overlap unknown"), "Opaque predicate gives possible conflict");
            shifted.Conditions = null;
            Check(Rules.ConflictWarning(keyboard, shifted).Contains("conditions not exposed"), "Imported declarations remain conservative");
            keyboard.Id = shifted.Id = "sameowner"; keyboard.ApiActionId = shifted.ApiActionId = "sameaction";
            Check(Rules.Conflict(keyboard, shifted) == "", "Same action's two device rows do not warn about duplicate callback");
            Console.WriteLine("Passed " + checks + " checks: discovery, validation, conflicts, suggestions, backups, persistence and rollback.");
        }
        finally { Directory.Delete(temp, true); }
    }
}
