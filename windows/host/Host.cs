// DomovoiHost's work (PLAN 5.1, the step-2 subset): runs as the hidden
// account domovoi-svc, which owns the private WSL distro "domovoi".
//
//   1. write the account's .wslconfig (from job.json) before WSL starts;
//   2. import the pinned Ubuntu image as "domovoi" when it isn't there;
//   3. hold the distro up: `wsl.exe -d domovoi --exec sleep infinity`,
//      restarted with backoff 5 s .. 5 min;
//   4. run provision.sh (the pinned copy) as root inside it, its progress
//      lines into status.json for Setup's progress bar, and handle its
//      exits: restart_required and exit 3 (terminate, start, run again),
//      4 (busy, wait), a lost final line (run again once); right before
//      its "start" step, wait until the ports Domovoi needs are free on
//      Windows (when the pinned provision.sh has --stop-before);
//   5. wait until both health URLs answer from Windows;
//   6. answer one-off requests (the setup code, the household's AI answer,
//      and an administrator's Linux update: WindowsSide.cs);
//   6a. tell the Linux side which Windows side runs it (WindowsSide.cs:
//      /var/lib/domovoi-windows/side.json, written as root at every
//      bring-up), for the dashboard's Version card;
//   6b. copy the core's journal out to logs\core.log for "Save logs"
//      (CoreLog.cs, CoreJournal.cs): once ready, every 10 minutes, and on
//      the helper's request;
//   6c. the household's hardware limits (HardwareLimits.cs): every 30 s,
//      read the core's request as the service user, keep a changed choice
//      and write it into .wslconfig (memory=, processors=), restart the
//      Linux system once for a new restart id, and tell the Linux side
//      this PC's RAM, processors and the limits
//      (/var/lib/domovoi-windows/limits.json, as root);
//   6d. the media folders (MediaFolders.cs, MediaPrep.cs): every 30 s,
//      tell the Linux side (/var/lib/domovoi-windows/media.json, as root)
//      and the tray (host\out\media-folders.json) which Windows folders
//      Domovoi uses, and carry out a move the dashboard asked for: the
//      folder checked and made, the distro's fstab block rewritten, the
//      Linux system restarted (the limits' restart rules);
//   7. with the start parameter "unregister": unregister the distro, end
//      this account's own WSL session (wsl --shutdown as domovoi-svc, which
//      leaves every other account's WSL alone), and stop (the uninstaller's
//      way to delete it as its owner and free the account's profile).
//
// Nothing here runs a Windows program but wsl.exe, and nothing it reads
// from the distro is executed on Windows.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Security.Principal;
using System.Text;
using System.Threading;

namespace Domovoi.Windows.Host
{
    sealed class Host
    {
        const string InDistroDir = "/var/lib/domovoi-installer";
        const string InDistroScript = InDistroDir + "/provision.sh";

        readonly ManualResetEvent stop;
        readonly Log log = new Log(Paths.HostLog, 5L * 1024 * 1024);
        readonly Log plog = new Log(Paths.ProvisionLog, 20L * 1024 * 1024);
        readonly HostStatus status = new HostStatus();
        readonly object statusGate = new object();
        readonly HashSet<string> answered = new HashSet<string>();

        Process holder;
        DateTime holderStarted;
        DateTime holderNext = DateTime.MinValue;
        int holderBackoffSec = 5;
        Process provision;
        // The pinned provision.sh takes --stop-before (ProvisionScript).
        bool canStopBeforeStart;
        DateTime lastStatusWrite = DateTime.MinValue;
        string lastJobError;
        // logs\core.log: when the periodic copy is next due, and the last
        // failure logged (one host.log line per change, not every 10 min).
        DateTime coreLogNext = DateTime.MinValue;
        string coreLogLastError;
        // The household's hardware limits (HardwareLimits.cs): when the
        // request is next read, the report last written (its text without
        // the time), the last problem logged, and when the last restart for
        // them ran and how it went. Which restart ids to act on:
        // HardwareLimits.DecideRestart.
        DateTime limitsNext = DateTime.MinValue;
        string limitsLastReport;
        string limitsLastProblem;
        // The last restart of Domovoi's Linux system for new limits or new
        // media folders: at most one per HardwareLimits.RestartGap.
        DateTime lastLinuxRestart = DateTime.MinValue;
        string limitsRestartState = "none";
        // The media folders (MediaFolders.cs): when the move request is next
        // read, and the report and tray list last written.
        DateTime mediaNext = DateTime.MinValue;
        string mediaLastReport;
        string mediaLastTrayMap;
        string mediaLastProblem;

        public Host(ManualResetEvent stop)
        {
            this.stop = stop;
            status.HostPid = Process.GetCurrentProcess().Id;
        }

        bool Stopping { get { return stop.WaitOne(0); } }

        bool Sleep(int ms) { return stop.WaitOne(ms); }

        public void Info(string m) { log.Write(m); }

        // -- status.json ------------------------------------------------

        void Status(string state, string phase, int phasePercent, string message)
        {
            lock (statusGate)
            {
                status.State = state;
                status.Phase = phase;
                int overall = HostProgress.Overall(phase, phasePercent);
                // The bar never moves back within one attempt.
                if (state == "failed" || overall >= status.Percent || phase == HostProgress.PhaseConfig) status.Percent = overall;
                status.Message = message ?? "";
                if (state != "failed") { status.Error = null; status.FailedStep = null; }
            }
            WriteStatus(true);
        }

        void Fail(string message, string failedStep)
        {
            lock (statusGate)
            {
                status.State = "failed";
                status.Error = message;
                status.FailedStep = failedStep;
                status.Message = "Setup could not finish: " + message;
            }
            log.Write("FAILED: " + message + (failedStep != null ? " (step " + failedStep + ")" : ""));
            WriteStatus(true);
        }

        void WriteStatus(bool force)
        {
            lock (statusGate)
            {
                if (!force && (DateTime.UtcNow - lastStatusWrite).TotalMilliseconds < 500) return;
                status.Updated = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
                status.Holder = holder != null && !SafeExited(holder) ? "running" : "stopped";
                try
                {
                    Json.WriteFile(Paths.Status, status.ToJson());
                    lastStatusWrite = DateTime.UtcNow;
                }
                catch (IOException e) { log.Write("can't write status.json: " + e.Message); }
                catch (UnauthorizedAccessException e) { log.Write("can't write status.json: " + e.Message); }
            }
        }

        static bool SafeExited(Process p)
        {
            try { return p.HasExited; }
            catch (InvalidOperationException) { return true; }
        }

        // -- the provisioned record (survives restarts of the service) --

        string StateFile { get { return Path.Combine(Paths.HostOut, "host-state.json"); } }

        HostStateStore Store { get { return new HostStateStore(StateFile); } }

        Dictionary<string, object> ReadState() { return Store.Read(); }

        // Read-modify-write (HostStateStore.cs): false, logged, when it
        // didn't land; a file that can't be read is never overwritten.
        bool UpdateState(string key, object value)
        {
            string why;
            var changes = new Dictionary<string, object>();
            changes[key] = value;
            if (Store.Update(changes, DateTime.UtcNow, out why)) return true;
            log.Write("can't record " + key + ": " + why);
            return false;
        }

        bool AlreadyProvisioned(HostJob job)
        {
            var d = ReadState();
            return Json.Int(d, "generation") == job.Generation && Json.Int(d, "attempt") == job.Attempt && Json.Str(d, "result") == "ok";
        }

        void RecordProvisioned(HostJob job, string result)
        {
            var d = new Dictionary<string, object>();
            d["generation"] = job.Generation;
            d["attempt"] = job.Attempt;
            d["result"] = result;
            string why;
            if (!Store.Update(d, DateTime.UtcNow, out why)) log.Write("can't record the result: " + why);
        }

        // The distro was imported once: its disk is the household's data and
        // is never deleted or replaced by an import again.
        bool ImportedBefore() { return Json.Bool(ReadState(), "imported") == true; }

        // -- the main loop ----------------------------------------------

        static void CheckAccount()
        {
            // WSL can't run as LocalSystem (microsoft/WSL#11280), and the
            // distro must belong to the hidden account and nobody else.
            string me = WindowsIdentity.GetCurrent().Name;
            int slash = me.LastIndexOf('\\');
            string user = slash >= 0 ? me.Substring(slash + 1) : me;
            if (!string.Equals(user, Paths.AccountName, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("DomovoiHost must run as " + Paths.AccountName + ", not " + me);
        }

        public void Run()
        {
            CheckAccount();
            log.Write("DomovoiHost started as " + WindowsIdentity.GetCurrent().Name + " (pid " + Inv.S(status.HostPid) + ")");
            Status("starting", HostProgress.PhaseConfig, 0, "Starting");
            int lastGen = -1, lastAttempt = -1;
            bool ready = false;
            HostJob job = null;
            while (!Stopping)
            {
                // WSL went away (WslOutage.cs): never exit, wait for it and
                // bring Domovoi up again.
                try
                {
                    if (recovery.Down)
                    {
                        if (job != null && WaitForWsl(job)) ready = true;
                        if (Sleep(1000)) break;
                        continue;
                    }
                    job = ReadJob();
                    if (job == null)
                    {
                        if (status.State != "waiting") Status("waiting", HostProgress.PhaseConfig, 0, "Waiting for Setup");
                        if (Sleep(5000)) break;
                        continue;
                    }
                    lock (statusGate) { status.Generation = job.Generation; status.Attempt = job.Attempt; }
                    bool newJob = job.Generation != lastGen || job.Attempt != lastAttempt;
                    if (newJob)
                    {
                        lastGen = job.Generation;
                        lastAttempt = job.Attempt;
                        ready = false;
                        lock (statusGate) status.Percent = 0;
                        ready = BringUp(job);
                        if (ready) wslVersionUp = WslOutage.ExeVersion(Paths.WslExe);
                        // At the service's start for a job already
                        // provisioned (every boot), a bring-up that failed is
                        // tried again; a first install's failure waits for
                        // Setup (review 2026-11-10).
                        else if (!Stopping && WslOutage.RetryFailedBringUp(true, AlreadyProvisioned(job)))
                        {
                            DomovoiDown("Domovoi didn't come up at the start", WslGoneNow());
                            continue;
                        }
                    }
                    else
                    {
                        // The holder ended while Domovoi ran: only the distro
                        // stopped (the holder's restart starts it again), or
                        // WSL itself went away (an update of WSL, say).
                        if (ready && holder != null && SafeExited(holder) && WslGoneNow())
                        {
                            ready = false;
                            DomovoiDown("the holder ended and WSL isn't answering", true);
                            continue;
                        }
                        KeepHolder();
                        if (ready && holder != null && SafeExited(holder))
                            WriteStatus(false);
                    }
                    ProcessRequests(ready);
                    if (ready) PeriodicCoreLog();
                    if (ready)
                    {
                        ready = PollLimits(job);
                        if (!ready && !Stopping)
                        {
                            DomovoiDown("Domovoi didn't come up after the restart for its new limits", WslGoneNow());
                            continue;
                        }
                    }
                    if (ready)
                    {
                        ready = PollMedia(job);
                        if (!ready && !Stopping)
                        {
                            DomovoiDown("Domovoi didn't come up after the restart for its new media folders", WslGoneNow());
                            continue;
                        }
                    }
                }
                // Only a wsl.exe that can't be started means WSL went away
                // (WslOutage.IsWslFailure); anything else is a bug, which
                // stops the host, and the SCM starts it again.
                catch (System.ComponentModel.Win32Exception e)
                {
                    ready = false;
                    DomovoiDown("wsl.exe could not be run: " + e.Message, true);
                }
                if (DateTime.UtcNow - lastStatusWrite > TimeSpan.FromSeconds(10)) WriteStatus(true);
                if (Sleep(1000)) break;
            }
            log.Write("DomovoiHost stopping");
        }

        // -- Domovoi down: WSL went away, or a bring-up failed (WslOutage.cs)

        readonly Recovery recovery = new Recovery();
        readonly MsiClock msiClock = new MsiClock();
        // wsl.exe's version when Domovoi last came up: another one after an
        // outage means Windows updated WSL.
        string wslVersionUp;

        WslFacts LookAtWsl()
        {
            WslFacts f = WslOutage.Look(Paths.WslExe);
            f.MsiBusyFor = msiClock.For(f.MsiBusy, DateTime.UtcNow);
            if (WslOutage.MayAsk(f)) f.Answers = AskWsl();
            return f;
        }

        // Whether WSL itself is in trouble: the reads first, wsl.exe only
        // when asking it can't get in an update's way.
        bool WslGoneNow() { return WslOutage.Gone(LookAtWsl()); }

        bool? AskWsl()
        {
            try { return Wsl.Registered() != null; }
            catch (System.ComponentModel.Win32Exception) { return false; }
        }

        void DomovoiDown(string why, bool wslTrouble)
        {
            KillHolder();
            if (!recovery.Down) log.Write("Domovoi is down (" + why + "); " + (wslTrouble ? "waiting for WSL, then " : "") + "starting it again");
            recovery.Lost(DateTime.UtcNow, wslTrouble);
            WslFacts f = WslOutage.Look(Paths.WslExe);
            Status("waiting", HostProgress.PhaseConfig, 0, WslOutage.DownMessage(f, wslVersionUp, wslTrouble));
        }

        // One look, when it is due. True once Domovoi is up again.
        bool WaitForWsl(HostJob job)
        {
            if (!recovery.Due(DateTime.UtcNow)) return false;
            WslFacts f = LookAtWsl();
            if (WslOutage.Decide(f) == WslProbe.Wait)
            {
                recovery.Failed(DateTime.UtcNow);
                string message = WslOutage.DownMessage(f, wslVersionUp, true);
                if (status.Message != message) Status("waiting", HostProgress.PhaseConfig, 0, message);
                return false;
            }
            log.Write("WSL answers" + (f.Version != null ? " (wsl.exe " + f.Version + ")" : "") + "; starting Domovoi again (try " + Inv.S(recovery.Attempts + 1) + ")");
            holderNext = DateTime.MinValue;
            holderBackoffSec = 5;
            limitsNext = DateTime.MinValue;
            coreLogNext = DateTime.MinValue;
            if (BringUp(job))
            {
                recovery.Recovered();
                wslVersionUp = f.Version;
                log.Write("Domovoi is running again");
                return true;
            }
            // BringUp said why (status.json); look again later.
            recovery.Failed(DateTime.UtcNow);
            return false;
        }

        HostJob ReadJob()
        {
            try
            {
                var d = Json.ReadFile(Paths.Job);
                if (d == null) return null;
                var j = HostJob.FromJson(d);
                lastJobError = null;
                return j;
            }
            catch (Exception e)
            {
                if (e is FormatException || e is IOException || e is UnauthorizedAccessException)
                {
                    if (lastJobError != e.Message) log.Write("job.json unreadable: " + e.Message);
                    lastJobError = e.Message;
                    return null;
                }
                throw;
            }
        }

        // One attempt at everything; true when Domovoi answers.
        bool BringUp(HostJob job)
        {
            log.Write(Inv.F("job generation {0} attempt {1}", job.Generation, job.Attempt));
            // What this job's provision.sh reports, not an earlier one's.
            lock (statusGate) status.Ai = null;
            Status("config", HostProgress.PhaseConfig, 0, "Configuring WSL for Domovoi");
            if (!WriteWslConfig(job)) return false;

            // WSL can be busy or updating, at boot especially: an answer we
            // can't read is "ask again", never "no distro".
            bool? reg = Wsl.Registered();
            while (reg == null)
            {
                Status("waiting", HostProgress.PhaseConfig, 0, "Waiting for WSL to answer");
                log.Write("wsl.exe --list gave no usable answer; asking again in 15 s");
                if (Sleep(15000)) return false;
                reg = Wsl.Registered();
            }
            if (!reg.Value && !Import(job)) return false;
            if (Stopping) return false;
            if (!AlreadyProvisioned(job) && !InteropOff()) return false;
            KeepHolder();

            if (AlreadyProvisioned(job))
            {
                log.Write("already provisioned for this job; checking health");
            }
            else
            {
                if (!Provision(job)) { RecordProvisioned(job, "failed"); return false; }
                RecordProvisioned(job, "ok");
            }
            if (!WaitHealthy(240)) return false;
            Status("ready", HostProgress.PhaseReady, 100, "Domovoi is running");
            log.Write("ready");
            PlaceSatelliteSetup();
            ReportWindowsSide();
            return true;
        }

        // This build's version and commit, into the distro as root, for the
        // core (domovoi/windows_side.py). Never fails the bring-up: a core
        // without it shows no Windows side and offers no Windows step.
        void ReportWindowsSide()
        {
            try
            {
                string text = WindowsSideReport.Content(BuildVersion.Version, BuildVersion.Commit, DateTime.UtcNow);
                byte[] body = new UTF8Encoding(false).GetBytes(text);
                string sha;
                using (var h = System.Security.Cryptography.SHA256.Create()) sha = Sha256.Hex(h.ComputeHash(body));
                WslResult r = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c", WindowsSideReport.Script() }, 60000, body, 4096);
                if (r.ExitCode == 0 && r.Stdout.StartsWith(sha, StringComparison.Ordinal))
                    log.Write("told the Linux side this Windows side is " + BuildVersion.Version + (BuildVersion.Commit != null ? " (" + BuildVersion.Commit.Substring(0, 12) + ")" : ""));
                else
                    log.Write("could not tell the Linux side the Windows side's version (exit " + Inv.S(r.ExitCode) + "): " + FirstLine(r.Stderr + r.Stdout));
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is UnauthorizedAccessException || e is InvalidOperationException
                      || e is ArgumentException || e is System.ComponentModel.Win32Exception)) throw;
                log.Write("could not tell the Linux side the Windows side's version: " + e.Message);
            }
        }

        // The satellite Setup this server's Setup brought, into the core's
        // cache (common\SatelliteSetup.cs). Never fails the bring-up: a
        // server without it still runs, and its dashboard offers the
        // PowerShell command instead.
        void PlaceSatelliteSetup()
        {
            try
            {
                string src = SatelliteSetupPlacement.Source(AppDomain.CurrentDomain.BaseDirectory);
                if (src == null) return;
                byte[] exe = File.ReadAllBytes(src);
                string sha;
                using (var h = System.Security.Cryptography.SHA256.Create()) sha = Sha256.Hex(h.ComputeHash(exe));
                var user = new[] { "-d", Paths.DistroName, "-u", SatelliteSetupPlacement.ServiceUser, "--cd", "/", "--exec", "/bin/sh", "-c" };
                WslResult have = Wsl.Run(Concat(user, SatelliteSetupPlacement.CheckScript()), 60000, null, 4096);
                if (have.ExitCode == 0 && SatelliteSetupPlacement.IsPlaced(have.Stdout, sha, exe.Length)) return;
                long now = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
                WslResult r = Wsl.Run(Concat(user, SatelliteSetupPlacement.PlaceScript(sha, exe.Length, now)), 120000, exe, 4096);
                if (r.ExitCode == 0 && r.Stdout.StartsWith(sha, StringComparison.Ordinal))
                    log.Write("placed the satellite Setup (" + sha + ") in the core's cache");
                else
                    log.Write("could not place the satellite Setup (exit " + Inv.S(r.ExitCode) + "): " + FirstLine(r.Stderr + r.Stdout));
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is UnauthorizedAccessException || e is InvalidOperationException
                      || e is System.ComponentModel.Win32Exception)) throw;
                log.Write("could not place the satellite Setup: " + e.Message);
            }
        }

        static string[] Concat(string[] head, string last)
        {
            var all = new string[head.Length + 1];
            Array.Copy(head, all, head.Length);
            all[head.Length] = last;
            return all;
        }

        bool WriteWslConfig(HostJob job)
        {
            string error = WriteWslFile(WslText(job));
            if (error == null) return true;
            Fail(error, null);
            return false;
        }

        // The job's .wslconfig with the household's limits in it (the stored
        // choice, inside this PC's bounds as they are now); the job's own
        // text when they can't go in.
        string WslText(HostJob job)
        {
            LimitsChoice c = HardwareLimits.Within(StoredLimits(), CurrentBounds());
            try { return HardwareLimits.Apply(job.WslConfig, c); }
            catch (Exception e)
            {
                if (!(e is FormatException || e is ArgumentException)) throw;
                log.Write("the household's limits can't go into this .wslconfig (" + e.Message + "); the installer's values apply");
                return job.WslConfig;
            }
        }

        LimitsChoice StoredLimits() { return LimitsChoice.FromJson(Json.Obj(ReadState(), "limits")); }

        static LimitsBounds CurrentBounds()
        {
            return HardwareLimits.Bounds(HardwareLimits.InstalledRamBytes(), HardwareLimits.UsableRamBytes(), Environment.ProcessorCount);
        }

        // null, or why the file could not be written.
        string WriteWslFile(string wslText)
        {
            // This account's own profile, wherever Windows made it (WslConfig.FileIn).
            string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            if (string.IsNullOrEmpty(profile) || !Directory.Exists(profile)) return "the account's profile folder is missing";
            string path = WslConfig.FileIn(profile);
            string text = wslText.Replace("\r\n", "\n").Replace("\n", "\r\n");
            try
            {
                if (File.Exists(path) && File.ReadAllText(path) == text) return null;
                bool existed = File.Exists(path);
                AtomicFile.WriteAllText(path, text);
                log.Write("wrote " + path + (existed ? " (changed; WSL applies it when this account's VM next starts)" : ""));
                return null;
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is UnauthorizedAccessException)) throw;
                return "can't write " + path + ": " + e.Message;
            }
        }

        // -- the household's hardware limits (HardwareLimits.cs) ---------

        // Every HardwareLimits.Every while Domovoi runs: read the core's
        // request as the service user, keep a changed choice and write it
        // into .wslconfig, restart for a new restart id, and report to the
        // Linux side when anything in the report changed. Returns whether
        // Domovoi is up (false only after a restart that didn't come back).
        bool PollLimits(HostJob job)
        {
            if (DateTime.UtcNow < limitsNext) return true;
            limitsNext = DateTime.UtcNow + HardwareLimits.Every;
            // A wsl.exe -d call would start a stopped distro.
            if (holder == null || SafeExited(holder)) return true;
            LimitsBounds b = CurrentBounds();
            WslResult r;
            try { r = Wsl.Run(HardwareLimits.ReadArgs(Paths.DistroName), HardwareLimits.TimeoutMs, null, HardwareLimits.MaxRequestBytes + 64); }
            catch (System.ComponentModel.Win32Exception e) { log.Write("can't read the hardware-limits request: " + e.Message); return true; }
            if (r.TimedOut) return true;
            // head's exit 1 is "no such file": no request.
            LimitsRequest req = HardwareLimits.Parse(r.ExitCode == 0 ? r.Stdout : null, b);
            if (req.State == "invalid")
            {
                if (req.Problem != limitsLastProblem) log.Write("the hardware-limits request is refused: " + req.Problem);
                limitsLastProblem = req.Problem;
            }
            else limitsLastProblem = null;
            if (req.Choice != null && !req.Choice.SameAs(StoredLimits()))
            {
                UpdateState("limits", req.Choice.ToJson());
                log.Write("the household's limits are now " + HardwareLimits.Describe(req.Choice)
                    + (req.State == "clamped" ? " (moved inside this PC's bounds)" : "")
                    + "; WSL uses them when Domovoi's Linux system next starts");
                string error = WriteWslFile(WslText(job));
                if (error != null) log.Write("the new limits are kept, but " + error);
            }
            bool ready = true;
            Dictionary<string, object> st = Store.ReadStrict();
            string done = st == null ? null : Json.Str(st, "limits_restart_id");
            LimitsRestart decision = HardwareLimits.DecideRestart(req, done, st != null, DateTime.UtcNow, lastLinuxRestart,
                req.RestartId != null && st != null ? UpdateUnitState() : null);
            switch (decision)
            {
                case LimitsRestart.TooSoon: limitsRestartState = "waiting"; break;
                case LimitsRestart.Busy:
                    // An update or Restart of Domovoi runs: stopping the
                    // units now would cut it off. Asked again next poll.
                    if (limitsRestartState != "busy") log.Write("a restart for the new limits waits: an update or restart of Domovoi is running");
                    limitsRestartState = "busy";
                    break;
                case LimitsRestart.Stale:
                    // Older than RestartMaxAge (written before a reboot, say):
                    // recorded as seen, never acted on.
                    UpdateState("limits_restart_id", req.RestartId);
                    break;
                case LimitsRestart.Go:
                    // Recorded first, and only then: a restart whose id can't
                    // be recorded would run again at every poll.
                    if (!UpdateState("limits_restart_id", req.RestartId))
                    {
                        log.Write("the restart for the new limits is not done: its id could not be recorded");
                        limitsRestartState = "failed";
                        break;
                    }
                    lastLinuxRestart = DateTime.UtcNow;
                    ready = RestartLinux(job, "its new limits (" + HardwareLimits.Describe(StoredLimits()) + ")",
                        "Restarting Domovoi so it starts with its new limits");
                    limitsRestartState = ready ? "done" : "failed";
                    limitsNext = DateTime.MinValue;
                    break;
            }
            if (ready && holder != null && !SafeExited(holder)) ReportLimits(job, b, req);
            return ready;
        }

        // domovoi-update.service's ActiveState, as root; null when it can't
        // be read (then the restart waits too: HardwareLimits.DecideRestart).
        string UpdateUnitState()
        {
            try
            {
                WslResult r = Wsl.Run(HardwareLimits.UpdateStateArgs(Paths.DistroName), 30000, null, 4096);
                if (r.TimedOut || r.ExitCode != 0) return null;
                return r.Stdout.Trim();
            }
            catch (System.ComponentModel.Win32Exception) { return null; }
        }

        void ReportLimits(HostJob job, LimitsBounds b, LimitsRequest req)
        {
            LimitsChoice defaults = HardwareLimits.InFile(job.WslConfig);
            defaults.Processors = null;
            LimitsChoice chosen = HardwareLimits.Within(StoredLimits(), b);
            LimitsChoice inFile = HardwareLimits.InFile(WslText(job));
            string key = HardwareLimits.Report(b, defaults, chosen, inFile, req, limitsRestartState, DateTime.MinValue);
            if (key == limitsLastReport) return;
            string text = HardwareLimits.Report(b, defaults, chosen, inFile, req, limitsRestartState, DateTime.UtcNow);
            byte[] body = new UTF8Encoding(false).GetBytes(text);
            string sha;
            using (var h = System.Security.Cryptography.SHA256.Create()) sha = Sha256.Hex(h.ComputeHash(body));
            try
            {
                WslResult w = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c", HardwareLimits.ReportScript() }, 60000, body, 4096);
                if (w.ExitCode == 0 && w.Stdout.StartsWith(sha, StringComparison.Ordinal))
                {
                    limitsLastReport = key;
                    log.Write("told the Linux side this PC's hardware and limits (" + Inv.S(b.RamGb) + " GB, " + Inv.S(b.LogicalProcessors)
                        + " logical processors; " + HardwareLimits.Describe(chosen) + ")");
                }
                else log.Write("could not tell the Linux side its hardware limits (exit " + Inv.S(w.ExitCode) + "): " + FirstLine(w.Stderr + w.Stdout));
            }
            catch (System.ComponentModel.Win32Exception e) { log.Write("could not tell the Linux side its hardware limits: " + e.Message); }
        }

        // Domovoi's Linux system restarted so the VM starts again with the
        // new limits, or mounts the new media folders: the units stopped cleanly, the distro terminated, this
        // account's WSL shut down (the household's own WSL and Docker run
        // on: ReleaseWsl), then the usual bring-up.
        bool RestartLinux(HostJob job, string why, string line)
        {
            log.Write("restarting Domovoi's Linux system for " + why);
            Status("restarting", HostProgress.PhaseRestart, 0, line);
            WslResult s = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/usr/bin/systemctl", "stop", "domovoi-web", "domovoi-core", "domovoi-db" }, 40000, null, 8192);
            log.Write("systemctl stop exit " + Inv.S(s.ExitCode) + (s.TimedOut ? " (timed out)" : ""));
            KillHolder();
            WslResult t = Wsl.Run(new[] { "--terminate", Paths.DistroName }, 60000, null, 8192);
            log.Write("wsl --terminate exit " + Inv.S(t.ExitCode) + Trailer(t));
            ReleaseWsl();
            holderNext = DateTime.MinValue;
            holderBackoffSec = 5;
            lock (statusGate) status.Percent = 0;
            return BringUp(job);
        }

        // -- the media folders (MediaFolders.cs, MediaPrep.cs) -----------

        // The folders Domovoi uses now, and whether they are the
        // dashboard's; a dashboard choice Setup has since overruled (a new
        // folder picked in Setup) is dropped from host-state.json.
        List<KeyValuePair<string, string>> EffectiveMedia(HostJob job, out bool fromDashboard)
        {
            Dictionary<string, object> st = Store.ReadStrict();
            MediaOverride ov = MediaOverride.FromJson(Json.Obj(st ?? new Dictionary<string, object>(), "media"));
            bool drop;
            List<KeyValuePair<string, string>> media = MediaFolders.Effective(job.ProvisionArgs, ov, out drop);
            if (drop && st != null)
            {
                log.Write("Setup chose the media folder " + (MediaFolders.RootOf(MediaFolders.MediaArgs(job.ProvisionArgs)) ?? "(none)")
                    + "; the dashboard's choice (" + ov.Root + ") no longer applies");
                UpdateState("media", null);
            }
            fromDashboard = ov != null && !drop;
            return media;
        }

        // provision.sh's arguments with the folders Domovoi uses now, so a
        // repair keeps a move made from the dashboard.
        List<string> ProvisionArgsFor(HostJob job)
        {
            bool fromDashboard;
            List<KeyValuePair<string, string>> media = EffectiveMedia(job, out fromDashboard);
            return fromDashboard ? MediaFolders.WithMedia(job.ProvisionArgs, media) : job.ProvisionArgs;
        }

        MediaChange StoredChange()
        {
            return MediaChange.FromJson(Json.Obj(ReadState(), "media_change"));
        }

        void SetChange(MediaChange c)
        {
            if (Json.Serialize(c.ToJson()) == Json.Serialize(StoredChange().ToJson())) return;
            UpdateState("media_change", c.ToJson());
        }

        // Every HardwareLimits.Every while Domovoi runs: read the core's move
        // request as the service user, act on a new one (MediaFolders.Decide),
        // and tell the Linux side and the tray which folders Domovoi uses.
        // Returns whether Domovoi is up (false only after a restart that
        // didn't come back).
        bool PollMedia(HostJob job)
        {
            if (DateTime.UtcNow < mediaNext) return true;
            mediaNext = DateTime.UtcNow + HardwareLimits.Every;
            if (holder == null || SafeExited(holder)) return true;
            WslResult r;
            try { r = Wsl.Run(MediaFolders.ReadArgs(Paths.DistroName), HardwareLimits.TimeoutMs, null, MediaFolders.MaxRequestBytes + 64); }
            catch (System.ComponentModel.Win32Exception e) { log.Write("can't read the media-folders request: " + e.Message); return true; }
            if (r.TimedOut) return true;
            MediaRequest req = MediaFolders.Parse(r.ExitCode == 0 ? r.Stdout : null);
            if (req.State == "invalid" && req.Problem != mediaLastProblem) log.Write("the media-folders request is refused: " + req.Problem);
            mediaLastProblem = req.State == "invalid" ? req.Problem : null;
            Dictionary<string, object> st = Store.ReadStrict();
            string done = st == null ? null : Json.Str(st, "media_request_id");
            bool pending = req.Id != null && req.Id != done && req.State == "ok" && st != null;
            MediaMove decision = MediaFolders.Decide(req, done, st != null, DateTime.UtcNow, lastLinuxRestart,
                pending ? UpdateUnitState() : null);
            bool ready = true;
            var change = new MediaChange { RequestId = req.Id, Root = req.Root };
            switch (decision)
            {
                case MediaMove.Invalid:
                    UpdateState("media_request_id", req.Id);
                    change.State = "refused";
                    change.Problem = req.Problem;
                    SetChange(change);
                    break;
                case MediaMove.Stale:
                    UpdateState("media_request_id", req.Id);
                    change.State = "stale";
                    change.Problem = "the request waited too long; ask again";
                    SetChange(change);
                    break;
                case MediaMove.TooSoon:
                    change.State = "waiting";
                    SetChange(change);
                    break;
                case MediaMove.Busy:
                    if (StoredChange().State != "busy") log.Write("moving the media folders waits: an update or restart of Domovoi is running");
                    change.State = "busy";
                    SetChange(change);
                    break;
                case MediaMove.Go:
                    ready = MoveMedia(job, req, change);
                    mediaNext = DateTime.MinValue;
                    break;
            }
            if (ready && holder != null && !SafeExited(holder)) ReportMedia(job, null);
            return ready;
        }

        // A new media root: checked and made on Windows, kept, the distro's
        // mounts rewritten, then Domovoi's Linux system restarted.
        bool MoveMedia(HostJob job, MediaRequest req, MediaChange change)
        {
            // Recorded first, and only then acted on: an id that can't be
            // recorded would run again at every poll.
            if (!UpdateState("media_request_id", req.Id))
            {
                log.Write("the media folders are not moved: the request's id could not be recorded");
                return true;
            }
            bool wasDashboard;
            List<KeyValuePair<string, string>> before = EffectiveMedia(job, out wasDashboard);
            change.PreviousRoot = MediaFolders.RootOf(before);
            string setupRoot = MediaFolders.RootOf(MediaFolders.MediaArgs(job.ProvisionArgs));
            log.Write("moving the media folders to " + req.Root + " (asked from the dashboard)");
            change.State = "applying";
            SetChange(change);
            ReportMedia(job, change);
            string why = setupRoot == null
                ? "Setup's media folders aren't the usual five under one folder; run Domovoi Setup again to choose the folder"
                : MediaPrep.Prepare(req.Root, delegate (string m) { log.Write(m); });
            if (why != null)
            {
                log.Write("the media folders stay where they are: " + why);
                change.State = "refused";
                change.Problem = why.Length > 290 ? why.Substring(0, 290) : why;
                SetChange(change);
                return true;
            }
            Dictionary<string, object> old = Json.Obj(ReadState(), "media");
            var ov = new MediaOverride { Root = MediaPaths.Normalize(req.Root), BasedOn = setupRoot };
            // Back to Setup's own root: nothing of the dashboard's to keep.
            bool toSetup = string.Equals(ov.Root, setupRoot, StringComparison.OrdinalIgnoreCase);
            if (!UpdateState("media", toSetup ? null : ov.ToJson()))
            {
                change.State = "failed";
                change.Problem = "host-state.json could not be written";
                SetChange(change);
                return true;
            }
            string fstabError = WriteFstab(MediaPaths.Under(ov.Root));
            if (fstabError != null)
            {
                UpdateState("media", old);
                log.Write("the media folders stay where they are: " + fstabError);
                change.State = "failed";
                change.Problem = "the Linux system's mounts could not be changed: " + fstabError;
                if (change.Problem.Length > 290) change.Problem = change.Problem.Substring(0, 290);
                SetChange(change);
                return true;
            }
            lastLinuxRestart = DateTime.UtcNow;
            change.State = "done";
            SetChange(change);
            bool ready = RestartLinux(job, "its new media folders (" + ov.Root + ")", "Restarting Domovoi so it uses the new media folders");
            if (!ready)
            {
                change.State = "failed";
                change.Problem = "Domovoi didn't come up after the restart; it starts again by itself";
                SetChange(change);
            }
            return ready;
        }

        // null, or why the distro's /etc/fstab could not be rewritten.
        string WriteFstab(List<KeyValuePair<string, string>> media)
        {
            try
            {
                byte[] body = new UTF8Encoding(false).GetBytes(MediaFolders.FstabInput(media));
                WslResult w = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c", MediaFolders.FstabScript() }, 60000, body, 4096);
                if (w.ExitCode == 0) { log.Write("rewrote the media block of the distro's /etc/fstab"); return null; }
                return "exit " + Inv.S(w.ExitCode) + ": " + FirstLine(w.Stderr + w.Stdout);
            }
            catch (System.ComponentModel.Win32Exception e) { return e.Message; }
            catch (ArgumentException e) { return e.Message; }
        }

        // media.json for the core and the tray's list, when either changed.
        void ReportMedia(HostJob job, MediaChange change)
        {
            bool fromDashboard;
            List<KeyValuePair<string, string>> media = EffectiveMedia(job, out fromDashboard);
            if (media.Count == 0) return;
            MediaChange c = change ?? StoredChange();
            string source = fromDashboard ? "dashboard" : "setup";
            string key = MediaFolders.Report(media, source, c, true, true, DateTime.MinValue);
            if (key != mediaLastReport)
            {
                string text = MediaFolders.Report(media, source, c, true, true, DateTime.UtcNow);
                byte[] body = new UTF8Encoding(false).GetBytes(text);
                string sha;
                using (var h = System.Security.Cryptography.SHA256.Create()) sha = Sha256.Hex(h.ComputeHash(body));
                try
                {
                    WslResult w = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c", MediaFolders.ReportScript() }, 60000, body, 4096);
                    if (w.ExitCode == 0 && w.Stdout.StartsWith(sha, StringComparison.Ordinal))
                    {
                        mediaLastReport = key;
                        log.Write("told the Linux side its media folders (" + (MediaFolders.RootOf(media) ?? "several folders") + ", " + source + ")");
                    }
                    else log.Write("could not tell the Linux side its media folders (exit " + Inv.S(w.ExitCode) + "): " + FirstLine(w.Stderr + w.Stdout));
                }
                catch (System.ComponentModel.Win32Exception e) { log.Write("could not tell the Linux side its media folders: " + e.Message); }
            }
            string map = MediaFolders.TrayMap(media, DateTime.MinValue);
            if (map != mediaLastTrayMap || !File.Exists(MediaFolders.TrayMapPath))
            {
                try
                {
                    MediaPrep.WriteTrayMap(MediaFolders.TrayMap(media, DateTime.UtcNow));
                    mediaLastTrayMap = map;
                }
                catch (Exception e)
                {
                    if (!(e is IOException || e is UnauthorizedAccessException || e is InvalidOperationException)) throw;
                    log.Write("could not write the tray's list of media folders: " + e.Message);
                }
            }
        }

        bool Import(HostJob job)
        {
            Status("importing", HostProgress.PhaseImport, 0, "Checking the Ubuntu image");
            if (!File.Exists(job.Image)) { Fail("the Ubuntu image is missing: " + job.Image, null); return false; }
            string sha = Sha256.OfFile(job.Image);
            if (sha != job.ImageSha256) { Fail("the Ubuntu image does not match its pinned SHA-256", null); return false; }
            string vhd = Paths.DistroDisk;
            if (File.Exists(vhd))
            {
                // Once imported, the disk holds the household's data: WSL not
                // listing it is a problem to look at, never a reason to import
                // over it.
                if (ImportedBefore())
                {
                    Fail("WSL does not list the distro '" + Paths.DistroName + "', but its disk " + vhd + " is there; Setup won't touch it (see docs/WINDOWS_HOST.md)", null);
                    return false;
                }
                // A disk left by an import that was killed midway: keep it
                // aside rather than delete it, then import again.
                string aside = vhd + ".unregistered-" + DateTime.UtcNow.ToString("yyyyMMddHHmmss", CultureInfo.InvariantCulture);
                log.Write("moving a disk no import finished to " + aside);
                try { File.Move(vhd, aside); }
                catch (IOException e) { Fail("a disk no import finished is in use: " + e.Message, null); return false; }
            }
            log.Write("wsl --import " + Paths.DistroName + " " + Paths.Distro + " " + job.Image + " --version 2");
            DateTime started = DateTime.UtcNow;
            WslResult r = Wsl.Run(new[] { "--import", Paths.DistroName, Paths.Distro, job.Image, "--version", "2" }, 30 * 60 * 1000, null, 64 * 1024,
                delegate
                {
                    int secs = (int)(DateTime.UtcNow - started).TotalSeconds;
                    Status("importing", HostProgress.PhaseImport, Math.Min(90, secs), "Unpacking Ubuntu (" + Inv.S(secs) + " s)");
                });
            log.Write("wsl --import exit " + Inv.S(r.ExitCode) + Trailer(r));
            bool? reg = Wsl.Registered();
            if (r.ExitCode != 0 || reg != true)
            {
                Fail("wsl --import failed (exit " + Inv.S(r.ExitCode) + "): " + FirstLine(r.Stdout + r.Stderr), null);
                return false;
            }
            UpdateState("imported", true);
            // The first install's checkout is then checked against the
            // commit this job (or a later one, before it succeeds) pinned.
            UpdateState("install_commits", InstalledHead.WithAsked(null, InstalledHead.RefOf(job.ProvisionArgs)).ToArray());
            UpdateState("head_verified", false);
            return true;
        }

        // Interop off before the first provisioning pass. provision.sh turns
        // it off too, but only in /etc/wsl.conf, which takes effect at the
        // distro's next start; the stock image already runs systemd, so
        // without this the whole first pass (as root, for minutes) would run
        // with interop on, able to start Windows programs as domovoi-svc.
        // A fresh image has no [interop] section, so this writes a minimal
        // wsl.conf (the keys provision.sh writes, minus the default user,
        // which doesn't exist yet) and restarts the distro. There is no race:
        // the holder is stopped first, and the next wsl.exe starts the
        // distro afresh with the new file. provision.sh then writes its own
        // file and asks for the restart Provision() already handles.
        bool InteropOff()
        {
            WslResult c = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c",
                "grep -A3 '^\\[interop\\]' /etc/wsl.conf 2>/dev/null | grep -qx 'enabled *= *false'" }, 120000, null, 4096);
            if (c.ExitCode == 0 && Json.Bool(ReadState(), "interop_off") == true) return true;
            Status("config", HostProgress.PhaseImport, 95, "Closing the Linux system off from Windows");
            string conf = "# Written by DomovoiHost before provisioning; scripts/linux/provision.sh replaces it.\n"
                + "[boot]\nsystemd=true\n[interop]\nenabled=false\nappendWindowsPath=false\n[automount]\nenabled=false\nmountFsTab=true\n";
            WslResult w = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c",
                "cat > /etc/wsl.conf.domovoi-new && mv -f /etc/wsl.conf.domovoi-new /etc/wsl.conf" }, 120000, Encoding.UTF8.GetBytes(conf), 4096);
            if (w.ExitCode != 0)
            {
                Fail("could not write /etc/wsl.conf in the distro (exit " + Inv.S(w.ExitCode) + "): " + FirstLine(w.Stderr + w.Stdout), null);
                return false;
            }
            KillHolder();
            WslResult t = Wsl.Run(new[] { "--terminate", Paths.DistroName }, 120000, null, 8192);
            log.Write("interop off: wrote /etc/wsl.conf, wsl --terminate exit " + Inv.S(t.ExitCode));
            if (t.ExitCode != 0)
            {
                Fail("could not restart the distro to turn interop off (exit " + Inv.S(t.ExitCode) + ")", null);
                return false;
            }
            UpdateState("interop_off", true);
            holderNext = DateTime.MinValue;
            holderBackoffSec = 5;
            return true;
        }

        static string FirstLine(string s)
        {
            foreach (string l in (s ?? "").Split('\n'))
            {
                string t = l.Replace("\0", "").Trim();
                if (t.Length > 0) return t.Length > 300 ? t.Substring(0, 300) : t;
            }
            return "no message";
        }

        static string Trailer(WslResult r)
        {
            string s = (r.Stdout + r.Stderr).Replace("\0", "").Trim();
            if (s.Length == 0) return "";
            if (s.Length > 2000) s = s.Substring(0, 2000) + "...";
            return ": " + s.Replace("\r", "").Replace("\n", " | ");
        }

        // -- the holder -------------------------------------------------

        void KeepHolder()
        {
            if (holder != null && !SafeExited(holder)) return;
            if (holder != null)
            {
                double ran = (DateTime.UtcNow - holderStarted).TotalSeconds;
                int code = -1;
                try { code = holder.ExitCode; } catch (InvalidOperationException) { }
                log.Write(Inv.F("holder exited (code {0}) after {1:0} s", code, ran));
                holder.Dispose();
                holder = null;
                if (ran > 60) holderBackoffSec = 5;
                holderNext = DateTime.UtcNow.AddSeconds(holderBackoffSec);
                holderBackoffSec = Math.Min(holderBackoffSec * 2, 300);
            }
            if (DateTime.UtcNow < holderNext) return;
            // PLAN 5.1: unquoted arguments; a quoted "sleep infinity" makes a
            // restart loop (microsoft/WSL#9231).
            var psi = Wsl.Start(new[] { "-d", Paths.DistroName, "--cd", "/", "--exec", "sleep", "infinity" });
            try
            {
                holder = Wsl.Launch(psi);
                holderStarted = DateTime.UtcNow;
                log.Write("holder started (pid " + Inv.S(holder.Id) + ")");
            }
            catch (System.ComponentModel.Win32Exception e)
            {
                log.Write("can't start the holder: " + e.Message);
                holderNext = DateTime.UtcNow.AddSeconds(holderBackoffSec);
                holderBackoffSec = Math.Min(holderBackoffSec * 2, 300);
            }
        }

        void KillHolder()
        {
            if (holder == null) return;
            try { if (!holder.HasExited) holder.Kill(); }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception) { }
            try { holder.WaitForExit(5000); } catch (InvalidOperationException) { }
            holder.Dispose();
            holder = null;
        }

        void RestartDistro()
        {
            Status("restarting", HostProgress.PhaseRestart, 0, "Restarting Domovoi's Linux system");
            KillHolder();
            WslResult r = Wsl.Run(new[] { "--terminate", Paths.DistroName }, 120000, null, 16 * 1024);
            log.Write("wsl --terminate exit " + Inv.S(r.ExitCode) + Trailer(r));
            holderNext = DateTime.MinValue;
            holderBackoffSec = 5;
            KeepHolder();
            Sleep(3000);
        }

        // -- provisioning -----------------------------------------------

        bool CopyScriptIn(HostJob job)
        {
            if (!File.Exists(job.Provision)) { Fail("provision.sh is missing: " + job.Provision, null); return false; }
            byte[] script = File.ReadAllBytes(job.Provision);
            string sha;
            using (var h = System.Security.Cryptography.SHA256.Create()) sha = Sha256.Hex(h.ComputeHash(script));
            if (sha != job.ProvisionSha256) { Fail("provision.sh does not match its pinned SHA-256", null); return false; }
            canStopBeforeStart = ProvisionScript.SupportsStopBefore(Encoding.UTF8.GetString(script));
            string sh = "umask 077 && mkdir -p " + InDistroDir + " && cat > " + InDistroScript + ".new && mv -f "
                + InDistroScript + ".new " + InDistroScript + " && sha256sum " + InDistroScript;
            WslResult r = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c", sh }, 120000, script, 16 * 1024);
            if (r.ExitCode != 0 || !r.Stdout.StartsWith(job.ProvisionSha256, StringComparison.Ordinal))
            {
                Fail("could not copy provision.sh into the distro (exit " + Inv.S(r.ExitCode) + "): " + FirstLine(r.Stderr + r.Stdout), null);
                return false;
            }
            return true;
        }

        bool Provision(HostJob job)
        {
            Status("provisioning", HostProgress.PhaseProvision, 0, "Preparing Domovoi's Linux system");
            if (!CopyScriptIn(job)) return false;
            // S3 (delta security review 2026-10-16): until the first install
            // is verified, the HEAD it reports must be a commit Setup pinned.
            // A distro imported before this check existed has no list: never
            // checked (its checkout follows the dashboard's updates).
            var st = ReadState();
            List<string> asked = Json.StrList(st, "install_commits");
            bool checkHead = asked != null && Json.Bool(st, "head_verified") != true;
            if (checkHead)
            {
                asked = InstalledHead.WithAsked(asked, InstalledHead.RefOf(job.ProvisionArgs));
                UpdateState("install_commits", asked.ToArray());
            }
            int restarts = 0, lost = 0, busy = 0;
            bool verifying = false;
            // The port check right before the start step (owner fixes
            // 2026-10-12, item 2): the first pass stops before "start".
            var gate = new PortGate(canStopBeforeStart);
            if (!gate.Pending) log.Write("the pinned provision.sh has no --stop-before: the ports are checked again only if the start fails");
            while (!Stopping)
            {
                ProvisionLine final;
                int code = RunProvision(job, verifying, gate.Args(ProvisionArgsFor(job)), out final);
                if (Stopping) return false;
                if (final != null)
                {
                    lock (statusGate)
                    {
                        status.Head = final.Head;
                        status.Tier = final.Tier;
                        if (final.Gpu != null) { status.Gpu = final.Gpu; status.GpuKnown = true; }
                        if (final.Ai != null) status.Ai = final.Ai;
                    }
                }
                string why;
                ProvisionVerdict v = ProvisionOutcome.Classify(code, final, out why);
                log.Write("provision.sh exit " + Inv.S(code) + ": " + v + (why.Length > 0 ? " (" + why + ")" : ""));
                switch (v)
                {
                    case ProvisionVerdict.Done:
                        if (checkHead)
                        {
                            string bad = InstalledHead.Problem(final.Head, asked);
                            if (bad != null) { Fail(bad, "checkout"); return false; }
                            UpdateState("head_verified", true);
                            log.Write("the installed commit " + final.Head + " is the one Setup pinned");
                        }
                        return true;
                    case ProvisionVerdict.DoneRestartDistro:
                    case ProvisionVerdict.RestartDistroAndRerun:
                        if (++restarts > 2) { Fail("provisioning still asks for a restart after two", null); return false; }
                        RestartDistro();
                        verifying = true;
                        continue;
                    case ProvisionVerdict.Busy:
                        if (++busy > 6) { Fail("another provisioning run stays busy", null); return false; }
                        if (Sleep(30000)) return false;
                        continue;
                    case ProvisionVerdict.Lost:
                        if (++lost > 1) { Fail(why, null); return false; }
                        continue;
                    case ProvisionVerdict.Stopped:
                        if (!gate.OnStopped()) { Fail("provisioning stopped though nobody asked it to: " + why, null); return false; }
                        if (!WaitForFreePorts()) return false;
                        continue;
                    default:
                        Fail(why, final != null ? final.FailedStep : null);
                        return false;
                }
            }
            return false;
        }

        int RunProvision(HostJob job, bool verifying, List<string> scriptArgs, out ProvisionLine final)
        {
            var args = new List<string> { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/bin/bash", InDistroScript };
            args.AddRange(scriptArgs);
            plog.Write("---- provision.sh " + string.Join(" ", scriptArgs.ToArray()) + (verifying ? " (after a restart)" : ""));
            var psi = Wsl.Start(args);
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.RedirectStandardInput = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            ProvisionLine last = null;
            string phase = verifying ? HostProgress.PhaseVerify : HostProgress.PhaseProvision;
            Status("provisioning", phase, 0, verifying ? "Checking Domovoi after the restart" : "Preparing Domovoi's Linux system");
            using (var p = new Process())
            {
                p.StartInfo = psi;
                p.OutputDataReceived += delegate (object s, DataReceivedEventArgs a)
                {
                    if (a.Data == null) return;
                    plog.Write(a.Data);
                    ProvisionLine line = ProvisionLine.TryParse(a.Data);
                    if (line == null) return;
                    if (line.Final) last = line;
                    lock (statusGate)
                    {
                        status.Step = line.Step;
                        status.Bytes = line.Bytes;
                        status.BytesTotal = line.BytesTotal;
                    }
                    if (!line.Final) Status("provisioning", phase, line.Percent, line.Message);
                };
                p.ErrorDataReceived += delegate (object s, DataReceivedEventArgs a)
                {
                    if (a.Data != null) plog.Write("stderr: " + a.Data);
                };
                p.Start();
                try { ChildJob.Add(p); }
                catch (System.ComponentModel.Win32Exception e)
                {
                    try { p.Kill(); } catch (InvalidOperationException) { }
                    throw new InvalidOperationException("can't put provision.sh's wsl.exe in the host's job object: " + e.Message);
                }
                provision = p;
                p.StandardInput.Close();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                while (!p.WaitForExit(1000))
                {
                    if (Stopping)
                    {
                        try { p.Kill(); } catch (InvalidOperationException) { } catch (System.ComponentModel.Win32Exception) { }
                        break;
                    }
                    ProcessRequests(false);
                    WriteStatus(false);
                }
                p.WaitForExit();
                provision = null;
                final = last;
                return p.ExitCode;
            }
        }

        // Right before provision.sh starts Domovoi: every port Domovoi needs
        // must be free on Windows, or the database, the core or the dashboard
        // can't bind (mirrored networking shares the port space). Setup's
        // own check was before the downloads, which take most of the
        // install; on 2026-10-12 Docker's domovoi-postgres took 6432 back in
        // those minutes and the start failed. Waits while any is taken,
        // looking again every 3 s; Setup shows the same pop-up per program
        // as its first check meanwhile (state "ports"). Only Windows
        // programs show in the table, never Domovoi's own Linux services
        // (PortScan). False: the service is stopping.
        bool WaitForFreePorts()
        {
            bool waited = false;
            bool free = PortGate.WaitUntilFree(
                delegate
                {
                    try { return PortCheck.Owners(PortScan.Listeners(), null); }
                    catch (System.ComponentModel.Win32Exception e)
                    {
                        log.Write("can't read Windows' port table (" + e.Message + "); starting Domovoi without the check");
                        return null;
                    }
                },
                delegate (string text)
                {
                    waited = true;
                    log.Write("ports Domovoi needs are taken: " + text + "; waiting before starting Domovoi");
                    Status("ports", HostProgress.PhaseProvision, 0, "Waiting for port " + text + " to be free");
                },
                delegate (int ms)
                {
                    ProcessRequests(false);
                    WriteStatus(false);
                    return Sleep(ms);
                },
                3000);
            if (free) log.Write(waited ? "the ports Domovoi needs are free again" : "the ports Domovoi needs are free");
            return free && !Stopping;
        }

        bool WaitHealthy(int seconds)
        {
            Status("health", HostProgress.PhaseHealth, 0, "Waiting for Domovoi to answer");
            DateTime until = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < until)
            {
                if (Healthy("http://127.0.0.1:6370/v1/health") && Healthy("http://127.0.0.1:6369/api/health")) return true;
                KeepHolder();
                if (Sleep(2000)) return false;
            }
            Fail(Inv.F("Domovoi did not answer on ports 6370 and 6369 within {0} s", seconds), null);
            return false;
        }

        static bool Healthy(string url)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Proxy = null;
                req.Timeout = 3000;
                req.ReadWriteTimeout = 3000;
                using (var resp = (HttpWebResponse)req.GetResponse()) return resp.StatusCode == HttpStatusCode.OK;
            }
            catch (WebException) { return false; }
        }

        // -- one-off requests -------------------------------------------

        void ProcessRequests(bool ready)
        {
            string[] files;
            try { files = Directory.GetFiles(Paths.HostIn, "req-*.json"); }
            catch (IOException) { return; }
            catch (UnauthorizedAccessException) { return; }
            foreach (string f in files)
            {
                string name = Path.GetFileNameWithoutExtension(f);
                string id = name.Substring(4);
                if (id.Length != 32 || answered.Contains(id) || File.Exists(Paths.ResponseFile(id))) continue;
                answered.Add(id);
                var resp = new Dictionary<string, object>();
                try
                {
                    var d = Json.ReadFile(f);
                    string verb = Json.Str(d, "verb");
                    if (verb == "setup-code") AnswerSetupCode(Json.Str(d, "public_key"), ready, resp);
                    else if (verb == "ai-features") AnswerAiFeatures(ready, resp);
                    else if (verb == "linux-update") AnswerLinuxUpdate(ready, resp);
                    else if (verb == CoreLog.Verb) AnswerCoreLog(resp);
                    else { resp["ok"] = false; resp["error"] = "unknown request"; }
                }
                catch (FormatException e) { resp["ok"] = false; resp["error"] = "bad request: " + e.Message; }
                catch (IOException e) { resp["ok"] = false; resp["error"] = e.Message; }
                try { Json.WriteFile(Paths.ResponseFile(id), resp); }
                catch (IOException e) { log.Write("can't answer request " + id + ": " + e.Message); }
                catch (UnauthorizedAccessException e) { log.Write("can't answer request " + id + ": " + e.Message); }
            }
            // The helper never deletes in host\out (a folder this account can
            // write): an answer goes once the installer has deleted its
            // request.
            string[] answers;
            try { answers = Directory.GetFiles(Paths.HostOut, "resp-*.json"); }
            catch (IOException) { return; }
            catch (UnauthorizedAccessException) { return; }
            foreach (string a in answers)
            {
                string id = Path.GetFileNameWithoutExtension(a).Substring(5);
                if (File.Exists(Paths.RequestFile(id))) continue;
                try { File.Delete(a); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }

        void AnswerSetupCode(string publicKey, bool ready, Dictionary<string, object> resp)
        {
            resp["ok"] = false;
            if (!ready) { resp["error"] = "Domovoi is not running yet"; return; }
            if (string.IsNullOrEmpty(publicKey)) { resp["error"] = "no public key"; return; }
            // As the service user, the file's owner (PLAN 4.12 step 5); the
            // code is never logged, only whether there was one.
            WslResult r = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "domovoi", "--cd", "/", "--exec", "cat", "/home/domovoi/.domovoi/setup-code.txt" }, 60000, null, 4096);
            string code = r.Stdout.Trim();
            if (r.ExitCode != 0 || !SetupCode.IsWellFormed(code))
            {
                resp["error"] = r.ExitCode != 0 ? "there is no setup code (the admin may be claimed already)" : "the setup code file is not in the expected shape";
                log.Write("setup-code request: none to give (exit " + Inv.S(r.ExitCode) + ")");
                return;
            }
            try
            {
                resp["ciphertext"] = SetupCodeKey.Encrypt(publicKey, code);
                resp["ok"] = true;
                log.Write("setup-code request: answered (encrypted to the requester's key)");
            }
            catch (FormatException e) { resp["error"] = "bad public key: " + e.Message; }
            catch (System.Security.Cryptography.CryptographicException e) { resp["error"] = "encryption failed: " + e.Message; }
        }

        // The household's AI_FEATURES (Settings > Models writes it to
        // domovoi/.env), for the AI page of a Setup run again on this
        // install: on or off, nothing else of the file (sed prints only the
        // values of AI_FEATURES lines). As the service user, the file's
        // owner, the way provision.sh reads it.
        void AnswerAiFeatures(bool ready, Dictionary<string, object> resp)
        {
            resp["ok"] = false;
            if (!ready) { resp["error"] = "Domovoi is not running yet"; return; }
            WslResult r = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "domovoi", "--cd", "/", "--exec", "/usr/bin/sed", "-n", HouseholdAi.SedScript, HouseholdAi.EnvFile }, 30000, null, 4096);
            if (r.TimedOut || r.ExitCode != 0)
            {
                resp["error"] = "could not read domovoi/.env (exit " + Inv.S(r.ExitCode) + ")";
                log.Write("ai-features request: " + (string)resp["error"]);
                return;
            }
            bool on = HouseholdAi.FromSedOutput(r.Stdout);
            resp["ai_features"] = on;
            resp["ok"] = true;
            log.Write("ai-features request: answered (" + (on ? "on" : "off") + ")");
        }

        // An administrator's Linux update (the tray's "Update Domovoi", after
        // Windows' permission prompt, through the helper's linux-update):
        // the dashboard's own update, run in the distro as the service user
        // (python -m domovoi.windows_side linux-update: the container
        // guard's freeze, the check, a commit that was rolled back, the
        // pull, then the restart through the update unit). Only an
        // administrator can write host\in, so only one can ask. The answer is
        // the CLI's: started (the update unit runs now), nothing_new, or an
        // error in the dashboard's words.
        void AnswerLinuxUpdate(bool ready, Dictionary<string, object> resp)
        {
            resp["ok"] = false;
            if (!ready) { resp["error"] = "Domovoi is not running yet"; return; }
            WslResult r = Wsl.Run(LinuxUpdate.WslArgs(Paths.DistroName, SatelliteSetupPlacement.ServiceUser), LinuxUpdate.TimeoutMs, null, 64 * 1024);
            Dictionary<string, object> a = LinuxUpdate.Answer(r.Stdout);
            if (r.TimedOut || a == null)
            {
                resp["error"] = r.TimedOut ? "the Linux update did not answer in time" : "the Linux update gave no answer (exit " + Inv.S(r.ExitCode) + "): " + FirstLine(r.Stderr + r.Stdout);
                log.Write("linux-update request: " + (string)resp["error"]);
                return;
            }
            foreach (string k in new[] { "ok", "started", "nothing_new", "error", "from_sha", "to_sha" })
                if (a.ContainsKey(k)) resp[k] = a[k];
            log.Write("linux-update request: " + (Json.Bool(a, "started") == true ? "the update unit is running" : Json.Bool(a, "nothing_new") == true ? "nothing new" : "refused: " + (Json.Str(a, "error") ?? "no reason")));
        }

        // -- the core's log (CoreLog.cs) ---------------------------------

        // A fresh logs\core.log; null, or why not. Only while the holder
        // keeps the distro up: a wsl.exe -d call would start a stopped one,
        // and a distro that isn't up has no journal to give.
        string RefreshCoreLog()
        {
            if (holder == null || SafeExited(holder)) return "Domovoi's Linux system is not running";
            return CoreJournal.Refresh(
                delegate (IEnumerable<string> args, int timeoutMs, int maxOutput) { return Wsl.Run(args, timeoutMs, null, maxOutput); },
                Paths.CoreLogFile, DateTime.UtcNow);
        }

        void NoteCoreLog(string why)
        {
            if (why == coreLogLastError) return;
            log.Write(why == null ? "copied the core's journal to " + Paths.CoreLogFile : "could not copy the core's journal: " + why);
            coreLogLastError = why;
        }

        // At the first ready pass, then every 10 minutes (bounded by
        // CoreLog.TimeoutMs, so a wedged WSL holds this loop 15 s at most).
        void PeriodicCoreLog()
        {
            if (DateTime.UtcNow < coreLogNext) return;
            coreLogNext = DateTime.UtcNow + CoreLog.Every;
            NoteCoreLog(RefreshCoreLog());
        }

        // The helper's copy-logs, right before it copies: a fresh core.log.
        // Ready or not (a failed start is when it matters most).
        void AnswerCoreLog(Dictionary<string, object> resp)
        {
            string why = RefreshCoreLog();
            coreLogLastError = why;
            if (why == null) coreLogNext = DateTime.UtcNow + CoreLog.Every;
            resp["ok"] = why == null;
            if (why != null) resp["error"] = why;
            log.Write("core-log request: " + (why == null ? "answered" : why));
        }

        // -- stopping and unregistering ---------------------------------

        // The service is stopping: stop holding, stop the units cleanly,
        // then terminate the distro (PLAN 5.2's recovery ladder treats a
        // host restart as a distro restart).
        public void Shutdown(bool quick)
        {
            Process p = provision;
            if (p != null)
            {
                try { p.Kill(); } catch (InvalidOperationException) { } catch (System.ComponentModel.Win32Exception) { }
            }
            KillHolder();
            // Any wsl.exe -d call would start a stopped distro; only a
            // running one needs stopping.
            if (Wsl.Running() != true)
            {
                log.Write("the distro is not running");
                lock (statusGate) { status.State = "stopped"; status.Message = "DomovoiHost is stopped"; }
                WriteStatus(true);
                return;
            }
            if (!quick)
            {
                WslResult s = Wsl.Run(new[] { "-d", Paths.DistroName, "-u", "root", "--cd", "/", "--exec", "/usr/bin/systemctl", "stop", "domovoi-web", "domovoi-core", "domovoi-db" }, 40000, null, 8192);
                log.Write("systemctl stop exit " + Inv.S(s.ExitCode) + (s.TimedOut ? " (timed out)" : ""));
            }
            WslResult t = Wsl.Run(new[] { "--terminate", Paths.DistroName }, quick ? 8000 : 30000, null, 8192);
            log.Write("wsl --terminate exit " + Inv.S(t.ExitCode) + (t.TimedOut ? " (timed out)" : ""));
            lock (statusGate) { status.State = "stopped"; status.Message = "DomovoiHost is stopped"; }
            WriteStatus(true);
        }

        // The uninstaller reads unregister.json; a failure it can show beats
        // a host that stopped saying nothing.
        public void UnregisterFailed(Exception e)
        {
            try
            {
                Json.WriteFile(Paths.UnregisterResult, HostExit.UnregisterFailure(e, DateTime.UtcNow));
                Status("unregistered", HostProgress.PhaseConfig, 0, "Removing Domovoi's Linux system failed");
            }
            catch (IOException x) { log.Write("can't write unregister.json: " + x.Message); }
            catch (UnauthorizedAccessException x) { log.Write("can't write unregister.json: " + x.Message); }
        }

        public void Unregister()
        {
            CheckAccount();
            log.Write("unregister requested (uninstall)");
            Status("unregistering", HostProgress.PhaseConfig, 0, "Removing Domovoi's Linux system");
            var result = new Dictionary<string, object>();
            bool? reg = Wsl.Registered();
            if (reg == null)
            {
                result["ok"] = false;
                result["error"] = "WSL did not answer";
            }
            else if (!reg.Value)
            {
                result["ok"] = true;
                result["message"] = "no distro to unregister";
            }
            else
            {
                WslResult t = Wsl.Run(new[] { "--terminate", Paths.DistroName }, 60000, null, 8192);
                log.Write("wsl --terminate exit " + Inv.S(t.ExitCode) + Trailer(t));
                WslResult u = Wsl.Run(new[] { "--unregister", Paths.DistroName }, 10 * 60 * 1000, null, 8192);
                log.Write("wsl --unregister exit " + Inv.S(u.ExitCode) + Trailer(u));
                bool? still = Wsl.Registered();
                result["ok"] = u.ExitCode == 0 && still == false;
                if (!(bool)result["ok"]) result["error"] = "wsl --unregister failed (exit " + Inv.S(u.ExitCode) + "): " + FirstLine(u.Stdout + u.Stderr);
                else result["message"] = "unregistered";
            }
            if (result["ok"] is bool && (bool)result["ok"]) result["released"] = ReleaseWsl();
            result["at"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            try { Json.WriteFile(Paths.UnregisterResult, result); }
            catch (IOException e) { log.Write("can't write unregister.json: " + e.Message); }
            Status("unregistered", HostProgress.PhaseConfig, 0, "Removed");
        }

        // With the distro gone, this account's WSL VM still holds files in
        // its profile (C:\Users\domovoi-svc or wherever Windows made it:
        // WSL's temporary folder for the VM) until it idles out, and the uninstaller deletes that profile
        // next; on 2026-10-12 it was still held minutes later and stayed until
        // a restart. wsl --shutdown ends the CALLER's WSL session only: WSL's
        // service keeps one session per Windows user, found by the caller's
        // SID (microsoft/WSL 3.0.1, the version on the PC this was built on:
        // src/windows/service/exe/LxssUserSessionFactory.cpp,
        // CreateInstanceForCurrentUser and FindSessionLockHeld), and the
        // shutdown acts on that session alone (LxssUserSession.cpp:521-528,
        // LxssUserSession::Shutdown, then LxssUserSessionImpl::Shutdown). So
        // the household's own WSL distros and Docker Desktop's run on. It
        // runs here, as domovoi-svc, and nowhere else: never from the helper,
        // which runs as the household's administrator.
        bool ReleaseWsl()
        {
            WslResult r = Wsl.Run(new[] { "--shutdown" }, 120000, null, 8192);
            log.Write("wsl --shutdown as " + Paths.AccountName + " (this account's WSL session only) exit " + Inv.S(r.ExitCode)
                + (r.TimedOut ? " (timed out)" : "") + Trailer(r));
            return r.ExitCode == 0;
        }
    }
}
