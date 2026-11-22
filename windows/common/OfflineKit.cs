// Installing from an offline kit (release 1.5.0; docs/OFFLINE_KIT.md and
// docs/WINDOWS_HOST.md, "Installing without internet"). A house that is
// never online gets a USB stick made by an online Domovoi server; Setup on
// that stick (release\DomovoiSetup.exe) sees kit.json beside it, or the
// household points it at the stick, and installs from it with no internet.
//
// Before Setup changes anything, OfflineKit.Check (the helper's kit-check)
// decides whether this kit may be used, in this order:
//   1. The stick's SHA256SUMS for the files the trust chain reads (kit.json,
//      the release's signed manifest and its signature). Only a bad copy or
//      a dying stick is caught here; it is not trust.
//   2. kit.json: a kit format this Setup reads, made for a PC (not a Mac
//      only).
//   3. The release signature: release\DomovoiSetup.manifest.json signed with
//      the release key this Setup pins (SshSig, the same check as the
//      Windows update), naming the tag and commit kit.json names, and that
//      commit the one this Setup installs (pins.iss DomovoiCommit): the kit
//      and this Setup are the same release.
//   4. The parts: what Setup takes from the kit (Ubuntu's WSL image, WSL's
//      MSI when this PC needs it, provision.sh) listed with this Setup's own
//      pins, and what provision.sh --offline-kit needs every time (the same
//      list as its kit_check).
//   5. The disk: room for what the kit puts into Domovoi's Linux system.
//   6. Every file SHA256SUMS lists, hashed (a few minutes on a USB 3 stick).
// What is then used is checked against its pin again where it is used: the
// helper's job step copies the image and provision.sh only when they match
// their pins, Setup checks WSL's MSI before msiexec runs it, and inside the
// distro provision.sh checks every file against the pins in the checkout at
// that commit, as P2's Linux install does.
//
// DomovoiHost mounts the kit inside the distro, read-only, with drvfs at
// MountPoint, right before each provision.sh run, and unmounts it after.
//
// NetGuard: in kit mode nothing may reach beyond this PC. Every place the
// Windows side opens a connection asks it first, and it throws, loudly,
// for anything that isn't loopback.

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class NetGuard
    {
        // Set in kit mode: DomovoiSetupHelper --offline, and DomovoiHost
        // while it provisions from a kit.
        public static volatile bool Offline;

        public const string Fault = " would use the internet, and this install is from an offline kit: nothing may. This is a fault in Domovoi Setup; please report it (https://github.com/coders-farm-official/domovoi/issues). Nothing was downloaded.";

        public static bool IsLoopback(string host)
        {
            if (host == null) return false;
            string h = host.Trim().TrimStart('[').TrimEnd(']').ToLowerInvariant();
            return h == "127.0.0.1" || h == "localhost" || h == "::1";
        }

        // A URL about to be opened. Throws in kit mode unless it is this PC.
        public static void Check(string url)
        {
            if (!Offline) return;
            Uri u;
            if (Uri.TryCreate(url ?? "", UriKind.Absolute, out u) && IsLoopback(u.Host)) return;
            throw new InvalidOperationException(Safe(url) + Fault);
        }

        // A host about to be dialled.
        public static void CheckHost(string host)
        {
            if (!Offline || IsLoopback(host)) return;
            throw new InvalidOperationException(Safe(host) + Fault);
        }

        static string Safe(string s)
        {
            if (string.IsNullOrEmpty(s)) return "a connection";
            return s.Length > 120 ? s.Substring(0, 120) : s;
        }
    }

    // What this Setup asks of a kit: its own release and pins.
    public sealed class KitExpectation
    {
        public string Commit;             // pins.iss DomovoiCommit
        public string Version;            // this Setup's version, for the words only
        public byte[] Key;                // the release key this build pins
        public string UbuntuName, UbuntuSha256;
        public string WslMsiName, WslMsiSha256;
        public bool NeedWsl;              // this PC gets WSL from the kit
        public string ProvisionSha256;    // null: this build carries its own provision.sh
        public long FreeBytes = -1;       // free on the drive Domovoi's Linux system goes on; -1 unknown
        public string FreeDrive = "C:\\";
        public int MinFreeGb = 40;        // what any install needs (helper Preflight.MinFreeGb)
    }

    public sealed class KitReport
    {
        public string Root, Version, Tag, Commit;
        public bool Ai;
        public int Files;
        public long Bytes, PayloadBytes, NeedGb;
        public List<string> Problems = new List<string>();
        public bool Ok { get { return Problems.Count == 0; } }
    }

    // kit.json, as Setup reads it: the fields it needs, every file with its
    // size and SHA-256, and each component's kind.
    public sealed class KitIndex
    {
        public sealed class Entry { public string Path, Sha256; public long Size; public int Parts; }

        public int? Format;
        public string Version, Tag, Commit;
        public List<string> Targets = new List<string>();
        public List<string> Includes = new List<string>();
        public Dictionary<string, Entry> Files = new Dictionary<string, Entry>(StringComparer.Ordinal);
        public HashSet<string> Kinds = new HashSet<string>(StringComparer.Ordinal);

        public static KitIndex Parse(string text)
        {
            Dictionary<string, object> d = Json.ParseObject(text);
            var k = new KitIndex();
            k.Format = Json.Int(d, "kit_format");
            k.Version = Json.Str(d, "domovoi_version");
            k.Tag = Json.Str(d, "tag");
            k.Commit = Json.Str(d, "commit");
            k.Targets = Json.StrList(d, "targets") ?? new List<string>();
            k.Includes = Json.StrList(d, "includes") ?? new List<string>();
            foreach (var c in Json.ObjList(d, "components") ?? new List<Dictionary<string, object>>())
            {
                string kind = Json.Str(c, "kind");
                if (kind != null) k.Kinds.Add(kind);
                foreach (var f in Json.ObjList(c, "files") ?? new List<Dictionary<string, object>>())
                {
                    string p = Json.Str(f, "path");
                    if (!OfflineKit.SafePath(p) || k.Files.ContainsKey(p)) continue;
                    var parts = Json.ObjList(f, "parts");
                    k.Files[p] = new Entry { Path = p, Sha256 = Json.Str(f, "sha256"), Size = Json.Long(f, "size") ?? -1, Parts = parts == null ? 0 : parts.Count };
                }
            }
            return k;
        }

        public bool Has(string path) { return Files.ContainsKey(path); }

        public bool HasUnder(string prefix)
        {
            foreach (string p in Files.Keys) if (p.StartsWith(prefix, StringComparison.Ordinal)) return true;
            return false;
        }
    }

    public static class OfflineKit
    {
        // The kit format this Setup reads (domovoi/offline_kit/format.py KIT_FORMAT).
        public const int FormatRead = 1;
        // Where DomovoiHost mounts the kit inside the distro (provision.sh --offline-kit).
        public const string MountPoint = "/mnt/domovoi-kit";
        public const string KitJson = "kit.json";
        public const string Sums = "SHA256SUMS";
        public const string PcTarget = "pc-x86_64";
        public const string MacTarget = "mac-arm64";
        public const string ManifestPath = "release/" + ReleaseNames.Manifest;
        public const string ManifestSigPath = "release/" + ReleaseNames.ManifestSig;
        public const string ProvisionPath = "linux/provision.sh";
        public const string BundlePath = "source/domovoi.bundle";
        public const string Remake = " Make the stick again.";

        // What the kit holds for Windows, the Mac and the rooms, not for the
        // Linux system: left out of the disk estimate.
        static readonly string[] NotInTheDistro = { "windows/", "mac/", "rooms/", "release/", "licences/", "linux/" };

        static readonly Regex Hex40 = new Regex("^[0-9a-f]{40}\\z");
        static readonly Regex SumsLine = new Regex("^([0-9a-f]{64}) [ *](.+)\\z");
        static readonly Regex DriveOnly = new Regex("^[A-Za-z]:\\\\?\\z");

        // A kit path as kit.json and SHA256SUMS write them: relative, "/"
        // between names, no "." or ".." and nothing Windows reads as a drive.
        public static bool SafePath(string p)
        {
            if (string.IsNullOrEmpty(p) || p.StartsWith("/", StringComparison.Ordinal) || p.IndexOf('\\') >= 0 || p.IndexOf(':') >= 0) return false;
            foreach (char c in p) if (c < ' ') return false;
            foreach (string s in p.Split('/')) if (s.Length == 0 || s == "." || s == "..") return false;
            return true;
        }

        // The folder the household gave (or Setup found), as one full path:
        // "E:" and "E:\" are the drive's root, any other folder without the
        // trailing backslash. Null for anything that isn't a full path.
        public static string NormalizeRoot(string path)
        {
            if (string.IsNullOrEmpty(path)) return null;
            string p = path.Trim().Trim('"');
            if (DriveOnly.IsMatch(p)) return p.Substring(0, 2).ToUpperInvariant() + "\\";
            if (!Path.IsPathRooted(p)) return null;
            try { p = Path.GetFullPath(p); }
            catch (Exception e) { if (e is ArgumentException || e is NotSupportedException || e is PathTooLongException) return null; throw; }
            if (DriveOnly.IsMatch(p)) return p.Substring(0, 2).ToUpperInvariant() + "\\";
            return p.TrimEnd('\\');
        }

        // The kit beside a Setup in setupDir: the stick's release\ folder
        // (the kit is its parent) or the kit's own folder. Null: none.
        public static string Beside(string setupDir, Func<string, bool> fileExists)
        {
            string dir = NormalizeRoot(setupDir);
            if (dir == null) return null;
            if (fileExists(Path.Combine(dir, KitJson))) return dir;
            DirectoryInfo parent = Directory.GetParent(dir.TrimEnd('\\') + "\\");
            parent = parent == null ? null : parent.Parent;
            if (parent != null && fileExists(Path.Combine(parent.FullName, KitJson))) return NormalizeRoot(parent.FullName);
            return null;
        }

        // The kit's folder as drvfs takes it: "E:" for a drive's root,
        // "D:/kits/DOMOVOIKIT" for a folder. A network path is refused:
        // DomovoiHost runs as its own account, which has no mapped drives.
        public static string DrvfsSource(string root)
        {
            string r = NormalizeRoot(root);
            if (r == null || r.Length < 3 || r[1] != ':' || r[2] != '\\')
                throw new ArgumentException("the offline kit must be a folder on a drive of this PC: " + root);
            foreach (char c in r) if (c < ' ' || c == '"' || c == '\'' || c == '`' || c == '$') throw new ArgumentException("the offline kit's folder has a character Setup can't hand on: " + root);
            if (r.Length == 3) return r.Substring(0, 2);
            return r.Replace('\\', '/');
        }

        // Run as root in the distro with the drvfs source on stdin (one
        // line): mounts the kit read-only at MountPoint, afresh. Exit 3: a
        // source that isn't a drive path; 4: drvfs couldn't mount it (the
        // stick isn't plugged in, or the drive letter changed); 5: mounted,
        // but there is no kit.json this account can read there.
        public static string MountScript()
        {
            return "read -r src || exit 3; mp=" + MountPoint + "; "
                + "case $src in [A-Z]:|[A-Z]:/?*) ;; *) exit 3 ;; esac; "
                + "if grep -qs \" $mp \" /proc/mounts; then umount \"$mp\" 2>/dev/null || umount -l \"$mp\" 2>/dev/null; fi; "
                + "install -d -m 0755 \"$mp\" || exit 4; "
                + "mount -t drvfs \"$src\" \"$mp\" -o ro,noatime || exit 4; "
                + "[ -r \"$mp/" + KitJson + "\" ] || exit 5; echo mounted";
        }

        public static string UnmountScript()
        {
            return "mp=" + MountPoint + "; "
                + "if grep -qs \" $mp \" /proc/mounts; then umount \"$mp\" 2>/dev/null || umount -l \"$mp\" 2>/dev/null; fi; "
                + "rmdir \"$mp\" 2>/dev/null; exit 0";
        }

        // Why the mount failed, from MountScript's exit code, in words.
        public static string MountProblem(int exitCode, string root)
        {
            if (exitCode == 4)
                return "Domovoi couldn't open the offline kit at " + root + " (is the stick still plugged in, with the same drive letter?). Plug it in and try again.";
            if (exitCode == 5)
                return "Domovoi's service account can't read the offline kit at " + root + ". Use the kit on its USB stick, or copy it to a folder every account on this PC can read, then try again.";
            return "Domovoi couldn't open the offline kit at " + root + " (exit " + Inv.S(exitCode) + ").";
        }

        // What an install from this kit needs free on the drive of
        // Domovoi's Linux system: about twice what the kit holds for it (a
        // copy off the stick, then the installed files: images loaded,
        // packages unpacked) plus 10 GB, and never less than any install.
        public static long NeedGb(long payloadBytes, int minFreeGb)
        {
            long gib = 1024L * 1024 * 1024;
            long need = (2 * Math.Max(0, payloadBytes) + gib - 1) / gib + 10;
            return Math.Max(minFreeGb, need);
        }

        public static Dictionary<string, string> ParseSums(string text, out string problem)
        {
            problem = null;
            var d = new Dictionary<string, string>(StringComparer.Ordinal);
            int n = 0;
            foreach (string raw in (text ?? "").Split('\n'))
            {
                n++;
                string line = raw.TrimEnd('\r');
                if (line.Trim().Length == 0) continue;
                Match m = SumsLine.Match(line);
                if (!m.Success || !SafePath(m.Groups[2].Value) || d.ContainsKey(m.Groups[2].Value))
                {
                    problem = Sums + " line " + Inv.S(n) + " can't be read";
                    return null;
                }
                d[m.Groups[2].Value] = m.Groups[1].Value;
            }
            if (d.Count == 0) problem = Sums + " lists nothing";
            return problem == null ? d : null;
        }

        static string Local(string root, string kitPath)
        {
            return Path.Combine(root, kitPath.Replace('/', '\\'));
        }

        // The SHA256SUMS lines for one kit file: itself, or its parts.
        static List<string> Listed(Dictionary<string, string> sums, string path)
        {
            var l = new List<string>();
            if (sums.ContainsKey(path)) { l.Add(path); return l; }
            string prefix = path + ".part";
            foreach (string p in sums.Keys)
                if (p.StartsWith(prefix, StringComparison.Ordinal) && Regex.IsMatch(p.Substring(prefix.Length), "^[0-9]{2,3}\\z")) l.Add(p);
            l.Sort(StringComparer.Ordinal);
            return l;
        }

        // The files SHA256SUMS names that are missing or don't match, as kit paths.
        static List<string> Damaged(string root, Dictionary<string, string> sums, IEnumerable<string> paths, ref long bytes)
        {
            var bad = new List<string>();
            foreach (string p in paths)
            {
                string f = Local(root, p);
                try
                {
                    if (!File.Exists(f)) { bad.Add(p + " (missing)"); continue; }
                    bytes += new FileInfo(f).Length;
                    if (Sha256.OfFile(f) != sums[p]) bad.Add(p);
                }
                catch (IOException) { bad.Add(p + " (can't be read)"); }
                catch (UnauthorizedAccessException) { bad.Add(p + " (can't be read)"); }
            }
            return bad;
        }

        static string DamagedText(List<string> bad)
        {
            string s = string.Join(", ", bad.GetRange(0, Math.Min(3, bad.Count)).ToArray());
            if (bad.Count > 3) s += " and " + Inv.S(bad.Count - 3) + " more";
            return "This stick has a damaged file: " + s + "." + Remake;
        }

        static bool IsNetworkPath(string root)
        {
            if (root.StartsWith("\\\\", StringComparison.Ordinal)) return true;
            try { return new DriveInfo(root.Substring(0, 1)).DriveType == DriveType.Network; }
            catch (ArgumentException) { return false; }
            catch (IOException) { return false; }
        }

        public static KitReport Check(string givenRoot, KitExpectation want)
        {
            var r = new KitReport();
            string root = givenRoot != null && givenRoot.StartsWith("\\\\", StringComparison.Ordinal) ? givenRoot : NormalizeRoot(givenRoot);
            r.Root = root ?? givenRoot;
            if (root != null && root.StartsWith("\\\\", StringComparison.Ordinal))
            {
                r.Problems.Add("The offline kit is on a network folder (" + root + "), which Domovoi's service can't open. Plug the stick into this PC, or copy the kit to a folder on this PC, and choose that.");
                return r;
            }
            if (root == null || !File.Exists(Path.Combine(root, KitJson)))
            {
                r.Problems.Add("There is no offline kit at " + (givenRoot ?? "that folder") + ": no kit.json there. Choose the stick's top folder, the one with kit.json in it.");
                return r;
            }
            if (IsNetworkPath(root))
            {
                r.Problems.Add("The offline kit is on a network folder (" + root + "), which Domovoi's service can't open. Plug the stick into this PC, or copy the kit to a folder on this PC, and choose that.");
                return r;
            }
            // 1. The trust chain's files, against the stick's checksums.
            Dictionary<string, string> sums;
            string why;
            try { sums = ParseSums(File.ReadAllText(Path.Combine(root, Sums), new UTF8Encoding(false)), out why); }
            catch (FileNotFoundException) { sums = null; why = "there is no " + Sums; }
            catch (IOException e) { sums = null; why = Sums + " can't be read (" + e.Message + ")"; }
            catch (UnauthorizedAccessException e) { sums = null; why = Sums + " can't be read (" + e.Message + ")"; }
            if (sums == null) { r.Problems.Add("This stick's checksums can't be used: " + why + "." + Remake); return r; }
            var first = new List<string>();
            foreach (string p in new[] { KitJson, ManifestPath, ManifestSigPath })
            {
                if (!sums.ContainsKey(p)) { r.Problems.Add("This stick has a damaged file: " + p + " (" + Sums + " doesn't list it)." + Remake); return r; }
                first.Add(p);
            }
            long ignored = 0;
            List<string> bad = Damaged(root, sums, first, ref ignored);
            if (bad.Count > 0) { r.Problems.Add(DamagedText(bad)); return r; }

            // 2. kit.json.
            KitIndex k;
            try { k = KitIndex.Parse(File.ReadAllText(Path.Combine(root, KitJson), new UTF8Encoding(false, true))); }
            catch (Exception e)
            {
                if (!(e is FormatException || e is IOException || e is DecoderFallbackException || e is UnauthorizedAccessException)) throw;
                r.Problems.Add("kit.json can't be read (" + e.Message + ")." + Remake);
                return r;
            }
            r.Version = k.Version; r.Tag = k.Tag; r.Commit = k.Commit;
            r.Ai = k.Includes.Contains("ai");
            if (k.Format == null || k.Format.Value < 1) { r.Problems.Add("kit.json has no kit format." + Remake); return r; }
            if (k.Format.Value > FormatRead)
            {
                r.Problems.Add("This kit was made by a newer Domovoi (kit format " + Inv.S(k.Format.Value) + "; this Setup reads format " + Inv.S(FormatRead) + "). Run the DomovoiSetup.exe on the stick, in its release folder.");
                return r;
            }
            if (!k.Targets.Contains(PcTarget))
            {
                r.Problems.Add(k.Targets.Contains(MacTarget)
                    ? "This kit was made for a Mac only. A Windows PC needs a kit with \"A Windows or Linux PC\" ticked: make the kit again with it ticked."
                    : "This kit isn't for a Windows PC: make the kit again with \"A Windows or Linux PC\" ticked.");
                return r;
            }
            if (k.Commit == null || !Hex40.IsMatch(k.Commit) || !ReleaseNames.IsTag(k.Tag)) { r.Problems.Add("kit.json names no release." + Remake); return r; }

            // 3. The release signature, and the release this Setup is.
            if (want.Key == null)
            {
                r.Problems.Add("This Setup has no release key built in, so it can't check an offline kit. Run the DomovoiSetup.exe on the stick, in its release folder.");
                return r;
            }
            byte[] manifest;
            string sig;
            try
            {
                manifest = File.ReadAllBytes(Local(root, ManifestPath));
                sig = File.ReadAllText(Local(root, ManifestSigPath), Encoding.ASCII);
            }
            catch (IOException e) { r.Problems.Add("The kit's signed release can't be read (" + e.Message + ")." + Remake); return r; }
            catch (UnauthorizedAccessException e) { r.Problems.Add("The kit's signed release can't be read (" + e.Message + ")." + Remake); return r; }
            why = SshSig.Problem(sig, manifest, want.Key, ReleaseNames.ManifestNamespace);
            SetupManifest m = null;
            if (why == null) m = SetupManifest.Parse(manifest, out why);
            if (m == null)
            {
                r.Problems.Add("This kit is not a Domovoi release signed with Domovoi's release key (" + why + "), so Setup used nothing from it. Use a kit from a house you trust, made again.");
                return r;
            }
            if (m.Tag != k.Tag || m.Commit != k.Commit)
            {
                r.Problems.Add("This stick's kit.json names " + k.Tag + " (commit " + k.Commit.Substring(0, 12) + "), but its signed release is " + m.Tag + " (commit " + m.Commit.Substring(0, 12) + ")." + Remake);
                return r;
            }
            if (m.Commit != want.Commit)
            {
                string mine = want.Version ?? "another release";
                r.Problems.Add(m.Version == want.Version
                    ? "This kit holds Domovoi " + m.Version + " at commit " + m.Commit.Substring(0, 12) + ", and this Setup installs commit " + (want.Commit ?? "").Substring(0, Math.Min(12, (want.Commit ?? "").Length)) + ": they don't go together. Run the DomovoiSetup.exe on the stick, in its release folder."
                    : "This kit holds Domovoi " + m.Version + ", and this Setup installs Domovoi " + mine + ": they don't go together. Run the DomovoiSetup.exe on the stick, in its release folder.");
                return r;
            }

            // 4. The parts.
            var missing = MissingParts(k, want);
            string wrong = WrongPins(k, want);
            if (wrong != null) r.Problems.Add(wrong);
            if (missing.Count > 0)
                r.Problems.Add("This kit is missing what Setup needs: " + string.Join("; ", missing.ToArray()) + ". Make the kit again.");
            foreach (string p in UsedBySetup(k, want))
                if (Listed(sums, p).Count == 0) r.Problems.Add("This stick has a damaged file: " + p + " (" + Sums + " doesn't list it)." + Remake);
            if (r.Problems.Count == 0 && want.ProvisionSha256 != null)
            {
                string text = null;
                try { text = File.ReadAllText(Local(root, ProvisionPath), Encoding.UTF8); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                if (text != null && !ProvisionScript.SupportsOfflineKit(text))
                    r.Problems.Add("This kit holds Domovoi " + k.Version + ", which can't install without internet (offline kits start with 1.5.0). Make a kit on a newer Domovoi.");
            }

            // 5. The disk.
            foreach (var e in k.Files.Values)
            {
                if (e.Size > 0) r.Bytes += e.Size;
                bool outside = false;
                foreach (string pre in NotInTheDistro) if (e.Path.StartsWith(pre, StringComparison.Ordinal)) outside = true;
                if (!outside && e.Size > 0) r.PayloadBytes += e.Size;
            }
            r.Files = k.Files.Count;
            r.NeedGb = NeedGb(r.PayloadBytes, want.MinFreeGb);
            if (want.FreeBytes >= 0 && want.FreeBytes / (1024L * 1024 * 1024) < r.NeedGb)
                r.Problems.Add("Installing from this kit needs about " + Inv.S(r.NeedGb) + " GB free on " + want.FreeDrive + ", and there are " + Inv.S(want.FreeBytes / (1024L * 1024 * 1024)) + " GB. Free some space, or make a kit without AI chat, then try again.");
            if (r.Problems.Count > 0) return r;

            // 6. Every file on the stick.
            var rest = new List<string>();
            foreach (string p in sums.Keys) if (!first.Contains(p)) rest.Add(p);
            rest.Sort(StringComparer.Ordinal);
            long hashed = 0;
            bad = Damaged(root, sums, rest, ref hashed);
            if (bad.Count > 0) r.Problems.Add(DamagedText(bad));
            return r;
        }

        // The files Setup itself reads off the stick.
        static List<string> UsedBySetup(KitIndex k, KitExpectation want)
        {
            var l = new List<string> { "windows/" + want.UbuntuName };
            if (want.NeedWsl) l.Add("windows/" + want.WslMsiName);
            if (want.ProvisionSha256 != null) l.Add(ProvisionPath);
            l.RemoveAll(p => !k.Has(p));
            return l;
        }

        // A part Setup takes from the kit that is there, but not the one
        // this Setup's release pins: another release's.
        static string WrongPins(KitIndex k, KitExpectation want)
        {
            var wrong = new List<string>();
            Action<string, string> pin = delegate (string path, string sha)
            {
                KitIndex.Entry e;
                if (sha != null && k.Files.TryGetValue(path, out e) && e.Sha256 != sha) wrong.Add(path);
            };
            pin("windows/" + want.UbuntuName, want.UbuntuSha256);
            if (want.NeedWsl) pin("windows/" + want.WslMsiName, want.WslMsiSha256);
            pin(ProvisionPath, want.ProvisionSha256);
            if (wrong.Count == 0) return null;
            return "This kit's " + string.Join(", ", wrong.ToArray()) + " isn't the one this Setup's release pins." + Remake;
        }

        // What every install from a kit needs: Setup's own parts, then the
        // list provision.sh's kit_check refuses without (the same names, so
        // the household reads one message whichever finds it first;
        // domovoi/tests/test_windows_offline_kit.py keeps the two lists
        // together).
        public static List<string> MissingParts(KitIndex k, KitExpectation want)
        {
            var missing = new List<string>();
            if (!k.Has("windows/" + want.UbuntuName)) missing.Add("Ubuntu for WSL (windows/" + want.UbuntuName + ")");
            if (want.NeedWsl && !k.Has("windows/" + want.WslMsiName)) missing.Add("WSL (windows/" + want.WslMsiName + ")");
            if (want.ProvisionSha256 != null && !k.Has(ProvisionPath)) missing.Add("Domovoi's install script (" + ProvisionPath + ")");
            if (!k.Has(BundlePath)) missing.Add("Domovoi's source (" + BundlePath + ")");
            if (!k.HasUnder("apt/amd64/ubuntu/dists/")) missing.Add("Ubuntu's packages (apt/amd64/ubuntu)");
            if (!k.HasUnder("apt/amd64/docker/dists/") || !k.Has("apt/amd64/docker/gpg")) missing.Add("Docker's packages and key (apt/amd64/docker, with gpg)");
            if (!k.HasUnder("python/cp314-x86_64/")) missing.Add("the Python packages for x86_64 (python/cp314-x86_64)");
            if (!k.Kinds.Contains("container-image")) missing.Add("the database images (images/)");
            if (!k.Kinds.Contains("ollama-runtime")) missing.Add("Ollama (ollama/)");
            if (!k.Kinds.Contains("whisper-model")) missing.Add("the speech recognition model (speech/whisper-...)");
            if (!k.Kinds.Contains("piper-voice")) missing.Add("the voices (speech/piper)");
            if (!k.Kinds.Contains("speaker-model")) missing.Add("voice ID's model (speech/speaker)");
            return missing;
        }
    }
}
