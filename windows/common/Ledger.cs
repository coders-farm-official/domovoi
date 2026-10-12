// C:\ProgramData\Domovoi\state\install.json: the installer's answers and
// what it changed on this PC, written as it goes (PLAN 4.3: no state lives
// only in memory). The uninstaller reads it together with what it finds,
// so it can undo a finished install, a half-finished one, or none.

using System;
using System.Collections.Generic;

namespace Domovoi.Windows
{
    public sealed class MediaFolderRecord
    {
        public string Path;
        // Setup made the folder (so it may remove it again, if empty).
        public bool Created;
        // SIDs Setup added an access rule for (removed again on uninstall).
        public List<string> AddedSids = new List<string>();
    }

    public sealed class Ledger
    {
        public string Internet;
        public string MediaRoot;
        public string Commit;
        public string AccountSid;
        public bool AccountCreated;
        public bool UserListKeyCreated;
        public bool SpecialAccountsKeyCreated;
        public List<string> Rights = new List<string>();
        public bool ServiceCreated;
        public bool ProgramDataCreated;
        public List<string> FirewallRules = new List<string>();
        public string FirewallSubnet;
        public List<MediaFolderRecord> Media = new List<MediaFolderRecord>();
        public int JobGeneration;
        public int JobAttempt;
        // Uninstall runs so far (UninstallRunner.MaxRuns).
        public int UninstallRuns;
        public string Updated;

        public MediaFolderRecord MediaFor(string path)
        {
            foreach (var m in Media)
                if (string.Equals(m.Path, path, StringComparison.OrdinalIgnoreCase)) return m;
            return null;
        }

        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["v"] = 1;
            d["internet"] = Internet;
            d["media_root"] = MediaRoot;
            d["commit"] = Commit;
            d["account_sid"] = AccountSid;
            d["account_created"] = AccountCreated;
            d["userlist_key_created"] = UserListKeyCreated;
            d["specialaccounts_key_created"] = SpecialAccountsKeyCreated;
            d["rights"] = Rights.ToArray();
            d["service_created"] = ServiceCreated;
            d["programdata_created"] = ProgramDataCreated;
            d["firewall_rules"] = FirewallRules.ToArray();
            d["firewall_subnet"] = FirewallSubnet;
            var media = new List<object>();
            foreach (var m in Media)
            {
                var md = new Dictionary<string, object>();
                md["path"] = m.Path;
                md["created"] = m.Created;
                md["added_sids"] = m.AddedSids.ToArray();
                media.Add(md);
            }
            d["media"] = media.ToArray();
            d["job_generation"] = JobGeneration;
            d["job_attempt"] = JobAttempt;
            d["uninstall_runs"] = UninstallRuns;
            d["updated"] = Updated;
            return d;
        }

        public static Ledger FromJson(Dictionary<string, object> d)
        {
            if (Json.Int(d, "v") != 1) throw new FormatException("install.json: unknown version");
            var l = new Ledger();
            l.Internet = Json.Str(d, "internet");
            l.MediaRoot = Json.Str(d, "media_root");
            l.Commit = Json.Str(d, "commit");
            l.AccountSid = Json.Str(d, "account_sid");
            l.AccountCreated = Json.Bool(d, "account_created") ?? false;
            l.UserListKeyCreated = Json.Bool(d, "userlist_key_created") ?? false;
            l.SpecialAccountsKeyCreated = Json.Bool(d, "specialaccounts_key_created") ?? false;
            l.Rights = Json.StrList(d, "rights") ?? new List<string>();
            l.ServiceCreated = Json.Bool(d, "service_created") ?? false;
            l.ProgramDataCreated = Json.Bool(d, "programdata_created") ?? false;
            l.FirewallRules = Json.StrList(d, "firewall_rules") ?? new List<string>();
            l.FirewallSubnet = Json.Str(d, "firewall_subnet");
            var media = Json.ObjList(d, "media");
            if (media != null)
            {
                foreach (var md in media)
                {
                    var m = new MediaFolderRecord();
                    m.Path = Json.Str(md, "path");
                    m.Created = Json.Bool(md, "created") ?? false;
                    m.AddedSids = Json.StrList(md, "added_sids") ?? new List<string>();
                    if (!string.IsNullOrEmpty(m.Path)) l.Media.Add(m);
                }
            }
            l.JobGeneration = Json.Int(d, "job_generation") ?? 0;
            l.JobAttempt = Json.Int(d, "job_attempt") ?? 0;
            l.UninstallRuns = Json.Int(d, "uninstall_runs") ?? 0;
            l.Updated = Json.Str(d, "updated");
            return l;
        }
    }
}
