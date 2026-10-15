// wsl.exe as DomovoiHost runs it: always C:\Program Files\WSL\wsl.exe
// (the System32 stub doesn't work from session 0, microsoft/WSL#9231),
// always WSL_UTF8=1 so wsl.exe's own messages come back as UTF-8 rather
// than UTF-16, and always --cd / so no Windows working directory has to
// be translated (the distro has no /mnt/c once provisioned). Every wsl.exe
// it starts goes into a job object that kills them all when the host
// process ends, so a stopped or crashed host leaves no import or
// provisioning run behind.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Domovoi.Windows.Host
{
    sealed class WslResult
    {
        public int ExitCode;
        public string Stdout = "";
        public string Stderr = "";
        public bool TimedOut;
    }

    static class ChildJob
    {
        [StructLayout(LayoutKind.Sequential)]
        struct BASIC_LIMIT
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct IO_COUNTERS
        {
            public ulong a, b, c, d, e, f;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct EXTENDED_LIMIT
        {
            public BASIC_LIMIT Basic;
            public IO_COUNTERS Io;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;
        const int JobObjectExtendedLimitInformation = 9;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateJobObjectW(IntPtr attrs, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool SetInformationJobObject(IntPtr job, int cls, ref EXTENDED_LIMIT info, int size);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        static readonly object gate = new object();
        static IntPtr job = IntPtr.Zero;

        // The handle stays open for the life of the host process; Windows
        // closes it when the process ends, and that kills the children.
        static IntPtr Job()
        {
            lock (gate)
            {
                if (job != IntPtr.Zero) return job;
                IntPtr h = CreateJobObjectW(IntPtr.Zero, null);
                if (h == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject");
                var info = new EXTENDED_LIMIT();
                info.Basic.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if (!SetInformationJobObject(h, JobObjectExtendedLimitInformation, ref info, Marshal.SizeOf(typeof(EXTENDED_LIMIT))))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "SetInformationJobObject");
                job = h;
                return job;
            }
        }

        public static void Add(Process p)
        {
            if (!AssignProcessToJobObject(Job(), p.Handle))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "AssignProcessToJobObject");
        }
    }

    static class Wsl
    {
        public static ProcessStartInfo Start(IEnumerable<string> args)
        {
            var psi = new ProcessStartInfo(Paths.WslExe, CommandLine.Join(args));
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WorkingDirectory = Environment.SystemDirectory;
            psi.EnvironmentVariables["WSL_UTF8"] = "1";
            return psi;
        }

        // Starts wsl.exe inside the host's job object.
        public static Process Launch(ProcessStartInfo psi)
        {
            Process p = Process.Start(psi);
            try { ChildJob.Add(p); }
            catch (Win32Exception)
            {
                try { p.Kill(); } catch (InvalidOperationException) { } catch (Win32Exception) { }
                p.Dispose();
                throw;
            }
            return p;
        }

        // Runs wsl.exe to the end (or the timeout, then kills it), with
        // stdout and stderr captured up to maxOutput characters each. tick,
        // when given, runs about once a second while it waits.
        public static WslResult Run(IEnumerable<string> args, int timeoutMs, byte[] stdin, int maxOutput, Action tick)
        {
            var psi = Start(args);
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.RedirectStandardInput = stdin != null;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            var r = new WslResult();
            var o = new StringBuilder();
            var e = new StringBuilder();
            using (var p = Launch(psi))
            {
                p.OutputDataReceived += delegate (object s, DataReceivedEventArgs a) { if (a.Data != null) lock (o) if (o.Length < maxOutput) o.Append(a.Data).Append('\n'); };
                p.ErrorDataReceived += delegate (object s, DataReceivedEventArgs a) { if (a.Data != null) lock (e) if (e.Length < maxOutput) e.Append(a.Data).Append('\n'); };
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                if (stdin != null)
                {
                    try
                    {
                        p.StandardInput.BaseStream.Write(stdin, 0, stdin.Length);
                        p.StandardInput.BaseStream.Flush();
                    }
                    catch (IOException) { }
                    p.StandardInput.Close();
                }
                DateTime until = DateTime.UtcNow.AddMilliseconds(timeoutMs);
                bool exited = false;
                while (!(exited = p.WaitForExit(1000)))
                {
                    if (tick != null) tick();
                    if (DateTime.UtcNow > until) break;
                }
                if (!exited)
                {
                    r.TimedOut = true;
                    try { p.Kill(); } catch (InvalidOperationException) { } catch (Win32Exception) { }
                    p.WaitForExit(5000);
                }
                else
                {
                    p.WaitForExit();
                }
                r.ExitCode = r.TimedOut ? -1 : p.ExitCode;
            }
            lock (o) r.Stdout = o.ToString();
            lock (e) r.Stderr = e.ToString();
            return r;
        }

        public static WslResult Run(IEnumerable<string> args, int timeoutMs, byte[] stdin, int maxOutput)
        {
            return Run(args, timeoutMs, stdin, maxOutput, null);
        }

        // The distros this account has; an empty list only when WSL says it
        // has none; null whenever WSL can't be asked (WslList).
        public static List<string> List()
        {
            WslResult r = Run(new[] { "--list", "--quiet" }, 60000, null, 64 * 1024);
            return WslList.Parse(r.ExitCode, r.TimedOut, r.Stdout + "\n" + r.Stderr);
        }

        public static bool? Running()
        {
            WslResult r = Run(new[] { "--list", "--running", "--quiet" }, 30000, null, 64 * 1024);
            return WslList.Contains(WslList.Parse(r.ExitCode, r.TimedOut, r.Stdout + "\n" + r.Stderr), Paths.DistroName);
        }

        public static bool? Registered()
        {
            return WslList.Contains(List(), Paths.DistroName);
        }
    }
}
