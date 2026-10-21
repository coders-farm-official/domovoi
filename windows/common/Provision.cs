// scripts/linux/provision.sh as the Windows side sees it: its progress
// lines, what its exit means, and the arguments the installer gives it.
//
// The protocol (provision.sh, header comment): every stdout line that
// starts with "@@DOMOVOI " is one JSON object with v, step, index, total,
// state (start, progress, done, skip, fail; plan in a dry run), percent
// (never goes down) and message; download lines add bytes and bytes_total
// (null when unknown). Every run ends with exactly one line that has
// "final":true, result (ok, failed, rerun_after_restart, dry_run,
// usage_error, busy, help, stopped), restart_required, exit_code, failed_step,
// head, tier, wsl, gpu (a list, [] for the CPU alone, null when unknown),
// warnings and dashboard. Only a SIGKILL leaves no final line, and the
// installer treats that as a failure and runs the script again
// (step1\REPORT.md). Exit codes: 0 done (or "stopped": the run given
// --stop-before STEP got there), 1 failed, 2 bad usage, 3 WSL without
// systemd as PID 1 (terminate the distro, run again), 4 busy.

using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public sealed class ProvisionLine
    {
        public const string Prefix = "@@DOMOVOI ";

        public string Step;
        public int Index;
        public int Total;
        public string State;
        public int Percent;
        public string Message;
        public long? Bytes;
        public long? BytesTotal;

        public bool Final;
        public string Result;
        public bool RestartRequired;
        public int? ExitCode;
        public string FailedStep;
        public string Head;
        public string Tier;
        public bool? Wsl;
        // null: unknown (the line said null, or this is not the final line).
        public List<string> Gpu;
        public int Warnings;
        public string Dashboard;

        static readonly Regex StepId = new Regex(@"^[a-z][a-z0-9-]{0,31}\z");

        // The progress object a line carries, or null for any other line
        // (people's text, a malformed or unknown-version object).
        public static ProvisionLine TryParse(string line)
        {
            if (line == null) return null;
            line = line.TrimEnd('\r', '\n');
            if (!line.StartsWith(Prefix, StringComparison.Ordinal)) return null;
            Dictionary<string, object> d;
            try
            {
                d = Json.ParseObject(line.Substring(Prefix.Length));
            }
            catch (FormatException)
            {
                return null;
            }
            if (Json.Int(d, "v") != 1) return null;
            var p = new ProvisionLine();
            p.Step = Json.Str(d, "step");
            p.State = Json.Str(d, "state");
            if (p.Step == null || !StepId.IsMatch(p.Step) || string.IsNullOrEmpty(p.State)) return null;
            p.Index = Json.Int(d, "index") ?? 0;
            p.Total = Json.Int(d, "total") ?? 0;
            int pct = Json.Int(d, "percent") ?? 0;
            p.Percent = pct < 0 ? 0 : (pct > 100 ? 100 : pct);
            p.Message = Json.Str(d, "message") ?? "";
            p.Bytes = Json.Long(d, "bytes");
            p.BytesTotal = Json.Long(d, "bytes_total");
            p.Final = Json.Bool(d, "final") ?? false;
            if (p.Final)
            {
                p.Result = Json.Str(d, "result");
                if (p.Result == null) return null;
                p.RestartRequired = Json.Bool(d, "restart_required") ?? false;
                p.ExitCode = Json.Int(d, "exit_code");
                p.FailedStep = Json.Str(d, "failed_step");
                p.Head = Json.Str(d, "head");
                p.Tier = Json.Str(d, "tier");
                p.Wsl = Json.Bool(d, "wsl");
                p.Gpu = Json.StrList(d, "gpu");
                p.Warnings = Json.Int(d, "warnings") ?? 0;
                p.Dashboard = Json.Str(d, "dashboard");
            }
            return p;
        }
    }

    public enum ProvisionVerdict
    {
        // Finished; the distro runs what it should.
        Done,
        // Finished, but /etc/wsl.conf changed: terminate the distro, start
        // it again, and run the script once more to confirm.
        DoneRestartDistro,
        // Exit 3: systemd is not PID 1 yet. Terminate, start, run again.
        RestartDistroAndRerun,
        // Exit 4: another run holds the lock. Wait, run again.
        Busy,
        // No final line: killed (SIGKILL) or lost. Run again.
        Lost,
        Failed,
        // Exit 0, result "stopped": a run given --stop-before STEP got
        // there and ran nothing from STEP on.
        Stopped,
    }

    public static class ProvisionOutcome
    {
        // What a finished run means. The process's exit code wins over the
        // final line's exit_code if they disagree (the line could be
        // stale); reason says why for anything but Done.
        public static ProvisionVerdict Classify(int exitCode, ProvisionLine final, out string reason)
        {
            if (final == null || !final.Final)
            {
                reason = "provisioning stopped without its final progress line (exit " + Inv.S(exitCode) + ")";
                return ProvisionVerdict.Lost;
            }
            string why = string.IsNullOrEmpty(final.Message) ? "" : final.Message;
            if (!string.IsNullOrEmpty(final.FailedStep)) why = "step " + final.FailedStep + ": " + why;
            switch (exitCode)
            {
                case 0:
                    if (final.Result == "stopped")
                    {
                        reason = why;
                        return ProvisionVerdict.Stopped;
                    }
                    if (final.Result == "ok")
                    {
                        reason = final.RestartRequired ? "the distro must restart to apply /etc/wsl.conf" : "";
                        return final.RestartRequired ? ProvisionVerdict.DoneRestartDistro : ProvisionVerdict.Done;
                    }
                    reason = "provisioning ended with result \"" + final.Result + "\" instead of ok";
                    return ProvisionVerdict.Failed;
                case 3:
                    reason = "systemd is not running in the distro yet; it must restart";
                    return ProvisionVerdict.RestartDistroAndRerun;
                case 4:
                    reason = "another provisioning run is busy";
                    return ProvisionVerdict.Busy;
                case 2:
                    reason = "provisioning refused its options: " + why;
                    return ProvisionVerdict.Failed;
                default:
                    reason = why.Length > 0 ? why : "provisioning failed (exit " + Inv.S(exitCode) + ")";
                    return ProvisionVerdict.Failed;
            }
        }
    }

    // The commit a first install checked out (delta security review
    // 2026-10-16, S3). provision.sh reads --ref by its shape and checks HEAD
    // itself; the host checks the HEAD its final line reports too, and fails
    // closed. Until the first install has been verified, every commit a job
    // since the import asked for counts (a failed install, then a newer
    // Setup, may have cloned at either); once verified, later runs (repairs)
    // keep whatever the dashboard's updates moved the checkout to.
    public static class InstalledHead
    {
        static readonly Regex Sha = new Regex(@"^[0-9a-f]{40}\z");

        // The --ref an install's provision.sh arguments carry, or null.
        public static string RefOf(IList<string> args)
        {
            if (args == null) return null;
            for (int i = 0; i + 1 < args.Count; i++)
                if (args[i] == "--ref") return Sha.IsMatch(args[i + 1]) ? args[i + 1] : null;
            return null;
        }

        // The commits asked for since the import, this one added once.
        public static List<string> WithAsked(IList<string> asked, string commit)
        {
            var list = new List<string>();
            if (asked != null) foreach (string a in asked) if (a != null && Sha.IsMatch(a) && !list.Contains(a)) list.Add(a);
            if (commit != null && Sha.IsMatch(commit) && !list.Contains(commit)) list.Add(commit);
            return list;
        }

        // Why the reported HEAD is not acceptable, or null when it is one of
        // the commits Setup pinned.
        public static string Problem(string head, IList<string> asked)
        {
            if (string.IsNullOrEmpty(head)) return "provision.sh did not report the commit it installed";
            if (!Sha.IsMatch(head)) return "provision.sh reported a commit that is not a full SHA: " + head;
            if (asked == null || asked.Count == 0) return "no pinned commit to compare the installed one with";
            foreach (string a in asked) if (a == head) return null;
            return "Domovoi's code is at commit " + head + ", not the one Setup pinned (" + string.Join(", ", new List<string>(asked).ToArray())
                + "), so Setup stops here; uninstall Domovoi and run a Setup you trust";
        }
    }

    // What the pinned provision.sh can do, from its own text: the host
    // reads the exact file it runs (SHA-256 checked) before it runs it.
    public static class ProvisionScript
    {
        // --stop-before STEP (provision.sh after 2026-10-12): the host stops
        // the run before "start" and checks the PC's ports. An older pinned
        // script (44f0fff and before) has no such option and would refuse it
        // (exit 2), so the host only passes it when the usage text has it.
        public static bool SupportsStopBefore(string scriptText)
        {
            return scriptText != null && scriptText.IndexOf("\n  --stop-before STEP ", StringComparison.Ordinal) >= 0;
        }

        // --ai-models / --no-ai-models (provision.sh after 2026-10-21, "AI
        // chat and questions" optional): an older pinned script has neither
        // and would refuse them (exit 2). It always pulls the AI models,
        // which is the Yes answer.
        public static bool SupportsAiChoice(string scriptText)
        {
            return scriptText != null
                && scriptText.IndexOf("\n  --ai-models ", StringComparison.Ordinal) >= 0
                && scriptText.IndexOf("\n  --no-ai-models ", StringComparison.Ordinal) >= 0;
        }
    }

    // The AI models' download, for the question "Include AI chat and
    // questions?" (owner decision 2026-10-21: optional, included by
    // default). provision.sh --tier auto picks the standard models when the
    // distro's MemTotal is at least 22,528 MiB (provision-manifest.conf),
    // else the smaller ones; the distro sees about 97% of the memory cap
    // Setup gives it (WslConfig.MemoryCapGb: 24 GB reads 23.3 GiB), so a cap
    // of 23 GB or more is the standard tier: a PC with about 32 GB of
    // memory or more. Sizes: the question model, the voice-command model
    // and the vision model of each tier (2.0 + 2.5 + 1.9 GB on the smaller
    // tier, 2.0 + 5.2 + 1.9 GB on the standard one).
    public static class AiModels
    {
        public const string FloorGb = "6.4";
        public const string StandardGb = "9.1";
        public const int StandardMinCapGb = 23;

        public static string DownloadGb(int wslMemoryCapGb)
        {
            return wslMemoryCapGb >= StandardMinCapGb ? StandardGb : FloorGb;
        }
    }

    // The port check right before provision.sh starts Domovoi (owner fixes
    // 2026-10-12, item 2), as DomovoiHost runs it: while Pending, a pass
    // runs with --stop-before start; that pass ending "stopped" is the cue
    // to wait for the ports (WaitUntilFree), and the next pass runs without
    // it. Only for a pinned provision.sh that has the option.
    public sealed class PortGate
    {
        public const string Step = "start";
        public bool Pending;

        public PortGate(bool scriptHasStopBefore) { Pending = scriptHasStopBefore; }

        // The provision.sh arguments for the next pass.
        public List<string> Args(List<string> jobArgs)
        {
            var a = new List<string>(jobArgs);
            if (Pending) { a.Add("--stop-before"); a.Add(Step); }
            return a;
        }

        // A pass ended "stopped": true when that was this gate (now wait for
        // the ports, then run again without it); false when nobody asked
        // for a stop, which is a failure.
        public bool OnStopped()
        {
            if (!Pending) return false;
            Pending = false;
            return true;
        }

        // Looks at the ports every pollMs until no Windows program holds one
        // Domovoi needs. owners: the check (null: it can't be made, go on);
        // taken: told once per different answer while waiting; sleep: true
        // when the host is stopping. True: free; false: stopping.
        public static bool WaitUntilFree(Func<List<PortOwner>> owners, Action<string> taken, Func<int, bool> sleep, int pollMs)
        {
            string last = null;
            while (true)
            {
                List<PortOwner> now = owners();
                if (now == null || now.Count == 0) return true;
                string text = PortCheck.Summary(now);
                if (text != last) taken(text);
                last = text;
                if (sleep(pollMs)) return false;
            }
        }
    }

    // The installer's one progress bar across everything the host does.
    public static class HostProgress
    {
        public const string PhaseConfig = "config";
        public const string PhaseImport = "import";
        public const string PhaseProvision = "provision";
        public const string PhaseRestart = "restart";
        public const string PhaseVerify = "verify";
        public const string PhaseHealth = "health";
        public const string PhaseReady = "ready";

        public static int Overall(string phase, int provisionPercent)
        {
            if (provisionPercent < 0) provisionPercent = 0;
            if (provisionPercent > 100) provisionPercent = 100;
            switch (phase)
            {
                case PhaseConfig: return 0;
                case PhaseImport: return 2;
                case PhaseProvision: return 5 + provisionPercent * 88 / 100;
                case PhaseRestart: return 94;
                case PhaseVerify: return 95 + provisionPercent * 3 / 100;
                case PhaseHealth: return 99;
                case PhaseReady: return 100;
                default: return 0;
            }
        }
    }

    // The provision.sh options for one install (PLAN 4.8; the brief:
    // --internet, --media, --lan-ip, tier auto, from the pinned commit; AI
    // chat and questions since 2026-10-21).
    public sealed class ProvisionOptions
    {
        public string RepoUrl;
        public string Ref;
        // The release channel the install follows for updates (pins.iss
        // DomovoiChannel): stable, or main. provision.sh's --track-branch.
        public string TrackBranch = "stable";
        public string Internet;
        public string LanIp;
        // name -> Windows folder, in a stable order.
        public List<KeyValuePair<string, string>> Media = new List<KeyValuePair<string, string>>();
        public int HealthTimeoutSec = 900;
        // "Include AI chat and questions?" (Setup's page; Yes by default).
        // No: provision.sh --no-ai-models (no AI models pulled, AI_FEATURES
        // =false); speech recognition, the voices and Ollama itself are
        // installed either way.
        public bool IncludeAi = true;
        // The pinned provision.sh has --ai-models / --no-ai-models
        // (ProvisionScript.SupportsAiChoice).
        public bool ScriptHasAiChoice;
    }

    public static class ProvisionArgs
    {
        public static readonly string[] MediaNames = { "music", "pictures", "videos", "documents", "audiobooks" };

        static readonly Regex Sha = new Regex(@"^[0-9a-f]{40}\z");
        static readonly Regex RepoUrl = new Regex(@"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~+-]+)+\z");

        public static List<string> Build(ProvisionOptions o)
        {
            if (o == null) throw new ArgumentNullException("o");
            if (o.RepoUrl == null || !RepoUrl.IsMatch(o.RepoUrl)) throw new ArgumentException("repo URL must be https: " + o.RepoUrl);
            // A full commit SHA: the installer pins a commit, never a branch.
            if (o.Ref == null || !Sha.IsMatch(o.Ref)) throw new ArgumentException("ref must be a full 40-character commit SHA: " + o.Ref);
            if (o.TrackBranch != "stable" && o.TrackBranch != "main")
                throw new ArgumentException("the channel must be stable or main: " + o.TrackBranch);
            if (o.Internet != "always" && o.Internet != "sometimes" && o.Internet != "never")
                throw new ArgumentException("internet must be always, sometimes or never: " + o.Internet);
            if (o.HealthTimeoutSec < 60 || o.HealthTimeoutSec > 99999) throw new ArgumentException("health timeout out of range");
            var a = new List<string>();
            a.Add("--repo-url"); a.Add(o.RepoUrl);
            a.Add("--ref"); a.Add(o.Ref);
            a.Add("--track-branch"); a.Add(o.TrackBranch);
            a.Add("--internet"); a.Add(o.Internet);
            a.Add("--tier"); a.Add("auto");
            // The household's answer, always said out loud to a provision.sh
            // that takes it: with neither option, provision.sh keeps the
            // answer an earlier install left in domovoi/.env, so a Yes in a
            // later Setup over an earlier No would not be heard. An older
            // pinned script gets nothing for Yes (its arguments as before:
            // it pulls the models anyway) and can't do No.
            if (!o.IncludeAi)
            {
                if (!o.ScriptHasAiChoice)
                    throw new ArgumentException("this Setup's provision.sh always downloads the AI models (it has no --no-ai-models); run Setup again and include AI chat and questions, or use a newer Setup");
                a.Add("--no-ai-models");
            }
            else if (o.ScriptHasAiChoice) a.Add("--ai-models");
            if (!string.IsNullOrEmpty(o.LanIp))
            {
                if (!Lan.IsPrivateIPv4(o.LanIp)) throw new ArgumentException("LAN IP must be a private IPv4 address: " + o.LanIp);
                a.Add("--lan-ip"); a.Add(o.LanIp);
            }
            var seen = new HashSet<string>();
            foreach (var m in o.Media)
            {
                if (Array.IndexOf(MediaNames, m.Key) < 0) throw new ArgumentException("unknown media folder name: " + m.Key);
                if (!seen.Add(m.Key)) throw new ArgumentException("media folder given twice: " + m.Key);
                string why = MediaPaths.Problem(m.Value);
                if (why != null) throw new ArgumentException("media folder " + m.Key + ": " + why);
                a.Add("--media"); a.Add(m.Key + "=" + MediaPaths.Normalize(m.Value));
            }
            a.Add("--health-timeout"); a.Add(Inv.S(o.HealthTimeoutSec));
            return a;
        }
    }

    // The media root and its folders (PLAN 4.6; owner decision 2026-10-09):
    // a local Windows folder the household opens in Explorer. The rules
    // match what provision.sh accepts for --media on WSL.
    public static class MediaPaths
    {
        public const string DefaultRoot = @"C:\Users\Public\Domovoi";

        // The folder names under the root, with the media name each maps to.
        public static readonly string[][] Folders =
        {
            new[] { "music", "Music" },
            new[] { "pictures", "Pictures" },
            new[] { "videos", "Videos" },
            new[] { "documents", "Documents" },
            new[] { "audiobooks", "Audiobooks" },
        };

        static readonly Regex Drive = new Regex(@"^[A-Za-z]:\\");

        public static string Normalize(string path)
        {
            if (path == null) return null;
            string p = path.Trim().Replace('/', '\\');
            while (p.Length > 3 && p.EndsWith("\\", StringComparison.Ordinal)) p = p.Substring(0, p.Length - 1);
            return p;
        }

        // Why a path can't be a media folder, or null when it can.
        public static string Problem(string path)
        {
            string p = Normalize(path);
            if (string.IsNullOrEmpty(p)) return "no folder given";
            foreach (char c in p) if (char.IsControl(c)) return "control characters in the path";
            if (p.StartsWith(@"\\", StringComparison.Ordinal)) return "a network folder can't be used; pick a folder on a drive of this PC";
            if (!Drive.IsMatch(p)) return "pick a folder on a drive of this PC, such as " + DefaultRoot;
            string rest = p.Substring(3);
            if (rest.Length == 0) return "pick a folder, not a whole drive";
            if (rest.IndexOfAny(new[] { '"', '*', '?', '<', '>', '|', ':' }) >= 0) return "not a valid Windows folder name";
            foreach (string seg in rest.Split('\\'))
            {
                if (seg.Length == 0) return "the path has an empty folder name (\\\\)";
                if (seg == "." || seg == "..") return "no . or .. in the path";
                if (seg.EndsWith(" ", StringComparison.Ordinal) || seg.EndsWith(".", StringComparison.Ordinal)) return "a folder name can't end with a space or a dot";
            }
            return null;
        }

        public static List<KeyValuePair<string, string>> Under(string root)
        {
            string r = Normalize(root);
            var list = new List<KeyValuePair<string, string>>();
            foreach (var f in Folders) list.Add(new KeyValuePair<string, string>(f[0], r + "\\" + f[1]));
            return list;
        }

        static bool Same(string a, string b) { return string.Equals(a, b, StringComparison.OrdinalIgnoreCase); }

        static bool Below(string child, string parent)
        {
            return child.Length > parent.Length + 1 && child.StartsWith(parent + "\\", StringComparison.OrdinalIgnoreCase);
        }

        // Why a path can't be the media root because of what it is on this PC,
        // or null. domovoi-svc gets inherited Modify on the media root, and the
        // distro reaches it through drvfs, so it must never be a system or
        // program folder (the distro could then replace programs Windows runs
        // elevated), a profile or the profiles folder itself, or a folder
        // that holds any of those. A folder inside a profile (its Music, say)
        // is fine; its AppData is not.
        public static string SystemProblem(string path, SystemFolders sys)
        {
            string p = Normalize(path);
            foreach (string t in sys.Trees)
            {
                string n = Normalize(t);
                if (Same(p, n) || Below(p, n)) return "that is inside " + n + ", a system folder; pick a folder of your own, such as " + DefaultRoot;
                if (Below(n, p)) return "that folder holds " + n + ", a system folder; pick a folder of your own, such as " + DefaultRoot;
            }
            foreach (string x in sys.Exact)
            {
                string n = Normalize(x);
                if (Same(p, n)) return n + " is a profile or system folder itself; pick or make a folder inside it, such as " + DefaultRoot;
                if (Below(n, p)) return "that folder holds " + n + "; pick a folder of your own, such as " + DefaultRoot;
            }
            return null;
        }
    }

    // The folders a media root may not be, be inside of (Trees) or hold
    // (both). The helper fills it from this PC (Folders.SystemFoldersHere).
    public sealed class SystemFolders
    {
        public List<string> Trees = new List<string>();
        public List<string> Exact = new List<string>();

        // A typical Windows 11 PC, for the tests.
        public static SystemFolders Typical()
        {
            var s = new SystemFolders();
            s.Trees.AddRange(new[]
            {
                @"C:\Windows", @"C:\Program Files", @"C:\Program Files (x86)", @"C:\ProgramData",
                @"C:\Users\Default", @"C:\Users\domovoi-svc", @"C:\Users\alex\AppData", @"C:\Users\Public\AppData",
                @"C:\$Recycle.Bin", @"C:\System Volume Information", @"C:\Recovery",
            });
            s.Exact.AddRange(new[] { @"C:\Users", @"C:\Users\Public", @"C:\Users\alex" });
            return s;
        }
    }
}
