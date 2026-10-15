// DomovoiHost.exe: the Windows service Setup registers to run as the
// hidden account domovoi-svc (Automatic, depends on WSLService). See
// Host.cs for what it does and docs/WINDOWS_HOST.md for the whole picture.

using System;
using System.ServiceProcess;
using System.Threading;

namespace Domovoi.Windows.Host
{
    static class Program
    {
        static int Main(string[] args)
        {
            if (Environment.UserInteractive)
            {
                Console.Error.WriteLine("DomovoiHost is a Windows service that Domovoi Setup registers; run Setup instead.");
                return 2;
            }
            ServiceBase.Run(new HostService());
            return 0;
        }
    }

    sealed class HostService : ServiceBase
    {
        readonly ManualResetEvent stop = new ManualResetEvent(false);
        Host host;
        Thread worker;
        bool unregisterMode;

        public HostService()
        {
            ServiceName = Paths.ServiceName;
            CanStop = true;
            CanShutdown = true;
            CanPauseAndContinue = false;
            AutoLog = true;
        }

        protected override void OnStart(string[] args)
        {
            // Start parameters reach here only from someone allowed to start
            // the service: Administrators and SYSTEM (the SCM's default DACL).
            unregisterMode = args != null && args.Length == 1 && args[0] == Paths.UnregisterArg;
            host = new Host(stop);
            worker = new Thread(Work);
            worker.IsBackground = true;
            worker.Name = "DomovoiHost";
            worker.Start();
        }

        void Work()
        {
            try
            {
                if (unregisterMode) host.Unregister();
                else host.Run();
            }
            catch (Exception e)
            {
                host.Info("fatal: " + e);
                ExitCode = 1064; // ERROR_EXCEPTION_IN_SERVICE
            }
            if (unregisterMode || !stop.WaitOne(0))
            {
                // Done (unregister) or failed: stop the service ourselves.
                ThreadPool.QueueUserWorkItem(delegate { try { Stop(); } catch (InvalidOperationException) { } });
            }
        }

        protected override void OnStop()
        {
            RequestAdditionalTime(90000);
            stop.Set();
            if (worker != null) worker.Join(15000);
            if (!unregisterMode && host != null) host.Shutdown(false);
        }

        protected override void OnShutdown()
        {
            stop.Set();
            if (worker != null) worker.Join(3000);
            if (!unregisterMode && host != null) host.Shutdown(true);
        }
    }
}
