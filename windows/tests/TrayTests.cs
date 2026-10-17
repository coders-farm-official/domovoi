// The tray's tests (windows/tray): the card helper's merge against the
// server's own output (card-goldens.json, made by
// domovoi/tests/test_windows_card_parity.py from overlay.write_overlay),
// the zip checks, the drive filter on a fake drive list, a whole card
// written into a folder that mimics a flashed boot partition, the status
// rules, the network address pick, sign-in registration on an in-memory
// store, the command line, and the QR encoder. Nothing here touches a real
// drive, the registry, a service or the network.

using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using Domovoi.Windows;
using Domovoi.Windows.Tray;

static partial class Tests
{
    static string GoldensPath()
    {
        string env = Environment.GetEnvironmentVariable("DOMOVOI_CARD_GOLDENS");
        if (!string.IsNullOrEmpty(env)) return env;
        // windows\build\obj\DomovoiWindowsTests.exe -> windows\tests\card-goldens.json
        string exeDir = Path.GetDirectoryName(typeof(Tests).Assembly.Location);
        return Path.GetFullPath(Path.Combine(exeDir, @"..\..\tests\card-goldens.json"));
    }

    static Dictionary<string, object> goldens;

    static Dictionary<string, object> Goldens()
    {
        if (goldens == null) goldens = Json.ParseObject(File.ReadAllText(GoldensPath(), Encoding.ASCII));
        return goldens;
    }

    static byte[] B64(object o) { return Convert.FromBase64String((string)o); }

    static string Utf(byte[] b) { return new UTF8Encoding(false).GetString(b); }

    static string TempDir(string name)
    {
        string d = Path.Combine(Path.GetTempPath(), "domovoi-tray-tests", name + "-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        Directory.CreateDirectory(d);
        return d;
    }

    static void TrayTests()
    {
        CardParity();
        CardZipChecks();
        CardDriveFilter();
        CardWrites();
        TrayStatus();
        TrayAddresses();
        TrayStartup();
        TrayOptions();
        TrayQr();
        TrayMisc();
        try { Directory.Delete(Path.Combine(Path.GetTempPath(), "domovoi-tray-tests"), true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }

    // -- parity with overlay.py ---------------------------------------------

    static void CardParity()
    {
        Run("card parity: the goldens are there and cover every input and setup", delegate
        {
            var g = Goldens();
            True(Json.ObjList(g, "cases").Count >= 50, "cases");
            True(Json.ObjList(g, "inputs").Count >= 17, "inputs");
            SeqEq(new[] { "portal", "portal-usb-mic", "usb" }, Json.Obj(g, "zips").Keys.OrderBy(k => k), "setups");
        });
        Run("card parity: the zip's config.txt and cmdline.txt give the block and the tokens", delegate
        {
            foreach (KeyValuePair<string, object> kv in Json.Obj(Goldens(), "zips"))
            {
                var z = (Dictionary<string, object>)kv.Value;
                string cfg = Utf(B64(z["config"])), cmd = Utf(B64(z["cmdline"]));
                Eq(cfg, BootText.BlockFromZip(cfg), kv.Key + " block");
                List<string> tokens = BootText.TokensFromZip(cmd);
                True(tokens != null && tokens.Contains(BootText.FirstrunToken), kv.Key + " tokens");
            }
            True(Utf(B64(((Dictionary<string, object>)Json.Obj(Goldens(), "zips")["usb"])["config"])).Contains("dtoverlay=dwc2,dr_mode=peripheral"), "usb: gadget overlay");
            True(Utf(B64(((Dictionary<string, object>)Json.Obj(Goldens(), "zips")["portal-usb-mic"])["cmdline"])).Contains("dwc_otg.speed=1"), "usb mic: full speed");
        });
        Run("card parity: every input gives the server's bytes (SHA-256, length, written or not)", delegate
        {
            var g = Goldens();
            var inputs = Json.ObjList(g, "inputs");
            var zips = Json.Obj(g, "zips");
            int n = 0;
            foreach (var c in Json.ObjList(g, "cases"))
            {
                var input = inputs[(int)Json.Long(c, "input")];
                var z = (Dictionary<string, object>)zips[Json.Str(c, "flags")];
                string block = BootText.BlockFromZip(Utf(B64(z["config"])));
                List<string> tokens = BootText.TokensFromZip(Utf(B64(z["cmdline"])));
                string what = Json.Str(c, "name") + " / " + Json.Str(c, "flags");
                bool changed;
                byte[] cfg = BootText.Apply(B64(input["config"]), true, t => BootText.EditConfig(t, block), out changed);
                Eq(Json.Bool(c, "config_written").Value, changed, what + ": config written");
                Eq(Json.Str(c, "config_sha256"), CardZip.Sha256Hex(cfg), what + ": config.txt bytes (" + Utf(cfg).Replace("\n", "\\n") + ")");
                Eq((long)Json.Long(c, "config_length"), (long)cfg.Length, what + ": config length");
                byte[] cmd = BootText.Apply(B64(input["cmdline"]), true, t => BootText.EditCmdline(t, tokens), out changed);
                Eq(Json.Bool(c, "cmdline_written").Value, changed, what + ": cmdline written");
                Eq(Json.Str(c, "cmdline_sha256"), CardZip.Sha256Hex(cmd), what + ": cmdline.txt bytes (" + Utf(cmd).Replace("\n", "\\n") + ")");
                Eq((long)Json.Long(c, "cmdline_length"), (long)cmd.Length, what + ": cmdline length");
                // Run again over its own output: nothing changes.
                byte[] again = BootText.Apply(cfg, true, t => BootText.EditConfig(t, block), out changed);
                True(!changed && again.SequenceEqual(cfg), what + ": config twice");
                again = BootText.Apply(cmd, true, t => BootText.EditCmdline(t, tokens), out changed);
                True(!changed && again.SequenceEqual(cmd), what + ": cmdline twice");
                n++;
            }
            True(n >= 50, "cases run");
        });
        Run("card parity: ap.json is the server's bytes (UTF-8, no BOM, LF)", delegate
        {
            var ap = Json.Obj(Goldens(), "ap_json");
            True(ApJson.Bytes(Json.Str(ap, "ssid"), Json.Str(ap, "psk")).SequenceEqual(B64(ap["bytes"])), "bytes");
        });
        Run("card parity: Python's whitespace, lines and UTF-8 replacement", delegate
        {
            SeqEq(new[] { "a", "b", "c", "d" }, BootText.PySplit("a\u00a0b\u2003c\u001fd "), "split on what str.split() splits on");
            Eq("x", BootText.PyStrip("\x1c\t x \u3000"), "strip");
            Eq("one", BootText.PyFirstLine("one\x0btwo"), "\\v ends a line");
            Eq("a\nb\nc", BootText.Read(Encoding.ASCII.GetBytes("a\r\nb\rc")), "universal newlines");
            Eq("caf\uFFFD!", Utf8.Decode(new byte[] { 0x63, 0x61, 0x66, 0xE9, 0x21 }), "one bad byte, one U+FFFD");
            Eq("\uFFFD(", Utf8.Decode(new byte[] { 0xC3, 0x28 }), "a lead without its continuation");
            Eq("\uFFFD", Utf8.Decode(new byte[] { 0xE2, 0x82 }), "a truncated sequence is one U+FFFD");
            Eq("\uFFFD\uFFFD", Utf8.Decode(new byte[] { 0xED, 0xA0 }), "a surrogate's bytes are two");
            Eq("\uD83D\uDE00", Utf8.Decode(new byte[] { 0xF0, 0x9F, 0x98, 0x80 }), "four bytes");
            Eq("\uFEFFx", Utf8.Decode(new byte[] { 0xEF, 0xBB, 0xBF, 0x78 }), "a BOM is kept, as Python's utf-8 keeps it");
        });
        Run("card parity: a zip whose additions aren't Domovoi's shape is refused", delegate
        {
            True(BootText.BlockFromZip("dtparam=x=1\n") == null, "no marker");
            True(BootText.BlockFromZip(BootText.Marker + "\n" + BootText.EndMarker) == null, "no final newline");
            True(BootText.BlockFromZip(BootText.Marker + "\r\nx=1\r\n" + BootText.EndMarker + "\r\n") == null, "CRLF");
            True(BootText.BlockFromZip(BootText.Marker + "\n#comment\n" + BootText.EndMarker + "\n") == null, "not a setting");
            True(BootText.TokensFromZip("quiet\n") == null, "no first-boot hook");
            True(BootText.TokensFromZip(BootText.FirstrunToken + "\nmore\n") == null, "two lines");
            True(BootText.TokensFromZip(BootText.FirstrunToken + "  x=1\n") == null, "doubled space");
        });
    }

    // -- the zip ----------------------------------------------------------

    sealed class ZipSpec
    {
        public string Transport = "portal", Board = "pi02w", Ssid = "Domovoi-Setup-3FA9";
        public int Schema = 1;
        public byte[] Payload = Encoding.ASCII.GetBytes("a payload, not really a tar");
        public string Sha = null;   // default: the payload's
        public string Flags = "portal";
        public List<KeyValuePair<string, byte[]>> Extra = new List<KeyValuePair<string, byte[]>>();
        public HashSet<string> Skip = new HashSet<string>();
    }

    static string MakeZip(string dir, ZipSpec s)
    {
        string path = Path.Combine(dir, "domovoi-satellite-overlay.zip");
        if (File.Exists(path)) File.Delete(path);
        var z = (Dictionary<string, object>)Json.Obj(Goldens(), "zips")[s.Flags];
        var files = new List<KeyValuePair<string, byte[]>>
        {
            new KeyValuePair<string, byte[]>("README.txt", Encoding.ASCII.GetBytes("Domovoi satellite overlay\n")),
            new KeyValuePair<string, byte[]>("config.txt", B64(z["config"])),
            new KeyValuePair<string, byte[]>("cmdline.txt", B64(z["cmdline"])),
            new KeyValuePair<string, byte[]>("userconf.txt", Encoding.ASCII.GetBytes("domovoi:$6$salt$hash\n")),
            new KeyValuePair<string, byte[]>("domovoi/firstrun.sh", Encoding.ASCII.GetBytes("#!/bin/sh\necho stage 1\n")),
            new KeyValuePair<string, byte[]>("domovoi/payload.tar.gz", s.Payload),
            new KeyValuePair<string, byte[]>("domovoi/payload.sha256", Encoding.ASCII.GetBytes((s.Sha ?? CardZip.Sha256Hex(s.Payload)) + "  payload.tar.gz\n")),
            new KeyValuePair<string, byte[]>("domovoi/build-info.json", Encoding.UTF8.GetBytes("{\n  \"schema\": " + Inv.S(s.Schema) + ",\n  \"board\": \"" + s.Board + "\",\n  \"mic_profile\": \"respeaker_2mic_hat_v2\",\n  \"built_at\": \"2026-10-16T21:04:11-0400\",\n  \"offline\": true\n}")),
            new KeyValuePair<string, byte[]>("domovoi/device-info.json", Encoding.UTF8.GetBytes("{\"domovoi_setup\": 1, \"setup_transport\": \"" + s.Transport + "\", \"ap_ssid\": " + (s.Transport == "portal" ? "\"" + s.Ssid + "\"" : "null") + "}")),
            new KeyValuePair<string, byte[]>("domovoi/server-identity.json", Encoding.UTF8.GetBytes("{\"fingerprint\": \"ab:cd\"}")),
        };
        files.AddRange(s.Extra);
        using (ZipArchive zip = ZipFile.Open(path, ZipArchiveMode.Create))
            foreach (var f in files)
            {
                if (s.Skip.Contains(f.Key)) continue;
                using (Stream w = zip.CreateEntry(f.Key).Open()) w.Write(f.Value, 0, f.Value.Length);
            }
        return path;
    }

    static void Refused(string path, string needle)
    {
        try { CardZip.Open(path); }
        catch (CardZipException e)
        {
            True(e.Message.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0, "message \"" + e.Message + "\" lacks \"" + needle + "\"");
            return;
        }
        Fail("accepted a zip it should refuse (" + needle + ")");
    }

    static void CardZipChecks()
    {
        Run("card zip: a dashboard zip opens, with what the card needs", delegate
        {
            string d = TempDir("zip-ok");
            CardPackage p = CardZip.Open(MakeZip(d, new ZipSpec()));
            Eq("pi02w", p.Board, "board");
            Eq("bcm2710-rpi-zero-2-w.dtb", p.BootMarker, "marker");
            True(p.IsPortal, "portal");
            Eq("Domovoi-Setup-3FA9", p.ApSsid, "ssid");
            SeqEq(new[] { "domovoi\\build-info.json", "domovoi\\device-info.json", "domovoi\\firstrun.sh", "domovoi\\payload.sha256", "domovoi\\payload.tar.gz", "domovoi\\server-identity.json", "userconf.txt" },
                p.Files.Select(f => f.Dest).OrderBy(x => x, StringComparer.Ordinal), "files to copy (no README, no config/cmdline)");
            True(p.Summary().Contains("Domovoi-Setup-3FA9"), "summary names the network");
        });
        Run("card zip: a USB card opens without a network", delegate
        {
            CardPackage p = CardZip.Open(MakeZip(TempDir("zip-usb"), new ZipSpec { Transport = "usb", Flags = "usb" }));
            True(!p.IsPortal && p.ApSsid == null, "usb");
        });
        Run("card zip: the newest card zip in a folder, a browser's (1) copy included", delegate
        {
            string d = TempDir("zip-newest");
            File.WriteAllText(Path.Combine(d, "domovoi-satellite-overlay.zip"), "old");
            File.SetLastWriteTimeUtc(Path.Combine(d, "domovoi-satellite-overlay.zip"), DateTime.UtcNow.AddHours(-2));
            File.WriteAllText(Path.Combine(d, "domovoi-satellite-overlay (1).zip"), "new");
            File.WriteAllText(Path.Combine(d, "other.zip"), "x");
            Eq(Path.Combine(d, "domovoi-satellite-overlay (1).zip"), CardZip.Newest(d), "newest");
            True(CardZip.Newest(Path.Combine(d, "missing")) == null, "no folder");
        });
        Run("card zip: refused when it isn't one, or is damaged", delegate
        {
            string d = TempDir("zip-bad");
            string notZip = Path.Combine(d, "x.zip");
            File.WriteAllText(notZip, "hello");
            Refused(notZip, "isn't a zip");
            Refused(Path.Combine(d, "missing.zip"), "no file");
            var s = new ZipSpec();
            s.Skip.Add("domovoi/build-info.json");
            Refused(MakeZip(d, s), "build-info.json");
            s = new ZipSpec();
            s.Skip.Add("domovoi/payload.sha256");
            Refused(MakeZip(d, s), "payload.sha256");
            Refused(MakeZip(d, new ZipSpec { Sha = new string('0', 64) }), "damaged");
            Refused(MakeZip(d, new ZipSpec { Schema = 2 }), "newer Domovoi");
            Refused(MakeZip(d, new ZipSpec { Board = "radxa-zero3w" }), "doesn't know");
            Refused(MakeZip(d, new ZipSpec { Ssid = "" }), "setup network");
            Refused(MakeZip(d, new ZipSpec { Transport = "carrier-pigeon" }), "setup route");
        });
        Run("card zip: names that could land outside the card's domovoi folder are refused", delegate
        {
            string d = TempDir("zip-names");
            foreach (string evil in new[] { "../boot.txt", "domovoi/../../x", "domovoi/sub/x.sh", "C:/Windows/x", "/etc/passwd", "domovoi/.hidden", "kernel8.img", "domovoi\\x" })
            {
                var s = new ZipSpec();
                s.Extra.Add(new KeyValuePair<string, byte[]>(evil, new byte[] { 1 }));
                Refused(MakeZip(d, s), "never does");
            }
            // ap.json or console.json in a zip are never copied: the helper writes its own ap.json.
            var t = new ZipSpec();
            t.Extra.Add(new KeyValuePair<string, byte[]>("domovoi/ap.json", Encoding.ASCII.GetBytes("{}")));
            True(CardZip.Open(MakeZip(d, t)).Files.All(f => f.Dest != "domovoi\\ap.json"), "ap.json from a zip is not copied");
        });
        Run("card zip: device names, FAT's case-blind names and trailing dots are refused", delegate
        {
            string d = TempDir("zip-fat");
            foreach (string evil in new[] { "domovoi/NUL", "domovoi/con.txt", "domovoi/COM1", "domovoi/lpt9.log", "domovoi/notes." })
            {
                var s1 = new ZipSpec();
                s1.Extra.Add(new KeyValuePair<string, byte[]>(evil, new byte[] { 1 }));
                Refused(MakeZip(d, s1), "never does");
            }
            foreach (string twin in new[] { "domovoi/PAYLOAD.TAR.GZ", "domovoi/FirstRun.sh", "Config.txt", "domovoi/firstrun.sh." })
            {
                var s2 = new ZipSpec();
                s2.Extra.Add(new KeyValuePair<string, byte[]>(twin, new byte[] { 1 }));
                Refused(MakeZip(d, s2), "twice");
            }
        });
        Run("card zip: built by this PC's Domovoi, another server's, or can't tell", delegate
        {
            CardPackage p = CardZip.Open(MakeZip(TempDir("zip-origin"), new ZipSpec()));
            Eq("ab:cd", p.ServerFingerprint, "the zip's server identity");
            Eq(CardZip.Origin.ThisServer, CardZip.FromThisServer("ab:cd", "ab:cd"), "same");
            Eq(CardZip.Origin.OtherServer, CardZip.FromThisServer("ab:cd", "ef:01"), "another server");
            Eq(CardZip.Origin.Unknown, CardZip.FromThisServer("ab:cd", null), "this PC's Domovoi not answering");
            Eq(CardZip.Origin.Unknown, CardZip.FromThisServer(null, "ab:cd"), "a zip without an identity");
        });
        Run("card zip: the setup password's rules (overlay.py's alphabet and length)", delegate
        {
            True(SetupPassword.Problem("k7tqmzr4wd") == null, "a good one");
            True(SetupPassword.Problem(" K7TQ MZR4WD ") == null, "spaces and capitals are forgiven");
            Eq("k7tqmzr4wd", SetupPassword.Normalize(" K7TQ MZR4WD "), "normalized");
            True(SetupPassword.Problem("k7tqmzr4w").Contains("9 characters"), "too short");
            True(SetupPassword.Problem("k7tqmzr4wdd").Contains("11 characters"), "too long");
            True(SetupPassword.Problem("k7tqmzr4w0").Contains("never use 0, 1, l or o"), "a 0");
            True(SetupPassword.Problem("k7tqmzr4w!").Contains("isn't in these passwords"), "punctuation");
            True(SetupPassword.Problem("") != null, "empty");
        });
    }

    // -- the drive list ---------------------------------------------------

    sealed class FakeDrives : IDriveSource
    {
        public List<DriveFacts> Drives = new List<DriveFacts>();
        public Dictionary<string, string> Files = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        public List<DriveFacts> List() { return Drives; }
        public bool FileExists(string p) { return Files.ContainsKey(p); }
        public string ReadAllText(string p) { string v; if (!Files.TryGetValue(p, out v)) throw new FileNotFoundException(p); return v; }

        public void Boot(string root, bool prepared, bool dtb)
        {
            Files[Path.Combine(root, "config.txt")] = prepared ? "dtparam=audio=on\n" + BootText.Marker + "\n" : "dtparam=audio=on\n";
            Files[Path.Combine(root, "cmdline.txt")] = "console=tty1 rootwait\n";
            if (dtb) Files[Path.Combine(root, "bcm2710-rpi-zero-2-w.dtb")] = "";
        }
    }

    static DriveFacts D(string root, bool removable, string format, bool ready)
    {
        return new DriveFacts { Root = root, Label = "bootfs", Format = format, Removable = removable, Ready = ready, Total = 512L << 20, Free = 400L << 20 };
    }

    static void CardDriveFilter()
    {
        Run("card drives: only removable FAT drives that look like a flashed Pi card", delegate
        {
            var f = new FakeDrives();
            f.Drives.Add(D(@"C:\", false, "NTFS", true));       // the system drive
            f.Drives.Add(D(@"D:\", false, "FAT32", true));      // a fixed disk, even FAT with Pi files
            f.Drives.Add(D(@"E:\", true, "FAT32", true));       // the card
            f.Drives.Add(D(@"F:\", true, "NTFS", true));        // a USB stick
            f.Drives.Add(D(@"G:\", true, "FAT32", false));      // an empty card reader
            f.Drives.Add(D(@"H:\", true, "FAT32", true));       // a FAT stick without Pi files
            f.Drives.Add(D(@"I:\", true, "exFAT", true));       // exFAT: no Pi boots from it
            f.Drives.Add(D(@"J:\", true, "FAT32", true));       // a Pi card for another board (no dtb)
            f.Boot(@"D:\", false, true);
            f.Boot(@"E:\", false, true);
            f.Boot(@"F:\", false, true);
            f.Boot(@"I:\", false, true);
            f.Boot(@"J:\", false, false);
            int others;
            var list = CardDrives.Find(f, "bcm2710-rpi-zero-2-w.dtb", out others);
            SeqEq(new[] { @"E:\" }, list.Select(t => t.Drive.Root), "offered");
            Eq(5, others, "other removable drives counted, fixed ones not");
        });
        Run("card drives: a removable C: or the Windows drive is never offered", delegate
        {
            var f = new FakeDrives();
            string sys = Path.GetPathRoot(Environment.GetFolderPath(Environment.SpecialFolder.Windows));
            f.Drives.Add(D(sys, true, "FAT32", true));
            f.Drives.Add(D(@"C:\", true, "FAT32", true));
            f.Boot(sys, false, true);
            f.Boot(@"C:\", false, true);
            int others;
            Eq(0, CardDrives.Find(f, "bcm2710-rpi-zero-2-w.dtb", out others).Count, "none");
        });
        Run("card drives: an already prepared card is marked", delegate
        {
            var f = new FakeDrives();
            f.Drives.Add(D(@"E:\", true, "FAT32", true));
            f.Drives.Add(D(@"K:\", true, "FAT", true));
            f.Boot(@"E:\", true, true);
            f.Boot(@"K:\", false, true);
            int others;
            var list = CardDrives.Find(f, "bcm2710-rpi-zero-2-w.dtb", out others);
            True(list[0].Prepared && !list[1].Prepared, "prepared flag");
            True(list[0].Title().StartsWith("E:  bootfs", StringComparison.Ordinal), list[0].Title());
        });
    }

    // -- writing a card ---------------------------------------------------

    static string FlashedCard(string name, string config, string cmdline)
    {
        string root = TempDir(name);
        File.WriteAllText(Path.Combine(root, "config.txt"), config, new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(root, "cmdline.txt"), cmdline, new UTF8Encoding(false));
        File.WriteAllBytes(Path.Combine(root, "bcm2710-rpi-zero-2-w.dtb"), new byte[] { 0xd0, 0x0d, 0xfe, 0xed });
        File.WriteAllBytes(Path.Combine(root, "kernel8.img"), new byte[] { 1, 2, 3 });
        return root;
    }

    static string GoldenInput(string name, string which)
    {
        foreach (var i in Json.ObjList(Goldens(), "inputs"))
            if (Json.Str(i, "name") == name) return Utf(B64(i[which]));
        throw new InvalidOperationException("no golden input " + name);
    }

    static Dictionary<string, object> GoldenCase(string name, string flags)
    {
        foreach (var c in Json.ObjList(Goldens(), "cases"))
            if (Json.Str(c, "name") == name && Json.Str(c, "flags") == flags) return c;
        throw new InvalidOperationException("no golden case " + name);
    }

    static void CardWrites()
    {
        Run("card write: a stock bookworm card gets the server's files and edits, checked", delegate
        {
            string zipDir = TempDir("write-zip");
            var spec = new ZipSpec();
            CardPackage p = CardZip.Open(MakeZip(zipDir, spec));
            string card = FlashedCard("write-card", GoldenInput("bookworm stock", "config"), GoldenInput("bookworm stock", "cmdline"));
            var progress = new List<int>();
            CardWriteResult r = CardWriter.Write(p, card, "k7tqmzr4wd", (m, pct) => progress.Add(pct));
            var c = GoldenCase("bookworm stock", "portal");
            Eq(Json.Str(c, "config_sha256"), CardZip.Sha256Hex(File.ReadAllBytes(Path.Combine(card, "config.txt"))), "config.txt as the server writes it");
            Eq(Json.Str(c, "cmdline_sha256"), CardZip.Sha256Hex(File.ReadAllBytes(Path.Combine(card, "cmdline.txt"))), "cmdline.txt as the server writes it");
            True(File.ReadAllBytes(Path.Combine(card, "domovoi", "payload.tar.gz")).SequenceEqual(spec.Payload), "payload copied");
            True(File.ReadAllBytes(Path.Combine(card, "domovoi", "ap.json")).SequenceEqual(ApJson.Bytes("Domovoi-Setup-3FA9", "k7tqmzr4wd")), "ap.json");
            True(File.Exists(Path.Combine(card, "userconf.txt")), "userconf.txt at the root");
            True(!File.Exists(Path.Combine(card, "README.txt")), "no README on the card");
            True(File.ReadAllBytes(Path.Combine(card, "kernel8.img")).SequenceEqual(new byte[] { 1, 2, 3 }), "the card's own files untouched");
            True(r.Checked.Contains("domovoi/payload.tar.gz against payload.sha256") && r.Checked.Contains("cmdline.txt"), "checked");
            Eq("cmdline.txt", r.Written[r.Written.Count - 1], "the boot hook is written last");
            Eq(100, progress[progress.Count - 1], "progress ends at 100");
        });
        Run("card write: writing the same card again changes nothing but the copies", delegate
        {
            CardPackage p = CardZip.Open(MakeZip(TempDir("again-zip"), new ZipSpec()));
            string card = FlashedCard("again-card", GoldenInput("trixie CRLF", "config"), GoldenInput("trixie CRLF", "cmdline"));
            CardWriter.Write(p, card, "k7tqmzr4wd", null);
            byte[] cfg = File.ReadAllBytes(Path.Combine(card, "config.txt")), cmd = File.ReadAllBytes(Path.Combine(card, "cmdline.txt"));
            var c = GoldenCase("trixie CRLF", "portal");
            Eq(Json.Str(c, "config_sha256"), CardZip.Sha256Hex(cfg), "first write: the server's bytes");
            CardWriteResult r = CardWriter.Write(p, card, "k7tqmzr4wd", null);
            True(!r.ConfigChanged && !r.CmdlineChanged, "no second edit");
            True(File.ReadAllBytes(Path.Combine(card, "config.txt")).SequenceEqual(cfg) && File.ReadAllBytes(Path.Combine(card, "cmdline.txt")).SequenceEqual(cmd), "same bytes");
        });
        Run("card write: a USB-mic Wi-Fi card and a USB card", delegate
        {
            CardPackage p = CardZip.Open(MakeZip(TempDir("mic-zip"), new ZipSpec { Flags = "portal-usb-mic" }));
            string card = FlashedCard("mic-card", GoldenInput("an existing dwc_otg.speed", "config"), GoldenInput("an existing dwc_otg.speed", "cmdline"));
            CardWriter.Write(p, card, "k7tqmzr4wd", null);
            Eq(Json.Str(GoldenCase("an existing dwc_otg.speed", "portal-usb-mic"), "cmdline_sha256"), CardZip.Sha256Hex(File.ReadAllBytes(Path.Combine(card, "cmdline.txt"))), "dwc_otg.speed kept as it was");
            CardPackage u = CardZip.Open(MakeZip(TempDir("usb-zip"), new ZipSpec { Transport = "usb", Flags = "usb" }));
            string ucard = FlashedCard("usb-card", GoldenInput("bookworm stock", "config"), GoldenInput("bookworm stock", "cmdline"));
            CardWriter.Write(u, ucard, null, null);
            True(!File.Exists(Path.Combine(ucard, "domovoi", "ap.json")), "a USB card has no ap.json");
            Eq(Json.Str(GoldenCase("bookworm stock", "usb"), "config_sha256"), CardZip.Sha256Hex(File.ReadAllBytes(Path.Combine(ucard, "config.txt"))), "usb config");
        });
        Run("card write: refused before anything is written", delegate
        {
            CardPackage p = CardZip.Open(MakeZip(TempDir("refuse-zip"), new ZipSpec()));
            string card = FlashedCard("refuse-card", "x=1\n", "rootwait\n");
            Throws<CardWriteException>(() => CardWriter.Write(p, card, "k7tqmzr4w0", null), "a wrong password");
            Eq(0, Directory.GetFileSystemEntries(card).Count(e => e.EndsWith("domovoi", StringComparison.Ordinal)), "no domovoi folder after a refusal");
            File.Delete(Path.Combine(card, "bcm2710-rpi-zero-2-w.dtb"));
            Throws<CardWriteException>(() => CardWriter.Write(p, card, "k7tqmzr4wd", null), "not a Pi card any more");
            Eq("x=1\n", File.ReadAllText(Path.Combine(card, "config.txt")), "config untouched");
            Throws<CardWriteException>(() => CardWriter.CheckFree(10L << 20, 300L << 20), "too little room");
            CardWriter.CheckFree(300L << 20, 300L << 20);
        });
        Run("card write: a card already running another first-boot script is caught", delegate
        {
            var tokens = BootText.TokensFromZip(Utf(B64(((Dictionary<string, object>)Json.Obj(Goldens(), "zips")["portal"])["cmdline"])));
            True(CardChecks.CmdlineProblem(GoldenInput("another first-boot script (Imager)", "cmdline"), tokens) != null, "Imager's systemd.run");
            True(CardChecks.CmdlineProblem(GoldenInput("bookworm stock", "cmdline"), tokens) == null, "stock");
            True(CardChecks.CmdlineProblem(GoldenInput("the first-boot hook already there", "cmdline"), tokens) == null, "ours");
        });
        Run("card write: eject takes only a drive letter", delegate
        {
            Eq(@"\\.\E:", Eject.VolumePath(@"e:\"), "E:");
            Eq(@"\\.\E:", Eject.VolumePath("E:"), "E: without a slash");
            Throws<ArgumentException>(() => Eject.VolumePath(@"C:\Users\x"), "a path");
            Throws<ArgumentException>(() => Eject.VolumePath(@"\\server\share"), "a share");
        });
    }

    // -- status -----------------------------------------------------------

    static void TrayStatus()
    {
        DateTime now = new DateTime(2026, 10, 17, 12, 0, 0, DateTimeKind.Utc);
        TimeSpan grace = TimeSpan.FromMinutes(10);
        Func<Facts, DateTime, Verdict> decide = (f, since) => Status.Decide(f, since, now, grace);
        Run("status: what the icon says", delegate
        {
            Eq(TrayState.Running, decide(new Facts { Service = ServiceFact.Running, Web = WebFact.Ok }, now).State, "healthy");
            Eq(TrayState.Running, decide(new Facts { Service = ServiceFact.NotInstalled, Web = WebFact.Ok }, now).State, "answering without the service (a hand install)");
            Eq(TrayState.Stopped, decide(new Facts { Service = ServiceFact.Stopped }, now).State, "stopped");
            Eq("Domovoi is stopped", decide(new Facts { Service = ServiceFact.Stopped }, now).Line, "stopped words");
            Eq(TrayState.Starting, decide(new Facts { Service = ServiceFact.Starting }, now.AddHours(-3)).State, "the service starting");
            Eq(TrayState.Starting, decide(new Facts { Service = ServiceFact.Stopping }, now).State, "stopping");
            Eq(TrayState.Starting, decide(new Facts { Service = ServiceFact.Running }, now.AddMinutes(-9)).State, "inside the grace");
            Verdict late = decide(new Facts { Service = ServiceFact.Running }, now.AddMinutes(-11));
            Eq(TrayState.Problem, late.State, "past the grace");
            Eq("Nothing is answering.", late.Detail, "nothing");
            Eq("The voice service isn't running.", decide(new Facts { Service = ServiceFact.Running, Web = WebFact.Degraded, DbReachable = true, CoreAlive = false }, now.AddHours(-1)).Detail, "core down");
            Eq("Its database isn't answering.", decide(new Facts { Service = ServiceFact.Running, Web = WebFact.Degraded, DbReachable = false, CoreAlive = true }, now.AddHours(-1)).Detail, "db down");
            Eq("The dashboard isn't answering.", decide(new Facts { Service = ServiceFact.Running, CoreAlive = true }, now.AddHours(-1)).Detail, "web down");
            Eq("Domovoi isn't installed on this PC", decide(new Facts { Service = ServiceFact.NotInstalled }, now).Line, "not installed");
            foreach (ServiceFact sf in Enum.GetValues(typeof(ServiceFact)))
                foreach (WebFact wf in Enum.GetValues(typeof(WebFact)))
                {
                    Verdict v = decide(new Facts { Service = sf, Web = wf }, now.AddHours(-1));
                    True(v.Line.Length > 0 && v.Line.Length <= 63, "tooltip fits: " + v.Line);
                }
        });
        Run("status: the grace counts from the first poll that saw it up but not ready", delegate
        {
            var clock = new ReadyClock();
            Eq(now, clock.Since(new Facts { Service = ServiceFact.Stopped }, now), "stopped: no clock");
            Eq(now.AddMinutes(1), clock.Since(new Facts { Service = ServiceFact.Running }, now.AddMinutes(1)), "starts");
            Eq(now.AddMinutes(1), clock.Since(new Facts { Service = ServiceFact.Running }, now.AddMinutes(5)), "keeps its start");
            clock.Since(new Facts { Service = ServiceFact.Running, Web = WebFact.Ok }, now.AddMinutes(6));
            Eq(now.AddMinutes(7), clock.Since(new Facts { Service = ServiceFact.Running }, now.AddMinutes(7)), "restarts after a healthy poll");
        });
        Run("status: notifications only when Domovoi goes down or comes back", delegate
        {
            var n = new Notifier();
            Func<TrayState, string[]> next = s => n.Next(new Verdict { State = s, Line = s == TrayState.Stopped ? "Domovoi is stopped" : "x", Detail = "d" });
            True(next(TrayState.Running) == null, "not at start");
            True(next(TrayState.Running) == null, "not every poll");
            True(next(TrayState.Starting) == null, "not while restarting");
            True(next(TrayState.Running) == null, "an update that came back: nothing");
            Eq("Domovoi is stopped", next(TrayState.Stopped)[0], "down");
            True(next(TrayState.Problem) == null, "still down");
            Eq("Domovoi is running again", next(TrayState.Running)[0], "back");
            var m = new Notifier();
            True(m.Next(new Verdict { State = TrayState.Problem, Line = "Domovoi has a problem" }) == null, "a tray that starts on a problem says nothing yet");
        });
    }

    // -- network addresses ------------------------------------------------

    static AdapterFacts A(string name, bool up, string gw, params string[] ips)
    {
        var a = new AdapterFacts { Name = name, Up = up };
        if (gw != null) a.Gateways.Add(gw);
        a.Addresses.AddRange(ips);
        return a;
    }

    static void TrayAddresses()
    {
        Run("addresses: the adapter with the default route, private addresses only", delegate
        {
            var list = Addresses.Pick(new[]
            {
                A("Ethernet", true, "192.168.1.1", "192.168.1.30"),
                A("vEthernet (Default Switch)", true, null, "172.20.16.1"),
                A("Tailscale", true, "0.0.0.0", "100.101.102.103"),
                A("Wi-Fi", false, "192.168.1.1", "192.168.1.31"),
                new AdapterFacts { Name = "Loopback", Up = true, LoopbackOrTunnel = true, Gateways = { "127.0.0.1" }, Addresses = { "127.0.0.1" } },
                A("VPN", true, "10.8.0.1", "203.0.113.5"),
            });
            SeqEq(new[] { "192.168.1.30 Ethernet" }, list.Select(x => x.Address + " " + x.Adapter), "one");
            var two = Addresses.Pick(new[] { A("Ethernet", true, "192.168.1.1", "192.168.1.30"), A("Wi-Fi", true, "192.168.1.1", "192.168.1.31", "192.168.1.30") });
            SeqEq(new[] { "192.168.1.30", "192.168.1.31" }, two.Select(x => x.Address), "two, each once");
            Eq("http://192.168.1.30:6369", Addresses.Url("http://localhost:6369", "192.168.1.30"), "url");
            Eq("http://192.168.1.30:6390", Addresses.Url("http://127.0.0.1:6390", "192.168.1.30"), "a developer's port");
        });
    }

    // -- sign-in registration ---------------------------------------------

    sealed class MemStore : ITrayStore
    {
        public string Machine, User;
        public Dictionary<string, int> Values = new Dictionary<string, int>();
        public string MachineRun() { return Machine; }
        public string UserRun() { return User; }
        public void SetUserRun(string c) { User = c; }
        public int? Dword(string n) { int v; return Values.TryGetValue(n, out v) ? (int?)v : null; }
        public void SetDword(string n, int v) { Values[n] = v; }
    }

    static void TrayStartup()
    {
        const string installed = @"C:\Program Files\Domovoi\DomovoiTray.exe";
        const string copy = @"C:\Users\alex\Downloads\DomovoiTray.exe";
        Func<string, bool> exists = p => p == installed || p == copy;
        Run("startup: a copy run on its own registers itself for this account's sign-in", delegate
        {
            var s = new MemStore();
            string to;
            Eq(LaunchAction.Continue, Startup.OnLaunch(s, copy, false, exists, out to), "continue");
            Eq("\"" + copy + "\" --autostart", s.User, "HKCU Run");
            Startup.SetStartWithWindows(s, copy, false, exists);
            True(s.User == null && s.Values[Startup.StartPref] == 0, "off removes it");
            Eq(LaunchAction.Quit, Startup.OnLaunch(s, copy, true, exists, out to), "the sign-in start of an account that said no");
            True(s.User == null, "and doesn't put it back");
            Eq(LaunchAction.Continue, Startup.OnLaunch(s, copy, false, exists, out to), "started by hand");
            True(s.User == null, "still off");
            Startup.SetStartWithWindows(s, copy, true, exists);
            Eq("\"" + copy + "\" --autostart", s.User, "on again");
        });
        Run("startup: Setup's machine-wide start wins; a per-account copy steps aside", delegate
        {
            var s = new MemStore { Machine = "\"" + installed + "\" --autostart", User = "\"" + copy + "\" --autostart" };
            string to;
            Eq(LaunchAction.HandOver, Startup.OnLaunch(s, copy, false, exists, out to), "hand over");
            Eq(installed, to, "to Setup's copy");
            True(s.User == null, "the per-account start removed");
            Eq(LaunchAction.Continue, Startup.OnLaunch(s, installed, true, exists, out to), "Setup's copy runs");
            Startup.SetStartWithWindows(s, installed, false, exists);
            True(s.User == null && s.Values[Startup.StartPref] == 0, "off is this account's setting; HKLM untouched");
            Eq(LaunchAction.Quit, Startup.OnLaunch(s, installed, true, exists, out to), "then its sign-in start does nothing");
            Eq(LaunchAction.Continue, Startup.OnLaunch(new MemStore { Machine = "\"" + @"C:\gone\DomovoiTray.exe" + "\" --autostart" }, copy, false, exists, out to), "a machine value whose exe is gone is ignored");
        });
        Run("startup: a copy run on its own registers a private copy, which Setup's later replaces", delegate
        {
            string dl = TempDir("dl"), local = Path.Combine(TempDir("local"), "Programs-Domovoi");
            string exe = Path.Combine(dl, "DomovoiTray.exe");
            File.WriteAllText(exe, "v1");
            string reg = LocalCopy.Ensure(exe, null, local);
            Eq(Path.Combine(local, "DomovoiTray.exe"), reg, "registers the private copy");
            Eq("v1", File.ReadAllText(reg), "copied");
            File.WriteAllText(exe, "v2");
            Eq("v2", File.ReadAllText(LocalCopy.Ensure(exe, null, local)), "a newer download refreshes it");
            Eq(reg, LocalCopy.Ensure(reg, null, local), "run from the private copy: itself");
            Eq(exe, LocalCopy.Ensure(exe, @"C:\Program Files\Domovoi\DomovoiTray.exe", local), "Setup's copy installed: nothing to register");
            True(!File.Exists(reg), "and the private copy goes");
        });
        Run("startup: the Run command's exe", delegate
        {
            Eq(installed, Startup.ExeOf("\"" + installed + "\" --autostart"), "quoted");
            Eq(@"C:\x\DomovoiTray.exe", Startup.ExeOf(@"C:\x\DomovoiTray.exe --autostart"), "bare");
            True(Startup.ExeOf(null) == null && Startup.ExeOf("") == null, "none");
            True(Startup.KeepAwake(new MemStore()) && Startup.StartWithWindows(new MemStore()), "both on by default");
        });
    }

    // -- the command line -------------------------------------------------

    static void TrayOptions()
    {
        Run("options: the real tray by default; any override is a developer run", delegate
        {
            Options o = Options.Parse(new string[0]);
            True(!o.Dev && o.WebUrl == "http://localhost:6369" && o.Service == "DomovoiHost", "defaults");
            o = Options.Parse(new[] { "--autostart" });
            True(o.Autostart && !o.Dev, "autostart");
            o = Options.Parse(new[] { "--web-url", "http://127.0.0.1:6390/", "--core-url", "http://127.0.0.1:6394", "--service", "DomovoiHostTest" });
            True(o.Dev && o.Profile == "dev", "developer profile implied");
            Eq("http://127.0.0.1:6390", o.WebUrl, "web url tidied");
            Eq("DomovoiHostTest", o.Service, "a fake service");
            True(Options.Parse(new[] { "--service", "-" }).Service == null, "no service");
            Eq(30.0, Options.Parse(new[] { "--grace", "30" }).Grace.TotalSeconds, "grace");
            Throws<ArgumentException>(() => Options.Parse(new[] { "--web-url", "file:///c:/x" }), "not http");
            Throws<ArgumentException>(() => Options.Parse(new[] { "--web-url", "http://u:p@localhost:6369" }), "a password in the URL");
            Throws<ArgumentException>(() => Options.Parse(new[] { "--service", "Domovoi Host'; x" }), "a service name with quotes");
            Throws<ArgumentException>(() => Options.Parse(new[] { "--profile", "../x" }), "a profile path");
            Throws<ArgumentException>(() => Options.Parse(new[] { "--bogus" }), "unknown");
            Eq("http://localhost:6369/#manual", TrayApp.ManualUrl(Options.Parse(new string[0]).WebUrl), "the user manual");
            Eq("http://127.0.0.1:6369", Probe.Loopback("http://localhost:6369"), "probes go to IPv4 loopback");
            Eq("http://127.0.0.1:6390", Probe.Loopback("http://127.0.0.1:6390"), "an address stays");
            Eq("http://192.168.1.30:6369", Probe.Loopback("http://192.168.1.30:6369"), "a LAN address stays");
            Eq("http://127.0.0.1:6390/#manual", TrayApp.ManualUrl(Options.Parse(new[] { "--web-url", "http://127.0.0.1:6390" }).WebUrl), "the manual follows --web-url");
        });
        Run("admin: restart and copy-logs commands", delegate
        {
            Eq("-NoProfile -NonInteractive -WindowStyle Hidden -Command \"$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine'); "
                + @"Microsoft.PowerShell.Management\Restart-Service -Name 'DomovoiHost'" + "\"", Admin.RestartArguments("DomovoiHost"), "restart, from the machine's modules only");
            True(Admin.InsideProfile(@"C:\Users\alex\Documents", @"C:\Users\alex"), "Documents in the profile");
            True(Admin.InsideProfile(@"C:\Users\alex\OneDrive\Documents", @"C:\Users\alex\"), "OneDrive's Documents");
            True(!Admin.InsideProfile(@"C:\Windows\System32", @"C:\Users\alex"), "a Documents pointed at Windows");
            True(!Admin.InsideProfile(@"C:\Users\alexander\Documents", @"C:\Users\alex"), "another profile with the same start");
            Throws<ArgumentException>(() => Admin.RestartArguments("x'; Stop-Computer; '"), "no injection");
            Throws<ArgumentException>(() => Admin.RestartArguments(null), "no service");
            True(Admin.PowerShell.StartsWith(Environment.SystemDirectory, StringComparison.OrdinalIgnoreCase), "System32's PowerShell");
            Eq("copy-logs --kind install --dest \"C:\\Users\\alex\\Documents\\Domovoi logs\"", Admin.CopyLogsArguments(@"C:\Users\alex\Documents\Domovoi logs\"), "copy logs");
        });
    }

    // -- the phone code ---------------------------------------------------

    static void TrayQr()
    {
        Run("qr: versions, finder patterns, timing and format bits", delegate
        {
            QrCode q = QrCode.EncodeText("http://192.168.1.30:6369", QrEcc.Medium, -1, true);
            Eq(2, q.Version, "a dashboard address fits version 2");
            Eq(25, q.Size, "size");
            foreach (int[] corner in new[] { new[] { 0, 0 }, new[] { q.Size - 7, 0 }, new[] { 0, q.Size - 7 } })
                for (int dy = 0; dy < 7; dy++)
                    for (int dx = 0; dx < 7; dx++)
                    {
                        int d = Math.Max(Math.Abs(dx - 3), Math.Abs(dy - 3));
                        Eq(d != 2, q.Dark(corner[0] + dx, corner[1] + dy), "finder module");
                    }
            for (int i = 8; i < q.Size - 8; i++) Eq(i % 2 == 0, q.Dark(i, 6), "timing row");
            True(q.Dark(8, q.Size - 8), "the dark module");
            // The format bits read back (both copies) as this code's ECC and mask.
            int bits = 0;
            for (int i = 0; i <= 5; i++) if (q.Dark(8, i)) bits |= 1 << i;
            if (q.Dark(8, 7)) bits |= 1 << 6;
            if (q.Dark(8, 8)) bits |= 1 << 7;
            if (q.Dark(7, 8)) bits |= 1 << 8;
            for (int i = 9; i < 15; i++) if (q.Dark(14 - i, 8)) bits |= 1 << i;
            int bits2 = 0;
            for (int i = 0; i < 8; i++) if (q.Dark(q.Size - 1 - i, 8)) bits2 |= 1 << i;
            for (int i = 8; i < 15; i++) if (q.Dark(8, q.Size - 15 + i)) bits2 |= 1 << i;
            Eq(bits, bits2, "two copies agree");
            int data = (bits ^ 0x5412) >> 10;
            int[] eccBits = { 1, 0, 3, 2 };
            Eq(eccBits[(int)q.Ecc], data >> 3, "ecc in the format bits");
            Eq(q.Mask, data & 7, "mask in the format bits");
            True(QrCode.EncodeText(new string('a', 300), QrEcc.Low, -1, false).Version >= 9, "long text encodes");
            True(QrCode.EncodeText(new string('a', 400), QrEcc.Medium, -1, true).Version >= 10, "16-bit count from version 10");
        });
        Run("qr: matches the reference encoder module for module", delegate
        {
            foreach (string[] r in QrReference.Cases)
            {
                QrCode q = QrCode.EncodeText(r[0], (QrEcc)int.Parse(r[1]), int.Parse(r[2]), false);
                Eq(r[3], q.Text(), "\"" + r[0] + "\" ecc " + r[1] + " mask " + r[2]);
            }
            True(QrReference.Cases.Length >= 3, "cases");
        });
    }

    // -- the rest ----------------------------------------------------------

    static void TrayMisc()
    {
        Run("awake: the line about sleeping when nobody is signed in", delegate
        {
            Eq("Windows won't put this PC to sleep by itself.", Awake.SignedOutLine(0, 0), "never");
            Eq("When nobody is signed in, Windows still sleeps it after 30 minutes.", Awake.SignedOutLine(1800, 0), "30 min");
            Eq("When nobody is signed in, Windows still sleeps it after 3 hours.", Awake.SignedOutLine(0, 10800), "hibernate only");
            Eq("When nobody is signed in, Windows still sleeps it after 1 minute.", Awake.SignedOutLine(60, 7200), "the first of the two");
            True(Awake.SignedOutLine(null, null) == null, "unknown: no line");
        });
        Run("icons: every state draws at every size", delegate
        {
            foreach (TrayState s in Enum.GetValues(typeof(TrayState)))
                foreach (int size in new[] { 16, 20, 24, 32 })
                    using (var b = Icons.Picture(s, size)) Eq(size, b.Width, s + " " + size);
            True(Icons.DotColor(TrayState.Running) != Icons.DotColor(TrayState.Problem), "colours differ");
        });
    }
}
