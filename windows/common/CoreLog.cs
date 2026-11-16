// The core's own log for "Save logs" (incident 2026-11-09: every room
// wedged and nobody could read why). The core and the dashboard log to the
// systemd journal inside the distro, which belongs to domovoi-svc: the
// elevated helper can't open it (a WSL distro is registered per account),
// so DomovoiHost, which runs as that account, copies it out:
//
//   * logs\core.log    the journal of domovoi-core and domovoi-web, the
//                      last WindowHours hours (or since the core last
//                      started, when that is earlier) and at most
//                      LinesPerUnit lines each, written whole each time
//                      (never appended), at the host's first ready pass,
//                      every 10 minutes after, and on the helper's request
//                      "core-log" (copy-logs asks for a fresh one, waits
//                      HelperWaitSec, and falls back to the last copy,
//                      saying so);
//   * logs\core-watchdog.log, core-watchdog.log.1, core-faulthandler.log
//                      the core's own freeze record (domovoi/loop_watchdog.py:
//                      the stall reports with every thread's stack, and
//                      what faulthandler wrote), files in the service
//                      user's ~/.domovoi/logs that the journal doesn't
//                      hold in full; read as that user (never as root: a
//                      link there reaches nothing the core couldn't read
//                      already), the newest BytesPerFile of each, at the
//                      same times. A file the distro no longer has is
//                      removed here too.
//
// Every line is redacted when DomovoiHost writes it and again in the copy,
// like host.log. A read that fails, times out or prints nothing never
// replaces the last good copy.

using System;
using System.Collections.Generic;
using System.Text;

namespace Domovoi.Windows
{
    public static class CoreLog
    {
        public const string Verb = "core-log";
        public const string FileName = "core.log";
        public static readonly string[] Units = { "domovoi-core", "domovoi-web" };

        // The window: three days cover "it broke over the weekend" without
        // dragging in weeks, and when the core has run longer than that
        // the window starts at its last start instead, so the start and
        // what led up to a freeze are in it; 5000 lines a unit (the newest
        // of the window) is hours of a busy core (a turn logs a few lines)
        // and keeps the file near 1 MB in practice.
        public const int WindowHours = 72;
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
        // The core's own files (FileArgs): a read of three small files,
        // skipped when the journal read timed out (WSL is wedged then).
        public const int FilesTimeoutMs = 10000;
        // What copy-logs waits for the host's fresh copy: both reads'
        // timeouts plus the host's one-second loop and the writes.
        public const int HelperWaitSec = 30;
        // copy-logs reads at most this much of core.log (a file the account
        // can write: a huge one must not fill the household's Documents).
        public const long MaxCopyBytes = 8L * 1024 * 1024;
        public static readonly TimeSpan Every = TimeSpan.FromMinutes(10);

        public const string Header = "==== ";

        // As root (the journal of system units), through /bin/sh with this
        // fixed script: nothing from outside goes into it. $s is where the
        // window starts, in seconds since the epoch: WindowHours ago, or
        // the core unit's last start (ActiveEnterTimestamp) when that is
        // earlier; anything odd about the latter leaves the former.
        public static string Script()
        {
            var sb = new StringBuilder();
            sb.Append("s=$(($(/usr/bin/date +%s)-").Append(Inv.S(WindowHours * 3600)).Append(")); ");
            sb.Append("r=$(/usr/bin/systemctl show ").Append(Units[0]).Append(".service -p ActiveEnterTimestamp --value 2>/dev/null); ");
            sb.Append("if [ -n \"$r\" ] && [ \"$r\" != n/a ]; then r=$(/usr/bin/date -d \"$r\" +%s 2>/dev/null) && [ \"$r\" -gt 0 ] 2>/dev/null && [ \"$r\" -lt \"$s\" ] && s=$r; fi; ");
            sb.Append("d=$(/usr/bin/date -u -d \"@$s\" '+%Y-%m-%d %H:%M:%S UTC'); ");
            sb.Append("for u in ").Append(string.Join(" ", Units)).Append("; do ");
            sb.Append("echo \"").Append(Header).Append("$u (journalctl since $d, at most ").Append(Inv.S(LinesPerUnit)).Append(" lines)\"; ");
            sb.Append("/usr/bin/journalctl -u \"$u\" --since=\"@$s\" -n ").Append(Inv.S(LinesPerUnit))
              .Append(" --no-pager -o short-iso 2>&1 | /usr/bin/tail -c ").Append(Inv.S(BytesPerUnit)).Append("; ");
            sb.Append("done");
            return sb.ToString();
        }

        public static string[] WslArgs(string distro)
        {
            return new[] { "-d", distro, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c", Script() };
        }

        // The text of core.log from one run of WslArgs, or null and why.
        public static string TimedOutWhy { get { return "journalctl did not answer within " + Inv.S(TimeoutMs / 1000) + " s"; } }

        public static string Render(int exitCode, bool timedOut, string stdout, string stderr, DateTime nowUtc, out string why)
        {
            if (timedOut)
            {
                why = TimedOutWhy;
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
            sb.Append("# Domovoi's core log: journalctl of ").Append(string.Join(" and ", Units)).Append(", the last ").Append(Inv.S(WindowHours))
              .Append(" hours (or since the core last started, if earlier), at most ")
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

        // -- the core's own files (domovoi/loop_watchdog.py) -------------

        // In the service user's ~/.domovoi/logs (loop_watchdog.logs_dir),
        // and under the same names in C:\ProgramData\Domovoi\logs and the
        // copy. The watchdog's log rotates past 1 MB to one .1 copy.
        public static readonly string[] Files = { "core-watchdog.log", "core-watchdog.log.1", "core-faulthandler.log" };
        public const string ServiceUser = "domovoi";
        public const string FilesDir = "/home/" + ServiceUser + "/.domovoi/logs";
        // The newest end of each (tail -c), in base64 on one line, so no
        // line of a file can pass for a header, whatever bytes it holds.
        public const int BytesPerFile = 2 * 1024 * 1024;
        // A backstop on what wsl.exe's output is read into: above all
        // three files in base64 (4 characters per 3 bytes).
        public const int MaxFilesOutputChars = 9 * 1024 * 1024;
        // copy-logs reads at most this much of each (a file the account can
        // write, like core.log).
        public const long MaxFileCopyBytes = 4L * 1024 * 1024;
        public const string MissingMark = " none";

        // As the service user (the files' owner), through /bin/sh with this
        // fixed script. Per file: "==== NAME" and its newest bytes in base64
        // on the next line, or "==== NAME none" when there is no such file.
        public static string FilesScript()
        {
            var sb = new StringBuilder();
            sb.Append("for f in ").Append(string.Join(" ", Files)).Append("; do ");
            sb.Append("p=").Append(FilesDir).Append("/$f; ");
            sb.Append("if [ -f \"$p\" ]; then echo \"").Append(Header).Append("$f\"; /usr/bin/tail -c ").Append(Inv.S(BytesPerFile))
              .Append(" \"$p\" | /usr/bin/base64 -w 0; echo; else echo \"").Append(Header).Append("$f").Append(MissingMark).Append("\"; fi; ");
            sb.Append("done");
            return sb.ToString();
        }

        public static string[] FileArgs(string distro)
        {
            return new[] { "-d", distro, "-u", ServiceUser, "--cd", "/", "--exec", "/bin/sh", "-c", FilesScript() };
        }

        // The files from one run of FileArgs: name -> the text to write
        // (a header, then every line redacted and cut like core.log's), or
        // null when the distro has no such file. Null overall, and why,
        // when the read failed or didn't print every file's header.
        public static Dictionary<string, string> RenderFiles(int exitCode, bool timedOut, string stdout, string stderr, DateTime nowUtc, out string why)
        {
            if (timedOut)
            {
                why = "reading the core's files did not answer within " + Inv.S(FilesTimeoutMs / 1000) + " s";
                return null;
            }
            if (exitCode != 0)
            {
                why = "wsl.exe exit " + Inv.S(exitCode) + ": " + FirstLine((stderr ?? "") + "\n" + (stdout ?? ""));
                return null;
            }
            var got = new Dictionary<string, string>(StringComparer.Ordinal);
            string[] lines = (stdout ?? "").Replace("\r", "").Split('\n');
            for (int i = 0; i < lines.Length; i++)
            {
                if (!lines[i].StartsWith(Header, StringComparison.Ordinal)) continue;
                string rest = lines[i].Substring(Header.Length);
                bool missing = rest.EndsWith(MissingMark, StringComparison.Ordinal);
                string name = missing ? rest.Substring(0, rest.Length - MissingMark.Length) : rest;
                if (Array.IndexOf(Files, name) < 0 || got.ContainsKey(name)) continue;
                if (missing) { got[name] = null; continue; }
                byte[] bytes;
                try { bytes = Convert.FromBase64String(i + 1 < lines.Length ? lines[i + 1].Trim() : ""); }
                catch (FormatException) { why = name + " did not come back as base64"; return null; }
                i++;
                got[name] = RenderFile(name, bytes, nowUtc);
            }
            foreach (string f in Files)
                if (!got.ContainsKey(f)) { why = "the read of the core's files printed nothing for " + f; return null; }
            why = "";
            return got;
        }

        static string RenderFile(string name, byte[] bytes, DateTime nowUtc)
        {
            var sb = new StringBuilder();
            sb.Append("# ").Append(name).Append(" from ").Append(FilesDir).Append(" in Domovoi's Linux system");
            if (bytes.Length >= BytesPerFile) sb.Append(" (its newest ").Append(Inv.S(BytesPerFile / (1024 * 1024))).Append(" MB)");
            sb.Append(", read by DomovoiHost at ")
              .Append(nowUtc.ToString("yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture)).Append(" UTC\r\n");
            string text = new UTF8Encoding(false, false).GetString(bytes).Replace("\0", "").Replace("\r", "").TrimEnd('\n');
            if (text.Length == 0) sb.Append("(empty)\r\n");
            else
                foreach (string raw in text.Split('\n'))
                {
                    string line = raw.Length > MaxLineChars ? raw.Substring(0, MaxLineChars) + " [cut]" : raw;
                    sb.Append(Redact.Line(line)).Append("\r\n");
                }
            return sb.ToString();
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
