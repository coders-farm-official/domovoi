// DomovoiHost's registration (PLAN 4.6): logs on as .\domovoi-svc,
// Automatic, depends on WSLService, restarts after 10 s, 30 s and 60 s
// when it fails. Created through the SCM API so the password goes in as
// an unmanaged buffer (Account.cs), never through sc.exe or PowerShell.

using System;
using System.ComponentModel;
using System.Management;
using System.Runtime.InteropServices;
using System.ServiceProcess;
using System.Threading;

namespace Domovoi.Windows.Helper
{
    static class HostService
    {
        public const string Description = "Keeps Domovoi's private Linux system (WSL distro \"domovoi\") running as the hidden account domovoi-svc. Removed by Domovoi's uninstaller.";

        public static string Account { get { return ".\\" + Paths.AccountName; } }

        // Creates the service, or points the existing one at this binary and
        // account with the given password. True when it created it.
        public static bool CreateOrUpdate(string exePath, Secret pw, Action<string> say)
        {
            IntPtr scm = Native.OpenSCManagerW(null, null, Native.SC_MANAGER_ALL_ACCESS);
            if (scm == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "OpenSCManager");
            try
            {
                string bin = "\"" + exePath + "\"";
                bool created = false;
                IntPtr svc = Native.OpenServiceW(scm, Paths.ServiceName, Native.SERVICE_ALL_ACCESS);
                if (svc == IntPtr.Zero)
                {
                    int err = Marshal.GetLastWin32Error();
                    if (err != Native.ERROR_SERVICE_DOES_NOT_EXIST) throw new Win32Exception(err, "OpenService");
                    svc = Native.CreateServiceW(scm, Paths.ServiceName, Paths.ServiceDisplayName, Native.SERVICE_ALL_ACCESS,
                        Native.SERVICE_WIN32_OWN_PROCESS, Native.SERVICE_AUTO_START, Native.SERVICE_ERROR_NORMAL, bin, null,
                        IntPtr.Zero, "WSLService\0", Account, pw.Buffer);
                    if (svc == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateService");
                    created = true;
                    say("registered the " + Paths.ServiceName + " service (Automatic, runs as " + Account + ", depends on WSLService)");
                }
                else
                {
                    if (!Native.ChangeServiceConfigW(svc, Native.SERVICE_WIN32_OWN_PROCESS, Native.SERVICE_AUTO_START, Native.SERVICE_ERROR_NORMAL,
                        bin, null, IntPtr.Zero, "WSLService\0", Account, pw.Buffer, Paths.ServiceDisplayName))
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "ChangeServiceConfig");
                    say("updated the " + Paths.ServiceName + " service (binary, account, fresh password, start type)");
                }
                try
                {
                    var d = new Native.SERVICE_DESCRIPTION();
                    d.description = Marshal.StringToHGlobalUni(Description);
                    try
                    {
                        if (!Native.ChangeServiceDescription(svc, Native.SERVICE_CONFIG_DESCRIPTION, ref d))
                            throw new Win32Exception(Marshal.GetLastWin32Error(), "ChangeServiceConfig2 description");
                    }
                    finally { Marshal.FreeHGlobal(d.description); }
                    SetFailureActions(svc);
                }
                finally { Native.CloseServiceHandle(svc); }
                return created;
            }
            finally { Native.CloseServiceHandle(scm); }
        }

        static void SetFailureActions(IntPtr svc)
        {
            int[] delays = { 10000, 30000, 60000 };
            int size = Marshal.SizeOf(typeof(Native.SC_ACTION));
            IntPtr arr = Marshal.AllocHGlobal(size * delays.Length);
            try
            {
                for (int i = 0; i < delays.Length; i++)
                {
                    var a = new Native.SC_ACTION();
                    a.type = Native.SC_ACTION_RESTART;
                    a.delay = delays[i];
                    Marshal.StructureToPtr(a, new IntPtr(arr.ToInt64() + i * size), false);
                }
                var fa = new Native.SERVICE_FAILURE_ACTIONS();
                fa.resetPeriod = 86400;
                fa.actionCount = delays.Length;
                fa.actions = arr;
                if (!Native.ChangeServiceFailureActions(svc, Native.SERVICE_CONFIG_FAILURE_ACTIONS, ref fa))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "ChangeServiceConfig2 failure actions");
            }
            finally { Marshal.FreeHGlobal(arr); }
        }

        // Only a fresh password for the existing service (uninstall's
        // rotate, PLAN 4.5).
        public static void SetPassword(Secret pw)
        {
            IntPtr scm = Native.OpenSCManagerW(null, null, Native.SC_MANAGER_ALL_ACCESS);
            if (scm == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "OpenSCManager");
            try
            {
                IntPtr svc = Native.OpenServiceW(scm, Paths.ServiceName, Native.SERVICE_ALL_ACCESS);
                if (svc == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "OpenService");
                try
                {
                    if (!Native.ChangeServiceConfigW(svc, Native.SERVICE_NO_CHANGE, Native.SERVICE_NO_CHANGE, Native.SERVICE_NO_CHANGE,
                        null, null, IntPtr.Zero, null, Account, pw.Buffer, null))
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "ChangeServiceConfig password");
                }
                finally { Native.CloseServiceHandle(svc); }
            }
            finally { Native.CloseServiceHandle(scm); }
        }

        // Automatic (true) or Manual (false).
        public static void SetAutomatic(bool automatic)
        {
            IntPtr scm = Native.OpenSCManagerW(null, null, Native.SC_MANAGER_ALL_ACCESS);
            if (scm == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "OpenSCManager");
            try
            {
                IntPtr svc = Native.OpenServiceW(scm, Paths.ServiceName, Native.SERVICE_ALL_ACCESS);
                if (svc == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "OpenService");
                try
                {
                    if (!Native.ChangeServiceConfigW(svc, Native.SERVICE_NO_CHANGE, automatic ? Native.SERVICE_AUTO_START : Native.SERVICE_DEMAND_START,
                        Native.SERVICE_NO_CHANGE, null, null, IntPtr.Zero, null, null, IntPtr.Zero, null))
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "ChangeServiceConfig start type");
                }
                finally { Native.CloseServiceHandle(svc); }
            }
            finally { Native.CloseServiceHandle(scm); }
        }

        public static bool Delete()
        {
            IntPtr scm = Native.OpenSCManagerW(null, null, Native.SC_MANAGER_ALL_ACCESS);
            if (scm == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "OpenSCManager");
            try
            {
                IntPtr svc = Native.OpenServiceW(scm, Paths.ServiceName, Native.SERVICE_ALL_ACCESS);
                if (svc == IntPtr.Zero)
                {
                    int err = Marshal.GetLastWin32Error();
                    if (err == Native.ERROR_SERVICE_DOES_NOT_EXIST) return false;
                    throw new Win32Exception(err, "OpenService");
                }
                try
                {
                    if (!Native.DeleteService(svc))
                    {
                        int err = Marshal.GetLastWin32Error();
                        if (err != Native.ERROR_SERVICE_MARKED_FOR_DELETE) throw new Win32Exception(err, "DeleteService");
                    }
                    return true;
                }
                finally { Native.CloseServiceHandle(svc); }
            }
            finally { Native.CloseServiceHandle(scm); }
        }

        public static ServiceController Find()
        {
            foreach (var s in ServiceController.GetServices())
            {
                if (string.Equals(s.ServiceName, Paths.ServiceName, StringComparison.OrdinalIgnoreCase)) return s;
                s.Dispose();
            }
            return null;
        }

        // The binary the SCM starts, or null when there is no such service.
        public static string BinaryPath()
        {
            using (var search = new ManagementObjectSearcher("SELECT PathName FROM Win32_Service WHERE Name = '" + Paths.ServiceName + "'"))
            {
                foreach (ManagementObject o in search.Get())
                {
                    using (o) return o["PathName"] as string;
                }
            }
            return null;
        }

        public static bool IsDomovoiBinary(string pathName)
        {
            if (pathName == null) return false;
            string p = pathName.Trim().Trim('"');
            return p.EndsWith("\\DomovoiHost.exe", StringComparison.OrdinalIgnoreCase);
        }

        public static void Stop(int seconds, Action<string> say)
        {
            using (var s = Find())
            {
                if (s == null) return;
                s.Refresh();
                if (s.Status == ServiceControllerStatus.Stopped) return;
                if (s.Status != ServiceControllerStatus.StopPending) s.Stop();
                say("stopping " + Paths.ServiceName + " (it stops Domovoi's services in the distro first)");
                s.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(seconds));
                say("stopped " + Paths.ServiceName);
            }
        }

        // Starts the long-running host (no start parameter) and waits until
        // it runs. One that stops again at once (it logs why in host.log)
        // is an error, not a 2-minute wait.
        public static void Start(int seconds)
        {
            using (var s = Find())
            {
                if (s == null) throw new InvalidOperationException(Paths.ServiceName + " is not registered");
                s.Refresh();
                if (s.Status == ServiceControllerStatus.Running) return;
                if (s.Status == ServiceControllerStatus.StopPending) s.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(240));
                s.Refresh();
                if (s.Status == ServiceControllerStatus.Stopped) s.Start();
                ServiceControllerStatus? st = ServiceWait.Started(delegate { s.Refresh(); return s.Status; }, Thread.Sleep,
                    delegate { return DateTime.UtcNow; }, TimeSpan.FromSeconds(seconds));
                if (st == null) throw new System.TimeoutException(Paths.ServiceName + " did not start within " + Inv.S(seconds) + " s");
                if (st == ServiceControllerStatus.Stopped) throw new InvalidOperationException(Paths.ServiceName + " stopped right after it started; see " + Paths.HostLog);
            }
        }

        // Starts the host for a run that stops by itself when its work is
        // done (the uninstall's "unregister"), and waits for it: Running or
        // already Stopped again (it can finish in a second), then Stopped
        // (ServiceWait.OneShot).
        public static OneShotResult RunOnce(string[] args, int startSeconds, int finishSeconds)
        {
            using (var s = Find())
            {
                if (s == null) throw new InvalidOperationException(Paths.ServiceName + " is not registered");
                s.Refresh();
                if (s.Status == ServiceControllerStatus.StopPending) s.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(240));
                s.Refresh();
                // A start parameter only reaches a service that starts now: a
                // running one would ignore it, so stop it first.
                if (s.Status != ServiceControllerStatus.Stopped)
                {
                    s.Stop();
                    s.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(240));
                }
                s.Start(args);
                return ServiceWait.OneShot(delegate { s.Refresh(); return s.Status; }, Thread.Sleep, delegate { return DateTime.UtcNow; },
                    TimeSpan.FromSeconds(startSeconds), TimeSpan.FromSeconds(finishSeconds));
            }
        }
    }
}
