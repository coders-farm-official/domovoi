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
//   record, which holds the account's SID, goes last, when a run finishes
//   (UninstallRunner says when leftovers no longer hold that up).

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
        // With the account gone, the record's SID is trusted only if its
        // profile entry points at a folder named domovoi-svc (or
        // domovoi-svc.<something>, Windows' name when the folder existed).
        public bool LedgerSidHasDomovoiProfile;

        public static bool IsDomovoiProfilePath(string profileImagePath)
        {
            if (string.IsNullOrEmpty(profileImagePath)) return false;
            string leaf = profileImagePath.TrimEnd('\\');
            int slash = leaf.LastIndexOf('\\');
            leaf = slash >= 0 ? leaf.Substring(slash + 1) : leaf;
            return string.Equals(leaf, Paths.AccountName, StringComparison.OrdinalIgnoreCase)
                || leaf.StartsWith(Paths.AccountName + ".", StringComparison.OrdinalIgnoreCase);
        }
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
            // The account's own SID when it is Setup's; with the account gone,
            // the record's SID only when its profile is domovoi-svc's.
            bool sidOurs = sid != null && (accountOurs || (!f.AccountExists && f.LedgerSidHasDomovoiProfile));
            if (sidOurs)
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

    // The "progress=" line the helper prints before each step, which the
    // uninstaller turns into its status text and progress bar while it
    // runs: "N|TOTAL|What it does now" (N from 1).
    public static class UninstallProgress
    {
        public static string Line(int index, int total, UninstallStep s)
        {
            string what = s.ToString().Replace("|", "/");
            if (what.Length > 0) what = char.ToUpperInvariant(what[0]) + what.Substring(1);
            return Inv.S(index + 1) + "|" + Inv.S(total) + "|" + what;
        }
    }

    // What DomovoiHost's unregister run left in host\out\unregister.json,
    // judged: null when the distro is gone, else what is left. Only an
    // answer written after the request counts (asked: when it was made).
    public static class UnregisterOutcome
    {
        public static string Judge(Dictionary<string, object> d, DateTime askedUtc)
        {
            DateTime at;
            if (d == null || !DateTime.TryParse(Json.Str(d, "at"), System.Globalization.CultureInfo.InvariantCulture,
                    System.Globalization.DateTimeStyles.RoundtripKind, out at) || at.ToUniversalTime() < askedUtc)
                return "the distro (" + Paths.ServiceName + " gave no result for this request; see " + Paths.HostLog + ")";
            if (Json.Bool(d, "ok") != true) return "the distro (" + (Json.Str(d, "error") ?? "unregister failed") + ")";
            return null;
        }
    }

    public sealed class UninstallLeftover
    {
        public UninstallStep Step;
        public string What;

        public UninstallLeftover(UninstallStep step, string what) { Step = step; What = what; }

        // How to remove it by hand, for the message and the log.
        public string HowToRemove()
        {
            switch (Step == null ? UninstallKind.Blocked : Step.Kind)
            {
                case UninstallKind.DeleteService: return "it goes at the next restart; or, as an administrator: sc.exe delete " + Paths.ServiceName;
                case UninstallKind.DeleteProgramData: return "restart Windows, then delete " + Paths.ProgramData + " as an administrator";
                case UninstallKind.DeleteProfile: return "restart Windows, then: Settings > System > About > Advanced system settings > User Profiles > Settings, delete the domovoi-svc profile (it may show as \"Account Unknown\")";
                case UninstallKind.RemoveMediaAccess: return "as an administrator: icacls \"" + Step.Target + "\" /remove *" + string.Join(" *", Step.Sids.ToArray());
                case UninstallKind.UnhideAccount: return "as an administrator: reg delete \"HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\\SpecialAccounts\\UserList\" /v " + Paths.AccountName + " /f";
                case UninstallKind.RemoveRunOnce: return "as an administrator: reg delete \"HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce\" /v DomovoiSetupResume /f";
                case UninstallKind.RemoveFirewallRules: return "in an administrator PowerShell: Get-NetFirewallHyperVRule -Name 'Domovoi-*' | Remove-NetFirewallHyperVRule";
                case UninstallKind.RemoveRights: return "the rights of " + Step.Target + " are inert once the account is gone; on Pro, Local Security Policy > User Rights Assignment lists them";
                case UninstallKind.DeleteAccount: return "as an administrator: net user " + Paths.AccountName + " /delete";
                case UninstallKind.StopAccountProcesses: return "end the programs running as " + Paths.AccountName + " (Task Manager > Details), then delete the account";
                case UninstallKind.RemoveRecord: return "as an administrator: reg delete \"HKLM\\SOFTWARE\\Domovoi\" /f";
                default: return "see docs/WINDOWS_HOST.md, Uninstalling";
            }
        }
    }

    public static class UninstallRunner
    {
        public const int ExitDone = 0;
        public const int ExitStopped = 1;
        // Leftovers that may still clear: the uninstaller stays for another run.
        public const int ExitLeftovers = 3;
        // Finished with leftovers listed for removal by hand: the uninstaller goes.
        public const int ExitFinishedWithLeftovers = 4;
        // The run that finishes whatever is left.
        public const int MaxRuns = 3;

        // Leftovers that hold none of Domovoi's data and give nothing access
        // once the account is gone: the service marked for deletion, files
        // that go at the next restart, the profile folder of a deleted
        // account, an access rule for a deleted account's SID, the sign-in
        // entry, the resume entry, an empty folder. They never keep Domovoi
        // in Installed apps. The account itself, its rights, its processes
        // and the firewall rules do, until the MaxRuns-th run.
        public static readonly UninstallKind[] Inert =
        {
            UninstallKind.DeleteService, UninstallKind.DeleteProgramData, UninstallKind.DeleteProfile,
            UninstallKind.RemoveMediaAccess, UninstallKind.RemoveFolderIfEmpty, UninstallKind.UnhideAccount,
            UninstallKind.RemoveRunOnce, UninstallKind.RemoveRecord,
        };

        public static bool IsInert(UninstallKind k) { return Array.IndexOf(Inert, k) >= 0; }

        // Runs the plan; run is this uninstall's number (1 for the first).
        // doStep returns null when the step is done, or what is left. A
        // critical step that doesn't finish stops the run (exit 1). Other
        // leftovers: when they are all inert, or this is run MaxRuns, the
        // run finishes (exit 4: the record goes, the leftovers are listed
        // with how to remove them by hand); otherwise exit 3 and the record
        // stays, so the next run still knows the account's SID.
        public static int Run(List<UninstallStep> plan, Func<UninstallStep, string> doStep, List<UninstallLeftover> left, List<UninstallStep> ran, int run)
        {
            UninstallStep record = null;
            foreach (var s in plan)
            {
                if (s.Kind == UninstallKind.RemoveRecord) { record = s; continue; }
                string what = Do(s, doStep, ran);
                if (what == null) continue;
                left.Add(new UninstallLeftover(s, what));
                if (s.Critical) return ExitStopped;
            }
            bool finish = left.Count == 0 || run >= MaxRuns;
            if (!finish)
            {
                finish = true;
                foreach (var l in left) if (!IsInert(l.Step.Kind)) finish = false;
            }
            if (!finish) return ExitLeftovers;
            if (record != null)
            {
                string what = Do(record, doStep, ran);
                if (what != null) left.Add(new UninstallLeftover(record, what));
            }
            return left.Count == 0 ? ExitDone : ExitFinishedWithLeftovers;
        }

        static string Do(UninstallStep s, Func<UninstallStep, string> doStep, List<UninstallStep> ran)
        {
            string what;
            try
            {
                what = s.Kind == UninstallKind.Blocked ? s.Reason : doStep(s);
            }
            catch (Exception e)
            {
                what = s + ": " + e.Message;
            }
            if (ran != null) ran.Add(s);
            return what;
        }
    }
}