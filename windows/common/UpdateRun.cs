// One run of the Windows update's Windows step, on disk (design note
// WINDOWS-ONE-UPDATE-2026-10-22):
//
//   %LOCALAPPDATA%\Domovoi\updates\<run>\   this account, Administrators and SYSTEM only
//       DomovoiSetup.exe, its .sig, the manifest and its .sig   (the tray's download)
//       setup.log                                                 (Setup's /LOG)
//       result.txt                                                (Setup's /updateresult, or the helper's --result)
//   %LOCALAPPDATA%\Domovoi\updates\pending.txt                    the run a tray started, for the next tray
//
// HeldFile is the TOCTOU guard: the Setup is read for the check through a
// handle that shares read only (nobody, this account included, can write,
// rename or delete the file while it is open) and that handle stays open
// until the elevated process exists, so Windows runs the bytes that were
// checked.

using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class BuildVersion
    {
        // This exe's version (AssemblyInfo.cs, held to pyproject.toml by
        // domovoi/tests/test_version_copies.py): the Windows side's.
        public static string Version
        {
            get
            {
                var a = (AssemblyInformationalVersionAttribute)Attribute.GetCustomAttribute(typeof(BuildVersion).Assembly, typeof(AssemblyInformationalVersionAttribute));
                return a != null ? a.InformationalVersion : null;
            }
        }

        // The commit this build was made from (BuildInfo.g.cs, written by
        // build.ps1), or null for a build that didn't say.
        public static string Commit
        {
            get
            {
                Type t = typeof(BuildVersion).Assembly.GetType("Domovoi.Windows.BuildInfo");
                FieldInfo f = t == null ? null : t.GetField("Commit", BindingFlags.Public | BindingFlags.Static);
                string c = f == null ? null : f.GetValue(null) as string;
                return c != null && Regex.IsMatch(c, "^[0-9a-f]{40}\\z") ? c : null;
            }
        }
    }

    public static class UpdateRun
    {
        public const string ResultFile = "result.txt";
        public const string LogFile = "setup.log";
        public const string PendingFile = "pending.txt";

        public static string Root(string localAppData)
        {
            return Path.Combine(localAppData, "Domovoi", "updates");
        }

        static readonly Regex RunName = new Regex("^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\\z");

        public static bool IsRunName(string n) { return n != null && RunName.IsMatch(n); }

        // A new, empty run folder that only this account, Administrators and
        // SYSTEM can open (inheritance off), made before anything is put in it.
        public static string NewRunDir(string root, DateTime utcNow)
        {
            Directory.CreateDirectory(root);
            string name = utcNow.ToString("yyyyMMdd-HHmmss", System.Globalization.CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N").Substring(0, 8);
            string dir = Path.Combine(root, name);
            var sec = new DirectorySecurity();
            sec.SetAccessRuleProtection(true, false);
            const InheritanceFlags inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
            using (WindowsIdentity me = WindowsIdentity.GetCurrent())
                sec.AddAccessRule(new FileSystemAccessRule(me.User, FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            sec.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            sec.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            if (Directory.Exists(dir)) throw new IOException("the run folder exists already: " + dir);
            Directory.CreateDirectory(dir, sec);
            return dir;
        }

        // Whether dir's ACL is the one NewRunDir sets: inheritance off, and
        // nobody but this account, Administrators and SYSTEM allowed in.
        public static bool IsPrivate(string dir)
        {
            DirectorySecurity sec = Directory.GetAccessControl(dir);
            if (!sec.AreAccessRulesProtected) return false;
            var allowed = new HashSet<string>();
            using (WindowsIdentity me = WindowsIdentity.GetCurrent()) allowed.Add(me.User.Value);
            allowed.Add(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null).Value);
            allowed.Add(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null).Value);
            foreach (FileSystemAccessRule r in sec.GetAccessRules(true, true, typeof(SecurityIdentifier)))
                if (r.AccessControlType == AccessControlType.Allow && !allowed.Contains(r.IdentityReference.Value)) return false;
            return true;
        }

        // Run folders older than two weeks go (their Setup is no use once a
        // newer one ran, and their logs are copies), except keep.
        public static void Prune(string root, DateTime utcNow, string keep)
        {
            string[] dirs;
            try { dirs = Directory.GetDirectories(root); }
            catch (IOException) { return; }
            catch (UnauthorizedAccessException) { return; }
            foreach (string d in dirs)
            {
                string name = Path.GetFileName(d);
                if (!IsRunName(name) || name == keep) continue;
                DateTime made;
                if (!DateTime.TryParseExact(name.Substring(0, 15), "yyyyMMdd-HHmmss", System.Globalization.CultureInfo.InvariantCulture,
                        System.Globalization.DateTimeStyles.AssumeUniversal | System.Globalization.DateTimeStyles.AdjustToUniversal, out made)) continue;
                if (utcNow - made < TimeSpan.FromDays(14)) continue;
                if ((File.GetAttributes(d) & FileAttributes.ReparsePoint) != 0) continue;
                try { Directory.Delete(d, true); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }

        // The arguments a Windows step starts DomovoiSetup.exe with: a silent
        // repair (DomovoiSetup.iss, update mode) that shows nothing, asks
        // nothing, can't be cancelled, never restarts the PC and never
        // uninstalls. linuxFirst: the Linux part runs first, inside the
        // elevated Setup, before anything on Windows changes (the tray's own
        // "Update Domovoi").
        public static string SetupArguments(string runDir, bool linuxFirst)
        {
            if (runDir.IndexOf('"') >= 0) throw new ArgumentException("bad folder");
            string d = runDir.TrimEnd('\\');
            return "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /SP- /update=1"
                + (linuxFirst ? " /linuxfirst=1" : "")
                + " \"/LOG=" + d + "\\" + LogFile + "\""
                + " \"/updateresult=" + d + "\\" + ResultFile + "\"";
        }

        // The helper's Linux part on its own (nothing newer on Windows).
        public static string LinuxArguments(string runDir)
        {
            if (runDir.IndexOf('"') >= 0) throw new ArgumentException("bad folder");
            return "linux-update --wait --result \"" + runDir.TrimEnd('\\') + "\\" + ResultFile + "\"";
        }
    }

    // The outcome Setup's update mode or the helper's linux-update writes:
    // key=value lines (ok=1|0, error=..., log=..., linux=updated|nothing|...).
    public sealed class RunResult
    {
        public bool Ok;
        public string Error, Log, Linux, Version;

        public static RunResult Read(string path)
        {
            if (!File.Exists(path)) return null;
            string text;
            using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                if (fs.Length > 64 * 1024) return null;
                var b = new byte[fs.Length];
                int got = 0;
                while (got < b.Length) { int n = fs.Read(b, got, b.Length - got); if (n <= 0) break; got += n; }
                text = Encoding.UTF8.GetString(b, 0, got);
            }
            return Parse(text);
        }

        public static RunResult Parse(string text)
        {
            var r = new RunResult();
            bool any = false;
            foreach (string raw in (text ?? "").Replace("\r", "").Split('\n'))
            {
                int i = raw.IndexOf('=');
                if (i <= 0) continue;
                string k = raw.Substring(0, i).Trim(), v = raw.Substring(i + 1).Trim();
                if (v.Length > 500) v = v.Substring(0, 500);
                any = true;
                if (k == "ok") r.Ok = v == "1";
                else if (k == "error") r.Error = v.Length > 0 ? v : null;
                else if (k == "log") r.Log = v.Length > 0 ? v : null;
                else if (k == "linux") r.Linux = v.Length > 0 ? v : null;
                else if (k == "version") r.Version = v.Length > 0 ? v : null;
            }
            return any ? r : null;
        }

        public string Format()
        {
            var sb = new StringBuilder();
            sb.Append("ok=").Append(Ok ? "1" : "0").Append("\r\n");
            if (Error != null) sb.Append("error=").Append(OneLine(Error)).Append("\r\n");
            if (Log != null) sb.Append("log=").Append(OneLine(Log)).Append("\r\n");
            if (Linux != null) sb.Append("linux=").Append(OneLine(Linux)).Append("\r\n");
            if (Version != null) sb.Append("version=").Append(OneLine(Version)).Append("\r\n");
            return sb.ToString();
        }

        static string OneLine(string s) { return s.Replace("\r", " ").Replace("\n", " "); }
    }

    // A file opened so it can't change while it's held: read sharing only.
    public sealed class HeldFile : IDisposable
    {
        readonly FileStream fs;
        public readonly string Path;

        public HeldFile(string path)
        {
            Path = path;
            fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        }

        public byte[] ReadAll(long max)
        {
            if (fs.Length > max) throw new IOException("the file is larger than " + Inv.S(max) + " bytes");
            fs.Position = 0;
            var b = new byte[fs.Length];
            int got = 0;
            while (got < b.Length) { int n = fs.Read(b, got, b.Length - got); if (n <= 0) break; got += n; }
            if (got != b.Length) throw new IOException("the file changed size while it was read");
            return b;
        }

        public void Dispose() { fs.Dispose(); }
    }
}
