// The core's own log for "Save logs" (incident 2026-11-09: every room
// wedged and nobody could read why). The core and the dashboard log to the
// systemd journal inside the distro, which belongs to domovoi-svc: the
// elevated helper can't open it (a WSL distro is registered per account),
// so DomovoiHost, which runs as that account, copies it out:
//
//   * logs\core.log    the journal of domovoi-core and domovoi-web, the
//                      last 24 hours and at most LinesPerUnit lines each,
//                      written whole each time (never appended), at the
//                      host's first ready pass, every 10 minutes after,
//                      and on the helper's request "core-log" (copy-logs
//                      asks for a fresh one, waits HelperWaitSec, and falls
//                      back to the last copy, saying so).
//
// Every line is redacted when DomovoiHost writes it and again in the copy,
// like host.log. A read that fails, times out or prints nothing never
// replaces the last good copy.

using System;
using System.Text;

namespace Domovoi.Windows
{
    public static class CoreLog
    {
        public const string Verb = "core-log";
        public const string FileName = "core.log";
        public static readonly string[] Units = { "domovoi-core", "domovoi-web" };

        // The window: a day covers "it broke last night" without dragging
        // in weeks; 5000 lines a unit is hours of a busy core (a turn logs
        // a few lines) and keeps the file near 1 MB in practice.
        public const string Since = "-24h";
        public const int LinesPerUnit = 5000;
        // Each unit's output is cut to its last 2 MiB inside the distro
        // (tail -c keeps the newest end), whatever the line lengths.
        public const int BytesPerUnit = 2 * 1024 * 1024;
        // A backstop on what wsl.exe's output is read into (Wsl.Run keeps the
        // first characters); above both units' cut, so it never drops the
        // newest lines in practice.
        public const int MaxOutputChars = 5 * 1024 * 1024;
        // One line longer than this is cut (a dumped request body, say).
        public const int MaxLineChars = 4000;
        // journalctl answers in well under a second; a wedged WSL must not
        // hold the host's loop (or "Save logs") for long.
        public const int TimeoutMs = 15000;
        // What copy-logs waits for the host's fresh copy: the host's own
        // timeout plus its one-second loop and the write.
        public const int HelperWaitSec = 20;
        // copy-logs reads at most this much of core.log (a file the account
        // can write: a huge one must not fill the household's Documents).
        public const long MaxCopyBytes = 8L * 1024 * 1024;
        public static readonly TimeSpan Every = TimeSpan.FromMinutes(10);

        public const string Header = "==== ";

        // As root (the journal of system units), through /bin/sh with this
        // fixed script: nothing from outside goes into it.
        public static string Script()
        {
            var sb = new StringBuilder();
            sb.Append("for u in ").Append(string.Join(" ", Units)).Append("; do ");
            sb.Append("echo \"").Append(Header).Append("$u (journalctl --since ").Append(Since)
              .Append(", at most ").Append(Inv.S(LinesPerUnit)).Append(" lines)\"; ");
            sb.Append("/usr/bin/journalctl -u \"$u\" --since=").Append(Since).Append(" -n ").Append(Inv.S(LinesPerUnit))
              .Append(" --no-pager -o short-iso 2>&1 | /usr/bin/tail -c ").Append(Inv.S(BytesPerUnit)).Append("; ");
            sb.Append("done");
            return sb.ToString();
        }

        public static string[] WslArgs(string distro)
        {
            return new[] { "-d", distro, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c", Script() };
        }

        // The text of core.log from one run of WslArgs, or null and why.
        public static string Render(int exitCode, bool timedOut, string stdout, string stderr, DateTime nowUtc, out string why)
        {
            if (timedOut)
            {
                why = "journalctl did not answer within " + Inv.S(TimeoutMs / 1000) + " s";
                return null;
            }
            string output = (stdout ?? "").Replace("\0", "");
            if (exitCode != 0)
            {
                why = "wsl.exe exit " + Inv.S(exitCode) + ": " + FirstLine((stderr ?? "") + "\n" + output);
                return null;
            }
            if (output.IndexOf(Header + Units[0], StringComparison.Ordinal) < 0)
            {
                why = "the journal read printed nothing";
                return null;
            }
            var sb = new StringBuilder();
            sb.Append("# Domovoi's core log: journalctl of ").Append(string.Join(" and ", Units)).Append(", the last 24 hours, at most ")
              .Append(Inv.S(LinesPerUnit)).Append(" lines each, read by DomovoiHost at ")
              .Append(nowUtc.ToString("yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture)).Append(" UTC\r\n");
            foreach (string raw in output.Replace("\r", "").Split('\n'))
            {
                string line = raw.Length > MaxLineChars ? raw.Substring(0, MaxLineChars) + " [cut]" : raw;
                sb.Append(Redact.Line(line)).Append("\r\n");
            }
            why = "";
            return sb.ToString().TrimEnd('\r', '\n') + "\r\n";
        }

        // The line the copy carries when the core's log couldn't be read.
        public static string Note(string why)
        {
            return "NOTE: Domovoi's core log (the journal of domovoi-core and domovoi-web) could not be read: " + why;
        }

        // The line above a last copy when a fresh one couldn't be had.
        public static string StaleNote(string why, DateTime writtenLocal)
        {
            return "NOTE: a fresh copy of Domovoi's core log could not be read (" + why + "); this is DomovoiHost's last copy, from "
                + writtenLocal.ToString("yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture) + ".";
        }

        // The line above the last copy when none was asked for (the
        // uninstaller's copy).
        public static string LastCopyNote(DateTime writtenLocal)
        {
            return "NOTE: this is DomovoiHost's last copy of Domovoi's core log, from "
                + writtenLocal.ToString("yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture) + ".";
        }

        static string FirstLine(string s)
        {
            foreach (string l in s.Split('\n'))
            {
                string t = l.Trim();
                if (t.Length > 0) return t.Length > 300 ? t.Substring(0, 300) : t;
            }
            return "no message";
        }
    }
}
