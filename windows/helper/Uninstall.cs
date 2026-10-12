// The uninstall (PLAN 7, "Remove everything"; keep-data is step 4): looks
// at the PC, asks UninstallPlan what to do, and runs it with
// UninstallRunner. Exit 0: all gone. Exit 1: a critical step (stopping the
// service, unregistering the distro) didn't work, and nothing after it was
// touched: the account, its profile and rights, the service and
// ProgramData are all still there for a retry, and Setup's uninstaller
// stops too, so it stays. Exit 3: done, with leftovers named in
// "leftover=" lines (a profile still in use until a restart); the install
// record stays so a second run still knows the account's SID.
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

            var leftovers = new List<string>();
            int code = UninstallRunner.Run(plan, delegate (UninstallStep s) { return Do(s, ledger, o); }, leftovers, null);
            foreach (string l in leftovers) o.Kv("leftover", l);
            o.Kv("result", code == UninstallRunner.ExitDone ? "removed" : (code == UninstallRunner.ExitStopped ? "stopped" : "leftovers"));
            if (code == UninstallRunner.ExitStopped)
                o.Kv("error", "uninstall stopped before removing the account, the service or any data: " + leftovers[leftovers.Count - 1]);
            return code;
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
        static string Do(UninstallStep s, Ledger ledger, Output o)
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
                    return Unregister(o);

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
                    return DeleteProfile(s.Target, o);

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
                    return DeleteProgramData(ledger != null ? ledger.AccountSid : null, o);

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

        // The host, as domovoi-svc, unregisters the distro, then stops itself
        // (Host.Unregister). The result counts only if the host wrote it
        // after this request: the helper never deletes in host\out.
        static string Unregister(Output o)
        {
            if (!Directory.Exists(Paths.HostOut)) return "the distro (the host's folder is gone, so it could not report)";
            DateTime asked = DateTime.UtcNow.AddSeconds(-2);
            o.Say("asking " + Paths.ServiceName + " to unregister the distro as " + Paths.AccountName);
            HostService.Start(new[] { Paths.UnregisterArg }, 120);
            if (!HostService.WaitFor(ServiceControllerStatus.Stopped, 15 * 60))
                return "the distro (" + Paths.ServiceName + " did not finish unregistering within 15 minutes)";
            var d = Steps.ReadHostFile(Paths.UnregisterResult);
            DateTime at;
            if (d == null || !DateTime.TryParse(Json.Str(d, "at"), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out at) || at.ToUniversalTime() < asked)
                return "the distro (" + Paths.ServiceName + " gave no result for this request; see " + Paths.HostLog + ")";
            if (Json.Bool(d, "ok") != true) return "the distro (" + (Json.Str(d, "error") ?? "unregister failed") + ")";
            if (File.Exists(Paths.DistroDisk)) return "the distro (WSL says it is unregistered, but " + Paths.DistroDisk + " is still there)";
            o.Say("unregistered the WSL distro '" + Paths.DistroName + "' (" + (Json.Str(d, "message") ?? "") + ")");
            return null;
        }

        // The profile stays loaded while the account's WSL VM runs; with
        // the distro gone the VM stops on its own after its idle timeout
        // (60 s, see WslConfig.cs), so this waits up to three minutes.
        static string DeleteProfile(string sid, Output o)
        {
            string path = Account.ProfilePath(sid);
            int err = 0;
            DateTime until = DateTime.UtcNow.AddMinutes(3);
            bool waited = false;
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
                if (!waited) { o.Say("waiting for WSL to let go of the profile (up to 3 minutes)"); waited = true; }
                Thread.Sleep(5000);
            }
            return "the profile " + (path ?? sid) + " (error " + Inv.S(err) + "; restart Windows, then run the uninstaller again)";
        }

        static string DeleteProgramData(string sid, Output o)
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
                    int n = ScheduleDelete(Paths.ProgramData);
                    return Paths.ProgramData + " (" + (last != null ? last.Message : "in use") + "; " + Inv.S(n) + " item(s) will be deleted at the next restart; run the uninstaller again after it)";
                }
                if (!waited) { o.Say("waiting for WSL to let go of " + Paths.ProgramData + " (up to 3 minutes)"); waited = true; }
                Thread.Sleep(5000);
            }
            o.Say("deleted " + Paths.ProgramData);
            return null;
        }

        // Files first, then folders deepest first: Windows runs these in
        // order at the next restart. Links are scheduled as links.
        static int ScheduleDelete(string root)
        {
            int n = 0;
            var dirs = new List<string>();
            var stack = new Stack<string>();
            stack.Push(root);
            while (stack.Count > 0)
            {
                string d = stack.Pop();
                dirs.Add(d);
                if ((File.GetAttributes(d) & FileAttributes.ReparsePoint) != 0) continue;
                foreach (string f in Directory.GetFiles(d)) if (Native.MoveFileExW(f, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT)) n++;
                foreach (string sub in Directory.GetDirectories(d)) stack.Push(sub);
            }
            dirs.Reverse();
            foreach (string d in dirs) if (Native.MoveFileExW(d, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT)) n++;
            return n;
        }
    }
}
