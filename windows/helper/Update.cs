// The helper's half of the Windows update (design note
// WINDOWS-ONE-UPDATE-2026-10-22; docs/WINDOWS_HOST.md, "Updates and the
// release channel"):
//
//   linux-update [--wait] [--result FILE]
//       The Linux part, for an administrator: a one-off request in host\in
//       (administrators only) that DomovoiHost answers by running the
//       dashboard's own update in the distro (WindowsSide.cs). With --wait,
//       then waits on the core's loopback state until the update unit has
//       finished. Run elevated by the tray's "Update Domovoi" (from
//       C:\Program Files\Domovoi) when the Windows side is current, and by
//       Setup's update mode with /linuxfirst=1 (from its own temporary
//       folder) before it changes anything. Prints linux=started|nothing_new
//       |updated|failed and error=...; --result FILE gets the same as
//       RunResult lines.
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

        public static int LinuxUpdate(Output o, bool wait, string resultFile)
        {
            if (resultFile != null) CheckResultPath(resultFile);
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

        // A file named result.txt in an existing folder.
        static void CheckResultPath(string p)
        {
            if (!Path.IsPathRooted(p) || !string.Equals(Path.GetFileName(p), UpdateRun.ResultFile, StringComparison.OrdinalIgnoreCase) || p.IndexOf("..", StringComparison.Ordinal) >= 0)
                throw new ArgumentException("--result must be a full path to " + UpdateRun.ResultFile);
            string dir = Path.GetDirectoryName(p);
            if (!Directory.Exists(dir) || (File.GetAttributes(dir) & FileAttributes.ReparsePoint) != 0)
                throw new ArgumentException("--result's folder doesn't exist");
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
