// copy-logs: Domovoi's logs, which only administrators can open (they are
// in C:\ProgramData\Domovoi), copied to a new folder the household can
// open, for Setup's and the uninstaller's "Copy logs" buttons. Setup passes
// Documents\Domovoi logs of the account running it.
//
// What it copies: logs\host.log and logs\provision.log (with their .1
// rotations), state\install.log, host\out\status.json, and the uninstall's
// own log from the temp folder when the uninstaller names it, and
// logs\core.log, the core's journal DomovoiHost copies out (common\
// CoreLog.cs; Setup's and the tray's copies ask it for a fresh one first,
// and the copy says when it has only an older one or none). Every line
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
using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
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
            // A fresh core.log from DomovoiHost for Setup's and the tray's
            // copies; the uninstaller's copy takes the last one (the host is
            // stopping or unregistering then, and wouldn't answer).
            Func<string> refresh = null;
            if (kind == "install") refresh = delegate { return AskForCoreLog(CoreLog.HelperWaitSec); };
            Copy(o, destRoot, kind, Sources(uninstallLog), Paths.CoreLogFile, refresh, now);
        }

        // The copy itself, with its inputs passed in (the tests use folders
        // of their own): sources as Sources() lists them, coreLog the
        // core.log to copy, refreshCoreLog null (don't ask) or what asks
        // DomovoiHost for a fresh one (null when it did, else why not).
        public static void Copy(Output o, string destRoot, string kind, List<KeyValuePair<string, string>> sources,
            string coreLog, Func<string> refreshCoreLog, DateTime now)
        {
            if (kind != "install" && kind != "uninstall") throw new ArgumentException("--kind must be install or uninstall");
            string root = MediaPaths.Normalize(destRoot);
            string why = MediaPaths.Problem(root);
            if (why != null) throw new ArgumentException("--dest: " + why);

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
                    string core = CopyCoreLog(o, destH, dest, coreLog, refreshCoreLog);
                    if (core != "none") copied++;
                    o.Kv("core_log", core);
                    using (SafeFileHandle rh = CreateIn(destH, "README.txt", false))
                    {
                        if (rh == null) throw new InvalidOperationException(Path.Combine(dest, "README.txt") + " appeared before the copy; not writing it");
                        byte[] text = new UTF8Encoding(false).GetBytes(
                            "Domovoi's logs, copied by " + (kind == "install" ? "Domovoi Setup" : "Domovoi's uninstaller") + " on "
                            + now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + ".\r\n"
                            + "core.log is the journal of Domovoi's core and dashboard (domovoi-core, domovoi-web) for the last 24 hours. "
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

        // -- the core's log (common\CoreLog.cs) --------------------------

        // core.log into the copy, with the same checks as the other files,
        // read up to CoreLog.MaxCopyBytes, every line redacted. Never fails
        // the copy for the core log's sake: when there is none to copy, or
        // only an older one, the copy's core.log says so and why. Returns
        // fresh, last (DomovoiHost's last periodic copy) or none.
        static string CopyCoreLog(Output o, SafeFileHandle destH, string dest, string path, Func<string> refresh)
        {
            // fresh: null when DomovoiHost wrote a fresh copy just now (or
            // wasn't asked), else why it didn't.
            string fresh = refresh == null ? null : refresh();
            SafeFileHandle sh = null;
            string openWhy = null;
            if (!File.Exists(path))
                openWhy = "DomovoiHost has not written " + path + " yet (it does once Domovoi is ready; a DomovoiHost from before this never does)";
            else
            {
                try { sh = OpenSource(path); }
                catch (InvalidOperationException e) { openWhy = "not copying it: " + e.Message; }
                catch (Win32Exception e) { openWhy = e.Message; }
                catch (IOException e) { openWhy = e.Message; }
                catch (UnauthorizedAccessException e) { openWhy = e.Message; }
            }
            string note = null;
            byte[] body = null;
            bool cut = false;
            using (sh)
            {
                if (sh != null)
                {
                    try
                    {
                        var ft = Info(sh).LastWriteTime;
                        DateTime written = DateTime.FromFileTime(((long)ft.dwHighDateTime << 32) | (uint)ft.dwLowDateTime);
                        if (refresh == null) note = CoreLog.LastCopyNote(written);
                        else if (fresh != null) note = CoreLog.StaleNote(fresh, written);
                        body = ReadBounded(sh, CoreLog.MaxCopyBytes, out cut);
                    }
                    catch (IOException e) { body = null; openWhy = e.Message; }
                    catch (Win32Exception e) { body = null; openWhy = e.Message; }
                }
            }
            if (body == null)
                note = CoreLog.Note(openWhy + (fresh != null ? "; asking DomovoiHost for a fresh one: " + fresh : ""));
            using (SafeFileHandle wh = CreateIn(destH, CoreLog.FileName, false))
            {
                if (wh == null) throw new InvalidOperationException(Path.Combine(dest, CoreLog.FileName) + " appeared before the copy; not writing it");
                using (var w = new StreamWriter(new FileStream(wh, FileAccess.Write), new UTF8Encoding(false)))
                {
                    if (note != null) { w.Write(Redact.Line(note)); w.Write("\r\n"); }
                    if (body != null)
                    {
                        using (var r = new StreamReader(new MemoryStream(body), Encoding.UTF8, true))
                        {
                            string line;
                            while ((line = r.ReadLine()) != null)
                            {
                                w.Write(Redact.Line(line));
                                w.Write("\r\n");
                            }
                        }
                        if (cut) w.Write("NOTE: core.log is larger than " + Inv.S(CoreLog.MaxCopyBytes / (1024 * 1024)) + " MB; the rest is left out.\r\n");
                    }
                }
            }
            string result = body == null ? "none" : refresh != null && fresh == null ? "fresh" : "last";
            o.Say(result == "fresh" ? "copied a fresh copy of the core's log" : Redact.Line(note));
            return result;
        }

        // At most max bytes of the file; cut: there was more.
        static byte[] ReadBounded(SafeFileHandle h, long max, out bool cut)
        {
            using (var fs = new FileStream(h, FileAccess.Read))
            using (var ms = new MemoryStream())
            {
                var buf = new byte[64 * 1024];
                long left = max + 1;
                int n;
                while (left > 0 && (n = fs.Read(buf, 0, (int)Math.Min(buf.Length, left))) > 0)
                {
                    ms.Write(buf, 0, n);
                    left -= n;
                }
                cut = ms.Length > max;
                byte[] all = ms.ToArray();
                if (!cut) return all;
                var head = new byte[max];
                Array.Copy(all, head, max);
                return head;
            }
        }

        // One core-log request to DomovoiHost (the way Update.cs asks for a
        // Linux update): null when it wrote a fresh core.log, else why not.
        // Asked whether Domovoi is ready or not; never throws.
        static string AskForCoreLog(int waitSec)
        {
            try
            {
                using (var s = HostService.Find())
                {
                    if (s == null) return "there is no " + Paths.ServiceName + " service on this PC";
                    s.Refresh();
                    if (s.Status != ServiceControllerStatus.Running) return Paths.ServiceName + " is not running";
                }
                if (!Directory.Exists(Paths.HostIn)) return "no " + Paths.HostIn;
                string id = Guid.NewGuid().ToString("N");
                var req = new Dictionary<string, object>();
                req["v"] = 1;
                req["verb"] = CoreLog.Verb;
                string reqFile = Paths.RequestFile(id), respFile = Paths.ResponseFile(id);
                Json.WriteFile(reqFile, req);
                try
                {
                    DateTime until = DateTime.UtcNow.AddSeconds(waitSec);
                    while (!File.Exists(respFile))
                    {
                        if (DateTime.UtcNow > until) return Paths.ServiceName + " did not answer within " + Inv.S(waitSec) + " s";
                        Thread.Sleep(300);
                    }
                    Thread.Sleep(200);
                    var resp = Steps.ReadHostFile(respFile);
                    if (resp == null) return "no answer";
                    if (Json.Bool(resp, "ok") == true) return null;
                    string error = Json.Str(resp, "error") ?? "no reason given";
                    if (error == "unknown request") return "this PC's " + Paths.ServiceName + " is older than this helper (update Domovoi)";
                    return error;
                }
                finally
                {
                    try { File.Delete(reqFile); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                }
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is UnauthorizedAccessException || e is InvalidOperationException || e is FormatException
                      || e is Win32Exception)) throw;
                return e.Message;
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
