// "Keep this PC awake": while the tray runs, Windows doesn't put the PC to
// sleep on its own (SetThreadExecutionState with ES_SYSTEM_REQUIRED; no
// administrator needed; the screen may still turn off). The tray runs only
// while someone is signed in, so the menu also says, from the power plan
// (read only, never changed), whether Windows would still sleep the PC when
// nobody is.

using System;
using System.Runtime.InteropServices;

namespace Domovoi.Windows.Tray
{
    public static class Awake
    {
        const uint ES_CONTINUOUS = 0x80000000, ES_SYSTEM_REQUIRED = 0x00000001;

        [DllImport("kernel32.dll")]
        static extern uint SetThreadExecutionState(uint flags);

        // Call on the thread that lives as long as the tray (the UI thread).
        public static bool Hold(bool on)
        {
            return SetThreadExecutionState(on ? ES_CONTINUOUS | ES_SYSTEM_REQUIRED : ES_CONTINUOUS) != 0;
        }

        static readonly Guid SleepGroup = new Guid("238C9FA8-0AAD-41ED-83F4-97BE242C8F20");
        static readonly Guid StandbyIdle = new Guid("29F6C1DB-86DA-48C5-9FDB-F2B67B1F44DA");
        static readonly Guid HibernateIdle = new Guid("9D7815A6-7EE4-497E-8888-515A05F02364");

        [DllImport("powrprof.dll")]
        static extern uint PowerGetActiveScheme(IntPtr root, out IntPtr scheme);

        [DllImport("powrprof.dll")]
        static extern uint PowerReadACValueIndex(IntPtr root, IntPtr scheme, ref Guid group, ref Guid setting, out uint value);

        [DllImport("powrprof.dll")]
        static extern uint PowerReadDCValueIndex(IntPtr root, IntPtr scheme, ref Guid group, ref Guid setting, out uint value);

        [DllImport("kernel32.dll")]
        static extern IntPtr LocalFree(IntPtr p);

        // The power plan's idle timeouts in seconds (0: never), or null when
        // Windows won't say. dc: on battery.
        public static uint? Timeout(bool dc, bool hibernate)
        {
            IntPtr scheme;
            if (PowerGetActiveScheme(IntPtr.Zero, out scheme) != 0) return null;
            try
            {
                Guid g = SleepGroup, s = hibernate ? HibernateIdle : StandbyIdle;
                uint v;
                uint rc = dc ? PowerReadDCValueIndex(IntPtr.Zero, scheme, ref g, ref s, out v) : PowerReadACValueIndex(IntPtr.Zero, scheme, ref g, ref s, out v);
                return rc == 0 ? (uint?)v : null;
            }
            finally { LocalFree(scheme); }
        }

        // The menu line under "Keep this PC awake". seconds: the plugged-in
        // sleep (or hibernate) timeout, whichever comes first; 0 never;
        // null unknown.
        public static string SignedOutLine(uint? sleepSeconds, uint? hibernateSeconds)
        {
            if (sleepSeconds == null && hibernateSeconds == null) return null;
            uint s = sleepSeconds ?? 0, h = hibernateSeconds ?? 0;
            uint first = s == 0 ? h : (h == 0 ? s : Math.Min(s, h));
            if (first == 0) return "Windows won't put this PC to sleep by itself.";
            return "When nobody is signed in, Windows still sleeps it after " + Minutes(first) + ".";
        }

        static string Minutes(uint seconds)
        {
            uint m = (seconds + 59) / 60;
            if (m >= 120 && m % 60 == 0) return Inv.S((int)(m / 60)) + " hours";
            if (m == 60) return "1 hour";
            return Inv.S((int)m) + (m == 1 ? " minute" : " minutes");
        }

        public static string CurrentSignedOutLine()
        {
            return SignedOutLine(Timeout(false, false), Timeout(false, true));
        }
    }
}
