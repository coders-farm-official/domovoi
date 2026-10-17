// What the tray icon says about Domovoi, from sources that need no
// password: the DomovoiHost service's state (readable by any user), the
// dashboard's open /api/health (status, db_reachable, domovoi_reachable,
// core_alive) and the core's open /v1/alive. Nothing new is asked of the
// server. Status.Decide is pure (the tests feed it facts); Probe gathers
// the facts.

using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.ServiceProcess;
using System.Text;

namespace Domovoi.Windows.Tray
{
    public enum ServiceFact { Running, Starting, Stopping, Stopped, NotInstalled, Unknown, NotChecked }

    public enum WebFact { Ok, Degraded, NoAnswer }

    public sealed class Facts
    {
        public ServiceFact Service = ServiceFact.Unknown;
        public WebFact Web = WebFact.NoAnswer;
        public bool DbReachable, CoreReachable;
        public bool? CoreAlive;     // the web's view, else the core's own /v1/alive
    }

    public enum TrayState { Running, Starting, Stopped, Problem }

    public sealed class Verdict
    {
        public TrayState State;
        public string Line;          // "Domovoi is running": the tooltip and the menu's first line
        public string Detail;        // a second line for the menu, or null
        public bool Up { get { return State == TrayState.Running; } }
        public bool Settled { get { return State != TrayState.Starting; } }
    }

    public static class Status
    {
        // How long Domovoi may take to come up (after a restart, an update,
        // or the PC starting) before the icon says something is wrong.
        public static readonly TimeSpan DefaultGrace = TimeSpan.FromMinutes(10);

        // notReadySince: when the tray first saw Domovoi not ready in this
        // stretch (or started looking); now: the time of these facts.
        public static Verdict Decide(Facts f, DateTime notReadySince, DateTime now, TimeSpan grace)
        {
            if (f.Web == WebFact.Ok)
                return V(TrayState.Running, "Domovoi is running", null);
            switch (f.Service)
            {
                case ServiceFact.Stopped:
                    return V(TrayState.Stopped, "Domovoi is stopped", "Restart Domovoi below starts it again.");
                case ServiceFact.Stopping:
                    return V(TrayState.Starting, "Domovoi is stopping", null);
                case ServiceFact.Starting:
                    return V(TrayState.Starting, "Domovoi is starting", "This can take a few minutes.");
                case ServiceFact.NotInstalled:
                    if (f.Web == WebFact.NoAnswer && f.CoreAlive != true)
                        return V(TrayState.Stopped, "Domovoi isn't installed on this PC", "Its service (DomovoiHost) isn't here.");
                    break;
            }
            string why = Why(f);
            if (now - notReadySince < grace)
                return V(TrayState.Starting, "Domovoi is starting or updating", "This can take a few minutes.");
            return V(TrayState.Problem, "Domovoi has a problem", why);
        }

        static string Why(Facts f)
        {
            if (f.Web == WebFact.NoAnswer)
                return f.CoreAlive == true ? "The dashboard isn't answering." : "Nothing is answering.";
            if (!f.DbReachable) return "Its database isn't answering.";
            if (f.CoreAlive == false) return "The voice service isn't running.";
            return "The voice service isn't answering properly.";
        }

        static Verdict V(TrayState s, string line, string detail)
        {
            return new Verdict { State = s, Line = line, Detail = detail };
        }
    }

    // Since when Domovoi has been up but not answering: the grace counts
    // from the first poll that saw the service running (or the tray
    // starting) without a healthy dashboard, never from a stop before it.
    public sealed class ReadyClock
    {
        DateTime? since;

        public DateTime Since(Facts f, DateTime now)
        {
            bool waiting = f.Web != WebFact.Ok
                && (f.Service == ServiceFact.Running || f.Service == ServiceFact.Unknown || f.Service == ServiceFact.NotChecked || f.Service == ServiceFact.NotInstalled);
            if (!waiting) { since = null; return now; }
            if (since == null) since = now;
            return since.Value;
        }
    }

    // When to tell the household (a Windows notification): Domovoi went
    // down, or came back. Not on every poll, not while it is starting, not
    // when the tray itself starts.
    public sealed class Notifier
    {
        bool? lastUp;

        // The notification for this verdict, or null.
        public string[] Next(Verdict v)
        {
            if (!v.Settled) return null;
            bool? was = lastUp;
            lastUp = v.Up;
            if (was == null || was.Value == v.Up) return null;
            if (v.Up) return new[] { "Domovoi is running again", "Satellites and phones can reach it." };
            if (v.State == TrayState.Stopped) return new[] { v.Line, "Satellites and phones can't reach it until it starts again." };
            return new[] { v.Line, v.Detail ?? "Satellites and phones may not reach it." };
        }
    }

    // The facts, gathered. Every call has a short timeout; nothing here
    // carries a credential.
    public sealed class Probe
    {
        readonly string serviceName, webUrl, coreUrl;

        public Probe(string serviceName, string webUrl, string coreUrl)
        {
            this.serviceName = serviceName;
            this.webUrl = webUrl.TrimEnd('/');
            this.coreUrl = coreUrl.TrimEnd('/');
        }

        public Facts Gather()
        {
            var f = new Facts();
            f.Service = ServiceState(serviceName);
            Dictionary<string, object> health = GetJson(webUrl + "/api/health");
            if (health != null)
            {
                f.DbReachable = Json.Bool(health, "db_reachable") == true;
                f.CoreReachable = Json.Bool(health, "domovoi_reachable") == true;
                f.CoreAlive = Json.Bool(health, "core_alive");
                f.Web = Json.Str(health, "status") == "ok" ? WebFact.Ok : WebFact.Degraded;
            }
            if (f.CoreAlive == null) f.CoreAlive = GetJson(coreUrl + "/v1/alive") != null;
            return f;
        }

        // The server identity's fingerprint the dashboard reports (the
        // open /api/health), or null when it doesn't answer.
        public static string ServerFingerprint(string webUrl)
        {
            Dictionary<string, object> health = GetJson(webUrl.TrimEnd('/') + "/api/health");
            return Json.Str(Json.Obj(health, "identity"), "fingerprint");
        }

        public static ServiceFact ServiceState(string name)
        {
            if (string.IsNullOrEmpty(name)) return ServiceFact.NotChecked;
            try
            {
                using (var sc = new ServiceController(name))
                {
                    switch (sc.Status)
                    {
                        case ServiceControllerStatus.Running: return ServiceFact.Running;
                        case ServiceControllerStatus.StartPending:
                        case ServiceControllerStatus.ContinuePending: return ServiceFact.Starting;
                        case ServiceControllerStatus.StopPending:
                        case ServiceControllerStatus.PausePending: return ServiceFact.Stopping;
                        case ServiceControllerStatus.Stopped:
                        case ServiceControllerStatus.Paused: return ServiceFact.Stopped;
                    }
                }
            }
            catch (InvalidOperationException e)
            {
                // 1060: no such service.
                var w = e.InnerException as System.ComponentModel.Win32Exception;
                if (w != null && w.NativeErrorCode == 1060) return ServiceFact.NotInstalled;
                return ServiceFact.Unknown;
            }
            catch (System.ComponentModel.Win32Exception) { return ServiceFact.Unknown; }
            return ServiceFact.Unknown;
        }

        // A JSON object from an open GET, or null for no answer at all
        // (any HTTP answer with a JSON object counts, whatever its status).
        static Dictionary<string, object> GetJson(string url)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "GET";
                req.Timeout = 4000;
                req.ReadWriteTimeout = 4000;
                req.Proxy = null;
                req.AllowAutoRedirect = false;
                req.Accept = "application/json";
                req.UserAgent = "DomovoiTray";
                HttpWebResponse resp;
                try { resp = (HttpWebResponse)req.GetResponse(); }
                catch (WebException e)
                {
                    resp = e.Response as HttpWebResponse;
                    if (resp == null) return null;
                }
                using (resp)
                using (Stream s = resp.GetResponseStream())
                using (var r = new StreamReader(s, Encoding.UTF8))
                {
                    char[] buf = new char[65536];
                    int n = r.ReadBlock(buf, 0, buf.Length);
                    try { return Json.ParseObject(new string(buf, 0, n)); }
                    catch (FormatException) { return new Dictionary<string, object>(); }
                    catch (ArgumentException) { return new Dictionary<string, object>(); }
                    catch (InvalidOperationException) { return new Dictionary<string, object>(); }
                }
            }
            catch (WebException) { return null; }
            catch (IOException) { return null; }
            catch (UriFormatException) { return null; }
            catch (NotSupportedException) { return null; }
        }
    }
}
