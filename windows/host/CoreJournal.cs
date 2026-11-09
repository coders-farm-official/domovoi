// logs\core.log, written by DomovoiHost as domovoi-svc (common\CoreLog.cs
// says what and when). The file is made the way host.log is: in
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
    }
}
