// The card files the dashboard builds (Settings > Satellites > prepare
// satellite media > the zip download), read and checked before anything
// goes near a card. The server writes the zip in
// domovoi/satellite_media/builder.py: README.txt, config.txt and
// cmdline.txt (only Domovoi's additions), userconf.txt (the console
// password's hash) and domovoi/ (firstrun.sh, payload.tar.gz,
// payload.sha256, build-info.json, device-info.json,
// server-identity.json). No passwords: the setup network's one is typed in
// the card helper (domovoi/ap.json is written from it).
//
// Refused: anything that isn't a zip of that shape, a payload whose
// SHA-256 isn't the one payload.sha256 names, a board this helper doesn't
// know, and any name that could land outside the card's folder.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows.Tray
{
    public sealed class CardZipException : Exception
    {
        public CardZipException(string message) : base(message) { }
    }

    public sealed class CardFile
    {
        public string Entry;        // the name in the zip
        public string Dest;         // the path on the card, relative, with '\'
        public long Size;
    }

    public sealed class CardPackage
    {
        public string ZipPath;
        public string Board;
        public string BoardLabel;
        public string BootMarker;   // the board's .dtb on a flashed card
        public string MicProfile;
        public string SetupTransport;  // "portal" or "usb"
        public string ApSsid;       // portal cards: the setup network's name
        public string BuiltAt;
        public bool Offline;
        public string ConfigBlock;
        public List<string> CmdlineTokens;
        public string PayloadSha256;
        public List<CardFile> Files = new List<CardFile>();

        public bool IsPortal { get { return SetupTransport == "portal"; } }

        public long CopyBytes
        {
            get { long n = 0; foreach (CardFile f in Files) n += f.Size; return n; }
        }

        public string Summary()
        {
            string when = BuiltAt;
            DateTimeOffset t;
            if (DateTimeOffset.TryParseExact(BuiltAt, "yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture, DateTimeStyles.None, out t)
                || DateTimeOffset.TryParse(BuiltAt, CultureInfo.InvariantCulture, DateTimeStyles.None, out t))
                when = t.LocalDateTime.ToString("d MMM yyyy, HH:mm", CultureInfo.CurrentCulture);
            return "Built " + when + " for " + BoardLabel + (IsPortal ? ", set up over Wi-Fi (" + ApSsid + ")" : ", adopted over USB")
                + ". " + CardZip.Mib(CopyBytes) + " to copy.";
        }
    }

    public static class CardZip
    {
        public const string NamePattern = "domovoi-satellite-overlay*.zip";
        public const string Readme = "README.txt";
        public const string ApJson = "domovoi/ap.json";

        // The boards the server prepares cards for (boards.py, BOARDS with
        // supported=True), with the file a flashed card of each carries.
        public static readonly Dictionary<string, string[]> Boards = new Dictionary<string, string[]>
        {
            { "pi02w", new[] { "Raspberry Pi Zero 2 W (and Pi 3/4/5)", "bcm2710-rpi-zero-2-w.dtb" } },
        };

        static readonly Regex Sha256Line = new Regex(@"^([0-9a-f]{64})[ \t]+\*?payload\.tar\.gz\s*\z");
        static readonly Regex PlainName = new Regex(@"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\z");
        static readonly Regex Ssid = new Regex(@"^[\x21-\x7e]([\x20-\x7e]{0,30}[\x21-\x7e])?\z");

        public static string Mib(long bytes)
        {
            return Inv.S((bytes + 1024 * 1024 - 1) / (1024 * 1024)) + " MB";
        }

        // The newest card zip in a folder (a browser names a second
        // download "domovoi-satellite-overlay (1).zip"), or null.
        public static string Newest(string folder)
        {
            if (string.IsNullOrEmpty(folder) || !Directory.Exists(folder)) return null;
            string best = null;
            DateTime bestTime = DateTime.MinValue;
            try
            {
                foreach (string f in Directory.GetFiles(folder, NamePattern, SearchOption.TopDirectoryOnly))
                {
                    if (!f.EndsWith(".zip", StringComparison.OrdinalIgnoreCase)) continue;
                    DateTime t = File.GetLastWriteTimeUtc(f);
                    if (best == null || t > bestTime) { best = f; bestTime = t; }
                }
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
            return best;
        }

        // Where a name in the zip goes on the card, or null for a name the
        // card doesn't get (the README, the two files merged instead of
        // copied). Throws for a name no Domovoi zip has.
        public static string DestFor(string entry)
        {
            if (entry == Readme || entry == "config.txt" || entry == "cmdline.txt") return null;
            if (entry == "userconf.txt") return "userconf.txt";
            if (entry.StartsWith("domovoi/", StringComparison.Ordinal))
            {
                string name = entry.Substring(8);
                if (PlainName.IsMatch(name) && name != "." && name != "..")
                    return name == "ap.json" || name == "console.json" ? null : "domovoi\\" + name;
            }
            throw new CardZipException("This zip holds \"" + entry + "\", which a Domovoi card zip never does. Download the card files again from the dashboard.");
        }

        public static CardPackage Open(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) throw new CardZipException("There's no file there.");
            ZipArchive zip;
            try { zip = ZipFile.OpenRead(path); }
            catch (InvalidDataException) { throw new CardZipException("That file isn't a zip (or it's damaged). Download the card files again from the dashboard."); }
            catch (IOException e) { throw new CardZipException("Can't read that file: " + e.Message); }
            catch (UnauthorizedAccessException) { throw new CardZipException("Windows won't let this program read that file."); }
            using (zip)
            {
                var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
                foreach (ZipArchiveEntry e in zip.Entries)
                {
                    string name = e.FullName;
                    if (name.EndsWith("/", StringComparison.Ordinal) && e.Length == 0) continue;   // a folder entry
                    if (entries.ContainsKey(name)) throw new CardZipException("This zip names \"" + name + "\" twice. Download the card files again.");
                    entries[name] = e;
                }
                foreach (string req in new[] { "domovoi/build-info.json", "domovoi/payload.sha256", "domovoi/payload.tar.gz", "domovoi/firstrun.sh", "domovoi/device-info.json", "config.txt", "cmdline.txt" })
                    if (!entries.ContainsKey(req))
                        throw new CardZipException("This isn't a Domovoi satellite card zip (it has no " + req + "). Download it from the dashboard: Settings > Satellites > prepare satellite media.");

                var pkg = new CardPackage();
                pkg.ZipPath = Path.GetFullPath(path);
                foreach (KeyValuePair<string, ZipArchiveEntry> kv in entries)
                {
                    string dest = DestFor(kv.Key);
                    if (dest != null) pkg.Files.Add(new CardFile { Entry = kv.Key, Dest = dest, Size = kv.Value.Length });
                }

                Dictionary<string, object> info = ReadJson(entries["domovoi/build-info.json"], "build-info.json");
                if (Json.Long(info, "schema") != 1) throw new CardZipException("These card files come from a newer Domovoi than this card helper knows. Update Domovoi's tray icon (the release page has DomovoiTray.exe).");
                pkg.Board = Json.Str(info, "board") ?? "";
                string[] board;
                if (!Boards.TryGetValue(pkg.Board, out board))
                    throw new CardZipException("These card files are for a board this card helper doesn't know (\"" + pkg.Board + "\").");
                pkg.BoardLabel = board[0];
                pkg.BootMarker = board[1];
                pkg.MicProfile = Json.Str(info, "mic_profile") ?? "";
                pkg.BuiltAt = Json.Str(info, "built_at") ?? "";
                pkg.Offline = Json.Bool(info, "offline") ?? false;

                Dictionary<string, object> dev = ReadJson(entries["domovoi/device-info.json"], "device-info.json");
                pkg.SetupTransport = Json.Str(dev, "setup_transport") ?? "usb";
                if (pkg.SetupTransport != "usb" && pkg.SetupTransport != "portal")
                    throw new CardZipException("These card files ask for a setup route this card helper doesn't know (\"" + pkg.SetupTransport + "\").");
                if (pkg.IsPortal)
                {
                    pkg.ApSsid = Json.Str(dev, "ap_ssid");
                    if (pkg.ApSsid == null || !Ssid.IsMatch(pkg.ApSsid))
                        throw new CardZipException("These Wi-Fi setup card files don't name their setup network. Build them again in the dashboard.");
                }

                pkg.ConfigBlock = BootText.BlockFromZip(ReadText(entries["config.txt"], "config.txt"));
                if (pkg.ConfigBlock == null) throw new CardZipException("This zip's config.txt isn't the one Domovoi writes. Download the card files again.");
                pkg.CmdlineTokens = BootText.TokensFromZip(ReadText(entries["cmdline.txt"], "cmdline.txt"));
                if (pkg.CmdlineTokens == null) throw new CardZipException("This zip's cmdline.txt isn't the one Domovoi writes. Download the card files again.");

                Match m = Sha256Line.Match(ReadText(entries["domovoi/payload.sha256"], "payload.sha256"));
                if (!m.Success) throw new CardZipException("This zip's payload.sha256 isn't the one Domovoi writes. Download the card files again.");
                pkg.PayloadSha256 = m.Groups[1].Value;
                string actual;
                using (Stream s = entries["domovoi/payload.tar.gz"].Open()) actual = Sha256Hex(s);
                if (actual != pkg.PayloadSha256)
                    throw new CardZipException("The satellite software in this zip is damaged (its SHA-256 doesn't match). Download the card files again.");
                return pkg;
            }
        }

        static string ReadText(ZipArchiveEntry e, string what)
        {
            if (e.Length > 1024 * 1024) throw new CardZipException("This zip's " + what + " is far too big to be Domovoi's.");
            using (Stream s = e.Open())
            using (var ms = new MemoryStream())
            {
                s.CopyTo(ms);
                byte[] b = ms.ToArray();
                try { return new UTF8Encoding(false, true).GetString(b); }
                catch (DecoderFallbackException) { throw new CardZipException("This zip's " + what + " isn't text."); }
            }
        }

        static Dictionary<string, object> ReadJson(ZipArchiveEntry e, string what)
        {
            try { return Json.ParseObject(ReadText(e, what)); }
            catch (FormatException) { throw new CardZipException("This zip's " + what + " isn't readable. Download the card files again."); }
            catch (ArgumentException) { throw new CardZipException("This zip's " + what + " isn't readable. Download the card files again."); }
            catch (InvalidOperationException) { throw new CardZipException("This zip's " + what + " isn't readable. Download the card files again."); }
        }

        public static string Sha256Hex(Stream s)
        {
            using (SHA256 h = SHA256.Create())
            {
                byte[] d = h.ComputeHash(s);
                var sb = new StringBuilder(64);
                foreach (byte x in d) sb.Append(x.ToString("x2", CultureInfo.InvariantCulture));
                return sb.ToString();
            }
        }

        public static string Sha256Hex(byte[] b)
        {
            using (var ms = new MemoryStream(b)) return Sha256Hex(ms);
        }
    }
}
