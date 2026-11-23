// The install verbs, in the order Setup calls them after copying its files:
// prepare, account, media, firewall, job, host-start, then host-status
// until the host says ready (or failed: host-retry), then setup-code.
// Each one is safe to run again: Setup's Retry and a repair run them all
// a second time.
//
// The install record (the ledger) is written before each change it
// records ("about to make X"), so a run killed in between still leaves the
// uninstaller a note of everything that may exist.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Threading;
using Microsoft.Win32;
using Domovoi.Windows.Tray;

namespace Domovoi.Windows.Helper
{
    static class Steps
    {
        public const string ImageName = "ubuntu-26.04.1-wsl-amd64.wsl";
        public const string ProvisionName = "provision.sh";

        // -- the install record (HKLM\SOFTWARE\Domovoi, value Install) ---
        // Admin-written, readable by everyone (it holds no secret), and
        // outside C:\ProgramData\Domovoi, so it outlives a half-finished
        // uninstall: the uninstaller deletes it last, and only when nothing
        // is left over.

        public static Ledger LoadLedger()
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(Paths.RecordKey))
            {
                string text = k == null ? null : k.GetValue(Paths.RecordValue) as string;
                return text == null ? null : Ledger.FromJson(Json.ParseObject(text));
            }
        }

        public static bool RecordExists()
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(Paths.RecordKey))
                return k != null && k.GetValue(Paths.RecordValue) != null;
        }

        static Ledger NeedLedger()
        {
            Ledger l = LoadLedger();
            if (l == null) throw new InvalidOperationException("Setup's install record is missing: run the prepare step first");
            return l;
        }

        public static void SaveLedger(Ledger l)
        {
            l.Updated = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.CreateSubKey(Paths.RecordKey))
                k.SetValue(Paths.RecordValue, Json.Serialize(l.ToJson()), RegistryValueKind.String);
        }

        public static void DeleteRecord(Output o)
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            {
                using (var k = hklm.OpenSubKey(Paths.RecordKey, true))
                {
                    if (k == null) return;
                    k.DeleteValue(Paths.RecordValue, false);
                    if (k.ValueCount > 0 || k.SubKeyCount > 0) { o.Say("kept HKLM\\" + Paths.RecordKey + " (it holds other entries)"); return; }
                }
                hklm.DeleteSubKey(Paths.RecordKey, false);
                o.Say("deleted Setup's install record HKLM\\" + Paths.RecordKey);
            }
        }

        // -- prepare: the admin-only tree and the answers ---------------

        public static void Prepare(Output o, string internet, string ai, string mediaRoot, string commit)
        {
            if (internet != "always" && internet != "sometimes" && internet != "never")
                throw new ArgumentException("--internet must be always, sometimes or never");
            if (ai != "yes" && ai != "no" && ai != "keep") throw new ArgumentException("--ai must be yes, no or keep");
            Folders.CheckMediaRoot(mediaRoot);
            if (commit.Length != 40) throw new ArgumentException("--commit must be a full SHA");
            Ledger l = LoadLedger() ?? new Ledger();
            if (!Directory.Exists(Paths.ProgramData)) { l.ProgramDataCreated = true; SaveLedger(l); }
            Folders.EnsureProgramDataBase(l.ProgramDataCreated, o.Say);
            l.Internet = internet;
            RecordAiAnswer(l, ai);
            l.MediaRoot = MediaPaths.Normalize(mediaRoot);
            l.Commit = commit;
            SaveLedger(l);
            o.Say("recorded the answers (internet: " + internet + "; AI chat and questions: " + ai + "; media folder: " + l.MediaRoot + ")");
        }

        // prepare --ai: yes or no, said out loud to provision.sh; keep (Setup's
        // page left at a Yes that came from this record alone, the
        // household's own value unread): neither option, so provision.sh
        // keeps domovoi/.env's. A keep over a recorded No is a No: Setup
        // never pulls the models on a guess.
        public static void RecordAiAnswer(Ledger l, string ai)
        {
            if (ai == "keep" && l.IncludeAi)
            {
                l.AiKeep = true;
                return;
            }
            l.AiKeep = false;
            l.IncludeAi = ai == "yes";
        }

        // -- saved-ai: what Setup's AI page starts at on a re-run ---------
        // Prints ai_default=yes|no and ai_from=household|record|none
        // (SavedAiAnswer). The household's value comes from DomovoiHost (a
        // one-off request: the distro is the hidden account's), only while
        // it says Domovoi is ready; an older host, or none, leaves the
        // record's answer.

        public static void SavedAi(Output o)
        {
            Ledger l = null;
            try { l = LoadLedger(); }
            catch (FormatException) { l = null; }
            bool? household = null;
            if (l != null)
            {
                string why;
                household = AskHouseholdAi(15, out why);
                o.LogOnly(household == null ? "the household's AI_FEATURES: not read (" + why + ")"
                    : "the household's AI_FEATURES reads " + (household.Value ? "on" : "off"));
            }
            SavedAiAnswer a = SavedAiAnswer.Decide(l, household);
            o.Kv("ai_default", a.Yes ? "yes" : "no");
            o.Kv("ai_from", a.From);
        }

        static bool? AskHouseholdAi(int timeoutSec, out string why)
        {
            using (var s = HostService.Find())
            {
                if (s == null) { why = "no " + Paths.ServiceName; return null; }
                s.Refresh();
                if (s.Status != ServiceControllerStatus.Running) { why = Paths.ServiceName + " is not running"; return null; }
            }
            Dictionary<string, object> status = null;
            try { status = ReadHostFile(Paths.Status); }
            catch (FormatException) { }
            catch (IOException) { }
            if (status == null || HostStatus.FromJson(status).State != "ready") { why = "Domovoi is not running"; return null; }
            if (!Directory.Exists(Paths.HostIn)) { why = "no " + Paths.HostIn; return null; }
            string id = Guid.NewGuid().ToString("N");
            var req = new Dictionary<string, object>();
            req["v"] = 1;
            req["verb"] = "ai-features";
            string reqFile = Paths.RequestFile(id), respFile = Paths.ResponseFile(id);
            Json.WriteFile(reqFile, req);
            try
            {
                DateTime until = DateTime.UtcNow.AddSeconds(timeoutSec);
                while (!File.Exists(respFile))
                {
                    if (DateTime.UtcNow > until) { why = Paths.ServiceName + " did not answer"; return null; }
                    Thread.Sleep(300);
                }
                Thread.Sleep(200);
                Dictionary<string, object> resp;
                try { resp = ReadHostFile(respFile); }
                catch (FormatException e) { why = e.Message; return null; }
                bool? value = resp == null ? null : Json.Bool(resp, "ai_features");
                if (resp == null || Json.Bool(resp, "ok") != true || value == null)
                {
                    why = resp == null ? "no answer" : (Json.Str(resp, "error") ?? "no value");
                    return null;
                }
                why = "";
                return value;
            }
            finally
            {
                try { File.Delete(reqFile); } catch (IOException) { }
            }
        }

        // -- record-ai: the install record takes what provision.sh said ---
        // Once DomovoiHost reports Domovoi ready, its status carries the
        // final line's "ai"; the record keeps that (AiReport). Prints
        // ai=yes|no, or ai= when the host didn't say.

        public static void RecordAi(Output o)
        {
            Ledger l = NeedLedger();
            Dictionary<string, object> d = null;
            try { d = ReadHostFile(Paths.Status); }
            catch (FormatException) { }
            catch (IOException) { }
            HostStatus st = d == null ? null : HostStatus.FromJson(d);
            bool? ai = AiReport.Reported(st);
            if (AiReport.Apply(l, st))
            {
                SaveLedger(l);
                o.Say("recorded what provision.sh reported: AI chat and questions " + (ai.Value ? "on" : "off"));
            }
            o.Kv("ai", ai == null ? "" : (ai.Value ? "yes" : "no"));
        }

        // -- the account and the service --------------------------------

        public static void AccountAndService(Output o, string exe, bool denyLogon)
        {
            string expected = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Domovoi\DomovoiHost.exe");
            if (!string.Equals(Path.GetFullPath(exe), expected, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("DomovoiHost.exe must be the one in " + expected + " (an admin-owned folder)");
            if (!File.Exists(exe)) throw new FileNotFoundException("DomovoiHost.exe is missing", exe);
            Ledger l = NeedLedger();
            // A repair: the service stopped, and nothing left running as the
            // account, before its password changes and its folders are reset.
            using (var existing = HostService.Find()) if (existing != null) HostService.Stop(240, o.Say);
            if (l.AccountSid != null) Processes.StopAccount(new SecurityIdentifier(l.AccountSid), o.Say);
            using (Secret pw = Secret.NewPassword())
            {
                if (!Account.Get().Exists) { l.AccountCreated = true; SaveLedger(l); }
                Account.CreateOrRotate(pw, l.AccountCreated, o.Say);
                SecurityIdentifier sid = Account.Sid();
                l.AccountSid = sid.Value;
                SaveLedger(l);
                Account.JoinUsers(sid);
                bool missingSpecial, missingUserList;
                Account.MissingHideKeys(out missingSpecial, out missingUserList);
                if (missingSpecial) l.SpecialAccountsKeyCreated = true;
                if (missingUserList) l.UserListKeyCreated = true;
                SaveLedger(l);
                Account.Hide();
                o.Say("hid " + Paths.AccountName + " from the sign-in screen (HKLM\\" + Account.UserListKey + ")");
                var rights = new List<string>(Account.BaseRights);
                if (denyLogon) rights.AddRange(Account.DenyRights);
                foreach (string r in rights) if (!l.Rights.Contains(r)) l.Rights.Add(r);
                SaveLedger(l);
                Account.AddRights(sid, rights);
                o.Say("granted " + Paths.AccountName + ": " + string.Join(", ", rights.ToArray()));
                if (!denyLogon)
                {
                    // /nodenylogon=1 on a later run: take back what an earlier one granted.
                    Account.RemoveRights(sid, Account.DenyRights);
                    foreach (string r in Account.DenyRights) l.Rights.Remove(r);
                    SaveLedger(l);
                    o.Say("removed the deny sign-in rights from " + Paths.AccountName + " (if it had them)");
                }
                Folders.EnsureProgramData(sid, l.ProgramDataCreated, o.Say);
                o.Say("set the access rules on " + Paths.ProgramData);
                using (var svc = HostService.Find()) if (svc == null) { l.ServiceCreated = true; SaveLedger(l); }
                HostService.CreateOrUpdate(exe, pw, o.Say);
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
            Action save = delegate { SaveLedger(l); };
            Folders.EnsureMediaFolder(l.MediaRoot, sid, true, l, save, o.Say);
            foreach (var m in MediaPaths.Under(l.MediaRoot))
                Folders.EnsureMediaFolder(m.Value, sid, false, l, save, o.Say);
        }

        public static void CheckMedia(Output o, string root)
        {
            Folders.CheckMediaRoot(root);
            o.Kv("media", "ok");
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

        // Throws when the rules can't be listed: a failed listing is never
        // "no rules".
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
            // WIN-10: port 80 only while the short address can use it: its
            // switch as DomovoiHost last read it (host-state.json; unknown on
            // a first install, when it is on by default) and no Windows
            // program holding the port.
            bool? switchOn = null;
            try { switchOn = Json.Bool(new HostStateStore(Path.Combine(Paths.HostOut, "host-state.json")).Read(), ShortAddressSwitch.HostStateKey); }
            catch (Exception e) { o.LogOnly("firewall: can't read the short address's switch: " + e.Message); }
            string port80 = null;
            try { port80 = ShortAddressCheck.State(PortScan.Listeners()); }
            catch (Exception e) { o.LogOnly("firewall: can't check port 80: " + e.Message); }
            bool shortAddress = ShortAddressSwitch.RuleWanted(switchOn, port80);
            if (!shortAddress)
                o.LogOnly("firewall: no port-80 rule (the short address is "
                    + (switchOn == false ? "switched off" : "blocked: port 80 is " + port80) + ")");
            List<FirewallRule> rules = Firewall.Plan(subnet, shortAddress);
            l.FirewallSubnet = subnet;
            foreach (var r in rules) if (!l.FirewallRules.Contains(r.Name)) l.FirewallRules.Add(r.Name);
            if (!l.FirewallRules.Contains(Firewall.MdnsRuleName)) l.FirewallRules.Add(Firewall.MdnsRuleName);
            SaveLedger(l);
            string outText = PowerShell(Firewall.AddScript(rules), o);
            foreach (string line in outText.Split('\n')) if (line.Trim().Length > 0) o.LogOnly("firewall: " + line.Trim());
            o.Say("added Hyper-V firewall rules for WSL (inbound TCP from " + subnet + "): "
                + string.Join(", ", rules.ConvertAll(r => r.Name + " " + r.LocalPorts).ToArray()));
            // The network name (MdnsResponder.cs): after the Hyper-V rules,
            // whose script takes every Domovoi rule away first.
            outText = PowerShell(Firewall.MdnsAddScript(HostExe), o);
            foreach (string line in outText.Split('\n')) if (line.Trim().Length > 0) o.LogOnly("firewall: " + line.Trim());
            o.Say("added the firewall rule " + Firewall.MdnsRuleName + " (inbound UDP 5353 for DomovoiHost.exe, from the local subnet, on "
                + Firewall.MdnsProfiles.Replace(",", " and ") + " networks): other devices find this PC as domovoi.local");
            o.Kv("firewall", subnet);
        }

        // Where Setup installs DomovoiHost.exe (an admin-owned folder).
        public static string HostExe
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Domovoi\DomovoiHost.exe"); }
        }

        // -- the host's job ---------------------------------------------

        // The pinned downloads go to host\in, which the account can read but
        // not write: the distro can't swap them under the host.
        static void CopyPinned(string src, string dest, string sha, Output o)
        {
            if (File.Exists(dest) && Sha256.OfFile(dest) == sha) return;
            if (!File.Exists(src)) throw new FileNotFoundException("missing download", src);
            if (Sha256.OfFile(src) != sha) throw new InvalidOperationException(Path.GetFileName(src) + " does not match its pinned SHA-256");
            File.Copy(src, dest, true);
            if (Sha256.OfFile(dest) != sha) throw new InvalidOperationException("the copy of " + Path.GetFileName(src) + " does not match its pin");
            o.Say("copied " + Path.GetFileName(src) + " to " + dest + " (SHA-256 checked)");
        }

        // The provision.sh options from the recorded answers (all but the LAN
        // address, which WriteJob looks up). scriptText: the pinned
        // provision.sh, for the options it takes.
        public static ProvisionOptions JobOptions(Ledger l, string repoUrl, string commit, string trackBranch, string scriptText)
        {
            var opts = new ProvisionOptions();
            opts.RepoUrl = repoUrl;
            opts.Ref = commit;
            opts.TrackBranch = trackBranch;
            opts.Internet = l.Internet;
            opts.IncludeAi = l.IncludeAi;
            opts.KeepAi = l.AiKeep;
            opts.ScriptHasAiChoice = ProvisionScript.SupportsAiChoice(scriptText);
            opts.Media = MediaPaths.Under(l.MediaRoot);
            return opts;
        }

        public static void WriteJob(Output o, string image, string imageSha, string provision, string provisionSha, string repoUrl, string commit, string trackBranch, string lanIp, string kit,
            string restoreIdentity, bool importInPlace)
        {
            Ledger l = NeedLedger();
            // From an offline kit: the image and provision.sh are read off
            // the stick, each copied only when it matches its pin, and the
            // host mounts the kit in the distro for provision.sh.
            string kitRoot = null;
            if (kit != null)
            {
                kitRoot = OfflineKit.NormalizeRoot(kit);
                OfflineKit.DrvfsSource(kitRoot);
            }
            if (!Sha256.IsHex(imageSha) || !Sha256.IsHex(provisionSha)) throw new ArgumentException("pins must be SHA-256 hex");
            string imageDest = Path.Combine(Paths.HostIn, ImageName);
            string provDest = Path.Combine(Paths.HostIn, ProvisionName);
            CopyPinned(image, imageDest, imageSha, o);
            CopyPinned(provision, provDest, provisionSha, o);

            // The copy just checked against its pin: what DomovoiHost runs.
            string scriptText = File.ReadAllText(provDest, Encoding.UTF8);
            ProvisionOptions opts = JobOptions(l, repoUrl, commit, trackBranch, scriptText);
            if (kitRoot != null)
            {
                opts.OfflineKit = OfflineKit.MountPoint;
                opts.ScriptHasOfflineKit = ProvisionScript.SupportsOfflineKit(scriptText);
            }
            // S3-4: a house identity backup, copied into host\in (the account
            // reads, can't change it). Only its shape is looked at here; the
            // service user's python in the distro checks the key pair before
            // it is used (provision.sh --restore-identity). Never logged.
            string restoreSha = null;
            if (restoreIdentity != null)
            {
                byte[] doc = KeptData.ReadBackup(restoreIdentity);
                File.WriteAllBytes(Paths.RestoreIdentityFile, doc);
                restoreSha = Sha256.OfFile(Paths.RestoreIdentityFile);
                opts.RestoreIdentity = ProvisionArgs.InDistroRestoreIdentity;
                opts.ScriptHasRestoreIdentity = ProvisionScript.SupportsRestoreIdentity(scriptText);
                o.Say("the house identity backup goes to the new install (checked inside before it is used)");
            }
            if (importInPlace && !File.Exists(Paths.DistroDisk))
                throw new InvalidOperationException("--import-in-place: there is no kept disk in " + Paths.Distro + " (adopt-kept first)");
            if (lanIp == "auto")
            {
                Preflight.LanInfo lan = Preflight.Lan();
                opts.LanIp = lan != null && lan.Private ? lan.Ip : null;
            }
            else if (lanIp != "none") opts.LanIp = lanIp;

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
            job.Kit = kitRoot;
            if (restoreSha != null)
            {
                job.RestoreIdentity = Paths.RestoreIdentityFile;
                job.RestoreIdentitySha256 = restoreSha;
            }
            job.ImportInPlace = importInPlace;

            // Same inputs, same generation: the host then skips work it did.
            HostJob old = null;
            try { var d = Json.ReadFile(Paths.Job); if (d != null) old = HostJob.FromJson(d); }
            catch (FormatException) { old = null; }
            bool same = old != null && old.ImageSha256 == job.ImageSha256 && old.ProvisionSha256 == job.ProvisionSha256
                && old.Image == job.Image && old.Provision == job.Provision
                && string.Join("\n", old.ProvisionArgs.ToArray()) == string.Join("\n", job.ProvisionArgs.ToArray())
                && old.WslConfig == job.WslConfig && old.Kit == job.Kit
                && old.RestoreIdentitySha256 == job.RestoreIdentitySha256 && old.ImportInPlace == job.ImportInPlace;
            if (!same) l.JobGeneration++;
            if (l.JobGeneration == 0) l.JobGeneration = 1;
            job.Generation = l.JobGeneration;
            job.Attempt = l.JobAttempt;
            Json.WriteFile(Paths.Job, job.ToJson());
            SaveLedger(l);
            if (kitRoot != null) o.Say("installing from the offline kit at " + kitRoot + "; DomovoiHost mounts it in the distro at " + OfflineKit.MountPoint);
            o.Say("wrote the host's job (generation " + Inv.S(job.Generation) + "): provision.sh " + string.Join(" ", job.ProvisionArgs.ToArray()));
            o.Say("the account's .wslconfig will cap its WSL VM at " + Inv.S(w.MemoryGb) + " GB, mirrored networking");
        }

        // -- the host service -------------------------------------------

        public static void HostStart(Output o)
        {
            HostService.SetAutomatic(true);
            HostService.Start(120);
            o.Say("started " + Paths.ServiceName);
        }

        // Setup stopped without undoing: DomovoiHost stopped and set to
        // Manual, so it doesn't provision again at every boot. Running
        // Setup again sets it back to Automatic.
        public static void HostPark(Output o)
        {
            using (var s = HostService.Find()) if (s == null) { o.Kv("service", "missing"); return; }
            HostService.Stop(240, o.Say);
            HostService.SetAutomatic(false);
            o.Say("set " + Paths.ServiceName + " to Manual and left it stopped");
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
            HostService.Start(120);
            o.Say("asked " + Paths.ServiceName + " to try again (attempt " + Inv.S(job.Attempt) + ")");
            o.Kv("attempt", Inv.S(job.Attempt));
        }

        // Reads a file the account writes, without following a link put in
        // its place.
        public static Dictionary<string, object> ReadHostFile(string path)
        {
            if (!File.Exists(path)) return null;
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 || (File.GetAttributes(Paths.HostOut) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException(path + " is a link; not reading it");
            return Json.ReadFile(path);
        }

        // L42 (review 2026-11-23): the house identity backup Setup put in
        // host\in (the house's private key) is deleted once DomovoiHost has
        // recorded it restored (host-state.json's identity_restored names
        // its SHA-256). DomovoiHost can only read host\in, so the helper
        // does it, from Setup's status polls. Silent: the poll's output is
        // key=value lines Setup reads.
        static void ForgetUsedRestoreIdentity()
        {
            string path = Paths.RestoreIdentityFile;
            try
            {
                if (!File.Exists(path)) return;
                var state = ReadHostFile(Path.Combine(Paths.HostOut, "host-state.json"));
                string done = state == null ? null : Json.Str(state, "identity_restored");
                if (done == null || done != Sha256.OfFile(path)) return;
                File.Delete(path);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            catch (FormatException) { }
            catch (InvalidOperationException) { }
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
            try { d = ReadHostFile(Paths.Status); }
            catch (FormatException) { }
            catch (IOException) { }
            if (d == null) { o.Kv("state", "none"); return; }
            HostStatus st = HostStatus.FromJson(d);
            if (st.State == "ready") ForgetUsedRestoreIdentity();
            o.Kv("state", st.State);
            o.Kv("percent", Inv.S(st.Percent));
            o.Kv("message", st.Message);
            o.Kv("step", st.Step ?? "");
            o.Kv("host_error", st.Error ?? "");
            o.Kv("failed_step", st.FailedStep ?? "");
            o.Kv("tier", st.Tier ?? "");
            o.Kv("gpu", st.GpuKnown ? (st.Gpu.Count == 0 ? "none" : string.Join(", ", st.Gpu.ToArray())) : "");
            o.Kv("ai", st.Ai == null ? "" : (st.Ai.Value ? "yes" : "no"));
            o.Kv("phone_app", st.PhoneApp == null ? "" : (st.PhoneApp.Value ? "yes" : "no"));
            o.Kv("lan_name", st.LanName ?? "");
            o.Kv("checkout_kept", st.CheckoutKept == null ? "" : (st.CheckoutKept.Value ? "yes" : "no"));
            o.Kv("generation", Inv.S(st.Generation));
            o.Kv("attempt", Inv.S(st.Attempt));
            if (st.Bytes != null) o.Kv("bytes", Inv.S(st.Bytes.Value));
            if (st.BytesTotal != null) o.Kv("bytes_total", Inv.S(st.BytesTotal.Value));
            DateTime updated;
            if (DateTime.TryParse(st.Updated, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out updated))
                o.Kv("age_sec", Inv.S((long)(DateTime.UtcNow - updated.ToUniversalTime()).TotalSeconds));
        }

        // The setup code, through the host (PLAN 4.12 step 5): printed as
        // code=... for Setup's finish page, and nowhere else. The request
        // file (host\in, admin-only) is deleted here; the answer (host\out)
        // is the host's to delete once the request is gone.
        //
        // With --lan-ip (this PC's address on the network, as Setup's
        // preflight found it), also the setup-code QR (SetupQr.cs): qr_size=
        // and qr= on stdout, right after code=, and likewise nowhere else.
        public static int ReadSetupCode(Output o, string lanIp)
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
                    var resp = ReadHostFile(respFile);
                    if (Json.Bool(resp, "ok") != true) throw new InvalidOperationException(Json.Str(resp, "error") ?? "no setup code");
                    string code = key.Decrypt(Json.Str(resp, "ciphertext"));
                    if (!SetupCode.IsWellFormed(code)) throw new InvalidOperationException("the answer is not a setup code");
                    o.Kv("code", code);
                    string url = SetupQr.ClaimUrl(lanIp, code);
                    if (url != null)
                    {
                        QrCode q = SetupQr.Encode(url);
                        o.Kv("qr_size", q.Size.ToString(CultureInfo.InvariantCulture));
                        o.Kv("qr", SetupQr.Bits(q));
                    }
                    o.LogOnly("read the setup code through DomovoiHost (shown on Setup's last page only"
                        + (url != null ? ", with its QR code)" : ")"));
                    return 0;
                }
                finally
                {
                    try { File.Delete(reqFile); } catch (IOException) { }
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
            o.Kv("change", "write Setup's install record HKLM\\" + Paths.RecordKey);
            o.Kv("change", "create " + Paths.ProgramData + " (SYSTEM and Administrators; " + Paths.AccountName + " where it needs it)");
            o.Kv("change", "register the " + Paths.ServiceName + " service (Automatic, runs as " + HostService.Account + ", restarted by Windows if it fails; no dependency on WSLService)");
            o.Kv("change", "create the media folders under " + MediaPaths.DefaultRoot + " (unless chosen otherwise) and allow " + Paths.AccountName + " to read and write them");
            if (lan != null && lan.Private)
                o.Kv("change", "add seven Hyper-V firewall rules for WSL (every WSL VM on this PC), inbound TCP from " + lan.Subnet + ": 80 (the short address), 6369, 6370, 8050-8099, 8096, 7676, 9467");
            o.Kv("change", "as " + Paths.AccountName + ": write its .wslconfig (memory " + Inv.S(WslConfig.MemoryCapGb(ram)) + " GB, mirrored networking) and import the WSL distro 'domovoi' into " + Paths.Distro);
            if (!Preflight.VmpEnabled()) o.Kv("change", "turn on the Windows feature VirtualMachinePlatform (restart)");
            if (Preflight.WslStatus(Preflight.WslVersion()) != "ok") o.Kv("change", "install the WSL package 2.7.14 (restart)");
        }
    }
}
