// JSON over this PC's loopback to the Domovoi core, for the Windows
// update's state and results (domovoi/windows_side.py; the core answers
// these routes to a loopback peer only, and refuses any request carrying a
// forwarding header). Never through a proxy, never following a redirect,
// and nothing here carries a credential.

using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;

namespace Domovoi.Windows
{
    public sealed class JsonReply
    {
        public int Status;
        public Dictionary<string, object> Body = new Dictionary<string, object>();
    }

    public static class Loopback
    {
        public const string Core = "http://127.0.0.1:6370";
        public const string StatePath = "/v1/windows/update";
        public const string ResultPath = "/v1/windows/update/result";

        // Null when nothing answered at all.
        public static JsonReply Send(string method, string url, string json, int timeoutMs)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = method;
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.Proxy = null;
                req.AllowAutoRedirect = false;
                req.Accept = "application/json";
                req.UserAgent = "DomovoiTray";
                req.Headers["X-Requested-With"] = "DomovoiTray";
                if (json != null)
                {
                    byte[] b = new UTF8Encoding(false).GetBytes(json);
                    req.ContentType = "application/json";
                    req.ContentLength = b.Length;
                    using (Stream s = req.GetRequestStream()) s.Write(b, 0, b.Length);
                }
                HttpWebResponse resp;
                try { resp = (HttpWebResponse)req.GetResponse(); }
                catch (WebException e)
                {
                    resp = e.Response as HttpWebResponse;
                    if (resp == null) return null;
                }
                using (resp)
                using (Stream s = resp.GetResponseStream())
                using (var rd = new StreamReader(s, Encoding.UTF8))
                {
                    var reply = new JsonReply { Status = (int)resp.StatusCode };
                    char[] buf = new char[256 * 1024];
                    int n = rd.ReadBlock(buf, 0, buf.Length);
                    try { reply.Body = Json.ParseObject(new string(buf, 0, n)) ?? new Dictionary<string, object>(); }
                    catch (FormatException) { }
                    catch (ArgumentException) { }
                    catch (InvalidOperationException) { }
                    return reply;
                }
            }
            catch (WebException) { return null; }
            catch (IOException) { return null; }
            catch (UriFormatException) { return null; }
            catch (NotSupportedException) { return null; }
        }

        // A JSON string.
        public static string Quote(string s)
        {
            var sb = new StringBuilder("\"");
            foreach (char c in s ?? "")
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4", System.Globalization.CultureInfo.InvariantCulture));
                else sb.Append(c);
            }
            return sb.Append('"').ToString();
        }
    }

    // The core's answer to GET /v1/windows/update, as the tray and the
    // helper read it.
    public sealed class UpdateState
    {
        public string Installed, InstalledCommit;     // the Windows side DomovoiHost reported
        public string TargetTag, TargetVersion, TargetCommit;
        public bool StepNeeded;
        public string WhyNot;                          // internet_off, no_release, up_to_date, ...
        public string Internet;
        public string Channel;
        public bool LinuxBusy;
        public string LastUpdateStarted, LastUpdateStatus, LastUpdateError;
        public string RequestId;
        public bool RequestOnThisPc;

        public static UpdateState From(Dictionary<string, object> d)
        {
            if (d == null || Json.Int(d, "schema") != 1) return null;
            var s = new UpdateState();
            Dictionary<string, object> w = Json.Obj(d, "windows"), t = Json.Obj(d, "target"), l = Json.Obj(d, "linux"), r = Json.Obj(d, "request");
            if (w != null) { s.Installed = Json.Str(w, "version"); s.InstalledCommit = Json.Str(w, "build_commit"); }
            if (t != null) { s.TargetTag = Json.Str(t, "tag"); s.TargetVersion = Json.Str(t, "version"); s.TargetCommit = Json.Str(t, "commit"); }
            s.StepNeeded = Json.Bool(d, "step_needed") == true;
            s.WhyNot = Json.Str(d, "why_not");
            s.Internet = Json.Str(d, "internet");
            s.Channel = Json.Str(d, "channel");
            if (l != null)
            {
                s.LinuxBusy = Json.Bool(l, "busy") == true;
                Dictionary<string, object> lu = Json.Obj(l, "last_update");
                if (lu != null)
                {
                    s.LastUpdateStarted = Json.Str(lu, "started_at");
                    s.LastUpdateStatus = Json.Str(lu, "status");
                    s.LastUpdateError = Json.Str(lu, "error");
                }
            }
            if (r != null) { s.RequestId = Json.Str(r, "id"); s.RequestOnThisPc = Json.Bool(r, "on_this_pc") == true; }
            if (s.TargetTag != null && !ReleaseNames.IsTag(s.TargetTag)) { s.TargetTag = null; s.StepNeeded = false; }
            return s;
        }
    }
}
