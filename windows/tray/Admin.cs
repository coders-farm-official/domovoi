// The two things the tray asks an administrator for (Windows' own
// permission prompt): restarting Domovoi, and copying its logs. The tray
// itself never runs elevated. What runs elevated is Windows' own
// PowerShell (System32) for the restart, and Setup's helper from
// C:\Program Files\Domovoi (an administrators-only folder) for the logs:
// never a program from a folder the household can write, such as wherever
// this copy of the tray was started from.

using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;

namespace Domovoi.Windows.Tray
{
    public static class Admin
    {
        static readonly Regex ServiceName = new Regex(@"^[A-Za-z0-9_.-]{1,80}\z");

        public static string PowerShell
        {
            get { return Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"); }
        }

        // The documented admin restart (docs/WINDOWS_HOST.md): Restart-Service.
        // Its module is looked up on the machine's module path only, never
        // the account's own (a module of the same name in Documents could
        // otherwise stand in for it in the elevated PowerShell).
        public static string RestartArguments(string service)
        {
            if (service == null || !ServiceName.IsMatch(service)) throw new ArgumentException("not a service name");
            return "-NoProfile -NonInteractive -WindowStyle Hidden -Command \"$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine'); "
                + @"Microsoft.PowerShell.Management\Restart-Service -Name '" + service + "'\"";
        }

        // Setup's helper, where Setup installs it, or null.
        public static string Helper()
        {
            string p = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Domovoi\DomovoiSetupHelper.exe");
            return File.Exists(p) ? p : null;
        }

        // Documents\Domovoi logs, where Setup's Copy logs puts them too, as
        // long as Documents is inside this account's profile; otherwise (a
        // Documents moved elsewhere) the profile's own folder. The copy runs
        // as an administrator, so it never goes where the account's own
        // settings alone point it.
        public static string LogsFolder()
        {
            string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile).TrimEnd(Path.DirectorySeparatorChar);
            string docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            return Path.Combine(InsideProfile(docs, profile) ? docs : profile, "Domovoi logs");
        }

        public static bool InsideProfile(string folder, string profile)
        {
            if (string.IsNullOrEmpty(folder) || string.IsNullOrEmpty(profile)) return false;
            char sep = Path.DirectorySeparatorChar;
            string f = Path.GetFullPath(folder).TrimEnd(sep) + sep, p = Path.GetFullPath(profile).TrimEnd(sep) + sep;
            return f.StartsWith(p, StringComparison.OrdinalIgnoreCase) && f.IndexOf("..", StringComparison.Ordinal) < 0;
        }

        public static string CopyLogsArguments(string dest)
        {
            if (dest.IndexOf('"') >= 0) throw new ArgumentException("bad folder");
            return "copy-logs --kind install --dest \"" + dest.TrimEnd('\\') + "\"";
        }

        // Runs exe elevated (Windows asks first) and waits. Null when it
        // ran and exited 0; "cancelled" when the person said no; else why.
        public static string RunElevated(string exe, string args, int timeoutMs)
        {
            var psi = new ProcessStartInfo(exe, args)
            {
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
                WorkingDirectory = Environment.SystemDirectory,
            };
            try
            {
                using (Process p = Process.Start(psi))
                {
                    if (p == null) return "Windows didn't start it";
                    if (!p.WaitForExit(timeoutMs)) return "it is still running";
                    return p.ExitCode == 0 ? null : "it stopped with code " + Inv.S(p.ExitCode);
                }
            }
            catch (Win32Exception e)
            {
                if (e.NativeErrorCode == 1223) return "cancelled";
                return e.Message;
            }
        }
    }
}
