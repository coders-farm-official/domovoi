// Unit tests for windows/common, compiled with the same in-box C# compiler
// as the exes (no test framework to install), together with the helper's
// and the host's own sources (so they are built and some of the helper's
// file handling is exercised on throwaway folders under %TEMP%). Nothing
// here changes the PC: no account, service, registry or firewall call is
// made. build.ps1 builds and runs it; domovoi/tests/test_windows_installer.py
// runs build.ps1 on Windows. Exit code 0 when every test passes.

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Management.Automation;
using System.Management.Automation.Language;
using System.Runtime.InteropServices;
using Domovoi.Windows;

static class Tests
{
    static int passed, failed;
    static string current;

    static void Fail(string what)
    {
        failed++;
        Console.WriteLine("FAIL " + current + ": " + what);
        throw new TestFailed();
    }

    sealed class TestFailed : Exception { }

    static void True(bool cond, string what) { if (!cond) Fail(what); }

    static void Eq<T>(T expected, T actual, string what)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
            Fail(what + ": expected <" + expected + "> got <" + actual + ">");
    }

    static void SeqEq(IEnumerable<string> expected, IEnumerable<string> actual, string what)
    {
        string e = string.Join(" | ", expected.ToArray()), a = string.Join(" | ", actual.ToArray());
        if (e != a) Fail(what + ":\n  expected " + e + "\n  got      " + a);
    }

    static void Throws<E>(Action a, string what) where E : Exception
    {
        try { a(); }
        catch (E) { return; }
        catch (Exception x) { Fail(what + ": threw " + x.GetType().Name + " instead of " + typeof(E).Name); }
        Fail(what + ": did not throw " + typeof(E).Name);
    }

    static void Run(string name, Action t)
    {
        current = name;
        try
        {
            t();
            passed++;
            Console.WriteLine("ok   " + name);
        }
        catch (TestFailed) { }
        catch (Exception x)
        {
            failed++;
            Console.WriteLine("FAIL " + name + ": " + x.GetType().Name + ": " + x.Message);
        }
    }

    static int Main()
    {
        ProgressLines();
        Outcomes();
        Args();
        Media();
        LanRules();
        PortOwners();
        WslConfigText();
        FirewallRules();
        Quoting();
        SetupCodes();
        Protocol();
        Uninstall();
        Runner();
        WslListing();
        MediaRoots();
        FirewallFilter();
        Links();
        Console.WriteLine();
        Console.WriteLine(Inv.F("{0} passed, {1} failed", passed, failed));
        return failed == 0 ? 0 : 1;
    }

    // -- provision.sh progress lines -------------------------------------

    // The final line of the real run of 2026-10-11 (step1\realrun-logs\t6-run1.log).
    const string RealFinal = "@@DOMOVOI {\"v\":1,\"step\":\"finish\",\"index\":21,\"total\":21,\"state\":\"done\",\"percent\":100,\"message\":\"Domovoi is running; restart the distro to apply /etc/wsl.conf\",\"final\":true,\"result\":\"ok\",\"restart_required\":true,\"exit_code\":0,\"failed_step\":null,\"head\":\"eb4a497fe821c08b6b8c39d7603ff7384d696dfc\",\"tier\":\"standard\",\"wsl\":true,\"gpu\":[\"NVIDIA GeForce RTX 4070 Ti SUPER\",\"NVIDIA GeForce RTX 4070 Ti SUPER\"],\"warnings\":2,\"dashboard\":\"http://localhost:6369\"}";

    static void ProgressLines()
    {
        Run("progress: a start line", delegate
        {
            var p = ProvisionLine.TryParse("@@DOMOVOI {\"v\":1,\"step\":\"venv\",\"index\":8,\"total\":21,\"state\":\"start\",\"percent\":24,\"message\":\"Installing Python packages\"}");
            True(p != null, "parsed");
            Eq("venv", p.Step, "step"); Eq(8, p.Index, "index"); Eq(21, p.Total, "total");
            Eq("start", p.State, "state"); Eq(24, p.Percent, "percent");
            Eq("Installing Python packages", p.Message, "message");
            True(!p.Final, "not final"); True(p.Bytes == null && p.BytesTotal == null, "no bytes");
        });
        Run("progress: a download line, size unknown", delegate
        {
            var p = ProvisionLine.TryParse("@@DOMOVOI {\"v\":1,\"step\":\"models\",\"index\":11,\"total\":21,\"state\":\"progress\",\"percent\":40,\"message\":\"qwen3\",\"bytes\":123456789012,\"bytes_total\":null}\r");
            True(p != null, "parsed with a CR");
            Eq(123456789012L, p.Bytes.Value, "bytes");
            True(p.BytesTotal == null, "bytes_total null");
        });
        Run("progress: the real final line", delegate
        {
            var p = ProvisionLine.TryParse(RealFinal);
            True(p != null && p.Final, "final");
            Eq("ok", p.Result, "result"); True(p.RestartRequired, "restart_required");
            Eq(0, p.ExitCode.Value, "exit_code"); True(p.FailedStep == null, "failed_step null");
            Eq("standard", p.Tier, "tier"); Eq(true, p.Wsl.Value, "wsl"); Eq(2, p.Warnings, "warnings");
            Eq(2, p.Gpu.Count, "two GPUs"); Eq("NVIDIA GeForce RTX 4070 Ti SUPER", p.Gpu[0], "gpu name");
            Eq("eb4a497fe821c08b6b8c39d7603ff7384d696dfc", p.Head, "head");
        });
        Run("progress: gpu null is unknown, [] is the CPU alone", delegate
        {
            var n = ProvisionLine.TryParse("@@DOMOVOI {\"v\":1,\"step\":\"finish\",\"index\":21,\"total\":21,\"state\":\"fail\",\"percent\":30,\"message\":\"x\",\"final\":true,\"result\":\"failed\",\"restart_required\":false,\"exit_code\":1,\"failed_step\":\"apt\",\"head\":null,\"tier\":\"\",\"wsl\":true,\"gpu\":null,\"warnings\":0,\"dashboard\":\"http://localhost:6369\"}");
            True(n != null && n.Gpu == null, "null gpu");
            Eq("apt", n.FailedStep, "failed_step");
            var e = ProvisionLine.TryParse("@@DOMOVOI {\"v\":1,\"step\":\"finish\",\"index\":21,\"total\":21,\"state\":\"done\",\"percent\":100,\"message\":\"\",\"final\":true,\"result\":\"ok\",\"restart_required\":false,\"exit_code\":0,\"failed_step\":null,\"head\":null,\"tier\":\"floor\",\"wsl\":true,\"gpu\":[],\"warnings\":0,\"dashboard\":\"\"}");
            True(e != null && e.Gpu != null && e.Gpu.Count == 0, "empty gpu list");
        });
        Run("progress: anything else is not a progress line", delegate
        {
            string[] bad =
            {
                null, "", "provision: [apt] Installing system packages",
                " @@DOMOVOI {\"v\":1,\"step\":\"apt\",\"state\":\"start\"}",
                "21:22:56 @@DOMOVOI {\"v\":1,\"step\":\"apt\",\"state\":\"start\"}",
                "@@DOMOVOI{\"v\":1,\"step\":\"apt\",\"state\":\"start\"}",
                "@@DOMOVOI {not json",
                "@@DOMOVOI [1,2]",
                "@@DOMOVOI {\"v\":2,\"step\":\"apt\",\"state\":\"start\"}",
                "@@DOMOVOI {\"v\":1,\"state\":\"start\"}",
                "@@DOMOVOI {\"v\":1,\"step\":\"APT\",\"state\":\"start\"}",
                "@@DOMOVOI {\"v\":1,\"step\":\"apt\"}",
                "@@DOMOVOI {\"v\":1,\"step\":\"finish\",\"state\":\"done\",\"final\":true}",
            };
            foreach (string b in bad) True(ProvisionLine.TryParse(b) == null, "rejected: " + (b ?? "null"));
        });
        Run("progress: percent is clamped", delegate
        {
            Eq(100, ProvisionLine.TryParse("@@DOMOVOI {\"v\":1,\"step\":\"apt\",\"state\":\"done\",\"percent\":250}").Percent, "high");
            Eq(0, ProvisionLine.TryParse("@@DOMOVOI {\"v\":1,\"step\":\"apt\",\"state\":\"done\",\"percent\":-3}").Percent, "low");
        });
    }

    static void Outcomes()
    {
        string why;
        Run("outcome: ok with and without a restart", delegate
        {
            var p = ProvisionLine.TryParse(RealFinal);
            Eq(ProvisionVerdict.DoneRestartDistro, ProvisionOutcome.Classify(0, p, out why), "restart required");
            p.RestartRequired = false;
            Eq(ProvisionVerdict.Done, ProvisionOutcome.Classify(0, p, out why), "done");
        });
        Run("outcome: exit codes 3, 4, 1, 2", delegate
        {
            var p = ProvisionLine.TryParse(RealFinal);
            p.Result = "rerun_after_restart";
            Eq(ProvisionVerdict.RestartDistroAndRerun, ProvisionOutcome.Classify(3, p, out why), "3");
            p.Result = "busy";
            Eq(ProvisionVerdict.Busy, ProvisionOutcome.Classify(4, p, out why), "4");
            p.Result = "failed"; p.FailedStep = "venv"; p.Message = "pip said no";
            Eq(ProvisionVerdict.Failed, ProvisionOutcome.Classify(1, p, out why), "1");
            True(why.Contains("venv") && why.Contains("pip said no"), "reason names the step: " + why);
            p.Result = "usage_error";
            Eq(ProvisionVerdict.Failed, ProvisionOutcome.Classify(2, p, out why), "2");
        });
        Run("outcome: no final line is lost, a dry run at exit 0 is a failure", delegate
        {
            Eq(ProvisionVerdict.Lost, ProvisionOutcome.Classify(137, null, out why), "lost");
            True(why.Contains("137"), "exit code in reason");
            var notFinal = ProvisionLine.TryParse("@@DOMOVOI {\"v\":1,\"step\":\"apt\",\"state\":\"start\"}");
            Eq(ProvisionVerdict.Lost, ProvisionOutcome.Classify(0, notFinal, out why), "a non-final line");
            var p = ProvisionLine.TryParse(RealFinal);
            p.Result = "dry_run";
            Eq(ProvisionVerdict.Failed, ProvisionOutcome.Classify(0, p, out why), "dry run");
        });
        Run("outcome: the overall bar only moves forward", delegate
        {
            string[] phases = { HostProgress.PhaseConfig, HostProgress.PhaseImport, HostProgress.PhaseProvision, HostProgress.PhaseRestart, HostProgress.PhaseVerify, HostProgress.PhaseHealth, HostProgress.PhaseReady };
            int last = -1;
            foreach (string ph in phases)
            {
                foreach (int pc in new[] { 0, 50, 100 })
                {
                    int o = HostProgress.Overall(ph, pc);
                    True(o >= last, ph + " " + pc + " went back: " + o + " < " + last);
                    True(o >= 0 && o <= 100, "in range");
                    last = o;
                }
            }
            Eq(100, HostProgress.Overall(HostProgress.PhaseReady, 0), "ready is 100");
        });
    }

    static ProvisionOptions Opts()
    {
        var o = new ProvisionOptions();
        o.RepoUrl = "https://github.com/coders-farm-official/domovoi";
        o.Ref = "eb4a497fe821c08b6b8c39d7603ff7384d696dfc";
        o.Internet = "never";
        o.LanIp = "192.168.0.240";
        o.Media = MediaPaths.Under(@"C:\Users\Public\Domovoi\");
        return o;
    }

    static void Args()
    {
        Run("args: the provision.sh options for one install", delegate
        {
            SeqEq(new[]
            {
                "--repo-url", "https://github.com/coders-farm-official/domovoi",
                "--ref", "eb4a497fe821c08b6b8c39d7603ff7384d696dfc",
                "--internet", "never", "--tier", "auto", "--lan-ip", "192.168.0.240",
                "--media", @"music=C:\Users\Public\Domovoi\Music",
                "--media", @"pictures=C:\Users\Public\Domovoi\Pictures",
                "--media", @"videos=C:\Users\Public\Domovoi\Videos",
                "--media", @"documents=C:\Users\Public\Domovoi\Documents",
                "--media", @"audiobooks=C:\Users\Public\Domovoi\Audiobooks",
                "--health-timeout", "900",
            }, ProvisionArgs.Build(Opts()), "args");
        });
        Run("args: no LAN IP leaves the repo's auto", delegate
        {
            var o = Opts(); o.LanIp = null;
            True(!ProvisionArgs.Build(o).Contains("--lan-ip"), "no --lan-ip");
        });
        Run("args: refused inputs", delegate
        {
            var o = Opts(); o.Ref = "main";
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "a branch");
            o = Opts(); o.Ref = "eb4a497";
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "a short SHA");
            o = Opts(); o.RepoUrl = "http://github.com/x/y";
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "http");
            o = Opts(); o.Internet = "yes";
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "internet");
            o = Opts(); o.LanIp = "8.8.8.8";
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "public LAN IP");
            o = Opts(); o.Media.Add(new KeyValuePair<string, string>("music", @"D:\Music"));
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "music twice");
            o = Opts(); o.Media.Add(new KeyValuePair<string, string>("podcasts", @"D:\Podcasts"));
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "unknown name");
            o = Opts(); o.Media = new List<KeyValuePair<string, string>> { new KeyValuePair<string, string>("music", @"\\nas\music") };
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "network folder");
        });
    }

    static void Media()
    {
        Run("media: folder rules", delegate
        {
            True(MediaPaths.Problem(@"C:\Users\Public\Domovoi") == null, "default root");
            True(MediaPaths.Problem(@"D:\Family Media\Domovoi\") == null, "spaces and a trailing backslash");
            True(MediaPaths.Problem("C:/Users/Public/Domovoi") == null, "forward slashes");
            True(MediaPaths.Problem(@"C:\") != null, "a whole drive");
            True(MediaPaths.Problem(@"\\nas\share") != null, "network");
            True(MediaPaths.Problem(@"Music") != null, "relative");
            True(MediaPaths.Problem(@"C:\a\..\b") != null, "..");
            True(MediaPaths.Problem(@"C:\a\.\b") != null, ".");
            True(MediaPaths.Problem(@"C:\a\\b") != null, "empty segment");
            True(MediaPaths.Problem("C:\\a\"b") != null, "quote");
            True(MediaPaths.Problem(@"C:\a|b") != null, "pipe");
            True(MediaPaths.Problem(@"C:\a\b:c") != null, "a second colon");
            True(MediaPaths.Problem(@"C:\a\b ") == null, "trailing space is trimmed");
            True(MediaPaths.Problem(@"C:\a\b.\c") != null, "a name ending in a dot");
            True(MediaPaths.Problem("C:\\a\tb") != null, "control character");
            True(MediaPaths.Problem("") != null && MediaPaths.Problem(null) != null, "empty");
        });
        Run("media: the five folders under a root", delegate
        {
            var u = MediaPaths.Under(@"E:\Domovoi\");
            SeqEq(new[] { "music", "pictures", "videos", "documents", "audiobooks" }, u.Select(x => x.Key), "names");
            Eq(@"E:\Domovoi\Audiobooks", u[4].Value, "path");
        });
    }

    static void LanRules()
    {
        Run("lan: private addresses", delegate
        {
            foreach (string s in new[] { "10.0.0.1", "10.255.255.255", "172.16.0.1", "172.31.255.254", "192.168.0.240" })
                True(Lan.IsPrivateIPv4(s), s);
            foreach (string s in new[] { "172.15.0.1", "172.32.0.1", "100.64.0.1", "100.101.102.103", "169.254.1.1", "127.0.0.1", "8.8.8.8", "192.169.0.1", "1.2", "::1", "", null, "192.168.0.256" })
                True(!Lan.IsPrivateIPv4(s), "not private: " + s);
        });
        Run("lan: subnets", delegate
        {
            Eq("192.168.0.0/24", Lan.Subnet("192.168.0.240", 24), "/24");
            Eq("10.0.0.0/8", Lan.Subnet("10.1.2.3", 8), "/8");
            Eq("192.168.0.240/32", Lan.Subnet("192.168.0.240", 32), "/32");
            Eq("172.16.0.0/12", Lan.Subnet("172.20.5.6", 12), "/12");
            Throws<ArgumentException>(delegate { Lan.Subnet("192.168.0.1", 7); }, "/7");
            Throws<ArgumentException>(delegate { Lan.Subnet("192.168.0.1", 0); }, "/0");
            Eq(24, Lan.PrefixFromMask("255.255.255.0"), "mask 24");
            Eq(20, Lan.PrefixFromMask("255.255.240.0"), "mask 20");
            Throws<ArgumentException>(delegate { Lan.PrefixFromMask("255.0.255.0"); }, "holes");
            True(Lan.IsCidr("192.168.0.0/24"), "cidr");
            True(!Lan.IsCidr("192.168.0.5/24"), "host bits set");
            True(!Lan.IsCidr("192.168.0.0"), "no prefix");
            True(!Lan.IsCidr("0.0.0.0/0"), "everything");
        });
    }

    // This PC's own `netsh int ipv4 show excludedportrange protocol=tcp`, 2026-10-11.
    const string NetshSample = "\r\nProtocol tcp Port Exclusion Ranges\r\n\r\nStart Port    End Port      \r\n----------    --------      \r\n      5357        5357      \r\n     27339       27339      \r\n     50000       50059     *\r\n     61445       61544      \r\n\r\n* - Administered port exclusions.\r\n\r\n";

    static void PortOwners()
    {
        Run("ports: excluded ranges from netsh", delegate
        {
            var r = PortCheck.ParseExcludedRanges(NetshSample);
            Eq(4, r.Count, "four ranges");
            Eq(50000, r[2].First, "administered range first"); Eq(50059, r[2].Last, "last");
            Eq(0, PortCheck.ParseExcludedRanges("").Count, "empty");
            Eq(0, PortCheck.ParseExcludedRanges(null).Count, "null");
        });
        Run("ports: the dev box today, Docker Desktop on 6432", delegate
        {
            var o = PortCheck.Owners(new[] { new Listener(6432, 35452, "com.docker.backend") }, PortCheck.ParseExcludedRanges(NetshSample));
            Eq(1, o.Count, "one owner");
            Eq("docker", o[0].Kind, "kind"); Eq("Docker Desktop", o[0].Name, "name");
            SeqEq(new[] { "6432" }, o[0].Ports.Select(p => p.ToString()), "ports");
            True(o[0].Title.Contains("6432") && o[0].Advice.Contains("Containers"), "advice: " + o[0].Advice);
        });
        Run("ports: one pop-up per owner, ports grouped", delegate
        {
            var o = PortCheck.Owners(new[]
            {
                new Listener(8051, 10, "wslrelay"), new Listener(6432, 11, "com.docker.backend"), new Listener(8050, 10, "wslrelay"),
                new Listener(11434, 20, "ollama"), new Listener(6369, 30, "nginx"), new Listener(6369, 30, "nginx"),
                new Listener(5432, 11, "com.docker.backend"), new Listener(11435, 20, "ollama app"),
                new Listener(6370, 40, null), new Listener(9467, 4, "System"),
            }, null);
            SeqEq(new[] { "Docker Desktop", "Ollama", "nginx", "A program that has exited or can't be read (PID 40)", "Windows (the System process)" }, o.Select(x => x.Name), "owners in order seen");
            Eq("6432, 8050-8051", PortCheck.Format(o[0].Ports), "docker ports");
            SeqEq(new[] { "11434" }, o[1].Ports.Select(p => p.ToString()), "ollama only on its Domovoi port");
            Eq(1, o[2].Ports.Count, "IPv4 and IPv6 listeners count once");
        });
        Run("ports: a household Docker or Ollama on other ports gets no warning", delegate
        {
            var o = PortCheck.Owners(new[] { new Listener(5432, 1, "com.docker.backend"), new Listener(11500, 2, "ollama"), new Listener(80, 3, "httpd") }, null);
            Eq(0, o.Count, "no owners");
        });
        Run("ports: reserved ranges that overlap Domovoi's", delegate
        {
            var o = PortCheck.Owners(new[] { new Listener(8095, 1, "foo") }, new[] { new PortRange(8090, 8110), new PortRange(50000, 50059) });
            Eq(2, o.Count, "foo and the reserved range");
            Eq("8090-8094, 8096-8099", PortCheck.Format(o[1].Ports), "reserved ports, without the one foo holds");
            True(o[1].Advice.Contains("Restarting Windows"), "advice");
        });
        Run("ports: format", delegate
        {
            Eq("6432", PortCheck.Format(new List<int> { 6432 }), "one");
            Eq("6369-6370, 6432, 8050-8099", PortCheck.Format(Enumerable.Range(8050, 50).Concat(new[] { 6432, 6370, 6369 }).ToList()), "ranges");
        });
        Run("ports: the table matches AGENTS.md and PLAN 4.1", delegate
        {
            foreach (int p in new[] { 6369, 6370, 6432, 7676, 8050, 8099, 8096, 9467, 11434, 6650 }) True(DomovoiPorts.Needed(p), "needed " + p);
            foreach (int p in new[] { 8000, 8001, 8765, 5432, 8100, 11435 }) True(!DomovoiPorts.Needed(p), "not needed " + p);
        });
    }

    static void WslConfigText()
    {
        Run("wslconfig: memory cap by installed RAM", delegate
        {
            long gib = 1024L * 1024 * 1024;
            Eq(10, WslConfig.MemoryCapGb(16 * gib - gib / 3), "a 16 GB PC");
            Eq(10, WslConfig.MemoryCapGb(23 * gib), "23 GB");
            Eq(16, WslConfig.MemoryCapGb(24 * gib - gib / 4), "24 GB");
            Eq(24, WslConfig.MemoryCapGb(32 * gib), "32 GB");
            Eq(56, WslConfig.MemoryCapGb(64 * gib), "64 GB");
            Eq(64, WslConfig.MemoryCapGb(137242800128L), "this dev box, 128 GB");
        });
        Run("wslconfig: the file", delegate
        {
            var o = new WslConfigOptions();
            o.MemoryGb = 10;
            o.SwapFile = @"C:\ProgramData\Domovoi\wsl\swap.vhdx";
            string t = WslConfig.Render(o);
            string[] want =
            {
                "[general]", "instanceIdleTimeout=-1", "[wsl2]", "networkingMode=mirrored", "guiApplications=false",
                "memory=10GB", "swap=4GB", @"swapFile=C:\\ProgramData\\Domovoi\\wsl\\swap.vhdx", "firewall=true",
                "[experimental]", "hostAddressLoopback=true", "sparseVhd=true", "autoMemoryReclaim=dropCache",
            };
            var lines = t.Split('\n').Where(l => l.Length > 0 && !l.StartsWith("#")).ToList();
            SeqEq(want, lines, "lines in order");
            True(!t.Contains("vmIdleTimeout"), "vmIdleTimeout stays at WSL's default");
            True(!t.Contains("\r"), "LF only");
        });
        Run("wslconfig: refused values", delegate
        {
            var o = new WslConfigOptions(); o.MemoryGb = 10; o.SwapFile = "C:\\x\nmemory=1GB";
            Throws<ArgumentException>(delegate { WslConfig.Render(o); }, "a newline in a path");
            o.SwapFile = @"C:\x"; o.MemoryGb = 0;
            Throws<ArgumentException>(delegate { WslConfig.Render(o); }, "no memory");
        });
    }

    static ParseError[] PsErrors(string script)
    {
        Token[] tokens;
        ParseError[] errors;
        Parser.ParseInput(script, out tokens, out errors);
        return errors;
    }

    static void FirewallRules()
    {
        Run("firewall: rules for the LAN ports, scoped to the subnet", delegate
        {
            var rules = Firewall.Plan("192.168.0.0/24");
            SeqEq(new[] { "Domovoi-Dashboard-6369", "Domovoi-Core-6370", "Domovoi-MPD-8050-8099", "Domovoi-Jellyfin-8096", "Domovoi-RomM-7676", "Domovoi-Kiwix-9467" }, rules.Select(r => r.Name), "names");
            SeqEq(new[] { "6369", "6370", "8050-8099", "8096", "7676", "9467" }, rules.Select(r => r.LocalPorts), "ports");
            True(rules.All(r => r.RemoteAddress == "192.168.0.0/24"), "subnet");
            True(!rules.Any(r => r.LocalPorts == "6432" || r.LocalPorts == "11434"), "no loopback-only port opened");
        });
        Run("firewall: never a public or malformed scope", delegate
        {
            Throws<ArgumentException>(delegate { Firewall.Plan("8.8.8.0/24"); }, "public");
            Throws<ArgumentException>(delegate { Firewall.Plan("192.168.0.5/24"); }, "host address");
            Throws<ArgumentException>(delegate { Firewall.Plan("0.0.0.0/0"); }, "everything");
            Throws<ArgumentException>(delegate { Firewall.Plan("100.64.0.0/10"); }, "CGNAT");
        });
        Run("firewall: the add and remove scripts", delegate
        {
            string add = Firewall.AddScript(Firewall.Plan("10.0.0.0/16"));
            Eq(6, add.Split('\n').Count(l => l.StartsWith("New-NetFirewallHyperVRule ")), "six New- lines");
            True(add.Contains("-VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'"), "WSL's creator id");
            True(add.Contains("-Direction Inbound") && add.Contains("-Action Allow") && add.Contains("-RemoteAddresses '10.0.0.0/16'"), "inbound allow from the subnet");
            True(add.Contains("Remove-NetFirewallHyperVRule -Name $r.Name"), "removes old Domovoi rules first");
            True(!add.Contains("DefaultInboundAction"), "never touches the default action");
            Eq(0, PsErrors(add).Length, "add parses: " + string.Join("; ", PsErrors(add).Select(e => e.Message).ToArray()));
            Eq(0, PsErrors(Firewall.RemoveScript()).Length, "remove parses");
            Eq(0, PsErrors(Firewall.ListScript()).Length, "list parses");
        });
        Run("firewall: a quote in a display name can't break out", delegate
        {
            var r = Firewall.Plan("192.168.0.0/24");
            r[0].DisplayName = "x'; Remove-Item C:\\ -Recurse; '";
            string s = Firewall.AddScript(r);
            True(s.Contains("'x''; Remove-Item C:\\ -Recurse; '''"), "doubled quote");
            Eq(0, PsErrors(s).Length, "still parses");
            r[0].Name = "Evil";
            Throws<ArgumentException>(delegate { Firewall.AddScript(r); }, "unexpected rule name");
        });
    }

    [DllImport("shell32.dll", SetLastError = true)]
    static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string cmdLine, out int numArgs);

    [DllImport("kernel32.dll")]
    static extern IntPtr LocalFree(IntPtr mem);

    static List<string> Argv(string cmdLine)
    {
        int n;
        IntPtr p = CommandLineToArgvW(cmdLine, out n);
        var list = new List<string>();
        try
        {
            for (int i = 0; i < n; i++) list.Add(Marshal.PtrToStringUni(Marshal.ReadIntPtr(p, i * IntPtr.Size)));
        }
        finally { LocalFree(p); }
        return list;
    }

    static void Quoting()
    {
        Run("quoting: what Windows reads back", delegate
        {
            string[] args =
            {
                "plain", "", "two words", @"C:\Program Files\WSL\", @"music=C:\Users\Public\Domovoi\Music",
                "say \"hi\"", @"a\""b", @"ends\\", @"\\server\share\", "tab\there", "--media", @"music=D:\My Music\",
            };
            var back = Argv("x.exe " + CommandLine.Join(args));
            back.RemoveAt(0);
            SeqEq(args, back, "round trip through CommandLineToArgvW");
            Eq("plain", CommandLine.Quote("plain"), "no quotes when none are needed");
            Eq("\"\"", CommandLine.Quote(""), "empty");
        });
    }

    static void SetupCodes()
    {
        Run("setup code: shape", delegate
        {
            True(SetupCode.IsWellFormed("amber-anchor-aspen-aster-bank-barn-bay-bell"), "eight words");
            True(!SetupCode.IsWellFormed("amber-anchor-aspen-aster-bank-barn-bay"), "seven");
            True(!SetupCode.IsWellFormed("Amber-anchor-aspen-aster-bank-barn-bay-bell"), "upper case");
            True(!SetupCode.IsWellFormed("amber-anchor-aspen-aster-bank-barn-bay-bell\n"), "newline");
            True(!SetupCode.IsWellFormed(null), "null");
        });
        Run("setup code: scrubbed from log lines", delegate
        {
            Eq("Setup code: [hidden] (24 h)", SetupCode.Scrub("Setup code: amber-anchor-aspen-aster-bank-barn-bay-bell (24 h)"), "hidden");
            string keep = "Unpacking python3-venv (3.14.0-1) ... libchromaprint-tools espeak-ng";
            Eq(keep, SetupCode.Scrub(keep), "ordinary hyphens stay");
            Eq(RealFinal, SetupCode.Scrub(RealFinal), "a progress line stays whole");
        });
        Run("setup code: the trip through the host", delegate
        {
            using (var key = new SetupCodeKey())
            {
                string pub = key.PublicKey;
                True(!pub.Contains("amber"), "public key is not the code");
                string box = SetupCodeKey.Encrypt(pub, "amber-anchor-aspen-aster-bank-barn-bay-bell");
                True(!box.Contains("amber"), "ciphertext");
                Eq("amber-anchor-aspen-aster-bank-barn-bay-bell", key.Decrypt(box), "round trip");
                using (var other = new SetupCodeKey())
                    Throws<System.Security.Cryptography.CryptographicException>(delegate { other.Decrypt(box); }, "another key can't read it");
            }
            Throws<FormatException>(delegate { SetupCodeKey.Encrypt("nope", "x"); }, "a bad public key");
        });
    }

    static void Protocol()
    {
        Run("protocol: job.json round trip", delegate
        {
            var j = new HostJob();
            j.Generation = 2; j.Attempt = 3;
            j.Image = @"C:\ProgramData\Domovoi\wsl\ubuntu.wsl"; j.ImageSha256 = new string('a', 64);
            j.Provision = @"C:\ProgramData\Domovoi\wsl\provision.sh"; j.ProvisionSha256 = new string('b', 64);
            j.ProvisionArgs = ProvisionArgs.Build(Opts());
            j.WslConfig = "[wsl2]\nmemory=10GB\n";
            var back = HostJob.FromJson(Json.ParseObject(Json.Serialize(j.ToJson())));
            Eq(2, back.Generation, "generation"); Eq(3, back.Attempt, "attempt");
            SeqEq(j.ProvisionArgs, back.ProvisionArgs, "args");
            Eq(j.WslConfig, back.WslConfig, "wslconfig");
            var bad = j.ToJson(); bad["image_sha256"] = "x";
            Throws<FormatException>(delegate { HostJob.FromJson(Json.ParseObject(Json.Serialize(bad))); }, "pins required");
        });
        Run("protocol: status.json round trip", delegate
        {
            var s = new HostStatus();
            s.State = "provisioning"; s.Percent = 42; s.Message = "Installing \"Python\" packages"; s.Bytes = 5000000000L;
            s.Gpu = new List<string>(); s.GpuKnown = true;
            var back = HostStatus.FromJson(Json.ParseObject(Json.Serialize(s.ToJson())));
            Eq("provisioning", back.State, "state"); Eq(42, back.Percent, "percent");
            Eq(s.Message, back.Message, "message"); Eq(5000000000L, back.Bytes.Value, "bytes");
            True(back.GpuKnown && back.Gpu.Count == 0, "empty gpu list survives");
            s.GpuKnown = false;
            True(!HostStatus.FromJson(Json.ParseObject(Json.Serialize(s.ToJson()))).GpuKnown, "unknown gpu survives");
        });
        Run("protocol: install.json round trip", delegate
        {
            var l = FullLedger();
            var back = Ledger.FromJson(Json.ParseObject(Json.Serialize(l.ToJson())));
            Eq(l.AccountSid, back.AccountSid, "sid");
            Eq(6, back.Media.Count, "media records");
            True(back.Media[0].Created && back.Media[0].AddedSids.Count == 2, "media record fields");
            SeqEq(l.FirewallRules, back.FirewallRules, "rules");
        });
        Run("protocol: sha256 hex", delegate
        {
            True(Sha256.IsHex(new string('0', 64)), "zeros");
            True(!Sha256.IsHex(new string('A', 64)), "upper case");
            True(!Sha256.IsHex(new string('0', 63)), "short");
        });
    }

    const string Sid = "S-1-5-21-1-2-3-1001";
    const string Users = "S-1-5-32-545";

    static Ledger FullLedger()
    {
        var l = new Ledger();
        l.Internet = "never"; l.MediaRoot = @"C:\Users\Public\Domovoi";
        l.AccountSid = Sid; l.AccountCreated = true; l.ServiceCreated = true; l.ProgramDataCreated = true;
        l.UserListKeyCreated = true; l.SpecialAccountsKeyCreated = true;
        l.Rights = new List<string> { "SeServiceLogonRight" };
        l.FirewallRules = Firewall.Plan("192.168.0.0/24").Select(r => r.Name).ToList();
        var root = new MediaFolderRecord(); root.Path = l.MediaRoot; root.Created = true; root.AddedSids = new List<string> { Sid, Users };
        l.Media.Add(root);
        foreach (var m in MediaPaths.Under(l.MediaRoot))
        {
            var r = new MediaFolderRecord(); r.Path = m.Value; r.Created = true; r.AddedSids = new List<string> { Sid };
            l.Media.Add(r);
        }
        return l;
    }

    static UninstallFacts FullFacts(Ledger l)
    {
        var f = new UninstallFacts();
        f.ServiceExists = true; f.ServiceRunning = true; f.ServiceIsDomovoi = true;
        f.AccountExists = true; f.AccountSid = Sid; f.AccountHasMarker = true; f.ProfileExists = true;
        f.FirewallRules = new List<string>(l.FirewallRules);
        f.Rights = new List<string> { "SeServiceLogonRight", "SeDenyInteractiveLogonRight" };
        f.UserListValuePresent = true; f.ProgramDataExists = true; f.RecordExists = true;
        f.DistroDiskExists = true;
        foreach (var m in l.Media) f.MediaExisting.Add(m.Path);
        return f;
    }

    static readonly UninstallKind[] Stranding =
    {
        UninstallKind.DeleteService, UninstallKind.DeleteProfile, UninstallKind.RemoveRights,
        UninstallKind.UnhideAccount, UninstallKind.DeleteAccount, UninstallKind.DeleteProgramData, UninstallKind.RemoveRecord,
    };

    static void NoMediaDeleted(Ledger l, List<UninstallStep> plan)
    {
        foreach (var s in plan)
        {
            foreach (var m in l.Media)
            {
                if (!string.Equals(s.Target, m.Path, StringComparison.OrdinalIgnoreCase)) continue;
                True(s.Kind == UninstallKind.RemoveMediaAccess || s.Kind == UninstallKind.RemoveFolderIfEmpty, "only safe kinds touch " + m.Path);
                if (s.Kind == UninstallKind.RemoveFolderIfEmpty) True(m.Created, "only a folder Setup made");
            }
            if (s.Kind == UninstallKind.DeleteProgramData) Eq(Paths.ProgramData, s.Target, "the only tree deleted");
        }
    }

    // Every step that could strand the distro comes after every critical step.
    static void CriticalFirst(List<UninstallStep> plan)
    {
        int lastCritical = -1, firstStranding = int.MaxValue;
        for (int i = 0; i < plan.Count; i++)
        {
            if (plan[i].Critical) lastCritical = i;
            if (Stranding.Contains(plan[i].Kind) && i < firstStranding) firstStranding = i;
        }
        True(lastCritical < firstStranding, "a stranding step comes before a critical one");
    }

    static void Uninstall()
    {
        Run("uninstall: a finished install, in order", delegate
        {
            var l = FullLedger();
            var plan = UninstallPlan.Make(l, FullFacts(l));
            var kinds = plan.Select(s => s.Kind.ToString()).ToList();
            SeqEq(new[]
            {
                "StopService", "RotatePassword", "UnregisterDistro", "DeleteService", "StopAccountProcesses", "RemoveFirewallRules",
                "RemoveMediaAccess", "RemoveMediaAccess", "RemoveMediaAccess", "RemoveMediaAccess", "RemoveMediaAccess", "RemoveMediaAccess",
                "RemoveFolderIfEmpty", "RemoveFolderIfEmpty", "RemoveFolderIfEmpty", "RemoveFolderIfEmpty", "RemoveFolderIfEmpty", "RemoveFolderIfEmpty",
                "DeleteProfile", "RemoveRights", "UnhideAccount", "DeleteAccount", "DeleteProgramData", "RemoveRecord",
            }, kinds, "steps");
            SeqEq(new[] { "StopService", "RotatePassword", "UnregisterDistro" }, plan.Where(s => s.Critical).Select(s => s.Kind.ToString()), "critical steps");
            var folders = plan.Where(s => s.Kind == UninstallKind.RemoveFolderIfEmpty).Select(s => s.Target).ToList();
            Eq(@"C:\Users\Public\Domovoi", folders.Last(), "the root goes last, after its folders");
            var unhide = plan.Single(s => s.Kind == UninstallKind.UnhideAccount);
            True(unhide.DeleteUserListKey && unhide.DeleteSpecialAccountsKey, "keys Setup made");
            NoMediaDeleted(l, plan);
            CriticalFirst(plan);
        });
        Run("uninstall: nothing there, no record: nothing to do (a second run)", delegate
        {
            Eq(0, UninstallPlan.Make(null, new UninstallFacts()).Count, "empty plan");
        });
        Run("uninstall: a domovoi-svc nobody marked is left alone", delegate
        {
            var f = new UninstallFacts();
            f.AccountExists = true; f.AccountSid = Sid; f.ProfileExists = true; f.UserListValuePresent = true;
            f.Rights = new List<string> { "SeServiceLogonRight" }; f.DistroDiskExists = false;
            var plan = UninstallPlan.Make(null, f);
            True(!plan.Any(s => s.Kind == UninstallKind.DeleteAccount || s.Kind == UninstallKind.DeleteProfile
                || s.Kind == UninstallKind.RemoveRights || s.Kind == UninstallKind.UnhideAccount || s.Kind == UninstallKind.StopAccountProcesses), "not ours");
        });
        Run("uninstall: half-finished, account made but no service and no distro", delegate
        {
            var l = new Ledger(); l.AccountSid = Sid; l.AccountCreated = true; l.ProgramDataCreated = true;
            var f = new UninstallFacts();
            f.AccountExists = true; f.AccountSid = Sid; f.AccountHasMarker = true; f.ProgramDataExists = true;
            f.Rights = new List<string> { "SeServiceLogonRight" }; f.UserListValuePresent = true;
            f.DistroDiskExists = false; f.RecordExists = true;
            var kinds = UninstallPlan.Make(l, f).Select(s => s.Kind.ToString()).ToList();
            SeqEq(new[] { "StopAccountProcesses", "RemoveRights", "UnhideAccount", "DeleteAccount", "DeleteProgramData", "RemoveRecord" }, kinds, "steps");
        });
        Run("uninstall: the distro's disk but no service: stop before anything (review #2)", delegate
        {
            var l = FullLedger();
            var f = FullFacts(l); f.ServiceExists = false; f.ServiceRunning = false;
            var plan = UninstallPlan.Make(l, f);
            Eq(UninstallKind.Blocked, plan[0].Kind, "blocked first");
            True(plan[0].Critical && plan[0].Reason.Contains("Setup again"), "critical, and says what to do: " + plan[0].Reason);
            CriticalFirst(plan);
        });
        Run("uninstall: can't tell whether the disk is there: treated as there", delegate
        {
            var l = FullLedger();
            var f = FullFacts(l); f.DistroDiskExists = null;
            True(UninstallPlan.Make(l, f).Any(s => s.Kind == UninstallKind.UnregisterDistro), "unregisters");
        });
        Run("uninstall: no distro disk: no unregister needed", delegate
        {
            var l = FullLedger();
            var f = FullFacts(l); f.DistroDiskExists = false;
            var plan = UninstallPlan.Make(l, f);
            True(!plan.Any(s => s.Kind == UninstallKind.UnregisterDistro || s.Kind == UninstallKind.RotatePassword), "no unregister");
            Eq(UninstallKind.StopService, plan[0].Kind, "still stops the service first");
        });
        Run("uninstall: account already gone, leftovers keyed to its SID", delegate
        {
            var l = FullLedger();
            var f = new UninstallFacts();
            f.ProfileExists = true; f.Rights = new List<string> { "SeServiceLogonRight" };
            f.ProgramDataExists = true; f.RunOnceValuePresent = true; f.RecordExists = true; f.DistroDiskExists = true;
            f.LedgerSidHasDomovoiProfile = true;
            var kinds = UninstallPlan.Make(l, f).Select(s => s.Kind.ToString()).ToList();
            SeqEq(new[] { "StopAccountProcesses", "RemoveFirewallRules", "DeleteProfile", "RemoveRights", "UnhideAccount", "DeleteProgramData", "RemoveRunOnce", "RemoveRecord" }, kinds, "steps (the ledger's rule list counts)");
        });
        Run("uninstall: a record SID whose profile isn't domovoi-svc's is never acted on", delegate
        {
            var l = FullLedger();
            var f = new UninstallFacts();
            f.ProfileExists = true; f.Rights = new List<string> { "SeServiceLogonRight" }; f.DistroDiskExists = false;
            f.LedgerSidHasDomovoiProfile = false;
            var kinds = UninstallPlan.Make(l, f).Select(s => s.Kind).ToList();
            True(!kinds.Contains(UninstallKind.DeleteProfile) && !kinds.Contains(UninstallKind.RemoveRights) && !kinds.Contains(UninstallKind.StopAccountProcesses), "left alone");
            True(UninstallFacts.IsDomovoiProfilePath(@"C:\Users\domovoi-svc") && UninstallFacts.IsDomovoiProfilePath(@"C:\Users\domovoi-svc.MY-PC\"), "domovoi-svc folders");
            True(!UninstallFacts.IsDomovoiProfilePath(@"C:\Users\Kamron") && !UninstallFacts.IsDomovoiProfilePath(@"C:\Users\domovoi-svcx") && !UninstallFacts.IsDomovoiProfilePath(null), "others");
        });
        Run("uninstall: ProgramData Setup didn't make is never deleted (review #4)", delegate
        {
            var l = FullLedger(); l.ProgramDataCreated = false;
            True(!UninstallPlan.Make(l, FullFacts(l)).Any(s => s.Kind == UninstallKind.DeleteProgramData), "kept");
            var f = new UninstallFacts(); f.ProgramDataExists = true;
            True(!UninstallPlan.Make(null, f).Any(s => s.Kind == UninstallKind.DeleteProgramData), "no record: kept");
        });
        Run("uninstall: unreadable rules or rights still get their step (review #9)", delegate
        {
            var l = new Ledger(); l.AccountSid = Sid; l.AccountCreated = true;
            var f = new UninstallFacts(); f.AccountExists = true; f.AccountSid = Sid; f.AccountHasMarker = true;
            f.DistroDiskExists = false; f.FirewallUnknown = true; f.RightsUnknown = true;
            var kinds = UninstallPlan.Make(l, f).Select(s => s.Kind).ToList();
            True(kinds.Contains(UninstallKind.RemoveFirewallRules) && kinds.Contains(UninstallKind.RemoveRights), "both there");
        });
        Run("uninstall: the household's own media folder keeps everything but Setup's rule", delegate
        {
            var l = new Ledger();
            var mine = new MediaFolderRecord(); mine.Path = @"D:\Music"; mine.Created = false; mine.AddedSids = new List<string> { Sid };
            l.Media.Add(mine);
            var f = new UninstallFacts(); f.MediaExisting.Add(@"d:\music"); f.DistroDiskExists = false;
            var plan = UninstallPlan.Make(l, f);
            Eq(1, plan.Count, "one step");
            Eq(UninstallKind.RemoveMediaAccess, plan[0].Kind, "access rule only");
            NoMediaDeleted(l, plan);
        });
        Run("uninstall: a stopped service is not stopped again; a foreign service is left", delegate
        {
            var l = FullLedger();
            var f = FullFacts(l); f.ServiceRunning = false;
            True(!UninstallPlan.Make(l, f).Any(s => s.Kind == UninstallKind.StopService), "no stop");
            var g = new UninstallFacts(); g.ServiceExists = true; g.ServiceRunning = true; g.DistroDiskExists = false;
            Eq(0, UninstallPlan.Make(null, g).Count, "a DomovoiHost Setup didn't register");
        });
    }

    static int RunPlan(List<UninstallStep> plan, Func<UninstallStep, string> doStep, int run, out List<UninstallLeftover> left, out List<UninstallStep> ran)
    {
        left = new List<UninstallLeftover>(); ran = new List<UninstallStep>();
        return UninstallRunner.Run(plan, doStep, left, ran, run);
    }

    static void Runner()
    {
        List<UninstallLeftover> left; List<UninstallStep> ran;
        Run("runner: everything works: exit 0, record removed", delegate
        {
            var l = FullLedger();
            var plan = UninstallPlan.Make(l, FullFacts(l));
            Eq(0, RunPlan(plan, s => null, 1, out left, out ran), "exit");
            Eq(plan.Count, ran.Count, "all ran");
            Eq(UninstallKind.RemoveRecord, ran.Last().Kind, "record last");
        });
        Run("runner: unregister fails: exit 1, nothing that could strand the distro ran (review #2)", delegate
        {
            var l = FullLedger();
            var plan = UninstallPlan.Make(l, FullFacts(l));
            int code = RunPlan(plan, s => s.Kind == UninstallKind.UnregisterDistro ? "the distro (WSL said no)" : null, 3, out left, out ran);
            Eq(1, code, "exit, even on the third run");
            SeqEq(new[] { "StopService", "RotatePassword", "UnregisterDistro" }, ran.Select(s => s.Kind.ToString()), "ran");
            Eq("the distro (WSL said no)", left.Single().What, "leftover");
        });
        Run("runner: a throwing step counts as not done", delegate
        {
            var l = FullLedger();
            var plan = UninstallPlan.Make(l, FullFacts(l));
            int code = RunPlan(plan, delegate (UninstallStep s) { if (s.Kind == UninstallKind.StopService) throw new InvalidOperationException("stuck"); return null; }, 1, out left, out ran);
            Eq(1, code, "a critical step threw");
            Eq(1, ran.Count, "stopped right there");
        });
        Run("runner: only inert leftovers: finished (exit 4), record gone, listed with how to remove them", delegate
        {
            var l = FullLedger();
            var plan = UninstallPlan.Make(l, FullFacts(l));
            int code = RunPlan(plan, s => s.Kind == UninstallKind.DeleteProfile || s.Kind == UninstallKind.DeleteProgramData ? "in use" : null, 1, out left, out ran);
            Eq(4, code, "exit");
            True(ran.Any(s => s.Kind == UninstallKind.RemoveRecord), "the record goes");
            Eq(2, left.Count, "two leftovers");
            True(left.All(x => x.HowToRemove().Length > 20), "each says how to remove it");
        });
        Run("runner: a leftover that may clear keeps the uninstaller until the third run", delegate
        {
            var l = FullLedger();
            var plan = UninstallPlan.Make(l, FullFacts(l));
            Func<UninstallStep, string> rules = s => s.Kind == UninstallKind.RemoveFirewallRules ? "firewall rules Domovoi-Core-6370" : null;
            Eq(3, RunPlan(plan, rules, 1, out left, out ran), "first run");
            True(!ran.Any(s => s.Kind == UninstallKind.RemoveRecord), "the record stays");
            Eq(3, RunPlan(plan, rules, 2, out left, out ran), "second run");
            Eq(4, RunPlan(plan, rules, UninstallRunner.MaxRuns, out left, out ran), "third run finishes");
            True(left.Single().HowToRemove().Contains("Remove-NetFirewallHyperVRule"), "how to remove it: " + left.Single().HowToRemove());
        });
        Run("runner: blocked plans stop at once", delegate
        {
            var l = FullLedger();
            var f = FullFacts(l); f.ServiceExists = false; f.ServiceRunning = false;
            Eq(1, RunPlan(UninstallPlan.Make(l, f), s => null, 3, out left, out ran), "exit");
            Eq(1, ran.Count, "only the block");
        });
        Run("runner: which leftovers count as inert", delegate
        {
            foreach (var k in new[] { UninstallKind.DeleteService, UninstallKind.DeleteProgramData, UninstallKind.DeleteProfile, UninstallKind.RemoveMediaAccess, UninstallKind.RemoveFolderIfEmpty, UninstallKind.UnhideAccount, UninstallKind.RemoveRunOnce })
                True(UninstallRunner.IsInert(k), "inert: " + k);
            foreach (var k in new[] { UninstallKind.DeleteAccount, UninstallKind.RemoveRights, UninstallKind.RemoveFirewallRules, UninstallKind.StopAccountProcesses, UninstallKind.UnregisterDistro, UninstallKind.StopService })
                True(!UninstallRunner.IsInert(k), "not inert: " + k);
        });
    }
    static void WslListing()
    {
        Run("wsl --list: names, none, or unknown (review #3)", delegate
        {
            SeqEq(new[] { "domovoi", "Ubuntu" }, WslList.Parse(0, false, "domovoi\r\nUbuntu\r\n"), "names");
            Eq(true, WslList.Contains(WslList.Parse(0, false, "domovoi\n"), "domovoi").Value, "contains");
            Eq(0, WslList.Parse(-1, false, "Windows Subsystem for Linux has no installed distributions.\nYou can resolve this by installing a distribution with the instructions below:\n\nError code: Wsl/WSL_E_DEFAULT_DISTRO_NOT_FOUND\n").Count, "English, none");
            Eq(0, WslList.Parse(-1, false, "Windows-Subsystem fuer Linux hat keine installierten Distributionen.\nFehlercode: Wsl/WSL_E_DEFAULT_DISTRO_NOT_FOUND\n").Count, "another language, by the error code");
            True(WslList.Parse(-1, false, "The Windows Subsystem for Linux is being updated.\nError code: Wsl/Service/E_ACCESSDENIED\n") == null, "an update in progress is unknown");
            True(WslList.Parse(1, false, "") == null, "a failure with no text is unknown");
            True(WslList.Parse(0, true, "domovoi\n") == null, "a timeout is unknown");
            True(WslList.Contains(null, "domovoi") == null, "unknown stays unknown");
            Eq(false, WslList.Contains(WslList.Parse(-1, false, "Error code: Wsl/WSL_E_DEFAULT_DISTRO_NOT_FOUND"), "domovoi").Value, "none means not registered");
            SeqEq(new[] { "docker-desktop" }, WslList.Parse(0, false, "d\0o\0c\0k\0e\0r\0-\0d\0e\0s\0k\0t\0o\0p\0\n"), "UTF-16 leftovers");
        });
    }

    static void MediaRoots()
    {
        Run("media root: system folders and profile roots refused (review #5)", delegate
        {
            var sys = SystemFolders.Typical();
            foreach (string bad in new[]
            {
                @"C:\Windows", @"C:\Windows\Temp\x", @"C:\Program Files", @"C:\Program Files\Domovoi", @"C:\Program Files (x86)\Foo",
                @"C:\ProgramData", @"C:\ProgramData\Domovoi\x", @"C:\Users", @"C:\Users\Kamron", @"C:\Users\Public",
                @"C:\Users\Kamron\AppData\Roaming\Microsoft\Windows\Start Menu", @"C:\Users\Default\Music", @"C:\Users\domovoi-svc\x",
                @"C:\Recovery\x",
            })
                True(MediaPaths.SystemProblem(bad, sys) != null, "refused: " + bad);
            foreach (string ok in new[] { @"C:\Users\Public\Domovoi", @"C:\Users\Kamron\Music", @"D:\Media", @"C:\Media", @"C:\Users\Public\Music\Domovoi", @"C:\Windows2" })
                True(MediaPaths.SystemProblem(ok, sys) == null, "allowed: " + ok + " (" + MediaPaths.SystemProblem(ok, sys) + ")");
        });
        Run("media root: this PC's own system folders are found", delegate
        {
            var here = Domovoi.Windows.Helper.Folders.SystemFoldersHere();
            string win = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            True(MediaPaths.SystemProblem(win, here) != null, "Windows");
            True(MediaPaths.SystemProblem(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) + @"\Domovoi", here) != null, "Program Files");
            True(MediaPaths.SystemProblem(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), here) != null, "my profile");
            True(MediaPaths.SystemProblem(MediaPaths.DefaultRoot, here) == null, "the default is fine");
        });
    }

    // Runs one of Firewall's scripts in PowerShell against stand-in cmdlets:
    // a Domovoi rule, WSL's own rules, a household rule and a Domovoi-named
    // rule for another VM creator. Returns what it removed.
    static List<string> RunAgainstFakeRules(string script)
    {
        const string fakes =
            "$script:removed = New-Object System.Collections.ArrayList\n" +
            "function Get-NetFirewallHyperVRule { param($ErrorAction) @(" +
            "[pscustomobject]@{ Name='Domovoi-Core-6370'; VMCreatorId='{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' }, " +
            "[pscustomobject]@{ Name='Domovoi-MPD-8050-8099'; VMCreatorId='{40e0ac32-46a5-438a-a0b2-2b479e8f2e90}' }, " +
            "[pscustomobject]@{ Name='WslCore-Allow-Inbound-ICMPv4-1-40e0ac32-46a5-438a-a0b2-2b479e8f2e90'; VMCreatorId='{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' }, " +
            "[pscustomobject]@{ Name='Household-Game-Server'; VMCreatorId='{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' }, " +
            "[pscustomobject]@{ Name='Domovoi-Other-VM'; VMCreatorId='{11111111-2222-3333-4444-555555555555}' }) }\n" +
            "function Remove-NetFirewallHyperVRule { param($Name) [void]$script:removed.Add($Name) }\n";
        using (var ps = PowerShell.Create())
        {
            ps.AddScript(fakes + script + "\n$script:removed");
            var outp = ps.Invoke();
            if (ps.HadErrors) throw new InvalidOperationException("script errors: " + string.Join("; ", ps.Streams.Error.Select(e => e.ToString()).ToArray()));
            return outp.Where(x => x != null && !x.ToString().StartsWith("removed ")).Select(x => x.ToString()).ToList();
        }
    }

    static void FirewallFilter()
    {
        Run("firewall cleanup removes only Domovoi's WSL rules (review #11, mutant M1)", delegate
        {
            var removed = RunAgainstFakeRules(Firewall.RemoveScript());
            SeqEq(new[] { "Domovoi-Core-6370", "Domovoi-MPD-8050-8099" }, removed, "removed");
        });
        Run("firewall: a cleanup filter wider than Domovoi-* fails this test", delegate
        {
            // The same script with the filter widened, as mutant M1 did:
            // the check above must catch it.
            string wide = Firewall.RemoveScript().Replace("'" + Firewall.RulePrefix + "*'", "'*'");
            True(wide != Firewall.RemoveScript(), "the mutant changed the script");
            var removed = RunAgainstFakeRules(wide);
            True(removed.Contains("Household-Game-Server") && removed.Count > 2, "a wider filter would remove more");
        });
        Run("firewall: the add script clears only Domovoi's rules before adding", delegate
        {
            string add = Firewall.AddScript(Firewall.Plan("192.168.0.0/24"));
            string prelude = add.Substring(0, add.IndexOf("New-NetFirewallHyperVRule", StringComparison.Ordinal));
            SeqEq(new[] { "Domovoi-Core-6370", "Domovoi-MPD-8050-8099" }, RunAgainstFakeRules(prelude), "removed");
        });
    }

    // Throwaway folders and junctions under %TEMP% (mklink /J needs no
    // administrator), deleted at the end.
    static string Scratch()
    {
        string d = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "domovoi-tests-" + Guid.NewGuid().ToString("N"));
        System.IO.Directory.CreateDirectory(d);
        return d;
    }

    static void Junction(string link, string target)
    {
        var psi = new System.Diagnostics.ProcessStartInfo(System.IO.Path.Combine(Environment.SystemDirectory, "cmd.exe"),
            "/c mklink /J " + CommandLine.Quote(link) + " " + CommandLine.Quote(target));
        psi.UseShellExecute = false; psi.CreateNoWindow = true; psi.RedirectStandardOutput = true;
        using (var p = System.Diagnostics.Process.Start(psi)) { p.StandardOutput.ReadToEnd(); p.WaitForExit(); if (p.ExitCode != 0) throw new InvalidOperationException("mklink failed"); }
    }

    static void Links()
    {
        Run("links: DeleteTree removes a junction, never what it points at (review probe T1)", delegate
        {
            string root = Scratch(), victim = Scratch();
            try
            {
                System.IO.File.WriteAllText(System.IO.Path.Combine(victim, "keep.txt"), "x");
                System.IO.Directory.CreateDirectory(System.IO.Path.Combine(root, "logs"));
                Junction(System.IO.Path.Combine(root, "logs", "evil"), victim);
                Domovoi.Windows.Helper.Folders.DeleteTree(root);
                True(!System.IO.Directory.Exists(root), "tree gone");
                True(System.IO.File.Exists(System.IO.Path.Combine(victim, "keep.txt")), "target untouched");
            }
            finally { if (System.IO.Directory.Exists(victim)) System.IO.Directory.Delete(victim, true); }
        });
        Run("links: a media folder that is a junction is refused, its target untouched (review #10, probe T2)", delegate
        {
            string root = Scratch(), victim = Scratch();
            try
            {
                string link = System.IO.Path.Combine(root, "Music");
                Junction(link, victim);
                // The target carries a rule for the SID RemoveAccess is asked
                // to purge: following the link would take it off.
                var vs = System.IO.Directory.GetAccessControl(victim);
                vs.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(new System.Security.Principal.SecurityIdentifier(Users),
                    System.Security.AccessControl.FileSystemRights.ReadData, System.Security.AccessControl.AccessControlType.Allow));
                System.IO.Directory.SetAccessControl(victim, vs);
                string before = System.IO.Directory.GetAccessControl(victim).GetSecurityDescriptorSddlForm(System.Security.AccessControl.AccessControlSections.Access);
                var l = new Ledger();
                Throws<InvalidOperationException>(delegate
                {
                    Domovoi.Windows.Helper.Folders.EnsureMediaFolder(link, new System.Security.Principal.SecurityIdentifier(Sid), false, l, delegate { }, delegate (string m) { });
                }, "refused");
                string after = System.IO.Directory.GetAccessControl(victim).GetSecurityDescriptorSddlForm(System.Security.AccessControl.AccessControlSections.Access);
                Eq(before, after, "target's rules unchanged");
                True(!l.Media.Any(m => m.Created), "not recorded as made");
                Domovoi.Windows.Helper.Folders.RemoveAccess(link, new[] { Sid, Users }, delegate (string m) { });
                Eq(before, System.IO.Directory.GetAccessControl(victim).GetSecurityDescriptorSddlForm(System.Security.AccessControl.AccessControlSections.Access), "RemoveAccess skips the link (probe T3a)");
                True(!Domovoi.Windows.Helper.Folders.RemoveIfEmpty(link, delegate (string m) { }), "RemoveIfEmpty keeps a link");
            }
            finally
            {
                Domovoi.Windows.Helper.Folders.DeleteTree(root);
                if (System.IO.Directory.Exists(victim)) System.IO.Directory.Delete(victim, true);
            }
        });
        Run("links: RemoveIfEmpty keeps a folder with anything in it", delegate
        {
            string root = Scratch();
            try
            {
                string full = System.IO.Path.Combine(root, "Music"), empty = System.IO.Path.Combine(root, "Videos");
                System.IO.Directory.CreateDirectory(full); System.IO.Directory.CreateDirectory(empty);
                System.IO.File.WriteAllText(System.IO.Path.Combine(full, "desktop.ini"), "");
                True(!Domovoi.Windows.Helper.Folders.RemoveIfEmpty(full, delegate (string m) { }), "kept");
                True(System.IO.File.Exists(System.IO.Path.Combine(full, "desktop.ini")), "file kept");
                True(Domovoi.Windows.Helper.Folders.RemoveIfEmpty(empty, delegate (string m) { }), "empty one removed");
            }
            finally { Domovoi.Windows.Helper.Folders.DeleteTree(root); }
        });
    }
}