using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using BepInEx;
using BepInEx.Configuration;

namespace GK2.HotkeyManager
{
    internal static class BindingInfo
    {
        static readonly Dictionary<short, OpCode> opcodes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
            .Where(f => f.FieldType == typeof(OpCode)).Select(f => (OpCode)f.GetValue(null)).ToDictionary(o => o.Value);
        internal static string Describe(BaseUnityPlugin plugin, ConfigEntryBase entry)
        {
            var fields = new List<FieldInfo>(); var readers = new List<string>();
            Type[] types;
            try { types = plugin.GetType().Assembly.GetTypes(); }
            catch (ReflectionTypeLoadException ex) { types = ex.Types.Where(t => t != null).ToArray(); }
            foreach (var type in types)
                foreach (var field in type.GetFields(BindingFlags.Static | BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.DeclaredOnly))
                {
                    if (!typeof(ConfigEntryBase).IsAssignableFrom(field.FieldType) || (!field.IsStatic && !type.IsInstanceOfType(plugin))) continue;
                    try { if (ReferenceEquals(field.GetValue(field.IsStatic ? null : plugin), entry)) fields.Add(field); } catch { }
                }
            if (fields.Count != 0)
                foreach (var type in types)
                    foreach (var method in type.GetMethods(BindingFlags.Static | BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.DeclaredOnly))
                    {
                        try { if (ReadFields(method).Any(token => fields.Any(f => f.Module == method.Module && f.MetadataToken == token))) readers.Add(type.FullName + "." + method.Name); }
                        catch { /* Native, stripped or generic bodies may not be inspectable. */ }
                    }
            string info = entry.Description.Description + "\nPlugin: " + plugin.Info.Metadata.GUID + "\nSetting: " + entry.Definition.Section + "." + entry.Definition.Key;
            if (fields.Count != 0) info += "\nConfig field: " + string.Join(", ", fields.Select(f => f.DeclaringType.FullName + "." + f.Name));
            info += readers.Count == 0 ? "\nFunction: not exposed by this mod; see its description above." : "\nRead by: " + string.Join(", ", readers.Distinct().Take(5));
            return info;
        }
        static IEnumerable<int> ReadFields(MethodInfo method)
        {
            byte[] il = method.GetMethodBody()?.GetILAsByteArray();
            if (il == null) yield break;
            for (int offset = 0; offset < il.Length;)
            {
                short code = il[offset++] == 0xfe ? (short)(0xfe00 | il[offset++]) : il[offset - 1];
                OpCode op;
                if (!opcodes.TryGetValue(code, out op)) yield break;
                if (op == OpCodes.Ldfld || op == OpCodes.Ldsfld) yield return BitConverter.ToInt32(il, offset);
                switch (op.OperandType)
                {
                    case OperandType.InlineNone: break;
                    case OperandType.ShortInlineI: case OperandType.ShortInlineVar: case OperandType.ShortInlineBrTarget: offset += 1; break;
                    case OperandType.InlineVar: offset += 2; break;
                    case OperandType.InlineI8: case OperandType.InlineR: offset += 8; break;
                    case OperandType.InlineSwitch: offset += 4 + 4 * BitConverter.ToInt32(il, offset); break;
                    default: offset += 4; break;
                }
            }
        }
    }
}
