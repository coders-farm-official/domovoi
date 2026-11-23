// Installing from an offline kit (common\OfflineKit.cs; release 1.5.0, P3):
// the whole check against tests\kit-fixture, a kit the real Python writer
// made (domovoi/tests/test_windows_offline_kit.py keeps it so), signed with
// the throwaway key in tests\release-sig; each refusal on a copy changed
// one way; where Setup finds a kit; drvfs and the mount script; the
// provision.sh options and the host's job in kit mode; and the network
// guard. Only folders under %TEMP% are written.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Domovoi.Windows;

static partial class Tests
{
    // The fixture's stand-ins for the files Setup pins (the Python test
    // holds these strings to the bytes it writes).
    const string UbuntuBytes = "fixture: Ubuntu's WSL image\n";
    const string MsiBytes = "fixture: WSL's MSI\n";
    const string ProvisionBytes = "#!/bin/bash\n# fixture\nusage() {\n  --offline-kit DIR     install from the offline kit at DIR\n}\n";

    static string KitFixture()
    {
        string exeDir = Path.GetDirectoryName(typeof(Tests).Assembly.Location);
        return Path.GetFullPath(Path.Combine(exeDir, @"..\..\tests\kit-fixture"));
    }

    static string ShaOf(string text)
    {
        using (var h = SHA256.Create()) return Sha256.Hex(h.ComputeHash(Encoding.ASCII.GetBytes(text)));
    }

    static KitExpectation FixtureWant()
    {
        return new KitExpectation
        {
            Commit = FixtureCommit,
            Version = "1.0.1",
            Key = TestKey(),
            UbuntuName = "ubuntu-26.04.1-wsl-amd64.wsl",
            UbuntuSha256 = ShaOf(UbuntuBytes),
            WslMsiName = "wsl.2.7.14.0.x64.msi",
            WslMsiSha256 = ShaOf(MsiBytes),
            NeedWsl = true,
            ProvisionSha256 = ShaOf(ProvisionBytes),
            FreeBytes = 500L * 1024 * 1024 * 1024,
        };
    }

    // A copy of the fixture to change.
    static string KitCopy()
    {
        string dest = Path.Combine(Path.GetTempPath(), "domovoi-kit-" + Guid.NewGuid().ToString("N"));
        string src = KitFixture();
        foreach (string f in Directory.GetFiles(src, "*", SearchOption.AllDirectories))
        {
            string to = Path.Combine(dest, f.Substring(src.Length + 1));
            Directory.CreateDirectory(Path.GetDirectoryName(to));
            File.Copy(f, to);
        }
        return dest;
    }

    // SHA256SUMS made again for what is there now (a kit made that way,
    // not damaged on the way).
    static void Resum(string root)
    {
        var lines = new List<string>();
        foreach (string f in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
        {
            string rel = f.Substring(root.Length + 1).Replace('\\', '/');
            if (rel == "SHA256SUMS") continue;
            lines.Add(Sha256.OfFile(f) + "  " + rel);
        }
        lines.Sort((a, b) => string.CompareOrdinal(a.Substring(66), b.Substring(66)));
        File.WriteAllText(Path.Combine(root, "SHA256SUMS"), string.Join("\n", lines.ToArray()) + "\n", new UTF8Encoding(false));
    }

    // The bundle stored as parts, the way the writer splits a file bigger
    // than part_size (domovoi/offline_kit/write.py): <path>.part01, .part02,
    // every part but the last exactly partSize bytes, each listed in
    // kit.json's "parts" with its size and SHA-256, and SHA256SUMS listing
    // the parts, not the whole. The fixture itself is never split: the
    // format's floor is 1 MiB, and the fixture stays a few KB.
    static void SplitBundle(string root, int partSize)
    {
        string whole = Path.Combine(root, @"source\domovoi.bundle");
        byte[] b = File.ReadAllBytes(whole);
        var parts = new List<object>();
        for (int i = 0, n = 1; i < b.Length; i += partSize, n++)
        {
            byte[] chunk = new byte[Math.Min(partSize, b.Length - i)];
            Array.Copy(b, i, chunk, 0, chunk.Length);
            string rel = "source/domovoi.bundle.part" + n.ToString("00");
            File.WriteAllBytes(Path.Combine(root, rel.Replace('/', '\\')), chunk);
            using (var h = SHA256.Create())
                parts.Add(new Dictionary<string, object> { { "path", rel }, { "size", chunk.Length }, { "sha256", Sha256.Hex(h.ComputeHash(chunk)) } });
        }
        File.Delete(whole);
        string kj = Path.Combine(root, "kit.json");
        Dictionary<string, object> d = Json.ParseObject(File.ReadAllText(kj, new UTF8Encoding(false)));
        d["part_size"] = partSize;
        foreach (var c in Json.ObjList(d, "components"))
            foreach (var f in Json.ObjList(c, "files"))
                if (Json.Str(f, "path") == "source/domovoi.bundle") f["parts"] = parts.ToArray();
        File.WriteAllText(kj, Json.Serialize(d), new UTF8Encoding(false));
        Resum(root);
    }

    static void EditKitJson(string root, Func<string, string> edit)
    {
        string p = Path.Combine(root, "kit.json");
        File.WriteAllText(p, edit(File.ReadAllText(p, new UTF8Encoding(false))), new UTF8Encoding(false));
    }

    static KitIndex KitOf(string root)
    {
        return KitIndex.Parse(File.ReadAllText(Path.Combine(root, "kit.json"), new UTF8Encoding(false)));
    }

    [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
    [return: System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.I1)]
    static extern bool CreateSymbolicLinkW(string link, string target, int flags);

    static string OneProblem(KitReport r)
    {
        True(r.Problems.Count >= 1, "refused");
        return string.Join(" | ", r.Problems.ToArray());
    }

    static void Has(string text, string part, string what)
    {
        if (text.IndexOf(part, StringComparison.Ordinal) < 0) Fail(what + ": no \"" + part + "\" in: " + text);
    }

    static void KitTests()
    {
        Run("kit: the fixture made by the real writer passes", delegate
        {
            KitReport r = OfflineKit.Check(KitFixture(), FixtureWant());
            True(r.Ok, "ok: " + string.Join(" | ", r.Problems.ToArray()));
            Eq("1.0.1", r.Version, "version");
            Eq("v1.0.1", r.Tag, "tag");
            Eq(FixtureCommit, r.Commit, "commit");
            True(!r.Ai, "no AI on this kit");
            True(r.Files >= 15, "files");
            Eq(40L, r.NeedGb, "a small kit needs what any install needs");
        });
        Run("kit: a damaged file is named, and the stick is to be made again", delegate
        {
            string k = KitCopy();
            File.AppendAllText(Path.Combine(k, @"images\postgres_16.15-trixie.tar"), "x");
            string p = OneProblem(OfflineKit.Check(k, FixtureWant()));
            Has(p, "This stick has a damaged file: images/postgres_16.15-trixie.tar.", "named");
            Has(p, "Make the stick again.", "advice");
        });
        Run("kit: a split file passes, and a missing part is named", delegate
        {
            string k = KitCopy();
            SplitBundle(k, 4000);
            True(!File.Exists(Path.Combine(k, @"source\domovoi.bundle")), "stored as parts only");
            KitReport r = OfflineKit.Check(k, FixtureWant());
            True(r.Ok, "split: " + string.Join(" | ", r.Problems.ToArray()));
            File.Delete(Path.Combine(k, @"source\domovoi.bundle.part02"));
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "source/domovoi.bundle.part02 (missing)", "the part");
        });
        Run("kit: a changed kit.json is caught before it is read", delegate
        {
            string k = KitCopy();
            EditKitJson(k, s => s.Replace("\"includes\": []", "\"includes\": [\"ai\"]"));
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "damaged file: kit.json", "kit.json");
        });
        // M12 (review of 2026-11-23): what is on the stick, before anything
        // on it is used.
        Run("kit: a clean kit has nothing unaccounted for, as a folder or a drive's root", delegate
        {
            string k = KitCopy();
            Eq(0, OfflineKit.Unaccounted(k, KitOf(k), false).Count, "a folder");
            Eq(0, OfflineKit.Unaccounted(k, KitOf(k), true).Count, "a drive's root");
            SplitBundle(k, 4000);
            Eq(0, OfflineKit.Unaccounted(k, KitOf(k), false).Count, "split: its parts are listed");
            File.WriteAllText(Path.Combine(k, @"source\domovoi.bundle"), "whole");
            Resum(k);
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "source/domovoi.bundle is here whole, but kit.json says it is stored in parts only.", "whole beside its parts");
        });
        Run("kit: a symlink is refused, named", delegate
        {
            string k = KitCopy();
            bool made = CreateSymbolicLinkW(Path.Combine(k, "speech-link"), Path.Combine(k, "speech"), 0x1 | 0x2);
            if (!made && Environment.GetEnvironmentVariable("CI") == null)
            {
                Console.WriteLine("     (this account can't make a symlink here: skipped outside CI)");
                return;
            }
            True(made, "made a folder symlink (CI runs as an administrator)");
            string p = OneProblem(OfflineKit.Check(k, FixtureWant()));
            Has(p, "speech-link is a link, which a kit never holds.", "the folder link");
            Has(p, "Setup used nothing from it.", "nothing used");
            string k2 = KitCopy();
            string target = Path.Combine(k2, @"linux\provision.sh");
            File.Move(target, target + ".real");
            True(CreateSymbolicLinkW(target, target + ".real", 0x2), "made a file symlink");
            Has(OneProblem(OfflineKit.Check(k2, FixtureWant())), "linux/provision.sh is a link or not a plain file, which a kit never holds.", "the file link, though kit.json lists its name");
        });
        Run("kit: a junction is refused, named, and not walked into", delegate
        {
            string k = KitCopy();
            string outside = Path.Combine(Path.GetTempPath(), "domovoi-kit-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(outside);
            File.WriteAllText(Path.Combine(outside, "Release"), "x");
            string j = Path.Combine(k, @"apt\amd64\ubuntu\dists\noble");
            var psi = new System.Diagnostics.ProcessStartInfo("cmd.exe", "/c mklink /J \"" + j + "\" \"" + outside + "\"") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true };
            using (var pr = System.Diagnostics.Process.Start(psi)) { pr.StandardOutput.ReadToEnd(); pr.WaitForExit(); Eq(0, pr.ExitCode, "mklink /J"); }
            True((new DirectoryInfo(j).Attributes & FileAttributes.ReparsePoint) != 0, "a junction");
            List<string> found = OfflineKit.Unaccounted(k, KitOf(k), true);
            SeqEq(new[] { "apt/amd64/ubuntu/dists/noble is a link, which a kit never holds." }, found, "only the junction");
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "apt/amd64/ubuntu/dists/noble is a link, which a kit never holds.", "Check");
        });
        Run("kit: an unlisted folder and file are refused in the checker's words", delegate
        {
            string k = KitCopy();
            Directory.CreateDirectory(Path.Combine(k, @"apt\amd64\ubuntu\dists\evil"));
            File.WriteAllText(Path.Combine(k, @"speech\extra.onnx"), "x");
            Directory.CreateDirectory(Path.Combine(k, @"notes\deeper"));
            File.WriteAllText(Path.Combine(k, @"notes\deeper\a.txt"), "x");
            SeqEq(new[] {
                "notes/deeper/a.txt is not part of the kit (kit.json doesn't list it).",
                "speech/extra.onnx is not part of the kit (kit.json doesn't list it).",
                "apt/amd64/ubuntu/dists/evil/ is not part of the kit (kit.json lists nothing in it).",
            }, OfflineKit.Unaccounted(k, KitOf(k), false), "named, folders after files");
            string p = OneProblem(OfflineKit.Check(k, FixtureWant()));
            Eq(1, OfflineKit.Check(k, FixtureWant()).Problems.Count, "one message");
            True(p.StartsWith("This stick holds what a kit never does, so Setup used nothing from it. notes/deeper/a.txt is not part of the kit", StringComparison.Ordinal), "the words: " + p);
            True(p.EndsWith("(kit.json lists nothing in it). Make the stick again.", StringComparison.Ordinal), "the advice: " + p);
            for (int i = 0; i < 6; i++) File.WriteAllText(Path.Combine(k, "extra" + i + ".txt"), "x");
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "And 4 more like these. Make the stick again.", "a long list is cut");
        });
        Run("kit: an apt folder with a name apt doesn't use is refused, even listed", delegate
        {
            // Listed in kit.json (which no signature covers) and in SHA256SUMS.
            string k = KitCopy();
            string from = Path.Combine(k, @"apt\amd64\ubuntu\dists\resolute");
            string to = Path.Combine(k, @"apt\amd64\ubuntu\dists\Noble_X");
            Directory.Move(from, to);
            EditKitJson(k, s2 => s2.Replace("apt/amd64/ubuntu/dists/resolute/", "apt/amd64/ubuntu/dists/Noble_X/"));
            Resum(k);
            SeqEq(new[] { "The package folder apt/amd64/ubuntu/dists/Noble_X/ has a name a kit never uses." }, OfflineKit.Unaccounted(k, KitOf(k), false), "the suite");
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "The package folder apt/amd64/ubuntu/dists/Noble_X/ has a name a kit never uses.", "Check");
            string k2 = KitCopy();
            Directory.CreateDirectory(Path.Combine(k2, @"apt\amd64\docker\dists\resolute\stable"));
            Directory.CreateDirectory(Path.Combine(k2, @"apt\amd64\ubuntu\dists\resolute\main universe"));
            File.WriteAllText(Path.Combine(k2, @"apt\amd64\docker\dists\resolute\stable\Packages"), "x");
            File.WriteAllText(Path.Combine(k2, @"apt\amd64\ubuntu\dists\resolute\main universe\Packages"), "x");
            List<string> found = OfflineKit.Unaccounted(k2, KitOf(k2), false);
            True(found.Contains("The package folder apt/amd64/ubuntu/dists/resolute/main universe/ has a name a kit never uses."), "the component: " + string.Join(" | ", found.ToArray()));
            True(!found.Exists(f => f.StartsWith("The package folder apt/amd64/docker", StringComparison.Ordinal)), "stable is a name apt uses");
        });
        Run("kit: the system's own folders are let be at a drive's root only", delegate
        {
            string k = KitCopy();
            foreach (string d in new[] { "System Volume Information", "$RECYCLE.BIN", ".Spotlight-V100", ".fseventsd", ".Trashes" })
                File.WriteAllText(Path.Combine(Directory.CreateDirectory(Path.Combine(k, d)).FullName, "IndexerVolumeGuid"), "x");
            Directory.CreateDirectory(Path.Combine(k, "lost+found"));
            File.WriteAllText(Path.Combine(k, ".DS_Store"), "x");
            File.WriteAllText(Path.Combine(k, "desktop.ini"), "x");
            Eq(0, OfflineKit.Unaccounted(k, KitOf(k), true).Count, "a drive's root: " + string.Join(" | ", OfflineKit.Unaccounted(k, KitOf(k), true).ToArray()));
            List<string> asFolder = OfflineKit.Unaccounted(k, KitOf(k), false);
            True(asFolder.Contains("System Volume Information/IndexerVolumeGuid is not part of the kit (kit.json doesn't list it)."), "a kit in a folder: " + string.Join(" | ", asFolder.ToArray()));
            True(asFolder.Contains(".DS_Store is not part of the kit (kit.json doesn't list it)."), "the file too");
            True(asFolder.Contains("lost+found/ is not part of the kit (kit.json lists nothing in it)."), "the empty one too");
            // The same names deeper down are the kit's to explain.
            string k2 = KitCopy();
            File.WriteAllText(Path.Combine(Directory.CreateDirectory(Path.Combine(k2, @"speech\System Volume Information")).FullName, "x"), "x");
            Directory.CreateDirectory(Path.Combine(k2, @"apt\$RECYCLE.BIN"));
            File.WriteAllText(Path.Combine(k2, @"speech\.DS_Store"), "x");
            SeqEq(new[] {
                "speech/.DS_Store is not part of the kit (kit.json doesn't list it).",
                "speech/System Volume Information/x is not part of the kit (kit.json doesn't list it).",
                "apt/$RECYCLE.BIN/ is not part of the kit (kit.json lists nothing in it).",
            }, OfflineKit.Unaccounted(k2, KitOf(k2), true), "deeper, even on a drive's root");
            // Nothing else is let be at the root.
            Directory.CreateDirectory(Path.Combine(k2, "System Volume Informatio"));
            True(OfflineKit.Unaccounted(k2, KitOf(k2), true).Contains("System Volume Informatio/ is not part of the kit (kit.json lists nothing in it)."), "only the names listed");
        });
        Run("kit: no kit.json is no kit", delegate
        {
            string d = Path.Combine(Path.GetTempPath(), "domovoi-kit-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(d);
            Has(OneProblem(OfflineKit.Check(d, FixtureWant())), "There is no offline kit at", "words");
        });
        Run("kit: a newer kit format is for the Setup on the stick", delegate
        {
            string k = KitCopy();
            EditKitJson(k, s => s.Replace("\"kit_format\": 1", "\"kit_format\": 2"));
            Resum(k);
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "made by a newer Domovoi (kit format 2", "newer");
        });
        Run("kit: a kit for a Mac only says so", delegate
        {
            string k = KitCopy();
            EditKitJson(k, s => s.Replace("\"pc-x86_64\"", "\"mac-arm64\""));
            Resum(k);
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "made for a Mac only", "Mac");
        });
        Run("kit: signed with another key, nothing on it is used", delegate
        {
            KitExpectation w = FixtureWant();
            w.Key = OtherKey();
            Has(OneProblem(OfflineKit.Check(KitFixture(), w)), "not a Domovoi release signed with Domovoi's release key", "key");
        });
        Run("kit: a Setup with no release key can't check one", delegate
        {
            KitExpectation w = FixtureWant();
            w.Key = null;
            Has(OneProblem(OfflineKit.Check(KitFixture(), w)), "no release key built in", "no key");
        });
        Run("kit: another release than this Setup's", delegate
        {
            KitExpectation w = FixtureWant();
            w.Commit = "0123456789abcdef0123456789abcdef01234567";
            w.Version = "1.5.0";
            string p = OneProblem(OfflineKit.Check(KitFixture(), w));
            Has(p, "This kit holds Domovoi 1.0.1, and this Setup installs Domovoi 1.5.0", "versions");
            Has(p, "Run the DomovoiSetup.exe on the stick", "advice");
            w.Version = "1.0.1";
            Has(OneProblem(OfflineKit.Check(KitFixture(), w)), "this Setup installs commit 0123456789ab", "same version, another commit");
        });
        Run("kit: kit.json and the signed release must name the same commit", delegate
        {
            string k = KitCopy();
            EditKitJson(k, s => s.Replace(FixtureCommit, "0123456789abcdef0123456789abcdef01234567"));
            Resum(k);
            Has(OneProblem(OfflineKit.Check(k, FixtureWant())), "but its signed release is v1.0.1", "mismatch");
        });
        Run("kit: Setup's own parts must be the ones its release pins", delegate
        {
            KitExpectation w = FixtureWant();
            w.UbuntuSha256 = ShaOf("another image");
            Has(OneProblem(OfflineKit.Check(KitFixture(), w)), "windows/ubuntu-26.04.1-wsl-amd64.wsl isn't the one this Setup's release pins", "pin");
        });
        Run("kit: missing parts are listed in provision.sh's words", delegate
        {
            string k = KitCopy();
            EditKitJson(k, s => s.Replace("\"kind\": \"ollama-runtime\"", "\"kind\": \"licence\"").Replace("\"path\": \"windows/wsl.2.7.14.0.x64.msi\"", "\"path\": \"licences/wsl.msi\""));
            File.Move(Path.Combine(k, @"windows\wsl.2.7.14.0.x64.msi"), Path.Combine(Directory.CreateDirectory(Path.Combine(k, "licences")).FullName, "wsl.msi"));
            Resum(k);
            string p = OneProblem(OfflineKit.Check(k, FixtureWant()));
            Has(p, "This kit is missing what Setup needs: WSL (windows/wsl.2.7.14.0.x64.msi); Ollama (ollama/). Make the kit again.", "list");
            KitExpectation w = FixtureWant();
            w.NeedWsl = false;
            p = OneProblem(OfflineKit.Check(k, w));
            True(p.IndexOf("WSL (", StringComparison.Ordinal) < 0, "a PC with WSL doesn't need it: " + p);
        });
        Run("kit: a kit whose provision.sh can't install offline is older than 1.5.0", delegate
        {
            string k = KitCopy();
            string text = "#!/bin/bash\n# an older provision.sh\n";
            File.WriteAllText(Path.Combine(k, @"linux\provision.sh"), text);
            EditKitJson(k, s => s.Replace(ShaOf(ProvisionBytes), ShaOf(text)).Replace("\"size\": " + ProvisionBytes.Length + ",", "\"size\": " + text.Length + ","));
            Resum(k);
            KitExpectation w = FixtureWant();
            w.ProvisionSha256 = ShaOf(text);
            Has(OneProblem(OfflineKit.Check(k, w)), "offline kits start with 1.5.0", "older");
        });
        Run("kit: not enough disk is said before anything changes", delegate
        {
            KitExpectation w = FixtureWant();
            w.FreeBytes = 12L * 1024 * 1024 * 1024;
            w.FreeDrive = @"C:\";
            Has(OneProblem(OfflineKit.Check(KitFixture(), w)), "needs about 40 GB free on C:\\, and there are 12 GB", "disk");
            Eq(40L, OfflineKit.NeedGb(1L << 30, 40), "1 GB of kit");
            Eq(38L, OfflineKit.NeedGb(14L << 30, 30), "14 GB of kit: twice, plus 10");
        });
        Run("kit: found beside Setup, in the stick's release folder or the kit's own", delegate
        {
            var files = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { @"E:\kit.json", @"D:\kits\K\kit.json" };
            Func<string, bool> exists = files.Contains;
            Eq(@"E:\", OfflineKit.Beside(@"E:\release", exists), "the stick's release folder");
            Eq(@"E:\", OfflineKit.Beside(@"E:\", exists), "the stick's root");
            Eq(@"D:\kits\K", OfflineKit.Beside(@"D:\kits\K\release\", exists), "a copied kit");
            True(OfflineKit.Beside(@"C:\Users\alex\Downloads", exists) == null, "none");
        });
        Run("kit: the folder the household gives, as one full path", delegate
        {
            Eq(@"E:\", OfflineKit.NormalizeRoot("E:"), "a drive");
            Eq(@"E:\", OfflineKit.NormalizeRoot(@"e:\"), "a drive's root");
            Eq(@"D:\kits\K", OfflineKit.NormalizeRoot("\"D:\\kits\\K\\\""), "a folder, quoted, trailing backslash");
            True(OfflineKit.NormalizeRoot(@"kits\K") == null, "relative");
        });
        Run("kit: drvfs takes the drive or the folder; a network folder is refused", delegate
        {
            Eq("E:", OfflineKit.DrvfsSource(@"E:\"), "drive");
            Eq("D:/kits/DOMOVOI KIT", OfflineKit.DrvfsSource(@"D:\kits\DOMOVOI KIT\"), "folder");
            foreach (string bad in new[] { @"\\server\share\kit", "kits", @"D:\it's" })
            {
                bool threw = false;
                try { OfflineKit.DrvfsSource(bad); } catch (ArgumentException) { threw = true; }
                True(threw, "refused: " + bad);
            }
            Has(OneProblem(OfflineKit.Check(@"\\server\share\kit", FixtureWant())), "on a network folder", "a share Setup can't open");
        });
        Run("kit: the mount script mounts read-only, afresh, and checks kit.json", delegate
        {
            string s = OfflineKit.MountScript();
            Has(s, "read -r src", "the source on stdin, never in the command line");
            Has(s, "mount -t drvfs \"$src\" \"$mp\" -o ro,noatime", "read-only drvfs");
            Has(s, "mp=" + OfflineKit.MountPoint, "the mount point");
            Has(s, "[ -r \"$mp/kit.json\" ] || exit 5", "kit.json readable");
            Has(OfflineKit.UnmountScript(), "umount", "unmount");
            Has(OfflineKit.MountProblem(4, @"E:\"), "is the stick still plugged in", "4");
            Has(OfflineKit.MountProblem(5, @"D:\kits\K"), "can't read the offline kit", "5");
        });
        Run("kit: provision.sh gets --offline-kit at the mount point, only when it takes it", delegate
        {
            var o = new ProvisionOptions { RepoUrl = "https://github.com/coders-farm-official/domovoi", Ref = FixtureCommit, Internet = "never" };
            True(!ProvisionArgs.Build(o).Contains("--offline-kit"), "online: none");
            o.OfflineKit = OfflineKit.MountPoint;
            o.ScriptHasOfflineKit = true;
            List<string> a = ProvisionArgs.Build(o);
            int i = a.IndexOf("--offline-kit");
            True(i >= 0 && a[i + 1] == "/mnt/domovoi-kit", "the option");
            o.ScriptHasOfflineKit = false;
            bool threw = false;
            try { ProvisionArgs.Build(o); } catch (ArgumentException e) { threw = e.Message.Contains("1.5.0"); }
            True(threw, "an older provision.sh is refused");
            o.ScriptHasOfflineKit = true;
            o.OfflineKit = "/media/alex/DOMOVOIKIT";
            threw = false;
            try { ProvisionArgs.Build(o); } catch (ArgumentException) { threw = true; }
            True(threw, "only the host's mount point");
            True(ProvisionScript.SupportsOfflineKit(ProvisionBytes), "the usage text");
            True(!ProvisionScript.SupportsOfflineKit("  --offline-kit"), "not a usage line");
        });
        Run("kit: the host's job carries the kit's folder, and refuses one it can't mount", delegate
        {
            var j = new HostJob { Image = @"C:\x\u.wsl", ImageSha256 = ShaOf(UbuntuBytes), Provision = @"C:\x\p.sh", ProvisionSha256 = ShaOf(ProvisionBytes), WslConfig = "[wsl2]\n", Kit = @"E:\" };
            var d = j.ToJson();
            Eq(@"E:\", (string)d["kit"], "written");
            Eq(@"E:\", HostJob.FromJson(Json.ParseObject(Json.Serialize(d))).Kit, "read back");
            j.Kit = null;
            True(!j.ToJson().ContainsKey("kit"), "online: no key, as before");
            d["kit"] = @"\\server\share";
            bool threw = false;
            try { HostJob.FromJson(d); } catch (FormatException) { threw = true; }
            True(threw, "a network folder");
        });
        Run("kit: the network guard lets only this PC through, and only in kit mode", delegate
        {
            try
            {
                NetGuard.Offline = false;
                NetGuard.Check("https://github.com/");
                NetGuard.Offline = true;
                NetGuard.Check("http://127.0.0.1:6370/v1/health");
                NetGuard.Check("http://localhost:6369/");
                NetGuard.CheckHost("::1");
                foreach (Action a in new Action[] {
                    () => NetGuard.Check("https://github.com/"),
                    () => NetGuard.Check("https://127.0.0.1.example.com/"),
                    () => NetGuard.CheckHost("1.1.1.1"),
                    () => Domovoi.Windows.Helper.Preflight.Reachable("https://releases.ubuntu.com/"),
                    () => Domovoi.Windows.Helper.Preflight.Dial("1.1.1.1", 443),
                    () => Loopback.Send("GET", "https://github.com/", null, 1000) })
                {
                    bool threw = false;
                    try { a(); }
                    catch (InvalidOperationException e) { threw = e.Message.Contains("offline kit: nothing may"); }
                    True(threw, "refused, loudly");
                }
            }
            finally { NetGuard.Offline = false; }
        });
    }
}
