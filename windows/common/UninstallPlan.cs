// What the uninstaller does, decided from what Setup recorded (the install
// record, HKLM\SOFTWARE\Domovoi) and what it finds on the PC now, so the
// same plan undoes a finished install, a half-finished one, and nothing at
// all (a second run). PLAN 7, "Remove everything"; keep-data uninstall is
// step 4.
//
// Rules the plan holds to:
// * the distro is unregistered as domovoi-svc, through DomovoiHost, while
//   the account and the service still exist. Until that has worked, or
//   the distro's disk is confirmed gone, nothing that could strand it
//   (the service, the account, its profile and rights, ProgramData) is
//   touched: those steps come after critical ones, and a critical step
//   that fails stops the whole run (exit 1), so the uninstaller survives
//   for a retry;
// * nothing in a media folder is ever deleted: a folder Setup made is
//   removed only when it is empty, and only Setup's own access rules come
//   off the others;
// * an account, a service, a folder or a registry key Setup did not make
//   is left alone (a domovoi-svc someone made by hand has no Setup marker);
// * every step is safe when its target is already gone, and the install
//   record, which holds the account's SID, goes last and only when
//   nothing is left over.

using System;
using System.Collections.Generic;

namespace Domovoi.Windows
{
    public enum UninstallKind
    {
        StopService,
        RotatePassword,
        UnregisterDistro,
        Blocked,
        DeleteService,
        StopAccountProcesses,
        RemoveFirewallRules,
        RemoveMediaAccess,
        RemoveFolderIfEmpty,
        DeleteProfile,
        RemoveRights,
        UnhideAccount,
        DeleteAccount,
        DeleteProgramData,
        RemoveRunOnce,
        RemoveRecord,
    }

    public sealed class UninstallStep
    {
        public UninstallKind Kind;
        public string Target;
        public List<string> Sids = new List<string>();
        public bool DeleteUserListKey;
        public bool DeleteSpecialAccountsKey;
        // A failure stops the uninstall at once: nothing after it runs.
        public bool Critical;
        public string Reason;

        public UninstallStep(UninstallKind kind, string target) { Kind = kind; Target = target; }

        public override string ToString()
        {
            switch (Kind)
            {
                case UninstallKind.StopService: return "stop the " + Target + " service";
                case UninstallKind.RotatePassword: return "set a fresh password on " + Target + " and its service (never shown or kept)";
                case UninstallKind.UnregisterDistro: return "unregister the '" + Target + "' WSL distro as " + Paths.AccountName + " (deletes its disk)";
                case UninstallKind.Blocked: return "stop: " + Reason;
                case UninstallKind.DeleteService: return "delete the " + Target + " service";
                case UninstallKind.StopAccountProcesses: return "end any program still running as " + Paths.AccountName;
                case UninstallKind.RemoveFirewallRules: return "remove the Hyper-V firewall rules " + Target;
                case UninstallKind.RemoveMediaAccess: return "remove Setup's access rules from " + Target;
                case UninstallKind.RemoveFolderIfEmpty: return "remove " + Target + " if it is empty";
                case UninstallKind.DeleteProfile: return "delete the profile of " + Paths.AccountName + " (" + Target + ")";
                case UninstallKind.RemoveRights: return "remove the account rights of " + Target;
                case UninstallKind.UnhideAccount: return "remove the sign-in screen entry for " + Target;
                case UninstallKind.DeleteAccount: return "delete the local account " + Target;
                case UninstallKind.DeleteProgramData: return "delete " + Target;
                case UninstallKind.RemoveRunOnce: return "remove Setup's resume-after-restart entry";
                case UninstallKind.RemoveRecord: return "delete Setup's install record " + Target;
                default: return Kind.ToString();
            }
        }
    }

    public sealed class UninstallFacts
    {
        public bool ServiceExists;
        public bool ServiceRunning;
        // Its binary is DomovoiHost.exe (the one Setup registers).
        public bool ServiceIsDomovoi;
        public bool AccountExists;
        public string AccountSid;
        // The account carries Setup's marker in its comment.
        public bool AccountHasMarker;
        public bool ProfileExists;
        public List<string> FirewallRules = new List<string>();
        // Listing the rules failed: assume Domovoi's may be there.
        public bool FirewallUnknown;
        public List<string> Rights = new List<string>();
        public bool RightsUnknown;
        public bool UserListValuePresent;
        public bool ProgramDataExists;
        public bool RunOnceValuePresent;
        public bool RecordExists;
        // C:\ProgramData\Domovoi\distro\ext4.vhdx: true, false, or null
        // when it can't be told (counted as there).
        public bool? DistroDiskExists;
        // Media folders from the record that exist now.
        public HashSet<string> MediaExisting = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    }

    public static class UninstallPlan
    {
        public const string RecordKey = @"HKLM\SOFTWARE\Domovoi";

        public static List<UninstallStep> Make(Ledger ledger, UninstallFacts f)
        {
            if (f == null) throw new ArgumentNullException("f");
            var steps = new List<UninstallStep>();
            bool accountOurs = f.AccountExists && (f.AccountHasMarker || (ledger != null && ledger.AccountCreated
                && ledger.AccountSid != null && string.Equals(ledger.AccountSid, f.AccountSid, StringComparison.OrdinalIgnoreCase)));
            bool serviceOurs = f.ServiceExists && (f.ServiceIsDomovoi || (ledger != null && ledger.ServiceCreated));
            string sid = f.AccountSid ?? (ledger != null ? ledger.AccountSid : null);
            bool disk = f.DistroDiskExists != false;

            if (serviceOurs && f.ServiceRunning)
            {
                var s = new UninstallStep(UninstallKind.StopService, Paths.ServiceName);
                s.Critical = true;
                steps.Add(s);
            }
            // The distro belongs to the account; only the host, running as it,
            // can unregister it. Starting the host needs the account's
            // password in the SCM, so it gets a fresh one first (PLAN 4.5:
            // uninstall rotates, nothing stores it). Without the account the
            // registration went with it and the disk goes with ProgramData.
            if (disk && f.AccountExists)
            {
                if (serviceOurs && accountOurs)
                {
                    var r = new UninstallStep(UninstallKind.RotatePassword, Paths.AccountName);
                    r.Critical = true;
                    steps.Add(r);
                    var u = new UninstallStep(UninstallKind.UnregisterDistro, Paths.DistroName);
                    u.Critical = true;
                    steps.Add(u);
                }
                else
                {
                    var b = new UninstallStep(UninstallKind.Blocked, Paths.DistroName);
                    b.Critical = true;
                    b.Reason = !accountOurs
                        ? "the distro's disk is in " + Paths.Distro + " but the account " + Paths.AccountName + " was not made by Setup; remove it by hand"
                        : "the distro's disk is in " + Paths.Distro + " but the " + Paths.ServiceName + " service is missing, so nothing can unregister it as its owner; run Domovoi Setup again (it repairs the service), then uninstall";
                    steps.Add(b);
                }
            }
            if (serviceOurs) steps.Add(new UninstallStep(UninstallKind.DeleteService, Paths.ServiceName));
            if (sid != null && (accountOurs || !f.AccountExists))
                steps.Add(new UninstallStep(UninstallKind.StopAccountProcesses, sid));

            var rules = new List<string>(f.FirewallRules);
            if (ledger != null) foreach (string r in ledger.FirewallRules) if (!rules.Contains(r)) rules.Add(r);
            if (rules.Count > 0 || f.FirewallUnknown)
                steps.Add(new UninstallStep(UninstallKind.RemoveFirewallRules, rules.Count > 0 ? string.Join(", ", rules.ToArray()) : Firewall.RulePrefix + "*"));

            if (ledger != null)
            {
                // Access rules first, then empty folders, deepest first, so a
                // root goes only after the folders inside it.
                var media = new List<MediaFolderRecord>(ledger.Media);
                media.Sort(delegate (MediaFolderRecord a, MediaFolderRecord b) { return b.Path.Length.CompareTo(a.Path.Length); });
                foreach (var m in media)
                {
                    if (!f.MediaExisting.Contains(m.Path) || m.AddedSids.Count == 0) continue;
                    var s = new UninstallStep(UninstallKind.RemoveMediaAccess, m.Path);
                    s.Sids.AddRange(m.AddedSids);
                    steps.Add(s);
                }
                foreach (var m in media)
                {
                    if (!f.MediaExisting.Contains(m.Path) || !m.Created) continue;
                    steps.Add(new UninstallStep(UninstallKind.RemoveFolderIfEmpty, m.Path));
                }
            }

            bool sidOurs = sid != null && (accountOurs || !f.AccountExists);
            if (sidOurs && f.ProfileExists)
                steps.Add(new UninstallStep(UninstallKind.DeleteProfile, sid));
            if (sidOurs && (f.Rights.Count > 0 || f.RightsUnknown || (ledger != null && ledger.Rights.Count > 0)))
            {
                var r = new UninstallStep(UninstallKind.RemoveRights, sid);
                r.Sids.Add(sid);
                steps.Add(r);
            }
            bool keysOurs = ledger != null && (ledger.UserListKeyCreated || ledger.SpecialAccountsKeyCreated);
            if ((f.UserListValuePresent || keysOurs) && (accountOurs || !f.AccountExists))
            {
                var u = new UninstallStep(UninstallKind.UnhideAccount, Paths.AccountName);
                u.DeleteUserListKey = ledger != null && ledger.UserListKeyCreated;
                u.DeleteSpecialAccountsKey = ledger != null && ledger.SpecialAccountsKeyCreated;
                steps.Add(u);
            }
            if (accountOurs) steps.Add(new UninstallStep(UninstallKind.DeleteAccount, Paths.AccountName));
            // Only a ProgramData folder Setup made (it refuses to use one it didn't).
            if (f.ProgramDataExists && ledger != null && ledger.ProgramDataCreated)
                steps.Add(new UninstallStep(UninstallKind.DeleteProgramData, Paths.ProgramData));
            if (f.RunOnceValuePresent) steps.Add(new UninstallStep(UninstallKind.RemoveRunOnce, "DomovoiSetupResume"));
            if (f.RecordExists) steps.Add(new UninstallStep(UninstallKind.RemoveRecord, RecordKey));
            return steps;
        }
    }

    public static class UninstallRunner
    {
        public const int ExitDone = 0;
        public const int ExitStopped = 1;
        public const int ExitLeftovers = 3;

        // Runs the plan. doStep returns null when the step is done, or what
        // is left. A critical step that doesn't finish stops the run (exit
        // 1); other leftovers are collected (exit 3), and then the install
        // record stays, so a second run still knows the account's SID.
        public static int Run(List<UninstallStep> plan, Func<UninstallStep, string> doStep, List<string> leftovers, List<UninstallStep> ran)
        {
            foreach (var s in plan)
            {
                if (s.Kind == UninstallKind.RemoveRecord && leftovers.Count > 0) continue;
                string left;
                try
                {
                    left = s.Kind == UninstallKind.Blocked ? s.Reason : doStep(s);
                }
                catch (Exception e)
                {
                    left = s + ": " + e.Message;
                }
                if (ran != null) ran.Add(s);
                if (left == null) continue;
                leftovers.Add(left);
                if (s.Critical) return ExitStopped;
            }
            return leftovers.Count == 0 ? ExitDone : ExitLeftovers;
        }
    }
}
