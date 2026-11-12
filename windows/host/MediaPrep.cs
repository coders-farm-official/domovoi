// The Windows half of moving the media folders from the dashboard
// (common\MediaFolders.cs has the rules and the request): the new root
// checked on this PC, its six folders made where missing, each proved
// writable by this account, and the tray's list of folders written where
// the household's accounts can read it.
//
// This runs as domovoi-svc, a standard account. It can only make folders
// where that account may, and it changes access rules only on folders it
// has just made (it owns them): Domovoi's account and, on a root it made,
// the household (Users) may read and write, as Setup's media step does
// (helper\Folders.cs EnsureMediaFolder). An existing folder keeps its own
// rules; one this account can't write is refused, never "fixed".

using System;
using System.Collections.Generic;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;

namespace Domovoi.Windows.Host
{
    static class MediaPrep
    {
        const InheritanceFlags Inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
        static readonly SecurityIdentifier UsersSid = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);

        // Why the dashboard's stored root can't be used any more (a link
        // appeared, a system folder by its long name), or null.
        public static string StoredProblem(string root)
        {
            return MediaFolders.StoredProblem(root, MediaFolders.SystemFoldersHere(), MediaFolders.Canonical, MediaFolders.LinkIn);
        }

        // Null when root and its six folders are ready for Domovoi, else
        // why not in plain words. say: for the host's log.
        public static string Prepare(string root, Action<string> say)
        {
            string p = MediaPaths.Normalize(root);
            SystemFolders sys = MediaFolders.SystemFoldersHere();
            string why = MediaFolders.RootProblem(p, MediaFolders.Canonical(p), sys, MediaFolders.DriveOf(p), MediaFolders.LinkIn(p));
            if (why != null) return why;
            SecurityIdentifier me = WindowsIdentity.GetCurrent().User;
            try
            {
                Make(p, me, true, say);
                foreach (var m in MediaPaths.Under(p)) Make(m.Value, me, false, say);
            }
            catch (UnauthorizedAccessException) { return "Domovoi's account may not make folders in " + Path.GetDirectoryName(p) + "; make " + p + " yourself, or pick another folder"; }
            catch (IOException e) { return "the folders could not be made: " + e.Message; }
            // Made between the check and now: never use a link, and the
            // folder's final name is checked again.
            why = MediaFolders.RootProblem(p, MediaFolders.Canonical(p), sys, MediaFolders.DriveOf(p), null);
            if (why != null) return why;
            string link = MediaFolders.LinkIn(p);
            if (link != null) return link + " is a link (junction or symbolic link); pick the real folder";
            foreach (var m in MediaPaths.Under(p))
            {
                link = MediaFolders.LinkIn(m.Value);
                if (link != null) return link + " is a link (junction or symbolic link); pick the real folder";
                why = Writable(m.Value);
                if (why != null) return why;
            }
            return null;
        }

        // Before provision.sh mounts a dashboard root: a kind folder added
        // since the root was chosen (Podcasts, 2026-11-11) is made in it,
        // as a move would have. Only in a root that is there and still
        // passes StoredProblem (checked by the caller); never the root
        // itself (an unplugged drive is left alone). Best effort: what
        // can't be made is logged, and provision.sh then keeps that kind
        // where it was (its media step tolerates a new podcasts line that
        // doesn't mount).
        public static void EnsureUnder(string root, Action<string> say)
        {
            string p = MediaPaths.Normalize(root);
            if (!Directory.Exists(p) || MediaFolders.LinkIn(p) != null) return;
            SecurityIdentifier me = WindowsIdentity.GetCurrent().User;
            foreach (var m in MediaPaths.Under(p))
            {
                try { Make(m.Value, me, false, say); }
                catch (UnauthorizedAccessException e) { say("could not make " + m.Value + ": " + e.Message); }
                catch (IOException e) { say("could not make " + m.Value + ": " + e.Message); }
            }
        }

        // The folder, made if missing (true when this made it, and then its
        // access rules set).
        static bool Make(string path, SecurityIdentifier me, bool isRoot, Action<string> say)
        {
            if (Directory.Exists(path)) return false;
            Directory.CreateDirectory(path);
            say("made " + path + " for the media folders");
            try
            {
                DirectorySecurity s = Directory.GetAccessControl(path, AccessControlSections.Access);
                s.AddAccessRule(new FileSystemAccessRule(me, FileSystemRights.Modify, Inherit, PropagationFlags.None, AccessControlType.Allow));
                if (isRoot) s.AddAccessRule(new FileSystemAccessRule(UsersSid, FileSystemRights.Modify, Inherit, PropagationFlags.None, AccessControlType.Allow));
                Directory.SetAccessControl(path, s);
            }
            catch (UnauthorizedAccessException e) { say("could not set the access rules on " + path + ": " + e.Message); }
            catch (InvalidOperationException e) { say("could not set the access rules on " + path + ": " + e.Message); }
            return true;
        }

        // Null when this account can make and delete a file in dir.
        static string Writable(string dir)
        {
            string f = Path.Combine(dir, ".domovoi-write-check-" + Guid.NewGuid().ToString("N"));
            try
            {
                using (new FileStream(f, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1, FileOptions.DeleteOnClose)) { }
                return null;
            }
            catch (UnauthorizedAccessException) { }
            catch (IOException) { }
            return "Domovoi's account can't write to " + dir + ". Pick a new folder (Domovoi makes it), or run Domovoi Setup again and choose this folder there (Setup gives Domovoi access)";
        }

        // The tray's list, readable by the household's accounts (Users):
        // the tray runs as the signed-in person. This account made the file,
        // so it may set its rules.
        public static void WriteTrayMap(string text)
        {
            WriteForUsers(MediaFolders.TrayMapPath, text);
        }

        // A file in host\out the tray reads (it runs as the signed-in
        // person): the media folders, the network name.
        public static void WriteForUsers(string path, string text)
        {
            AtomicFile.WriteAllText(path, text);
            FileSecurity s = File.GetAccessControl(path, AccessControlSections.Access);
            s.AddAccessRule(new FileSystemAccessRule(UsersSid, FileSystemRights.ReadAndExecute, AccessControlType.Allow));
            File.SetAccessControl(path, s);
        }
    }
}
