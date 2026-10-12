// The install verbs, in the order Setup calls them after copying its files:
// prepare, account, media, firewall, job, host-start, then host-status
// until the host says ready (or failed: host-retry), then setup-code.
// Each one is safe to run again: Setup's Retry and a repair run them all
// a second time.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Threading;

namespace Domovoi.Windows.Helper
{
    static class Steps
    {
        public const string ImageName = "ubuntu-26.04.1-wsl-amd64.wsl";
        public const string ProvisionName = "provision.sh";

        // -- the ledger -------------------------------------------------

        public static Ledger LoadLedger()
        {
            var d = Json.ReadFile(Paths.Ledger);
            return d == null ? null : Ledger.FromJson(d);
        }

        static Ledger NeedLedger()
        {
            Ledger l = LoadLedger();
            if (l == null) throw new InvalidOperationException("install.json is missing: run the prepare step first");
            return l;
        }

        public static void SaveLedger(Ledger l)
        {
            l.Updated = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            Json.WriteFile(Paths.Ledger, l.ToJson());
        }

        // -- prepare: the admin-only tree and the answers ---------------

        public static void Prepare(Output o, string internet, string mediaRoot, string commit)
        {
            if (internet != "always" && internet != "sometimes" && internet != "never")
                throw new ArgumentException("--internet must be always, sometimes or never");
            Folders.CheckMediaRoot(mediaRoot);
            if (commit.Length != 40) throw new ArgumentException("--commit must be a full SHA");
            bool made = Folders.EnsureProgramDataBase(o.Say);
            Ledger l = LoadLedger() ?? new Ledger();
            if (made) l.ProgramDataCreated = true;
            l.Internet = internet;
            l.MediaRoot = MediaPaths.Normalize(mediaRoot);
            l.Commit = commit;
            SaveLedger(l);
            o.Say("recorded the answers (internet: " + internet + "; media folder: " + l.MediaRoot + ")");
        }

        // -- the account and the service --------------------------------

        public static void AccountAndService(Output o, string exe, bool denyLogon)
        {
            string expected = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Domovoi\DomovoiHost.exe");
            if (!string.Equals(Path.GetFullPath(exe), expected, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("DomovoiHost.exe must be the one in " + expected + " (an admin-owned folder)");
            if (!File.Exists(exe)) throw new FileNotFoundException("DomovoiHost.exe is missing", exe);
            Ledger l = NeedLedger();
            // A stopped service before its account's password changes.
            using (var existing = HostService.Find()) if (existing != null) HostService.Stop(180, o.Say);
            using (Secret pw = Secret.NewPassword())
            {
                bool created = Account.CreateOrRotate(pw, l.AccountCreated, o.Say);
                if (created) { l.AccountCreated = true; SaveLedger(l); }
                SecurityIdentifier sid = Account.Sid();
                l.AccountSid = sid.Value;
                SaveLedger(l);
                Account.JoinUsers(sid);
                bool madeSpecial, madeUserList;
                Account.Hide(out madeSpecial, out madeUserList);
                if (madeSpecial) l.SpecialAccountsKeyCreated = true;
                if (madeUserList) l.UserListKeyCreated = true;
                SaveLedger(l);
                o.Say("hid " + Paths.AccountName + " from the sign-in screen (HKLM\\" + Account.UserListKey + ")");
                var rights = new List<string>(Account.BaseRights);
                if (denyLogon) rights.AddRange(Account.DenyRights);
                Account.AddRights(sid, rights);
                foreach (string r in rights) if (!l.Rights.Contains(r)) l.Rights.Add(r);
                SaveLedger(l);
                o.Say("granted " + Paths.AccountName + ": " + string.Join(", ", rights.ToArray()));
                if (Folders.EnsureProgramData(sid, o.Say)) l.ProgramDataCreated = true;
                o.Say("set the access rules on " + Paths.ProgramData);
                bool svcCreated = HostService.CreateOrUpdate(exe, pw, o.Say);
                if (svcCreated) l.ServiceCreated = true;
                SaveLedger(l);
            }
            o.Kv("account_sid", l.AccountSid);
        }

        // -- media folders ----------------------------------------------

        public static void Media(Output o)
        {
            Ledger l = NeedLedger();
            if (l.AccountSid == null) throw new InvalidOperationException("the account step has not run");
            var sid = new SecurityIdentifier(l.AccountSid);
            Folders.CheckMediaRoot(l.MediaRoot);
            Folders.EnsureMediaFolder(l.MediaRoot, sid, true, l, o.Say);
            SaveLedger(l);
            foreach (var m in MediaPaths.Under(l.MediaRoot))
            {
                Folders.EnsureMediaFolder(m.Value, sid, false, l, o.Say);
                SaveLedger(l);
            }
        }

        // -- firewall ---------------------------------------------------

        public static string PowerShell(string script, Output o)
        {
            string encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
            var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"),
                "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " + encoded);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            using (var p = Process.Start(psi))
            {
                var err = new StringBuilder();
                p.ErrorDataReceived += delegate (object s, DataReceivedEventArgs e) { if (e.Data != null) lock (err) err.AppendLine(e.Data); };
                p.BeginErrorReadLine();
                string text = p.StandardOutput.ReadToEnd();
                if (!p.WaitForExit(180000)) { p.Kill(); throw new System.TimeoutException("PowerShell did not finish"); }
                p.WaitForExit();
                if (p.ExitCode != 0)
                {
                    string e;
                    lock (err) e = err.ToString().Trim();
                    throw new InvalidOperationException("PowerShell failed (exit " + Inv.S(p.ExitCode) + "): " + (e.Length > 600 ? e.Substring(0, 600) : e));
                }
                return text;
            }
        }

        public static List<string> FirewallRuleNames(Output o)
        {
            var names = new List<string>();
            foreach (string line in PowerShell(Firewall.ListScript(), o).Split('\n'))
            {
                string t = line.Trim();
                if (t.StartsWith("rule ", StringComparison.Ordinal)) names.Add(t.Substring(5));
            }
            return names;
        }

        public static void FirewallRules(Output o, string subnet)
        {
            Ledger l = NeedLedger();
            if (string.IsNullOrEmpty(subnet) || subnet == "auto")
            {
                Preflight.LanInfo lan = Preflight.Lan();
                if (lan == null || !lan.Private)
                {
                    o.Say("added no firewall rules: this PC has no private home-network address (Domovoi answers on this PC only)");
                    o.Kv("firewall", "none");
                    return;
                }
                subnet = lan.Subnet;
            }
            List<FirewallRule> rules = Firewall.Plan(subnet);
            l.FirewallSubnet = subnet;
            foreach (var r in rules) if (!l.FirewallRules.Contains(r.Name)) l.FirewallRules.Add(r.Name);
            SaveLedger(l);
            string outText = PowerShell(Firewall.AddScript(rules), o);
            foreach (string line in outText.Split('\n')) if (line.Trim().Length > 0) o.LogOnly("firewall: " + line.Trim());
            o.Say("added Hyper-V firewall rules for WSL (inbound TCP from " + subnet + "): "
                + string.Join(", ", rules.ConvertAll(r => r.Name + " " + r.LocalPorts).ToArray()));
            o.Kv("firewall", subnet);
        }

        // -- the host's job ---------------------------------------------

        static void CopyPinned(string src, string dest, string sha, Output o)
        {
            if (File.Exists(dest) && Sha256.OfFile(dest) == sha) return;
            if (!File.Exists(src)) throw new FileNotFoundException("missing download", src);
            if (Sha256.OfFile(src) != sha) throw new InvalidOperationException(Path.GetFileName(src) + " does not match its pinned SHA-256");
            File.Copy(src, dest, true);
            if (Sha256.OfFile(dest) != sha) throw new InvalidOperationException("the copy of " + Path.GetFileName(src) + " does not match its pin");
            o.Say("copied " + Path.GetFileName(src) + " to " + dest + " (SHA-256 checked)");
        }

        public static void WriteJob(Output o, string image, string imageSha, string provision, string provisionSha, string repoUrl, string commit, string lanIp)
        {
            Ledger l = NeedLedger();
            if (!Sha256.IsHex(imageSha) || !Sha256.IsHex(provisionSha)) throw new ArgumentException("pins must be SHA-256 hex");
            string imageDest = Path.Combine(Paths.Wsl, ImageName);
            string provDest = Path.Combine(Paths.Wsl, ProvisionName);
            CopyPinned(image, imageDest, imageSha, o);
            CopyPinned(provision, provDest, provisionSha, o);

            var opts = new ProvisionOptions();
            opts.RepoUrl = repoUrl;
            opts.Ref = commit;
            opts.Internet = l.Internet;
            if (lanIp == "auto")
            {
                Preflight.LanInfo lan = Preflight.Lan();
                opts.LanIp = lan != null && lan.Private ? lan.Ip : null;
            }
            else if (lanIp != "none") opts.LanIp = lanIp;
            opts.Media = MediaPaths.Under(l.MediaRoot);

            var w = new WslConfigOptions();
            w.MemoryGb = WslConfig.MemoryCapGb(Preflight.InstalledRamBytes());
            w.SwapFile = Paths.SwapFile;

            var job = new HostJob();
            job.Image = imageDest;
            job.ImageSha256 = imageSha;
            job.Provision = provDest;
            job.ProvisionSha256 = provisionSha;
            job.ProvisionArgs = ProvisionArgs.Build(opts);
            job.WslConfig = WslConfig.Render(w);

            // Same inputs, same generation: the host then skips work it did.
            HostJob old = null;
            try { var d = Json.ReadFile(Paths.Job); if (d != null) old = HostJob.FromJson(d); }
            catch (FormatException) { old = null; }
            bool same = old != null && old.ImageSha256 == job.ImageSha256 && old.ProvisionSha256 == job.ProvisionSha256
                && string.Join("\n", old.ProvisionArgs.ToArray()) == string.Join("\n", job.ProvisionArgs.ToArray())
                && old.WslConfig == job.WslConfig;
            if (!same) l.JobGeneration++;
            if (l.JobGeneration == 0) l.JobGeneration = 1;
            job.Generation = l.JobGeneration;
            job.Attempt = l.JobAttempt;
            Json.WriteFile(Paths.Job, job.ToJson());
            SaveLedger(l);
            o.Say("wrote the host's job (generation " + Inv.S(job.Generation) + "): provision.sh " + string.Join(" ", job.ProvisionArgs.ToArray()));
            o.Say("the account's .wslconfig will cap its WSL VM at " + Inv.S(w.MemoryGb) + " GB, mirrored networking");
        }

        // -- the host service -------------------------------------------

        public static void HostStart(Output o)
        {
            HostService.Start(null, 120);
            o.Say("started " + Paths.ServiceName);
        }

        public static void HostRetry(Output o)
        {
            Ledger l = NeedLedger();
            var d = Json.ReadFile(Paths.Job);
            if (d == null) throw new InvalidOperationException("no job to retry");
            HostJob job = HostJob.FromJson(d);
            l.JobAttempt = job.Attempt + 1;
            job.Attempt = l.JobAttempt;
            Json.WriteFile(Paths.Job, job.ToJson());
            SaveLedger(l);
            HostService.Start(null, 120);
            o.Say("asked " + Paths.ServiceName + " to try again (attempt " + Inv.S(job.Attempt) + ")");
            o.Kv("attempt", Inv.S(job.Attempt));
        }

        public static void HostStatusOut(Output o)
        {
            string svc = "missing";
            using (var s = HostService.Find())
            {
                if (s != null)
                {
                    s.Refresh();
                    svc = s.Status == ServiceControllerStatus.Running ? "running"
                        : s.Status == ServiceControllerStatus.Stopped ? "stopped" : "pending";
                }
            }
            o.Kv("service", svc);
            Dictionary<string, object> d = null;
            try { d = Json.ReadFile(Paths.Status); }
            catch (FormatException) { }
            catch (IOException) { }
            if (d == null) { o.Kv("state", "none"); return; }
            HostStatus st = HostStatus.FromJson(d);
            o.Kv("state", st.State);
            o.Kv("percent", Inv.S(st.Percent));
            o.Kv("message", st.Message);
            o.Kv("step", st.Step ?? "");
            o.Kv("host_error", st.Error ?? "");
            o.Kv("failed_step", st.FailedStep ?? "");
            o.Kv("tier", st.Tier ?? "");
            o.Kv("gpu", st.GpuKnown ? (st.Gpu.Count == 0 ? "none" : string.Join(", ", st.Gpu.ToArray())) : "");
            o.Kv("generation", Inv.S(st.Generation));
            o.Kv("attempt", Inv.S(st.Attempt));
            if (st.Bytes != null) o.Kv("bytes", Inv.S(st.Bytes.Value));
            if (st.BytesTotal != null) o.Kv("bytes_total", Inv.S(st.BytesTotal.Value));
            DateTime updated;
            if (DateTime.TryParse(st.Updated, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out updated))
                o.Kv("age_sec", Inv.S((long)(DateTime.UtcNow - updated.ToUniversalTime()).TotalSeconds));
        }

        // The setup code, through the host (PLAN 4.12 step 5): printed as
        // code=... for Setup's finish page, and nowhere else.
        public static int ReadSetupCode(Output o)
        {
            using (var key = new SetupCodeKey())
            {
                string id = Guid.NewGuid().ToString("N");
                var req = new Dictionary<string, object>();
                req["v"] = 1;
                req["verb"] = "setup-code";
                req["public_key"] = key.PublicKey;
                string reqFile = Paths.RequestFile(id), respFile = Paths.ResponseFile(id);
                Json.WriteFile(reqFile, req);
                try
                {
                    DateTime until = DateTime.UtcNow.AddSeconds(120);
                    while (!File.Exists(respFile))
                    {
                        if (DateTime.UtcNow > until) throw new System.TimeoutException("DomovoiHost did not answer the setup-code request");
                        Thread.Sleep(500);
                    }
                    Thread.Sleep(200);
                    var resp = Json.ReadFile(respFile);
                    if (Json.Bool(resp, "ok") != true) throw new InvalidOperationException(Json.Str(resp, "error") ?? "no setup code");
                    string code = key.Decrypt(Json.Str(resp, "ciphertext"));
                    if (!SetupCode.IsWellFormed(code)) throw new InvalidOperationException("the answer is not a setup code");
                    o.Kv("code", code);
                    o.LogOnly("read the setup code through DomovoiHost (shown on Setup's last page only)");
                    return 0;
                }
                finally
                {
                    try { File.Delete(reqFile); } catch (IOException) { }
                    try { File.Delete(respFile); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                }
            }
        }

        // -- plan: what Setup will change on this PC (read-only) --------

        public static void Plan(Output o)
        {
            Preflight.LanInfo lan = Preflight.Lan();
            long ram = Preflight.InstalledRamBytes();
            o.Kv("change", "create the hidden local account " + Paths.AccountName + " (standard user, random 40-character password never shown or stored, password never expires, hidden from the sign-in screen)");
            o.Kv("change", "grant it SeServiceLogonRight and deny it interactive, remote-desktop and network sign-in (LSA account rights)");
            o.Kv("change", "create " + Paths.ProgramData + " (SYSTEM and Administrators; " + Paths.AccountName + " where it needs it)");
            o.Kv("change", "register the " + Paths.ServiceName + " service (Automatic, runs as " + HostService.Account + ", depends on WSLService)");
            o.Kv("change", "create the media folders under " + MediaPaths.DefaultRoot + " (unless chosen otherwise) and allow " + Paths.AccountName + " to read and write them");
            if (lan != null && lan.Private)
                o.Kv("change", "add six Hyper-V firewall rules for WSL, inbound TCP from " + lan.Subnet + ": 6369, 6370, 8050-8099, 8096, 7676, 9467");
            o.Kv("change", "as " + Paths.AccountName + ": write its .wslconfig (memory " + Inv.S(WslConfig.MemoryCapGb(ram)) + " GB, mirrored networking) and import the WSL distro 'domovoi' into " + Paths.Distro);
            if (!Preflight.VmpEnabled()) o.Kv("change", "turn on the Windows feature VirtualMachinePlatform (restart)");
            if (Preflight.WslStatus(Preflight.WslVersion()) != "ok") o.Kv("change", "install the WSL package 2.7.14 (restart)");
        }
    }
}
