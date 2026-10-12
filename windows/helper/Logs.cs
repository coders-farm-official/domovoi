// copy-logs: Domovoi's logs, which only administrators can open (they are
// in C:\ProgramData\Domovoi), copied to a new folder the household can
// open, for Setup's and the uninstaller's "Copy logs" buttons. Setup passes
// Documents\Domovoi logs of the account running it.
//
// What it copies: logs\host.log and logs\provision.log (with their .1
// rotations), state\install.log, host\out\status.json, and the uninstall's
// own log from the temp folder when the uninstaller names it. Every line
// goes through Redact.Line on the way: passwords, tokens and setup codes are
// blanked as far as the helper can recognise them.
//
// The helper runs elevated, the destination is a folder the household can
// write and the sources are in folders domovoi-svc can write, so nothing is
// done through a path that could be swapped between a check and its use
// (the review of 2026-10-12 swapped the new folder for a junction with an
// oplock and had README.txt overwrite another file):
// * "Domovoi logs" is opened with FILE_FLAG_OPEN_REPARSE_POINT and without
//   delete sharing, checked ON THAT HANDLE (a directory, not a reparse
//   point, the path asked for) and held open: while it is, nobody can
//   rename, delete or replace it;
// * the new folder and every file are created RELATIVE TO THE HELD HANDLE
//   (NtCreateFile with RootDirectory), FILE_CREATE (never an existing
//   name, never through a link), and the folder is held the same way;
// * each source is opened with FILE_FLAG_OPEN_REPARSE_POINT and checked on
//   its handle: a plain file, one link, at the path asked for.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;

namespace Domovoi.Windows.Helper
{
    static class Logs
    {
        static readonly Regex UninstallLogName = new Regex(@"^domovoi-uninstall-\d{8}-\d{6}\.log\z");

        // -- native -----------------------------------------------------

        const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000, SYNCHRONIZE = 0x00100000;
        const uint FILE_LIST_DIRECTORY = 0x1, FILE_ADD_FILE = 0x2, FILE_ADD_SUBDIRECTORY = 0x4, FILE_TRAVERSE = 0x20, FILE_READ_ATTRIBUTES = 0x80;
        const uint SHARE_READ = 1, SHARE_WRITE = 2, SHARE_DELETE = 4;
        const uint OPEN_EXISTING = 3;
        const uint FLAG_BACKUP_SEMANTICS = 0x02000000, FLAG_OPEN_REPARSE_POINT = 0x00200000;
        const uint ATTR_DIRECTORY = 0x10, ATTR_REPARSE_POINT = 0x400, ATTR_NORMAL = 0x80;
        const uint NT_FILE_CREATE = 2;
        const uint NT_DIRECTORY_FILE = 0x1, NT_SYNCHRONOUS_IO_NONALERT = 0x20, NT_NON_DIRECTORY_FILE = 0x40, NT_OPEN_REPARSE_POINT = 0x00200000;
        const uint OBJ_CASE_INSENSITIVE = 0x40;
        const int STATUS_OBJECT_NAME_COLLISION = unchecked((int)0xC0000035);

        [StructLayout(LayoutKind.Sequential)]
        struct BY_HANDLE_FILE_INFORMATION
        {
            public uint FileAttributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime, LastAccessTime, LastWriteTime;
            public uint VolumeSerialNumber, FileSizeHigh, FileSizeLow, NumberOfLinks, FileIndexHigh, FileIndexLow;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }

        [StructLayout(LayoutKind.Sequential)]
        struct OBJECT_ATTRIBUTES
        {
            public int Length;
            public IntPtr RootDirectory;
            public IntPtr ObjectName;
            public uint Attributes;
            public IntPtr SecurityDescriptor;
            public IntPtr SecurityQualityOfService;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct IO_STATUS_BLOCK { public IntPtr Status; public IntPtr Information; }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool GetFileInformationByHandle(SafeFileHandle h, out BY_HANDLE_FILE_INFORMATION info);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFinalPathNameByHandleW(SafeFileHandle h, StringBuilder path, uint size, uint flags);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetLongPathNameW(string shortPath, StringBuilder longPath, uint size);

        [DllImport("ntdll.dll")]
        static extern int NtCreateFile(out SafeFileHandle handle, uint access, ref OBJECT_ATTRIBUTES attrs, out IO_STATUS_BLOCK io,
            IntPtr allocationSize, uint fileAttributes, uint share, uint disposition, uint options, IntPtr ea, uint eaLength);

        [DllImport("ntdll.dll")]
        static extern int RtlNtStatusToDosError(int status);

        static BY_HANDLE_FILE_INFORMATION Info(SafeFileHandle h)
        {
            BY_HANDLE_FILE_INFORMATION i;
            if (!GetFileInformationByHandle(h, out i)) throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFileInformationByHandle");
            return i;
        }

        // The path the handle really is, without \\?\.
        static string FinalPath(SafeFileHandle h)
        {
            var sb = new StringBuilder(1024);
            uint n = GetFinalPathNameByHandleW(h, sb, (uint)sb.Capacity, 0);
            if (n == 0 || n >= sb.Capacity) throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFinalPathNameByHandle");
            string p = sb.ToString();
            if (p.StartsWith(@"\\?\UNC\", StringComparison.Ordinal)) return @"\\" + p.Substring(8);
            if (p.StartsWith(@"\\?\", StringComparison.Ordinal)) return p.Substring(4);
            return p;
        }

        // A path in the form FinalPath returns (long names, no trailing \).
        static string LongPath(string path)
        {
            string full = Path.GetFullPath(path);
            var sb = new StringBuilder(1024);
            uint n = GetLongPathNameW(full, sb, (uint)sb.Capacity);
            string p = n > 0 && n < sb.Capacity ? sb.ToString() : full;
            return p.Length > 3 ? p.TrimEnd('\\') : p;
        }

        static bool SamePath(string a, string b) { return string.Equals(a, b, StringComparison.OrdinalIgnoreCase); }

        // Opens an existing folder by path, the link itself if it is one,
        // without delete sharing, and checks the handle: a folder, not a
        // link, the path asked for. Hold the handle while using the folder.
        public static SafeFileHandle OpenDir(string path)
        {
            SafeFileHandle h = CreateFileW(path, FILE_LIST_DIRECTORY | FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
                SHARE_READ | SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING, FLAG_BACKUP_SEMANTICS | FLAG_OPEN_REPARSE_POINT, IntPtr.Zero);
            if (h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "can't open " + path);
            try
            {
                CheckDir(h, path);
                return h;
            }
            catch { h.Dispose(); throw; }
        }

        static void CheckDir(SafeFileHandle h, string path)
        {
            var i = Info(h);
            if ((i.FileAttributes & ATTR_REPARSE_POINT) != 0) throw new InvalidOperationException(path + " is a link; not copying anything through it");
            if ((i.FileAttributes & ATTR_DIRECTORY) == 0) throw new InvalidOperationException(path + " is not a folder");
            string real = FinalPath(h);
            if (!SamePath(real, LongPath(path))) throw new InvalidOperationException(path + " leads to " + real + "; not copying anything there");
        }

        // NtCreateFile of NAME inside the folder DIR is open on: FILE_CREATE
        // (never an existing name), the link itself if one appears there.
        // Null when the name is taken.
        static SafeFileHandle CreateIn(SafeFileHandle dir, string name, bool directory)
        {
            if (name.IndexOfAny(new[] { '\\', '/', ':' }) >= 0 || name == "." || name == "..") throw new ArgumentException("not a plain name: " + name);
            IntPtr buf = Marshal.StringToHGlobalUni(name);
            IntPtr us = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(UNICODE_STRING)));
            bool added = false;
            try
            {
                dir.DangerousAddRef(ref added);
                var u = new UNICODE_STRING();
                u.Length = (ushort)(name.Length * 2);
                u.MaximumLength = (ushort)(name.Length * 2 + 2);
                u.Buffer = buf;
                Marshal.StructureToPtr(u, us, false);
                var oa = new OBJECT_ATTRIBUTES();
                oa.Length = Marshal.SizeOf(typeof(OBJECT_ATTRIBUTES));
                oa.RootDirectory = dir.DangerousGetHandle();
                oa.ObjectName = us;
                oa.Attributes = OBJ_CASE_INSENSITIVE;
                IO_STATUS_BLOCK io;
                SafeFileHandle h;
                uint access = directory
                    ? FILE_LIST_DIRECTORY | FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE
                    : GENERIC_WRITE | FILE_READ_ATTRIBUTES | SYNCHRONIZE;
                uint options = NT_OPEN_REPARSE_POINT | NT_SYNCHRONOUS_IO_NONALERT | (directory ? NT_DIRECTORY_FILE : NT_NON_DIRECTORY_FILE);
                int st = NtCreateFile(out h, access, ref oa, out io, IntPtr.Zero, directory ? 0 : ATTR_NORMAL, SHARE_READ | SHARE_WRITE,
                    NT_FILE_CREATE, options, IntPtr.Zero, 0);
                if (st == STATUS_OBJECT_NAME_COLLISION) { h.Dispose(); return null; }
                if (st < 0) { h.Dispose(); throw new Win32Exception(RtlNtStatusToDosError(st), "can't create " + name); }
                return h;
            }
            finally
            {
                if (added) dir.DangerousRelease();
                Marshal.FreeHGlobal(us);
                Marshal.FreeHGlobal(buf);
            }
        }

        // Opens a source by path, the link itself if it is one, and checks
        // the handle: a plain file with one name, at the path asked for.
        public static SafeFileHandle OpenSource(string path)
        {
            SafeFileHandle h = CreateFileW(path, GENERIC_READ | FILE_READ_ATTRIBUTES, SHARE_READ | SHARE_WRITE | SHARE_DELETE, IntPtr.Zero,
                OPEN_EXISTING, FLAG_OPEN_REPARSE_POINT, IntPtr.Zero);
            if (h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "can't open " + path);
            try
            {
                var i = Info(h);
                if ((i.FileAttributes & (ATTR_REPARSE_POINT | ATTR_DIRECTORY)) != 0) throw new InvalidOperationException(path + " is a link or a folder");
                if (i.NumberOfLinks != 1) throw new InvalidOperationException(path + " has " + Inv.S((int)i.NumberOfLinks) + " names (hard links)");
                string real = FinalPath(h);
                if (!SamePath(real, LongPath(path))) throw new InvalidOperationException(path + " leads to " + real);
                return h;
            }
            catch { h.Dispose(); throw; }
        }

        // -- the copy ---------------------------------------------------

        // The files to copy, as (source, name in the copy).
        public static List<KeyValuePair<string, string>> Sources(string uninstallLog)
        {
            var list = new List<KeyValuePair<string, string>>();
            foreach (string name in new[] { "host.log", "host.log.1", "provision.log", "provision.log.1" })
                list.Add(new KeyValuePair<string, string>(Path.Combine(Paths.Logs, name), name));
            list.Add(new KeyValuePair<string, string>(Paths.InstallLog, "install.log"));
            list.Add(new KeyValuePair<string, string>(Paths.InstallLog + ".1", "install.log.1"));
            list.Add(new KeyValuePair<string, string>(Paths.Status, "host-status.json"));
            if (!string.IsNullOrEmpty(uninstallLog))
            {
                // Only the uninstall's own record, in this account's temp folder.
                string full = Path.GetFullPath(uninstallLog);
                string dir = Path.GetDirectoryName(full);
                if (!UninstallLogName.IsMatch(Path.GetFileName(full))
                    || !string.Equals(dir.TrimEnd('\\'), Path.GetTempPath().TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("--uninstall-log must be a domovoi-uninstall-*.log in " + Path.GetTempPath());
                list.Add(new KeyValuePair<string, string>(full, Path.GetFileName(full)));
            }
            return list;
        }

        public static void Copy(Output o, string destRoot, string kind, string uninstallLog)
        {
            Copy(o, destRoot, kind, uninstallLog, DateTime.Now);
        }

        // now: the time the folder is named after (the tests pass one).
        public static void Copy(Output o, string destRoot, string kind, string uninstallLog, DateTime now)
        {
            if (kind != "install" && kind != "uninstall") throw new ArgumentException("--kind must be install or uninstall");
            string root = MediaPaths.Normalize(destRoot);
            string why = MediaPaths.Problem(root);
            if (why != null) throw new ArgumentException("--dest: " + why);
            var sources = Sources(uninstallLog);

            Directory.CreateDirectory(root);
            using (SafeFileHandle rootH = OpenDir(root))
            {
                string stamp = now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
                string name = null;
                SafeFileHandle destH = null;
                for (int n = 1; n < 100 && destH == null; n++)
                {
                    name = kind + "-" + stamp + (n > 1 ? "-" + Inv.S(n) : "");
                    destH = CreateIn(rootH, name, true);
                }
                if (destH == null) throw new InvalidOperationException("no free folder name under " + root);
                using (destH)
                {
                    string dest = Path.Combine(root, name);
                    var di = Info(destH);
                    if ((di.FileAttributes & ATTR_REPARSE_POINT) != 0 || (di.FileAttributes & ATTR_DIRECTORY) == 0)
                        throw new InvalidOperationException(dest + " is not the folder Setup made");
                    int copied = 0;
                    foreach (var src in sources)
                    {
                        if (!File.Exists(src.Key)) continue;
                        SafeFileHandle sh;
                        try { sh = OpenSource(src.Key); }
                        catch (InvalidOperationException e) { o.Say("skipped " + src.Key + ": " + e.Message); continue; }
                        using (sh)
                        using (SafeFileHandle wh = CreateIn(destH, src.Value, false))
                        {
                            if (wh == null) throw new InvalidOperationException(Path.Combine(dest, src.Value) + " appeared before the copy; not writing it");
                            CopyRedacted(sh, wh);
                        }
                        copied++;
                    }
                    using (SafeFileHandle rh = CreateIn(destH, "README.txt", false))
                    {
                        if (rh == null) throw new InvalidOperationException(Path.Combine(dest, "README.txt") + " appeared before the copy; not writing it");
                        byte[] text = new UTF8Encoding(false).GetBytes(
                            "Domovoi's logs, copied by " + (kind == "install" ? "Domovoi Setup" : "Domovoi's uninstaller") + " on "
                            + now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + ".\r\n"
                            + "Passwords, tokens and setup codes are blanked as far as Setup can recognise them. "
                            + "docs/WINDOWS_HOST.md, \"When something goes wrong\", says what each file is.\r\n");
                        using (var w = new FileStream(rh, FileAccess.Write)) w.Write(text, 0, text.Length);
                    }
                    o.Say("copied " + Inv.S(copied) + " log file(s) to " + dest);
                    o.Kv("folder", dest);
                    o.Kv("copied", Inv.S(copied));
                }
            }
        }

        static void CopyRedacted(SafeFileHandle src, SafeFileHandle dest)
        {
            using (var r = new StreamReader(new FileStream(src, FileAccess.Read), Encoding.UTF8, true))
            using (var w = new StreamWriter(new FileStream(dest, FileAccess.Write), new UTF8Encoding(false)))
            {
                string line;
                while ((line = r.ReadLine()) != null)
                {
                    w.Write(Redact.Line(line));
                    w.Write("\r\n");
                }
            }
        }
    }
}
