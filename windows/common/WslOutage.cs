// When WSL goes away under Domovoi (incident 2026-11-10, 00:25: Windows
// Update installed WSL 3.0.5.0, which stopped every WSL VM and WSLService;
// DomovoiHost, registered as depending on WSLService, was stopped with it
// by the SCM and stayed down until someone noticed).
//
// Since then:
//   * DomovoiHost depends on nothing (helper\Service.cs): stopping
//     WSLService no longer stops it, and it starts at boot whether WSL is
//     ready or not (its loop already asks WSL again until it answers);
//   * it never exits because WSL is gone: a wsl.exe that can't be started,
//     or a holder that ended while WSL doesn't answer, puts it into a wait
//     (Host.cs WaitForWsl) that probes with the backoff below and, once WSL
//     answers, brings Domovoi up again (the .wslconfig, the distro, the
//     health check), exactly as at a start;
//   * the SCM restarts it after a failure of any kind, an exit with an
//     error code included (FailureActionsOnNonCrashFailures), after 10 s,
//     30 s, then every 60 s;
//   * the tray says why while it waits (tray\Status.cs).
//
// Everything here is pure or a read anyone may make: Windows Installer's
// _MSIExecute mutex (held while an MSI installs), the version of
// C:\Program Files\WSL\wsl.exe, and WSLService's state.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;

namespace Domovoi.Windows
{
    public enum WslService { Running, Starting, Stopping, Stopped, Missing, Unknown }

    public enum WslProbe { Wait, Recover }

    public sealed class WslFacts
    {
        public bool MsiBusy;            // an MSI is installing (any MSI: WSL's update is one)
        public TimeSpan MsiBusyFor;     // for how long, as the caller has seen it (MsiClock)
        public bool ExeExists = true;   // C:\Program Files\WSL\wsl.exe is there
        public string Version;          // its file version; null when it can't be read
        public WslService Service = WslService.Unknown;
        public bool? Answers;           // `wsl --list` answered; null: not asked (or no usable answer)
    }

    // DomovoiHost's exit code, and what an unregister that failed leaves in
    // host\out\unregister.json.
    public static class HostExit
    {
        public const int ExceptionInService = 1064;   // ERROR_EXCEPTION_IN_SERVICE

        // The long-running host that failed: an error code, so the SCM starts
        // it again. An unregister (the uninstaller's one-shot start): always
        // 0, since a restart by the SCM would be the long-running host.
        public static int CodeFor(bool unregisterMode, bool failed)
        {
            if (unregisterMode) return 0;
            return failed ? ExceptionInService : 0;
        }

        public static Dictionary<string, object> UnregisterFailure(Exception e, DateTime utc)
        {
            var d = new Dictionary<string, object>();
            d["ok"] = false;
            d["error"] = "DomovoiHost failed while unregistering: " + (e == null ? "unknown" : e.GetType().Name + ": " + e.Message);
            d["at"] = utc.ToString("o", System.Globalization.CultureInfo.InvariantCulture);
            return d;
        }
    }

    // Since when Windows Installer has been busy, as one caller saw it.
    public sealed class MsiClock
    {
        DateTime? since;

        public TimeSpan For(bool busy, DateTime now)
        {
            if (!busy) { since = null; return TimeSpan.Zero; }
            if (since == null) since = now;
            return now - since.Value;
        }
    }

    // The host's wait for Domovoi to come back (WSL gone, or a bring-up that
    // failed): when to look next, with WslOutage.Delay's backoff. A second
    // loss while already waiting keeps the count, so a flapping WSL doesn't
    // reset the backoff to 5 s every time (review 2026-11-10).
    public sealed class Recovery
    {
        public bool Down;
        public int Attempts;
        public DateTime NextLook = DateTime.MinValue;
        // True: WSL itself went away; false: WSL is fine, Domovoi didn't come up.
        public bool WslTrouble;

        public void Lost(DateTime now, bool wslTrouble)
        {
            WslTrouble = wslTrouble;
            if (!Down)
            {
                Down = true;
                Attempts = 0;
                NextLook = now + WslOutage.Delay(0);
                return;
            }
            DateTime next = now + WslOutage.Delay(Attempts);
            if (next > NextLook) NextLook = next;
        }

        public bool Due(DateTime now) { return Down && now >= NextLook; }

        public void Failed(DateTime now)
        {
            Attempts++;
            NextLook = now + WslOutage.Delay(Attempts);
        }

        public void Recovered()
        {
            Down = false;
            Attempts = 0;
            WslTrouble = false;
        }
    }

    public static class WslOutage
    {
        public const string UpdatedMessage = "Domovoi stopped: Windows updated WSL, starting again\u2026";
        public const string StoppedMessage = "Domovoi stopped: WSL stopped, starting again\u2026";
        public const string ServiceName = "WSLService";

        // Seconds before the next look: 5, 10, 20, 30, then every 60.
        static readonly int[] Delays = { 5, 10, 20, 30, 60 };

        public static TimeSpan Delay(int attempt)
        {
            if (attempt < 0) attempt = 0;
            return TimeSpan.FromSeconds(Delays[Math.Min(attempt, Delays.Length - 1)]);
        }

        // How long an MSI may keep wsl.exe unasked: an install that holds
        // Windows Installer's mutex longer (another product's, stuck, or a
        // pending reboot) mustn't keep Domovoi down for good.
        public static readonly TimeSpan MsiCeiling = TimeSpan.FromMinutes(30);

        // Whether to ask wsl.exe at all: not while an MSI installs (asking
        // would start WSLService in the middle of the update), unless that
        // has lasted MsiCeiling, nor while wsl.exe is missing or the service
        // is on its way down.
        public static bool MayAsk(WslFacts f)
        {
            if (f == null || !f.ExeExists || f.Service == WslService.Stopping || f.Service == WslService.Missing) return false;
            return !f.MsiBusy || f.MsiBusyFor >= MsiCeiling;
        }

        // Which failures out of the host's loop mean "WSL went away": a
        // wsl.exe that can't be started (Win32Exception from
        // Process.Start, or from putting it in the host's job object).
        // Anything else is a bug that should stop the host, and the SCM
        // restarts it (Service.cs).
        public static bool IsWslFailure(Exception e)
        {
            return e is System.ComponentModel.Win32Exception;
        }

        // A bring-up that failed is tried again (with the backoff) when it
        // wasn't a first install: at the service's start for a job already
        // provisioned, after a restart for new limits, after WSL came back.
        // A first install that failed waits for Setup, which shows why.
        public static bool RetryFailedBringUp(bool newJob, bool alreadyProvisioned)
        {
            return !newJob || alreadyProvisioned;
        }

        // Wait, or bring Domovoi up again: only once wsl.exe answered.
        public static WslProbe Decide(WslFacts f)
        {
            return MayAsk(f) && f.Answers == true ? WslProbe.Recover : WslProbe.Wait;
        }

        // Updated (the words say so) when an MSI installs, wsl.exe is gone,
        // or it is another version than when Domovoi last ran.
        public static bool Updated(WslFacts f, string versionWhenUp)
        {
            if (f == null) return false;
            if (f.MsiBusy || !f.ExeExists) return true;
            return versionWhenUp != null && f.Version != null && !string.Equals(f.Version, versionWhenUp, StringComparison.Ordinal);
        }

        public static string Message(WslFacts f, string versionWhenUp)
        {
            return Updated(f, versionWhenUp) ? UpdatedMessage : StoppedMessage;
        }

        // Whether WSL itself is in trouble (rather than only the distro
        // having stopped, which the holder's restart handles): an MSI
        // installs, wsl.exe is gone, WSLService is down, or wsl.exe didn't
        // answer.
        public static bool Gone(WslFacts f)
        {
            if (f == null) return true;
            if (f.MsiBusy || !f.ExeExists) return true;
            if (f.Service == WslService.Stopped || f.Service == WslService.Stopping || f.Service == WslService.Missing) return true;
            return f.Answers == false;
        }

        public static string DownMessage(WslFacts f, string versionWhenUp, bool wslTrouble)
        {
            return wslTrouble ? Message(f, versionWhenUp) : "Domovoi didn't start; trying again\u2026";
        }

        // -- the reads ----------------------------------------------------

        public static bool MsiBusy()
        {
            try
            {
                using (Mutex.OpenExisting(@"Global\_MSIExecute")) return true;
            }
            catch (WaitHandleCannotBeOpenedException) { return false; }
            // It exists, but this account may not open it: still busy.
            catch (UnauthorizedAccessException) { return true; }
            catch (IOException) { return false; }
        }

        public static string ExeVersion(string wslExe)
        {
            try
            {
                if (!File.Exists(wslExe)) return null;
                return FileVersionInfo.GetVersionInfo(wslExe).FileVersion;
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
            catch (ArgumentException) { return null; }
        }

        public static WslService ServiceState()
        {
            try
            {
                using (var sc = new System.ServiceProcess.ServiceController(ServiceName))
                {
                    switch (sc.Status)
                    {
                        case System.ServiceProcess.ServiceControllerStatus.Running: return WslService.Running;
                        case System.ServiceProcess.ServiceControllerStatus.StartPending: return WslService.Starting;
                        case System.ServiceProcess.ServiceControllerStatus.StopPending: return WslService.Stopping;
                        default: return WslService.Stopped;
                    }
                }
            }
            catch (InvalidOperationException e)
            {
                var w = e.InnerException as System.ComponentModel.Win32Exception;
                return w != null && w.NativeErrorCode == 1060 ? WslService.Missing : WslService.Unknown;
            }
            catch (System.ComponentModel.Win32Exception) { return WslService.Unknown; }
        }

        // Everything but Answers and MsiBusyFor (the caller asks wsl.exe
        // when MayAsk, and keeps its own MsiClock).
        public static WslFacts Look(string wslExe)
        {
            var f = new WslFacts();
            f.MsiBusy = MsiBusy();
            f.ExeExists = File.Exists(wslExe);
            f.Version = ExeVersion(wslExe);
            f.Service = ServiceState();
            return f;
        }
    }
}
