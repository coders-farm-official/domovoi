// Writes a card the way the server's write_overlay does
// (domovoi/satellite_media/overlay.py): domovoi/ and userconf.txt copied
// from the zip, domovoi/ap.json written for a Wi-Fi setup card, and the
// config.txt and cmdline.txt edits merged (BootText). Plain file writes
// on the card's boot partition and nowhere else: nothing is formatted,
// nothing outside that one folder is touched.
//
// Order: free space first; then the files the first-boot hook runs; then
// config.txt and cmdline.txt last, so a card pulled out halfway never
// boots into a hook whose files aren't there. Then every file is read back
// and checked (each copied file against the zip, the payload against
// payload.sha256, ap.json and the two edits against what was meant).

using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;

namespace Domovoi.Windows.Tray
{
    public sealed class CardWriteException : Exception
    {
        public CardWriteException(string message) : base(message) { }
    }

    public static class SetupPassword
    {
        // overlay.py: _PSK_ALPHABET and _AP_PSK_LEN.
        public const string Alphabet = "abcdefghijkmnpqrstuvwxyz23456789";
        public const int Length = 10;

        // What was typed, as the card needs it: spaces dropped, lower case
        // (the passwords never contain capitals).
        public static string Normalize(string typed)
        {
            var sb = new StringBuilder();
            foreach (char c in typed ?? "") if (!char.IsWhiteSpace(c)) sb.Append(char.ToLowerInvariant(c));
            return sb.ToString();
        }

        // Why it can't be the password, in household words; null when it can.
        public static string Problem(string typed)
        {
            string p = Normalize(typed);
            if (p.Length == 0) return "Type the setup network password the dashboard showed.";
            foreach (char c in p)
                if (Alphabet.IndexOf(c) < 0)
                {
                    if (c == '0' || c == '1' || c == 'l' || c == 'o')
                        return "These passwords never use 0, 1, l or o. Check that character against the dashboard.";
                    return "\"" + c + "\" isn't in these passwords: they're lower-case letters and the digits 2 to 9.";
                }
            if (p.Length != Length)
                return "That's " + Inv.S(p.Length) + " characters; the password has " + Inv.S(Length) + ".";
            return null;
        }
    }

    public static class ApJson
    {
        // json.dumps({"ssid": ..., "psk": ...}, indent=2): what the server
        // writes on a card it prepares itself. UTF-8 without a BOM, LF, no
        // newline at the end. Both values are printable ASCII (checked
        // before this), so only " and \ need escaping.
        public static byte[] Bytes(string ssid, string psk)
        {
            string text = "{\n  \"ssid\": \"" + Esc(ssid) + "\",\n  \"psk\": \"" + Esc(psk) + "\"\n}";
            return new UTF8Encoding(false).GetBytes(text);
        }

        static string Esc(string s)
        {
            var sb = new StringBuilder();
            foreach (char c in s)
            {
                if (c < ' ' || c > '~') throw new ArgumentException("not printable ASCII");
                if (c == '"' || c == '\\') sb.Append('\\');
                sb.Append(c);
            }
            return sb.ToString();
        }
    }

    public sealed class CardWriteResult
    {
        public List<string> Written = new List<string>();
        public List<string> Checked = new List<string>();
        public bool ConfigChanged, CmdlineChanged;
    }

    public static class CardWriter
    {
        // The server's margin over the payload (builder.py: + 8 MiB).
        public const long Margin = 8L * 1024 * 1024;

        public static void CheckFree(long free, long needed)
        {
            if (free < needed)
                throw new CardWriteException("The card has " + CardZip.Mib(free) + " free, and the satellite files need " + CardZip.Mib(needed)
                    + ". Flash it again with Raspberry Pi OS Lite (64-bit), which leaves room, then try again.");
        }

        // progress(message, percent).
        public static CardWriteResult Write(CardPackage pkg, string root, string psk, Action<string, int> progress)
        {
            if (progress == null) progress = delegate { };
            var result = new CardWriteResult();
            string config = Path.Combine(root, "config.txt"), cmdline = Path.Combine(root, "cmdline.txt");
            if (!File.Exists(config) || !File.Exists(cmdline) || !File.Exists(Path.Combine(root, pkg.BootMarker)))
                throw new CardWriteException("This drive no longer looks like a freshly flashed Raspberry Pi card. Put the card back in and try again.");
            byte[] ap = null;
            if (pkg.IsPortal)
            {
                string why = SetupPassword.Problem(psk);
                if (why != null) throw new CardWriteException(why);
                ap = ApJson.Bytes(pkg.ApSsid, SetupPassword.Normalize(psk));
            }

            progress("Checking the free space on the card", 2);
            long free, total;
            if (!FreeSpace.Get(root, out free, out total)) throw new CardWriteException("Windows can't say how much space the card has. Put it back in and try again.");
            CheckFree(free, pkg.CopyBytes + Margin);

            var hashes = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            Directory.CreateDirectory(Path.Combine(root, "domovoi"));
            using (ZipArchive zip = ZipFile.OpenRead(pkg.ZipPath))
            {
                long done = 0, all = Math.Max(1, pkg.CopyBytes);
                // The payload first (the big one), the hook's files after it.
                var order = new List<CardFile>(pkg.Files);
                order.Sort(delegate (CardFile a, CardFile b) { return (b.Entry == "domovoi/payload.tar.gz").CompareTo(a.Entry == "domovoi/payload.tar.gz"); });
                foreach (CardFile f in order)
                {
                    ZipArchiveEntry e = zip.GetEntry(f.Entry);
                    if (e == null || e.Length != f.Size) throw new CardWriteException("The zip changed while the card was being written. Pick it again.");
                    string dest = Path.Combine(root, f.Dest);
                    progress("Copying " + f.Dest.Replace('\\', '/'), 5 + (int)(80 * done / all));
                    using (Stream s = e.Open())
                    using (var hashing = new HashingStream(s))
                    {
                        WriteStream(dest, hashing);
                        hashes[f.Dest] = hashing.Hex();
                    }
                    if (f.Entry == "domovoi/payload.tar.gz" && hashes[f.Dest] != pkg.PayloadSha256)
                        throw new CardWriteException("The satellite software in the zip is damaged (its SHA-256 doesn't match). Download the card files again.");
                    result.Written.Add(f.Dest.Replace('\\', '/'));
                    done += f.Size;
                }
            }
            if (ap != null)
            {
                progress("Writing the setup network password (domovoi/ap.json)", 86);
                WriteBytes(Path.Combine(root, "domovoi", "ap.json"), ap);
                result.Written.Add("domovoi/ap.json");
            }

            progress("Adding Domovoi's lines to config.txt and cmdline.txt", 88);
            bool changed;
            byte[] newConfig = BootText.Apply(File.ReadAllBytes(config), true, delegate (string t) { return BootText.EditConfig(t, pkg.ConfigBlock); }, out changed);
            if (changed) { WriteBytes(config, newConfig); result.Written.Add("config.txt"); }
            result.ConfigChanged = changed;
            byte[] newCmdline = BootText.Apply(File.ReadAllBytes(cmdline), true, delegate (string t) { return BootText.EditCmdline(t, pkg.CmdlineTokens); }, out changed);
            if (changed) { WriteBytes(cmdline, newCmdline); result.Written.Add("cmdline.txt"); }
            result.CmdlineChanged = changed;

            progress("Reading everything back to check it", 90);
            foreach (KeyValuePair<string, string> kv in hashes)
            {
                string got;
                using (var s = new FileStream(Path.Combine(root, kv.Key), FileMode.Open, FileAccess.Read, FileShare.Read)) got = CardZip.Sha256Hex(s);
                if (got != kv.Value) throw new CardWriteException(kv.Key.Replace('\\', '/') + " on the card isn't what was written. The card may be failing: try another one.");
                result.Checked.Add(kv.Key.Replace('\\', '/'));
            }
            string sumFile = Path.Combine(root, "domovoi", "payload.sha256");
            string sum = File.ReadAllText(sumFile).Trim();
            string payloadOnCard;
            using (var s = new FileStream(Path.Combine(root, "domovoi", "payload.tar.gz"), FileMode.Open, FileAccess.Read, FileShare.Read)) payloadOnCard = CardZip.Sha256Hex(s);
            if (!sum.StartsWith(payloadOnCard + " ", StringComparison.Ordinal) || payloadOnCard != pkg.PayloadSha256)
                throw new CardWriteException("The satellite software on the card doesn't match its checksum. The card may be failing: try another one.");
            result.Checked.Add("domovoi/payload.tar.gz against payload.sha256");
            if (ap != null)
            {
                if (!Same(File.ReadAllBytes(Path.Combine(root, "domovoi", "ap.json")), ap)) throw new CardWriteException("domovoi/ap.json on the card isn't what was written. Try another card.");
                result.Checked.Add("domovoi/ap.json");
            }
            byte[] cfg = File.ReadAllBytes(config), cmd = File.ReadAllBytes(cmdline);
            if (!Same(cfg, newConfig) || BootText.EditConfig(BootText.Read(cfg), pkg.ConfigBlock) != BootText.Read(cfg))
                throw new CardWriteException("config.txt on the card isn't what was written. Try another card.");
            if (!Same(cmd, newCmdline) || BootText.EditCmdline(BootText.Read(cmd), pkg.CmdlineTokens) != BootText.Read(cmd))
                throw new CardWriteException("cmdline.txt on the card isn't what was written. Try another card.");
            result.Checked.Add("config.txt");
            result.Checked.Add("cmdline.txt");
            progress("Done", 100);
            return result;
        }

        static bool Same(byte[] a, byte[] b)
        {
            if (a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; i++) if (a[i] != b[i]) return false;
            return true;
        }

        // A file on the card, replaced whole, flushed to the card before
        // the next one starts.
        static void WriteStream(string path, Stream from)
        {
            using (var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16))
            {
                from.CopyTo(fs, 1 << 20);
                fs.Flush(true);
            }
        }

        static void WriteBytes(string path, byte[] bytes)
        {
            using (var ms = new MemoryStream(bytes)) WriteStream(path, ms);
        }
    }

    // Hashes what is read through it.
    sealed class HashingStream : Stream
    {
        readonly Stream inner;
        readonly System.Security.Cryptography.SHA256 sha = System.Security.Cryptography.SHA256.Create();
        byte[] digest;

        public HashingStream(Stream inner) { this.inner = inner; }

        public string Hex()
        {
            if (digest == null) { sha.TransformFinalBlock(new byte[0], 0, 0); digest = sha.Hash; }
            var sb = new StringBuilder(64);
            foreach (byte b in digest) sb.Append(b.ToString("x2", System.Globalization.CultureInfo.InvariantCulture));
            return sb.ToString();
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            int n = inner.Read(buffer, offset, count);
            if (n > 0) sha.TransformBlock(buffer, offset, n, null, 0);
            return n;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return false; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { throw new NotSupportedException(); } }
        public override long Position { get { throw new NotSupportedException(); } set { throw new NotSupportedException(); } }
        public override void Flush() { }
        public override long Seek(long offset, SeekOrigin origin) { throw new NotSupportedException(); }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }

        protected override void Dispose(bool disposing)
        {
            if (disposing) sha.Dispose();
            base.Dispose(disposing);
        }
    }
}
