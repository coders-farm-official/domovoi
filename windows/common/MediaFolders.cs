// The household's media folders as the dashboard sees them, and the two
// requests it can make about them (owner, 2026-11-11; docs/WINDOWS_HOST.md,
// "Media folders"; domovoi/media_folders.py on the Linux side).
//
// The distro reaches the media root's six folders through drvfs lines in
// its /etc/fstab, mounted at /srv/media/<name>: the core only ever sees
// those Linux paths. So DomovoiHost tells it, and the tray, which Windows
// folders they are, and carries out a move:
//
//   * the report: /var/lib/domovoi-windows/media.json, written as root on
//     wsl.exe's stdin (as limits.json and side.json are), 0644 in a folder
//     only root can write: the media root, each folder, whether the host
//     can open and move them, and what became of the last move. The core
//     believes it only while root owns it alone.
//   * the tray's list: host\out\media-folders.json, the same folders, which
//     the host makes readable by the household (Users) so the tray, in the
//     signed-in person's session, can map a kind to a folder. The core
//     only ever names a KIND; the tray opens a folder from this list, and
//     only one that passes its own checks (MediaOpen.cs).
//   * a move: ~/.domovoi/media_folders/request.json of the service user,
//     written by the core for an admin, read AS that user (head -c, at most
//     MaxRequestBytes) every HardwareLimits.Every while Domovoi runs. It is
//     only data: Parse takes v, a root, a 32-hex id and the time, refuses
//     anything else, and the root must pass MediaPaths.Problem. Then
//     (MediaPrep.cs, the host's side) the folder is checked on this PC
//     (RootProblem: a local NTFS drive, not a system or profile folder, no
//     link in its path, not longer than MaxRootLength), the six folders
//     are made where missing (Domovoi's account and, on a root it made,
//     the household may read and write them, as Setup does), each is
//     proved writable by this account, the choice is kept in host-state.json
//     ("media"), the distro's media block in /etc/fstab is rewritten as root
//     (FstabScript, the lines provision.sh writes), and Domovoi's Linux
//     system restarts so the mounts and the rooms' players take the new
//     folders: at most one restart per HardwareLimits.RestartGap (shared
//     with the limits), never while domovoi-update.service runs, never for
//     a request older than HardwareLimits.RestartMaxAge, and only once its
//     id is recorded as done (Decide). Files are never moved or deleted.
//
// The boundary, stated plainly (security review 2026-11-11): this lets the
// Linux side's service user (anyone who can write its home, so a
// compromised core too) choose WHICH folders domovoi-svc can reach get
// mounted into the distro, with no Windows administrator approving it.
// What it can't do: reach a folder domovoi-svc can't (the host refuses one
// it can't write, and changes access rules only on folders it makes itself,
// which it owns), mount a system, program or profile folder (checked on
// the canonical long path, 8.3 short names refused), or follow a link.
// The distro's root could already mount any folder domovoi-svc reaches
// with drvfs; this makes that a dashboard choice, so the checks above are
// the real line. docs/SECURITY_PRIVACY.md, "The media folders channel".
//
// Setup stays the owner of the choice it made: the dashboard's root is kept
// with the root Setup's job named when it was made ("based_on"). A repair
// that keeps Setup's answer keeps the dashboard's root (the host gives
// provision.sh the effective --media folders); a Setup run that picks a
// new folder wins, and the dashboard's choice is dropped (Effective).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

namespace Domovoi.Windows
{
    public sealed class MediaRequest
    {
        // none (no file), ok, invalid (Problem says why).
        public string State = "none";
        public string Problem;
        public string Root;
        public string Id;
        public string At;
        public DateTime? AtUtc;
    }

    // What to do about a move request (MediaFolders.Decide).
    public enum MediaMove { None, Invalid, Stale, TooSoon, Busy, Go }

    // The dashboard's media root, as kept in host-state.json ("media").
    public sealed class MediaOverride
    {
        public string Root;
        // The media root Setup's job named when this was chosen.
        public string BasedOn;

        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["root"] = Root;
            d["based_on"] = BasedOn;
            return d;
        }

        public static MediaOverride FromJson(Dictionary<string, object> d)
        {
            if (d == null) return null;
            var o = new MediaOverride();
            o.Root = Json.Str(d, "root");
            o.BasedOn = Json.Str(d, "based_on");
            if (o.Root == null || MediaPaths.Problem(o.Root) != null) return null;
            return o;
        }
    }

    // What became of the last move, as kept in host-state.json
    // ("media_change") and reported.
    public sealed class MediaChange
    {
        public string State = "none";
        public string RequestId;
        public string Root;
        public string Problem;
        public string PreviousRoot;

        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["state"] = State;
            d["request_id"] = RequestId;
            d["root"] = Root;
            d["problem"] = Problem;
            d["previous_root"] = PreviousRoot;
            return d;
        }

        public static MediaChange FromJson(Dictionary<string, object> d)
        {
            var c = new MediaChange();
            if (d == null) return c;
            string s = Json.Str(d, "state");
            c.State = Array.IndexOf(MediaFolders.States, s) >= 0 ? s : "none";
            c.RequestId = Json.Str(d, "request_id");
            c.Root = Json.Str(d, "root");
            c.Problem = Json.Str(d, "problem");
            c.PreviousRoot = Json.Str(d, "previous_root");
            return c;
        }
    }

    // What a drive is, for RootProblem (the host fills it from DriveInfo).
    public sealed class MediaDrive
    {
        public bool Ready;
        public DriveType Type;
        public string Format;
    }

    public static class MediaFolders
    {
        public const string ServiceUser = "domovoi";
        public const string RequestFile = "/home/" + ServiceUser + "/.domovoi/media_folders/request.json";
        public const int MaxRequestBytes = 2048;
        public const int MaxRootLength = 200;
        public const string ReportDir = WindowsSideReport.Dir;
        public const string ReportFile = ReportDir + "/media.json";
        public const string MountBase = "/srv/media";
        public const string TrayMapName = "media-folders.json";
        public const string FstabBegin = "# BEGIN Domovoi media folders (scripts/linux/provision.sh rewrites this block)";
        public const string FstabEnd = "# END Domovoi media folders";

        // The move's states (domovoi/media_folders.py CHANGE_STATES).
        public static readonly string[] States = { "none", "waiting", "busy", "applying", "done", "refused", "failed", "stale" };

        static readonly Regex HexId = new Regex("^[0-9a-f]{32}\\z");
        static readonly Regex AtShape = new Regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\\z");
        static readonly HashSet<string> Keys = new HashSet<string>(StringComparer.Ordinal) { "v", "root", "id", "at" };

        public static string TrayMapPath { get { return Path.Combine(Paths.HostOut, TrayMapName); } }

        // Why a path can't be a media root by its shape, or null.
        public static string ShapeProblem(string root)
        {
            string why = MediaPaths.Problem(root);
            if (why != null) return why;
            if (MediaPaths.Normalize(root).Length > MaxRootLength) return "the path is longer than " + Inv.S(MaxRootLength) + " characters";
            return null;
        }

        // The request file's text (null: no file) into what it asks for.
        public static MediaRequest Parse(string text)
        {
            var r = new MediaRequest();
            if (text == null) return r;
            r.State = "invalid";
            if (text.Length > MaxRequestBytes) { r.Problem = "the request is too large"; return r; }
            Dictionary<string, object> d;
            try { d = Json.ParseObject(text); }
            catch (FormatException) { r.Problem = "the request is not a JSON object"; return r; }
            if (d == null) { r.Problem = "the request is not a JSON object"; return r; }
            foreach (string k in d.Keys)
                if (!Keys.Contains(k)) { r.Problem = "the request has a key it may not have"; return r; }
            if (Json.Int(d, "v") != 1) { r.Problem = "the request's version is not 1"; return r; }
            string id = Json.Str(d, "id");
            if (id == null || !HexId.IsMatch(id)) { r.Problem = "id is not 32 hex digits"; return r; }
            r.Id = id;
            string root = Json.Str(d, "root");
            string why = root == null ? "no folder given" : ShapeProblem(root);
            if (why != null) { r.Problem = why; return r; }
            r.Root = MediaPaths.Normalize(root);
            string at = Json.Str(d, "at");
            DateTime t;
            if (at == null || !AtShape.IsMatch(at)
                || !DateTime.TryParseExact(at, "yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture,
                    DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out t))
            {
                r.Problem = "at is not a UTC time";
                return r;
            }
            r.At = at;
            r.AtUtc = DateTime.SpecifyKind(t, DateTimeKind.Utc);
            r.State = "ok";
            return r;
        }

        // What to do about the request. doneId: the last one acted on
        // (host-state.json); stateReadable: false when that file can't be
        // read, and then nothing is done. lastRestartUtc: the last restart
        // of Domovoi's Linux system for limits or folders. updateState:
        // domovoi-update.service's ActiveState, null when unknown (waits).
        public static MediaMove Decide(MediaRequest req, string doneId, bool stateReadable, DateTime nowUtc,
                                       DateTime lastRestartUtc, string updateState)
        {
            if (req == null || req.Id == null || !stateReadable) return MediaMove.None;
            if (req.Id == doneId) return MediaMove.None;
            if (req.State != "ok") return MediaMove.Invalid;
            if (req.AtUtc == null || nowUtc - req.AtUtc.Value > HardwareLimits.RestartMaxAge || req.AtUtc.Value - nowUtc > TimeSpan.FromMinutes(5))
                return MediaMove.Stale;
            if (nowUtc - lastRestartUtc < HardwareLimits.RestartGap) return MediaMove.TooSoon;
            if (updateState == null || !HardwareLimits.UpdateIdle(updateState)) return MediaMove.Busy;
            return MediaMove.Go;
        }

        // Read the request as the service user (as HardwareLimits.ReadArgs).
        public static string[] ReadArgs(string distro)
        {
            return new[] { "-d", distro, "-u", ServiceUser, "--cd", "/", "--exec", "/usr/bin/timeout", "-s", "KILL", "5",
                           "/usr/bin/head", "-c", Inv.S(MaxRequestBytes + 1), RequestFile };
        }

        // -- the --media arguments ---------------------------------------

        // The job's --media NAME=PATH pairs, in order.
        public static List<KeyValuePair<string, string>> MediaArgs(IList<string> args)
        {
            var list = new List<KeyValuePair<string, string>>();
            if (args == null) return list;
            for (int i = 0; i + 1 < args.Count; i++)
            {
                if (args[i] != "--media") continue;
                string v = args[i + 1];
                int eq = v.IndexOf('=');
                if (eq > 0) list.Add(new KeyValuePair<string, string>(v.Substring(0, eq), v.Substring(eq + 1)));
                i++;
            }
            return list;
        }

        // The arguments with their --media pairs replaced by media, where the
        // first pair was (or before --health-timeout, or at the end).
        public static List<string> WithMedia(IList<string> args, List<KeyValuePair<string, string>> media)
        {
            var outArgs = new List<string>();
            int at = -1;
            for (int i = 0; i < args.Count; i++)
            {
                if (args[i] == "--media" && i + 1 < args.Count)
                {
                    if (at < 0) at = outArgs.Count;
                    i++;
                    continue;
                }
                outArgs.Add(args[i]);
            }
            if (at < 0)
            {
                at = outArgs.IndexOf("--health-timeout");
                if (at < 0) at = outArgs.Count;
            }
            var ins = new List<string>();
            foreach (var m in media) { ins.Add("--media"); ins.Add(m.Key + "=" + m.Value); }
            outArgs.InsertRange(at, ins);
            return outArgs;
        }

        // The media root of Setup's folders (each <root>\<its name>), or null.
        public static string RootOf(List<KeyValuePair<string, string>> media)
        {
            if (media == null || media.Count == 0) return null;
            string root = null;
            foreach (var m in media)
            {
                string want = null;
                foreach (var f in MediaPaths.Folders) if (f[0] == m.Key) want = f[1];
                string p = MediaPaths.Normalize(m.Value);
                int slash = p.LastIndexOf('\\');
                if (want == null || slash < 3 || !string.Equals(p.Substring(slash + 1), want, StringComparison.OrdinalIgnoreCase)) return null;
                string r = p.Substring(0, slash);
                if (root == null) root = r;
                else if (!string.Equals(root, r, StringComparison.OrdinalIgnoreCase)) return null;
            }
            return root;
        }

        // The folders Domovoi uses: the dashboard's root while Setup's job
        // still names the root it was chosen over; Setup's folders
        // otherwise (drop: the dashboard's choice no longer applies).
        public static List<KeyValuePair<string, string>> Effective(IList<string> jobArgs, MediaOverride ov, out bool drop)
        {
            drop = false;
            List<KeyValuePair<string, string>> job = MediaArgs(jobArgs);
            if (ov == null) return job;
            string jobRoot = RootOf(job);
            if (jobRoot == null || ov.BasedOn == null || !string.Equals(jobRoot, ov.BasedOn, StringComparison.OrdinalIgnoreCase))
            {
                drop = true;
                return job;
            }
            return MediaPaths.Under(ov.Root);
        }

        // -- /etc/fstab ----------------------------------------------------

        // provision.sh's fstab_source: C:\Users\Public\Music ->
        // C:/Users/Public/Music, blanks as fstab's octal escapes.
        public static string FstabSource(string winPath)
        {
            string p = MediaPaths.Normalize(winPath);
            string s = char.ToUpperInvariant(p[0]) + ":/" + p.Substring(3).Replace('\\', '/');
            s = s.TrimEnd('/');
            return s.Replace(" ", "\\040").Replace("\t", "\\011");
        }

        // The script's stdin: "SOURCE MOUNTPOINT" per folder. Only names
        // and paths that passed their checks reach it.
        public static string FstabInput(List<KeyValuePair<string, string>> media)
        {
            var sb = new StringBuilder();
            foreach (var m in media)
            {
                bool known = false;
                foreach (var f in MediaPaths.Folders) if (f[0] == m.Key) known = true;
                if (!known) throw new ArgumentException("unknown media folder name: " + m.Key);
                if (ShapeProblem(m.Value) != null) throw new ArgumentException("not a media folder: " + m.Value);
                sb.Append(FstabSource(m.Value)).Append(' ').Append(MountBase).Append('/').Append(m.Key).Append('\n');
            }
            return sb.ToString();
        }

        // /etc/fstab without the Domovoi block. A BEGIN with no END is a
        // stray marker: the marker goes, every line after it stays (they are
        // the household's, not a block) (review 2026-11-11, nit 14).
        public const string FstabKeepAwk = "$0==b{s=1;k=z;next} s&&$0==e{s=0;k=z;next} s{k=k $0 RS;next} {print} END{if(s){ORS=z;print k}}";

        // As root: the Domovoi block of /etc/fstab rewritten from stdin, the
        // rest of the file kept, with the options provision.sh's
        // fstab_lines writes. No double quotes (wsl.exe's command line), no
        // globbing (set -f), and every line checked again.
        public static string FstabScript()
        {
            return "set -euf; umask 022; t=/etc/.fstab.domovoi.$$; trap 'rm -f $t' EXIT; "
                + "u=$(id -u " + ServiceUser + "); g=$(id -g " + ServiceUser + "); "
                + "{ awk -v b='" + FstabBegin + "' -v e='" + FstabEnd + "' '" + FstabKeepAwk + "' /etc/fstab; "
                + "printf '%s\\n' '" + FstabBegin + "'; "
                + "while read -r src mp; do "
                + "case $mp in " + MountBase + "/music|" + MountBase + "/pictures|" + MountBase + "/videos|" + MountBase + "/documents|" + MountBase + "/audiobooks|" + MountBase + "/podcasts) ;; *) exit 3 ;; esac; "
                + "case $src in [A-Z]:/?*) ;; *) exit 3 ;; esac; "
                + "[ -d $mp ] || install -d -m 0755 -o root -g root $mp; "
                + "printf '%s %s drvfs metadata,uid=%s,gid=%s,umask=022,fmask=133,noatime,nofail 0 0\\n' $src $mp $u $g; "
                + "done; printf '%s\\n' '" + FstabEnd + "'; } > $t; "
                + "chmod 0644 $t; mv -f $t /etc/fstab; sha256sum /etc/fstab";
        }

        // -- the report and the tray's list --------------------------------

        static Dictionary<string, object> FolderMap(List<KeyValuePair<string, string>> media)
        {
            var f = new Dictionary<string, object>();
            foreach (var m in media) f[m.Key] = MediaPaths.Normalize(m.Value);
            return f;
        }

        // media.json's text. canOpen: the tray can open them (this host
        // writes its list); canChange: a move can be asked for here.
        public static string Report(List<KeyValuePair<string, string>> media, string source, MediaChange change,
                                    bool canOpen, bool canChange, DateTime utc)
        {
            var d = new Dictionary<string, object>();
            d["schema"] = 1;
            d["host_kind"] = "windows";
            d["root"] = RootOf(media);
            d["mount_base"] = MountBase;
            d["folders"] = FolderMap(media);
            d["source"] = source == "dashboard" ? "dashboard" : "setup";
            d["can_open"] = canOpen;
            d["can_change"] = canChange;
            d["change"] = (change ?? new MediaChange()).ToJson();
            d["written_at"] = utc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            return Json.Serialize(d) + "\n";
        }

        public static string ReportScript()
        {
            return "set -e; umask 022; mkdir -p " + ReportDir + "; chown root:root " + ReportDir + "; chmod 0755 " + ReportDir + "; "
                + "cat > " + ReportDir + "/.media.json.new; chmod 0644 " + ReportDir + "/.media.json.new; "
                + "mv -f " + ReportDir + "/.media.json.new " + ReportFile + "; sha256sum " + ReportFile;
        }

        public static string TrayMap(List<KeyValuePair<string, string>> media, DateTime utc)
        {
            var d = new Dictionary<string, object>();
            d["schema"] = 1;
            d["folders"] = FolderMap(media);
            d["written_at"] = utc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            return Json.Serialize(d) + "\n";
        }

        // The tray's list, each folder's name and shape checked; empty for
        // anything else.
        public static Dictionary<string, string> ParseTrayMap(string text)
        {
            var outMap = new Dictionary<string, string>(StringComparer.Ordinal);
            if (text == null || text.Length > 8192) return outMap;
            Dictionary<string, object> d;
            try { d = Json.ParseObject(text); }
            catch (FormatException) { return outMap; }
            if (d == null || Json.Int(d, "schema") != 1) return outMap;
            Dictionary<string, object> f = Json.Obj(d, "folders");
            if (f == null) return outMap;
            foreach (var kv in MediaPaths.Folders)
            {
                string p = Json.Str(f, kv[0]);
                if (p != null && ShapeProblem(p) == null) outMap[kv[0]] = MediaPaths.Normalize(p);
            }
            return outMap;
        }

        // -- the root on this PC -------------------------------------------

        // Why root can't be the media root on this PC (its shape, its drive,
        // what it is), or null. drive: what its drive is; links: the first
        // part of its path that is a link (junction or symbolic link), or
        // null.
        public static string RootProblem(string root, SystemFolders sys, MediaDrive drive, string link)
        {
            return RootProblem(root, null, sys, drive, link);
        }

        // canonical: root's long, final form on this PC (Canonical), checked
        // against the system folders too; null: root as written.
        // Why a path is refused as a short name: it has a segment with ~ and
        // a digit AND Windows gives it another long name (canonical), so it
        // IS an 8.3 short name (PROGRA~3). A folder really named Music~2024
        // resolves to itself and passes (review round 2, 2026-11-11).
        public static string ShortNameProblem(string path, string canonical)
        {
            string p = MediaPaths.Normalize(path);
            if (p == null || !MediaPaths.ShortName.IsMatch(p)) return null;
            if (canonical == null || string.Equals(MediaPaths.Normalize(canonical), p, StringComparison.OrdinalIgnoreCase)) return null;
            return "write the folder's full name (" + canonical + "), not a short name with ~ and a digit";
        }

        public static string RootProblem(string root, string canonical, SystemFolders sys, MediaDrive drive, string link)
        {
            string why = ShapeProblem(root);
            if (why != null) return why;
            string p = MediaPaths.Normalize(root);
            if (drive == null || !drive.Ready) return "drive " + p.Substring(0, 2) + " isn't there or isn't ready";
            if (drive.Type != DriveType.Fixed && drive.Type != DriveType.Removable)
                return "pick a folder on a local drive of this PC (" + p.Substring(0, 2) + " is a " + drive.Type.ToString().ToLowerInvariant() + " drive)";
            if (!string.Equals(drive.Format, "NTFS", StringComparison.OrdinalIgnoreCase))
                return p.Substring(0, 2) + " is formatted " + (drive.Format ?? "unknown") + "; Domovoi needs an NTFS drive to keep its files' owners";
            why = MediaPaths.SystemProblem(p, sys) ?? ShortNameProblem(p, canonical);
            if (why == null && canonical != null && !string.Equals(canonical, p, StringComparison.OrdinalIgnoreCase))
                why = MediaPaths.Problem(canonical) != null ? canonical + " is not a folder on a drive of this PC" : MediaPaths.SystemProblem(canonical, sys);
            if (why != null) return why;
            if (link != null) return link + " is a link (junction or symbolic link); pick the real folder";
            return null;
        }

        // The folders a media root may not be, be inside or hold, on this PC
        // (Setup's and the host's same list).
        public static SystemFolders SystemFoldersHere()
        {
            var s = new SystemFolders();
            string win = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            string sysDrive = Path.GetPathRoot(win).TrimEnd('\\');
            foreach (string t in new[]
            {
                win,
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                Environment.GetEnvironmentVariable("ProgramW6432"),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                sysDrive + @"\$Recycle.Bin", sysDrive + @"\System Volume Information", sysDrive + @"\Recovery",
            })
                if (!string.IsNullOrEmpty(t) && !s.Trees.Contains(t)) s.Trees.Add(t);
            string profiles = null, defaultProfile = null, publicProfile = null;
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(ProfileListKey))
            {
                if (k != null)
                {
                    profiles = Environment.ExpandEnvironmentVariables(k.GetValue("ProfilesDirectory") as string ?? "");
                    defaultProfile = Environment.ExpandEnvironmentVariables(k.GetValue("Default") as string ?? "");
                    publicProfile = Environment.ExpandEnvironmentVariables(k.GetValue("Public") as string ?? "");
                    foreach (string sid in k.GetSubKeyNames())
                    {
                        using (var p = k.OpenSubKey(sid))
                        {
                            string img = p == null ? null : p.GetValue("ProfileImagePath") as string;
                            if (string.IsNullOrEmpty(img)) continue;
                            img = Environment.ExpandEnvironmentVariables(img);
                            s.Exact.Add(img);
                            s.Trees.Add(Path.Combine(img, "AppData"));
                        }
                    }
                }
            }
            if (string.IsNullOrEmpty(profiles)) profiles = sysDrive + @"\Users";
            s.Exact.Add(profiles);
            s.Exact.Add(string.IsNullOrEmpty(publicProfile) ? Path.Combine(profiles, "Public") : publicProfile);
            s.Trees.Add(Path.Combine(string.IsNullOrEmpty(publicProfile) ? Path.Combine(profiles, "Public") : publicProfile, "AppData"));
            s.Trees.Add(string.IsNullOrEmpty(defaultProfile) ? Path.Combine(profiles, "Default") : defaultProfile);
            s.Trees.Add(Path.Combine(profiles, Paths.AccountName));
            // Folders under the profiles folder with no profile entry (old,
            // half-deleted ones) are still someone's.
            if (Directory.Exists(profiles))
                foreach (string d in Directory.GetDirectories(profiles))
                {
                    if (!s.Exact.Contains(d)) s.Exact.Add(d);
                    if (UninstallFacts.IsDomovoiProfilePath(d) && !s.Trees.Contains(d)) s.Trees.Add(d);
                }
            return s;
        }

        public const string ProfileListKey = @"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList";

        // Why a stored dashboard root can no longer be used (its shape, a
        // system folder by its long name, a link in its path or a folder's),
        // or null. Never its drive: an unplugged USB drive keeps the choice.
        public static string StoredProblem(string root, SystemFolders sys, Func<string, string> canonical, Func<string, string> linkIn)
        {
            string why = ShapeProblem(root);
            if (why != null) return why;
            string p = MediaPaths.Normalize(root);
            string c = canonical(p);
            why = MediaPaths.SystemProblem(p, sys) ?? MediaPaths.SystemProblem(c, sys) ?? ShortNameProblem(p, c);
            if (why != null) return why;
            string link = linkIn(p);
            foreach (var m in MediaPaths.Under(p)) if (link == null) link = linkIn(m.Value);
            return link == null ? null : link + " is a link (junction or symbolic link)";
        }

        // An "applying" whose request is recorded as done can only be one
        // the host stopped in the middle of (a crash, a reboot): it would
        // show forever and hold off every new move (review 2026-11-11, #2).
        public static bool Interrupted(MediaChange c, string doneId)
        {
            return c != null && c.State == "applying" && c.RequestId != null && c.RequestId == doneId;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint size, uint flags);

        // The path's long, final form: the deepest part that exists resolved
        // by Windows (8.3 short names expanded, the real case, a link
        // followed), the rest as written. The path as written when Windows
        // can't say (security review 2026-11-11, #1).
        public static string Canonical(string path)
        {
            string p = MediaPaths.Normalize(path);
            if (p.Length < 3 || p[1] != ':' || p[2] != '\\') return p;
            string existing = p, rest = "";
            while (existing != null && existing.Length > 3 && !Directory.Exists(existing))
            {
                rest = "\\" + Path.GetFileName(existing) + rest;
                existing = Path.GetDirectoryName(existing);
            }
            if (existing == null) return p;
            string final = FinalPath(existing);
            if (final == null) return p;
            return MediaPaths.Normalize(final.TrimEnd('\\') + (rest.Length == 0 && final.Length <= 3 ? "\\" : rest));
        }

        static string FinalPath(string dir)
        {
            // 0x02000000: FILE_FLAG_BACKUP_SEMANTICS (a folder); no access
            // asked, every sharing mode: only its name is read.
            using (SafeFileHandle h = CreateFileW(dir, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero))
            {
                if (h.IsInvalid) return null;
                var sb = new StringBuilder(1024);
                uint n = GetFinalPathNameByHandleW(h, sb, (uint)sb.Capacity, 0);
                if (n == 0 || n >= sb.Capacity) return null;
                string s = sb.ToString();
                if (s.StartsWith(@"\\?\UNC\", StringComparison.Ordinal)) return @"\\" + s.Substring(8);
                if (s.StartsWith(@"\\?\", StringComparison.Ordinal)) return s.Substring(4);
                return s;
            }
        }

        // The first existing part of path (itself, or the nearest folder
        // above it) that is a link, or null.
        public static string LinkIn(string path)
        {
            for (string d = MediaPaths.Normalize(path); d != null && d.Length > 3; d = Path.GetDirectoryName(d))
            {
                if (!Directory.Exists(d)) continue;
                if ((File.GetAttributes(d) & FileAttributes.ReparsePoint) != 0) return d;
            }
            return null;
        }

        public static MediaDrive DriveOf(string path)
        {
            var f = new MediaDrive();
            try
            {
                var di = new DriveInfo(MediaPaths.Normalize(path).Substring(0, 1));
                f.Type = di.DriveType;
                f.Ready = di.IsReady;
                if (f.Ready) f.Format = di.DriveFormat;
            }
            catch (Exception e)
            {
                if (!(e is IOException || e is ArgumentException || e is UnauthorizedAccessException)) throw;
                f.Ready = false;
            }
            return f;
        }
    }
}
