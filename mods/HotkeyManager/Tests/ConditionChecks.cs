using System;
using System.Collections.Generic;
using System.Linq;
using GK2.HotkeyManager;

static class ConditionChecks
{
    internal static void Run(Action<bool, string> check)
    {
        ConditionSet Set(params string[] tokens) => ConditionSet.Parse(tokens);
        void Reject(string[] tokens, string label)
        {
            try { Set(tokens); check(false, label); }
            catch (ArgumentException) { check(true, label); }
        }
        var ready = new ContextState { UiReady = true, Loaded = true, Controls = true, ControlsExceptUI = true, Combat = false, Dialogue = false };
        check(Set().Evaluate(null, null, out _) && Set().IsUnrestricted, "Empty condition list permits actions without game state");
        check(Set("gameLoaded", "NotTyping", "OutOfCombat", "NoDialogueOrCutscene").Evaluate(ready, null, out _), "Conditions AND canonical and inverse aliases");
        check(Set("!OutOfCombat").Tokens.Single() == "InCombat", "Inversion applies to an opposite alias");
        check(Set("WindowClosed:inventory").Tokens.Single() == "!WindowOpen:Inventory", "WindowClosed normalizes with a validated alias");
        Reject(new[] { "GameLoaded", "!GameLoaded" }, "Reject direct opposite requirements");
        Reject(new[] { "WindowActive:Inventory", "WindowClosed:Inventory" }, "Reject active-and-closed same window");
        Reject(new[] { "WindowActive:Inventory", "WindowActive:Map" }, "Reject simultaneous distinct active alias windows");
        Reject(new[] { "UnknownState" }, "Reject unknown condition names");
        Reject(new[] { "WindowOpen:Inventroy" }, "Reject misspelled window identities");
        Reject(new[] { "WindowOpen:MainGame" }, "Reject native types that are not windows");
        Reject(new[] { "WindowClosed:CharMainPageWidget" }, "Reject non-window widgets so closed requirements cannot silently pass");
        Reject(new[] { " " }, "Reject empty conditions");
        var tokens = new[] { "GameLoaded" };
        var immutable = Set(tokens); tokens[0] = "Loading"; var exposed = immutable.Tokens; exposed[0] = "Loading";
        check(immutable.Tokens.Single() == "GameLoaded", "ConditionSet retains immutable copied tokens");
        foreach (string name in new[] { "GameLoaded", "Loading", "Paused", "Typing", "AnyMenuOpen", "ControlsEnabled", "InCombat", "DialogueOrCutscene" })
        {
            var unknown = new ContextState { UiReady = true, Known = new HashSet<string>() };
            check(!Set(name).Evaluate(unknown, null, out var reason) && !Set("!" + name).Evaluate(unknown, null, out _) && reason.Contains("Unavailable"), "Unknown state fails both forms: " + name);
            check(Set(name).Exclusivity(Set("!" + name)), "Opposite conditions prove exclusivity: " + name);
        }
        check(!Set("InCombat").Evaluate(ready, null, out _) && Set("OutOfCombat").Evaluate(ready, null, out _), "Known inactive combat admits only OutOfCombat");
        ready.Combat = true;
        check(Set("InCombat").Evaluate(ready, null, out _) && !Set("OutOfCombat").Evaluate(ready, null, out _), "Known active combat admits only InCombat");
        var inventory = new object(); var map = new object();
        ready.Windows = new[] { inventory, map }; ready.ActiveWindow = map;
        ready.WindowIdentities[inventory] = new[] { "Inventory", "CharacterWindow" };
        ready.WindowIdentities[map] = new[] { "Map", "UIMapWindow" };
        check(Set("WindowOpen:Inventory").Evaluate(ready, null, out _) && !Set("WindowActive:Inventory").Evaluate(ready, null, out _), "Open inventory under another window is not active");
        check(Set("WindowActive:Map").Evaluate(ready, null, out _) && !Set("WindowClosed:Map").Evaluate(ready, null, out _), "Active window has its open identity");
        check(!Set("WindowClosed:Inventory").Evaluate(ready, null, out _), "Inventory closed differs from no-menu restriction");
        ready.Windows = new[] { map };
        check(Set("WindowClosed:Inventory", "AnyMenuOpen").Evaluate(ready, null, out _), "Inventory closed permits a different menu");
        check(Set("WindowActive:Inventory").Exclusivity(Set("NoMenusOpen")), "Inventory active and no menus cannot overlap");
        check(!Set("WindowActive:Inventory").Exclusivity(Set("NoMenusOpen").WithWindowExceptions(true)), "Own-window exception prevents no-menu exclusivity proof");
        check(!Set("AnyMenuOpen").Exclusivity(Set("NoMenusOpen").WithWindowExceptions(true)), "Different menu exemption functions can overlap opposite menu conditions");
        check(Set("WindowActive:Inventory").Exclusivity(Set("WindowClosed:Inventory")), "Active window cannot overlap same window closed");
        check(!Set("WindowOpen:Inventory").Exclusivity(Set("WindowOpen:Map")), "Different open windows can coexist");
        check(Set("WindowActive:Inventory").Exclusivity(Set("WindowActive:Map")), "Only one alias window can be active");
        check(!Set("WindowActive:Inventory").Exclusivity(Set("WindowActive:CharacterWindow")), "Alias and concrete active identity may refer to same window");
        check(Set("WindowActive:UIMapWindow").Exclusivity(Set("WindowActive:CharacterWindow")), "Different concrete active types cannot coexist");
        check(Set("GameLoaded").WithCustom(true).Exclusivity(Set("!GameLoaded")), "Custom predicates cannot widen declaratively disjoint conditions");
        check(!Set().WithCustom(true).IsUnrestricted && Set().WithCustom(true).HasCustom, "Custom metadata is retained for uncertainty classification");
        check(Set().WithCustom(true).Tags == "Custom" && Set("GameLoaded").WithCustom(true).Tags == "Game loaded · Custom", "Custom condition tags are visible in the controls column");
        try { Set("WindowOpen:Inventory", "NoMenusOpen").WithWindowExceptions(false); check(false, "Reject impossible menu implications after exceptions finalized"); }
        catch (ArgumentException) { check(true, "Reject impossible menu implications after exceptions finalized"); }
        ready.Windows = new[] { inventory }; ready.ActiveWindow = inventory;
        check(Set("WindowOpen:Inventory", "NoMenusOpen").WithWindowExceptions(true).Evaluate(ready, w => w == inventory, out _), "Open owned window can coexist with no unexempted menus");
        ready.Windows = new[] { inventory }; ready.ActiveWindow = inventory; ready.Paused = ready.ModalPause = true; ready.Controls = false;
        var gameplay = ConditionSet.ForContext(ActionContext.GameplayOnly, true);
        check(gameplay.Evaluate(ready, w => w == inventory, out _), "Gameplay preset preserves owned-window UI pause/control exception");
        check(!Set("!Paused").WithWindowExceptions(true).Evaluate(ready, w => true, out _), "Explicit pause requirement stays literal despite owned-window exception");
        check(!Set("ControlsEnabled").WithWindowExceptions(true).Evaluate(ready, w => true, out _), "Explicit control requirement stays literal despite owned-window exception");
        check(!gameplay.Exclusivity(Set("Paused")) && !gameplay.Exclusivity(Set("ControlsDisabled")), "Gameplay UI exceptions prevent false pause/control exclusivity");
        ready.TextInput = true;
        check(!gameplay.Evaluate(ready, w => true, out var blocked) && blocked == "Requires Not typing", "Blocked reason identifies the unmet requirement");
        ready.UiReady = false;
        check(!Set("WindowOpen:Inventory").Evaluate(ready, null, out _) && !Set("WindowClosed:Inventory").Evaluate(ready, null, out _), "Unknown window stack fails positive and negative identities");
    }
}
