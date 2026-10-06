using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using BepInEx.Configuration;
using UnityEngine;

namespace GK2.HotkeyManager
{
    public enum BindingKind { Keyboard, Key, GameAction, PadEnum, PadIndex, PadChord }

    public sealed class Binding
    {
        public string Mod;
        public string Id;
        public ConfigEntryBase Entry;
        public ConfigEntryBase Secondary;
        public BindingKind Kind;
        public string Draft;
        public string SecondaryDraft;
        public string Title;
        public string Details;
        public ConditionSet Conditions;
        public string ApiActionId;
        public string ConditionTags => Conditions?.Tags ?? (Mod == "Game" ? "Game" : "Not exposed");
        public string ConditionDetails;
        public ConfigEntryBase TriggerEntry;
        public ConfigEntry<bool> ActivationEntry;
        public string TriggerDraft;
        public string Label => Title ?? Entry.Definition.Key;
        public string Original => Entry.GetSerializedValue();
        public bool Dirty => Draft != Original || (Secondary != null && SecondaryDraft != Secondary.GetSerializedValue()) ||
            (TriggerEntry != null && TriggerDraft != TriggerEntry.GetSerializedValue());
        public bool IsPad => Kind == BindingKind.GameAction || Kind == BindingKind.PadEnum || Kind == BindingKind.PadIndex || Kind == BindingKind.PadChord;
        public string Combined => Secondary == null || Rules.IsNone(SecondaryDraft) ? Draft : Draft + "+" + SecondaryDraft;
        public void Revert() { Draft = Original; SecondaryDraft = Secondary?.GetSerializedValue(); TriggerDraft = TriggerEntry?.GetSerializedValue(); }

        public static BindingKind? Detect(Type type, string section, string key, string description)
        {
            if (type == typeof(KeyboardShortcut)) return BindingKind.Keyboard;
            if (type == typeof(KeyCode)) return BindingKind.Key;
            string context = section + " " + key + " " + description;
            bool pad = Regex.IsMatch(context, @"gamepad|controller|joystick|\bpad\b", RegexOptions.IgnoreCase);
            bool binding = Regex.IsMatch(key, "key|button|bind|toggle|shortcut|gamepad", RegexOptions.IgnoreCase);
            if (type.IsEnum && pad && binding && Enum.GetNames(type).Any(n => n == "None") &&
                Enum.GetNames(type).All(n => Rules.PadNames.ContainsKey(n))) return BindingKind.PadEnum;
            if (type == typeof(string) && pad && binding && Regex.IsMatch(description,
                @"GameKey|game.?s own actions|chord|binding|gamepad action", RegexOptions.IgnoreCase)) return BindingKind.GameAction;
            if (type == typeof(int) && pad && binding && Regex.IsMatch(description,
                @"joystick button|button index", RegexOptions.IgnoreCase)) return BindingKind.PadIndex;
            return null;
        }

        public bool TryValue(string text, ConfigEntryBase entry, out object value, out string error)
        {
            value = null;
            error = "";
            try
            {
                if (entry == TriggerEntry)
                {
                    if (!Enum.GetNames(typeof(TriggerMode)).Contains(text)) throw new ArgumentException("Choose Press or Release.");
                    value = Enum.Parse(typeof(TriggerMode), text);
                }
                else if (entry.SettingType == typeof(string) && Kind == BindingKind.PadChord)
                {
                    value = Chords.NormalizeController(text);
                }
                else if (entry.SettingType == typeof(string))
                {
                    var tokens = text.Split('+').Select(t => t.Trim()).ToArray();
                    if (!Rules.IsNone(text) && (tokens.Any(t => !Rules.ActionNames.Contains(t)) ||
                        tokens.Distinct().Count() != tokens.Length || tokens.Contains("None")))
                        throw new ArgumentException("Choose valid game actions; use None to disable.");
                    value = Rules.IsNone(text) ? "None" : string.Join("+", tokens);
                }
                else if (entry.SettingType == typeof(int))
                {
                    int index;
                    if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out index) || index < -1 || index > 19)
                        throw new ArgumentException("Use a joystick index from -1 (off) to 19.");
                    value = index;
                }
                else if (entry.SettingType == typeof(KeyboardShortcut))
                {
                    var keys = ParseKeys(text);
                    value = keys.Length == 0 ? KeyboardShortcut.Empty : new KeyboardShortcut(keys[0], keys.Skip(1).ToArray());
                }
                else
                {
                    if (entry.SettingType == typeof(KeyCode) && Rules.IsNone(text)) text = "None";
                    if (!Enum.GetNames(entry.SettingType).Contains(text)) throw new ArgumentException("Choose a value from the button list.");
                    value = Enum.Parse(entry.SettingType, text);
                }
                if (value is KeyboardShortcut shortcut) ReservedKeys.Validate(Chords.Keys(shortcut));
                else if (value is KeyCode code) ReservedKeys.Validate(new[] { code });
                if (entry.Description.AcceptableValues != null && !entry.Description.AcceptableValues.IsValid(value))
                    throw new ArgumentException("This mod does not accept that binding.");
                return true;
            }
            catch (Exception ex) { error = ex.Message; return false; }
        }

        static KeyCode[] ParseKeys(string text)
        {
            if (Rules.IsNone(text)) return new KeyCode[0];
            var tokens = text.Split('+').Select(t => t.Trim()).ToArray();
            if (tokens.Any(t => !Enum.GetNames(typeof(KeyCode)).Contains(t) || t == "None") || tokens.Distinct().Count() != tokens.Length)
                throw new ArgumentException("Use key names joined with +, or press Capture.");
            return tokens.Select(t => (KeyCode)Enum.Parse(typeof(KeyCode), t)).ToArray();
        }

        public IEnumerable<string> Choices()
        {
            if (Kind == BindingKind.PadEnum) return Enum.GetNames(Entry.SettingType);
            if (Kind == BindingKind.PadIndex) return Enumerable.Range(-1, 21).Select(i => i.ToString(CultureInfo.InvariantCulture));
            if (Kind == BindingKind.GameAction) return Rules.ActionNames.Where(n => n == "None" || Rules.ActionButtons.ContainsKey(n) || Draft.Split('+').Contains(n));
            if (Kind == BindingKind.PadChord) return new[] { "None", "A", "B", "X", "Y", "LB", "RB", "LT", "RT", "L3", "R3", "Back", "Start", "Up", "Down", "Left", "Right" };
            return new[] { "None" }.Concat(Enumerable.Range(1, 12).Select(i => "F" + i));
        }
    }

    public static class ReservedKeys
    {
        public static void Validate(IEnumerable<KeyCode> keys)
        {
            var held = new HashSet<KeyCode>(keys);
            bool alt = held.Contains(KeyCode.LeftAlt) || held.Contains(KeyCode.RightAlt);
            bool control = held.Contains(KeyCode.LeftControl) || held.Contains(KeyCode.RightControl);
            bool shift = held.Contains(KeyCode.LeftShift) || held.Contains(KeyCode.RightShift);
            string reason = null;
            if (held.Contains(KeyCode.LeftWindows) || held.Contains(KeyCode.RightWindows) || held.Contains(KeyCode.LeftCommand) || held.Contains(KeyCode.RightCommand)) reason = "Windows/Command keys are reserved for the operating system";
            else if (alt && held.Contains(KeyCode.F4)) reason = "Alt+F4 closes the game";
            else if (control && alt && held.Contains(KeyCode.Delete)) reason = "Ctrl+Alt+Delete opens the system security screen";
            else if (control && shift && held.Contains(KeyCode.Escape)) reason = "Ctrl+Shift+Esc opens Task Manager";
            else if (alt && held.Contains(KeyCode.Tab)) reason = "Alt+Tab switches applications";
            else if (alt && held.Contains(KeyCode.Escape)) reason = "Alt+Esc switches applications";
            else if (control && held.Contains(KeyCode.Escape)) reason = "Ctrl+Esc opens the Start menu";
            else if (alt && held.Contains(KeyCode.Space)) reason = "Alt+Space opens the window menu";
            else if (held.Contains(KeyCode.Print)) reason = "Print Screen is reserved for system screenshots";
            if (reason != null) throw new ArgumentException("Reserved shortcut: " + reason + ". Choose another binding.");
        }
    }

    public static class Rules
    {
        // Names are converted to physical buttons before comparison. Live game bindings
        // populate ActionButtons, including aliases, so contextual game actions are covered.
        public static readonly Dictionary<string, string> PadNames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            { "None", "" }, { "A", "A" }, { "B", "B" }, { "X", "X" }, { "Y", "Y" },
            { "L1", "LB" }, { "R1", "RB" }, { "L2", "LT" }, { "R2", "RT" },
            { "LB", "LB" }, { "RB", "RB" }, { "LT", "LT" }, { "RT", "RT" },
            { "LeftShoulder", "LB" }, { "RightShoulder", "RB" },
            { "LeftTrigger", "LT" }, { "RightTrigger", "RT" },
            { "LeftStick", "L3" }, { "RightStick", "R3" }, { "LStick", "L3" }, { "RStick", "R3" },
            { "Start", "Start" }, { "Back", "Back" }, { "DPadUp", "Up" }, { "DPadDown", "Down" },
            { "DPadLeft", "Left" }, { "DPadRight", "Right" },
            { "DUp", "Up" }, { "DDown", "Down" }, { "DLeft", "Left" }, { "DRight", "Right" },
            { "Up", "Up" }, { "Down", "Down" }, { "Left", "Left" }, { "Right", "Right" }, { "L3", "L3" }, { "R3", "R3" }
        };
        public static readonly Dictionary<string, string> ActionButtons = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        public static string[] ActionNames = { "None", "LeftStick", "RightStick", "LeftTrigger", "RightTrigger", "RightBumper" };
        static readonly string[] JoystickButtons = { "A", "B", "X", "Y", "LB", "RB", "Back", "Start", "L3", "R3" };
        public static bool IsNone(string value) => string.IsNullOrWhiteSpace(value) || value.Trim().Equals("None", StringComparison.OrdinalIgnoreCase) || value.Trim() == "-1";

        public static HashSet<string> Tokens(Binding row)
        {
            var result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (Rules.IsNone(row.Draft)) return result;
            if (row.Kind == BindingKind.PadIndex)
            {
                int index;
                if (int.TryParse(row.Draft, out index) && index >= 0)
                    result.Add("pad:" + (index < JoystickButtons.Length ? JoystickButtons[index] : "index" + index));
                return result;
            }
            foreach (var raw in row.Combined.Split('+').Select(t => t.Trim()).Where(t => !IsNone(t)))
            {
                string button;
                if (row.Kind == BindingKind.GameAction)
                {
                    if (!ActionButtons.TryGetValue(raw, out button)) button = "action:" + raw;
                    result.Add("pad:" + button);
                }
                else if (row.Kind == BindingKind.PadEnum || row.Kind == BindingKind.PadChord)
                {
                    result.Add("pad:" + (PadNames.TryGetValue(raw, out button) ? button : raw));
                }
                else
                {
                    var match = Regex.Match(raw, @"^Joystick(?:\d+)?Button(\d+)$", RegexOptions.IgnoreCase);
                    if (match.Success)
                    {
                        int index = int.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture);
                        result.Add("pad:" + (index < JoystickButtons.Length ? JoystickButtons[index] : "index" + index));
                    }
                    else result.Add("key:" + raw);
                }
            }
            return result;
        }

        public static string Conflict(Binding first, Binding second)
        {
            if (first.Conditions != null && second.Conditions != null && first.Conditions.Exclusivity(second.Conditions)) return "";
            // Keyboard and controller rows of one API action invoke its callback at most once per frame.
            if (first.ApiActionId != null && first.Id == second.Id && first.ApiActionId == second.ApiActionId) return "";
            return PhysicalOverlap(first, second);
        }

        public static string ConflictWarning(Binding first, Binding second)
        {
            string overlap = Conflict(first, second);
            if (overlap.Length == 0) return "";
            bool declared = first.Conditions != null && second.Conditions != null;
            bool custom = declared && (first.Conditions.HasCustom || second.Conditions.HasCustom);
            return (declared && !custom && overlap == "Same binding" ? "Conflict: " : "Possible conflict: ") + overlap +
                (!declared ? " (conditions not exposed)" : custom ? " (custom condition overlap unknown)" : "");
        }

        static string PhysicalOverlap(Binding first, Binding second)
        {
            var a = Tokens(first);
            var b = Tokens(second);
            if (a.Count == 0 || b.Count == 0) return "";
            if (a.SetEquals(b)) return "Same binding";
            // BepInEx KeyboardShortcut checks extra keyboard modifiers itself. Controller
            // mods vary, so subsets are shown conservatively as a potential overlap.
            if ((a.Any(t => t.StartsWith("pad:")) || b.Any(t => t.StartsWith("pad:"))) && (a.IsSubsetOf(b) || b.IsSubsetOf(a)))
                return "Possible chord overlap";
            if ((first.Kind == BindingKind.Key || second.Kind == BindingKind.Key || first.TriggerEntry != null || second.TriggerEntry != null) && (a.IsSubsetOf(b) || b.IsSubsetOf(a)))
                return "Possible key overlap";
            return "";
        }

        public static IEnumerable<Tuple<Binding, Binding, string>> Conflicts(IList<Binding> rows)
        {
            // ponytail: pairwise scan is sufficient for a mod list; index by buttons if thousands of bindings appear.
            for (int i = 0; i < rows.Count; i++)
                for (int j = i + 1; j < rows.Count; j++)
                {
                    string reason = ConflictWarning(rows[i], rows[j]);
                    if (reason.Length != 0) yield return Tuple.Create(rows[i], rows[j], reason);
                }
        }

        public static bool Suggest(Binding row, IList<Binding> rows)
        {
            string old = row.Draft, oldSecond = row.SecondaryDraft;
            var candidates = new List<string>();
            if (row.Kind == BindingKind.Keyboard || row.Kind == BindingKind.Key)
            {
                candidates.AddRange(Enumerable.Range(5, 8).Select(i => "F" + i));
                if (row.Kind == BindingKind.Keyboard)
                    candidates.AddRange(Enumerable.Range(1, 12).Select(i => "F" + i + " + LeftControl + LeftShift"));
            }
            else if (row.Kind == BindingKind.PadChord)
            {
                foreach (string modifier in new[] { "RT", "RB", "LT", "LB" })
                    candidates.AddRange(row.Choices().Where(n => !IsNone(n) && n != modifier).Select(n => modifier + "+" + n));
            }
            else if (row.Kind == BindingKind.GameAction)
            {
                var actions = ActionNames.Where(n => !IsNone(n) && ActionButtons.ContainsKey(n)).ToArray();
                foreach (string mod in new[] { "RightTrigger", "NextTab", "RightBumper", "LeftTrigger" }.Where(actions.Contains))
                    candidates.AddRange(actions.Where(n => n != mod).Select(n => mod + "+" + n));
            }
            else if (row.Secondary != null)
            {
                foreach (string main in row.Choices().Where(n => !IsNone(n)))
                    candidates.AddRange(Enum.GetNames(row.Secondary.SettingType).Where(n => n != main && !IsNone(n)).Select(n => main + "+" + n));
            }
            // Single-button mods keep their native limitation. No hidden disabling or invented chords.
            foreach (string candidate in candidates)
            {
                row.Draft = candidate;
                row.SecondaryDraft = oldSecond;
                if (row.Secondary != null)
                {
                    var parts = candidate.Split('+');
                    row.Draft = parts[0]; row.SecondaryDraft = parts.Length > 1 ? parts[1] : "None";
                }
                object value; string error;
                if (!row.TryValue(row.Draft, row.Entry, out value, out error)) continue;
                if (row.Secondary != null && !row.TryValue(row.SecondaryDraft, row.Secondary, out value, out error)) continue;
                if (rows.All(other => other == row || Conflict(row, other).Length == 0)) return true;
            }
            row.Draft = old; row.SecondaryDraft = oldSecond;
            return false;
        }
    }

    public static class BindingStore
    {
        public static string Apply(IList<Binding> rows, string backupRoot)
        {
            var updates = new List<Tuple<ConfigEntryBase, object, object>>();
            foreach (var row in rows.Where(r => r.Dirty))
            {
                object value; string error;
                if (!row.TryValue(row.Draft, row.Entry, out value, out error)) throw new ArgumentException(row.Mod + " / " + row.Label + ": " + error);
                updates.Add(Tuple.Create(row.Entry, value, row.Entry.BoxedValue));
                if (row.TriggerEntry != null)
                {
                    if (!row.TryValue(row.TriggerDraft, row.TriggerEntry, out value, out error)) throw new ArgumentException(row.Mod + ": " + error);
                    updates.Add(Tuple.Create(row.TriggerEntry, value, row.TriggerEntry.BoxedValue));
                }
                if (row.ActivationEntry != null)
                    updates.Add(Tuple.Create((ConfigEntryBase)row.ActivationEntry, (object)true, row.ActivationEntry.BoxedValue));
                if (row.Secondary != null)
                {
                    if (!row.TryValue(row.SecondaryDraft, row.Secondary, out value, out error)) throw new ArgumentException(row.Mod + ": " + error);
                    updates.Add(Tuple.Create(row.Secondary, value, row.Secondary.BoxedValue));
                }
            }
            if (updates.Count == 0) return "";
            var files = updates.Select(u => u.Item1.ConfigFile).Distinct().ToArray();
            Dictionary<ConfigFile, string> backups;
            string backup = BackupFiles(files, backupRoot, out backups);
            var autoSave = files.ToDictionary(f => f, f => f.SaveOnConfigSet);
            try
            {
                foreach (var file in files) file.SaveOnConfigSet = false;
                foreach (var update in updates) update.Item1.BoxedValue = update.Item2;
                foreach (var file in files) file.Save();
            }
            catch (Exception saveError)
            {
                var recoveryErrors = new List<string>();
                foreach (var update in updates)
                    try { update.Item1.BoxedValue = update.Item3; } catch (Exception ex) { recoveryErrors.Add(ex.Message); }
                foreach (var file in files)
                    try { File.Copy(backups[file], file.ConfigFilePath, true); } catch (Exception ex) { recoveryErrors.Add(ex.Message); }
                throw new IOException("Save failed: " + saveError.Message + ". Backup: " + backup +
                    (recoveryErrors.Count == 0 ? ". Original settings restored." : ". Restore needed: " + string.Join("; ", recoveryErrors)), saveError);
            }
            finally { foreach (var file in files) file.SaveOnConfigSet = autoSave[file]; }
            foreach (var row in rows) row.Revert();
            return backup;
        }
        internal static string BackupFiles(IEnumerable<ConfigFile> files, string backupRoot, out Dictionary<ConfigFile, string> backups)
        {
            string backup = Path.Combine(backupRoot, DateTime.UtcNow.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            Directory.CreateDirectory(backup);
            backups = new Dictionary<ConfigFile, string>();
            foreach (var file in files)
            {
                if (!File.Exists(file.ConfigFilePath)) throw new IOException("Missing config: " + file.ConfigFilePath);
                string name = backups.Count.ToString("D3", CultureInfo.InvariantCulture) + "-" + Path.GetFileName(file.ConfigFilePath);
                string target = Path.Combine(backup, name);
                File.Copy(file.ConfigFilePath, target);
                backups.Add(file, target);
            }
            File.WriteAllLines(Path.Combine(backup, "restore-paths.txt"), backups.Select(p => Path.GetFileName(p.Value) + " -> " + p.Key.ConfigFilePath));
            return backup;
        }
    }
}
