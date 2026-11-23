// What a keep-data uninstall kept, and the house identity backup (S3-4,
// owner decision 2026-11-22: keep the house identity across a reinstall).
//
//   kept-data              read-only: the newest C:\ProgramData\Domovoi-kept-<time>
//                          that holds a distro disk (kept_dir=, kept_gb=), for
//                          Setup's "use the kept data" choice
//   adopt-kept --dir DIR   move that disk back into C:\ProgramData\Domovoi\distro
//                          (after the account step made the folder), its access
//                          rules reset to the folder's, so DomovoiHost imports
//                          it in place (job --import-in-place)
//
// Only a folder named like the uninstaller's, directly in ProgramData, owned
// by Administrators or SYSTEM and not a link, is ever used: a household user
// can't hand Setup a disk of their own making.
//
// ReadBackup checks that a house identity backup file is plausibly one
// (size, JSON with the key fields) before Setup copies it into host\in; the
// key pair itself is checked in the distro by the service user's python
// (provision.sh --restore-identity), which refuses anything that isn't a
// working key. Neither the file nor its contents are ever logged.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;

namespace Domovoi.Windows.Helper
{
    static class KeptData
    {
        const string DiskName = "ext4.vhdx";
        public const int BackupMaxBytes = 16 * 1024;

        static bool Trusted(string dir)
        {
            if (!Directory.Exists(dir) || Folders.IsReparse(dir)) return false;
            var sec = Directory.GetAccessControl(dir, AccessControlSections.Owner);
            var owner = sec.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier;
            return owner != null && (owner.IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid) || owner.IsWellKnown(WellKnownSidType.LocalSystemSid));
        }

        // The kept folders, newest first.
        public static List<string> Candidates()
        {
            var found = new List<string>();
            string parent = Path.GetDirectoryName(Paths.ProgramData);
            string prefix = Path.GetFileName(Paths.KeptPrefix);
            if (!Directory.Exists(parent)) return found;
            foreach (string d in Directory.GetDirectories(parent, prefix + "*"))
            {
                try
                {
                    string disk = Path.Combine(d, DiskName);
                    if (KeptName.IsKept(Path.GetFileName(d)) && Trusted(d) && File.Exists(disk)
                        && (File.GetAttributes(disk) & FileAttributes.ReparsePoint) == 0)
                        found.Add(d);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
            found.Sort(StringComparer.Ordinal);
            found.Reverse();
            return found;
        }

        public static void Find(Output o)
        {
            List<string> all = Candidates();
            o.Kv("kept_count", Inv.S(all.Count));
            if (all.Count == 0) return;
            string d = all[0];
            long bytes = new FileInfo(Path.Combine(d, DiskName)).Length;
            o.Kv("kept_dir", d);
            o.Kv("kept_gb", (bytes / (1024.0 * 1024 * 1024)).ToString("0.0", CultureInfo.InvariantCulture));
            o.Kv("kept_when", KeptName.When(Path.GetFileName(d)) ?? "");
        }

        public static void Adopt(Output o, string dir)
        {
            string full = Path.GetFullPath(dir).TrimEnd('\\');
            if (!Candidates().Exists(c => string.Equals(c, full, StringComparison.OrdinalIgnoreCase)))
                throw new InvalidOperationException(full + " is not a folder of Domovoi's kept data (" + Paths.KeptPrefix + "<time>, made by the uninstaller)");
            if (!Directory.Exists(Paths.Distro) || Folders.IsReparse(Paths.Distro))
                throw new InvalidOperationException(Paths.Distro + " isn't ready (the account step makes it)");
            if (File.Exists(Paths.DistroDisk))
                throw new InvalidOperationException(Paths.DistroDisk + " is already there; Setup won't replace a disk");
            string src = Path.Combine(full, DiskName);
            File.Move(src, Paths.DistroDisk);
            // The disk kept the rules it had in the kept folder (moves keep a
            // file's security): give it the distro folder's, which let the
            // new domovoi-svc use it.
            var fs = new FileSecurity();
            fs.SetOwner(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null));
            fs.SetAccessRuleProtection(false, false);
            File.SetAccessControl(Paths.DistroDisk, fs);
            try { Directory.Delete(full); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            o.Say("moved the kept disk from " + full + " back into " + Paths.Distro + " (DomovoiHost imports it in place)");
        }

        // The backup's bytes, if it is plausibly a house identity backup.
        public static byte[] ReadBackup(string path)
        {
            var fi = new FileInfo(path);
            if (!fi.Exists) throw new InvalidOperationException("the house identity backup " + path + " isn't there");
            if ((fi.Attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("the house identity backup is a link; give the file itself");
            if (fi.Length > BackupMaxBytes) throw new InvalidOperationException("that file is far too large to be a house identity backup");
            byte[] raw = File.ReadAllBytes(path);
            string why = BackupShape.Problem(raw);
            if (why != null) throw new InvalidOperationException(why);
            return raw;
        }
    }
}
