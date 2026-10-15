// The uninstall (PLAN 7, "Remove everything"; keep-data is step 4): looks
// at the PC, asks UninstallPlan what to do, and runs it with
// UninstallRunner. Exit 0: all gone. Exit 1: a critical step (stopping the
// service, unregistering the distro) didn't work, and nothing after it was
// touched: the account, its profile and rights, the service and
// ProgramData are all still there for a retry, and Setup's uninstaller
// stops too, so it stays. Exit 3: leftovers that may still clear (the
// account, its rights, a firewall rule); the install record and the
// uninstaller stay for another run. Exit 4: finished, with leftovers that
// hold no data and grant nothing (or after the third run, whatever is
// left), each in a "leftover=" line with how to remove it by hand; the
// uninstaller goes. "kept=" lines name what was kept on purpose (a distro
// disk WSL no longer listed); "restart=" lines what Windows removes at the
// next restart by itself (files still in use, the account's profile when
// Windows wouldn't let go of it), which needs nothing from anyone and
// doesn't count as left over. Before each step a "progress=N|TOTAL|what"
// line, and "info=" lines as it goes, which the uninstaller shows while it
// runs. The leftovers and restart items come once more in plain words
// (leftover_plain=, restart_plain=), and the messages the uninstaller shows
// are ready-made lines (notice_restart=, notice_left=, notice_still=;
// UninstallMessages): the details stay in the log and the list of steps.
// --dry-run prints the facts and the plan and changes nothing.
//
// Left in place on purpose: WSL and the VirtualMachinePlatform feature
// (shared with the household's own WSL, PLAN 7), and every file in the
// media folders.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Principal;
using System.ServiceProcess;
using System.Threading;
using Microsoft.Win32;

namespace Domovoi.Windows.Helper
{
    static class Uninstall
    {
        const string RunOnceKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce";
        const string RunOnceValue = "DomovoiSetupResume";

        public static int Run(Output o, bool dry)
        {
            Ledger ledger = null;
            try { ledger = Steps.LoadLedger(); }
            catch (FormatException e) { o.Say("the install record is unreadable (" + e.Message + "); going by what is on the PC"); }

            UninstallFacts f = Facts(ledger, o);
            List<UninstallStep> plan = UninstallPlan.Make(ledger, f);
            o.Kv("steps", Inv.S(plan.Count));
            foreach (var s in plan) o.Kv("plan", (s.Critical ? "[critical] " : "") + s);
            if (dry) return 0;

            int run = 1;
            if (ledger != null)
            {
                ledger.UninstallRuns++;
                run = ledger.UninstallRuns;
                Steps.SaveLedger(ledger);
            }
            o.Kv("run", Inv.S(run) + " of " + Inv.S(UninstallRunner.MaxRuns));
            var notes = new List<string>();
            var atRestart = new List<RestartNote>();
            var left = new List<UninstallLeftover>();
            int code = UninstallRunner.Run(plan, delegate (UninstallStep s)
            {
                o.Kv("progress", UninstallProgress.Line(plan.IndexOf(s), plan.Count, s));
                return Do(s, ledger, notes, atRestart, o);
            }, left, null, run);
            foreach (var l in left)
            {
                o.Kv("leftover", l.What + " | to remove it by hand: " + l.HowToRemove());
                o.Kv("leftover_plain", l.Plain());
                o.LogOnly("leftover: " + l.What + " | " + l.HowToRemove());
            }
            foreach (string n in notes) { o.Kv("kept", n); o.LogOnly("kept: " + n); }
            foreach (RestartNote n in atRestart)
            {
                o.Kv("restart", n.Detail);
                o.Kv("restart_plain", n.Plain);
                o.LogOnly("at the next restart: " + n.Detail);
            }
            string later = UninstallMessages.AtRestart(atRestart);
            if (later != null) o.Kv("notice_restart", later);
            if (code == UninstallRunner.ExitFinishedWithLeftovers && left.Count > 0) o.Kv("notice_left", UninstallMessages.Leftovers(left));
            if (code == UninstallRunner.ExitLeftovers && left.Count > 0) o.Kv("notice_still", UninstallMessages.StillThere(left));
            o.Kv("result", code == UninstallRunner.ExitDone ? "removed" : code == UninstallRunner.ExitStopped ? "stopped"
                : code == UninstallRunner.ExitLeftovers ? "leftovers, run again" : "removed, with leftovers");
            if (code == UninstallRunner.ExitStopped)
                o.Kv("error", "uninstall stopped before removing the account, the service or any data: " + left[left.Count - 1].What);
            return code;
        }

        // A disk the host recorded as imported holds the household's data
        // (the database, the server's identity): if WSL no longer lists it,
        // it is moved out of the way, to an admin-only folder named in the
        // uninstall's message, rather than deleted or left blocking the
        // uninstall. One the host never recorded is a half-finished import:
        // it goes with C:\ProgramData\Domovoi. Returns a leftover, or null.
        static string KeepOrDropDisk(List<string> notes, Output o)
        {
            if (!File.Exists(Paths.DistroDisk)) return null;
            if (Folders.IsReparse(Paths.Distro)) throw new InvalidOperationException(Paths.Distro + " is a link; not moving or deleting anything through it");
            Dictionary<string, object> st = null;
            try { st = Steps.ReadHostFile(Path.Combine(Paths.HostOut, "host-state.json")); }
            catch (FormatException) { }
            catch (IOException) { }
            if (st == null || Json.Bool(st, "imported") != true)
            {
                o.Say(Paths.DistroDisk + " was never recorded as imported (an import that didn't finish); it goes with " + Paths.ProgramData);
                return null;
            }
            string dir = Paths.ProgramData + "-kept-" + DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
            Folders.CreateAdminOnly(dir);
            string dest = Path.Combine(dir, "ext4.vhdx");
            File.Move(Paths.DistroDisk, dest);
            string note = "Your Domovoi data was kept: Windows no longer listed Domovoi's Linux system, so its disk (the database, settings and the server's identity) was moved to "
                + dest + ". Only administrators can open that folder; delete it when you no longer need it.";
            notes.Add(note);
            o.Say(note);
            return null;
        }

        static UninstallFacts Facts(Ledger ledger, Output o)
        {
            var f = new UninstallFacts();
            using (var s = HostService.Find())
            {
                if (s != null)
                {
                    f.ServiceExists = true;
                    s.Refresh();
                    f.ServiceRunning = s.Status != ServiceControllerStatus.Stopped;
                    f.ServiceIsDomovoi = HostService.IsDomovoiBinary(HostService.BinaryPath());
                }
            }
            Account.Info a = Account.Get();
            f.AccountExists = a.Exists;
            f.AccountHasMarker = a.HasMarker;
            if (a.Exists) f.AccountSid = Account.Sid().Value;
            string sid = f.AccountSid ?? (ledger != null ? ledger.AccountSid : null);
            if (sid != null)
            {
                f.ProfileExists = Account.ProfileExists(sid);
                try { f.Rights = Account.Rights(new SecurityIdentifier(sid)); }
                catch (System.ComponentModel.Win32Exception e)
                {
                    f.RightsUnknown = true;
                    o.Say("could not read the account rights (" + e.Message + "); removing them anyway");
                }
            }
            f.UserListValuePresent = Account.HiddenValuePresent();
            try { f.FirewallRules = Steps.FirewallRuleNames(o); }
            catch (Exception e)
            {
                if (!(e is InvalidOperationException || e is System.TimeoutException || e is System.ComponentModel.Win32Exception)) throw;
                f.FirewallUnknown = true;
                o.Say("could not list the firewall rules (" + e.Message + "); the removal step will check again");
            }
            f.ProgramDataExists = Directory.Exists(Paths.ProgramData);
            try
            {
                f.DistroDiskExists = File.Exists(Paths.DistroDisk);
                if (Directory.Exists(Paths.Distro) && Folders.IsReparse(Paths.Distro)) f.DistroDiskExists = null;
            }
            catch (IOException) { f.DistroDiskExists = null; }
            catch (UnauthorizedAccessException) { f.DistroDiskExists = null; }
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(RunOnceKey))
                f.RunOnceValuePresent = k != null && k.GetValue(RunOnceValue) != null;
            f.RecordExists = Steps.RecordExists();
            if (!a.Exists && ledger != null && ledger.AccountSid != null)
                f.LedgerSidHasDomovoiProfile = UninstallFacts.IsDomovoiProfilePath(Account.ProfilePath(ledger.AccountSid));
            if (ledger != null)
                foreach (var m in ledger.Media) if (Directory.Exists(m.Path)) f.MediaExisting.Add(m.Path);
            o.Kv("fact", "service=" + (f.ServiceExists ? (f.ServiceRunning ? "running" : "stopped") : "absent")
                + " account=" + (f.AccountExists ? (f.AccountHasMarker ? "ours" : "not-marked") : "absent")
                + " profile=" + (f.ProfileExists ? "present" : "absent")
                + " distro_disk=" + (f.DistroDiskExists == null ? "unknown" : (f.DistroDiskExists.Value ? "present" : "absent"))
                + " rules=" + (f.FirewallUnknown ? "unknown" : Inv.S(f.FirewallRules.Count))
                + " rights=" + (f.RightsUnknown ? "unknown" : Inv.S(f.Rights.Count))
                + " programdata=" + (f.ProgramDataExists ? "present" : "absent")
                + " record=" + (ledger != null ? "present" : "absent"));
            return f;
        }

        // Does one step; returns a leftover description, or null when done.
        static string Do(UninstallStep s, Ledger ledger, List<string> notes, List<RestartNote> atRestart, Output o)
        {
            switch (s.Kind)
            {
                case UninstallKind.StopService:
                    HostService.Stop(240, o.Say);
                    return null;

                case UninstallKind.RotatePassword:
                    using (Secret pw = Secret.NewPassword())
                    {
                        Account.SetPassword(pw);
                        HostService.SetPassword(pw);
                    }
                    o.Say("set a fresh password (never shown) on " + Paths.AccountName + " and its service");
                    return null;

                case UninstallKind.UnregisterDistro:
                    return Unregister(notes, o);

                case UninstallKind.DeleteService:
                    HostService.Delete();
                    for (int i = 0; i < 30; i++)
                    {
                        using (var svc = HostService.Find()) if (svc == null) break;
                        Thread.Sleep(1000);
                    }
                    using (var still = HostService.Find())
                    {
                        if (still != null) return "the " + Paths.ServiceName + " service is marked for deletion; it goes at the next restart";
                    }
                    o.Say("deleted the " + Paths.ServiceName + " service");
                    return null;

                case UninstallKind.StopAccountProcesses:
                    Processes.StopAccount(new SecurityIdentifier(s.Target), o.Say);
                    return null;

                case UninstallKind.RemoveFirewallRules:
                    Steps.PowerShell(Firewall.RemoveScript(), o);
                    // Listing again must work: a failure is a leftover, never "removed".
                    List<string> left = Steps.FirewallRuleNames(o);
                    if (left.Count > 0) return "firewall rules " + string.Join(", ", left.ToArray());
                    o.Say("removed the Hyper-V firewall rules " + s.Target);
                    return null;

                case UninstallKind.RemoveMediaAccess:
                    return Folders.RemoveAccess(s.Target, s.Sids, o.Say);

                case UninstallKind.RemoveFolderIfEmpty:
                    Folders.RemoveIfEmpty(s.Target, o.Say);
                    return null;

                case UninstallKind.DeleteProfile:
                    return DeleteProfile(s.Target, atRestart, o);

                case UninstallKind.RemoveRights:
                    Account.RemoveAllRights(new SecurityIdentifier(s.Target));
                    o.Say("removed the account rights of " + s.Target);
                    return null;

                case UninstallKind.UnhideAccount:
                    Account.Unhide(s.DeleteUserListKey, s.DeleteSpecialAccountsKey, o.Say);
                    return null;

                case UninstallKind.DeleteAccount:
                    Account.Delete();
                    o.Say("deleted the local account " + Paths.AccountName);
                    return null;

                case UninstallKind.DeleteProgramData:
                    return KeepOrDropDisk(notes, o) ?? DeleteProgramData(ledger != null ? ledger.AccountSid : null, atRestart, o);

                case UninstallKind.RemoveRunOnce:
                    using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
                    using (var k = hklm.OpenSubKey(RunOnceKey, true))
                        if (k != null) k.DeleteValue(RunOnceValue, false);
                    o.Say("removed Setup's resume-after-restart entry");
                    return null;

                case UninstallKind.RemoveRecord:
                    Steps.DeleteRecord(o);
                    return null;
            }
            return null;
        }

        // The host, as domovoi-svc, unregisters the distro, ends its own WSL
        // session and stops itself (Host.Unregister), often within a second:
        // the wait is for Running or Stopped, then Stopped (ServiceWait).
        // The result counts only if the host wrote it after this request:
        // the helper never deletes in host\out.
        static string Unregister(List<string> notes, Output o)
        {
            if (!Directory.Exists(Paths.HostOut)) return "the distro (the host's folder is gone, so it could not report)";
            DateTime asked = DateTime.UtcNow.AddSeconds(-2);
            o.Say("asking " + Paths.ServiceName + " to unregister the distro as " + Paths.AccountName);
            OneShotResult r = HostService.RunOnce(new[] { Paths.UnregisterArg }, 120, 15 * 60);
            if (r == OneShotResult.NeverStarted) return "the distro (" + Paths.ServiceName + " did not start within 2 minutes; see " + Paths.HostLog + ")";
            if (r == OneShotResult.StillRunning) return "the distro (" + Paths.ServiceName + " did not finish unregistering within 15 minutes)";
            var d = Steps.ReadHostFile(Paths.UnregisterResult);
            string why = UnregisterOutcome.Judge(d, asked);
            if (why != null) return why;
            o.Say("the WSL distro '" + Paths.DistroName + "' is not registered any more (" + (Json.Str(d, "message") ?? "") + ")");
            if (Json.Bool(d, "released") == true) o.Say("ended " + Paths.AccountName + "'s own WSL session (the household's WSL keeps running)");
            // WSL doesn't list it, yet its disk is there: kept aside or, if no
            // import ever finished, left to go with ProgramData.
            return KeepOrDropDisk(notes, o);
        }

        // The host ended the account's WSL session after unregistering
        // (Host.ReleaseWsl), so the profile is normally free at once; this
        // still waits up to two minutes. If Windows keeps it open (error 32
        // on 2026-10-12, held until a restart), its deletion is scheduled
        // for the next restart (ProfileTask), which needs nothing from the
        // household; only if that can't be set up is it left over.
        static string DeleteProfile(string sid, List<RestartNote> atRestart, Output o)
        {
            string path = Account.ProfilePath(sid);
            int err = 0;
            DateTime start = DateTime.UtcNow, until = start.AddMinutes(2);
            bool waited = false;
            int lastMinute = 0;
            while (true)
            {
                if (!Account.ProfileExists(sid)) return null;
                err = Account.DeleteProfile(sid);
                if (err == 0)
                {
                    o.Say("deleted the profile " + (path ?? sid));
                    return null;
                }
                if (DateTime.UtcNow > until) break;
                if (!waited) { o.Say("waiting for Windows to let go of the profile " + (path ?? sid) + " (up to 2 minutes)"); waited = true; }
                int minute = (int)(DateTime.UtcNow - start).TotalMinutes;
                if (minute > lastMinute) { lastMinute = minute; o.Say("still waiting for the profile (" + Inv.S(minute) + " min)"); }
                Thread.Sleep(5000);
            }
            if (!ProfileTask.IsLocalAccountSid(sid))
                return "the profile " + (path ?? sid) + " (error " + Inv.S(err) + ")";
            // The task deletes the profile only once its account is gone, so
            // it is scheduled only then (this step comes after DeleteAccount);
            // an account that couldn't be deleted keeps the uninstaller for
            // another run anyway, and that run deletes the profile.
            if (!AccountGone(sid))
                return "the profile " + (path ?? sid) + " (error " + Inv.S(err) + "; Windows still had it open, and its account is still there)";
            try
            {
                string outText = Steps.PowerShell(ProfileTask.RegisterScript(sid, Environment.SystemDirectory), o);
                if (outText.IndexOf("scheduled", StringComparison.Ordinal) < 0) throw new InvalidOperationException("the task did not register");
            }
            catch (Exception e)
            {
                if (!(e is InvalidOperationException || e is System.TimeoutException || e is System.ComponentModel.Win32Exception)) throw;
                return "the profile " + (path ?? sid) + " (error " + Inv.S(err) + "; its removal at the next restart could not be scheduled either: " + e.Message + ")";
            }
            string note = "the profile " + (path ?? sid) + ": Windows still had it open (error " + Inv.S(err)
                + "), so a one-time startup task, " + ProfileTask.TaskName + ", deletes it at the next restart and then deletes itself";
            o.Say(note);
            atRestart.Add(new RestartNote(note, UninstallMessages.ProfilePlain));
            return null;
        }

        // The SID maps to no account any more (the startup task's own rule).
        static bool AccountGone(string sid)
        {
            try
            {
                new SecurityIdentifier(sid).Translate(typeof(NTAccount));
                return false;
            }
            catch (IdentityNotMappedException) { return true; }
            catch (SystemException) { return false; }
        }

        static string DeleteProgramData(string sid, List<RestartNote> atRestart, Output o)
        {
            // Nothing runs as the account by now (its service is gone and
            // StopAccountProcesses ran); make the tree admin-only anyway
            // before deleting, then delete without following links.
            Folders.ResetForDelete(sid, o.Say);
            DateTime until = DateTime.UtcNow.AddMinutes(3);
            Exception last = null;
            bool waited = false;
            while (Directory.Exists(Paths.ProgramData))
            {
                try { Folders.DeleteTree(Paths.ProgramData); }
                catch (IOException e) { last = e; }
                catch (UnauthorizedAccessException e) { last = e; }
                if (!Directory.Exists(Paths.ProgramData)) break;
                if (DateTime.UtcNow > until)
                {
                    int failed;
                    int n = ScheduleDelete(Paths.ProgramData, out failed);
                    string why = last != null ? last.Message : "in use";
                    if (failed > 0)
                        return Paths.ProgramData + " (" + why + "; " + Inv.S(failed) + " item(s) could not even be scheduled for deletion at the next restart)";
                    string note = Paths.ProgramData + ": " + Inv.S(n) + " file(s) and folder(s) Windows still had open (" + why + "); Windows deletes them at the next restart";
                    o.Say(note);
                    atRestart.Add(new RestartNote(note, UninstallMessages.ProgramDataPlain));
                    return null;
                }
                if (!waited) { o.Say("waiting for WSL to let go of " + Paths.ProgramData + " (up to 3 minutes)"); waited = true; }
                Thread.Sleep(5000);
            }
            o.Say("deleted " + Paths.ProgramData);
            return null;
        }

        // Files first, then folders deepest first: Windows runs these in
        // order at the next restart. Links are scheduled as links. failed:
        // how many Windows refused to schedule.
        static int ScheduleDelete(string root, out int failed)
        {
            int n = 0;
            failed = 0;
            var dirs = new List<string>();
            var stack = new Stack<string>();
            stack.Push(root);
            while (stack.Count > 0)
            {
                string d = stack.Pop();
                dirs.Add(d);
                if ((File.GetAttributes(d) & FileAttributes.ReparsePoint) != 0) continue;
                foreach (string f in Directory.GetFiles(d)) { if (Native.MoveFileExW(f, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT)) n++; else failed++; }
                foreach (string sub in Directory.GetDirectories(d)) stack.Push(sub);
            }
            dirs.Reverse();
            foreach (string d in dirs) { if (Native.MoveFileExW(d, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT)) n++; else failed++; }
            return n;
        }
    }
}
