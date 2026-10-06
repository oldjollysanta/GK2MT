using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;

namespace GK2.HotkeyManager
{
    /// <summary>Stores the largest set actually held together; sequential presses cannot fabricate a chord.</summary>
    public sealed class ChordCapture
    {
        public string[] Peak { get; private set; } = new string[0];
        public string Main { get; private set; }
        public bool WaitingForNeutral = true;
        public bool Sample(IEnumerable<string> held, IEnumerable<string> pressed, bool neutral)
        {
            if (WaitingForNeutral) { if (neutral) WaitingForNeutral = false; return false; }
            string[] current = held.Distinct().ToArray();
            if (current.Length != 0 && current.Length >= Peak.Length)
            {
                var down = pressed.Where(current.Contains).ToArray();
                Peak = current;
                if (down.Length != 0) Main = down.Last();
                if (!Peak.Contains(Main)) Main = Peak.Last();
            }
            return neutral && Peak.Length != 0;
        }
    }
    internal static class InputController
    {
        [StructLayout(LayoutKind.Sequential)]
        struct State { public uint Packet; public ushort Buttons; public byte LT, RT; public short LX, LY, RX, RY; }
        [DllImport("xinput1_4.dll", EntryPoint = "XInputGetState")]
        static extern uint Read14(uint index, out State state);
        [DllImport("xinput9_1_0.dll", EntryPoint = "XInputGetState")]
        static extern uint Read910(uint index, out State state);
        static bool unavailable14, unavailable910;
        internal static readonly string[] Names = { "Up", "Down", "Left", "Right", "Start", "Back", "L3", "R3", "LB", "RB", "A", "B", "X", "Y", "LT", "RT" };
        static readonly uint[] masks = { 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 4096, 8192, 16384, 32768, 65536, 131072 };
        internal static uint Mask(string name)
        {
            string physical;
            if (Rules.PadNames.TryGetValue(name, out physical)) name = physical;
            int index = Array.IndexOf(Names, name);
            return index < 0 ? 0 : masks[index];
        }
        internal static string[] Buttons(uint mask) => Names.Where(n => (mask & Mask(n)) != 0).ToArray();
        internal static uint Read()
        {
            for (uint index = 0; index < 4; index++)
            {
                State state = default(State);
                bool connected = false;
                if (!unavailable14)
                    try { connected = Read14(index, out state) == 0; }
                    catch (DllNotFoundException) { unavailable14 = true; }
                    catch (EntryPointNotFoundException) { unavailable14 = true; }
                if (unavailable14 && !unavailable910)
                    try { connected = Read910(index, out state) == 0; }
                    catch (DllNotFoundException) { unavailable910 = true; }
                    catch (EntryPointNotFoundException) { unavailable910 = true; }
                if (connected) return state.Buttons | (state.LT > 100 ? 65536u : 0) | (state.RT > 100 ? 131072u : 0);
            }
            return 0;
        }
    }
}
