// Starting with Windows, and the tray's two settings.
//
// Setup installs DomovoiTray.exe in C:\Program Files\Domovoi and starts it
// at every account's sign-in with one machine-wide value:
// HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run, "DomovoiTray" =
// "...\DomovoiTray.exe" --autostart (the uninstaller removes it). An
// account that turns "Start with Windows" off gets StartWithWindows=0 in
// its own HKCU\Software\Domovoi\Tray, and the sign-in start then does
// nothing for it.
//
// A copy run on its own (an install from before the tray, rc1/rc2) has no
// machine-wide value: it registers itself for this account's sign-in under
// HKCU\...\Run, "DomovoiTray", with the same switch. When Setup's value
// exists, a per-account one is removed (Setup's copy starts instead), and a
// copy run from elsewhere hands over to Setup's.
//
// A developer run (Options.Dev) keeps all of this under
// HKCU\Software\Domovoi\TrayDev\<profile>, the real Run keys untouched.

using System;
using System.IO;
using Microsoft.Win32;

namespace Domovoi.Windows.Tray
{
    public interface ITrayStore
    {
        string MachineRun();            // Setup's value, or null
        string UserRun();
        void SetUserRun(string command);  // null deletes it
        int? Dword(string name);
        void SetDword(string name, int value);
    }

    public sealed class RegistryTrayStore : ITrayStore
    {
        public const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        public const string RunValue = "DomovoiTray";
        public const string SettingsKey = @"Software\Domovoi\Tray";

        readonly string profileKey;    // null: the real keys

        public RegistryTrayStore(string devProfile)
        {
            profileKey = devProfile == null ? null : @"Software\Domovoi\TrayDev\" + devProfile;
        }

        string Settings { get { return profileKey ?? SettingsKey; } }

        public string MachineRun()
        {
            if (profileKey != null) return ReadString(Registry.CurrentUser, profileKey + @"\MachineRun", RunValue);
            using (RegistryKey hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
                return ReadString(hklm, RunKey, RunValue);
        }

        public string UserRun()
        {
            return ReadString(Registry.CurrentUser, profileKey != null ? profileKey + @"\Run" : RunKey, RunValue);
        }

        public void SetUserRun(string command)
        {
            string key = profileKey != null ? profileKey + @"\Run" : RunKey;
            using (RegistryKey k = Registry.CurrentUser.CreateSubKey(key))
            {
                if (command == null) k.DeleteValue(RunValue, false);
                else k.SetValue(RunValue, command, RegistryValueKind.String);
            }
        }

        public int? Dword(string name)
        {
            using (RegistryKey k = Registry.CurrentUser.OpenSubKey(Settings))
            {
                object v = k == null ? null : k.GetValue(name);
                return v is int ? (int?)(int)v : null;
            }
        }

        public void SetDword(string name, int value)
        {
            using (RegistryKey k = Registry.CurrentUser.CreateSubKey(Settings)) k.SetValue(name, value, RegistryValueKind.DWord);
        }

        static string ReadString(RegistryKey root, string key, string name)
        {
            using (RegistryKey k = root.OpenSubKey(key))
                return k == null ? null : k.GetValue(name) as string;
        }
    }

    public enum LaunchAction { Continue, Quit, HandOver }

    public static class Startup
    {
        public const string StartPref = "StartWithWindows";
        public const string AwakePref = "KeepAwake";

        public static string Command(string exe) { return "\"" + exe + "\" --autostart"; }

        // The exe a Run command starts, or null.
        public static string ExeOf(string command)
        {
            if (string.IsNullOrEmpty(command)) return null;
            string c = command.Trim();
            if (c.StartsWith("\"", StringComparison.Ordinal))
            {
                int end = c.IndexOf('"', 1);
                return end > 1 ? c.Substring(1, end - 1) : null;
            }
            int sp = c.IndexOf(' ');
            return sp < 0 ? c : c.Substring(0, sp);
        }

        public static bool StartWithWindows(ITrayStore s) { return (s.Dword(StartPref) ?? 1) != 0; }

        public static bool KeepAwake(ITrayStore s) { return (s.Dword(AwakePref) ?? 1) != 0; }

        // What a starting tray does about sign-in. handOverTo: Setup's copy,
        // when this one is another.
        public static LaunchAction OnLaunch(ITrayStore s, string exe, bool autostart, Func<string, bool> fileExists, out string handOverTo)
        {
            handOverTo = null;
            if (autostart && !StartWithWindows(s)) return LaunchAction.Quit;
            string machineExe = ExeOf(s.MachineRun());
            if (machineExe != null && fileExists(machineExe))
            {
                if (s.UserRun() != null && IsTray(ExeOf(s.UserRun()))) s.SetUserRun(null);
                if (!SamePath(machineExe, exe)) { handOverTo = machineExe; return LaunchAction.HandOver; }
                return LaunchAction.Continue;
            }
            if (StartWithWindows(s) && s.UserRun() != Command(exe)) s.SetUserRun(Command(exe));
            return LaunchAction.Continue;
        }

        public static void SetStartWithWindows(ITrayStore s, string exe, bool on, Func<string, bool> fileExists)
        {
            s.SetDword(StartPref, on ? 1 : 0);
            string machineExe = ExeOf(s.MachineRun());
            if (machineExe != null && fileExists(machineExe)) return;
            if (on) s.SetUserRun(Command(exe));
            else if (s.UserRun() != null && IsTray(ExeOf(s.UserRun()))) s.SetUserRun(null);
        }

        static bool IsTray(string exe)
        {
            return exe != null && Path.GetFileName(exe).Equals("DomovoiTray.exe", StringComparison.OrdinalIgnoreCase);
        }

        static bool SamePath(string a, string b)
        {
            try { return string.Equals(Path.GetFullPath(a), Path.GetFullPath(b), StringComparison.OrdinalIgnoreCase); }
            catch (ArgumentException) { return false; }
        }
    }
}
