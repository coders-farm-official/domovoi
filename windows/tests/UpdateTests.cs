// The Windows update's tests (design note WINDOWS-ONE-UPDATE-2026-10-22):
// Ed25519 against RFC 8032's vectors; the SSH signatures ssh-keygen made
// with a throwaway key (tests\release-sig: its public half, another key's,
// a pretend Setup and its manifests, and their signatures; the private
// keys never left the scratch folder they were made in); the whole check
// (good, bad, wrong key, wrong namespace, older, wrong release); the
// private run folder and the held file (the TOCTOU guard); the silent
// repair's arguments; and the Windows step end to end against a pretend
// release, a pretend Windows (permission given, declined) and a pretend
// core on a loopback socket. Nothing here starts anything elevated or
// touches the network beyond loopback.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Domovoi.Windows;
using Domovoi.Windows.Tray;

static partial class Tests
{
    static string SigDir()
    {
        string exeDir = Path.GetDirectoryName(typeof(Tests).Assembly.Location);
        return Path.GetFullPath(Path.Combine(exeDir, @"..\..\tests\release-sig"));
    }

    static byte[] SigFile(string name) { return File.ReadAllBytes(Path.Combine(SigDir(), name)); }
    static string SigText(string name) { return Encoding.ASCII.GetString(SigFile(name)); }
    static byte[] TestKey() { return SshKey.ParseEd25519(SigText("test-key.pub")); }
    static byte[] OtherKey() { return SshKey.ParseEd25519(SigText("other-key.pub")); }
    const string FixtureCommit = "c0ffee0123456789abcdef0123456789abcdef01";

    static byte[] Hex(string h)
    {
        var b = new byte[h.Length / 2];
        for (int i = 0; i < b.Length; i++) b[i] = Convert.ToByte(h.Substring(i * 2, 2), 16);
        return b;
    }

    static void UpdateTests()
    {
        Ed25519Vectors();
        SshSignatures();
        SetupChecks();
        Versions();
        RunFolders();
        RepairArguments();
        StepFlow();
        HelperStage();
        UpdatePollFlow();
        SideReport();
    }

    static void Ed25519Vectors()
    {
        // RFC 8032 section 7.1, tests 1-3.
        string[][] v =
        {
            new[] { "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
                    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b" },
            new[] { "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
                    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00" },
            new[] { "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
                    "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a" },
        };
        Run("ed25519: RFC 8032's vectors verify", delegate
        {
            foreach (string[] t in v) True(Ed25519.Verify(Hex(t[0]), Hex(t[1]), Hex(t[2])), "vector " + t[0].Substring(0, 8));
        });
        Run("ed25519: a changed message, signature or key does not", delegate
        {
            foreach (string[] t in v)
            {
                byte[] pk = Hex(t[0]), m = Hex(t[1]), sig = Hex(t[2]);
                byte[] m2 = m.Concat(new byte[] { 1 }).ToArray();
                True(!Ed25519.Verify(pk, m2, sig), "message");
                for (int bit = 0; bit < 512; bit += 37)
                {
                    byte[] s2 = (byte[])sig.Clone();
                    s2[bit / 8] ^= (byte)(1 << (bit % 8));
                    True(!Ed25519.Verify(pk, m, s2), "signature bit " + bit);
                }
                byte[] pk2 = (byte[])pk.Clone();
                pk2[0] ^= 1;
                True(!Ed25519.Verify(pk2, m, sig), "key");
            }
            True(!Ed25519.Verify(new byte[31], new byte[0], new byte[64]), "short key");
            True(!Ed25519.Verify(Hex(v[0][0]), new byte[0], new byte[63]), "short signature");
        });
        Run("ed25519: S at or above the group order is refused", delegate
        {
            byte[] sig = Hex(v[0][2]);
            // L = 2^252 + 27742317777372353535851937790883648493, little-endian.
            byte[] l = Hex("edd3f55c1a631258d69cf7a2def9de1400000000000000000000000000000010");
            Array.Copy(l, 0, sig, 32, 32);
            True(!Ed25519.Verify(Hex(v[0][0]), new byte[0], sig), "S = L");
        });
    }

    static void SshSignatures()
    {
        Run("ssh signature: the throwaway key's ssh-keygen signature checks out", delegate
        {
            True(TestKey() != null && TestKey().Length == 32, "the key parses");
            True(SshSig.Problem(SigText("setup.bin.sig"), SigFile("setup.bin"), TestKey(), ReleaseNames.SetupNamespace) == null, "good");
        });
        Run("ssh signature: another key's signature, a changed file, the wrong namespace and garbage are refused", delegate
        {
            string p = SshSig.Problem(SigText("setup.bin.other-key.sig"), SigFile("setup.bin"), TestKey(), ReleaseNames.SetupNamespace);
            True(p != null && p.Contains("key other than"), "another key: " + p);
            byte[] changed = SigFile("setup.bin");
            changed[1234] ^= 0x40;
            p = SshSig.Problem(SigText("setup.bin.sig"), changed, TestKey(), ReleaseNames.SetupNamespace);
            True(p != null && p.Contains("doesn't match"), "changed: " + p);
            p = SshSig.Problem(SigText("manifest-1.0.1.json.wrong-namespace.sig"), SigFile("manifest-1.0.1.json"), TestKey(), ReleaseNames.ManifestNamespace);
            True(p != null && p.Contains("for something else"), "namespace: " + p);
            True(SshSig.Problem("not a signature", SigFile("setup.bin"), TestKey(), ReleaseNames.SetupNamespace) != null, "garbage");
            True(SshSig.Problem(SigText("setup.bin.sig"), SigFile("setup.bin"), null, ReleaseNames.SetupNamespace) != null, "no key");
            True(SshSig.Problem(SigText("setup.bin.sig"), SigFile("setup.bin"), OtherKey(), ReleaseNames.SetupNamespace) != null, "pinned to another key");
        });
        Run("ssh signature: public key lines", delegate
        {
            True(SshKey.ParseEd25519("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ== x") == null, "rsa");
            True(SshKey.ParseEd25519("ssh-ed25519 !!!") == null, "not base64");
            True(SshKey.ParseEd25519("# comment\n" + SigText("test-key.pub")) != null, "a comment line first");
            True(ReleaseKey.Pinned() == null, "the test exe embeds no release key");
        });
    }

    static SetupExpectation Want(string installed = "1.0.0", string tag = "v1.0.1", string commit = FixtureCommit, byte[] key = null)
    {
        return new SetupExpectation { Tag = tag, Commit = commit, Installed = installed, Key = key ?? TestKey() };
    }

    static string Check(SetupExpectation w, string manifest, byte[] setup, string setupSig, out string code)
    {
        SetupManifest m;
        return SetupCheck.Run(w, SigFile(manifest), SigText(manifest + ".sig"), setup ?? SigFile("setup.bin"), setupSig ?? SigText("setup.bin.sig"), out m, out code);
    }

    static void SetupChecks()
    {
        string code;
        Run("setup check: a good, newer release passes", delegate
        {
            True(Check(Want(), "manifest-1.0.1.json", null, null, out code) == null && code == null, "passes");
            True(Check(Want(installed: "1.0.1-rc3"), "manifest-1.0.1.json", null, null, out code) == null, "over its release candidate");
            True(Check(Want(commit: null), "manifest-1.0.1.json", null, null, out code) == null, "commit not checked when the core didn't name one");
        });
        Run("setup check: a bad signature never passes", delegate
        {
            byte[] changed = SigFile("setup.bin");
            changed[0] ^= 1;
            string p = Check(Want(), "manifest-1.0.1.json", changed, null, out code);
            True(p != null && code == SetupCheck.BadSignature, "changed Setup: " + p);
            p = Check(Want(), "manifest-1.0.1.json", null, SigText("setup.bin.other-key.sig"), out code);
            True(p != null && code == SetupCheck.BadSignature && p.Contains("key other than"), "another key's: " + p);
            SetupManifest m;
            byte[] manifest = SigFile("manifest-1.0.1.json");
            manifest[manifest.Length - 3] = (byte)'9';
            p = SetupCheck.Run(Want(), manifest, SigText("manifest-1.0.1.json.sig"), SigFile("setup.bin"), SigText("setup.bin.sig"), out m, out code);
            True(p != null && p.Contains("manifest can't be trusted"), "a changed manifest: " + p);
            p = SetupCheck.Run(Want(), SigFile("manifest-1.0.1.json"), SigText("manifest-1.0.1.json.wrong-namespace.sig"), SigFile("setup.bin"), SigText("setup.bin.sig"), out m, out code);
            True(p != null && code == SetupCheck.BadSignature, "the exe's signature offered as the manifest's: " + p);
        });
        Run("setup check: the wrong key pinned", delegate
        {
            string p = Check(Want(key: OtherKey()), "manifest-1.0.1.json", null, null, out code);
            True(p != null && code == SetupCheck.BadSignature && p.Contains("key other than"), p);
        });
        Run("setup check: no key pinned, no signed files", delegate
        {
            SetupManifest m;
            var w = Want();
            w.Key = null;
            True(SetupCheck.Run(w, SigFile("manifest-1.0.1.json"), SigText("manifest-1.0.1.json.sig"), SigFile("setup.bin"), SigText("setup.bin.sig"), out m, out code) != null && code == SetupCheck.NoKey, "no key");
            True(SetupCheck.Run(Want(), null, null, SigFile("setup.bin"), SigText("setup.bin.sig"), out m, out code) != null && code == SetupCheck.NoSignedSetup, "no manifest");
        });
        Run("setup check: an older or the same version never runs", delegate
        {
            string p = Check(Want(tag: "v0.9.0"), "manifest-0.9.0.json", null, null, out code);
            True(p != null && code == SetupCheck.NotNewer && p.Contains("isn't newer"), "older: " + p);
            p = Check(Want(installed: "1.0.1"), "manifest-1.0.1.json", null, null, out code);
            True(p != null && code == SetupCheck.NotNewer, "the same: " + p);
            foreach (string unknown in new[] { null, "", "garbage", "1.0" })
            {
                SetupManifest m;
                p = SetupCheck.Run(new SetupExpectation { Tag = "v1.0.1", Commit = FixtureCommit, Installed = unknown, Key = TestKey() },
                    SigFile("manifest-1.0.1.json"), SigText("manifest-1.0.1.json.sig"), SigFile("setup.bin"), SigText("setup.bin.sig"), out m, out code);
                True(p != null && code == SetupCheck.NotNewer && p.Contains("isn't known"), "an installed version of '" + unknown + "' fails closed: " + p);
            }
        });
        Run("setup check: a signed manifest of another release, or another commit, is refused", delegate
        {
            string p = Check(Want(tag: "v1.0.2"), "manifest-1.0.1.json", null, null, out code);
            True(p != null && p.Contains("is for v1.0.1"), "another tag: " + p);
            p = Check(Want(commit: "1111111111111111111111111111111111111111"), "manifest-1.0.1.json", null, null, out code);
            True(p != null && p.Contains("built from"), "another commit: " + p);
        });
    }

    static void Versions()
    {
        Run("versions: release candidates, releases and patches in order", delegate
        {
            string[] order = { "0.9.0", "1.0.0-rc1", "1.0.0-rc2", "1.0.0-rc10", "1.0.0", "1.0.1-rc1", "1.0.1", "1.1.0", "v2.0.0" };
            for (int i = 0; i + 1 < order.Length; i++)
            {
                True(ReleaseVersion.Compare(order[i], order[i + 1]) < 0, order[i] + " < " + order[i + 1]);
                True(ReleaseVersion.Compare(order[i + 1], order[i]) > 0, order[i + 1] + " > " + order[i]);
            }
            Eq(0, ReleaseVersion.Compare("1.0.0", "v1.0.0"), "the v is optional");
            True(!ReleaseVersion.IsValid("1.0") && !ReleaseVersion.IsValid("one"), "not versions");
            Throws<FormatException>(delegate { ReleaseVersion.Compare("x", "1.0.0"); }, "a non-version throws");
            True(ReleaseNames.IsTag("v1.0.0-rc3") && !ReleaseNames.IsTag("main") && !ReleaseNames.IsTag("v1.0.0/../x"), "tags");
            Eq("https://github.com/coders-farm-official/domovoi/releases/download/v1.0.1/DomovoiSetup.exe", ReleaseNames.Url("v1.0.1", ReleaseNames.SetupAsset), "the pinned repository");
            Throws<ArgumentException>(delegate { ReleaseNames.Url("../evil", "x"); }, "a bad tag");
        });
    }

    static void RunFolders()
    {
        Run("run folder: private to this account, administrators and SYSTEM", delegate
        {
            string root = TempDir("updates");
            string dir = UpdateRun.NewRunDir(root, new DateTime(2026, 10, 22, 21, 30, 0, DateTimeKind.Utc));
            True(Directory.Exists(dir) && UpdateRun.IsRunName(Path.GetFileName(dir)), "made, and named for its time");
            True(UpdateRun.IsPrivate(dir), "its ACL");
            True(!UpdateRun.IsPrivate(root), "a plain folder isn't");
        });
        Run("run folder: the held Setup can't be written, renamed or deleted, and reads what was there", delegate
        {
            string dir = UpdateRun.NewRunDir(TempDir("held"), DateTime.UtcNow);
            string exe = Path.Combine(dir, "DomovoiSetup.exe");
            File.WriteAllBytes(exe, SigFile("setup.bin"));
            using (var held = new HeldFile(exe))
            {
                Throws<IOException>(delegate { using (new FileStream(exe, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete)) { } }, "written");
                Throws<IOException>(delegate { File.Delete(exe); }, "deleted");
                Throws<IOException>(delegate { File.Move(exe, exe + ".x"); }, "renamed");
                using (var r = new FileStream(exe, FileMode.Open, FileAccess.Read, FileShare.Read)) True(r.Length == 3000, "others may still read it (Windows maps it to run it)");
                True(held.ReadAll(1 << 20).SequenceEqual(SigFile("setup.bin")), "the bytes");
                Throws<IOException>(delegate { held.ReadAll(100); }, "over the cap");
            }
            File.Delete(exe);
            True(!File.Exists(exe), "free once let go");
        });
        Run("run folder: old runs are pruned, the pending one kept", delegate
        {
            string root = TempDir("prune");
            DateTime now = new DateTime(2026, 11, 30, 0, 0, 0, DateTimeKind.Utc);
            string oldRun = UpdateRun.NewRunDir(root, now.AddDays(-20));
            string keep = UpdateRun.NewRunDir(root, now.AddDays(-30));
            string fresh = UpdateRun.NewRunDir(root, now.AddDays(-1));
            Directory.CreateDirectory(Path.Combine(root, "not-a-run"));
            UpdateRun.Prune(root, now, Path.GetFileName(keep));
            True(!Directory.Exists(oldRun) && Directory.Exists(keep) && Directory.Exists(fresh) && Directory.Exists(Path.Combine(root, "not-a-run")), "pruned");
        });
        Run("run folder: the result file reads back", delegate
        {
            var r = new RunResult { Ok = false, Error = "a\nb", Log = @"C:\x\setup.log", Linux = "failed", Version = "1.0.1" };
            RunResult back = RunResult.Parse(r.Format());
            True(!back.Ok && back.Error == "a b" && back.Log == @"C:\x\setup.log" && back.Linux == "failed" && back.Version == "1.0.1", "round trip");
            True(RunResult.Parse("ok=1\r\n").Ok, "ok");
            True(RunResult.Parse("") == null, "empty");
        });
    }

    static void RepairArguments()
    {
        Run("silent repair: the arguments, and never anything that could uninstall or restart", delegate
        {
            string a = UpdateRun.SetupArguments(@"C:\ProgramData\Domovoi\updates\20261022-213000-0a1b2c3d\", false);
            Eq("/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /SP- /update=1 \"/LOG=C:\\ProgramData\\Domovoi\\updates\\20261022-213000-0a1b2c3d\\setup.log\" \"/updateresult=C:\\ProgramData\\Domovoi\\updates\\20261022-213000-0a1b2c3d\\result.txt\"", a, "the dashboard's Windows step");
            True(UpdateRun.SetupArguments(@"C:\r", true).Contains(" /update=1 /linuxfirst=1 "), "the menu's: the Linux part first");
            foreach (string bad in new[] { "/SILENT", "/RESTARTAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", "/MERGETASKS", "/TASKS", "uninst", "/DIR=" })
                True(a.IndexOf(bad, StringComparison.OrdinalIgnoreCase) < 0 || (bad == "/SILENT" && a.Contains("/VERYSILENT")), "no " + bad);
            Throws<ArgumentException>(delegate { UpdateRun.SetupArguments("C:\\a\" /DIR=x", false); }, "a quote can't add arguments");
            Eq("linux-update --wait --run 20261022-213000-0a1b2c3d", UpdateRun.LinuxArguments("20261022-213000-0a1b2c3d"), "the helper's Linux part");
            Throws<ArgumentException>(delegate { UpdateRun.LinuxArguments("..\\x"); }, "only a run name");
        });
        Run("silent repair: the helper's stage arguments", delegate
        {
            Eq("run-update --from \"C:\\Users\\Kim Ng\\AppData\\Local\\Domovoi\\updates\\20261022-213000-0a1b2c3d\" --run 20261022-213000-0a1b2c3d --tag v1.0.1 --commit " + FixtureCommit + " --linuxfirst",
                UpdateRun.StageArguments(@"C:\Users\Kim Ng\AppData\Local\Domovoi\updates\20261022-213000-0a1b2c3d\", "20261022-213000-0a1b2c3d", "v1.0.1", FixtureCommit, true), "with everything");
            True(!UpdateRun.StageArguments(@"C:\r", "20261022-213000-0a1b2c3d", "v1.0.1", null, false).Contains("--commit"), "no commit");
            Throws<ArgumentException>(delegate { UpdateRun.StageArguments("C:\\r\" --x", "20261022-213000-0a1b2c3d", "v1.0.1", null, false); }, "quote");
            Throws<ArgumentException>(delegate { UpdateRun.StageArguments("C:\\r", "20261022-213000-0a1b2c3d", "v1.0.1 --x", null, false); }, "tag");
            Throws<ArgumentException>(delegate { UpdateRun.StageArguments("C:\\r", "20261022-213000-0a1b2c3d", "v1.0.1", "HEAD", false); }, "commit");
            Eq(@"C:\ProgramData\Domovoi\updates", UpdateRun.AdminRoot, "the elevated side's folder");
        });
    }

    // -- the Windows step against pretend files and a pretend Windows ------

    sealed class FakeFiles : IReleaseFiles
    {
        public Dictionary<string, byte[]> Assets = new Dictionary<string, byte[]>();
        public string Fail;
        public List<string> Asked = new List<string>();

        public static FakeFiles Release(string manifest)
        {
            var f = new FakeFiles();
            f.Assets[ReleaseNames.Manifest] = SigFile(manifest);
            f.Assets[ReleaseNames.ManifestSig] = SigFile(manifest + ".sig");
            f.Assets[ReleaseNames.SetupAsset] = SigFile("setup.bin");
            f.Assets[ReleaseNames.SetupSig] = SigFile("setup.bin.sig");
            return f;
        }

        public byte[] Get(string tag, string asset, long max, out bool notFound, out string problem)
        {
            Asked.Add(tag + "/" + asset);
            notFound = false; problem = null;
            if (Fail != null) { problem = Fail; return null; }
            byte[] b;
            if (!Assets.TryGetValue(asset, out b)) { notFound = true; problem = "not in the release"; return null; }
            if (b.Length > max) { problem = "too large"; return null; }
            return b;
        }

        public bool Save(string tag, string asset, long max, string path, out bool notFound, out string problem)
        {
            byte[] b = Get(tag, asset, max, out notFound, out problem);
            if (b == null) return false;
            File.WriteAllBytes(path, b);
            return true;
        }
    }

    sealed class FakeElevated : IElevated
    {
        public int Code;
        public int WaitForExit() { return Code; }
    }

    // A pretend Windows: records what would run elevated, and lets a test
    // play the helper's part (what it leaves in the administrators' run
    // folder).
    sealed class FakeElevator : IElevator
    {
        public bool Decline;
        public string Exe, Args, From;
        public bool HeldWhileStarting;
        public Action<string> Writes;     // the helper's part, given its admin run folder
        public string AdminRoot;
        public int Code = 0;

        static string ArgAfter(string args, string flag)
        {
            int i = args.IndexOf(flag + " ", StringComparison.Ordinal);
            if (i < 0) return null;
            string rest = args.Substring(i + flag.Length + 1);
            if (rest.StartsWith("\"")) return rest.Substring(1, rest.IndexOf('"', 1) - 1);
            int sp = rest.IndexOf(' ');
            return sp < 0 ? rest : rest.Substring(0, sp);
        }

        public IElevated Start(string exe, string args, out bool declined, out string why)
        {
            Exe = exe; Args = args; why = null;
            declined = Decline;
            From = ArgAfter(args, "--from");
            if (From != null)
            {
                // An attacker in the same account, mid-start: the checked
                // download can't be changed while the helper copies it.
                try { using (new FileStream(Path.Combine(From, ReleaseNames.SetupAsset), FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete)) { } HeldWhileStarting = false; }
                catch (IOException) { HeldWhileStarting = true; }
            }
            if (Decline) return null;
            string run = ArgAfter(args, "--run");
            if (Writes != null && run != null)
            {
                string dir = Path.Combine(AdminRoot, run);
                Directory.CreateDirectory(dir);
                Writes(dir);
            }
            return new FakeElevated { Code = Code };
        }
    }

    const string HelperExe = @"C:\Program Files\Domovoi\DomovoiSetupHelper.exe";

    static StepEnv FakeEnv(FakeFiles files, FakeElevator elev, string helper = HelperExe)
    {
        string admin = TempDir("admin");
        elev.AdminRoot = admin;
        return new StepEnv { Files = files, Elevator = elev, Key = TestKey(), Installed = "1.0.0", Helper = helper, UpdatesRoot = TempDir("step"), AdminRoot = admin };
    }

    static UpdateState Behind(string tag = "v1.0.1", string version = "1.0.1")
    {
        return new UpdateState { Installed = "1.0.0", TargetTag = tag, TargetVersion = version, TargetCommit = FixtureCommit, StepNeeded = true, Internet = "always", RequestId = "r1" };
    }

    static void StepFlow()
    {
        Run("windows step: checked, then the helper is started elevated with the held download, and Setup's result comes from the admin folder", delegate
        {
            var files = FakeFiles.Release("manifest-1.0.1.json");
            var elev = new FakeElevator();
            var env = FakeEnv(files, elev);
            string runDir;
            StepResult r = WindowsStep.Start(env, Behind(), false, out runDir);
            True(r.Launched && r.Kind == "setup" && r.Run == Path.GetFileName(runDir), "started: " + r.Outcome + " " + r.Detail);
            Eq(HelperExe, elev.Exe, "the installed helper is what runs elevated, never the download");
            Eq(UpdateRun.StageArguments(runDir, r.Run, "v1.0.1", FixtureCommit, false), elev.Args, "run-update with the private run folder");
            True(UpdateRun.IsPrivate(runDir), "the run folder is private");
            True(elev.HeldWhileStarting, "nobody could write the Setup while the elevated helper started");
            True(r.Held != null, "and it stays held until the helper has exited");
            Throws<IOException>(delegate { using (new FileStream(Path.Combine(runDir, ReleaseNames.SetupAsset), FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete)) { } }, "still held after Start");
            foreach (string n in new[] { ReleaseNames.Manifest, ReleaseNames.ManifestSig, ReleaseNames.SetupSig })
                True(File.Exists(Path.Combine(runDir, n)), n + " beside it, for the helper's own check");
            SeqEq(new[] { "v1.0.1/" + ReleaseNames.Manifest, "v1.0.1/" + ReleaseNames.ManifestSig, "v1.0.1/" + ReleaseNames.SetupSig, "v1.0.1/" + ReleaseNames.SetupAsset }, files.Asked, "the release's four files");
            int code = r.Process.WaitForExit();
            r.Held.Dispose();
            True(WindowsStep.Finish(env.AdminRoot, r.Run, code, "setup", r.Version) == null, "handed over to Setup: still running");
            string admin = Path.Combine(env.AdminRoot, r.Run);
            Directory.CreateDirectory(admin);
            File.WriteAllText(Path.Combine(admin, UpdateRun.ResultFile), "ok=1\r\nversion=1.0.1\r\n");
            StepResult fin = WindowsStep.Finish(env.AdminRoot, r.Run, code, "setup", r.Version);
            Eq(Outcome.Ok, fin.Outcome, "ok");
            Eq("Domovoi is updated: the Windows side is now 1.0.1.", WindowsStep.Words(fin, true), "words");
        });
        Run("windows step: the helper's own check refused the copy: never run, its words", delegate
        {
            var elev = new FakeElevator { Code = 1, Writes = delegate (string dir) { File.WriteAllText(Path.Combine(dir, UpdateRun.ResultFile), "ok=0\r\noutcome=bad_signature\r\nerror=Domovoi didn't run the Setup: its signature doesn't match the file\r\n"); } };
            var env = FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev);
            string runDir;
            StepResult r = WindowsStep.Start(env, Behind(), false, out runDir);
            r.Held.Dispose();
            StepResult fin = WindowsStep.Finish(env.AdminRoot, r.Run, r.Process.WaitForExit(), "setup", r.Version);
            Eq(Outcome.BadSignature, fin.Outcome, "the helper's refusal");
            True(fin.Detail.Contains("doesn't match"), fin.Detail);
        });
        Run("windows step: Windows' permission declined (simulated): nothing runs, words by what ran", delegate
        {
            var elev = new FakeElevator { Decline = true };
            string runDir;
            StepResult r = WindowsStep.Start(FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev), Behind(), false, out runDir);
            Eq(Outcome.Declined, r.Outcome, "declined");
            True(!r.Launched && r.Process == null && r.Held == null, "nothing runs, nothing held");
            File.Delete(Path.Combine(runDir, ReleaseNames.SetupAsset));
            string words = WindowsStep.Words(r, true);
            True(words.Contains("half done") && words.Contains("Linux part is updated") && words.Contains("try again"), words);
            string alone = WindowsStep.Words(r, false);
            True(alone.Contains("nothing was updated") && !alone.Contains("Linux part is updated"), "a Windows-only press updated nothing: " + alone);
        });
        Run("windows step: a bad signature, the wrong key or an older Setup is never started", delegate
        {
            var files = FakeFiles.Release("manifest-1.0.1.json");
            files.Assets[ReleaseNames.SetupAsset] = (byte[])SigFile("setup.bin").Clone();
            files.Assets[ReleaseNames.SetupAsset][7] ^= 0x10;
            var elev = new FakeElevator();
            string runDir;
            StepResult r = WindowsStep.Start(FakeEnv(files, elev), Behind(), false, out runDir);
            Eq(Outcome.BadSignature, r.Outcome, "bad");
            True(elev.Exe == null, "never started");
            True(!File.Exists(Path.Combine(runDir, ReleaseNames.SetupAsset)), "and deleted");
            True(r.Detail.StartsWith("Domovoi didn't run the downloaded Setup: "), r.Detail);

            files = FakeFiles.Release("manifest-1.0.1.json");
            files.Assets[ReleaseNames.SetupSig] = SigFile("setup.bin.other-key.sig");
            r = WindowsStep.Start(FakeEnv(files, elev), Behind(), false, out runDir);
            Eq(Outcome.BadSignature, r.Outcome, "another key");
            True(elev.Exe == null, "never started");

            var env = FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev);
            env.Key = OtherKey();
            Eq(Outcome.BadSignature, WindowsStep.Start(env, Behind(), false, out runDir).Outcome, "pinned to another key");

            r = WindowsStep.Start(FakeEnv(FakeFiles.Release("manifest-0.9.0.json"), elev), Behind("v0.9.0", "0.9.0"), false, out runDir);
            Eq(Outcome.NotNewer, r.Outcome, "older");
            env = FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev);
            env.Installed = null;
            r = WindowsStep.Start(env, Behind(), false, out runDir);
            Eq(Outcome.NotNewer, r.Outcome, "an installed version nobody knows fails closed");
            True(r.Detail.Contains("isn't known"), r.Detail);
            True(elev.Exe == null, "never started");
        });
        Run("windows step: no release-key signature, a failed download, internet off, not installed by Setup, a developer run", delegate
        {
            var elev = new FakeElevator();
            string runDir;
            var files = FakeFiles.Release("manifest-1.0.1.json");
            files.Assets.Remove(ReleaseNames.ManifestSig);
            StepResult r = WindowsStep.Start(FakeEnv(files, elev), Behind(), false, out runDir);
            Eq(Outcome.NoSignedSetup, r.Outcome, "no signature in the release");
            True(r.Detail.Contains("no signed Windows Setup"), r.Detail);
            files = FakeFiles.Release("manifest-1.0.1.json");
            files.Fail = "the name could not be resolved";
            r = WindowsStep.Start(FakeEnv(files, elev), Behind(), false, out runDir);
            Eq(Outcome.DownloadFailed, r.Outcome, "download");
            True(r.Detail.Contains("couldn't be downloaded") && r.Detail.Contains("Nothing was run"), r.Detail);
            UpdateState off = Behind();
            off.Internet = "never";
            Eq(Outcome.InternetOff, WindowsStep.Start(FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev), off, false, out runDir).Outcome, "never");
            Eq(Outcome.NotInstalled, WindowsStep.Start(FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev, null), Behind(), false, out runDir).Outcome, "no helper");
            var dev = FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev);
            dev.Dev = true;
            r = WindowsStep.Start(dev, Behind(), false, out runDir);
            True(!r.Launched && r.Detail.Contains("developer run") && r.Held == null, "a developer run never elevates");
            True(elev.Exe == null, "nothing was started in any of these");
            UpdateState current = Behind();
            current.StepNeeded = false;
            Eq(Outcome.NothingNew, WindowsStep.Start(FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev), current, false, out runDir).Outcome, "nothing to do");
            UpdateState busy = Behind();
            busy.LinuxBusy = true;
            Eq(Outcome.Failed, WindowsStep.Start(FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev), busy, false, out runDir).Outcome, "an update under way");
        });
        Run("windows step: from the menu, the Linux part first (inside the Setup, or the helper alone)", delegate
        {
            var elev = new FakeElevator();
            string runDir;
            StepResult r = WindowsStep.Start(FakeEnv(FakeFiles.Release("manifest-1.0.1.json"), elev), Behind(), true, out runDir);
            True(r.Launched && elev.Args.EndsWith(" --linuxfirst"), "the Setup with the Linux part first");
            r.Held.Dispose();
            UpdateState current = Behind();
            current.StepNeeded = false;
            elev = new FakeElevator { Writes = delegate (string dir) { File.WriteAllText(Path.Combine(dir, UpdateRun.ResultFile), "ok=1\r\nlinux=nothing_new\r\n"); } };
            var files = new FakeFiles();
            var env = FakeEnv(files, elev);
            r = WindowsStep.Start(env, current, true, out runDir);
            True(r.Launched && r.Kind == "linux" && r.Held == null, "started");
            Eq(HelperExe, elev.Exe, "the installed helper");
            Eq(UpdateRun.LinuxArguments(r.Run), elev.Args, "its Linux part");
            Eq(0, files.Asked.Count, "nothing downloaded");
            Eq(Outcome.NothingNew, WindowsStep.Finish(env.AdminRoot, r.Run, r.Process.WaitForExit(), "linux", null).Outcome, "nothing new");
        });
        Run("windows step: a failed repair names its log; a failed Linux part changed nothing on Windows", delegate
        {
            string admin = TempDir("fin");
            string run = "20261022-213000-0a1b2c3d";
            string dir = Path.Combine(admin, run);
            Directory.CreateDirectory(dir);
            File.WriteAllText(Path.Combine(dir, UpdateRun.ResultFile), "ok=0\r\nerror=Setting up Domovoi's Linux system did not work: provision.sh failed at venv\r\n");
            StepResult f = WindowsStep.Finish(admin, run, 0, "setup", "1.0.1");
            Eq(Outcome.RepairFailed, f.Outcome, "repair failed");
            Eq(Path.Combine(dir, UpdateRun.LogFile), f.Log, "Setup's log, in the admin folder");
            True(WindowsStep.Words(f, true).Contains(f.Log) && f.Detail.Contains("provision.sh failed"), WindowsStep.Words(f, true));
            File.WriteAllText(Path.Combine(dir, UpdateRun.ResultFile), "ok=0\r\nlinux=failed\r\nerror=the Linux side's update rolled_back\r\n");
            f = WindowsStep.Finish(admin, run, 0, "setup", "1.0.1");
            Eq(Outcome.LinuxFailed, f.Outcome, "Linux part failed");
            True(f.Detail.Contains("Nothing on Windows was changed"), f.Detail);
            File.Delete(Path.Combine(dir, UpdateRun.ResultFile));
            f = WindowsStep.Finish(admin, run, 1, "setup", "1.0.1");
            Eq(Outcome.RepairFailed, f.Outcome, "the helper failed without a word: no success");
        });
        Run("windows step: a run another tray started is picked up from pending.txt", delegate
        {
            string root = TempDir("pending");
            var p = new PendingRun { Run = "20261022-213000-0a1b2c3d", RequestId = "r1", Claim = "c1", Kind = "setup", Started = "2026-10-22T21:30:00.0000000Z", Version = "1.0.1", AfterLinux = true };
            p.Save(root);
            PendingRun back = PendingRun.Load(root);
            True(back != null && back.Run == p.Run && back.RequestId == "r1" && back.Claim == "c1" && back.Kind == "setup" && back.AfterLinux && back.Outcome == null && !back.Shown, "round trip");
            back.Outcome = Outcome.Ok; back.Shown = true; back.Detail = "a\nb";
            back.Save(root);
            back = PendingRun.Load(root);
            True(back.Outcome == Outcome.Ok && back.Shown && back.Detail == "a b", "the outcome");
            PendingRun.Clear(root);
            True(PendingRun.Load(root) == null, "cleared");
            File.WriteAllText(PendingRun.PathIn(root), "run=..\\..\\Windows\r\n");
            True(PendingRun.Load(root) == null, "a run name that isn't one is ignored");
        });
    }

    static void HelperStage()
    {
        Run("helper: it checks its own copy in the admin folder, so a swap after the tray's check never runs (review item 3)", delegate
        {
            string from = TempDir("helper-from"), dir = TempDir("helper-admin");
            File.WriteAllBytes(Path.Combine(from, ReleaseNames.SetupAsset), SigFile("setup.bin"));
            File.WriteAllBytes(Path.Combine(from, ReleaseNames.SetupSig), SigFile("setup.bin.sig"));
            File.WriteAllBytes(Path.Combine(from, ReleaseNames.Manifest), SigFile("manifest-1.0.1.json"));
            File.WriteAllBytes(Path.Combine(from, ReleaseNames.ManifestSig), SigFile("manifest-1.0.1.json.sig"));
            SetupManifest m;
            string code;
            string why = Domovoi.Windows.Helper.Update.CopyAndCheck(from, dir, Want(), out m, out code);
            True(why == null && m.Version == "1.0.1", "the good copy passes: " + why);
            True(File.ReadAllBytes(Path.Combine(dir, ReleaseNames.SetupAsset)).SequenceEqual(SigFile("setup.bin")), "and is what runs");
            // The same account swaps the Setup in its own folder after the
            // tray checked it: the helper's copy of that doesn't check out.
            byte[] evil = SigFile("setup.bin");
            evil[100] ^= 0xff;
            File.WriteAllBytes(Path.Combine(from, ReleaseNames.SetupAsset), evil);
            string dir2 = TempDir("helper-admin2");
            why = Domovoi.Windows.Helper.Update.CopyAndCheck(from, dir2, Want(), out m, out code);
            True(why != null && code == SetupCheck.BadSignature, "the swapped Setup is refused: " + why);
            var w = Want();
            w.Installed = null;
            why = Domovoi.Windows.Helper.Update.CopyAndCheck(from, TempDir("helper-admin3"), w, out m, out code);
            True(why != null, "and so is anything it can't compare");
            File.Delete(Path.Combine(from, ReleaseNames.SetupSig));
            why = Domovoi.Windows.Helper.Update.CopyAndCheck(from, TempDir("helper-admin4"), Want(), out m, out code);
            True(why != null && code == SetupCheck.NoSignedSetup, "a missing signature: " + why);
        });
    }

    // -- the poll and the notification, against a pretend core -------------

    // A pretend core with the claim and result rules of domovoi/windows_side.py.
    sealed class FakeCore : IUpdateServer
    {
        public string RequestId = "0123456789abcdef";
        public bool OnThisPc = true, StepNeeded = true;
        public string ClaimedBy;
        public readonly List<string> Calls = new List<string>();
        public readonly List<string> Results = new List<string>();
        int claims;

        public UpdateState State()
        {
            Calls.Add("state");
            var s = new UpdateState { Installed = "1.0.0", TargetTag = "v1.0.1", TargetVersion = "1.0.1", TargetCommit = FixtureCommit, StepNeeded = StepNeeded, Internet = "always" };
            if (RequestId != null) { s.RequestId = RequestId; s.RequestOnThisPc = OnThisPc; s.RequestClaimed = ClaimedBy != null; }
            return s;
        }

        public string Claim(string requestId)
        {
            Calls.Add("claim");
            if (requestId != RequestId || ClaimedBy != null) return null;
            ClaimedBy = "token" + (++claims);
            return ClaimedBy;
        }

        public bool Report(string requestId, string claim, string outcome, string detail, string log, string version)
        {
            Calls.Add("report " + outcome);
            if (requestId != null && claim != ClaimedBy) return true;   // refused: not retried
            Results.Add(outcome);
            if (requestId != null && requestId == RequestId) RequestId = null;
            if (outcome == Outcome.Ok) StepNeeded = false;
            return true;
        }
    }

    static string PendingSetupRun(string root, string admin, string result)
    {
        string run = "20261022-213000-0a1b2c3d";
        new PendingRun { Run = run, RequestId = "0123456789abcdef", Claim = "token1", Kind = "setup", AfterLinux = true,
                         Started = DateTime.UtcNow.ToString("o", System.Globalization.CultureInfo.InvariantCulture), Version = "1.0.1" }.Save(root);
        Directory.CreateDirectory(Path.Combine(admin, run));
        if (result != null) File.WriteAllText(Path.Combine(Path.Combine(admin, run), UpdateRun.ResultFile), result);
        return run;
    }

    static void UpdatePollFlow()
    {
        const string StateJson = "{\"schema\": 1, \"windows\": {\"version\": \"1.0.0\", \"build_commit\": null}, "
            + "\"target\": {\"tag\": \"v1.0.1\", \"version\": \"1.0.1\", \"commit\": \"" + FixtureCommit + "\"}, "
            + "\"step_needed\": true, \"why_not\": null, \"internet\": \"always\", \"channel\": \"stable\", "
            + "\"linux\": {\"busy\": false, \"last_update\": {\"status\": \"ok\", \"started_at\": \"2026-10-22T20:00:00Z\"}}, "
            + "\"request\": {\"id\": \"req-7\", \"on_this_pc\": false, \"after_linux\": true, \"claimed\": false, \"at\": \"2026-10-22T20:05:00Z\"}}";
        Run("update poll: the core's answer, read over loopback", delegate
        {
            using (var d = new FakeDashboard())
            {
                d.Answer = (k, bearer, body) => k.StartsWith("GET /v1/windows/update?tray=") ? R(200, StateJson) : R(404, "{}");
                UpdateState s = new HttpUpdateServer(d.Url).State();
                True(s != null, "read");
                True(s.Installed == "1.0.0" && s.TargetTag == "v1.0.1" && s.TargetCommit == FixtureCommit && s.StepNeeded, "the step");
                True(s.RequestId == "req-7" && !s.RequestOnThisPc && s.RequestAfterLinux && !s.RequestClaimed && s.Internet == "always" && !s.LinuxBusy && s.LastUpdateStatus == "ok", "the request");
            }
            using (var d = new FakeDashboard())
            {
                d.Answer = (k, bearer, body) => R(403, "{\"detail\": \"this PC only\"}");
                True(new HttpUpdateServer(d.Url).State() == null, "a refusal is no state");
            }
        });
        Run("update poll: a request from another device notifies, one from this PC starts at once, each once, never another tray's", delegate
        {
            UpdateState s = UpdateState.From(Json.ParseObject(StateJson));
            Eq(UpdateAction.Notify, UpdateDecision.OnState(s, null, false, false), "another device: a notification");
            Eq(UpdateAction.Nothing, UpdateDecision.OnState(s, "req-7", false, false), "only once");
            Eq(UpdateAction.Nothing, UpdateDecision.OnState(s, null, true, false), "not while a step runs");
            Eq(UpdateAction.Nothing, UpdateDecision.OnState(s, null, false, true), "not while a run's outcome is on its way");
            s.RequestOnThisPc = true;
            Eq(UpdateAction.StartNow, UpdateDecision.OnState(s, null, false, false), "this PC: at once");
            s.RequestClaimed = true;
            Eq(UpdateAction.Nothing, UpdateDecision.OnState(s, null, false, false), "another tray claimed it");
            s.RequestClaimed = false;
            s.LinuxBusy = true;
            Eq(UpdateAction.Nothing, UpdateDecision.OnState(s, null, false, false), "not while the Linux part runs");
            s.LinuxBusy = false;
            s.StepNeeded = false;
            Eq(UpdateAction.Nothing, UpdateDecision.OnState(s, null, false, false), "nothing to do");
            Eq(UpdateAction.Nothing, UpdateDecision.OnState(null, null, false, false), "no core");
            var bad = Json.ParseObject(StateJson.Replace("\"v1.0.1\"", "\"../x\""));
            True(!UpdateState.From(bad).StepNeeded, "a target that isn't a tag is no step");
        });
        Run("update poll: a fresh tray after a failed repair reports first, then reads: no instant second prompt (review probe 3)", delegate
        {
            string root = TempDir("poll-fail"), admin = TempDir("poll-fail-admin");
            PendingSetupRun(root, admin, "ok=0\r\nerror=the media folder isn't there\r\n");
            var core = new FakeCore { ClaimedBy = "token1" };
            UpdateState st;
            PendingDone done;
            UpdateAction act = UpdatePoll.Run(core, root, admin, DateTime.UtcNow, null, false, out st, out done);
            SeqEq(new[] { "report repair_failed", "state" }, core.Calls, "the outcome before the state");
            Eq(UpdateAction.Nothing, act, "the request it answered is gone: no second run");
            True(done != null && done.Show && done.AfterLinux && done.Result.Outcome == Outcome.RepairFailed, "said once");
            True(PendingRun.Load(root) == null, "forgotten");
        });
        Run("update poll: after a successful repair no second download, and the core keeps its 'ok'", delegate
        {
            string root = TempDir("poll-ok"), admin = TempDir("poll-ok-admin");
            PendingSetupRun(root, admin, "ok=1\r\nversion=1.0.1\r\n");
            var core = new FakeCore { ClaimedBy = "token1" };
            UpdateState st;
            PendingDone done;
            Eq(UpdateAction.Nothing, UpdatePoll.Run(core, root, admin, DateTime.UtcNow, null, false, out st, out done), "nothing to start");
            True(!st.StepNeeded && st.RequestId == null, "the step is done");
            Eq(UpdateAction.Nothing, UpdatePoll.Run(core, root, admin, DateTime.UtcNow, null, false, out st, out done), "and on the next poll");
            SeqEq(new[] { Outcome.Ok }, core.Results, "one 'ok', never overwritten");
            True(!core.Calls.Contains("claim"), "nothing claimed, nothing downloaded");
        });
        Run("update poll: a run still going is left alone", delegate
        {
            string root = TempDir("poll-run"), admin = TempDir("poll-run-admin");
            PendingSetupRun(root, admin, null);
            var core = new FakeCore { ClaimedBy = "token1" };
            UpdateState st;
            PendingDone done;
            Eq(UpdateAction.Nothing, UpdatePoll.Run(core, root, admin, DateTime.UtcNow, null, false, out st, out done), "nothing while Setup runs");
            True(done == null && PendingRun.Load(root) != null && !core.Calls.Exists(c => c.StartsWith("report")), "nothing reported yet");
        });
        Run("update poll: two trays, one request: only the first claim runs", delegate
        {
            var core = new FakeCore();
            string a = core.Claim(core.RequestId), b = core.Claim(core.RequestId);
            True(a != null && b == null, "the second account's tray gets no claim");
            True(core.State().RequestClaimed, "and the request reads as claimed");
            Eq(UpdateAction.Nothing, UpdateDecision.OnState(core.State(), null, false, false), "so the other tray does nothing");
        });
        Run("update poll: claim and outcome over loopback", delegate
        {
            using (var d = new FakeDashboard())
            {
                string got = null;
                bool taken = false;
                d.Answer = (k, bearer, body) =>
                {
                    if (k == "POST /v1/windows/update/claim")
                    {
                        if (taken) return R(409, "{\"reason\": \"claimed\"}");
                        taken = true;
                        True(body.Contains("\"request_id\": \"req-7\""), "the request");
                        return R(200, "{\"ok\": true, \"claim\": \"tok\"}");
                    }
                    if (k == "POST /v1/windows/update/result") { got = body; return R(200, "{\"ok\": true}"); }
                    return R(404, "{}");
                };
                var srv = new HttpUpdateServer(d.Url);
                Eq("tok", srv.Claim("req-7"), "claimed");
                True(srv.Claim("req-7") == null, "taken");
                True(srv.Report("req-7", "tok", Outcome.Declined, "said \"no\"", null, null), "accepted");
                True(got.Contains("\"request_id\": \"req-7\"") && got.Contains("\"claim\": \"tok\"") && got.Contains("\"outcome\": \"declined\"") && got.Contains("said \\\"no\\\""), got);
                True(d.Seen.All(x => !x.Contains("NO-XRW")), "with X-Requested-With");
            }
            using (var d = new FakeDashboard())
            {
                d.Answer = (k, bearer, body) => R(403, "{\"detail\": \"not this tray's\"}");
                True(new HttpUpdateServer(d.Url).Report("req-7", "x", Outcome.Ok, null, null, null), "a refusal isn't retried forever");
            }
        });
    }

    static void SideReport()
    {
        Run("windows side: the report DomovoiHost writes into the distro", delegate
        {
            string t = WindowsSideReport.Content("1.0.1", FixtureCommit, new DateTime(2026, 10, 22, 21, 30, 0, DateTimeKind.Utc));
            Eq("{\"schema\": 1, \"version\": \"1.0.1\", \"build_commit\": \"" + FixtureCommit + "\", \"written_at\": \"2026-10-22T21:30:00Z\"}\n", t, "content");
            True(WindowsSideReport.Content("1.0.0-rc3", null, DateTime.UtcNow).Contains("\"build_commit\": null"), "no commit");
            Throws<ArgumentException>(delegate { WindowsSideReport.Content("1.0\"; rm -rf /", null, DateTime.UtcNow); }, "only version characters");
            Throws<ArgumentException>(delegate { WindowsSideReport.Content("1.0.0", "HEAD", DateTime.UtcNow); }, "only a commit");
            string sh = WindowsSideReport.Script();
            True(sh.StartsWith("set -e; umask 022;") && sh.Contains("chown root:root /var/lib/domovoi-windows") && sh.Contains("mv -f /var/lib/domovoi-windows/.side.json.new /var/lib/domovoi-windows/side.json"), sh);
        });
        Run("windows side: the Linux update runs the dashboard's own, as the service user", delegate
        {
            SeqEq(new[] { "-d", "domovoi", "-u", "domovoi", "--cd", "/opt/domovoi/domovoi", "--exec", "/opt/domovoi/domovoi/.venv/bin/python", "-m", "domovoi.windows_side", "linux-update" },
                LinuxUpdate.WslArgs("domovoi", "domovoi"), "wsl arguments");
            var a = LinuxUpdate.Answer("some log line\n{\"ok\": true, \"started\": true, \"to_sha\": \"abc\"}\n");
            True(a != null && Json.Bool(a, "started") == true, "the last JSON line");
            True(LinuxUpdate.Answer("Traceback ...\n") == null, "none");
        });
    }
}
