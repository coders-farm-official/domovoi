// The folders Setup makes and their access rules (PLAN 4.6).
//
// C:\ProgramData\Domovoi: SYSTEM and Administrators only (protected, so
// nothing inherits from ProgramData, where every user may create files),
// plus domovoi-svc where it needs it:
//   distro, wsl, backups, logs, host\out   Modify
//   host\in                                Read (the job, requests, downloads)
//   state                                  nothing (install.log)
// Household users get nothing here: the distro disk and the backups hold
// the server's identity key and secrets. Setup uses this folder only if it
// made it (a pre-existing one is refused, never re-permissioned), and the
// uninstaller deletes it only then.
//
// The media root: domovoi-svc Modify (inherited by everything inside), and
// on a folder Setup makes, the household (Users) Modify too. An existing
// folder only gets domovoi-svc added; its other rules stay as they are. A
// system or program folder, a profile root or anything holding one is
// refused (MediaPaths.SystemProblem).
//
// This runs elevated over folders domovoi-svc (and so the distro's root,
// through drvfs) can write to, so it never follows a junction or symbolic
// link: a reparse point where Setup expects a folder is refused (checked
// again after creating it), ACL changes skip links, and deletion removes
// links without entering them. Before a tree domovoi-svc could write is
// deleted, the account's processes are ended and the tree is made
// admin-only again (ResetForDelete).

using System;
using System.Collections.Generic;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;
using Microsoft.Win32;

namespace Domovoi.Windows.Helper
{
    static class Folders
    {
        static readonly SecurityIdentifier SystemSid = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
        static readonly SecurityIdentifier AdminsSid = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        public static readonly SecurityIdentifier UsersSid = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);

        const InheritanceFlags Inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;

        public static bool IsReparse(string path)
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
            return owner != null && (owner.Equals(SystemSid) || owner.Equals(AdminsSid));
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

        // The root, made by Setup (ours: the install record says so), or
        // refused. Checked again after creating it: a link someone made
        // between the check and the create would otherwise be used.
        static void EnsureRoot(bool ours, Action<string> say)
        {
            string root = Paths.ProgramData;
            if (Directory.Exists(root))
            {
                if (!ours)
                    throw new InvalidOperationException(root + " already exists and Domovoi Setup did not make it; Setup won't use or change a folder it didn't make. Move it away and run Setup again.");
            }
            else
            {
                if (!ours) throw new InvalidOperationException("internal: the install record must note " + root + " before Setup makes it");
                Directory.CreateDirectory(root, RootSecurity());
                say("created " + root + " (SYSTEM and Administrators only)");
            }
            RefuseReparse(root);
            if (!OwnerTrusted(root))
                throw new InvalidOperationException(root + " is not owned by Administrators; Setup won't use it");
            Directory.SetAccessControl(root, RootSecurity());
        }

        // The admin-only parts, before the account exists (the install log
        // lives in state\).
        public static void EnsureProgramDataBase(bool ours, Action<string> say)
        {
            EnsureRoot(ours, say);
            Sub(Paths.State, null, 0);
        }

        public static void EnsureProgramData(SecurityIdentifier svc, bool ours, Action<string> say)
        {
            EnsureRoot(ours, say);
            Sub(Paths.Distro, svc, FileSystemRights.Modify);
            Sub(Paths.Wsl, svc, FileSystemRights.Modify);
            Sub(Paths.Backups, svc, FileSystemRights.Modify);
            Sub(Paths.Logs, svc, FileSystemRights.Modify);
            Sub(Paths.State, null, 0);
            Sub(Paths.HostDir, null, 0);
            Sub(Paths.HostIn, svc, FileSystemRights.ReadAndExecute);
            Sub(Paths.HostOut, svc, FileSystemRights.Modify);
        }

        static void Sub(string path, SecurityIdentifier svc, FileSystemRights rights)
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

        // Before deleting the tree: admin-only again, top down, skipping
        // links, so nothing the account could still write is left writable
        // while the elevated delete runs.
        public static void ResetForDelete(string sid, Action<string> say)
        {
            string root = Paths.ProgramData;
            if (!Directory.Exists(root)) return;
            RefuseReparse(root);
            Directory.SetAccessControl(root, RootSecurity());
            if (sid == null) return;
            var account = new SecurityIdentifier(sid);
            foreach (string d in new[] { Paths.Distro, Paths.Wsl, Paths.Backups, Paths.Logs, Paths.State, Paths.HostDir, Paths.HostIn, Paths.HostOut })
            {
                if (!Directory.Exists(d) || IsReparse(d)) continue;
                DirectorySecurity s = Directory.GetAccessControl(d, AccessControlSections.Access);
                s.PurgeAccessRules(account);
                Directory.SetAccessControl(d, s);
            }
            say("made " + root + " administrators-only before deleting it");
        }

        // A new folder only administrators and SYSTEM can open (for a kept
        // distro disk).
        public static void CreateAdminOnly(string path)
        {
            if (Directory.Exists(path)) throw new InvalidOperationException(path + " already exists");
            Directory.CreateDirectory(path, RootSecurity());
            RefuseReparse(path);
            if (!OwnerTrusted(path)) throw new InvalidOperationException(path + " is not owned by Administrators");
        }

        // -- media ------------------------------------------------------

        // The folders a media root may not be, be inside or hold, on this PC.
        public static SystemFolders SystemFoldersHere()
        {
            var s = new SystemFolders();
            string win = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            string sysDrive = Path.GetPathRoot(win).TrimEnd('\\');
            foreach (string t in new[]
            {
                win,
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                Environment.GetEnvironmentVariable("ProgramW6432"),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                sysDrive + @"\$Recycle.Bin", sysDrive + @"\System Volume Information", sysDrive + @"\Recovery",
            })
                if (!string.IsNullOrEmpty(t) && !s.Trees.Contains(t)) s.Trees.Add(t);
            string profiles = null, defaultProfile = null, publicProfile = null;
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(Account.ProfileListKey))
            {
                if (k != null)
                {
                    profiles = Environment.ExpandEnvironmentVariables(k.GetValue("ProfilesDirectory") as string ?? "");
                    defaultProfile = Environment.ExpandEnvironmentVariables(k.GetValue("Default") as string ?? "");
                    publicProfile = Environment.ExpandEnvironmentVariables(k.GetValue("Public") as string ?? "");
                    foreach (string sid in k.GetSubKeyNames())
                    {
                        using (var p = k.OpenSubKey(sid))
                        {
                            string img = p == null ? null : p.GetValue("ProfileImagePath") as string;
                            if (string.IsNullOrEmpty(img)) continue;
                            img = Environment.ExpandEnvironmentVariables(img);
                            s.Exact.Add(img);
                            s.Trees.Add(Path.Combine(img, "AppData"));
                        }
                    }
                }
            }
            if (string.IsNullOrEmpty(profiles)) profiles = sysDrive + @"\Users";
            s.Exact.Add(profiles);
            s.Exact.Add(string.IsNullOrEmpty(publicProfile) ? Path.Combine(profiles, "Public") : publicProfile);
            s.Trees.Add(Path.Combine(publicProfile ?? Path.Combine(profiles, "Public"), "AppData"));
            s.Trees.Add(string.IsNullOrEmpty(defaultProfile) ? Path.Combine(profiles, "Default") : defaultProfile);
            s.Trees.Add(Path.Combine(profiles, Paths.AccountName));
            // Folders under the profiles folder with no profile entry (old,
            // half-deleted ones) are still someone's.
            if (Directory.Exists(profiles))
                foreach (string d in Directory.GetDirectories(profiles)) if (!s.Exact.Contains(d)) s.Exact.Add(d);
            return s;
        }

        public static void CheckMediaRoot(string root)
        {
            string why = MediaPaths.Problem(root);
            if (why != null) throw new ArgumentException("media folder: " + why);
            string p = MediaPaths.Normalize(root);
            var drive = new DriveInfo(p.Substring(0, 1));
            if (!drive.IsReady) throw new ArgumentException("media folder: drive " + drive.Name + " is not ready");
            if (drive.DriveType != DriveType.Fixed && drive.DriveType != DriveType.Removable)
                throw new ArgumentException("media folder: pick a folder on a local drive (" + drive.Name + " is " + drive.DriveType + ")");
            why = MediaPaths.SystemProblem(p, SystemFoldersHere());
            if (why != null) throw new ArgumentException("media folder: " + why);
            // The folder, or the nearest part of its path that exists, must
            // be a real folder, not a link to somewhere else.
            for (string d = p; d != null && d.Length > 3; d = Path.GetDirectoryName(d))
            {
                if (!Directory.Exists(d)) continue;
                if (IsReparse(d)) throw new ArgumentException("media folder: " + d + " is a link (junction or symbolic link); pick the real folder");
            }
        }

        // One media folder: made if missing, domovoi-svc (and, on a root
        // Setup made, Users) allowed Modify. The install record notes each
        // change before it happens (save).
        public static void EnsureMediaFolder(string path, SecurityIdentifier svc, bool isRoot, Ledger ledger, Action save, Action<string> say)
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
                rec.Created = true;
                save();
                Directory.CreateDirectory(path);
                say("created " + path);
            }
            RefuseReparse(path);
            if (!rec.AddedSids.Contains(svc.Value)) { rec.AddedSids.Add(svc.Value); save(); }
            DirectorySecurity s = Directory.GetAccessControl(path, AccessControlSections.Access);
            if (rec.Created && isRoot)
                s.AddAccessRule(new FileSystemAccessRule(UsersSid, FileSystemRights.Modify, Inherit, PropagationFlags.None, AccessControlType.Allow));
            s.AddAccessRule(new FileSystemAccessRule(svc, FileSystemRights.Modify, Inherit, PropagationFlags.None, AccessControlType.Allow));
            Directory.SetAccessControl(path, s);
            say("allowed " + Paths.AccountName + " to read and write " + path + (rec.Created && isRoot ? " (and the household, Users)" : ""));
        }

        // Setup's rules off a media folder; a link is skipped (its target is
        // not Setup's to change).
        public static string RemoveAccess(string path, IEnumerable<string> sids, Action<string> say)
        {
            if (!Directory.Exists(path)) return null;
            if (IsReparse(path)) { say("left " + path + " alone (it is now a link, not the folder Setup changed)"); return null; }
            DirectorySecurity s = Directory.GetAccessControl(path, AccessControlSections.Access);
            foreach (string sid in sids) s.PurgeAccessRules(new SecurityIdentifier(sid));
            Directory.SetAccessControl(path, s);
            say("removed Setup's access rule from " + path);
            return null;
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
