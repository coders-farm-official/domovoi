// "Open on the Domovoi PC" for the media folders (Settings > Household >
// Media folders; domovoi/media_folders.py, common\MediaFolders.cs).
//
// DomovoiHost runs as a service account in session 0 and can open no window
// a person sees, so the tray, in the signed-in person's session, does it.
// With its status poll (every 5 s) it asks the core over loopback
// (GET /v1/windows/media/open) for a request an admin made from a browser
// on this PC. The core names only a media KIND (music, videos, ...), never
// a path. The tray:
//
//   * acts only in the session at this PC's screen (the active console
//     session), so a second signed-in account's tray doesn't open a window
//     nobody sees, and claims the request first (one tray only);
//   * maps the kind to a folder from DomovoiHost's own list
//     (host\out\media-folders.json, MediaFolders.ParseTrayMap: known names,
//     drive paths only);
//   * opens it only if it is a folder that exists, on a drive of this PC,
//     with no link (junction or symbolic link) in its path and not a system
//     or profile folder (MediaFolders.RootProblem's rules), with
//     explorer.exe and the folder as its one argument;
//   * reports what happened (opened, missing, refused, failed) for the page.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

namespace Domovoi.Windows.Tray
{
    public sealed class MediaOpenRequest
    {
        public string Id;
        public string Kind;
    }

    public interface IMediaServer
    {
        MediaOpenRequest Pending();
        // The claim's token (its result must carry it), or null when
        // another tray took it or it is gone.
        string Claim(string id);
        void Report(string id, string claim, string outcome, string detail);
    }

    // What the tray may look at on this PC (the tests fake it).
    public sealed class MediaOpenEnv
    {
        public bool ActiveSession;
        public Func<string> ReadMap;
        public Func<string, bool> FolderExists;
        public Func<string, string> LinkIn;
        public Func<SystemFolders> System;
        // The folder's long, final name (MediaFolders.Canonical).
        public Func<string, string> Canonical;
        public Action<string> Open;
        public Action<string> Log;
    }

    public static class MediaOpen
    {
        public const string PollPath = "/v1/windows/media/open";
        public const string ClaimPath = PollPath + "/claim";
        public const string ResultPath = PollPath + "/result";

        public const string Opened = "opened";
        public const string Missing = "missing";
        public const string Refused = "refused";
        public const string Failed = "failed";

        // The folder to open for kind, or null with the outcome and why.
        public static string Folder(string kind, MediaOpenEnv env, out string outcome, out string detail)
        {
            outcome = Refused;
            detail = null;
            Dictionary<string, string> map = MediaFolders.ParseTrayMap(env.ReadMap());
            string path;
            if (kind == null || !map.TryGetValue(kind, out path))
            {
                detail = "this PC's Domovoi has no " + (kind ?? "such") + " folder on its list";
                return null;
            }
            if (!env.FolderExists(path))
            {
                outcome = Missing;
                detail = path + " isn't there (is its drive connected?)";
                return null;
            }
            string link = env.LinkIn(path);
            if (link != null) { detail = link + " is a link; not opened"; return null; }
            SystemFolders sys = env.System();
            string why = MediaPaths.SystemProblem(path, sys);
            // By its long, final name too (an 8.3 short name, a link).
            string canonical = env.Canonical(path);
            if (why == null && MediaPaths.Problem(canonical) != null) why = canonical + " is not a folder on a drive of this PC";
            if (why == null) why = MediaPaths.SystemProblem(canonical, sys);
            if (why != null) { detail = why; return null; }
            outcome = Opened;
            return path;
        }

        // One poll: nothing unless there is a request and this is the
        // session at the screen; then claim, check, open, report.
        public static string Run(IMediaServer server, MediaOpenEnv env)
        {
            if (!env.ActiveSession) return null;
            MediaOpenRequest req = server.Pending();
            if (req == null) return null;
            string claim = server.Claim(req.Id);
            if (claim == null) return null;
            string outcome, detail;
            string path = Folder(req.Kind, env, out outcome, out detail);
            if (path != null)
            {
                try
                {
                    env.Open(path);
                    detail = path;
                }
                catch (Exception e)
                {
                    if (!(e is System.ComponentModel.Win32Exception || e is IOException || e is InvalidOperationException)) throw;
                    outcome = Failed;
                    detail = "File Explorer didn't start: " + e.Message;
                }
            }
            env.Log("media folders: " + outcome + " (" + (req.Kind ?? "?") + "): " + detail);
            server.Report(req.Id, claim, outcome, detail);
            return outcome;
        }

        [DllImport("kernel32.dll")]
        static extern uint WTSGetActiveConsoleSessionId();

        public static bool AtTheScreen()
        {
            try { return WTSGetActiveConsoleSessionId() == (uint)Process.GetCurrentProcess().SessionId; }
            catch (Exception e)
            {
                if (!(e is InvalidOperationException || e is EntryPointNotFoundException)) throw;
                return false;
            }
        }

        // The real environment.
        public static MediaOpenEnv Here(Action<string> log)
        {
            var env = new MediaOpenEnv();
            env.ActiveSession = AtTheScreen();
            env.ReadMap = delegate
            {
                try
                {
                    var fi = new FileInfo(MediaFolders.TrayMapPath);
                    if (!fi.Exists || fi.Length > 8192) return null;
                    return File.ReadAllText(fi.FullName);
                }
                catch (IOException) { return null; }
                catch (UnauthorizedAccessException) { return null; }
            };
            env.FolderExists = delegate (string p)
            {
                var d = MediaFolders.DriveOf(p);
                return d.Ready && (d.Type == DriveType.Fixed || d.Type == DriveType.Removable) && Directory.Exists(p);
            };
            env.LinkIn = MediaFolders.LinkIn;
            env.System = MediaFolders.SystemFoldersHere;
            env.Canonical = MediaFolders.Canonical;
            env.Open = delegate (string p)
            {
                string explorer = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe");
                using (Process.Start(new ProcessStartInfo(explorer, "\"" + p.TrimEnd('\\') + "\\\"") { UseShellExecute = false })) { }
            };
            env.Log = log;
            return env;
        }
    }

    public sealed class HttpMediaServer : IMediaServer
    {
        readonly string core;

        public HttpMediaServer(string coreUrl) { core = Probe.Loopback((coreUrl ?? Loopback.Core).TrimEnd('/')); }

        public MediaOpenRequest Pending()
        {
            JsonReply r = Loopback.Send("GET", core + MediaOpen.PollPath, null, 5000);
            if (r == null || r.Status != 200) return null;
            Dictionary<string, object> q = Json.Obj(r.Body, "request");
            if (q == null) return null;
            var req = new MediaOpenRequest { Id = Json.Str(q, "id"), Kind = Json.Str(q, "kind") };
            return req.Id == null || req.Id.Length > 64 ? null : req;
        }

        public string Claim(string id)
        {
            JsonReply r = Loopback.Send("POST", core + MediaOpen.ClaimPath, "{\"request_id\": " + Loopback.Quote(id) + "}", 5000);
            if (r == null || r.Status != 200) return null;
            string token = Json.Str(r.Body, "claim");
            return token != null && token.Length <= 64 ? token : null;
        }

        public void Report(string id, string claim, string outcome, string detail)
        {
            if (detail != null && detail.Length > 300) detail = detail.Substring(0, 300);
            Loopback.Send("POST", core + MediaOpen.ResultPath,
                "{\"request_id\": " + Loopback.Quote(id) + ", \"claim\": " + Loopback.Quote(claim)
                + ", \"outcome\": " + Loopback.Quote(outcome)
                + ", \"detail\": " + (detail == null ? "null" : Loopback.Quote(detail)) + "}", 5000);
        }
    }
}
