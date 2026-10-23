// What DomovoiHost tells the Linux side about the Windows side, and the
// Linux update it runs for an administrator (design note
// WINDOWS-ONE-UPDATE-2026-10-22).
//
// The report: at every bring-up, DomovoiHost writes
// /var/lib/domovoi-windows/side.json as root (the core reads it only while
// root owns it and nobody else can write it: domovoi/windows_side.py), with
// this build's version and commit. Interop stays off: the file goes in on
// wsl.exe's stdin, and nothing comes back but sha256sum's line.
//
// The Linux update: a one-off request in C:\ProgramData\Domovoi\host\in
// (which only administrators can write: the tray's "Update Domovoi", after
// Windows' permission prompt) makes DomovoiHost run the dashboard's own
// update in the distro, as the service user: python -m domovoi.windows_side
// linux-update, which prints one JSON line.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class WindowsSideReport
    {
        public const string Dir = "/var/lib/domovoi-windows";
        public const string File = Dir + "/side.json";

        static readonly Regex VersionShape = new Regex(@"^[0-9A-Za-z.+-]{1,64}\z");
        static readonly Regex CommitShape = new Regex("^[0-9a-f]{40}\\z");

        // The file's text: only characters that need no escaping.
        public static string Content(string version, string commit, DateTime utc)
        {
            if (version == null || !VersionShape.IsMatch(version)) throw new ArgumentException("not a version: " + version);
            if (commit != null && !CommitShape.IsMatch(commit)) throw new ArgumentException("not a commit: " + commit);
            return "{\"schema\": 1, \"version\": \"" + version + "\", \"build_commit\": "
                + (commit == null ? "null" : "\"" + commit + "\"")
                + ", \"written_at\": \"" + utc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture) + "\"}\n";
        }

        // Run as root with the file on stdin: the folder root's and readable,
        // the file written beside its name and moved in, then its hash.
        public static string Script()
        {
            return "set -e; umask 022; mkdir -p " + Dir + "; chown root:root " + Dir + "; chmod 0755 " + Dir + "; "
                + "cat > " + Dir + "/.side.json.new; chmod 0644 " + Dir + "/.side.json.new; "
                + "mv -f " + Dir + "/.side.json.new " + File + "; sha256sum " + File;
        }
    }

    public static class LinuxUpdate
    {
        // The checkout and its venv (scripts/linux/provision.sh CHECKOUT and
        // VENV_PY; HouseholdAi.EnvFile reads the same checkout).
        public const string Checkout = "/opt/domovoi/domovoi";
        public const string Python = Checkout + "/.venv/bin/python";
        public const int TimeoutMs = 5 * 60 * 1000;

        public static string[] WslArgs(string distro, string serviceUser)
        {
            return new[] { "-d", distro, "-u", serviceUser, "--cd", Checkout, "--exec", Python, "-m", "domovoi.windows_side", "linux-update" };
        }

        // The CLI's last JSON line, or null.
        public static Dictionary<string, object> Answer(string stdout)
        {
            string[] lines = (stdout ?? "").Replace("\r", "").Split('\n');
            for (int i = lines.Length - 1; i >= 0; i--)
            {
                string l = lines[i].Trim();
                if (!l.StartsWith("{", StringComparison.Ordinal)) continue;
                try { return Json.ParseObject(l); }
                catch (ArgumentException) { }
                catch (FormatException) { }
                catch (InvalidOperationException) { }
            }
            return null;
        }
    }
}
