using System;
using System.Collections.Generic;
using System.Linq;
using LazyBearTechnology;

namespace GK2.HotkeyManager
{
    /// <summary>An immutable AND of named requirements. Prefix any token with ! to invert it; unknown state never passes either form.</summary>
    public sealed class ConditionSet
    {
        static readonly Dictionary<string, string> names = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["GameLoaded"] = "GameLoaded", ["Loading"] = "Loading", ["Paused"] = "Paused", ["Typing"] = "Typing",
            ["AnyMenuOpen"] = "AnyMenuOpen", ["InCombat"] = "InCombat", ["DialogueOrCutscene"] = "DialogueOrCutscene",
            ["ControlsEnabled"] = "ControlsEnabled", ["NoMenusOpen"] = "!AnyMenuOpen", ["OutOfCombat"] = "!InCombat",
            ["NotTyping"] = "!Typing", ["NotPaused"] = "!Paused", ["NotLoading"] = "!Loading",
            ["NoDialogueOrCutscene"] = "!DialogueOrCutscene", ["ControlsDisabled"] = "!ControlsEnabled", ["GameNotLoaded"] = "!GameLoaded"
        };
        static readonly Dictionary<string, string> windows = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            { ["Inventory"] = "Inventory", ["Crafting"] = "Crafting", ["Map"] = "Map" };
        readonly Requirement[] requirements;
        readonly bool windowExceptions, gameplayPreset;
        public bool HasCustom { get; }
        public bool IsUnrestricted => requirements.Length == 0 && !HasCustom;
        internal bool RequiresState => requirements.Length != 0;
        public string[] Tokens => requirements.Select(r => r.Token).ToArray();
        public string Tags => requirements.Length == 0 ? (HasCustom ? "Custom" : "Anywhere") :
            string.Join(" · ", requirements.Select(r => r.Label).Concat(HasCustom ? new[] { "Custom" } : new string[0]));
        public string Description => requirements.Length == 0 ? "No declared gameplay restrictions." :
            "All required: " + string.Join("; ", requirements.Select(r => r.Label)) + "." +
            (windowExceptions ? " Explicitly allowed windows are exempt from menu checks." : "") +
            (gameplayPreset && windowExceptions ? " Their UI-only pause and control lock are also exempt." : "") +
            " Unavailable game state blocks both a condition and its opposite.";

        ConditionSet(Requirement[] requirements, bool windowExceptions = false, bool gameplayPreset = false, bool custom = false)
        {
            this.requirements = requirements; this.windowExceptions = windowExceptions;
            this.gameplayPreset = gameplayPreset; HasCustom = custom;
        }

        public static ConditionSet Parse(string[] tokens)
        {
            if (tokens == null) throw new ArgumentNullException(nameof(tokens));
            if (tokens.Length > 32) throw new ArgumentException("An action can declare at most 32 conditions.", nameof(tokens));
            var requirements = tokens.Select(ParseToken).GroupBy(r => r.Token).Select(g => g.First()).ToArray();
            for (int i = 0; i < requirements.Length; i++)
                for (int j = i + 1; j < requirements.Length; j++)
                    if (Opposite(requirements[i], requirements[j]) || ActiveWindowsExclude(requirements[i], requirements[j]))
                        throw new ArgumentException("Contradictory conditions: " + requirements[i].Token + " and " + requirements[j].Token + ".", nameof(tokens));
            return new ConditionSet(requirements);
        }

        public static ConditionSet ForContext(ActionContext context, bool hasWindowExceptions = false)
        {
            switch (context)
            {
                case ActionContext.Anywhere: return new ConditionSet(new Requirement[0], hasWindowExceptions);
                case ActionContext.NoMenusOpen: return Parse(new[] { "!AnyMenuOpen", "!Typing" }).WithWindowExceptions(hasWindowExceptions);
                case ActionContext.GameplayOnly:
                    var set = Parse(new[] { "GameLoaded", "!Loading", "!AnyMenuOpen", "!Typing", "ControlsEnabled", "!Paused" });
                    return new ConditionSet(set.requirements, hasWindowExceptions, true);
                default: throw new ArgumentException("Choose a supported context.", nameof(context));
            }
        }

        public ConditionSet WithWindowExceptions(bool enabled)
        {
            var result = new ConditionSet(requirements, enabled, gameplayPreset, HasCustom);
            if (result.Exclusivity(result)) throw new ArgumentException("Declared conditions cannot all be true together. Window exceptions must be enabled for open-window plus no-menu requirements.");
            return result;
        }
        public ConditionSet WithCustom(bool enabled) => new ConditionSet(requirements, windowExceptions, gameplayPreset, enabled);

        static Requirement ParseToken(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) throw new ArgumentException("Condition tokens cannot be empty.");
            text = text.Trim(); bool negative = text.StartsWith("!", StringComparison.Ordinal);
            if (negative) text = text.Substring(1);
            int colon = text.IndexOf(':');
            if (colon >= 0)
            {
                string kind = text.Substring(0, colon), identity = text.Substring(colon + 1).Trim();
                if (kind.Equals("WindowClosed", StringComparison.OrdinalIgnoreCase)) { kind = "WindowOpen"; negative = !negative; }
                else if (kind.Equals("WindowOpen", StringComparison.OrdinalIgnoreCase)) kind = "WindowOpen";
                else if (kind.Equals("WindowActive", StringComparison.OrdinalIgnoreCase)) kind = "WindowActive";
                else throw new ArgumentException("Unknown condition: " + text + ".");
                if (!windows.TryGetValue(identity, out var canonical))
                {
                    var type = typeof(MainGame).Assembly.GetType(identity, false, false);
                    if (type == null || type.IsAbstract || !IsNativeWindow(type))
                        throw new ArgumentException("Unknown native window: " + identity + ". Use Inventory, Crafting, Map or an exact native window type name.");
                    canonical = type.FullName;
                }
                return new Requirement(kind, negative, canonical);
            }
            if (!names.TryGetValue(text, out var name)) throw new ArgumentException("Unknown condition: " + text + ".");
            if (name.StartsWith("!", StringComparison.Ordinal)) { name = name.Substring(1); negative = !negative; }
            return new Requirement(name, negative);
        }

        static bool IsNativeWindow(Type type)
        {
            for (var parent = type; parent != null; parent = parent.BaseType)
                if (parent.IsGenericType && parent.GetGenericTypeDefinition() == typeof(LazyWindow<>)) return true;
            return false;
        }

        internal bool Evaluate(ContextState state, Func<object, bool> allowWindow, out string reason)
        {
            reason = "";
            foreach (var requirement in requirements)
            {
                bool? value = state == null ? null : requirement.Identity == null ? state.Value(requirement.Name) :
                    state.WindowValue(requirement.Identity, requirement.Name == "WindowActive");
                if (value.HasValue && requirement.Name == "AnyMenuOpen" && allowWindow != null)
                    value = (state.Windows ?? new object[0]).Any(w => !allowWindow(w));
                if (value.HasValue && gameplayPreset && allowWindow != null)
                {
                    var opened = state.Windows ?? new object[0];
                    bool allExempt = opened.Length != 0 && opened.All(allowWindow);
                    if (allExempt && requirement.Name == "ControlsEnabled") value = value.Value || state.ControlsExceptUI;
                    if (allExempt && requirement.Name == "Paused" && state.ModalPause) value = false;
                }
                if (!value.HasValue) { reason = "Unavailable state: " + requirement.Label; return false; }
                if (value.Value == requirement.Negative) { reason = "Requires " + requirement.Label; return false; }
            }
            return true;
        }

        /// <summary>True only when declared requirements prove that two actions cannot be eligible together.</summary>
        internal bool Exclusivity(ConditionSet other)
        {
            if (other == null) return false;
            var left = ProofFacts(); var right = other.ProofFacts();
            return left.Any(a => right.Any(b => Opposite(a, b) || ActiveWindowsExclude(a, b)));
        }

        Requirement[] ProofFacts()
        {
            var facts = requirements.Where(r => !(windowExceptions && (r.Name == "AnyMenuOpen" ||
                (gameplayPreset && (r.Name == "ControlsEnabled" || r.Name == "Paused"))))).ToList();
            foreach (var r in requirements)
            {
                if (r.Identity == null) continue;
                if (r.Name == "WindowActive" && !r.Negative) facts.Add(new Requirement("WindowOpen", false, r.Identity));
                if (r.Name == "WindowOpen" && r.Negative) facts.Add(new Requirement("WindowActive", true, r.Identity));
                // A specific window proves a real menu is open, even if this action exempts it.
                if (!r.Negative) facts.Add(new Requirement("AnyMenuOpen", false));
            }
            return facts.ToArray();
        }

        static bool Opposite(Requirement a, Requirement b) => a.Name == b.Name && a.Identity == b.Identity && a.Negative != b.Negative ||
            a.Identity != null && a.Identity == b.Identity &&
            ((a.Name == "WindowActive" && !a.Negative && b.Name == "WindowOpen" && b.Negative) ||
             (b.Name == "WindowActive" && !b.Negative && a.Name == "WindowOpen" && a.Negative));

        static bool ActiveWindowsExclude(Requirement a, Requirement b)
        {
            if (a.Name != "WindowActive" || b.Name != "WindowActive" || a.Negative || b.Negative || a.Identity == b.Identity) return false;
            // Aliases can coexist with a matching concrete type (Inventory + CharacterWindow), so do not infer those pairs.
            bool aliasA = windows.ContainsValue(a.Identity), aliasB = windows.ContainsValue(b.Identity);
            return aliasA == aliasB;
        }

        sealed class Requirement
        {
            internal readonly string Name, Identity;
            internal readonly bool Negative;
            internal string Token => (Negative ? "!" : "") + Name + (Identity == null ? "" : ":" + Identity);
            internal Requirement(string name, bool negative, string identity = null) { Name = name; Negative = negative; Identity = identity; }
            internal string Label
            {
                get
                {
                    if (Identity != null) return Identity + (Name == "WindowActive" ? (Negative ? " inactive" : " active") : (Negative ? " closed" : " open"));
                    switch (Name)
                    {
                        case "GameLoaded": return Negative ? "Game not loaded" : "Game loaded";
                        case "Loading": return Negative ? "Not loading" : "Loading";
                        case "Paused": return Negative ? "Not paused" : "Paused";
                        case "Typing": return Negative ? "Not typing" : "Typing";
                        case "AnyMenuOpen": return Negative ? "No menus" : "Menu open";
                        case "InCombat": return Negative ? "Out of combat" : "In combat";
                        case "DialogueOrCutscene": return Negative ? "No dialogue/cutscene" : "Dialogue/cutscene";
                        case "ControlsEnabled": return Negative ? "Controls disabled" : "Controls enabled";
                        default: return Token;
                    }
                }
            }
        }
    }
}
