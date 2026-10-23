// The Windows update's trust in a downloaded DomovoiSetup.exe (design note
// WINDOWS-ONE-UPDATE-2026-10-22; docs/WINDOWS_HOST.md, "Updates and the
// release channel"). Authenticode is on hold, so a release carries our own
// detached Ed25519 signatures, made by scripts/release/finish_release.py
// with `ssh-keygen -Y sign` (OpenSSH's SSHSIG format, PROTOCOL.sshsig):
//
//   DomovoiSetup.exe.sig             over the exe,      namespace domovoi-windows-setup
//   DomovoiSetup.manifest.json       {kind, schema, tag, version, commit, file, size, sha256}
//   DomovoiSetup.manifest.json.sig   over the manifest, namespace domovoi-windows-setup-manifest
//
// The public half of the release key is pinned in every exe this build
// makes (build.ps1 embeds windows/installer/release-signing.pub as a
// resource; the exes live in C:\Program Files\Domovoi, which only
// administrators can change). A build without that file has no key, and
// no Windows step.
//
// SetupCheck.Run is the whole decision: both signatures by the pinned key
// under their own namespaces, the manifest naming the release asked for
// (tag and commit) and a version newer than the installed one, and the
// exe's size and SHA-256 the manifest's. Any "no" comes with plain words.

using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class ReleaseNames
    {
        public const string Repo = "coders-farm-official/domovoi";
        public const string SetupAsset = "DomovoiSetup.exe";
        public const string SetupSig = "DomovoiSetup.exe.sig";
        public const string Manifest = "DomovoiSetup.manifest.json";
        public const string ManifestSig = "DomovoiSetup.manifest.json.sig";
        public const string SetupNamespace = "domovoi-windows-setup";
        public const string ManifestNamespace = "domovoi-windows-setup-manifest";
        public const string ManifestKind = "domovoi-windows-setup";
        // The resource build.ps1 embeds windows/installer/release-signing.pub as.
        public const string KeyResource = "Domovoi.ReleaseSigning.pub";

        static readonly Regex Tag = new Regex(@"^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]{1,40})?\z");

        public static bool IsTag(string t) { return t != null && Tag.IsMatch(t); }

        // https://github.com/<Repo>/releases/download/<tag>/<asset>.
        public static string Url(string tag, string asset)
        {
            if (!IsTag(tag)) throw new ArgumentException("not a release tag: " + tag);
            return "https://github.com/" + Repo + "/releases/download/" + tag + "/" + asset;
        }
    }

    // A release version: X.Y.Z, optionally -suffix (rc1 < rc2 < the release
    // itself), compared the way SemVer orders them. Domovoi keeps fewer than
    // ten release candidates (docs/CONTRIBUTING.md), but numbers inside a
    // suffix compare as numbers anyway.
    public static class ReleaseVersion
    {
        static readonly Regex Shape = new Regex(@"^v?([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})(?:-([0-9A-Za-z.]{1,40}))?(?:\+[0-9A-Za-z.]*)?\z");

        public static bool IsValid(string v) { return v != null && Shape.IsMatch(v); }

        // <0, 0, >0; throws on something that isn't a version.
        public static int Compare(string a, string b)
        {
            Match x = Shape.Match(a ?? ""), y = Shape.Match(b ?? "");
            if (!x.Success) throw new FormatException("not a version: " + a);
            if (!y.Success) throw new FormatException("not a version: " + b);
            for (int i = 1; i <= 3; i++)
            {
                int c = long.Parse(x.Groups[i].Value).CompareTo(long.Parse(y.Groups[i].Value));
                if (c != 0) return c;
            }
            string px = x.Groups[4].Success ? x.Groups[4].Value : null, py = y.Groups[4].Success ? y.Groups[4].Value : null;
            if (px == null && py == null) return 0;
            if (px == null) return 1;      // a release sorts after its candidates
            if (py == null) return -1;
            string[] ix = px.Split('.'), iy = py.Split('.');
            for (int i = 0; i < Math.Min(ix.Length, iy.Length); i++)
            {
                int c = Ident(ix[i], iy[i]);
                if (c != 0) return c;
            }
            return ix.Length.CompareTo(iy.Length);
        }

        // "rc2" against "rc10": the letters, then the number after them.
        static int Ident(string a, string b)
        {
            Match ma = Regex.Match(a, @"^([A-Za-z]*)([0-9]*)\z"), mb = Regex.Match(b, @"^([A-Za-z]*)([0-9]*)\z");
            if (ma.Success && mb.Success)
            {
                int c = string.CompareOrdinal(ma.Groups[1].Value, mb.Groups[1].Value);
                if (c != 0) return c;
                string na = ma.Groups[2].Value, nb = mb.Groups[2].Value;
                if (na.Length == 0 || nb.Length == 0) return na.Length.CompareTo(nb.Length);
                return long.Parse(na).CompareTo(long.Parse(nb));
            }
            return string.CompareOrdinal(a, b);
        }
    }

    // An OpenSSH public key line, "ssh-ed25519 <base64> [comment]".
    public static class SshKey
    {
        // The 32 raw bytes, or null when the text is no Ed25519 public key.
        public static byte[] ParseEd25519(string line)
        {
            if (line == null) return null;
            foreach (string raw in line.Split('\n'))
            {
                string l = raw.Trim();
                if (l.Length == 0 || l.StartsWith("#", StringComparison.Ordinal)) continue;
                string[] parts = l.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length < 2 || parts[0] != "ssh-ed25519") return null;
                byte[] blob;
                try { blob = Convert.FromBase64String(parts[1]); }
                catch (FormatException) { return null; }
                var r = new SshReader(blob);
                string type = r.Text();
                byte[] key = r.Bytes();
                if (type != "ssh-ed25519" || key == null || key.Length != Ed25519.PublicKeyBytes || !r.AtEnd) return null;
                return key;
            }
            return null;
        }
    }

    // SSH wire strings: uint32 length (big-endian), then the bytes.
    public sealed class SshReader
    {
        readonly byte[] b;
        int pos;
        public bool Bad;

        public SshReader(byte[] data) { b = data ?? new byte[0]; }

        public bool AtEnd { get { return !Bad && pos == b.Length; } }

        public uint UInt32()
        {
            if (Bad || pos + 4 > b.Length) { Bad = true; return 0; }
            uint v = ((uint)b[pos] << 24) | ((uint)b[pos + 1] << 16) | ((uint)b[pos + 2] << 8) | b[pos + 3];
            pos += 4;
            return v;
        }

        public byte[] Raw(int n)
        {
            if (Bad || n < 0 || pos + n > b.Length) { Bad = true; return null; }
            var r = new byte[n];
            Array.Copy(b, pos, r, 0, n);
            pos += n;
            return r;
        }

        public byte[] Bytes()
        {
            uint n = UInt32();
            if (Bad || n > (uint)(b.Length - pos)) { Bad = true; return null; }
            return Raw((int)n);
        }

        public string Text()
        {
            byte[] s = Bytes();
            return s == null ? null : Encoding.ASCII.GetString(s);
        }

        public static byte[] String(byte[] s)
        {
            var r = new byte[4 + s.Length];
            r[0] = (byte)(s.Length >> 24); r[1] = (byte)(s.Length >> 16); r[2] = (byte)(s.Length >> 8); r[3] = (byte)s.Length;
            Array.Copy(s, 0, r, 4, s.Length);
            return r;
        }
    }

    // An armored SSHSIG ("-----BEGIN SSH SIGNATURE-----"), checked against
    // one pinned key and one namespace.
    public static class SshSig
    {
        const string Begin = "-----BEGIN SSH SIGNATURE-----";
        const string End = "-----END SSH SIGNATURE-----";
        static readonly byte[] Magic = Encoding.ASCII.GetBytes("SSHSIG");

        // Null when message carries a good signature by key under ns;
        // otherwise why not, in plain words.
        public static string Problem(string armored, byte[] message, byte[] key, string ns)
        {
            if (key == null || key.Length != Ed25519.PublicKeyBytes) return "there is no release key to check it with";
            byte[] blob = Unarmor(armored);
            if (blob == null) return "its signature file isn't an SSH signature";
            var r = new SshReader(blob);
            byte[] magic = r.Raw(6);
            uint version = r.UInt32();
            byte[] pub = r.Bytes();
            string sigNs = r.Text();
            byte[] reserved = r.Bytes();
            string hashAlg = r.Text();
            byte[] sigBlob = r.Bytes();
            if (r.Bad || !r.AtEnd || magic == null || !Same(magic, Magic) || version != 1) return "its signature file isn't an SSH signature";
            var pr = new SshReader(pub);
            string pubType = pr.Text();
            byte[] pubKey = pr.Bytes();
            if (pr.Bad || !pr.AtEnd || pubType != "ssh-ed25519") return "it isn't signed with an Ed25519 key";
            if (!Same(pubKey, key)) return "it is signed with a key other than Domovoi's release key";
            if (sigNs != ns) return "its signature is for something else (\"" + Safe(sigNs) + "\", not \"" + ns + "\")";
            var sr = new SshReader(sigBlob);
            string sigType = sr.Text();
            byte[] sig = sr.Bytes();
            if (sr.Bad || !sr.AtEnd || sigType != "ssh-ed25519" || sig == null || sig.Length != Ed25519.SignatureBytes) return "its signature is malformed";
            byte[] digest;
            if (hashAlg == "sha512") using (var h = SHA512.Create()) digest = h.ComputeHash(message);
            else if (hashAlg == "sha256") using (var h = SHA256.Create()) digest = h.ComputeHash(message);
            else return "its signature uses an unknown hash (" + Safe(hashAlg) + ")";
            var signed = new MemoryStream();
            signed.Write(Magic, 0, Magic.Length);
            Put(signed, Encoding.ASCII.GetBytes(sigNs));
            Put(signed, reserved);
            Put(signed, Encoding.ASCII.GetBytes(hashAlg));
            Put(signed, digest);
            return Ed25519.Verify(key, signed.ToArray(), sig) ? null : "its signature doesn't match the file (it was changed, or signed by someone else)";
        }

        static void Put(Stream s, byte[] b) { byte[] w = SshReader.String(b); s.Write(w, 0, w.Length); }

        static string Safe(string s)
        {
            if (s == null) return "";
            var sb = new StringBuilder();
            foreach (char c in s) if (c >= ' ' && c < 127 && sb.Length < 60) sb.Append(c);
            return sb.ToString();
        }

        static byte[] Unarmor(string text)
        {
            if (text == null) return null;
            string t = text.Replace("\r", "");
            int a = t.IndexOf(Begin, StringComparison.Ordinal), z = t.IndexOf(End, StringComparison.Ordinal);
            if (a < 0 || z < a) return null;
            string body = t.Substring(a + Begin.Length, z - a - Begin.Length).Replace("\n", "").Trim();
            try { return Convert.FromBase64String(body); }
            catch (FormatException) { return null; }
        }

        public static bool Same(byte[] a, byte[] b)
        {
            if (a == null || b == null || a.Length != b.Length) return false;
            int d = 0;
            for (int i = 0; i < a.Length; i++) d |= a[i] ^ b[i];
            return d == 0;
        }
    }

    public sealed class SetupManifest
    {
        public string Tag, Version, Commit, File, Sha256;
        public long Size;

        static readonly Regex Hex40 = new Regex("^[0-9a-f]{40}\\z");

        // The manifest's fields, or null with why.
        public static SetupManifest Parse(byte[] bytes, out string problem)
        {
            problem = null;
            Dictionary<string, object> d;
            try { d = Json.ParseObject(new UTF8Encoding(false, true).GetString(bytes)); }
            catch (Exception e)
            {
                if (!(e is ArgumentException || e is FormatException || e is InvalidOperationException || e is DecoderFallbackException)) throw;
                problem = "its manifest isn't readable";
                return null;
            }
            var m = new SetupManifest
            {
                Tag = Json.Str(d, "tag"),
                Version = Json.Str(d, "version"),
                Commit = Json.Str(d, "commit"),
                File = Json.Str(d, "file"),
                Sha256 = Json.Str(d, "sha256"),
                Size = Json.Long(d, "size") ?? -1,
            };
            if (Json.Str(d, "kind") != ReleaseNames.ManifestKind || Json.Int(d, "schema") != 1) problem = "its manifest is for something else";
            else if (!ReleaseNames.IsTag(m.Tag) || !ReleaseVersion.IsValid(m.Version) || m.Tag != "v" + m.Version) problem = "its manifest names no release";
            else if (m.Commit == null || !Hex40.IsMatch(m.Commit)) problem = "its manifest names no commit";
            else if (m.File != ReleaseNames.SetupAsset || !Domovoi.Windows.Sha256.IsHex(m.Sha256) || m.Size <= 0) problem = "its manifest doesn't describe a Setup";
            return problem == null ? m : null;
        }
    }

    // What the Windows step asks of a download.
    public sealed class SetupExpectation
    {
        public string Tag;              // the release the core named
        public string Commit;           // its commit, as the core's checkout has it (null: not checked)
        public string Installed;        // the installed Windows side's version
        public byte[] Key;              // the pinned release key
    }

    public static class SetupCheck
    {
        // Why a download may not run, for the card (domovoi/windows_side.py
        // OUTCOMES): the release has no signed files, they don't check out,
        // or they aren't newer.
        public const string NoKey = "no_key";
        public const string NoSignedSetup = "no_signed_setup";
        public const string BadSignature = "bad_signature";
        public const string NotNewer = "not_newer";

        // Null when the files are what was asked for and may run; else why
        // not, as a sentence's end ("Domovoi didn't run it: " + this), and
        // code one of the four above.
        public static string Run(SetupExpectation want, byte[] manifest, string manifestSig, byte[] setup, string setupSig, out SetupManifest m, out string code)
        {
            m = null;
            code = BadSignature;
            if (want.Key == null) { code = NoKey; return "this Windows side pins no release key, so it can't check a Setup"; }
            if (manifest == null || manifestSig == null) { code = NoSignedSetup; return "the release has no signed manifest"; }
            if (setup == null || setupSig == null) { code = NoSignedSetup; return "the release has no Setup signature"; }
            string why = SshSig.Problem(manifestSig, manifest, want.Key, ReleaseNames.ManifestNamespace);
            if (why != null) return "its manifest can't be trusted: " + why;
            m = SetupManifest.Parse(manifest, out why);
            if (m == null) return why;
            if (m.Tag != want.Tag) return "its manifest is for " + m.Tag + ", not " + want.Tag;
            if (want.Commit != null && m.Commit != want.Commit) return "it was built from " + m.Commit.Substring(0, 12) + ", not " + want.Tag + "'s commit " + want.Commit.Substring(0, Math.Min(12, want.Commit.Length));
            if (want.Installed == null || !ReleaseVersion.IsValid(want.Installed))
            {
                // Fail closed: what can't be compared is never run.
                code = NotNewer;
                return "the installed Windows side's version isn't known (" + (want.Installed ?? "none") + "), so Domovoi can't tell this one is newer; run the newer DomovoiSetup.exe by hand";
            }
            if (ReleaseVersion.Compare(m.Version, want.Installed) <= 0)
            {
                code = NotNewer;
                return "it is version " + m.Version + ", which isn't newer than the installed " + want.Installed;
            }
            why = SshSig.Problem(setupSig, setup, want.Key, ReleaseNames.SetupNamespace);
            if (why != null) return "the Setup can't be trusted: " + why;
            if (setup.LongLength != m.Size) return "the Setup is " + Inv.S(setup.LongLength) + " bytes, its manifest says " + Inv.S(m.Size);
            string sha;
            using (var h = SHA256.Create()) sha = Sha256.Hex(h.ComputeHash(setup));
            if (sha != m.Sha256) return "the Setup's SHA-256 isn't the one its manifest names";
            code = null;
            return null;
        }
    }

    // The release key this build pins (null: none).
    public static class ReleaseKey
    {
        static byte[] cached;
        static bool read;

        public static byte[] Pinned()
        {
            if (read) return cached;
            read = true;
            var asm = typeof(ReleaseKey).Assembly;
            using (Stream s = asm.GetManifestResourceStream(ReleaseNames.KeyResource))
            {
                if (s == null) return null;
                using (var r = new StreamReader(s, Encoding.ASCII)) cached = SshKey.ParseEd25519(r.ReadToEnd());
            }
            return cached;
        }
    }
}
