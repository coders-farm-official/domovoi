// Ends every process running as domovoi-svc. The helper runs elevated and
// works in folders that account can write (the media folders, parts of
// C:\ProgramData\Domovoi); before it changes or deletes anything there it
// makes sure nothing is acting as the account at the same time (with the
// service stopped or deleted, nothing starts a new one).

using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security;
using System.Security.Principal;

namespace Domovoi.Windows.Helper
{
    static class Processes
    {
        const int PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
        const int TOKEN_QUERY = 0x0008;

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr OpenProcess(int access, [MarshalAs(UnmanagedType.Bool)] bool inherit, int pid);

        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool OpenProcessToken(IntPtr process, int access, out IntPtr token);

        [DllImport("kernel32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool CloseHandle(IntPtr h);

        static SecurityIdentifier Owner(int pid)
        {
            IntPtr p = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
            if (p == IntPtr.Zero) return null;
            try
            {
                IntPtr tok;
                if (!OpenProcessToken(p, TOKEN_QUERY, out tok)) return null;
                try
                {
                    using (var id = new WindowsIdentity(tok)) return id.User;
                }
                finally { CloseHandle(tok); }
            }
            finally { CloseHandle(p); }
        }

        // Kills them all and waits; returns how many there were. Throws if
        // one can't be ended.
        public static int StopAccount(SecurityIdentifier sid, Action<string> say)
        {
            int n = 0;
            foreach (Process p in Process.GetProcesses())
            {
                using (p)
                {
                    SecurityIdentifier owner;
                    try { owner = Owner(p.Id); }
                    catch (Exception e)
                    {
                        if (e is ArgumentException || e is SecurityException || e is UnauthorizedAccessException) continue;
                        throw;
                    }
                    if (owner == null || !owner.Equals(sid)) continue;
                    try
                    {
                        p.Kill();
                        if (!p.WaitForExit(15000)) throw new InvalidOperationException(p.ProcessName + " (PID " + Inv.S(p.Id) + ") did not end");
                        n++;
                    }
                    catch (InvalidOperationException e)
                    {
                        if (!p.HasExited) throw new InvalidOperationException("can't end " + p.ProcessName + " running as " + Paths.AccountName + ": " + e.Message);
                    }
                    catch (Win32Exception e)
                    {
                        throw new InvalidOperationException("can't end a process running as " + Paths.AccountName + ": " + e.Message);
                    }
                }
            }
            if (n > 0) say("ended " + Inv.S(n) + " process(es) still running as " + Paths.AccountName);
            return n;
        }
    }
}
