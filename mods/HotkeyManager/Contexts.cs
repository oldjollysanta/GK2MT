using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using LazyBearTechnology;
using TMPro;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace GK2.HotkeyManager
{
    public static class GameplayContexts
    {
        static readonly FieldInfo stack = AccessTools.Field(typeof(LazyWindowsStackController), "openedWindowsStack");
        static readonly FieldInfo guiElements = AccessTools.Field(typeof(LazyUI), "guiElementsDictionary");
        static readonly FieldInfo speechBubbles = AccessTools.Field(typeof(UISpeechBubble), "activeBubbles");
        // LazySingleton.Instance creates missing instances. Conditions must only observe the game.
        static readonly FieldInfo fightInstance = AccessTools.Field(typeof(LazySingleton<FightingGameController>), "instance");
        static MainGame observedGame;
        static bool observedLoaded;
        static FightingGameController observedFight;
        static float nextFightSearch;

        public static bool NoMenusOpen => IsAllowed(ActionContext.NoMenusOpen);
        public static bool GameplayOnly => IsAllowed(ActionContext.GameplayOnly);
        public static bool? InCombat => Capture().Combat;
        public static bool? DialogueOrCutscene => Capture().Dialogue;
        public static bool IsWindowOpen(string identity) => IsAllowed(ConditionSet.Parse(new[] { "WindowOpen:" + identity }));
        public static bool IsWindowActive(string identity) => IsAllowed(ConditionSet.Parse(new[] { "WindowActive:" + identity }));

        /// <summary>Unity main thread. AllowWindow exempts specified menus; explicit pause/control conditions remain literal.</summary>
        public static bool IsAllowed(ActionContext context, Func<object, bool> allowWindow = null)
        {
            var conditions = ConditionSet.ForContext(context, allowWindow != null);
            return !conditions.RequiresState || conditions.Evaluate(Capture(), allowWindow, out _);
        }

        public static bool IsAllowed(ConditionSet conditions, Func<object, bool> allowWindow = null)
        {
            if (conditions == null) throw new ArgumentNullException(nameof(conditions));
            return !conditions.RequiresState || conditions.Evaluate(Capture(), allowWindow, out _);
        }

        internal static ContextState Capture()
        {
            var state = new ContextState { Known = new HashSet<string>(StringComparer.Ordinal) };
            MainGame game = null;
            PlayerController player = null;
            try
            {
                game = MainGame.Instance;
                player = game != null ? MainGame.PlayerController : null;
                state.Loaded = game != null && game.gameState == MainGame.GameState.InGame && game.GameSave != null && MainGame.PlayerData != null;
                state.Known.Add("GameLoaded");
                if (game != null)
                {
                    state.Paused = MainGame.IsGamePaused;
                    state.Known.Add("Paused");
                    state.Controls = player != null && player.isActiveAndEnabled && player.IsControlsEnabled;
                    state.ControlsExceptUI = player != null && player.isActiveAndEnabled && player.IsControlsEnabledExcept(TakenControlType.ByUI);
                    state.Known.Add("ControlsEnabled");
                }
            }
            catch { state.Known.Clear(); }
            try
            {
                state.UiReady = LazyUI.IsInitialized && stack != null;
                if (state.UiReady)
                {
                    state.Windows = ((IEnumerable<LazyWidgetBase>)stack.GetValue(null)).Where(w => w != null).Cast<object>().ToArray();
                    state.ActiveWindow = LazyWindowsStackController.ActiveWindow;
                    state.WindowIdentities = state.Windows.ToDictionary(w => w, WindowIdentities);
                    state.ModalPause = LazyWindowsStackController.HasAnyModalWindowOpened;
                    state.Known.Add("AnyMenuOpen");
                    var selected = EventSystem.current?.currentSelectedGameObject;
                    state.TextInput = selected != null && ((selected.GetComponentInParent<InputField>()?.isFocused ?? false) ||
                        (selected.GetComponentInParent<TMP_InputField>()?.isFocused ?? false));
                    state.Known.Add("Typing");
                    var loading = Gui<UILoadingOverlay>();
                    if (loading != null) { state.Loading = loading.IsShown; state.Known.Add("Loading"); }
                }
            }
            catch { state.UiReady = false; state.Known.Remove("AnyMenuOpen"); state.Known.Remove("Typing"); }
            try
            {
                var fight = fightInstance?.GetValue(null) as FightingGameController;
                if (!ReferenceEquals(observedGame, game) || observedLoaded != state.Loaded)
                {
                    observedGame = game; observedLoaded = state.Loaded; observedFight = null; nextFightSearch = 0;
                }
                if (fight == null) fight = observedFight;
                if (fight == null && game != null && UnityEngine.Time.realtimeSinceStartup >= nextFightSearch)
                {
                    // The controller hides its base Awake, so its singleton cache may not yet be set.
                    // Cache this specific lookup; retry at most once per second while a scene is still initializing.
                    nextFightSearch = UnityEngine.Time.realtimeSinceStartup + 1;
                    observedFight = fight = UnityEngine.Object.FindObjectOfType<FightingGameController>(includeInactive: true);
                }
                if (fight != null) state.Combat = fight.CurrentFightState == FightState.ActiveFight;
            }
            catch { state.Combat = null; }
            try
            {
                var cinematic = state.UiReady ? Gui<UICinematic>() : null;
                var bubbles = speechBubbles?.GetValue(null) as IDictionary;
                if (state.UiReady && cinematic != null && bubbles != null)
                {
                    state.Dialogue = state.Windows.Any(w => w is UIDialogWindow || w is UIDialogInputWindow) ||
                        bubbles.Values.Cast<object>().OfType<UIDialogBubble>().Any(b => b != null && b.gameObject.activeInHierarchy) ||
                        cinematic.gameObject.activeInHierarchy ||
                        (player != null && !player.IsControlEnabledByType(TakenControlType.ByCinematics));
                }
            }
            catch { state.Dialogue = null; }
            return state;
        }

        static T Gui<T>() where T : class
        {
            var elements = guiElements?.GetValue(null) as IDictionary;
            return elements != null && elements.Contains(typeof(T)) ? elements[typeof(T)] as T : null;
        }

        static string[] WindowIdentities(object window)
        {
            var identities = new List<string> { window.GetType().FullName };
            if (window is CharacterWindow character)
            {
                if (character.LastOpenedPage == CharacterWindowData.CharPage.Main) identities.Add("Inventory");
                if (character.LastOpenedPage == CharacterWindowData.CharPage.Map) identities.Add("Map");
            }
            if (window is UIMultiInventoryWindow) identities.Add("Inventory");
            if (window is UIMapWindow) identities.Add("Map");
            if (window is UIBaseCraftWindow || window is UIBaseCraftSelectionWindow || window is UIResourceBasedCraftWindow || window is UISingleCraftWindow)
                identities.Add("Crafting");
            return identities.ToArray();
        }

        internal static bool Evaluate(ActionContext context, ContextState state, Func<object, bool> allowWindow = null)
            => ConditionSet.ForContext(context, allowWindow != null).Evaluate(state, allowWindow, out _);
    }

    internal sealed class ContextState
    {
        // Known == null keeps hand-built legacy test snapshots explicit and deterministic.
        internal HashSet<string> Known;
        internal bool UiReady, Loaded, Controls, ControlsExceptUI, Paused, ModalPause, Loading, TextInput;
        internal bool? Combat, Dialogue;
        internal object[] Windows = new object[0];
        internal object ActiveWindow;
        internal Dictionary<object, string[]> WindowIdentities = new Dictionary<object, string[]>();

        internal bool? Value(string name)
        {
            if (name == "InCombat") return Combat;
            if (name == "DialogueOrCutscene") return Dialogue;
            if (Known != null && !Known.Contains(name)) return null;
            switch (name)
            {
                case "GameLoaded": return Loaded;
                case "Loading": return Loading;
                case "Paused": return Paused;
                case "Typing": return UiReady ? TextInput : (bool?)null;
                case "AnyMenuOpen": return UiReady ? (Windows?.Length ?? 0) != 0 : (bool?)null;
                case "ControlsEnabled": return Controls;
                default: return null;
            }
        }

        internal bool? WindowValue(string identity, bool active)
        {
            if (!UiReady || (Known != null && !Known.Contains("AnyMenuOpen"))) return null;
            if (active) return ActiveWindow != null && Matches(ActiveWindow, identity);
            return (Windows ?? new object[0]).Any(w => Matches(w, identity));
        }
        bool Matches(object window, string identity) => WindowIdentities.TryGetValue(window, out var identities) ?
            identities.Contains(identity) : window.GetType().FullName == identity;
    }
}
