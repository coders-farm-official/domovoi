// logs\core.log and the core's own files (core-watchdog.log, its .1,
// core-faulthandler.log), written by DomovoiHost as domovoi-svc
// (common\CoreLog.cs says what and when). Each file is made the way host.log is: in
// C:\ProgramData\Domovoi\logs, inheriting that folder's ACL (SYSTEM,
// Administrators, and Modify for this account), through AtomicFile (a
// temporary file beside it, then a replace), so the helper never copies a
// half-written one. A read that fails leaves the last copy as it was.

using System;
using System.Collections.Generic;
using System.IO;

namespace Domovoi.Windows.Host
{
    static class CoreJournal
    {
        // run: Wsl.Run(args, timeoutMs, null, maxOutput) in the service; the
        // tests pass a fake. Null when path now holds a fresh copy, else why
        // not.
        public static string Refresh(Func<IEnumerable<string>, int, int, WslResult> run, string path, DateTime nowUtc)
        {
            WslResult r;
            try { r = run(CoreLog.WslArgs(Paths.DistroName), CoreLog.TimeoutMs, CoreLog.MaxOutputChars); }
            catch (Exception e)
            {
                if (!(e is System.ComponentModel.Win32Exception || e is InvalidOperationException || e is IOException)) throw;
                return "wsl.exe could not be started: " + e.Message;
            }
            string why;
            string text = CoreLog.Render(r.ExitCode, r.TimedOut, r.Stdout, r.Stderr, nowUtc, out why);
            if (text == null) return why;
            try
            {
                AtomicFile.WriteAllText(path, text);
                return null;
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is UnauthorizedAccessException)) throw;
                return "can't write " + path + ": " + e.Message;
            }
        }

        // The core's own files (CoreLog.Files) into dir, the same way, read
        // as the service user. Null when dir now holds what the distro has
        // (a file it no longer has is deleted here too), else why not; a
        // failed read changes nothing.
        public static string RefreshFiles(Func<IEnumerable<string>, int, int, WslResult> run, string dir, DateTime nowUtc)
        {
            WslResult r;
            try { r = run(CoreLog.FileArgs(Paths.DistroName), CoreLog.FilesTimeoutMs, CoreLog.MaxFilesOutputChars); }
            catch (Exception e)
            {
                if (!(e is System.ComponentModel.Win32Exception || e is InvalidOperationException || e is IOException)) throw;
                return "wsl.exe could not be started: " + e.Message;
            }
            string why;
            Dictionary<string, string> files = CoreLog.RenderFiles(r.ExitCode, r.TimedOut, r.Stdout, r.Stderr, nowUtc, out why);
            if (files == null) return why;
            var failed = new List<string>();
            foreach (string name in CoreLog.Files)
            {
                string path = Path.Combine(dir, name);
                try
                {
                    if (files[name] != null) AtomicFile.WriteAllText(path, files[name]);
                    else if (File.Exists(path)) File.Delete(path);
                }
                catch (Exception e)
                {
                    if (!(e is IOException || e is UnauthorizedAccessException)) throw;
                    failed.Add("can't write " + path + ": " + e.Message);
                }
            }
            return failed.Count == 0 ? null : string.Join("; ", failed.ToArray());
        }
    }
}
