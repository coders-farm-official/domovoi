// A profile folder an earlier Domovoi left behind: C:\Users\domovoi-svc (or
// domovoi-svc.<name>) whose account is gone. Nothing in Setup depends on
// the folder's name (DomovoiHost writes .wslconfig into its own profile,
// whatever Windows called it; the uninstall finds the profile by SID), but
// a new install next to the old folder gets C:\Users\domovoi-svc.<PC name>
// and the old one stays for good, so Setup asks for it to be removed
// first, with the one line that does it (2026-10-12: the first test
// install's profile, held by WSL, outlived its uninstall).

using System;
using System.Collections.Generic;

namespace Domovoi.Windows
{
    public sealed class ProfileEntry
    {
        public string Sid;
        public string Path;
        public ProfileEntry(string sid, string path) { Sid = sid; Path = path; }
    }

    public sealed class LeftoverProfile
    {
        public string Path;
        // The SID its ProfileList entry names, or null for a folder with no entry.
        public string Sid;

        // The one line, for an administrator PowerShell, that removes it the
        // way Settings > User Profiles does (or, with no profile entry, the
        // folder itself).
        public string Cleanup()
        {
            if (Sid != null)
                return "Get-CimInstance Win32_UserProfile -Filter \"SID='" + Sid + "'\" | Remove-CimInstance";
            return "Remove-Item -LiteralPath '" + Path.Replace("'", "''") + "' -Recurse -Force";
        }
    }

    public static class LeftoverProfiles
    {
        // entries: ProfileList (SID, ProfileImagePath); folders: the folders
        // in the profiles folder; sidIsAnAccount: whether a SID still maps to
        // an account (false only when Windows says it maps to none).
        public static List<LeftoverProfile> Find(IEnumerable<ProfileEntry> entries, IEnumerable<string> folders, Func<string, bool> sidIsAnAccount)
        {
            var found = new List<LeftoverProfile>();
            var known = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var e in entries)
            {
                if (e == null || string.IsNullOrEmpty(e.Path)) continue;
                known.Add(e.Path.TrimEnd('\\'));
                if (!UninstallFacts.IsDomovoiProfilePath(e.Path)) continue;
                if (!ProfileTask.IsLocalAccountSid(e.Sid) || sidIsAnAccount(e.Sid)) continue;
                var l = new LeftoverProfile();
                l.Path = e.Path.TrimEnd('\\');
                l.Sid = e.Sid;
                found.Add(l);
            }
            // A domovoi-svc folder no profile entry names: what a deleted
            // profile left on disk. (A live account's profile has an entry.)
            foreach (string f in folders)
            {
                if (string.IsNullOrEmpty(f) || known.Contains(f.TrimEnd('\\')) || !UninstallFacts.IsDomovoiProfilePath(f)) continue;
                var l = new LeftoverProfile();
                l.Path = f.TrimEnd('\\');
                found.Add(l);
            }
            return found;
        }

        public static string Message(List<LeftoverProfile> found)
        {
            var parts = new List<string>();
            foreach (var l in found) parts.Add(found.Count == 1 ? l.Cleanup() : l.Path + ": " + l.Cleanup());
            return "An earlier Domovoi left its account's profile folder behind (" + (found.Count == 1 ? found[0].Path : Inv.S(found.Count) + " folders")
                + "), and that account is gone. Restart Windows (Windows may still hold it), then remove it from an administrator PowerShell with "
                + string.Join("; and ", parts.ToArray())
                + " (or Settings > System > About > Advanced system settings > User Profiles > Settings, \"Account Unknown\"), then click Check again.";
        }
    }
}
