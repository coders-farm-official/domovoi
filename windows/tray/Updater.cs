// "Update Domovoi" on the Windows side (design note
// WINDOWS-ONE-UPDATE-2026-10-22; docs/WINDOWS_HOST.md, "Updates and the
// release channel").
//
// The tray polls the core over loopback (GET /v1/windows/update, every 30
// s): what Windows side is installed, which release the Linux side
// includes, whether a Windows step is needed, and an admin's request from
// the dashboard. A request from a browser on this PC starts the Windows
// step at once (Windows' permission prompt comes next); one from another
// device shows a notification, and a click on it starts the step. "Update
// Domovoi..." in the menu does both halves: the Linux part runs first, in
// the elevated program, then the Windows part.
//
// The Windows step: download the release's DomovoiSetup.exe, its manifest
// and both signatures from the pinned repository into a private run folder
// (UpdateRun), check them (SetupCheck: the pinned release key, the release
// the core named, newer than this one), and start that same, still held
// file elevated as a silent repair. Nothing runs whose check fails, and the
// reason is said in plain words. Setup ends every tray to replace its file,
// so the outcome goes into pending.txt and the next tray reports it.
//
// WindowsStep is pure orchestration over four interfaces (the core, the
// release download, Windows' elevation, the clock and files), so the tests
// run it whole against fakes.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;

namespace Domovoi.Windows.Tray
{
    // The outcome words the core keeps (domovoi/windows_side.py OUTCOMES).
    public static class Outcome
    {
        public const string Ok = "ok";
        public const string NothingNew = "nothing_new";
        public const string Declined = "declined";
        public const string DownloadFailed = "download_failed";
        public const string NoSignedSetup = SetupCheck.NoSignedSetup;
        public const string BadSignature = SetupCheck.BadSignature;
        public const string NotNewer = SetupCheck.NotNewer;
        public const string NoKey = SetupCheck.NoKey;
        public const string RepairFailed = "repair_failed";
        public const string LinuxFailed = "linux_failed";
        public const string InternetOff = "internet_off";
        public const string NotInstalled = "not_installed";
        public const string Failed = "failed";
    }

    public interface IUpdateServer
    {
        UpdateState State();
        bool Report(string requestId, string outcome, string detail, string log, string version);
    }

    public interface IReleaseFiles
    {
        // The asset's bytes (at most max), or null with notFound true when
        // the release has no such file, or why it couldn't be had.
        byte[] Get(string tag, string asset, long max, out bool notFound, out string problem);
        // The same, into a file.
        bool Save(string tag, string asset, long max, string path, out bool notFound, out string problem);
    }

    public interface IElevated
    {
        int WaitForExit();     // the exit code
    }

    public interface IElevator
    {
        // Starts exe elevated (Windows asks first). Null with declined true
        // when the person said no; null with why for anything else.
        IElevated Start(string exe, string args, out bool declined, out string why);
    }

    public sealed class StepResult
    {
        public string Outcome;      // Outcome.*
        public string Detail;       // plain words, or null
        public string Log;          // a log to look at, or null
        public string Version;      // the release that was installed (ok)
        public bool Launched;       // an elevated program ran
        public IElevated Process;   // while it runs

        public bool Ok { get { return Outcome == Tray.Outcome.Ok || Outcome == Tray.Outcome.NothingNew; } }
    }

    public sealed class StepEnv
    {
        public IReleaseFiles Files;
        public IElevator Elevator;
        public byte[] Key;              // ReleaseKey.Pinned()
        public string Installed;        // this tray's own version
        public string Helper;           // the installed DomovoiSetupHelper.exe, or null
        public string UpdatesRoot;      // %LOCALAPPDATA%\Domovoi\updates
        public bool Dev;                // a developer run: never starts anything elevated
        public Func<DateTime> Now = delegate { return DateTime.UtcNow; };
    }

    public static class WindowsStep
    {
        public const long MaxSetupBytes = 300L * 1024 * 1024;
        const long MaxSmall = 64 * 1024;

        // Everything up to the elevated start, then the start; the caller
        // waits on result.Process (when Launched) and then calls Finish.
        // linuxFirst: the tray's own "Update Domovoi" (the Linux part runs
        // first, elevated); false for a dashboard request (the Linux part is
        // done).
        public static StepResult Start(StepEnv env, UpdateState s, bool linuxFirst, out string runDir)
        {
            runDir = null;
            if (s == null) return R(Outcome.Failed, "Domovoi on this PC isn't answering, so there's nothing to update from. Check that it's running (this icon says so), then try again.");
            if (s.Internet == "never") return R(Outcome.InternetOff, "Internet access is turned off for this Domovoi (Settings > Privacy > Internet), and updates come from Domovoi's releases on the internet.");
            if (env.Helper == null) return R(Outcome.NotInstalled, "This PC's Domovoi wasn't installed by Domovoi Setup, so the tray can't update it. Run the newer DomovoiSetup.exe by hand.");
            if (s.LinuxBusy) return R(Outcome.Failed, "Domovoi is updating or restarting right now. Try again when it's done.");
            bool windows = s.StepNeeded && s.TargetTag != null;
            if (!windows && !linuxFirst) return R(Outcome.NothingNew, "The Windows side is already up to date.");
            runDir = UpdateRun.NewRunDir(env.UpdatesRoot, env.Now());
            if (!windows)
            {
                // Only the Linux part: the installed helper, elevated.
                return Launch(env, env.Helper, UpdateRun.LinuxArguments(runDir), null, null);
            }
            if (env.Key == null) return R(Outcome.NoKey, "This Windows side pins no release key, so it can't check a downloaded Setup. Run the newer DomovoiSetup.exe by hand.");
            bool missing;
            string why;
            byte[] manifest = env.Files.Get(s.TargetTag, ReleaseNames.Manifest, MaxSmall, out missing, out why);
            if (manifest == null) return missing ? NoSigned(s) : R(Outcome.DownloadFailed, "Domovoi " + s.TargetVersion + "'s Setup couldn't be downloaded (" + why + "). Nothing was run. Try again later.");
            byte[] manifestSig = env.Files.Get(s.TargetTag, ReleaseNames.ManifestSig, MaxSmall, out missing, out why);
            if (manifestSig == null) return missing ? NoSigned(s) : R(Outcome.DownloadFailed, "Domovoi " + s.TargetVersion + "'s Setup couldn't be downloaded (" + why + "). Nothing was run. Try again later.");
            byte[] setupSig = env.Files.Get(s.TargetTag, ReleaseNames.SetupSig, MaxSmall, out missing, out why);
            if (setupSig == null) return missing ? NoSigned(s) : R(Outcome.DownloadFailed, "Domovoi " + s.TargetVersion + "'s Setup couldn't be downloaded (" + why + "). Nothing was run. Try again later.");
            string exe = Path.Combine(runDir, ReleaseNames.SetupAsset);
            if (!env.Files.Save(s.TargetTag, ReleaseNames.SetupAsset, MaxSetupBytes, exe, out missing, out why))
                return missing ? NoSigned(s) : R(Outcome.DownloadFailed, "Domovoi " + s.TargetVersion + "'s Setup couldn't be downloaded (" + why + "). Nothing was run. Try again later.");
            // From here the file can't change: checked and started through
            // the same handle's bytes.
            HeldFile held = new HeldFile(exe);
            try
            {
                byte[] bytes = held.ReadAll(MaxSetupBytes);
                SetupManifest m;
                string code;
                string bad = SetupCheck.Run(new SetupExpectation { Tag = s.TargetTag, Commit = s.TargetCommit, Installed = env.Installed, Key = env.Key },
                    manifest, Ascii(manifestSig), bytes, Ascii(setupSig), out m, out code);
                if (bad != null)
                {
                    held.Dispose();
                    held = null;
                    TryDelete(exe);
                    if (code == Outcome.NotNewer) return R(Outcome.NotNewer, "Domovoi didn't run the downloaded Setup: " + bad + ".");
                    if (code == Outcome.NoSignedSetup) return NoSigned(s);
                    return R(code ?? Outcome.BadSignature, "Domovoi didn't run the downloaded Setup: " + bad + ". It was deleted.");
                }
                StepResult r = Launch(env, exe, UpdateRun.SetupArguments(runDir, linuxFirst), Path.Combine(runDir, UpdateRun.LogFile), m.Version);
                return r;
            }
            finally
            {
                if (held != null) held.Dispose();
            }
        }

        static StepResult NoSigned(UpdateState s)
        {
            return R(Outcome.NoSignedSetup, "Domovoi " + s.TargetVersion + " has no signed Windows Setup, so the Windows side can't update from it. Nothing was run.");
        }

        static StepResult Launch(StepEnv env, string exe, string args, string log, string version)
        {
            if (env.Dev) return R(Outcome.Failed, "A developer run of the tray never starts anything elevated (it would have run " + Path.GetFileName(exe) + " " + args + ").");
            bool declined;
            string why;
            IElevated p = env.Elevator.Start(exe, args, out declined, out why);
            if (p == null)
            {
                if (declined) return R(Outcome.Declined, null);
                return R(Outcome.Failed, "Windows didn't start " + Path.GetFileName(exe) + ": " + why + ".");
            }
            return new StepResult { Launched = true, Process = p, Log = log, Version = version };
        }

        // After the elevated program exits: its result file says how it went.
        public static StepResult Finish(string runDir, int exitCode, bool setup, string version)
        {
            RunResult rr = RunResult.Read(Path.Combine(runDir, UpdateRun.ResultFile));
            string log = setup ? Path.Combine(runDir, UpdateRun.LogFile) : null;
            if (rr != null && rr.Log != null) log = rr.Log;
            if (rr != null && rr.Ok && exitCode == 0)
            {
                if (!setup && rr.Linux == "nothing_new") return new StepResult { Outcome = Outcome.NothingNew, Detail = "Domovoi is up to date." };
                return new StepResult { Outcome = Outcome.Ok, Version = setup ? (rr.Version ?? version) : null, Log = log };
            }
            string err = rr != null && rr.Error != null ? rr.Error : (rr == null ? "it ended without saying how it went (exit code " + Inv.S(exitCode) + ")" : "exit code " + Inv.S(exitCode));
            if (rr != null && rr.Linux == "failed")
                return new StepResult { Outcome = Outcome.LinuxFailed, Detail = "The Linux part didn't update: " + err + "." + (setup ? " Nothing on Windows was changed." : ""), Log = log };
            return new StepResult { Outcome = setup ? Outcome.RepairFailed : Outcome.Failed, Detail = (setup ? "Setup couldn't update the Windows side: " : "The update didn't finish: ") + err + ".", Log = log };
        }

        static StepResult R(string outcome, string detail) { return new StepResult { Outcome = outcome, Detail = detail }; }

        static string Ascii(byte[] b) { return Encoding.ASCII.GetString(b); }

        static void TryDelete(string p)
        {
            try { File.Delete(p); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        // What the notification or the message says.
        public static string Words(StepResult r, bool linuxAlreadyDone)
        {
            switch (r.Outcome)
            {
                case Outcome.Ok: return r.Version != null ? "Domovoi is updated: the Windows side is now " + r.Version + "." : "Domovoi is updated.";
                case Outcome.NothingNew: return r.Detail ?? "Domovoi is up to date.";
                case Outcome.Declined:
                    return linuxAlreadyDone
                        ? "Windows didn't get an administrator's permission, so the update is half done: the Linux part is updated, the Windows part isn't. Choose \"Update Domovoi\" in this icon's menu to try again."
                        : "Windows didn't get an administrator's permission, so nothing was updated. Choose \"Update Domovoi\" in this icon's menu to try again.";
                default:
                    return (r.Detail ?? "The update didn't finish.") + (r.Log != null ? " Setup's log: " + r.Log : "");
            }
        }
    }

    // What the tray does with a poll's answer.
    public enum UpdateAction { Nothing, StartNow, Notify }

    public static class UpdateDecision
    {
        // A dashboard request the tray hasn't acted on: from a browser on
        // this PC, the Windows step at once (Windows' permission prompt is
        // next, in front of the person who just clicked); from another
        // device, a notification to click. Nothing while a step runs, while
        // an earlier run's outcome hasn't reached the core, or when the
        // request no longer needs a step.
        public static UpdateAction OnState(UpdateState s, string seenRequest, bool stepRunning, bool pendingRun)
        {
            if (s == null || s.RequestId == null || s.RequestId == seenRequest || stepRunning || pendingRun) return UpdateAction.Nothing;
            if (!s.StepNeeded || s.LinuxBusy) return UpdateAction.Nothing;
            return s.RequestOnThisPc ? UpdateAction.StartNow : UpdateAction.Notify;
        }
    }

    // pending.txt: the run a tray started, until its outcome reaches the
    // core (Setup ends this tray halfway through; the next one finishes).
    public sealed class PendingRun
    {
        public string Run, RequestId, Kind, Started, Version;
        public string Outcome, Detail, Log;
        public bool Shown;

        public static string PathIn(string root) { return Path.Combine(root, UpdateRun.PendingFile); }

        public static PendingRun Load(string root)
        {
            string p = PathIn(root);
            if (!File.Exists(p)) return null;
            var r = new PendingRun();
            foreach (string raw in File.ReadAllLines(p, Encoding.UTF8))
            {
                int i = raw.IndexOf('=');
                if (i <= 0) continue;
                string k = raw.Substring(0, i), v = raw.Substring(i + 1);
                if (v.Length == 0) v = null;
                switch (k)
                {
                    case "run": r.Run = v; break;
                    case "request": r.RequestId = v; break;
                    case "kind": r.Kind = v; break;
                    case "started": r.Started = v; break;
                    case "version": r.Version = v; break;
                    case "outcome": r.Outcome = v; break;
                    case "detail": r.Detail = v; break;
                    case "log": r.Log = v; break;
                    case "shown": r.Shown = v == "1"; break;
                }
            }
            return UpdateRun.IsRunName(r.Run) ? r : null;
        }

        public void Save(string root)
        {
            var sb = new StringBuilder();
            Line(sb, "run", Run); Line(sb, "request", RequestId); Line(sb, "kind", Kind); Line(sb, "started", Started);
            Line(sb, "version", Version); Line(sb, "outcome", Outcome); Line(sb, "detail", Detail); Line(sb, "log", Log);
            Line(sb, "shown", Shown ? "1" : null);
            Directory.CreateDirectory(root);
            AtomicFile.WriteAllText(PathIn(root), sb.ToString());
        }

        public static void Clear(string root)
        {
            try { File.Delete(PathIn(root)); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        static void Line(StringBuilder sb, string k, string v)
        {
            sb.Append(k).Append('=').Append((v ?? "").Replace("\r", " ").Replace("\n", " ")).Append("\r\n");
        }
    }

    // -- the real ones ---------------------------------------------------

    public sealed class HttpUpdateServer : IUpdateServer
    {
        readonly string core;

        public HttpUpdateServer(string coreUrl) { core = Probe.Loopback((coreUrl ?? Loopback.Core).TrimEnd('/')); }

        public UpdateState State()
        {
            JsonReply r = Loopback.Send("GET", core + Loopback.StatePath + "?tray=" + Uri.EscapeDataString(BuildVersion.Version ?? ""), null, 10000);
            return r != null && r.Status == 200 ? UpdateState.From(r.Body) : null;
        }

        public bool Report(string requestId, string outcome, string detail, string log, string version)
        {
            string json = "{\"request_id\": " + (requestId == null ? "null" : Loopback.Quote(requestId))
                + ", \"outcome\": " + Loopback.Quote(outcome)
                + ", \"detail\": " + (detail == null ? "null" : Loopback.Quote(detail.Length > 400 ? detail.Substring(0, 400) : detail))
                + ", \"log\": " + (log == null ? "null" : Loopback.Quote(log))
                + ", \"version\": " + (version == null ? "null" : Loopback.Quote(version)) + "}";
            JsonReply r = Loopback.Send("POST", core + Loopback.ResultPath, json, 10000);
            return r != null && r.Status == 200;
        }
    }

    // A release's files from GitHub (ReleaseNames.Url): https only, a few
    // redirects (GitHub hands assets to its download host), the system's
    // proxy settings, a size cap.
    public sealed class GitHubReleaseFiles : IReleaseFiles
    {
        public const int TimeoutMs = 60000;

        public byte[] Get(string tag, string asset, long max, out bool notFound, out string problem)
        {
            var ms = new MemoryStream();
            if (!Fetch(tag, asset, max, ms, out notFound, out problem)) return null;
            return ms.ToArray();
        }

        public bool Save(string tag, string asset, long max, string path, out bool notFound, out string problem)
        {
            using (var fs = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                return Fetch(tag, asset, max, fs, out notFound, out problem);
        }

        static bool Fetch(string tag, string asset, long max, Stream into, out bool notFound, out string problem)
        {
            notFound = false;
            problem = null;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(ReleaseNames.Url(tag, asset));
                req.Method = "GET";
                req.Timeout = TimeoutMs;
                req.ReadWriteTimeout = TimeoutMs;
                req.AllowAutoRedirect = true;
                req.MaximumAutomaticRedirections = 5;
                req.UserAgent = "DomovoiTray/" + (BuildVersion.Version ?? "unknown");
                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    if (resp.ResponseUri == null || resp.ResponseUri.Scheme != Uri.UriSchemeHttps) { problem = "the download left https"; return false; }
                    if (resp.ContentLength > max) { problem = "the file is larger than expected"; return false; }
                    using (Stream s = resp.GetResponseStream())
                    {
                        var buf = new byte[81920];
                        long total = 0;
                        int n;
                        while ((n = s.Read(buf, 0, buf.Length)) > 0)
                        {
                            total += n;
                            if (total > max) { problem = "the file is larger than expected"; return false; }
                            into.Write(buf, 0, n);
                        }
                    }
                }
                return true;
            }
            catch (WebException e)
            {
                var r = e.Response as HttpWebResponse;
                if (r != null && r.StatusCode == HttpStatusCode.NotFound) { notFound = true; problem = "not in the release"; r.Close(); return false; }
                if (r != null) r.Close();
                problem = r != null ? "GitHub answered " + Inv.S((int)r.StatusCode) : e.Message;
                return false;
            }
            catch (IOException e) { problem = e.Message; return false; }
        }
    }

    public sealed class ShellElevator : IElevator
    {
        sealed class Running : IElevated
        {
            readonly Process p;
            public Running(Process p) { this.p = p; }
            public int WaitForExit()
            {
                using (p) { p.WaitForExit(); return p.ExitCode; }
            }
        }

        public IElevated Start(string exe, string args, out bool declined, out string why)
        {
            declined = false;
            why = null;
            var psi = new ProcessStartInfo(exe, args)
            {
                UseShellExecute = true,
                Verb = "runas",
                WorkingDirectory = Environment.SystemDirectory,
            };
            try
            {
                Process p = Process.Start(psi);
                if (p == null) { why = "Windows didn't start it"; return null; }
                return new Running(p);
            }
            catch (Win32Exception e)
            {
                if (e.NativeErrorCode == 1223) { declined = true; return null; }
                why = e.Message;
                return null;
            }
        }
    }
}
