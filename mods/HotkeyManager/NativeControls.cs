using System;
using System.Collections.Generic;
using System.Linq;
using BepInEx.Configuration;
using HarmonyLib;
using LazyBearTechnology;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using Object = UnityEngine.Object;

namespace GK2.HotkeyManager
{
    // Reuse the game's row and button prefabs, scroll views, and controller navigation.
    internal sealed class NativeControls : IDisposable
    {
        internal readonly UIGameBindingSettingsWindow Window;
        internal readonly List<ControlRow> Rows = new List<ControlRow>();
        internal bool IsCapturing => capture != null;
        internal string Filter = "Both";
        readonly Plugin manager;
        readonly List<GameObject> added = new List<GameObject>();
        readonly ScrollRect keyboardScroll, padScroll;
        readonly Vector2 keyboardMin, keyboardMax, padMin, padMax;
        readonly TextMeshProUGUI status, tooltip;
        readonly LazyButton triggerButton, clearButton, suggestButton;
        readonly Dictionary<string, LazyButton> filters = new Dictionary<string, LazyButton>();
        readonly RectTransform tooltipRect;
        readonly RectTransform frame;
        readonly List<Action> restoreLayout = new List<Action>();
        readonly HashSet<UnityEngine.Object> savedLayout = new HashSet<UnityEngine.Object>();
        const float panelWidth = 478, actionWidth = 172, keyWidth = 140, conditionWidth = 120;
        readonly RectTransform padContainer;
        readonly Vector2 padContainerSize;
        readonly LayoutElement padContainerLayout;
        readonly float padContainerMin, padContainerPreferred;
        ControlRow selected, capture;
        ChordCapture recorder;
        bool pending;
        float cancelHeldSince = -1;
        static readonly KeyCode[] captureKeys = Enum.GetValues(typeof(KeyCode)).Cast<KeyCode>().Distinct()
            .Where(k => k != KeyCode.None && k != KeyCode.Escape && k != KeyCode.Mouse0 && k < KeyCode.JoystickButton0).ToArray();

        internal static T Field<T>(object target, string name) => (T)AccessTools.Field(target.GetType(), name).GetValue(target);
        internal NativeControls(Plugin plugin, UIGameBindingSettingsWindow window)
        {
            manager = plugin; Window = window;
            frame = (RectTransform)window.transform.Find("GenericWIndowLayout");
            manager.EnsureNativeKeys(); manager.Discover();
            keyboardScroll = Field<ScrollRect>(window, "scrollRect"); padScroll = Field<ScrollRect>(window, "scrollRectGamepad");
            padContainer = (RectTransform)padScroll.transform.parent; padContainerSize = padContainer.sizeDelta;
            padContainerLayout = padContainer.GetComponent<LayoutElement>() ?? padContainer.gameObject.AddComponent<LayoutElement>();
            padContainerMin = padContainerLayout.minHeight; padContainerPreferred = padContainerLayout.preferredHeight;
            float height = Mathf.Max(((RectTransform)keyboardScroll.transform.parent).rect.height, padContainer.rect.height);
            padContainerLayout.minHeight = padContainerLayout.preferredHeight = height;
            padContainer.SetSizeWithCurrentAnchors(RectTransform.Axis.Vertical, height);
            var keyboardRect = (RectTransform)keyboardScroll.transform;
            var padRect = (RectTransform)padScroll.transform;
            keyboardMin = keyboardRect.offsetMin; keyboardMax = keyboardRect.offsetMax;
            padMin = padRect.offsetMin; padMax = padRect.offsetMax;
            WidenPanel();
            keyboardRect.offsetMax -= new Vector2(0, 40); padRect.offsetMax -= new Vector2(0, 40);
            keyboardRect.offsetMin += new Vector2(0, 40); padRect.offsetMin += new Vector2(0, 40);
            var native = Field<Dictionary<GameKey, UIGameBindingElement>>(window, "cachedBindingElements");
            var template = native.Values.First();
            // Clone the untouched row before adding the extra column to pooled game rows.
            var modRows = new List<KeyValuePair<UIGameBindingElement, Binding>>();
            foreach (var binding in manager.Rows)
            {
                var element = Object.Instantiate(template, binding.IsPad ? padScroll.content : keyboardScroll.content, false);
                element.name = "HotkeyManager_" + binding.Id + "_" + binding.Label;
                element.enabled = false; added.Add(element.gameObject);
                Field<LayoutElement>(element, "layoutElement").minHeight = 30;
                Field<TextMeshProUGUI>(element, "actionLabel").text = binding.Mod.Replace("GK2 ", "") + "\n" + binding.Label;
                modRows.Add(new KeyValuePair<UIGameBindingElement, Binding>(element, binding));
            }
            foreach (var pair in native)
            {
                NativeKey key;
                if (!manager.NativeKeys.TryGetValue(pair.Key.value, out key)) continue;
                if (!key.Enabled.Value) key.Shortcut.Value = new KeyboardShortcut(key.Source.keyCode, key.Source.additionalKeyCodes ?? new KeyCode[0]);
                bool custom = GameKeyName(pair.Key) == "Custom GameKey";
                var row = new Binding { Id = custom ? "native.custom" : "game", Mod = custom ? "Custom control" : "Game", Entry = key.Shortcut, Kind = BindingKind.Keyboard,
                    TriggerEntry = key.Trigger, ActivationEntry = key.Enabled, Title = Field<TextMeshProUGUI>(pair.Value, "actionLabel").text,
                    Details = "Game action: " + GameKeyName(pair.Key) + " (" + pair.Key.value + ")\nKeyboardController → LazyInput.GetKey / GetKeyDown\n" + key.Source.localeId };
                row.Revert();
                AddRow(pair.Value, row, custom, Field<LazyButton>(pair.Value, "button").gameObject.activeSelf);
            }
            var padKeys = Field<List<GameKey>>(window, "gamepadBindings");
            var nativePad = Field<List<UIGameBindingElement>>(window, "gamepadBindingsList");
            for (int i = 0; i < nativePad.Count; i++)
                AddRow(nativePad[i], null, GameKeyName(padKeys[i]) == "Custom GameKey", false, "Game action: " + GameKeyName(padKeys[i]) + "\nThe game's controller layout is displayed here; mod controller shortcuts below can be rebound.");
            foreach (var pair in modRows) AddRow(pair.Key, pair.Value, true, true);
            var buttonTemplate = Field<LazyButton>(window, "okBtn");
            float width = frame.rect.width;
            int n = 0;
            foreach (string filter in new[] { "Both", "Game", "Modded" })
            {
                string choice = filter;
                filters.Add(filter, MakeButton(buttonTemplate, filter, new Vector2((n++ - 1) * 78, -44), true, 74, () => SetFilter(choice)));
            }
            MakeColumnHeader(Field<TextMeshProUGUI>(template, "actionLabel"), "Action", -130, actionWidth);
            MakeColumnHeader(Field<TextMeshProUGUI>(template, "actionLabel"), "Binding", 26, keyWidth);
            MakeColumnHeader(Field<TextMeshProUGUI>(template, "actionLabel"), "Conditions", 156, conditionWidth);
            triggerButton = MakeButton(buttonTemplate, "Trigger", new Vector2(-85, 54), false, 80, ChangeTrigger);
            suggestButton = MakeButton(buttonTemplate, "Suggest", new Vector2(0, 54), false, 80, Suggest);
            clearButton = MakeButton(buttonTemplate, "Clear", new Vector2(85, 54), false, 80, Clear);
            status = MakeText(Field<TextMeshProUGUI>(template, "actionLabel"), frame, "HotkeyManager_Status");
            Place(status.rectTransform, new Vector2(0, 74), false, width - 20, 18);
            status.alignment = TextAlignmentOptions.Center; status.fontSize = 8; status.enableAutoSizing = false;
            status.text = "Select a binding. Hover or focus a row for details.";
            tooltipRect = new GameObject("HotkeyManager_Tooltip", typeof(RectTransform), typeof(Image)).GetComponent<RectTransform>();
            tooltipRect.SetParent(frame, false); added.Add(tooltipRect.gameObject);
            tooltipRect.gameObject.AddComponent<LayoutElement>().ignoreLayout = true;
            tooltipRect.anchorMin = tooltipRect.anchorMax = new Vector2(.5f, .5f); tooltipRect.pivot = new Vector2(0, 1);
            var image = tooltipRect.GetComponent<Image>(); image.color = new Color(.075f, .08f, .11f, .98f); image.raycastTarget = false;
            tooltip = MakeText(Field<TextMeshProUGUI>(template, "actionLabel"), tooltipRect, "Description");
            tooltip.rectTransform.anchorMin = Vector2.zero; tooltip.rectTransform.anchorMax = Vector2.one;
            tooltip.rectTransform.offsetMin = new Vector2(7, 7); tooltip.rectTransform.offsetMax = new Vector2(-7, -7);
            tooltip.fontSize = 11; tooltip.enableAutoSizing = false; tooltip.alignment = TextAlignmentOptions.TopLeft;
            tooltip.textWrappingMode = TextWrappingModes.Normal; tooltip.overflowMode = TextOverflowModes.Overflow;
            tooltip.richText = false; tooltip.gameObject.SetActive(true); tooltipRect.gameObject.SetActive(false);
            SetFilter("Both");
            RefreshTools();
            RefreshConflicts();
        }
        internal static string GameKeyName(GameKey key) => typeof(GameKey).GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static)
            .Where(f => f.FieldType == typeof(GameKey) && ((GameKey)f.GetValue(null)).value == key.value).Select(f => f.Name).FirstOrDefault() ?? "Custom GameKey";
        void WidenPanel()
        {
            SetWidth(frame, panelWidth);
            var containers = new HashSet<RectTransform>();
            foreach (var scroll in new[] { keyboardScroll, padScroll })
            {
                var container = (RectTransform)scroll.transform.parent;
                containers.Add(container); containers.Add((RectTransform)container.parent);
                SaveRect(scroll.content);
                // Content width follows the viewport; row height remains owned by the game.
                scroll.content.anchorMin = new Vector2(0, scroll.content.anchorMin.y);
                scroll.content.anchorMax = new Vector2(1, scroll.content.anchorMax.y);
                scroll.content.sizeDelta = new Vector2(0, scroll.content.sizeDelta.y);
            }
            foreach (var container in containers.Where(r => r != frame && r != Window.transform))
            {
                SetWidth(container, container.parent == frame ? panelWidth - 48 : panelWidth - 36);
                container.anchorMin = new Vector2(.5f, container.anchorMin.y);
                container.anchorMax = new Vector2(.5f, container.anchorMax.y);
                container.anchoredPosition = new Vector2(0, container.anchoredPosition.y);
            }
        }
        void SaveRect(RectTransform rect)
        {
            if (!savedLayout.Add(rect)) return;
            restoreLayout.Add(RectPlacement(rect));
        }
        static Action RectPlacement(RectTransform rect)
        {
            Vector2 min = rect.anchorMin, max = rect.anchorMax, pivot = rect.pivot, size = rect.sizeDelta, position = rect.anchoredPosition;
            return () => { if (rect != null) { rect.anchorMin = min; rect.anchorMax = max; rect.pivot = pivot; rect.sizeDelta = size; rect.anchoredPosition = position; } };
        }
        void SetWidth(RectTransform rect, float width)
        {
            SaveRect(rect);
            var layout = rect.GetComponent<LayoutElement>();
            if (layout != null)
            {
                if (savedLayout.Add(layout))
                {
                    float min = layout.minWidth, preferred = layout.preferredWidth, flexible = layout.flexibleWidth;
                    restoreLayout.Add(() => { if (layout != null) { layout.minWidth = min; layout.preferredWidth = preferred; layout.flexibleWidth = flexible; } });
                }
                layout.minWidth = layout.preferredWidth = width; layout.flexibleWidth = 0;
            }
            var fitter = rect.GetComponent<ContentSizeFitter>();
            if (fitter != null)
            {
                if (savedLayout.Add(fitter))
                {
                    var fit = fitter.horizontalFit;
                    restoreLayout.Add(() => { if (fitter != null) fitter.horizontalFit = fit; });
                }
                fitter.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            }
            rect.SetSizeWithCurrentAnchors(RectTransform.Axis.Horizontal, width);
        }
        void StretchCell(RectTransform rect, float left, float right)
        {
            SaveRect(rect);
            foreach (var component in rect.GetComponents<Behaviour>().Where(c => c is LayoutGroup || c is ContentSizeFitter))
            {
                if (savedLayout.Add(component))
                {
                    bool enabled = component.enabled;
                    restoreLayout.Add(() => { if (component != null) component.enabled = enabled; });
                }
                component.enabled = false;
            }
            PlaceCell(rect, left, right);
        }
        static void PlaceCell(RectTransform rect, float left, float right)
        {
            rect.anchorMin = new Vector2(left, 0); rect.anchorMax = new Vector2(right, 1);
            rect.offsetMin = rect.offsetMax = Vector2.zero;
        }
        void MakeColumnHeader(TextMeshProUGUI template, string title, float x, float width)
        {
            var text = MakeText(template, frame, "HotkeyManager_Header_" + title);
            Place(text.rectTransform, new Vector2(x, -65), true, width - 6, 14);
            text.text = title; text.fontSize = 9; text.enableAutoSizing = false;
            text.alignment = TextAlignmentOptions.Center;
        }
        void LayoutColumns(ControlRow row)
        {
            const float width = actionWidth + keyWidth + conditionWidth;
            var rect = (RectTransform)row.Element.transform;
            SetWidth(rect, width);
            var horizontal = rect.GetComponent<HorizontalLayoutGroup>();
            if (horizontal != null)
            {
                bool enabled = horizontal.enabled;
                restoreLayout.Add(() => { if (horizontal != null) horizontal.enabled = enabled; });
                horizontal.enabled = false;
            }
            var left = (RectTransform)row.Action.transform.parent;
            var right = (RectTransform)row.Key.transform.parent;
            StretchCell(left, 0, actionWidth / width);
            StretchCell(right, actionWidth / width, (actionWidth + keyWidth) / width);
            StretchCell(row.Action.rectTransform, 0, 1); StretchCell(row.Key.rectTransform, 0, 1);
            row.Action.rectTransform.offsetMin = new Vector2(3, 2); row.Action.rectTransform.offsetMax = new Vector2(-6, -2);
            row.Key.rectTransform.offsetMin = new Vector2(3, 2); row.Key.rectTransform.offsetMax = new Vector2(row.Editable ? -27 : -3, -2);
            row.Key.alignment = TextAlignmentOptions.Center;
            var cell = new GameObject("HotkeyManager_ConditionCell", typeof(RectTransform), typeof(Image), typeof(LayoutElement)).GetComponent<RectTransform>();
            cell.SetParent(rect, false); added.Add(cell.gameObject);
            cell.GetComponent<LayoutElement>().ignoreLayout = true;
            var skin = right.GetComponent<Image>() ?? left.GetComponent<Image>();
            if (skin != null)
            {
                var image = cell.GetComponent<Image>();
                image.sprite = skin.sprite; image.type = skin.type; image.color = skin.color;
                image.material = skin.material; image.raycastTarget = false;
            }
            StretchCell(cell, (actionWidth + keyWidth) / width, 1);
            row.Conditions = MakeText(row.Action, cell, "HotkeyManager_Conditions");
            row.Conditions.rectTransform.anchorMin = Vector2.zero; row.Conditions.rectTransform.anchorMax = Vector2.one;
            row.Conditions.rectTransform.offsetMin = new Vector2(4, 2); row.Conditions.rectTransform.offsetMax = new Vector2(-4, -2);
            row.Conditions.fontSize = 10; row.Conditions.enableAutoSizing = true; row.Conditions.fontSizeMin = 8; row.Conditions.fontSizeMax = 10;
            row.Conditions.alignment = TextAlignmentOptions.Center; row.Conditions.textWrappingMode = TextWrappingModes.Normal;
            row.Conditions.overflowMode = TextOverflowModes.Ellipsis; row.Conditions.richText = false;
            row.Conditions.text = row.Modded ? (string.IsNullOrEmpty(row.Binding?.ConditionTags) ? "Not exposed" : row.Binding.ConditionTags) : "Game";
            row.ConditionDetails = row.Modded ? row.Binding?.ConditionDetails ?? "This mod has not declared its gameplay conditions. Its own input code decides when the action is available."
                : "Availability is controlled by the game's own action logic.";
            row.Conditions.color = row.Modded && row.Conditions.text != "Not exposed" ? new Color(.68f, .83f, .9f) : new Color(.65f, .65f, .7f);
            row.Conditions.raycastTarget = true;
            var hover = row.Conditions.gameObject.GetComponent<ControlHover>() ?? row.Conditions.gameObject.AddComponent<ControlHover>();
            hover.Controls = this; hover.Row = row;
        }
        internal void RepairColumns()
        {
            // The native RefreshContentFitter invokes even disabled LayoutGroups on a device switch.
            // Reapply only our row geometry after that call; keep the game's scroll layout and sizing.
            const float width = actionWidth + keyWidth + conditionWidth;
            foreach (var row in Rows)
            {
                PlaceCell((RectTransform)row.Action.transform.parent, 0, actionWidth / width);
                PlaceCell((RectTransform)row.Key.transform.parent, actionWidth / width, (actionWidth + keyWidth) / width);
                PlaceCell((RectTransform)row.Conditions.transform.parent, (actionWidth + keyWidth) / width, 1);
                PlaceCell(row.Action.rectTransform, 0, 1); PlaceCell(row.Key.rectTransform, 0, 1); PlaceCell(row.Conditions.rectTransform, 0, 1);
                row.Action.rectTransform.offsetMin = new Vector2(3, 2); row.Action.rectTransform.offsetMax = new Vector2(-6, -2);
                row.Key.rectTransform.offsetMin = new Vector2(3, 2); row.Key.rectTransform.offsetMax = new Vector2(row.Editable ? -27 : -3, -2);
                row.Conditions.rectTransform.offsetMin = new Vector2(4, 2); row.Conditions.rectTransform.offsetMax = new Vector2(-4, -2);
                row.PlaceButton(); row.PlaceRecord();
            }
        }
        void SaveText(TextMeshProUGUI text)
        {
            if (!savedLayout.Add(text)) return;
            bool auto = text.enableAutoSizing, raycast = text.raycastTarget;
            float size = text.fontSize, min = text.fontSizeMin, max = text.fontSizeMax;
            var wrap = text.textWrappingMode; var overflow = text.overflowMode; var alignment = text.alignment; var color = text.color;
            restoreLayout.Add(() =>
            {
                if (text == null) return;
                text.fontSize = size; text.fontSizeMin = min; text.fontSizeMax = max; text.enableAutoSizing = auto;
                text.textWrappingMode = wrap; text.overflowMode = overflow; text.alignment = alignment; text.raycastTarget = raycast; text.color = color;
            });
            foreach (var component in text.GetComponents<MonoBehaviour>().Where(c => !(c is TextMeshProUGUI) && !(c is ControlHover)))
                if (savedLayout.Add(component))
                {
                    bool enabled = component.enabled;
                    restoreLayout.Add(() => { if (component != null) component.enabled = enabled; });
                }
        }
        ControlRow AddRow(UIGameBindingElement element, Binding binding, bool modded, bool editable, string details = null)
        {
            var row = new ControlRow { Element = element, Binding = binding, Modded = modded, Editable = editable,
                Action = Field<TextMeshProUGUI>(element, "actionLabel"), Key = Field<TextMeshProUGUI>(element, "keyLabel"),
                Button = Field<LazyButton>(element, "button"), Record = Field<GameObject>(element, "recordObject"), Details = details ?? binding.Details };
            row.KeyColor = row.Key.color;
            SaveText(row.Action); SaveText(row.Key);
            SaveRect(row.Action.rectTransform); SaveRect(row.Key.rectTransform);
            SaveRect((RectTransform)row.Button.transform); SaveRect((RectTransform)row.Record.transform);
            row.PlaceButton = RectPlacement((RectTransform)row.Button.transform); row.PlaceRecord = RectPlacement((RectTransform)row.Record.transform);
            if (modded) { FreezeText(row.Action); FreezeText(row.Key); }
            row.Action.textWrappingMode = TextWrappingModes.NoWrap;
            row.Action.enableAutoSizing = true; row.Action.fontSizeMin = 9; row.Action.fontSizeMax = row.Action.fontSize;
            if (modded)
            {
                var fitter = row.Action.GetComponent<ContentSizeFitter>(); if (fitter != null) fitter.enabled = false;
                var layout = row.Action.GetComponent<LayoutElement>() ?? row.Action.gameObject.AddComponent<LayoutElement>(); layout.ignoreLayout = true;
                row.Action.rectTransform.anchorMin = Vector2.zero; row.Action.rectTransform.anchorMax = Vector2.one;
                row.Action.rectTransform.offsetMin = new Vector2(3, 2); row.Action.rectTransform.offsetMax = new Vector2(-6, -2);
                row.Action.overflowMode = TextOverflowModes.Ellipsis;
                row.Action.alignment = TextAlignmentOptions.MidlineRight;
            }
            row.Key.textWrappingMode = TextWrappingModes.NoWrap;
            row.Key.enableAutoSizing = true; row.Key.fontSizeMin = 7; row.Key.fontSizeMax = row.Key.fontSize;
            LayoutColumns(row);
            row.Action.raycastTarget = true;
            var hover = row.Action.gameObject.GetComponent<ControlHover>() ?? row.Action.gameObject.AddComponent<ControlHover>();
            hover.Controls = this; hover.Row = row;
            row.Button.onClick.RemoveAllListeners();
            if (editable) row.Button.onClick.AddListener(() => BeginCapture(row));
            var own = row.Element.GetComponent<ControlNavigation>();
            if (editable && !row.Element.GetComponentsInChildren<GamepadNavigationItem>(true).Any(n => !(n is ControlNavigation)))
            {
                var source = Field<LazyButton>(Window, "okBtn").GetComponentInChildren<GamepadNavigationItem>(true);
                var nav = own ?? row.Element.gameObject.AddComponent<ControlNavigation>(); nav.group = source.group; nav.Active = true;
                nav.FocusRectTransform = (RectTransform)row.Element.transform;
                AccessTools.Field(typeof(GamepadNavigationItem), "configuredForButton").SetValue(nav, row.Button);
                if (source.focusFrame != null && nav.focusFrame == null)
                {
                    nav.focusFrame = Object.Instantiate(source.focusFrame, row.Element.transform, false);
                    var rect = (RectTransform)nav.focusFrame.transform; rect.anchorMin = Vector2.zero; rect.anchorMax = Vector2.one; rect.offsetMin = rect.offsetMax = Vector2.zero;
                    foreach (var graphic in nav.focusFrame.GetComponentsInChildren<Graphic>(true)) graphic.raycastTarget = false;
                    nav.focusFrame.SetActive(false);
                }
                row.OwnNavigation = nav;
            }
            else if (own != null) own.Active = false;
            foreach (var item in row.Element.GetComponentsInChildren<GamepadNavigationItem>(true))
            {
                if (item is ControlNavigation) item.OnFocus.RemoveAllListeners();
                if (editable)
                {
                    var field = AccessTools.Field(typeof(GamepadNavigationItem), "onSelect");
                    object previous = field.GetValue(item);
                    restoreLayout.Add(() => { if (item != null) field.SetValue(item, previous); });
                    field.SetValue(item, new UnityEngine.Events.UnityEvent()); item.OnSelect.AddListener(() => row.Button.onClick.Invoke());
                }
                UnityEngine.Events.UnityAction focused = () => { if (!IsCapturing) ShowTooltip(row); };
                item.OnFocus.AddListener(focused);
                restoreLayout.Add(() => { if (item != null) item.OnFocus.RemoveListener(focused); });
            }
            row.Button.gameObject.SetActive(editable); row.Record.SetActive(false);
            Rows.Add(row); RefreshLabel(row); return row;
        }
        LazyButton MakeButton(LazyButton template, string text, Vector2 position, bool top, float width, Action click)
        {
            var button = Object.Instantiate(template, frame, false); added.Add(button.gameObject);
            button.name = "HotkeyManager_" + text; button.onClick.RemoveAllListeners(); button.onClick.AddListener(() => click());
            AccessTools.Field(typeof(LazyButton), "textTransitions").SetValue(button, new List<TextTransition>());
            foreach (var label in button.GetComponentsInChildren<TextMeshProUGUI>(true)) FreezeText(label);
            foreach (var item in button.GetComponentsInChildren<GamepadNavigationItem>(true))
            {
                AccessTools.Field(typeof(GamepadNavigationItem), "onSelect").SetValue(item, new UnityEngine.Events.UnityEvent()); item.OnSelect.AddListener(() => { if (button.interactable) button.onClick.Invoke(); });
                item.OnFocus.AddListener(HideTooltip);
            }
            var layout = button.GetComponent<LayoutElement>() ?? button.gameObject.AddComponent<LayoutElement>(); layout.ignoreLayout = true;
            Place((RectTransform)button.transform, position, top, width, 18);
            SetButtonText(button, text); button.gameObject.SetActive(true); return button;
        }
        static void SetButtonText(LazyButton button, string text)
        {
            var label = button.GetComponentInChildren<TextMeshProUGUI>(true);
            label.textWrappingMode = TextWrappingModes.NoWrap;
            var fitter = label.GetComponent<ContentSizeFitter>(); if (fitter != null) fitter.enabled = false;
            var layout = label.GetComponent<LayoutElement>() ?? label.gameObject.AddComponent<LayoutElement>(); layout.ignoreLayout = true;
            label.rectTransform.anchorMin = Vector2.zero; label.rectTransform.anchorMax = Vector2.one;
            label.rectTransform.offsetMin = new Vector2(3, 1); label.rectTransform.offsetMax = new Vector2(-3, -1);
            label.alignment = TextAlignmentOptions.Center;
            label.text = text; label.fontSize = 10; label.enableAutoSizing = true; label.fontSizeMin = 7; label.fontSizeMax = 10;
        }
        TextMeshProUGUI MakeText(TextMeshProUGUI template, Transform parent, string name)
        {
            var text = Object.Instantiate(template, parent, false); added.Add(text.gameObject); text.name = name;
            FreezeText(text);
            var layout = text.GetComponent<LayoutElement>() ?? text.gameObject.AddComponent<LayoutElement>(); layout.ignoreLayout = true;
            var fitter = text.GetComponent<ContentSizeFitter>(); if (fitter != null) fitter.enabled = false;
            text.raycastTarget = false; text.gameObject.SetActive(true); return text;
        }
        static void FreezeText(TextMeshProUGUI text)
        {
            foreach (var component in text.GetComponents<MonoBehaviour>()) if (!(component is TextMeshProUGUI) && !(component is ControlHover)) component.enabled = false;
        }
        static void Place(RectTransform rect, Vector2 position, bool top, float width, float height)
        {
            rect.anchorMin = rect.anchorMax = new Vector2(.5f, top ? 1 : 0); rect.pivot = new Vector2(.5f, .5f);
            rect.sizeDelta = new Vector2(width, height); rect.anchoredPosition = position;
        }
        internal void SetFilter(string filter)
        {
            if (IsCapturing || !filters.ContainsKey(filter)) return;
            Filter = filter;
            foreach (var row in Rows) row.Element.gameObject.SetActive(filter == "Both" || row.Modded == (filter == "Modded"));
            foreach (var pair in filters) SetButtonText(pair.Value, pair.Key == filter ? "[" + pair.Key + "]" : pair.Key);
            HideTooltip(); Rebuild();
        }
        internal void Rebuild()
        {
            RepairColumns();
            LayoutRebuilder.ForceRebuildLayoutImmediate(keyboardScroll.content); LayoutRebuilder.ForceRebuildLayoutImmediate(padScroll.content);
            keyboardScroll.verticalNormalizedPosition = padScroll.verticalNormalizedPosition = 1;
            Window.GetComponentInChildren<GamepadNavigationController>(true)?.ReinitItems(LazyInput.IsGamepadActive);
        }
        internal void ShowTooltip(ControlRow row)
        {
            selected = row; RefreshTools();
            var overlaps = row.Binding == null ? new string[0] : Rows.Where(r => r.Binding != null && r != row && Rules.Conflict(row.Binding, r.Binding) != "")
                .Select(r => Rules.ConflictWarning(row.Binding, r.Binding) + " — " + r.Binding.Mod + " / " + r.Binding.Label).ToArray();
            string details = row.Details ?? "";
            string conditions = details.Contains(row.ConditionDetails) ? "" : "\nConditions: " + row.Conditions.text + "\n" + row.ConditionDetails;
            tooltip.text = row.Action.text + "\n" + details + conditions
                + (row.Binding == null ? "" : "\nBinding value: " + row.Binding.Combined) + (overlaps.Length == 0 ? "" : "\n" + string.Join("\n", overlaps));
            tooltipRect.sizeDelta = new Vector2(316, Mathf.Clamp(tooltip.GetPreferredValues(tooltip.text, 302, 1000).y + 14, 35, 450));
            tooltipRect.anchoredPosition = new Vector2(frame.rect.width / 2 + 8, 120);
            tooltipRect.SetAsLastSibling(); tooltipRect.gameObject.SetActive(true);
        }
        internal void HideTooltip() => tooltipRect?.gameObject.SetActive(false);
        void RefreshTools()
        {
            SetButtonText(triggerButton, pending ? "Keep conflict" : "Trigger: " + (selected?.Binding?.TriggerDraft ?? "Mod"));
            SetButtonText(clearButton, IsCapturing ? "Cancel" : "Clear");
            triggerButton.interactable = pending || (!IsCapturing && selected?.Editable == true && selected.Binding.TriggerEntry != null);
            clearButton.interactable = IsCapturing || selected?.Editable == true;
            suggestButton.interactable = !IsCapturing && selected?.Editable == true;
        }
        void RefreshLabel(ControlRow row)
        {
            if (row.Binding == null) return;
            row.Key.text = Friendly(row.Binding);
        }
        void RefreshConflicts()
        {
            var conflicts = Rules.Conflicts(Rows.Where(r => r.Binding != null).Select(r => r.Binding).ToList()).ToArray();
            foreach (var row in Rows.Where(r => r.Binding != null)) row.Key.color = conflicts.Any(c => c.Item1 == row.Binding || c.Item2 == row.Binding) ? new Color(1, .55f, .25f) : row.KeyColor;
            status.text = conflicts.Length == 0 ? "Select a binding. Hover or focus a row for details." : conflicts.Length + " overlaps highlighted. Hover or focus rows for details.";
        }
        static string Friendly(Binding binding)
        {
            string text = binding.Combined;
            if (!binding.IsPad) return text.Replace("LeftControl", "Ctrl").Replace("RightControl", "RCtrl")
                .Replace("LeftShift", "Shift").Replace("RightShift", "RShift").Replace("LeftAlt", "Alt").Replace("RightAlt", "RAlt");
            if (Rules.IsNone(text)) return "None";
            if (binding.Kind == BindingKind.PadIndex) return string.Join("+", Rules.Tokens(binding).Select(t => t.Replace("pad:", "")));
            return string.Join("+", text.Split('+').Select(t => { string physical; return (binding.Kind == BindingKind.GameAction ? Rules.ActionButtons : Rules.PadNames).TryGetValue(t.Trim(), out physical) ? physical : t; }));
        }
        internal void BeginCapture(ControlRow row)
        {
            if (IsCapturing || !row.Editable) return;
            selected = capture = row; recorder = new ChordCapture(); pending = false; cancelHeldSince = -1;
            Lock(true); HideTooltip(); row.Record.SetActive(true); row.Key.text = "...";
            status.text = "Hold a combination, then release. Esc / hold B cancels."; RefreshTools();
        }
        void Lock(bool locked)
        {
            AccessTools.Method(typeof(UIGameBindingSettingsWindow), "SetLockOnBindingButtons").Invoke(Window, new object[] { locked });
            foreach (var row in Rows.Where(r => r.Editable)) row.Button.interactable = !locked;
            foreach (var button in filters.Values) button.interactable = !locked;
        }
        internal void Tick()
        {
            if (!Window.IsShown) { if (IsCapturing) Cancel(); HideTooltip(); return; }
            if (capture == null) return;
            if (!Application.isFocused) { Cancel(); status.text = "Capture cancelled when the game lost focus."; return; }
            if (Input.GetKeyDown(KeyCode.Escape)) { Cancel(); return; }
            if (!pending)
            {
                if ((manager.Buttons & InputController.Mask("B")) == 0) cancelHeldSince = -1;
                else if (cancelHeldSince < 0) cancelHeldSince = Time.unscaledTime;
                else if (Time.unscaledTime - cancelHeldSince > 1.2f) { Cancel(); return; }
            }
            if (pending)
            {
                if (Input.GetKeyDown(KeyCode.Return) || PadDown("A")) Save();
                else if (PadDown("B")) Cancel();
                return;
            }
            string[] held = capture.Binding.IsPad ? InputController.Buttons(manager.Buttons) : captureKeys.Where(Input.GetKey).Select(k => k.ToString()).ToArray();
            string[] down = capture.Binding.IsPad ? InputController.Buttons(manager.Buttons & ~manager.PreviousButtons) : captureKeys.Where(Input.GetKeyDown).Select(k => k.ToString()).ToArray();
            bool neutral = held.Length == 0 && !Input.GetKey(KeyCode.Mouse0);
            if (!recorder.Sample(held, down, neutral)) return;
            try { CaptureDraft(capture.Binding, recorder.Peak, recorder.Main); Propose(); }
            catch (Exception ex) { Cancel(); status.text = ex.Message; }
        }
        bool PadDown(string button) => (manager.Buttons & InputController.Mask(button)) != 0 && (manager.PreviousButtons & InputController.Mask(button)) == 0;
        // Conversion respects each imported mod's own config type and capacity.
        internal static void CaptureDraft(Binding binding, string[] peak, string main)
        {
            if (peak.Length == 0) throw new ArgumentException("No controls captured.");
            main = peak.Contains(main) ? main : peak.Last();
            if (binding.Kind == BindingKind.Keyboard)
            {
                string nonModifier = peak.LastOrDefault(k => !k.Contains("Control") && !k.Contains("Shift") && !k.Contains("Alt") && !k.Contains("Command"));
                if (nonModifier != null && (main.Contains("Control") || main.Contains("Shift") || main.Contains("Alt"))) main = nonModifier;
                binding.Draft = string.Join("+", new[] { main }.Concat(peak.Where(p => p != main)));
            }
            else if (binding.Kind == BindingKind.Key)
            {
                if (peak.Length != 1) throw new ArgumentException("This mod accepts one keyboard key. Register with the API to support chords.");
                binding.Draft = main;
            }
            else if (binding.Kind == BindingKind.PadChord) binding.Draft = Chords.NormalizeController(string.Join("+", peak.Where(p => p != main).Concat(new[] { main })));
            else if (binding.Kind == BindingKind.GameAction)
            {
                var actions = new List<string>();
                foreach (string button in peak.Where(p => p != main).Concat(new[] { main }))
                {
                    string preferred = button == "RT" ? "RightTrigger" : button == "LT" ? "LeftTrigger" : button == "R3" ? "RightStick" : button == "L3" ? "LeftStick" : null;
                    string action = Rules.ActionButtons.Where(p => p.Value == button).Select(p => p.Key).OrderBy(n => n).FirstOrDefault();
                    if (preferred != null && Rules.ActionButtons.ContainsKey(preferred)) action = preferred;
                    if (action == null) throw new ArgumentException("This mod has no mapped game action for " + button + ".");
                    actions.Add(action);
                }
                binding.Draft = string.Join("+", actions);
            }
            else if (binding.Kind == BindingKind.PadEnum)
            {
                if (peak.Length > (binding.Secondary == null ? 1 : 2)) throw new ArgumentException("This mod accepts " + (binding.Secondary == null ? "one controller button." : "at most two controller buttons."));
                Func<string, string> convert = button => Enum.GetNames(binding.Entry.SettingType).FirstOrDefault(n => Rules.PadNames.ContainsKey(n) && Rules.PadNames[n] == button)
                    ?? throw new ArgumentException("This mod does not support " + button + ".");
                binding.Draft = convert(main);
                if (binding.Secondary != null) binding.SecondaryDraft = peak.Length == 1 ? "None" : convert(peak.First(p => p != main));
            }
            else
            {
                if (peak.Length != 1) throw new ArgumentException("This mod accepts one joystick button.");
                int index = Array.IndexOf(new[] { "A", "B", "X", "Y", "LB", "RB", "Back", "Start", "L3", "R3" }, main);
                if (index < 0) throw new ArgumentException("This mod's joystick index cannot represent " + main + ".");
                binding.Draft = index.ToString();
            }
            object value; string error;
            if (!binding.TryValue(binding.Draft, binding.Entry, out value, out error) ||
                (binding.Secondary != null && !binding.TryValue(binding.SecondaryDraft, binding.Secondary, out value, out error))) throw new ArgumentException(error);
        }
        internal void Propose()
        {
            var binding = capture.Binding;
            var conflicts = Rules.Conflicts(Rows.Where(r => r.Binding != null).Select(r => r.Binding).ToList()).Where(p => p.Item1 == binding || p.Item2 == binding).ToArray();
            capture.Record.SetActive(false); RefreshLabel(capture);
            if (conflicts.Length == 0) { Save(); return; }
            pending = true;
            var other = conflicts[0].Item1 == binding ? conflicts[0].Item2 : conflicts[0].Item1;
            status.text = (conflicts[0].Item3.StartsWith("Conflict:") ? "Conflict: " : "Possible conflict: ") + other.Mod + " / " + other.Label + ". Keep or cancel.";
            RefreshTools();
        }
        internal void Save()
        {
            try { manager.Apply(capture.Binding); Finish(); foreach (var row in Rows.Where(r => r.Binding != null)) { row.Binding.Revert(); RefreshLabel(row); } RefreshConflicts(); }
            catch (Exception ex) { manager.ReportError("Saving control", ex); Cancel(); status.text = "Could not save: " + ex.Message; }
        }
        internal void Cancel()
        {
            if (capture == null) return;
            capture.Binding.Revert(); Finish(); status.text = "Binding unchanged.";
        }
        void Finish()
        {
            capture.Record.SetActive(false); RefreshLabel(capture); capture = null; recorder = null; pending = false;
            manager.SuppressUntil = Time.unscaledTime + .2f; manager.WaitForNeutral = true; Lock(false); RefreshTools();
        }
        void ChangeTrigger()
        {
            if (pending) { Save(); return; }
            if (selected?.Editable != true || selected.Binding.TriggerEntry == null) return;
            capture = selected; Lock(true); selected.Binding.TriggerDraft = selected.Binding.TriggerDraft == "Press" ? "Release" : "Press"; Propose();
        }
        void Clear()
        {
            if (IsCapturing) { Cancel(); return; }
            if (selected?.Editable != true) return;
            capture = selected; Lock(true); selected.Binding.Draft = selected.Binding.Kind == BindingKind.PadIndex ? "-1" : "None";
            if (selected.Binding.Secondary != null) selected.Binding.SecondaryDraft = "None";
            Propose();
        }
        void Suggest()
        {
            if (selected?.Editable != true || IsCapturing) return;
            if (!Rules.Suggest(selected.Binding, Rows.Where(r => r.Binding != null).Select(r => r.Binding).ToList())) { status.text = "No free combination supported by this mod was found."; return; }
            capture = selected; Lock(true); Propose();
        }
        internal void ResetNative()
        {
            foreach (var key in manager.NativeKeys.Values)
            {
                key.Enabled.Value = false; key.ForgetOverride();
                key.Shortcut.Value = new KeyboardShortcut(key.Source.keyCode, key.Source.additionalKeyCodes ?? new KeyCode[0]); key.Trigger.Value = TriggerMode.Press;
            }
            foreach (var row in Rows.Where(r => r.Binding?.ActivationEntry != null)) { row.Binding.Revert(); RefreshLabel(row); }
            RefreshConflicts();
            status.text = "Game controls restored to defaults.";
        }
        internal void ReportStatus(string message) => status.text = message;
        public void Dispose()
        {
            Cancel();
            foreach (var row in Rows.Where(r => !added.Contains(r.Element.gameObject)))
            {
                row.Element.gameObject.SetActive(true);
                var hover = row.Action.GetComponent<ControlHover>(); if (hover != null) { hover.Controls = null; hover.Row = null; }
                if (row.OwnNavigation != null) { row.OwnNavigation.Active = false; row.OwnNavigation.OnFocus.RemoveAllListeners(); row.OwnNavigation.OnSelect.RemoveAllListeners(); }
            }
            foreach (var obj in added) if (obj != null) { obj.SetActive(false); obj.transform.SetParent(null, false); Object.Destroy(obj); }
            foreach (var restore in restoreLayout) restore();
            ((RectTransform)keyboardScroll.transform).offsetMin = keyboardMin; ((RectTransform)keyboardScroll.transform).offsetMax = keyboardMax;
            ((RectTransform)padScroll.transform).offsetMin = padMin; ((RectTransform)padScroll.transform).offsetMax = padMax;
            padContainer.sizeDelta = padContainerSize; padContainerLayout.minHeight = padContainerMin; padContainerLayout.preferredHeight = padContainerPreferred;
        }
    }
    [HarmonyPatch(typeof(UIGameBindingSettingsWindow), "UpdateGamepadDependentStuff")]
    internal static class NativeDevicePatch
    {
        static void Postfix()
        {
            var controls = Plugin.Instance?.Controls;
            if (controls == null) return;
            if (Plugin.Instance.Capturing) controls.RepairColumns(); else controls.Rebuild();
        }
    }
    internal sealed class ControlRow
    {
        internal UIGameBindingElement Element;
        internal Binding Binding;
        internal bool Modded, Editable;
        internal TextMeshProUGUI Action, Key, Conditions;
        internal LazyButton Button;
        internal GameObject Record;
        internal string Details, ConditionDetails;
        internal Action PlaceButton, PlaceRecord;
        internal Color KeyColor;
        internal GamepadNavigationItem OwnNavigation;
    }
    public sealed class ControlHover : MonoBehaviour, IPointerEnterHandler, IPointerExitHandler
    {
        internal NativeControls Controls;
        internal ControlRow Row;
        public void OnPointerEnter(PointerEventData data) { if (Controls != null && Row != null && !Controls.IsCapturing) Controls.ShowTooltip(Row); }
        public void OnPointerExit(PointerEventData data) => Controls?.HideTooltip();
    }
    public sealed class ControlNavigation : GamepadNavigationItem { }
    [HarmonyPatch(typeof(UIGameBindingSettingsWindow), nameof(UIGameBindingSettingsWindow.Redraw))]
    internal static class NativePanelPatch
    {
        static void Prefix() { Plugin.Instance.Controls?.Dispose(); Plugin.Instance.Controls = null; }
        [HarmonyPriority(Priority.Last)]
        static void Postfix(UIGameBindingSettingsWindow __instance)
        {
            try { Plugin.Instance.Controls = new NativeControls(Plugin.Instance, __instance); }
            catch (Exception ex) { Plugin.Instance.ReportError("Native Controls panel", ex); }
        }
    }
    [HarmonyPatch(typeof(UIGameBindingSettingsWindow), nameof(UIGameBindingSettingsWindow.Close))]
    internal static class NativeClosePatch
    {
        static bool Prefix()
        {
            if (Plugin.Instance?.Capturing != true) return true;
            Plugin.Instance.Controls.Cancel(); return false;
        }
    }
    [HarmonyPatch(typeof(UIGameBindingSettingsWindow), "OnReset")]
    internal static class NativeResetPatch
    {
        static bool Prefix(out bool __state)
        {
            __state = true;
            if (Plugin.Instance == null || !Plugin.Instance.NativeKeys.Values.Any(k => k.Enabled.Value)) return true;
            try
            {
                Dictionary<ConfigFile, string> backups;
                BindingStore.BackupFiles(new[] { Plugin.Instance.Config }, System.IO.Path.Combine(BepInEx.Paths.ConfigPath, "HotkeyManagerBackups"), out backups);
                return true;
            }
            catch (Exception ex) { __state = false; Plugin.Instance.ReportError("Backing up game chord overrides", ex); Plugin.Instance.Controls?.ReportStatus("Reset cancelled: config backup failed."); return false; }
        }
        static void Postfix(bool __state) { if (__state) Plugin.Instance?.Controls?.ResetNative(); }
    }
}
