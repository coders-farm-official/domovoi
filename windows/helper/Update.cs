// The helper's half of the Windows update (design note
// WINDOWS-ONE-UPDATE-2026-10-22; docs/WINDOWS_HOST.md, "Updates and the
// release channel"):
//
//   run-update --from DIR --run NAME --tag TAG [--commit SHA] [--linuxfirst]
//       The Windows step, elevated (the tray, after Windows' permission
//       prompt): copy the tray's checked download from DIR (its private run
//       folder) into C:\ProgramData\Domovoi\updates\NAME, which only
//       administrators can write, check it again there (the release key
//       built into this program, the release TAG and its commit, newer than
//       this program's own version), and start that copy as the silent
//       repair, without waiting (Setup replaces this program's file). A
//       refusal writes result.txt there (ok=0, outcome=, error=) and exits 1.
//   linux-update [--wait] [--run NAME]
//       The Linux part, for an administrator: a one-off request in host\in
//       (administrators only) that DomovoiHost answers by running the
//       dashboard's own update in the distro (WindowsSide.cs). With --wait,
//       then waits on the core's loopback state until the update unit has
//       finished. Run elevated by the tray's "Update Domovoi" (from
//       C:\Program Files\Domovoi) when the Windows side is current, and by
//       Setup's update mode with /linuxfirst=1 (from its own temporary
//       folder) before it changes anything. Prints linux=started|nothing_new
//       |updated|failed and error=...; --run NAME writes the same as
//       RunResult lines to C:\ProgramData\Domovoi\updates\NAME\result.txt.
//   saved-answers
//       What Setup's update mode passes on, from the install record:
//       internet=, media_root=. Read-only.

using System;
using System.Collections.Generic;
using System.IO;
using System.ServiceProcess;
using System.Text;
using System.Threading;

namespace Domovoi.Windows.Helper
{
    static class Update
    {
        // The update unit backs up, syncs, migrates and health-checks: give it
        // the dashboard's own patience (restartDomovoiServer: 15 minutes), and
        // more for a slow dependency sync.
        const int WaitMinutes = 45;

        public static int LinuxUpdate(Output o, bool wait, string run)
        {
            string resultFile = run != null ? Path.Combine(AdminRun(run), UpdateRun.ResultFile) : null;
            var result = new RunResult();
            try
            {
                Run(o, wait, result);
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is InvalidOperationException || e is UnauthorizedAccessException || e is FormatException)) throw;
                result.Ok = false;
                result.Linux = "failed";
                result.Error = e.Message;
            }
            o.Kv("linux", result.Linux);
            if (result.Error != null) o.Kv("error", result.Error);
            if (resultFile != null) File.WriteAllText(resultFile, result.Format(), new UTF8Encoding(false));
            return result.Ok ? 0 : 1;
        }

        static void Run(Output o, bool wait, RunResult result)
        {
            UpdateState before = State();
            string beforeRun = before != null ? before.LastUpdateStarted : null;
            if (before != null && before.Internet == "never")
            {
                Fail(result, "internet access is turned off for this box (Settings > Privacy > Internet), so Domovoi can't check for updates");
                return;
            }
            string why;
            Dictionary<string, object> resp = AskHost("linux-update", (Domovoi.Windows.LinuxUpdate.TimeoutMs / 1000) + 60, out why);
            if (resp == null) { Fail(result, why); return; }
            if (Json.Bool(resp, "ok") != true)
            {
                Fail(result, Json.Str(resp, "error") ?? "the Linux side refused the update");
                return;
            }
            if (Json.Bool(resp, "nothing_new") == true)
            {
                o.LogOnly("linux-update: nothing new for the Linux side");
                result.Ok = true;
                result.Linux = "nothing_new";
                return;
            }
            o.Say("the Linux side's update is running (" + (Json.Str(resp, "from_sha") ?? "?") + " to " + (Json.Str(resp, "to_sha") ?? "?") + ")");
            result.Ok = true;
            result.Linux = "started";
            if (!wait) return;
            WaitForUpdate(beforeRun, result);
            o.Say("the Linux side's update: " + result.Linux + (result.Error != null ? " (" + result.Error + ")" : ""));
        }

        static void Fail(RunResult r, string why)
        {
            r.Ok = false;
            r.Linux = "failed";
            r.Error = why;
        }

        // Until the core says the update unit has recorded a new run and
        // nothing is under way, or WaitMinutes.
        static void WaitForUpdate(string beforeRun, RunResult result)
        {
            DateTime until = DateTime.UtcNow.AddMinutes(WaitMinutes);
            Thread.Sleep(3000);
            while (DateTime.UtcNow < until)
            {
                UpdateState s = State();
                if (s != null && !s.LinuxBusy && s.LastUpdateStarted != null && s.LastUpdateStarted != beforeRun && s.LastUpdateStatus != "running")
                {
                    if (s.LastUpdateStatus == "ok") { result.Ok = true; result.Linux = "updated"; }
                    else
                    {
                        result.Ok = false;
                        result.Linux = "failed";
                        result.Error = "the Linux side's update " + (s.LastUpdateStatus ?? "failed") + (s.LastUpdateError != null ? ": " + s.LastUpdateError : "");
                    }
                    return;
                }
                Thread.Sleep(3000);
            }
            Fail(result, "the Linux side's update didn't finish within " + Inv.S(WaitMinutes) + " minutes");
        }

        static UpdateState State()
        {
            JsonReply r = Loopback.Send("GET", Loopback.Core + Loopback.StatePath, null, 10000);
            return r != null && r.Status == 200 ? UpdateState.From(r.Body) : null;
        }

        // -- the elevated side's run folders --------------------------------

        // C:\ProgramData\Domovoi\updates (Administrators and SYSTEM write,
        // Users read, inheritance off), then a new folder NAME in it.
        public static string AdminRun(string run)
        {
            if (!UpdateRun.IsRunName(run)) throw new ArgumentException("--run takes a run name");
            string root = UpdateRun.AdminRoot;
            if (!Directory.Exists(Paths.ProgramData)) throw new InvalidOperationException(Paths.ProgramData + " is missing: Domovoi isn't installed by Setup");
            if (Directory.Exists(root) && (File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException(root + " is a link; not using it");
            Directory.CreateDirectory(root);
            var sec = new System.Security.AccessControl.DirectorySecurity();
            sec.SetAccessRuleProtection(true, false);
            const System.Security.AccessControl.InheritanceFlags inherit = System.Security.AccessControl.InheritanceFlags.ContainerInherit | System.Security.AccessControl.InheritanceFlags.ObjectInherit;
            sec.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(new System.Security.Principal.SecurityIdentifier(System.Security.Principal.WellKnownSidType.LocalSystemSid, null),
                System.Security.AccessControl.FileSystemRights.FullControl, inherit, System.Security.AccessControl.PropagationFlags.None, System.Security.AccessControl.AccessControlType.Allow));
            sec.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(new System.Security.Principal.SecurityIdentifier(System.Security.Principal.WellKnownSidType.BuiltinAdministratorsSid, null),
                System.Security.AccessControl.FileSystemRights.FullControl, inherit, System.Security.AccessControl.PropagationFlags.None, System.Security.AccessControl.AccessControlType.Allow));
            sec.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(new System.Security.Principal.SecurityIdentifier(System.Security.Principal.WellKnownSidType.BuiltinUsersSid, null),
                System.Security.AccessControl.FileSystemRights.ReadAndExecute, inherit, System.Security.AccessControl.PropagationFlags.None, System.Security.AccessControl.AccessControlType.Allow));
            Directory.SetAccessControl(root, sec);
            UpdateRun.Prune(root, DateTime.UtcNow, run);
            string dir = Path.Combine(root, run);
            if (Directory.Exists(dir)) throw new InvalidOperationException("the run folder " + dir + " exists already");
            Directory.CreateDirectory(dir);
            return dir;
        }

        // The four files from the tray's folder into dir (the
        // administrators' run folder), then the check on what is now there,
        // out of reach of anyone but an administrator: null when the copy
        // may run, else why not and code (SetupCheck's words).
        public static string CopyAndCheck(string from, string dir, SetupExpectation want, out SetupManifest m, out string code)
        {
            m = null;
            code = null;
            try
            {
                var bytes = new Dictionary<string, byte[]>();
                foreach (string name in new[] { ReleaseNames.Manifest, ReleaseNames.ManifestSig, ReleaseNames.SetupSig, ReleaseNames.SetupAsset })
                {
                    string src = Path.Combine(from, name), dest = Path.Combine(dir, name);
                    if (!File.Exists(src) || (File.GetAttributes(src) & FileAttributes.ReparsePoint) != 0) { code = SetupCheck.NoSignedSetup; return "the download has no " + name; }
                    File.Copy(src, dest, false);
                    bytes[name] = File.ReadAllBytes(dest);
                }
                return SetupCheck.Run(want, bytes[ReleaseNames.Manifest], Encoding.ASCII.GetString(bytes[ReleaseNames.ManifestSig]),
                    bytes[ReleaseNames.SetupAsset], Encoding.ASCII.GetString(bytes[ReleaseNames.SetupSig]), out m, out code);
            }
            catch (IOException e) { code = "failed"; return "the download couldn't be copied: " + e.Message; }
        }

        // The Windows step's elevated half (header: run-update).
        public static int RunUpdate(Output o, string from, string run, string tag, string commit, bool linuxFirst)
        {
            if (!ReleaseNames.IsTag(tag)) throw new ArgumentException("--tag takes a release tag");
            if (commit != null && !System.Text.RegularExpressions.Regex.IsMatch(commit, "^[0-9a-f]{40}\\z")) throw new ArgumentException("--commit takes a full SHA");
            if (!Path.IsPathRooted(from) || !Directory.Exists(from) || (File.GetAttributes(from) & FileAttributes.ReparsePoint) != 0)
                throw new ArgumentException("--from must be the tray's run folder");
            string dir = AdminRun(run);
            var result = new RunResult();
            string code;
            SetupManifest m;
            string why = CopyAndCheck(from, dir, new SetupExpectation { Tag = tag, Commit = commit, Installed = BuildVersion.Version, Key = ReleaseKey.Pinned() }, out m, out code);
            if (why != null)
            {
                result.Ok = false;
                result.Outcome = code ?? SetupCheck.BadSignature;
                result.Error = "Domovoi didn't run the Setup: " + why;
                File.WriteAllText(Path.Combine(dir, UpdateRun.ResultFile), result.Format(), new UTF8Encoding(false));
                try { File.Delete(Path.Combine(dir, ReleaseNames.SetupAsset)); } catch (IOException) { }
                o.Kv("error", result.Error);
                o.LogOnly("run-update " + run + ": refused: " + why);
                return 1;
            }
            string exe = Path.Combine(dir, ReleaseNames.SetupAsset);
            var psi = new System.Diagnostics.ProcessStartInfo(exe, UpdateRun.SetupArguments(dir, linuxFirst))
            {
                UseShellExecute = false,
                WorkingDirectory = Environment.SystemDirectory,
            };
            using (System.Diagnostics.Process p = System.Diagnostics.Process.Start(psi))
                o.Kv("pid", p == null ? "" : Inv.S(p.Id));
            o.Kv("run", dir);
            o.Kv("version", m.Version);
            o.Say("started Domovoi Setup " + m.Version + " from " + dir + " as a silent repair");
            return 0;
        }

        // One request to DomovoiHost; its answer, or null and why.
        static Dictionary<string, object> AskHost(string verb, int timeoutSec, out string why)
        {
            using (var s = HostService.Find())
            {
                if (s == null) { why = "no " + Paths.ServiceName + " on this PC"; return null; }
                s.Refresh();
                if (s.Status != ServiceControllerStatus.Running) { why = Paths.ServiceName + " is not running"; return null; }
            }
            Dictionary<string, object> status = null;
            try { status = Steps.ReadHostFile(Paths.Status); }
            catch (FormatException) { }
            catch (IOException) { }
            if (status == null || HostStatus.FromJson(status).State != "ready") { why = "Domovoi is not running"; return null; }
            if (!Directory.Exists(Paths.HostIn)) { why = "no " + Paths.HostIn; return null; }
            string id = Guid.NewGuid().ToString("N");
            var req = new Dictionary<string, object>();
            req["v"] = 1;
            req["verb"] = verb;
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
                try { resp = Steps.ReadHostFile(respFile); }
                catch (FormatException e) { why = e.Message; return null; }
                if (resp == null) { why = "no answer"; return null; }
                if (Json.Str(resp, "error") == "unknown request") { why = "this PC's DomovoiHost is from before the Windows update (run the new Setup once by hand)"; return null; }
                why = "";
                return resp;
            }
            finally
            {
                try { File.Delete(reqFile); } catch (IOException) { }
            }
        }

        public static void SavedAnswers(Output o)
        {
            Ledger l = Steps.LoadLedger();
            if (l == null || string.IsNullOrEmpty(l.Internet) || string.IsNullOrEmpty(l.MediaRoot))
                throw new InvalidOperationException("Setup's install record has no answers to keep: run Setup itself");
            o.Kv("internet", l.Internet);
            o.Kv("media_root", l.MediaRoot);
        }
    }
}
