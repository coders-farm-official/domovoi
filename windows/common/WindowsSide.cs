// What DomovoiHost tells the Linux side about the Windows side, and the
// Linux update it runs for an administrator (design note
// WINDOWS-ONE-UPDATE-2026-10-22).
//
// The report: DomovoiHost writes /var/lib/domovoi-windows/side.json as
// root (the core reads it only while root owns it and nobody else can write
// it: domovoi/windows_side.py), with this build's version and commit and
// how the last Setup's bring-up went (SetupOutcome). Interop stays off: the
// file goes in on wsl.exe's stdin, and nothing comes back but sha256sum's
// line.
//
// When: whenever the distro can be reached, not only after a bring-up that
// ended "ready" (2026-11-16: a 1.2.1 Setup's provisioning failed, the
// distro and the core kept running on the binaries it had replaced, and
// side.json said 1.2.0 for good, so the tray downloaded the 1.2.1 Setup on
// every "Update Domovoi" and refused it as not newer). So: before a new
// job's bring-up (the service's start included) when the distro is already
// set up, after every bring-up attempt whatever its result, and from the
// main loop whenever what was last written (SideReportKey) isn't this
// build's report, as soon as the holder keeps the distro up.
//
// The Setup outcome also goes to the tray (host\out\setup.json, readable by
// the household's accounts): a failed Setup shows in the icon's menu and
// once as a notification, in plain words, not only in host.log.
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
            return Content(version, commit, utc, null);
        }

        // With the last Setup's outcome ("setup"), when there is one: its
        // strings are SetupOutcome.Clean's, which need no escaping either.
        public static string Content(string version, string commit, DateTime utc, SetupOutcome setup)
        {
            if (version == null || !VersionShape.IsMatch(version)) throw new ArgumentException("not a version: " + version);
            if (commit != null && !CommitShape.IsMatch(commit)) throw new ArgumentException("not a commit: " + commit);
            return "{\"schema\": 1, \"version\": \"" + version + "\", \"build_commit\": "
                + (commit == null ? "null" : "\"" + commit + "\"")
                + ", \"written_at\": \"" + utc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture) + "\""
                + (setup != null ? ", \"setup\": " + setup.ToJsonText() : "")
                + "}\n";
        }

        // What a report says, without the time it was written: DomovoiHost
        // writes again whenever this differs from what it last wrote.
        public static string Key(string version, string commit, SetupOutcome setup)
        {
            return (version ?? "") + "|" + (commit ?? "") + "|" + (setup != null ? setup.ToJsonText() : "");
        }

        // Whether the main loop writes the report now: what was last written
        // isn't what this build would write, the distro is up (the holder
        // runs, so wsl.exe -d starts nothing) and the back-off after a
        // failed write has passed.
        public static bool Due(string lastWrittenKey, string wantKey, bool distroUp, DateTime now, DateTime notBefore)
        {
            return distroUp && lastWrittenKey != wantKey && now >= notBefore;
        }

        // The back-off after a failed write: 1 min, doubling, 10 min at most.
        public static TimeSpan Backoff(int failures)
        {
            int min = 1;
            for (int i = 1; i < failures && min < 10; i++) min *= 2;
            return TimeSpan.FromMinutes(Math.Min(min, 10));
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

    // How the last Setup's bring-up of its job went (DomovoiHost records it
    // in host-state.json "setup" when a job that wasn't provisioned yet is
    // brought up): "ok", or "failed" with provision.sh's step and its error.
    // It goes into side.json for the dashboard's Version card and into
    // host\out\setup.json for the tray. Plain words only: every string is
    // Clean's (printable ASCII, no quotes or backslashes, secrets blanked).
    public sealed class SetupOutcome
    {
        public const string TrayFile = "setup.json";
        public const int MaxError = 300;
        const string NoReason = "no reason was given";

        public string Result;      // "ok" or "failed"
        public string Version;     // the Windows side (DomovoiHost) that ran it
        public string Step;        // provision.sh's step id, or null
        public string Error;       // what failed, or null
        public string At;          // when, yyyy-MM-ddTHH:mm:ssZ
        public int Generation;     // job.json's generation

        static readonly Regex StepShape = new Regex(@"^[a-z][a-z0-9-]{0,31}\z");
        static readonly Regex VersionShape = new Regex(@"^[0-9A-Za-z.+-]{1,64}\z");
        static readonly Regex AtShape = new Regex(@"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\z");

        public bool Failed { get { return Result == "failed"; } }

        public static SetupOutcome Ok(string version, int generation, DateTime utc)
        {
            return Make("ok", version, null, null, generation, utc);
        }

        public static SetupOutcome Failure(string version, string step, string error, int generation, DateTime utc)
        {
            return Make("failed", version, step, error, generation, utc);
        }

        static SetupOutcome Make(string result, string version, string step, string error, int generation, DateTime utc)
        {
            return new SetupOutcome
            {
                Result = result,
                Version = version != null && VersionShape.IsMatch(version) ? version : null,
                Step = step != null && StepShape.IsMatch(step) ? step : null,
                Error = result == "failed" ? (Clean(error) ?? NoReason) : null,
                At = utc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture),
                Generation = generation < 0 ? 0 : generation,
            };
        }

        // Words that can go anywhere without escaping: secrets blanked
        // (Redact), one line, printable ASCII, no quote or backslash, at
        // most MaxError characters. Null for nothing.
        public static string Clean(string s)
        {
            if (s == null) return null;
            s = Redact.Line(s.Replace("\r", " ").Replace("\n", " ").Replace("\t", " "));
            var sb = new System.Text.StringBuilder(s.Length);
            bool space = false;
            foreach (char c in s)
            {
                char o = c == '"' ? '\'' : c == '\\' ? '/' : (c < 0x20 || c > 0x7e) ? '?' : c;
                if (o == ' ') { if (space) continue; space = true; }
                else space = false;
                sb.Append(o);
            }
            string t = sb.ToString().Trim();
            if (t.Length > MaxError) t = t.Substring(0, MaxError - 3).TrimEnd() + "...";
            return t.Length == 0 ? null : t;
        }

        static string Q(string s) { return s == null ? "null" : "\"" + s + "\""; }

        public string ToJsonText()
        {
            return "{\"result\": " + Q(Result) + ", \"version\": " + Q(Version) + ", \"step\": " + Q(Step)
                + ", \"error\": " + Q(Error) + ", \"at\": " + Q(At) + ", \"generation\": " + Inv.S(Generation) + "}";
        }

        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["result"] = Result;
            d["version"] = Version;
            d["step"] = Step;
            d["error"] = Error;
            d["at"] = At;
            d["generation"] = Generation;
            return d;
        }

        // From host-state.json or the tray's copy: null for anything that
        // isn't one (and every string checked again, as if written by
        // someone else).
        public static SetupOutcome FromJson(Dictionary<string, object> d)
        {
            if (d == null) return null;
            string result = Json.Str(d, "result");
            if (result != "ok" && result != "failed") return null;
            string at = Json.Str(d, "at");
            if (at == null || !AtShape.IsMatch(at)) return null;
            string version = Json.Str(d, "version"), step = Json.Str(d, "step");
            return new SetupOutcome
            {
                Result = result,
                Version = version != null && VersionShape.IsMatch(version) ? version : null,
                Step = step != null && StepShape.IsMatch(step) ? step : null,
                Error = result == "failed" ? (Clean(Json.Str(d, "error")) ?? NoReason) : null,
                At = at,
                Generation = Math.Max(0, Json.Int(d, "generation") ?? 0),
            };
        }

        // The tray's words. Menu: one short line, or null when nothing
        // failed.
        public string MenuLine()
        {
            if (!Failed) return null;
            return "Domovoi Setup" + (Version != null ? " " + Version : "") + " didn't finish. Run the newest Setup again.";
        }

        // The notification: a title and its text.
        public string[] Notification()
        {
            if (!Failed) return null;
            string e = (Error ?? NoReason).TrimEnd();
            if (!e.EndsWith(".", StringComparison.Ordinal)) e += ".";
            return new[]
            {
                "Domovoi Setup" + (Version != null ? " " + Version : "") + " didn't finish",
                "It stopped" + (Step != null ? " at its \"" + Step + "\" step" : "") + ": " + e
                    + " Domovoi keeps running what it had. Run the newest Domovoi Setup again; if it stops at the same place, use \"Save logs for support\" in this icon's menu.",
            };
        }

        // A short, stable number for "this failure was already shown"
        // (FNV-1a of its time and generation).
        public int NoticeId()
        {
            unchecked
            {
                uint h = 2166136261;
                foreach (char c in (At ?? "") + "#" + Inv.S(Generation)) { h ^= c; h *= 16777619; }
                return (int)(h & 0x7fffffff);
            }
        }
    }

    public static class LinuxUpdate
    {
        // The checkout and its venv (scripts/linux/provision.sh CHECKOUT and
        // VENV_PY: /opt/domovoi and /opt/domovoi/.venv). HouseholdAi.EnvFile
        // is the domovoi/.env inside it. Until 2026-10-30 this named the
        // package folder, where no venv is, and every Linux-first update
        // failed with execvpe(.../domovoi/.venv/bin/python).
        public const string Checkout = "/opt/domovoi";
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
