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
//   6. answer one-off requests (the setup code);
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

        Dictionary<string, object> ReadState()
        {
            try { return Json.ReadFile(StateFile) ?? new Dictionary<string, object>(); }
            catch (FormatException) { return new Dictionary<string, object>(); }
            catch (IOException) { return new Dictionary<string, object>(); }
        }

        void UpdateState(string key, object value)
        {
            var d = ReadState();
            d[key] = value;
            d["at"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            try { Json.WriteFile(StateFile, d); }
            catch (IOException e) { log.Write("can't record " + key + ": " + e.Message); }
            catch (UnauthorizedAccessException e) { log.Write("can't record " + key + ": " + e.Message); }
        }

        bool AlreadyProvisioned(HostJob job)
        {
            var d = ReadState();
            return Json.Int(d, "generation") == job.Generation && Json.Int(d, "attempt") == job.Attempt && Json.Str(d, "result") == "ok";
        }

        void RecordProvisioned(HostJob job, string result)
        {
            var d = ReadState();
            d["generation"] = job.Generation;
            d["attempt"] = job.Attempt;
            d["result"] = result;
            d["at"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            try { Json.WriteFile(StateFile, d); }
            catch (IOException e) { log.Write("can't record the result: " + e.Message); }
            catch (UnauthorizedAccessException e) { log.Write("can't record the result: " + e.Message); }
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
            while (!Stopping)
            {
                HostJob job = ReadJob();
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
                }
                else
                {
                    KeepHolder();
                    if (ready && holder != null && SafeExited(holder))
                        WriteStatus(false);
                }
                ProcessRequests(ready);
                if (DateTime.UtcNow - lastStatusWrite > TimeSpan.FromSeconds(10)) WriteStatus(true);
                if (Sleep(1000)) break;
            }
            log.Write("DomovoiHost stopping");
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
            return true;
        }

        bool WriteWslConfig(HostJob job)
        {
            // This account's own profile, wherever Windows made it (WslConfig.FileIn).
            string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            if (string.IsNullOrEmpty(profile) || !Directory.Exists(profile))
            {
                Fail("the account's profile folder is missing", null);
                return false;
            }
            string path = WslConfig.FileIn(profile);
            string text = job.WslConfig.Replace("\r\n", "\n").Replace("\n", "\r\n");
            try
            {
                if (File.Exists(path) && File.ReadAllText(path) == text) return true;
                bool existed = File.Exists(path);
                AtomicFile.WriteAllText(path, text);
                log.Write("wrote " + path + (existed ? " (changed; WSL applies it when this account's VM next starts)" : ""));
                return true;
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is UnauthorizedAccessException)) throw;
                Fail("can't write " + path + ": " + e.Message, null);
                return false;
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
            int restarts = 0, lost = 0, busy = 0;
            bool verifying = false;
            // The port check right before the start step (owner fixes
            // 2026-10-12, item 2): the first pass stops before "start".
            var gate = new PortGate(canStopBeforeStart);
            if (!gate.Pending) log.Write("the pinned provision.sh has no --stop-before: the ports are checked again only if the start fails");
            while (!Stopping)
            {
                ProvisionLine final;
                int code = RunProvision(job, verifying, gate.Args(job.ProvisionArgs), out final);
                if (Stopping) return false;
                if (final != null)
                {
                    lock (statusGate)
                    {
                        status.Head = final.Head;
                        status.Tier = final.Tier;
                        if (final.Gpu != null) { status.Gpu = final.Gpu; status.GpuKnown = true; }
                    }
                }
                string why;
                ProvisionVerdict v = ProvisionOutcome.Classify(code, final, out why);
                log.Write("provision.sh exit " + Inv.S(code) + ": " + v + (why.Length > 0 ? " (" + why + ")" : ""));
                switch (v)
                {
                    case ProvisionVerdict.Done:
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
