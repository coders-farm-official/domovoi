// What the uninstaller does, decided from what Setup recorded (install.json)
// and what it finds on the PC now, so the same plan undoes a finished
// install, a half-finished one, and nothing at all (a second run). PLAN 7,
// "Remove everything"; keep-data uninstall is step 4.
//
// Rules the plan holds to:
// * the distro is unregistered as domovoi-svc, through DomovoiHost, before
//   the account goes (PLAN 7: unregister deletes ext4.vhdx);
// * nothing in a media folder is ever deleted: a folder Setup made is
//   removed only when it is empty, and only Setup's own access rules come
//   off the others;
// * an account, a service or a registry key Setup did not make is left
//   alone (a domovoi-svc someone made by hand has no Setup marker);
// * every step is safe when its target is already gone.

using System;
using System.Collections.Generic;

namespace Domovoi.Windows
{
    public enum UninstallKind
    {
        StopService,
        RotatePassword,
        UnregisterDistro,
        DeleteService,
        RemoveFirewallRules,
        RemoveMediaAccess,
        RemoveFolderIfEmpty,
        DeleteProfile,
        RemoveRights,
        UnhideAccount,
        DeleteAccount,
        DeleteProgramData,
        RemoveRunOnce,
    }

    public sealed class UninstallStep
    {
        public UninstallKind Kind;
        public string Target;
        public List<string> Sids = new List<string>();
        public bool DeleteUserListKey;
        public bool DeleteSpecialAccountsKey;

        public UninstallStep(UninstallKind kind, string target) { Kind = kind; Target = target; }

        public override string ToString()
        {
            switch (Kind)
            {
                case UninstallKind.StopService: return "stop the " + Target + " service";
                case UninstallKind.RotatePassword: return "set a fresh password on " + Target + " and its service (never shown or kept)";
                case UninstallKind.UnregisterDistro: return "unregister the '" + Target + "' WSL distro as " + Paths.AccountName + " (deletes its disk)";
                case UninstallKind.DeleteService: return "delete the " + Target + " service";
                case UninstallKind.RemoveFirewallRules: return "remove the Hyper-V firewall rules " + Target;
                case UninstallKind.RemoveMediaAccess: return "remove Setup's access rules from " + Target;
                case UninstallKind.RemoveFolderIfEmpty: return "remove " + Target + " if it is empty";
                case UninstallKind.DeleteProfile: return "delete the profile of " + Paths.AccountName + " (" + Target + ")";
                case UninstallKind.RemoveRights: return "remove the account rights of " + Target;
                case UninstallKind.UnhideAccount: return "remove the sign-in screen entry for " + Target;
                case UninstallKind.DeleteAccount: return "delete the local account " + Target;
                case UninstallKind.DeleteProgramData: return "delete " + Target;
                case UninstallKind.RemoveRunOnce: return "remove Setup's resume-after-restart entry";
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
        public List<string> Rights = new List<string>();
        public bool UserListValuePresent;
        public bool ProgramDataExists;
        public bool RunOnceValuePresent;
        // Media folders from the ledger that exist now.
        public HashSet<string> MediaExisting = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    }

    public static class UninstallPlan
    {
        public static List<UninstallStep> Make(Ledger ledger, UninstallFacts f)
        {
            if (f == null) throw new ArgumentNullException("f");
            var steps = new List<UninstallStep>();
            bool accountOurs = f.AccountExists && (f.AccountHasMarker || (ledger != null && ledger.AccountCreated
                && ledger.AccountSid != null && string.Equals(ledger.AccountSid, f.AccountSid, StringComparison.OrdinalIgnoreCase)));
            bool serviceOurs = f.ServiceExists && (f.ServiceIsDomovoi || (ledger != null && ledger.ServiceCreated));
            string sid = f.AccountSid ?? (ledger != null ? ledger.AccountSid : null);

            if (serviceOurs && f.ServiceRunning) steps.Add(new UninstallStep(UninstallKind.StopService, Paths.ServiceName));
            // The distro belongs to the account; only the host, running as
            // it, can unregister it. Starting the host needs the account's
            // password in the SCM, so it gets a fresh one first (PLAN 4.5:
            // uninstall rotates, nothing stores it).
            if (serviceOurs && accountOurs)
            {
                steps.Add(new UninstallStep(UninstallKind.RotatePassword, Paths.AccountName));
                steps.Add(new UninstallStep(UninstallKind.UnregisterDistro, Paths.DistroName));
            }
            if (serviceOurs) steps.Add(new UninstallStep(UninstallKind.DeleteService, Paths.ServiceName));
            if (f.FirewallRules.Count > 0)
                steps.Add(new UninstallStep(UninstallKind.RemoveFirewallRules, string.Join(", ", f.FirewallRules.ToArray())));

            if (ledger != null)
            {
                // Access rules first, then empty folders, deepest first, so a
                // root goes only after the folders inside it.
                var media = new List<MediaFolderRecord>(ledger.Media);
                media.Sort(delegate (MediaFolderRecord a, MediaFolderRecord b) { return b.Path.Length.CompareTo(a.Path.Length); });
                foreach (var m in media)
                {
                    if (!f.MediaExisting.Contains(m.Path)) continue;
                    if (m.AddedSids.Count > 0)
                    {
                        var s = new UninstallStep(UninstallKind.RemoveMediaAccess, m.Path);
                        s.Sids.AddRange(m.AddedSids);
                        steps.Add(s);
                    }
                }
                foreach (var m in media)
                {
                    if (!f.MediaExisting.Contains(m.Path) || !m.Created) continue;
                    steps.Add(new UninstallStep(UninstallKind.RemoveFolderIfEmpty, m.Path));
                }
            }

            if (sid != null && f.ProfileExists && (accountOurs || !f.AccountExists))
                steps.Add(new UninstallStep(UninstallKind.DeleteProfile, sid));
            if (sid != null && f.Rights.Count > 0 && (accountOurs || !f.AccountExists))
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
            if (f.ProgramDataExists) steps.Add(new UninstallStep(UninstallKind.DeleteProgramData, Paths.ProgramData));
            if (f.RunOnceValuePresent) steps.Add(new UninstallStep(UninstallKind.RemoveRunOnce, "DomovoiSetupResume"));
            return steps;
        }
    }
}
