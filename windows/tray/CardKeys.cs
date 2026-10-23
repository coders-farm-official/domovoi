// The setup network's password for a Wi-Fi setup card, fetched from the
// Domovoi on this PC so nobody types it (owner request 2026-10-22).
//
// The dashboard puts a one-time pass in the card files (domovoi/claim.json:
// the build's number, a random secret, and when it stops working, a day
// after the build). The helper trades it once for the password, over this
// PC's loopback only: POST /api/satellites/media/claim, which the server
// answers to a loopback peer only (web/backend/api/satellite_media.py).
// The password is kept in this window's memory and written nowhere but
// the card's domovoi/ap.json.
//
// When that can't work (the pass was used, it expired, the server
// restarted and forgot the password, the server doesn't answer, or the
// files are from before passes existed) the helper says which, in plain
// words, and offers an admin sign-in: the dashboard's own sign-in
// (POST /api/auth/login), then the build's credentials
// (GET /api/satellites/media/jobs/{id}/credentials), or a new password for
// the same network when the server forgot it
// (POST /api/satellites/media/jobs/{id}/setup-key), then sign-out. A new
// password comes with a new console login too: the zip's console password
// exists only as a hash, so the server sends the matching userconf.txt line
// and the card writer puts it on the card in place of the zip's. The admin
// password is held only for that one call. Typing the password the
// dashboard showed stays the last way.

using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;

namespace Domovoi.Windows.Tray
{
    public sealed class CardClaim
    {
        public long JobId;
        public string Secret;
        public DateTimeOffset? ExpiresAt;
    }

    public enum KeyProblem
    {
        None,
        NoClaim,        // older card files: no pass in them
        Used,
        Expired,
        Forgotten,      // the server restarted since the card was prepared
        Unreachable,
        Refused,        // the server said no for another reason
        WrongPassword,  // the admin sign-in was refused (Detail: the server's words)
        SignInRefused,  // any other sign-in refusal (Detail: the server's words)
        Unrecoverable,  // signed in, and the server can't make a password for these files
    }

    public sealed class KeyAnswer
    {
        public string Psk;
        public bool Reissued;       // a new password the server made: the dashboard's old one is wrong now
        public KeyProblem Problem;
        public string Detail;       // the server's words, for a sign-in refusal
        // The console login to tell the person (null: the dashboard's still
        // applies, or none could be made), and, with a new one, the
        // userconf.txt line that carries its hash (written in place of the zip's).
        public string ConsoleUser, ConsolePassword, Userconf;

        public bool Ok { get { return Psk != null; } }

        public static KeyAnswer Got(string psk, bool reissued) { return new KeyAnswer { Psk = psk, Reissued = reissued }; }
        public static KeyAnswer Fail(KeyProblem p, string detail = null) { return new KeyAnswer { Problem = p, Detail = detail }; }
    }

    // The server side, behind an interface so the tests can answer for it.
    public interface ICardKeys
    {
        KeyAnswer Claim(CardClaim claim);
        KeyAnswer AdminFetch(long jobId, string ssid, string adminPassword);
    }

    public static class CardKeyWords
    {
        public const string Used = "This card's one-time pass was already used.";
        public const string Expired = "This card was prepared more than a day ago, so its pass expired.";
        public const string Forgotten = "Your Domovoi server restarted since this card was prepared, so it no longer has the password.";
        public const string Unreachable = "Can't reach your Domovoi server.";
        public const string NoClaim = "These card files are from an older Domovoi, so they don't carry a one-time pass.";
        public const string Refused = "Your Domovoi server didn't accept this card's one-time pass.";
        public const string Unrecoverable = "Your Domovoi server can't recover this card's password. Prepare the card again on the dashboard.";

        public const string SignInOrPrepare = "Sign in as admin to fetch it, or prepare the card again on the dashboard.";
        public const string CheckRunning = "Check that Domovoi is running (its tray icon says so), then try again.";
        public const string OrType = "Or type the password the dashboard showed.";

        // Why the helper couldn't fetch it, then what to do next.
        public static string Reason(KeyAnswer a)
        {
            switch (a.Problem)
            {
                case KeyProblem.Used: return Used;
                case KeyProblem.Expired: return Expired;
                case KeyProblem.Forgotten: return Forgotten;
                case KeyProblem.Unreachable: return Unreachable;
                case KeyProblem.NoClaim: return NoClaim;
                case KeyProblem.Unrecoverable: return Unrecoverable;
                // The dashboard's sign-in shows the server's own words ("wrong password").
                case KeyProblem.WrongPassword:
                case KeyProblem.SignInRefused: return string.IsNullOrEmpty(a.Detail) ? "wrong password" : a.Detail;
                default: return Refused;
            }
        }

        public static string NextStep(KeyAnswer a)
        {
            switch (a.Problem)
            {
                case KeyProblem.Unreachable: return CheckRunning + " " + OrType;
                case KeyProblem.Unrecoverable: return OrType;
                case KeyProblem.WrongPassword:
                case KeyProblem.SignInRefused: return "Try the admin password again. " + OrType;
                default: return SignInOrPrepare + " " + OrType;
            }
        }
    }

    public static class CardKeyFlow
    {
        // The pass traded for the password, or why not. Checked here first:
        // files with no pass, and a pass past its time (no need to ask).
        public static KeyAnswer Fetch(ICardKeys keys, CardPackage pkg, DateTimeOffset now)
        {
            if (pkg.Claim == null) return KeyAnswer.Fail(KeyProblem.NoClaim);
            if (pkg.Claim.ExpiresAt.HasValue && now >= pkg.Claim.ExpiresAt.Value) return KeyAnswer.Fail(KeyProblem.Expired);
            return Checked(keys.Claim(pkg.Claim));
        }

        public static KeyAnswer FetchAsAdmin(ICardKeys keys, CardPackage pkg, string adminPassword)
        {
            if (pkg.Claim == null) return KeyAnswer.Fail(KeyProblem.Unrecoverable);
            return Checked(keys.AdminFetch(pkg.Claim.JobId, pkg.ApSsid, adminPassword));
        }

        // A password the card can't use is no password.
        static KeyAnswer Checked(KeyAnswer a)
        {
            if (a == null) return KeyAnswer.Fail(KeyProblem.Unreachable);
            if (a.Ok && SetupPassword.Problem(a.Psk) != null) return KeyAnswer.Fail(KeyProblem.Refused);
            if (a.Ok && a.Userconf != null && !UserconfLine.IsMatch(a.Userconf)) return KeyAnswer.Fail(KeyProblem.Refused);
            return a;
        }

        // user:$6$salt$hash and a newline: what overlay.write_overlay writes,
        // and the only shape Pi OS's first boot reads (a bad one locks the
        // account silently).
        public static readonly System.Text.RegularExpressions.Regex UserconfLine =
            new System.Text.RegularExpressions.Regex(@"^[a-z_][a-z0-9_-]{0,31}:\$6\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}\n\z");

        public static byte[] UserconfBytes(string line)
        {
            return new UTF8Encoding(false).GetBytes(line);
        }
    }

    // The real server: the dashboard on this PC's loopback.
    public sealed class HttpCardKeys : ICardKeys
    {
        readonly string web;
        public const int TimeoutMs = 15000;

        public HttpCardKeys(string webUrl) { web = Probe.Loopback((webUrl ?? "").TrimEnd('/')); }

        public KeyAnswer Claim(CardClaim claim)
        {
            Reply r = Send("POST", "/api/satellites/media/claim",
                "{\"job_id\": " + Inv.S(claim.JobId) + ", \"secret\": " + Quote(claim.Secret) + "}", null);
            if (r == null) return KeyAnswer.Fail(KeyProblem.Unreachable);
            if (r.Status == 200) return Psk(r, KeyProblem.Refused, false);
            switch (Json.Str(r.Body, "reason"))
            {
                case "used": return KeyAnswer.Fail(KeyProblem.Used);
                case "expired": return KeyAnswer.Fail(KeyProblem.Expired);
                case "forgotten": return KeyAnswer.Fail(KeyProblem.Forgotten);
                default: return KeyAnswer.Fail(KeyProblem.Refused);
            }
        }

        public KeyAnswer AdminFetch(long jobId, string ssid, string adminPassword)
        {
            Reply login = Send("POST", "/api/auth/login",
                "{\"password\": " + Quote(adminPassword) + ", \"label\": \"tray card helper\"}", null);
            if (login == null) return KeyAnswer.Fail(KeyProblem.Unreachable);
            string token = null;
            if (login.Status == 200) token = Json.Str(login.Body, "token");
            else if (login.Status == 401) return KeyAnswer.Fail(KeyProblem.WrongPassword, Json.Str(login.Body, "detail"));
            // 409: no admin password set yet; the routes are open until then.
            else if (login.Status != 409) return KeyAnswer.Fail(KeyProblem.SignInRefused, Json.Str(login.Body, "detail") ?? ("the server answered " + Inv.S(login.Status)));
            try
            {
                string path = "/api/satellites/media/jobs/" + Inv.S(jobId);
                Reply got = Send("GET", path + "/credentials", null, token);
                if (got == null) return KeyAnswer.Fail(KeyProblem.Unreachable);
                // The build's own (not made again): the zip's userconf.txt
                // already matches its console login.
                if (got.Status == 200 && Json.Str(Json.Obj(got.Body, "ap"), "ssid") == ssid && Json.Bool(got.Body, "reissued") != true)
                    return Psk(got, KeyProblem.Unrecoverable, false);
                if (got.Status != 200 && got.Status != 404) return KeyAnswer.Fail(KeyProblem.SignInRefused, Json.Str(got.Body, "detail"));
                // Forgotten (404), made again earlier (its userconf.txt line is
                // only in setup-key's answer), or no such network: ask setup-key.
                Reply made = Send("POST", path + "/setup-key", "{\"ssid\": " + Quote(ssid) + "}", token);
                if (made == null) return KeyAnswer.Fail(KeyProblem.Unreachable);
                if (made.Status == 200) return Psk(made, KeyProblem.Unrecoverable, Json.Bool(made.Body, "reissued") == true);
                return KeyAnswer.Fail(KeyProblem.Unrecoverable);
            }
            finally
            {
                // The session was for this one call: ended at once.
                if (token != null) Send("POST", "/api/auth/logout", "{}", token);
            }
        }

        static KeyAnswer Psk(Reply r, KeyProblem otherwise, bool reissued)
        {
            string psk = Json.Str(Json.Obj(r.Body, "ap"), "psk");
            if (psk == null) return KeyAnswer.Fail(otherwise);
            KeyAnswer a = KeyAnswer.Got(psk, reissued);
            Dictionary<string, object> console = Json.Obj(r.Body, "console");
            a.ConsoleUser = Json.Str(console, "username");
            a.ConsolePassword = Json.Str(console, "password");
            if (a.ConsoleUser == null || a.ConsolePassword == null) a.ConsoleUser = a.ConsolePassword = null;
            a.Userconf = Json.Str(r.Body, "userconf");
            return a;
        }

        sealed class Reply
        {
            public int Status;
            public Dictionary<string, object> Body = new Dictionary<string, object>();
        }

        // Null when the server didn't answer at all.
        Reply Send(string method, string path, string json, string bearer)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(web + path);
                req.Method = method;
                req.Timeout = TimeoutMs;
                req.ReadWriteTimeout = TimeoutMs;
                req.Proxy = null;              // straight to loopback, never through a proxy
                req.AllowAutoRedirect = false;
                req.Accept = "application/json";
                req.UserAgent = "DomovoiTray";
                // The dashboard's write guard (WEB-6) wants it on every write.
                req.Headers["X-Requested-With"] = "DomovoiTray";
                if (bearer != null) req.Headers[HttpRequestHeader.Authorization] = "Bearer " + bearer;
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
                    var reply = new Reply { Status = (int)resp.StatusCode };
                    char[] buf = new char[65536];
                    int n = rd.ReadBlock(buf, 0, buf.Length);
                    try { reply.Body = Json.ParseObject(new string(buf, 0, n)); }
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
}
