// The folders Setup makes and their access rules (PLAN 4.6).
//
// C:\ProgramData\Domovoi: SYSTEM and Administrators only (protected, so
// nothing inherits from ProgramData, where every user may create files),
// plus domovoi-svc where it needs it:
//   distro, wsl, backups, logs, host\out   Modify
//   host\in                                Read (the job and requests)
//   state                                  nothing (install.json)
// Household users get nothing here: the distro disk and the backups hold
// the server's identity key and secrets.
//
// The media root: domovoi-svc Modify (inherited by everything inside), and
// on a folder Setup makes, the household (Users) Modify too. An existing
// folder only gets domovoi-svc added; its other rules stay as they are.
//
// This runs elevated over folders domovoi-svc (and so the distro's root,
// through drvfs) can write to, so it never follows a junction or symbolic
// link: a reparse point where Setup expects a folder is refused, and
// deletion removes links without entering them.

using System;
using System.Collections.Generic;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;

namespace Domovoi.Windows.Helper
{
    static class Folders
    {
        static readonly SecurityIdentifier SystemSid = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
        static readonly SecurityIdentifier AdminsSid = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        public static readonly SecurityIdentifier UsersSid = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);

        const InheritanceFlags Inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;

        static bool IsReparse(string path)
        {
            return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;
        }

        static void RefuseReparse(string path)
        {
            if (Directory.Exists(path) && IsReparse(path))
                throw new InvalidOperationException(path + " is a link (junction or symbolic link), not a folder; Setup won't use it");
        }

        static bool OwnerTrusted(string path)
        {
            var sec = Directory.GetAccessControl(path, AccessControlSections.Owner);
            var owner = sec.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier;
            return owner != null && (owner.Equals(SystemSid) || owner.Equals(AdminsSid) || owner.Equals(TrustedInstaller()));
        }

        static SecurityIdentifier TrustedInstaller()
        {
            // NT SERVICE\TrustedInstaller
            return new SecurityIdentifier("S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464");
        }

        static DirectorySecurity RootSecurity()
        {
            var s = new DirectorySecurity();
            s.SetOwner(AdminsSid);
            s.SetAccessRuleProtection(true, false);
            s.AddAccessRule(new FileSystemAccessRule(SystemSid, FileSystemRights.FullControl, Inherit, PropagationFlags.None, AccessControlType.Allow));
            s.AddAccessRule(new FileSystemAccessRule(AdminsSid, FileSystemRights.FullControl, Inherit, PropagationFlags.None, AccessControlType.Allow));
            return s;
        }

        // Makes or fixes the ProgramData tree. True when Setup made the root.
        public static bool EnsureProgramData(SecurityIdentifier svc, Action<string> say)
        {
            string root = Paths.ProgramData;
            bool created = false;
            if (Directory.Exists(root))
            {
                RefuseReparse(root);
                if (!OwnerTrusted(root))
                    throw new InvalidOperationException(root + " already exists and is not owned by Administrators; Setup won't use a folder someone else made. Delete it and run Setup again.");
            }
            else
            {
                Directory.CreateDirectory(root, RootSecurity());
                created = true;
                say("created " + root + " (SYSTEM and Administrators only)");
            }
            Directory.SetAccessControl(root, RootSecurity());
            Sub(Paths.Distro, svc, FileSystemRights.Modify, say);
            Sub(Paths.Wsl, svc, FileSystemRights.Modify, say);
            Sub(Paths.Backups, svc, FileSystemRights.Modify, say);
            Sub(Paths.Logs, svc, FileSystemRights.Modify, say);
            Sub(Paths.State, null, 0, say);
            Sub(Paths.HostDir, null, 0, say);
            Sub(Paths.HostIn, svc, FileSystemRights.ReadAndExecute, say);
            Sub(Paths.HostOut, svc, FileSystemRights.Modify, say);
            return created;
        }

        // Only the tree's admin-only parts, before the account exists (the
        // ledger lives in state\).
        public static bool EnsureProgramDataBase(Action<string> say)
        {
            string root = Paths.ProgramData;
            bool created = false;
            if (Directory.Exists(root))
            {
                RefuseReparse(root);
                if (!OwnerTrusted(root))
                    throw new InvalidOperationException(root + " already exists and is not owned by Administrators; Setup won't use a folder someone else made. Delete it and run Setup again.");
            }
            else
            {
                Directory.CreateDirectory(root, RootSecurity());
                created = true;
                say("created " + root + " (SYSTEM and Administrators only)");
            }
            Directory.SetAccessControl(root, RootSecurity());
            Sub(Paths.State, null, 0, say);
            Sub(Paths.Logs, null, 0, say);
            return created;
        }

        static void Sub(string path, SecurityIdentifier svc, FileSystemRights rights, Action<string> say)
        {
            if (!Directory.Exists(path)) Directory.CreateDirectory(path);
            RefuseReparse(path);
            var s = new DirectorySecurity();
            s.SetOwner(AdminsSid);
            s.SetAccessRuleProtection(false, false);
            if (svc != null)
                s.AddAccessRule(new FileSystemAccessRule(svc, rights, Inherit, PropagationFlags.None, AccessControlType.Allow));
            Directory.SetAccessControl(path, s);
        }

        // -- media ------------------------------------------------------

        public static void CheckMediaRoot(string root)
        {
            string why = MediaPaths.Problem(root);
            if (why != null) throw new ArgumentException("media folder: " + why);
            var drive = new DriveInfo(MediaPaths.Normalize(root).Substring(0, 1));
            if (!drive.IsReady) throw new ArgumentException("media folder: drive " + drive.Name + " is not ready");
            if (drive.DriveType != DriveType.Fixed && drive.DriveType != DriveType.Removable)
                throw new ArgumentException("media folder: pick a folder on a local drive (" + drive.Name + " is " + drive.DriveType + ")");
        }

        // One media folder: made if missing, domovoi-svc (and, on a folder
        // Setup made, Users) allowed Modify. Records it in the ledger.
        public static void EnsureMediaFolder(string path, SecurityIdentifier svc, bool isRoot, Ledger ledger, Action<string> say)
        {
            MediaFolderRecord rec = ledger.MediaFor(path);
            if (rec == null)
            {
                rec = new MediaFolderRecord();
                rec.Path = path;
                ledger.Media.Add(rec);
            }
            if (!Directory.Exists(path))
            {
                Directory.CreateDirectory(path);
                rec.Created = true;
                say("created " + path);
            }
            DirectorySecurity s = Directory.GetAccessControl(path, AccessControlSections.Access);
            bool changed = false;
            if (rec.Created && isRoot)
            {
                s.AddAccessRule(new FileSystemAccessRule(UsersSid, FileSystemRights.Modify, Inherit, PropagationFlags.None, AccessControlType.Allow));
                changed = true;
            }
            s.AddAccessRule(new FileSystemAccessRule(svc, FileSystemRights.Modify, Inherit, PropagationFlags.None, AccessControlType.Allow));
            changed = true;
            if (changed) Directory.SetAccessControl(path, s);
            if (!rec.AddedSids.Contains(svc.Value)) rec.AddedSids.Add(svc.Value);
            say("allowed " + Paths.AccountName + " to read and write " + path + (rec.Created && isRoot ? " (and the household, Users)" : ""));
        }

        public static void RemoveAccess(string path, IEnumerable<string> sids, Action<string> say)
        {
            if (!Directory.Exists(path)) return;
            DirectorySecurity s = Directory.GetAccessControl(path, AccessControlSections.Access);
            foreach (string sid in sids) s.PurgeAccessRules(new SecurityIdentifier(sid));
            Directory.SetAccessControl(path, s);
            say("removed Setup's access rule from " + path);
        }

        // Only an empty folder goes; anything inside it, even a hidden
        // desktop.ini, keeps it.
        public static bool RemoveIfEmpty(string path, Action<string> say)
        {
            if (!Directory.Exists(path)) return true;
            if (IsReparse(path)) { say("kept " + path + " (a link, not a folder Setup made)"); return false; }
            using (var e = Directory.EnumerateFileSystemEntries(path).GetEnumerator())
            {
                if (e.MoveNext()) { say("kept " + path + " (it has files in it)"); return false; }
            }
            Directory.Delete(path, false);
            say("removed the empty folder " + path);
            return true;
        }

        // Deletes a tree without following links: a junction or symbolic
        // link inside it is removed as a link, never entered.
        public static void DeleteTree(string path)
        {
            if (!Directory.Exists(path)) return;
            var info = new DirectoryInfo(path);
            if ((info.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                Directory.Delete(path, false);
                return;
            }
            foreach (var entry in info.EnumerateFileSystemInfos())
            {
                if ((entry.Attributes & FileAttributes.Directory) != 0)
                {
                    if ((entry.Attributes & FileAttributes.ReparsePoint) != 0) Directory.Delete(entry.FullName, false);
                    else DeleteTree(entry.FullName);
                }
                else
                {
                    if ((entry.Attributes & FileAttributes.ReadOnly) != 0) entry.Attributes &= ~FileAttributes.ReadOnly;
                    File.Delete(entry.FullName);
                }
            }
            Directory.Delete(path, false);
        }
    }
}
