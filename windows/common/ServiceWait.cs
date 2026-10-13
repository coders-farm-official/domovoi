// Waiting on DomovoiHost's state after a start, as pure logic over a
// status reader and a clock, so the tests can play back what Windows
// reported.
//
// Why: on 2026-10-12 the uninstall started DomovoiHost with "unregister";
// the host unregistered the distro and stopped itself within about a
// second, but the helper waited for the state Running, never saw it in
// that second, timed out after 120 s and reported the distro as left over
// (windows-installer\phaseB-logs-2, domovoi-uninstall-20261012-133801.log).
// A start is over when the service is Running OR already Stopped again; a
// one-shot run is over when it is Stopped.

using System;
using System.ServiceProcess;

namespace Domovoi.Windows
{
    public enum OneShotResult
    {
        // It ran and is stopped again (or never got past starting: the
        // caller's own result file tells which).
        Finished,
        // Still StartPending when the start limit ran out.
        NeverStarted,
        // Started, but not stopped when the finish limit ran out.
        StillRunning,
    }

    public static class ServiceWait
    {
        public const int PollMs = 250;

        // After a start: the state it settled on, Running or Stopped, or
        // null when it was still starting at the limit.
        public static ServiceControllerStatus? Started(Func<ServiceControllerStatus> status, Action<int> sleep, Func<DateTime> now, TimeSpan limit)
        {
            DateTime until = now() + limit;
            while (true)
            {
                ServiceControllerStatus st = status();
                if (st == ServiceControllerStatus.Running || st == ServiceControllerStatus.Stopped) return st;
                // Started and already on its way out: as good as having run.
                if (st == ServiceControllerStatus.StopPending) return ServiceControllerStatus.Running;
                if (now() > until) return null;
                sleep(PollMs);
            }
        }

        // A run that stops itself when its work is done (the host's
        // "unregister"): wait for Running or Stopped, then for Stopped.
        public static OneShotResult OneShot(Func<ServiceControllerStatus> status, Action<int> sleep, Func<DateTime> now, TimeSpan startLimit, TimeSpan finishLimit)
        {
            ServiceControllerStatus? st = Started(status, sleep, now, startLimit);
            if (st == null) return OneShotResult.NeverStarted;
            if (st == ServiceControllerStatus.Stopped) return OneShotResult.Finished;
            DateTime until = now() + finishLimit;
            while (true)
            {
                if (status() == ServiceControllerStatus.Stopped) return OneShotResult.Finished;
                if (now() > until) return OneShotResult.StillRunning;
                sleep(PollMs);
            }
        }
    }
}
