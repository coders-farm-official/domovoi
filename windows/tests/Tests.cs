// Unit tests for windows/common, compiled with the same in-box C# compiler
// as the exes (no test framework to install), together with the helper's
// and the host's own sources (so they are built and some of the helper's
// file handling is exercised on throwaway folders under %TEMP%). Nothing
// here changes the PC: no account, service, registry or firewall call is
// made. The tray's tests are in TrayTests.cs. build.ps1 builds and runs it; domovoi/tests/test_windows_installer.py
// runs build.ps1 on Windows. Exit code 0 when every test passes.

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Management.Automation;
using System.Management.Automation.Language;
using System.Runtime.InteropServices;
using System.ServiceProcess;
using Domovoi.Windows;

static partial class Tests
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
        AiChoice();
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
        ServiceWaits();
        PortGateTests();
        ProfileCleanup();
        LogCopies();
        CoreLogCopies();
        UninstallTexts();
        LeftoverProfileTests();
        TrayTests();
        SatelliteSetupTests();
        MediaFolderTests();
        UpdateTests();
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
        o.LanIp = "192.168.1.30";
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
                "--track-branch", "stable",
                "--internet", "never", "--tier", "auto", "--lan-ip", "192.168.1.30",
                "--media", @"music=C:\Users\Public\Domovoi\Music",
                "--media", @"pictures=C:\Users\Public\Domovoi\Pictures",
                "--media", @"videos=C:\Users\Public\Domovoi\Videos",
                "--media", @"documents=C:\Users\Public\Domovoi\Documents",
                "--media", @"audiobooks=C:\Users\Public\Domovoi\Audiobooks",
                "--health-timeout", "900",
            }, ProvisionArgs.Build(Opts()), "args");
        });
        Run("args: the main channel, where pins.iss says so", delegate
        {
            var o = Opts(); o.TrackBranch = "main";
            var a = ProvisionArgs.Build(o);
            Eq("main", a[a.IndexOf("--track-branch") + 1], "track branch");
        });
        Run("installed head: the --ref of the job", delegate
        {
            Eq("eb4a497fe821c08b6b8c39d7603ff7384d696dfc", InstalledHead.RefOf(ProvisionArgs.Build(Opts())), "ref");
            True(InstalledHead.RefOf(new List<string> { "--ref", "main" }) == null, "a branch is no pin");
            True(InstalledHead.RefOf(new List<string> { "--ref" }) == null, "no value");
            True(InstalledHead.RefOf(null) == null, "no args");
        });
        Run("installed head: any commit pinned since the import, once each", delegate
        {
            const string a = "eb4a497fe821c08b6b8c39d7603ff7384d696dfc", b = "606829ce49622b19ec7494093ab239297d16faf3";
            var l = InstalledHead.WithAsked(null, a);
            SeqEq(new[] { a }, l, "first");
            l = InstalledHead.WithAsked(l, b);
            SeqEq(new[] { a, b }, l, "a newer Setup");
            SeqEq(new[] { a, b }, InstalledHead.WithAsked(l, a), "no repeats");
            SeqEq(new[] { a }, InstalledHead.WithAsked(new List<string> { a, "main", null }, null), "junk dropped");
        });
        Run("installed head: fails closed on anything but a pinned commit", delegate
        {
            const string a = "eb4a497fe821c08b6b8c39d7603ff7384d696dfc", b = "606829ce49622b19ec7494093ab239297d16faf3";
            var asked = new List<string> { a, b };
            True(InstalledHead.Problem(a, asked) == null, "the first pin");
            True(InstalledHead.Problem(b, asked) == null, "the later pin");
            string why = InstalledHead.Problem("f280d9a4b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6", asked);
            True(why != null && why.Contains("not the one Setup pinned"), "another commit: " + why);
            True(InstalledHead.Problem(null, asked) != null, "no head reported");
            True(InstalledHead.Problem("eb4a497", asked) != null, "a short SHA");
            True(InstalledHead.Problem(a, new List<string>()) != null, "nothing to compare with");
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
            o = Opts(); o.TrackBranch = "release";
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "a branch that is not a channel");
            o = Opts(); o.TrackBranch = null;
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "no channel");
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

    // -- "Include AI chat and questions?" (owner decision 2026-10-21) ----

    // provision.sh's usage lines for the two options (wf/ai-optional-linux,
    // b86d689), and the lines an older one ends its options with.
    const string AiUsage = "  --wsl, --no-wsl       force WSL mode on or off\n"
        + "  --ai-models           include AI chat and questions (the default): pull\n"
        + "                        the tier's AI models, about 6.4 GB (about 9.1 GB on\n"
        + "  --no-ai-models        leave AI chat and questions out: no AI models are\n"
        + "                        pulled, and AI_FEATURES=false. Voice commands,\n";
    const string OldUsage = "  --keep-downloads      keep the Ollama tarball in /var/cache/domovoi-provision\n  --dry-run             say what each step would do; change nothing\n";

    static void AiChoice()
    {
        Run("ai: Yes to a provision.sh that takes the answer says --ai-models", delegate
        {
            var o = Opts(); o.ScriptHasAiChoice = true;
            var a = ProvisionArgs.Build(o);
            True(a.Contains("--ai-models") && !a.Contains("--no-ai-models"), "yes");
            Eq(a.IndexOf("--tier") + 2, a.IndexOf("--ai-models"), "after --tier auto");
            Eq(1, a.FindAll(x => x.EndsWith("ai-models", StringComparison.Ordinal)).Count, "once");
        });
        Run("ai: Yes to an older provision.sh keeps its arguments as they were", delegate
        {
            var o = Opts(); o.ScriptHasAiChoice = false; o.IncludeAi = true;
            var a = ProvisionArgs.Build(o);
            True(!a.Contains("--ai-models") && !a.Contains("--no-ai-models"), "no AI option");
            var before = Opts();
            SeqEq(ProvisionArgs.Build(before), a, "same as the default options");
        });
        Run("ai: Yes is the default", delegate
        {
            True(new ProvisionOptions().IncludeAi, "options");
            True(new Ledger().IncludeAi, "a new record");
        });
        Run("ai: No says --no-ai-models", delegate
        {
            var o = Opts(); o.IncludeAi = false; o.ScriptHasAiChoice = true;
            var a = ProvisionArgs.Build(o);
            True(a.Contains("--no-ai-models") && !a.Contains("--ai-models"), "no");
            Eq(a.IndexOf("--tier") + 2, a.IndexOf("--no-ai-models"), "after --tier auto");
            // Everything else as for Yes.
            o.IncludeAi = true;
            var yes = ProvisionArgs.Build(o);
            yes.Remove("--ai-models"); a.Remove("--no-ai-models");
            SeqEq(yes, a, "the rest unchanged");
        });
        Run("ai: No to an older provision.sh is refused, not sent (it would exit 2)", delegate
        {
            var o = Opts(); o.IncludeAi = false; o.ScriptHasAiChoice = false;
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "no --no-ai-models in the script");
        });
        Run("ai: which provision.sh takes the answer, from its usage text", delegate
        {
            True(ProvisionScript.SupportsAiChoice(AiUsage), "the new one");
            True(!ProvisionScript.SupportsAiChoice(OldUsage), "an older one");
            True(!ProvisionScript.SupportsAiChoice("# --ai-models and --no-ai-models in a comment only"), "a mention is not the option");
            True(!ProvisionScript.SupportsAiChoice("\n  --ai-models           include\n"), "both options, not one");
            True(!ProvisionScript.SupportsAiChoice(null), "null");
        });
        Run("ai: the models' download for this PC's memory", delegate
        {
            Eq("6.4", AiModels.DownloadGb(10), "below 24 GB of RAM: capped at 10");
            Eq("6.4", AiModels.DownloadGb(22), "a cap of 22 reads under 22 GiB");
            Eq("9.1", AiModels.DownloadGb(23), "a cap of 23 reads over 22 GiB");
            const long G = 1024L * 1024 * 1024;
            Eq("6.4", AiModels.DownloadGb(WslConfig.MemoryCapGb(16 * G)), "16 GB PC");
            Eq("6.4", AiModels.DownloadGb(WslConfig.MemoryCapGb(24 * G)), "24 GB PC (cap 16)");
            Eq("9.1", AiModels.DownloadGb(WslConfig.MemoryCapGb(32 * G)), "32 GB PC (cap 24)");
            Eq("9.1", AiModels.DownloadGb(WslConfig.MemoryCapGb(64 * G)), "64 GB PC");
        });
        Run("ai: the answer is kept in the install record", delegate
        {
            var l = FullLedger(); l.IncludeAi = false;
            var d = l.ToJson();
            Eq(false, (bool)d["include_ai"], "written");
            True(!Ledger.FromJson(Json.ParseObject(Json.Serialize(d))).IncludeAi, "No survives");
            l.IncludeAi = true;
            True(Ledger.FromJson(Json.ParseObject(Json.Serialize(l.ToJson()))).IncludeAi, "Yes survives");
            var old = l.ToJson(); old.Remove("include_ai");
            True(Ledger.FromJson(Json.ParseObject(Json.Serialize(old))).IncludeAi, "a record from before the question: Yes");
        });
        Run("ai: the job step passes the recorded answer to provision.sh", delegate
        {
            var l = FullLedger(); l.IncludeAi = false;
            const string repo = "https://github.com/coders-farm-official/domovoi", sha = "eb4a497fe821c08b6b8c39d7603ff7384d696dfc";
            var o = Domovoi.Windows.Helper.Steps.JobOptions(l, repo, sha, "stable", AiUsage);
            True(!o.IncludeAi && o.ScriptHasAiChoice, "options");
            True(ProvisionArgs.Build(o).Contains("--no-ai-models"), "No reaches the arguments");
            l.IncludeAi = true;
            True(ProvisionArgs.Build(Domovoi.Windows.Helper.Steps.JobOptions(l, repo, sha, "stable", AiUsage)).Contains("--ai-models"), "Yes");
            var older = ProvisionArgs.Build(Domovoi.Windows.Helper.Steps.JobOptions(l, repo, sha, "stable", OldUsage));
            True(!older.Contains("--ai-models") && !older.Contains("--no-ai-models"), "Yes to an older script: nothing");
            l.IncludeAi = false;
            var refused = Domovoi.Windows.Helper.Steps.JobOptions(l, repo, sha, "stable", OldUsage);
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(refused); }, "No to an older script");
            Eq("never", o.Internet, "internet from the record too");
            Eq(5, o.Media.Count, "media folders from the record");
        });
        Run("ai: a changed answer is a new job (the host provisions again)", delegate
        {
            var yes = Opts(); yes.ScriptHasAiChoice = true;
            var no = Opts(); no.ScriptHasAiChoice = true; no.IncludeAi = false;
            True(string.Join("\n", ProvisionArgs.Build(yes).ToArray()) != string.Join("\n", ProvisionArgs.Build(no).ToArray()), "different arguments");
        });
        SavedAiAnswers();
    }

    // Review round 1 (2026-10-21): on a Setup run again, the saved answer
    // wins; the models are pulled only when AI is actually on; the record
    // keeps what provision.sh reported.
    static List<string> JobArgsAfter(Ledger l, string prepareAi)
    {
        Domovoi.Windows.Helper.Steps.RecordAiAnswer(l, prepareAi);
        return ProvisionArgs.Build(Domovoi.Windows.Helper.Steps.JobOptions(l, "https://github.com/coders-farm-official/domovoi",
            "eb4a497fe821c08b6b8c39d7603ff7384d696dfc", "stable", AiUsage));
    }

    static HostStatus ReadyWith(bool? ai)
    {
        var s = new HostStatus(); s.State = "ready"; s.Ai = ai;
        return HostStatus.FromJson(Json.ParseObject(Json.Serialize(s.ToJson())));
    }

    static void SavedAiAnswers()
    {
        Run("ai re-run: AI off (saved No): the page starts at No and No is said", delegate
        {
            // The household turned AI off in Settings > Models after a Yes.
            var l = FullLedger(); l.IncludeAi = true;
            var a = SavedAiAnswer.Decide(l, false);
            True(!a.Yes, "page default No"); Eq("household", a.From, "from the household's .env");
            var args = JobArgsAfter(l, "no");
            True(args.Contains("--no-ai-models") && !args.Contains("--ai-models"), "--no-ai-models");
            // The record alone says No (DomovoiHost couldn't be asked).
            l = FullLedger(); l.IncludeAi = false;
            a = SavedAiAnswer.Decide(l, null);
            True(!a.Yes, "record No: page default No"); Eq("record", a.From, "from the record");
            args = JobArgsAfter(l, "no");
            True(args.Contains("--no-ai-models") && !args.Contains("--ai-models"), "record No: --no-ai-models");
        });
        Run("ai re-run: an earlier No, AI turned on later in Settings: the page follows the household", delegate
        {
            var l = FullLedger(); l.IncludeAi = false;
            var a = SavedAiAnswer.Decide(l, true);
            True(a.Yes, "page default Yes"); Eq("household", a.From, "from the household's .env");
            var args = JobArgsAfter(l, "yes");
            True(args.Contains("--ai-models") && !args.Contains("--no-ai-models"), "--ai-models");
            True(l.IncludeAi && !l.AiKeep, "recorded");
        });
        Run("ai re-run: a Yes from the record alone, untouched, says neither option", delegate
        {
            // An older DomovoiHost can't read .env: provision.sh keeps it.
            var l = FullLedger(); l.IncludeAi = true;
            var a = SavedAiAnswer.Decide(l, null);
            True(a.Yes, "page default Yes"); Eq("record", a.From, "from the record");
            var args = JobArgsAfter(l, "keep");
            True(!args.Contains("--ai-models") && !args.Contains("--no-ai-models"), "neither option");
            True(l.AiKeep && l.IncludeAi, "recorded as keep");
            True(Ledger.FromJson(Json.ParseObject(Json.Serialize(l.ToJson()))).AiKeep, "keep survives the record");
            // A Yes picked again over keep is said out loud.
            args = JobArgsAfter(l, "yes");
            True(args.Contains("--ai-models") && !l.AiKeep, "yes after keep");
            // keep over a recorded No is a No: never the models on a guess.
            l = FullLedger(); l.IncludeAi = false;
            args = JobArgsAfter(l, "keep");
            True(args.Contains("--no-ai-models") && !l.AiKeep, "keep over No");
            var old = FullLedger().ToJson(); old.Remove("ai_keep");
            True(!Ledger.FromJson(Json.ParseObject(Json.Serialize(old))).AiKeep, "a record from before: no keep");
        });
        Run("ai: a first install starts at Yes", delegate
        {
            var a = SavedAiAnswer.Decide(null, null);
            True(a.Yes, "yes"); Eq("none", a.From, "none");
        });
        Run("ai: the household's AI_FEATURES as the core reads it", delegate
        {
            True(!HouseholdAi.FromSedOutput("false\n"), "false");
            True(HouseholdAi.FromSedOutput("true\n"), "true");
            True(HouseholdAi.FromSedOutput(""), "no AI_FEATURES line: on");
            True(HouseholdAi.FromSedOutput(null), "null: on");
            True(!HouseholdAi.FromSedOutput("\"false\"\n"), "double quotes");
            True(!HouseholdAi.FromSedOutput("'off'\n"), "single quotes");
            True(!HouseholdAi.FromSedOutput("false # turned off in Settings\n"), "a comment");
            True(HouseholdAi.FromSedOutput("false\ntrue\n"), "the last line counts");
            True(!HouseholdAi.FromSedOutput("true\nFALSE\r\n"), "case and CRLF");
            True(!HouseholdAi.FromSedOutput("0\n") && !HouseholdAi.FromSedOutput("never\n") && !HouseholdAi.FromSedOutput("disabled\n"), "other offs");
            True(HouseholdAi.FromSedOutput("maybe\n") && HouseholdAi.FromSedOutput("\n\n"), "unknown or blank: on");
            True(HouseholdAi.SedScript.IndexOf(' ') < 0 && HouseholdAi.SedScript.IndexOf('"') < 0, "one argument for wsl --exec");
        });
        Run("ai: provision.sh's reported ai reaches the host's status", delegate
        {
            var p = ProvisionLine.TryParse(RealFinal.Replace("\"warnings\":2", "\"ai\":false,\"warnings\":2"));
            True(p != null && p.Ai == false, "false");
            p = ProvisionLine.TryParse(RealFinal.Replace("\"warnings\":2", "\"ai\":true,\"warnings\":2"));
            True(p != null && p.Ai == true, "true");
            p = ProvisionLine.TryParse(RealFinal.Replace("\"warnings\":2", "\"ai\":null,\"warnings\":2"));
            True(p != null && p.Ai == null, "null");
            True(ProvisionLine.TryParse(RealFinal).Ai == null, "an older script: unknown");
            True(ReadyWith(false).Ai == false && ReadyWith(true).Ai == true && ReadyWith(null).Ai == null, "status round trip");
        });
        Run("ai: the record takes provision.sh's reported ai, not the click", delegate
        {
            // Yes clicked (keep), but the household's .env had AI off.
            var l = FullLedger(); l.IncludeAi = true;
            True(AiReport.Apply(l, ReadyWith(false)), "changed");
            True(!l.IncludeAi, "No recorded");
            True(!AiReport.Apply(l, ReadyWith(false)), "no change the second time");
            True(AiReport.Apply(l, ReadyWith(true)) && l.IncludeAi, "Yes recorded");
            True(!AiReport.Apply(l, ReadyWith(null)) && l.IncludeAi, "unknown: the answer stays");
            var busy = ReadyWith(false); busy.State = "provisioning";
            True(!AiReport.Apply(l, busy) && l.IncludeAi && AiReport.Reported(busy) == null, "not ready: nothing yet");
            True(!AiReport.Apply(l, null) && !AiReport.Apply(null, ReadyWith(false)), "nothing to go on");
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
            // Security review 2026-11-11: 8.3 short names and device names.
            // A name with ~ and a digit is a name (Music~2024); whether it is
            // an 8.3 short name only Windows can say (ShortNameProblem).
            True(MediaPaths.Problem(@"D:\Music~2024") == null, "a folder really named Music~2024");
            True(MediaPaths.Problem(@"D:\Media~old") == null, "~ alone is a name like any other");
            foreach (string dev in new[] { @"D:\CON", @"D:\Media\nul", @"D:\aux.txt", @"D:\COM1\x", @"D:\lpt9.tar.gz", @"D:\Media\PRN" })
                True(MediaPaths.Problem(dev) != null, "a device name: " + dev);
            True(MediaPaths.Problem(@"D:\CONSOLE") == null && MediaPaths.Problem(@"D:\COM10") == null, "only the device names themselves");
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
            foreach (string s in new[] { "10.0.0.1", "10.255.255.255", "172.16.0.1", "172.31.255.254", "192.168.1.30" })
                True(Lan.IsPrivateIPv4(s), s);
            foreach (string s in new[] { "172.15.0.1", "172.32.0.1", "100.64.0.1", "100.101.102.103", "169.254.1.1", "127.0.0.1", "8.8.8.8", "192.169.0.1", "1.2", "::1", "", null, "192.168.1.256" })
                True(!Lan.IsPrivateIPv4(s), "not private: " + s);
        });
        Run("lan: subnets", delegate
        {
            Eq("192.168.1.0/24", Lan.Subnet("192.168.1.30", 24), "/24");
            Eq("10.0.0.0/8", Lan.Subnet("10.1.2.3", 8), "/8");
            Eq("192.168.1.30/32", Lan.Subnet("192.168.1.30", 32), "/32");
            Eq("172.16.0.0/12", Lan.Subnet("172.20.5.6", 12), "/12");
            Throws<ArgumentException>(delegate { Lan.Subnet("192.168.1.1", 7); }, "/7");
            Throws<ArgumentException>(delegate { Lan.Subnet("192.168.1.1", 0); }, "/0");
            Eq(24, Lan.PrefixFromMask("255.255.255.0"), "mask 24");
            Eq(20, Lan.PrefixFromMask("255.255.240.0"), "mask 20");
            Throws<ArgumentException>(delegate { Lan.PrefixFromMask("255.0.255.0"); }, "holes");
            True(Lan.IsCidr("192.168.1.0/24"), "cidr");
            True(!Lan.IsCidr("192.168.1.5/24"), "host bits set");
            True(!Lan.IsCidr("192.168.1.0"), "no prefix");
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
            Eq(20, WslConfig.MemoryCapGb(28 * gib), "28 GB: RAM - 8");
            Eq(24, WslConfig.MemoryCapGb(32 * gib), "32 GB: training's 24 GB and the standard tier fit");
            Eq(24, WslConfig.MemoryCapGb(48 * gib), "48 GB");
            Eq(32, WslConfig.MemoryCapGb(64 * gib), "64 GB: half (was 56)");
            Eq(32, WslConfig.MemoryCapGb(137242800128L), "a 128 GB PC: 32 (was 64, 2026-11-10)");
            Eq(32, WslConfig.MemoryCapGb(256 * gib), "256 GB");
        });
        HardwareLimitsTests();
        WslOutageTests();
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

    static void WslOutageTests()
    {
        Run("wsl outage: wait while an MSI installs, wsl.exe is gone or WSLService stops; never ask wsl.exe then", delegate
        {
            var msi = new WslFacts { MsiBusy = true, Service = WslService.Running, Answers = true };
            True(!WslOutage.MayAsk(msi), "no wsl.exe call during an install (it would start WSLService mid-update)");
            Eq(WslProbe.Wait, WslOutage.Decide(msi), "an install: wait, even with an answer");
            Eq(WslProbe.Wait, WslOutage.Decide(new WslFacts { ExeExists = false, Answers = true }), "no wsl.exe: wait");
            Eq(WslProbe.Wait, WslOutage.Decide(new WslFacts { Service = WslService.Stopping, Answers = true }), "WSLService stopping: wait");
            Eq(WslProbe.Wait, WslOutage.Decide(new WslFacts { Service = WslService.Running }), "not asked yet: wait");
            Eq(WslProbe.Wait, WslOutage.Decide(new WslFacts { Service = WslService.Running, Answers = false }), "no answer: wait");
            Eq(WslProbe.Recover, WslOutage.Decide(new WslFacts { Service = WslService.Running, Answers = true }), "it answers: bring Domovoi up");
            // A stopped WSLService starts on demand: wsl.exe may be asked.
            True(WslOutage.MayAsk(new WslFacts { Service = WslService.Stopped }), "stopped (not updating): ask, which starts it");
            Eq(WslProbe.Recover, WslOutage.Decide(new WslFacts { Service = WslService.Stopped, Answers = true }), "and it answered");
        });
        Run("wsl outage: the backoff between looks", delegate
        {
            SeqEq(new[] { "5", "10", "20", "30", "60", "60", "60" },
                  Enumerable.Range(0, 7).Select(i => Inv.S((int)WslOutage.Delay(i).TotalSeconds)), "5, 10, 20, 30, then every minute");
            Eq(5, (int)WslOutage.Delay(-3).TotalSeconds, "never negative");
        });
        Run("wsl outage: gone or only the distro stopped", delegate
        {
            True(!WslOutage.Gone(new WslFacts { Service = WslService.Running, Answers = true }), "WSL answers: only the distro stopped");
            True(WslOutage.Gone(new WslFacts { Service = WslService.Running, Answers = false }), "no answer");
            True(WslOutage.Gone(new WslFacts { Service = WslService.Stopped }), "WSLService stopped");
            True(WslOutage.Gone(new WslFacts { MsiBusy = true, Service = WslService.Running, Answers = true }), "an install");
            True(WslOutage.Gone(new WslFacts { ExeExists = false }), "no wsl.exe");
            True(WslOutage.Gone(null), "nothing known");
        });
        Run("wsl outage: the words", delegate
        {
            Eq("Domovoi stopped: Windows updated WSL, starting again\u2026", WslOutage.Message(new WslFacts { MsiBusy = true }, "2.7.14.0"), "an install");
            Eq(WslOutage.UpdatedMessage, WslOutage.Message(new WslFacts { Version = "3.0.5.0", Service = WslService.Running }, "2.7.14.0"), "a new version since Domovoi ran");
            Eq(WslOutage.UpdatedMessage, WslOutage.Message(new WslFacts { ExeExists = false }, "2.7.14.0"), "wsl.exe gone");
            Eq("Domovoi stopped: WSL stopped, starting again\u2026", WslOutage.Message(new WslFacts { Version = "2.7.14.0", Service = WslService.Stopped }, "2.7.14.0"), "same version");
            Eq(WslOutage.StoppedMessage, WslOutage.Message(new WslFacts { Version = "3.0.5.0" }, null), "no version known from before");
        });
        Run("wsl outage: an MSI that holds Windows Installer for 30 minutes no longer blocks asking", delegate
        {
            var clock = new MsiClock();
            DateTime t0 = new DateTime(2026, 11, 10, 0, 25, 0, DateTimeKind.Utc);
            Eq(TimeSpan.Zero, clock.For(true, t0), "starts");
            var f = new WslFacts { MsiBusy = true, Service = WslService.Running, MsiBusyFor = clock.For(true, t0.AddMinutes(29)) };
            True(!WslOutage.MayAsk(f), "29 minutes: still waits");
            f.MsiBusyFor = clock.For(true, t0.AddMinutes(30));
            True(WslOutage.MayAsk(f), "30 minutes: asks anyway");
            f.Answers = true;
            Eq(WslProbe.Recover, WslOutage.Decide(f), "and comes back when it answers");
            Eq(TimeSpan.Zero, clock.For(false, t0.AddMinutes(31)), "not busy: the clock resets");
            Eq(TimeSpan.Zero, clock.For(true, t0.AddMinutes(32)), "a new install counts afresh");
        });
        Run("wsl outage: only a wsl.exe that can't start means WSL went away", delegate
        {
            True(WslOutage.IsWslFailure(new System.ComponentModel.Win32Exception(2)), "wsl.exe missing");
            True(!WslOutage.IsWslFailure(new System.IO.IOException("disk")), "an I/O error is a bug: the host stops, the SCM restarts it");
            True(!WslOutage.IsWslFailure(new InvalidOperationException("x")), "nor this");
            True(!WslOutage.IsWslFailure(new NullReferenceException()), "nor this");
        });
        Run("recovery: a failed bring-up is tried again unless it was a first install", delegate
        {
            True(WslOutage.RetryFailedBringUp(false, false), "after WSL came back, or a limits restart");
            True(WslOutage.RetryFailedBringUp(true, true), "the service's start, a job already provisioned (every boot)");
            True(!WslOutage.RetryFailedBringUp(true, false), "a first install: Setup shows why and decides");
            var r = new Recovery();
            DateTime t = new DateTime(2026, 11, 10, 1, 0, 0, DateTimeKind.Utc);
            r.Lost(t, false);
            True(r.Down && !r.WslTrouble && r.Attempts == 0, "down, WSL fine");
            True(!r.Due(t.AddSeconds(4)) && r.Due(t.AddSeconds(5)), "first look after 5 s");
            r.Failed(t.AddSeconds(5)); r.Failed(t.AddSeconds(15)); r.Failed(t.AddSeconds(35));
            Eq(3, r.Attempts, "three tries");
            r.Lost(t.AddSeconds(40), true);
            Eq(3, r.Attempts, "lost again while waiting: the count is kept (no reset to 5 s)");
            True(r.WslTrouble, "now WSL's trouble");
            True(!r.Due(t.AddSeconds(41)), "and the next look isn't moved earlier");
            r.Recovered();
            True(!r.Down && r.Attempts == 0, "back");
            r.Lost(t.AddMinutes(5), true);
            Eq(0, r.Attempts, "a new outage starts afresh");
            Eq("Domovoi didn't start; trying again\u2026", WslOutage.DownMessage(new WslFacts(), null, false), "the words when WSL is fine");
        });
        Run("service: an unregister never ends with an error code (no SCM restart mid-uninstall)", delegate
        {
            Eq(0, HostExit.CodeFor(true, true), "unregister failed: 0, recorded in unregister.json");
            Eq(0, HostExit.CodeFor(true, false), "unregister done: 0");
            Eq(1064, HostExit.CodeFor(false, true), "the long-running host failed: the SCM restarts it");
            Eq(0, HostExit.CodeFor(false, false), "stopped: 0");
            var d = HostExit.UnregisterFailure(new InvalidOperationException("WSL did not answer"), new DateTime(2026, 11, 10, 1, 0, 0, DateTimeKind.Utc));
            True(Json.Bool(d, "ok") == false, "ok false");
            True(Json.Str(d, "error").Contains("WSL did not answer"), "the reason, for the uninstaller");
            True(Json.Str(d, "at").StartsWith("2026-11-10T01:00:00", StringComparison.Ordinal), "when");
        });
        Run("service: no dependency on WSLService, restarted on any failure", delegate
        {
            Eq("", Domovoi.Windows.Helper.HostService.NoDependencies, "an empty list (\"\" removes dependencies; null keeps them)");
            SeqEq(new[] { "10000", "30000", "60000" }, Domovoi.Windows.Helper.HostService.RestartDelaysMs.Select(d => Inv.S(d)), "10 s, 30 s, then 60 s repeating");
            Eq(86400, Domovoi.Windows.Helper.HostService.FailureResetSec, "counted afresh after a day");
        });
    }

    static void HardwareLimitsTests()
    {
        const long G = 1024L * 1024 * 1024;
        LimitsBounds b128 = HardwareLimits.Bounds(137242800128L, 32);
        Run("limits: this PC's bounds", delegate
        {
            Eq(128, b128.RamGb, "RAM label");
            Eq(8, b128.MemoryMin, "never under 8 GB");
            Eq(124, b128.MemoryMax, "RAM - 4");
            Eq(2, b128.ProcessorsMin, "never under 2 cores");
            Eq(32, b128.ProcessorsMax, "every logical processor");
            LimitsBounds small = HardwareLimits.Bounds(16 * G - G / 3, 4);
            Eq(12, small.MemoryMax, "16 GB PC: up to 12");
            LimitsBounds tiny = HardwareLimits.Bounds(8 * G, 1);
            Eq(8, tiny.MemoryMax, "an 8 GB PC can't go under the floor either");
            Eq(1, tiny.ProcessorsMin, "one core: the floor is what there is");
        });
        Run("limits: a request", delegate
        {
            LimitsRequest r = HardwareLimits.Parse("{\"v\":1,\"memory_gb\":24,\"processors\":8,\"restart_id\":null,\"at\":\"2026-11-10T05:00:00Z\"}", b128);
            Eq("ok", r.State, "ok");
            Eq(24, r.Choice.MemoryGb.Value, "memory");
            Eq(8, r.Choice.Processors.Value, "processors");
            True(r.RestartId == null, "no restart");
            r = HardwareLimits.Parse("{\"v\":1,\"memory_gb\":null,\"processors\":null,\"restart_id\":\"0123456789abcdef0123456789abcdef\"}", b128);
            Eq("ok", r.State, "automatic");
            True(!r.Choice.MemoryGb.HasValue && !r.Choice.Processors.HasValue, "both automatic");
            Eq("0123456789abcdef0123456789abcdef", r.RestartId, "the restart id");
            Eq("none", HardwareLimits.Parse(null, b128).State, "no file");
        });
        Run("limits: numbers outside this PC's bounds are moved inside", delegate
        {
            LimitsRequest r = HardwareLimits.Parse("{\"v\":1,\"memory_gb\":2,\"processors\":512}", b128);
            Eq("clamped", r.State, "clamped");
            Eq(8, r.Choice.MemoryGb.Value, "up to the floor");
            Eq(32, r.Choice.Processors.Value, "down to the cores there are");
            r = HardwareLimits.Parse("{\"v\":1,\"memory_gb\":4000,\"processors\":0}", b128);
            Eq(124, r.Choice.MemoryGb.Value, "never past RAM - 4");
            Eq(2, r.Choice.Processors.Value, "never under 2");
        });
        Run("limits: anything else in a request is refused", delegate
        {
            string[] bad =
            {
                "", "[]", "not json", "{\"v\":2,\"memory_gb\":24}", "{\"memory_gb\":24}",
                "{\"v\":1,\"memory_gb\":\"24\"}", "{\"v\":1,\"memory_gb\":24.5}", "{\"v\":1,\"processors\":[4]}",
                "{\"v\":1,\"memory_gb\":24,\"swap\":64}", "{\"v\":1,\"wslconfig\":\"[wsl2]\\nkernel=x\"}",
                "{\"v\":1,\"restart_id\":\"abc\"}", "{\"v\":1,\"restart_id\":\"0123456789ABCDEF0123456789ABCDEF\"}",
                "{\"v\":1,\"restart_id\":7}", "{\"v\":1,\"memory_gb\":99999999999}",
                "{\"v\":1,\"memory_gb\":24}" + new string(' ', 3000),
            };
            foreach (string t in bad)
            {
                LimitsRequest r = HardwareLimits.Parse(t, b128);
                Eq("invalid", r.State, "refused: " + (t.Length > 60 ? t.Substring(0, 60) : t));
                True(r.Choice == null && r.RestartId == null && !string.IsNullOrEmpty(r.Problem), "nothing taken from it");
            }
        });
        Run("limits: into the installer's .wslconfig, nothing else touched", delegate
        {
            var o = new WslConfigOptions();
            o.MemoryGb = 32;
            o.SwapFile = @"C:\ProgramData\Domovoi\wsl\swap.vhdx";
            string job = WslConfig.Render(o);
            Eq(job, HardwareLimits.Apply(job, new LimitsChoice()), "automatic: the installer's text as it is");
            var c = new LimitsChoice(); c.MemoryGb = 20; c.Processors = 6;
            string t = HardwareLimits.Apply(job, c);
            var lines = t.Split('\n').ToList();
            int m = lines.IndexOf("memory=20GB");
            True(m > 0, "memory replaced");
            Eq("processors=6", lines[m + 1], "processors right after it");
            True(!t.Contains("memory=32GB"), "the old size is gone");
            Eq(job.Split('\n').Length + 1, lines.Count, "one line more, nothing else");
            True(t.Contains("autoMemoryReclaim=dropCache") && t.Contains("networkingMode=mirrored"), "the rest stays");
            // Applying again, or another choice, never stacks lines.
            var c2 = new LimitsChoice(); c2.Processors = 4;
            string t2 = HardwareLimits.Apply(t, c2);
            Eq(1, t2.Split('\n').Count(l => l.StartsWith("processors=")), "one processors= line");
            True(t2.Contains("memory=20GB"), "an automatic size keeps the text's line");
            LimitsChoice back = HardwareLimits.InFile(t);
            Eq(20, back.MemoryGb.Value, "read back: memory");
            Eq(6, back.Processors.Value, "read back: processors");
            True(!HardwareLimits.InFile(job).Processors.HasValue, "the installer sets no processors=");
            Throws<FormatException>(delegate { HardwareLimits.Apply("[general]\nx=1\n", c); }, "no memory= line");
            var huge = new LimitsChoice(); huge.MemoryGb = 5000;
            Throws<ArgumentException>(delegate { HardwareLimits.Apply(job, huge); }, "a size no PC has");
        });
        Run("limits: a stored choice stays inside the bounds of today", delegate
        {
            var c = new LimitsChoice(); c.MemoryGb = 100; c.Processors = 64;
            LimitsChoice w = HardwareLimits.Within(c, HardwareLimits.Bounds(32 * G, 8));
            Eq(28, w.MemoryGb.Value, "RAM was taken out: 32 - 4");
            Eq(8, w.Processors.Value, "8 cores");
            True(!HardwareLimits.Within(new LimitsChoice(), b128).MemoryGb.HasValue, "automatic stays automatic");
        });
        Run("limits: the report and the root script", delegate
        {
            var chosen = new LimitsChoice(); chosen.MemoryGb = 24;
            var defaults = new LimitsChoice(); defaults.MemoryGb = 32;
            var req = new LimitsRequest(); req.State = "invalid"; req.Problem = "the request has a key it may not have";
            string t = HardwareLimits.Report(b128, defaults, chosen, chosen, req, "done", new DateTime(2026, 11, 10, 5, 0, 0, DateTimeKind.Utc));
            var d = Json.ParseObject(t);
            Eq(1, Json.Int(d, "schema").Value, "schema");
            Eq(128, Json.Int(d, "ram_gb").Value, "RAM");
            Eq(32, Json.Int(d, "logical_processors").Value, "cores");
            var mem = Json.Obj(d, "memory_gb");
            Eq(8, Json.Int(mem, "min").Value, "min");
            Eq(124, Json.Int(mem, "max").Value, "max");
            Eq(32, Json.Int(mem, "default").Value, "default");
            Eq(24, Json.Int(mem, "chosen").Value, "chosen");
            True(Json.IsNull(Json.Obj(d, "processors"), "chosen"), "processors automatic");
            Eq("invalid", Json.Str(d, "request"), "request state");
            Eq("done", Json.Str(d, "restart"), "restart");
            Eq("2026-11-10T05:00:00Z", Json.Str(d, "written_at"), "time");
            True(t.EndsWith("\n") && !t.Contains("\r"), "one LF line");
            string s = HardwareLimits.ReportScript();
            True(s.Contains("chown root:root /var/lib/domovoi-windows") && s.Contains("chmod 0644") && s.Contains("sha256sum /var/lib/domovoi-windows/limits.json"), "root's file");
            string[] a = HardwareLimits.ReadArgs("domovoi");
            SeqEq(new[] { "-d", "domovoi", "-u", "domovoi", "--cd", "/", "--exec", "/usr/bin/timeout", "-s", "KILL", "5",
                          "/usr/bin/head", "-c", "2049", "/home/domovoi/.domovoi/hardware_limits/request.json" }, a,
                  "read as the service user, bounded in size and (a FIFO) in time");
            SeqEq(new[] { "-d", "domovoi", "-u", "root", "--cd", "/", "--exec", "/usr/bin/systemctl", "show", "-p", "ActiveState", "--value", "domovoi-update.service" },
                  HardwareLimits.UpdateStateArgs("domovoi"), "the update unit's state, read as root");
        });
        Run("limits: the maximum counts from what Windows can use", delegate
        {
            LimitsBounds b = HardwareLimits.Bounds(137242800128L, 137242800128L - G / 3 - G / 4, 32);
            Eq(128, b.RamGb, "the label from the installed RAM");
            Eq(123, b.MemoryMax, "127.x GB usable, whole GB down, less 4");
            Eq(124, HardwareLimits.Bounds(137242800128L, 0, 32).MemoryMax, "usable unknown: the installed figure");
            Eq(28, HardwareLimits.Bounds(0, 32 * G, 8).MemoryMax, "installed unknown: the usable one");
        });
        Run("limits: the request's time", delegate
        {
            LimitsRequest r = HardwareLimits.Parse("{\"v\":1,\"memory_gb\":24,\"restart_id\":\"0123456789abcdef0123456789abcdef\",\"at\":\"2026-11-10T05:00:00Z\"}", b128);
            Eq("2026-11-10T05:00:00Z", r.At, "as written");
            Eq(new DateTime(2026, 11, 10, 5, 0, 0, DateTimeKind.Utc), r.AtUtc.Value, "as a time");
            foreach (string bad in new[] { "yesterday", "2026-11-10 05:00:00", "2026-13-40T05:00:00Z", "2026-11-10T05:00:00+02:00" })
            {
                LimitsRequest x = HardwareLimits.Parse("{\"v\":1,\"restart_id\":\"0123456789abcdef0123456789abcdef\",\"at\":\"" + bad + "\"}", b128);
                Eq("invalid", x.State, "refused: " + bad);
                True(x.RestartId == null, "and no restart from it");
            }
            True(HardwareLimits.Parse("{\"v\":1}", b128).AtUtc == null, "none at all: fine, no time");
            string report = HardwareLimits.Report(b128, new LimitsChoice(), new LimitsChoice(), new LimitsChoice(), r, "none", DateTime.UtcNow);
            Eq("2026-11-10T05:00:00Z", Json.Str(Json.ParseObject(report), "request_at"), "the report says which request it read");
        });
        Run("limits: which restart ids restart Domovoi", delegate
        {
            DateTime now = new DateTime(2026, 11, 10, 5, 10, 0, DateTimeKind.Utc);
            Func<string, LimitsRequest> req = at => HardwareLimits.Parse(
                "{\"v\":1,\"memory_gb\":24,\"restart_id\":\"0123456789abcdef0123456789abcdef\"" + (at == null ? "" : ",\"at\":\"" + at + "\"") + "}", b128);
            const string id = "0123456789abcdef0123456789abcdef";
            DateTime never = DateTime.MinValue;
            Eq(LimitsRestart.Go, HardwareLimits.DecideRestart(req("2026-11-10T05:09:00Z"), null, true, now, never, "inactive"), "fresh, nothing running");
            Eq(LimitsRestart.Go, HardwareLimits.DecideRestart(req("2026-11-10T05:09:00Z"), null, true, now, never, "failed"), "an update that failed earlier is not running");
            foreach (string busy in new[] { "active", "activating", "deactivating", "reloading" })
                Eq(LimitsRestart.Busy, HardwareLimits.DecideRestart(req("2026-11-10T05:09:00Z"), null, true, now, never, busy), "the update unit " + busy + ": wait");
            Eq(LimitsRestart.Busy, HardwareLimits.DecideRestart(req("2026-11-10T05:09:00Z"), null, true, now, never, null), "can't tell: wait");
            Eq(LimitsRestart.None, HardwareLimits.DecideRestart(req("2026-11-10T05:09:00Z"), id, true, now, never, "inactive"), "already done");
            Eq(LimitsRestart.None, HardwareLimits.DecideRestart(req("2026-11-10T05:09:00Z"), null, false, now, never, "inactive"), "host-state.json unreadable: nothing, since the id couldn't be recorded");
            Eq(LimitsRestart.Stale, HardwareLimits.DecideRestart(req("2026-11-10T04:50:00Z"), null, true, now, never, "inactive"), "older than 15 minutes (written before a reboot)");
            Eq(LimitsRestart.Stale, HardwareLimits.DecideRestart(req(null), null, true, now, never, "inactive"), "no time");
            Eq(LimitsRestart.Stale, HardwareLimits.DecideRestart(req("2026-11-10T06:10:00Z"), null, true, now, never, "inactive"), "from the future");
            Eq(LimitsRestart.TooSoon, HardwareLimits.DecideRestart(req("2026-11-10T05:09:00Z"), null, true, now, now.AddMinutes(-5), "inactive"), "one per 10 minutes");
            Eq(LimitsRestart.None, HardwareLimits.DecideRestart(HardwareLimits.Parse("{\"v\":1}", b128), null, true, now, never, "inactive"), "no id");
        });
        Run("host state: read-modify-write never wipes what it can't read", delegate
        {
            string dir = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "domovoi-hoststate-" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(dir);
            try
            {
                string file = System.IO.Path.Combine(dir, "host-state.json");
                var s = new HostStateStore(file);
                string why;
                var one = new Dictionary<string, object>(); one["imported"] = true;
                True(s.Update(one, DateTime.UtcNow, out why), "a first key, no file yet");
                var two = new Dictionary<string, object>(); two["limits_restart_id"] = "x";
                True(s.Update(two, DateTime.UtcNow, out why), "a second key");
                var d = s.ReadStrict();
                True(Json.Bool(d, "imported") == true && Json.Str(d, "limits_restart_id") == "x", "both kept");
                System.IO.File.WriteAllText(file, "{\"imported\": true, \"generation\": 3, ");   // torn
                True(!s.Update(two, DateTime.UtcNow, out why), "a file that can't be read: the change refused");
                True(why != null && why.Contains("can't be read"), "and says why");
                Eq("{\"imported\": true, \"generation\": 3, ", System.IO.File.ReadAllText(file), "the file left as it was");
                True(s.ReadStrict() == null && s.Read().Count == 0, "strict read: null; plain read: empty");
            }
            finally { System.IO.Directory.Delete(dir, true); }
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
            var rules = Firewall.Plan("192.168.1.0/24");
            SeqEq(new[] { "Domovoi-Dashboard-6369", "Domovoi-Core-6370", "Domovoi-MPD-8050-8099", "Domovoi-Jellyfin-8096", "Domovoi-RomM-7676", "Domovoi-Kiwix-9467" }, rules.Select(r => r.Name), "names");
            SeqEq(new[] { "6369", "6370", "8050-8099", "8096", "7676", "9467" }, rules.Select(r => r.LocalPorts), "ports");
            True(rules.All(r => r.RemoteAddress == "192.168.1.0/24"), "subnet");
            True(!rules.Any(r => r.LocalPorts == "6432" || r.LocalPorts == "11434"), "no loopback-only port opened");
        });
        Run("firewall: never a public or malformed scope", delegate
        {
            Throws<ArgumentException>(delegate { Firewall.Plan("8.8.8.0/24"); }, "public");
            Throws<ArgumentException>(delegate { Firewall.Plan("192.168.1.5/24"); }, "host address");
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
            var r = Firewall.Plan("192.168.1.0/24");
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
        l.FirewallRules = Firewall.Plan("192.168.1.0/24").Select(r => r.Name).ToList();
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
                "RemoveRights", "UnhideAccount", "DeleteAccount", "DeleteProfile", "DeleteProgramData", "RemoveRecord",
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
            SeqEq(new[] { "StopAccountProcesses", "RemoveFirewallRules", "RemoveRights", "UnhideAccount", "DeleteProfile", "DeleteProgramData", "RemoveRunOnce", "RemoveRecord" }, kinds, "steps (the ledger's rule list counts)");
        });
        Run("uninstall: a record SID whose profile isn't domovoi-svc's is never acted on", delegate
        {
            var l = FullLedger();
            var f = new UninstallFacts();
            f.ProfileExists = true; f.Rights = new List<string> { "SeServiceLogonRight" }; f.DistroDiskExists = false;
            f.LedgerSidHasDomovoiProfile = false;
            var kinds = UninstallPlan.Make(l, f).Select(s => s.Kind).ToList();
            True(!kinds.Contains(UninstallKind.DeleteProfile) && !kinds.Contains(UninstallKind.RemoveRights) && !kinds.Contains(UninstallKind.StopAccountProcesses), "left alone");
            True(UninstallFacts.IsDomovoiProfilePath(@"C:\Users\domovoi-svc") && UninstallFacts.IsDomovoiProfilePath(@"C:\Users\domovoi-svc.DESKTOP-ALEX\"), "domovoi-svc folders");
            True(!UninstallFacts.IsDomovoiProfilePath(@"C:\Users\alex") && !UninstallFacts.IsDomovoiProfilePath(@"C:\Users\domovoi-svcx") && !UninstallFacts.IsDomovoiProfilePath(null), "others");
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
                @"C:\ProgramData", @"C:\ProgramData\Domovoi\x", @"C:\Users", @"C:\Users\alex", @"C:\Users\Public",
                @"C:\Users\alex\AppData\Roaming\Microsoft\Windows\Start Menu", @"C:\Users\Default\Music", @"C:\Users\domovoi-svc\x",
                @"C:\Recovery\x",
            })
                True(MediaPaths.SystemProblem(bad, sys) != null, "refused: " + bad);
            foreach (string ok in new[] { @"C:\Users\Public\Domovoi", @"C:\Users\alex\Music", @"D:\Media", @"C:\Media", @"C:\Users\Public\Music\Domovoi", @"C:\Windows2" })
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
            string add = Firewall.AddScript(Firewall.Plan("192.168.1.0/24"));
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

    // -- the fixes from the first real run (2026-10-12) -------------------

    // A status reader and clock played back from a list: each read returns
    // the next state (the last one repeats), each sleep moves the clock on.
    sealed class FakeService
    {
        readonly ServiceControllerStatus[] states;
        int i;
        public DateTime Now = new DateTime(2026, 10, 12, 17, 38, 14, DateTimeKind.Utc);
        public int Reads;

        public FakeService(params ServiceControllerStatus[] states) { this.states = states; }

        public ServiceControllerStatus Read()
        {
            Reads++;
            ServiceControllerStatus s = states[Math.Min(i, states.Length - 1)];
            i++;
            return s;
        }

        public void Sleep(int ms) { Now = Now.AddMilliseconds(ms); }
        public DateTime Clock() { return Now; }
    }

    static OneShotResult PlayOneShot(FakeService f, int startSec, int finishSec)
    {
        return ServiceWait.OneShot(f.Read, f.Sleep, f.Clock, TimeSpan.FromSeconds(startSec), TimeSpan.FromSeconds(finishSec));
    }

    static void ServiceWaits()
    {
        Run("service wait: the unregister run of 2026-10-12 (StartPending, then Stopped within a second) is finished, not a timeout (item 6)", delegate
        {
            var f = new FakeService(ServiceControllerStatus.StartPending, ServiceControllerStatus.StartPending, ServiceControllerStatus.Stopped);
            Eq(OneShotResult.Finished, PlayOneShot(f, 120, 900), "finished");
            True(f.Now - new DateTime(2026, 10, 12, 17, 38, 14, DateTimeKind.Utc) < TimeSpan.FromSeconds(2), "no long wait: " + f.Now);
        });
        Run("service wait: Running, then StopPending, then Stopped", delegate
        {
            var f = new FakeService(ServiceControllerStatus.StartPending, ServiceControllerStatus.Running, ServiceControllerStatus.Running,
                ServiceControllerStatus.StopPending, ServiceControllerStatus.Stopped);
            Eq(OneShotResult.Finished, PlayOneShot(f, 120, 900), "finished");
        });
        Run("service wait: seen only on its way out (StopPending) counts as started", delegate
        {
            var f = new FakeService(ServiceControllerStatus.StopPending, ServiceControllerStatus.Stopped);
            Eq(OneShotResult.Finished, PlayOneShot(f, 120, 900), "finished");
        });
        Run("service wait: Started() takes StopPending as started at once, without waiting (review C2)", delegate
        {
            var f = new FakeService(ServiceControllerStatus.StopPending);
            Eq((ServiceControllerStatus?)ServiceControllerStatus.Running, ServiceWait.Started(f.Read, f.Sleep, f.Clock, TimeSpan.FromSeconds(120)), "running");
            Eq(1, f.Reads, "one look");
        });
        Run("service wait: never past StartPending is NeverStarted, at the start limit", delegate
        {
            var f = new FakeService(ServiceControllerStatus.StartPending);
            Eq(OneShotResult.NeverStarted, PlayOneShot(f, 120, 900), "never started");
            True(f.Now - new DateTime(2026, 10, 12, 17, 38, 14, DateTimeKind.Utc) <= TimeSpan.FromSeconds(121), "gave up at the limit");
        });
        Run("service wait: running and never stopping is StillRunning", delegate
        {
            var f = new FakeService(ServiceControllerStatus.Running);
            Eq(OneShotResult.StillRunning, PlayOneShot(f, 120, 900), "still running");
        });
        Run("service wait: a long-running start settles on Running, or Stopped when it died at once", delegate
        {
            var f = new FakeService(ServiceControllerStatus.StartPending, ServiceControllerStatus.Running);
            Eq((ServiceControllerStatus?)ServiceControllerStatus.Running, ServiceWait.Started(f.Read, f.Sleep, f.Clock, TimeSpan.FromSeconds(120)), "running");
            var g = new FakeService(ServiceControllerStatus.StartPending, ServiceControllerStatus.Stopped);
            Eq((ServiceControllerStatus?)ServiceControllerStatus.Stopped, ServiceWait.Started(g.Read, g.Sleep, g.Clock, TimeSpan.FromSeconds(120)), "stopped");
            var h = new FakeService(ServiceControllerStatus.StartPending);
            True(ServiceWait.Started(h.Read, h.Sleep, h.Clock, TimeSpan.FromSeconds(5)) == null, "still starting at the limit");
        });
        Run("unregister result: only a fresh ok counts (item 6)", delegate
        {
            DateTime asked = new DateTime(2026, 10, 12, 17, 38, 12, DateTimeKind.Utc);
            // host\out\unregister.json of 2026-10-12, with what the host now adds.
            var real = Json.ParseObject("{\"ok\":true,\"message\":\"unregistered\",\"released\":true,\"at\":\"2026-10-12T17:38:14.2268277Z\"}");
            True(UnregisterOutcome.Judge(real, asked) == null, "the real answer is a success");
            True(UnregisterOutcome.Judge(real, asked.AddMinutes(5)).Contains("gave no result"), "an answer from before the request is stale");
            True(UnregisterOutcome.Judge(null, asked).Contains("gave no result"), "no file");
            var failed = Json.ParseObject("{\"ok\":false,\"error\":\"wsl --unregister failed (exit 1): busy\",\"at\":\"2026-10-12T17:38:14Z\"}");
            True(UnregisterOutcome.Judge(failed, asked).Contains("busy"), "the host's error");
        });
    }

    // The final line of a run given --stop-before start.
    const string StoppedFinal = "@@DOMOVOI {\"v\":1,\"step\":\"finish\",\"index\":21,\"total\":21,\"state\":\"done\",\"percent\":90,\"message\":\"Stopped before: Starting Domovoi (--stop-before start)\",\"final\":true,\"result\":\"stopped\",\"restart_required\":true,\"exit_code\":0,\"failed_step\":null,\"head\":\"44f0fff6822991bbae7cf6d7dc46bf1c4d54e58e\",\"tier\":\"standard\",\"wsl\":true,\"gpu\":null,\"warnings\":0,\"dashboard\":\"http://localhost:6369\"}";

    static string RepoFile(string relative)
    {
        // windows\build\obj\DomovoiWindowsTests.exe -> the repository.
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        for (int i = 0; i < 6 && dir != null; i++)
        {
            string p = System.IO.Path.Combine(dir, relative);
            if (System.IO.File.Exists(p)) return p;
            dir = System.IO.Path.GetDirectoryName(dir.TrimEnd('\\'));
        }
        return null;
    }

    static void PortGateTests()
    {
        string why;
        Run("port gate: a run stopped before start is Stopped, not done or failed (item 2)", delegate
        {
            var p = ProvisionLine.TryParse(StoppedFinal);
            Eq(ProvisionVerdict.Stopped, ProvisionOutcome.Classify(0, p, out why), "stopped");
            True(why.Contains("Starting Domovoi"), "reason: " + why);
        });
        Run("port gate: only a provision.sh that has --stop-before gets it", delegate
        {
            string path = RepoFile(@"scripts\linux\provision.sh");
            True(path != null, "found provision.sh");
            True(ProvisionScript.SupportsStopBefore(System.IO.File.ReadAllText(path)), "this branch's provision.sh has it");
            // 44f0fff's usage text (the pin today) ends its options like this.
            string old = "  --keep-downloads      keep the Ollama tarball in /var/cache/domovoi-provision\n  --dry-run             say what each step would do; change nothing\n";
            True(!ProvisionScript.SupportsStopBefore(old), "an older one does not");
            True(!ProvisionScript.SupportsStopBefore("# mentions --stop-before STEP in a comment only"), "a mention is not the option");
            True(!ProvisionScript.SupportsStopBefore(null), "null");
        });
        Run("port gate: the first pass stops before start, the one after the gate runs to the end (review C19)", delegate
        {
            var job = new List<string> { "--internet", "never" };
            var gate = new PortGate(ProvisionScript.SupportsStopBefore(System.IO.File.ReadAllText(RepoFile(@"scripts\linux\provision.sh"))));
            SeqEq(new[] { "--internet", "never", "--stop-before", "start" }, gate.Args(job), "first pass");
            True(gate.OnStopped(), "its stop is the gate");
            SeqEq(new[] { "--internet", "never" }, gate.Args(job), "the next pass");
            True(!gate.OnStopped(), "a second stop is nobody's");
            var old = new PortGate(false);
            SeqEq(new[] { "--internet", "never" }, old.Args(job), "an older provision.sh gets no --stop-before");
            True(!old.OnStopped(), "and a stop from it is a failure");
            SeqEq(new[] { "--internet", "never" }, job, "the job's own list is untouched");
        });
        Run("port gate: waits while a port is taken, says so once per answer, goes on when free (review C20)", delegate
        {
            var docker = PortCheck.Owners(new[] { new Listener(6432, 35452, "com.docker.backend") }, null);
            var both = PortCheck.Owners(new[] { new Listener(6432, 35452, "com.docker.backend"), new Listener(11434, 7, "ollama") }, null);
            var answers = new Queue<List<PortOwner>>(new[] { docker, docker, both, new List<PortOwner>() });
            var said = new List<string>();
            int sleeps = 0;
            True(PortGate.WaitUntilFree(() => answers.Dequeue(), t => said.Add(t), ms => { sleeps++; return false; }, 3000), "free in the end");
            SeqEq(new[] { "6432 (Docker Desktop)", "6432 (Docker Desktop); 11434 (Ollama)" }, said, "told once per answer");
            Eq(3, sleeps, "looked again three times");
            Eq(0, answers.Count, "asked until free");
        });
        Run("port gate: stops waiting when the host stops; goes on when the table can't be read", delegate
        {
            var docker = PortCheck.Owners(new[] { new Listener(6432, 35452, "com.docker.backend") }, null);
            True(!PortGate.WaitUntilFree(() => docker, t => { }, ms => true, 3000), "stopping");
            int asked = 0;
            True(PortGate.WaitUntilFree(() => { asked++; return null; }, t => { throw new InvalidOperationException("nothing taken"); }, ms => { throw new InvalidOperationException("no wait"); }, 3000), "no table");
            Eq(1, asked, "asked once");
        });
        Run("port gate: the summary names each port and its program", delegate
        {
            var o = PortCheck.Owners(new[] { new Listener(6432, 35452, "com.docker.backend"), new Listener(11434, 7, "ollama") }, null);
            Eq("6432 (Docker Desktop); 11434 (Ollama)", PortCheck.Summary(o), "summary");
        });
        Run("port gate: Windows' own TCP table can be read without an administrator", delegate
        {
            var l = PortScan.Listeners();
            True(l.Count > 0, "some listeners on this PC");
            True(l.All(x => x.Port > 0 && x.Port < 65536), "ports in range");
        });
    }

    const string RealSid = "S-1-5-21-1111111111-2222222222-3333333333-1003";

    // Runs ProfileTask's cleanup script against stand-in cmdlets: the
    // profile Get-CimInstance returns, and what got removed and unregistered.
    static List<string> RunCleanup(string sid, string localPath, bool loaded)
    {
        return RunCleanup(sid, localPath, loaded, false);
    }

    static List<string> RunCleanup(string sid, string localPath, bool loaded, bool lookupFails)
    {
        // The failing New-Object comes after the log list is made with the real one.
        string fakes = "$script:log = New-Object System.Collections.ArrayList\n" +
            (lookupFails ? "function New-Object { throw 'the LSA did not answer' }\n" : "") +
            "function Get-CimInstance { param($ClassName, $Filter) " +
            (localPath == null ? "$null" : "[pscustomobject]@{ SID = '" + sid + "'; LocalPath = '" + localPath + "'; Loaded = $" + (loaded ? "true" : "false") + " }") + " }\n" +
            "function Remove-CimInstance { param($InputObject) [void]$script:log.Add('removed ' + $InputObject.LocalPath) }\n" +
            "function Unregister-ScheduledTask { param($TaskName, $TaskPath, $Confirm, $ErrorAction) [void]$script:log.Add('unregistered ' + $TaskPath + $TaskName) }\n";
        using (var ps = PowerShell.Create())
        {
            ps.AddScript(fakes + "& {\n" + ProfileTask.CleanupScript(sid) + "\n}\n$script:log");
            var outp = ps.Invoke();
            if (ps.HadErrors) throw new InvalidOperationException("script errors: " + string.Join("; ", ps.Streams.Error.Select(e => e.ToString()).ToArray()));
            return outp.Where(x => x != null).Select(x => x.ToString()).ToList();
        }
    }

    static void ParsesAsPowerShell(string script, string what)
    {
        Token[] tokens; ParseError[] errors;
        Parser.ParseInput(script, out tokens, out errors);
        True(errors.Length == 0, what + " parses: " + string.Join("; ", errors.Select(e => e.Message).ToArray()));
    }

    static void ProfileCleanup()
    {
        Run("profile task: deletes domovoi-svc's profile once its account is gone, then itself (item 8)", delegate
        {
            SeqEq(new[] { @"removed C:\Users\domovoi-svc", @"unregistered \DomovoiRemoveProfile" }, RunCleanup(RealSid, @"C:\Users\domovoi-svc", false), "done");
            SeqEq(new[] { @"removed C:\Users\domovoi-svc.DESKTOP-ALEX", @"unregistered \DomovoiRemoveProfile" }, RunCleanup(RealSid, @"C:\Users\domovoi-svc.DESKTOP-ALEX", false), "the renamed folder too");
        });
        Run("profile task: anything else is left, and the task still goes", delegate
        {
            SeqEq(new[] { @"unregistered \DomovoiRemoveProfile" }, RunCleanup(RealSid, @"C:\Users\alex", false), "another profile");
            SeqEq(new[] { @"unregistered \DomovoiRemoveProfile" }, RunCleanup(RealSid, @"C:\Users\domovoi-svcx", false), "a look-alike");
            Eq(0, RunCleanup(RealSid, @"C:\Users\domovoi-svc", true).Count, "a loaded profile: nothing, and the task stays for the next boot");
            SeqEq(new[] { @"unregistered \DomovoiRemoveProfile" }, RunCleanup(RealSid, null, false), "no profile");
            // This PC's own account resolves: its profile is never touched.
            string mine = System.Security.Principal.WindowsIdentity.GetCurrent().User.Value;
            if (ProfileTask.IsLocalAccountSid(mine))
                SeqEq(new[] { @"unregistered \DomovoiRemoveProfile" }, RunCleanup(mine, @"C:\Users\domovoi-svc", false), "an account that exists");
        });
        Run("profile task: a lookup that fails any other way than 'no such account' does nothing and keeps the task (review item 4)", delegate
        {
            Eq(0, RunCleanup(RealSid, @"C:\Users\domovoi-svc", false, true).Count, "nothing removed, not unregistered");
            True(ProfileTask.CleanupScript(RealSid).Contains("catch [System.Security.Principal.IdentityNotMappedException]"), "only that exception means gone");
        });
        Run("profile task: only a local account's SID goes into the scripts", delegate
        {
            foreach (string bad in new[] { "S-1-5-18", "S-1-5-21-1-2-3-1003'; Remove-Item C:\\ -Recurse; '", "", null, "S-1-5-21-1-2-3" })
                Throws<ArgumentException>(delegate { ProfileTask.CleanupScript(bad); }, "refused: " + bad);
            True(ProfileTask.IsLocalAccountSid(RealSid), "the real one");
        });
        Run("profile task: registered at startup as SYSTEM, running the cleanup encoded", delegate
        {
            string reg = ProfileTask.RegisterScript(RealSid, Environment.SystemDirectory);
            True(reg.Contains("-Execute '" + Environment.SystemDirectory + @"\WindowsPowerShell\v1.0\powershell.exe'"), "Windows' own powershell.exe, by the helper's path");
            True(!reg.Contains("$env:"), "nothing from the environment (review item 3)");
            Throws<ArgumentException>(delegate { ProfileTask.RegisterScript(RealSid, @"C:\Win'dows\System32"); }, "a quote in the folder");
            Throws<ArgumentException>(delegate { ProfileTask.RegisterScript(RealSid, @"%SystemRoot%\System32"); }, "a variable");
            ParsesAsPowerShell(reg, "the register script");
            ParsesAsPowerShell(ProfileTask.CleanupScript(RealSid), "the cleanup script");
            True(reg.Contains("New-ScheduledTaskTrigger -AtStartup") && reg.Contains("-UserId 'S-1-5-18'") && reg.Contains("-TaskName 'DomovoiRemoveProfile'"), "startup, SYSTEM, its name");
            int at = reg.IndexOf("-EncodedCommand ", StringComparison.Ordinal) + "-EncodedCommand ".Length;
            string b64 = reg.Substring(at, reg.IndexOf('\'', at) - at);
            Eq(ProfileTask.CleanupScript(RealSid), System.Text.Encoding.Unicode.GetString(Convert.FromBase64String(b64)), "the encoded command is the cleanup script");
        });
    }

    static void LogCopies()
    {
        Run("redact: no setup code, no password in a URL (item 4)", delegate
        {
            Eq("code [hidden] here", Redact.Line("code acorn-apple-arrow-autumn-badge-baker-basil-beach here"), "setup code");
            Eq("postgresql://domovoi:[hidden]@127.0.0.1:6432/domovoi", Redact.Line("postgresql://domovoi:s3cret-Pass@127.0.0.1:6432/domovoi"), "URL password");
            Eq("https://github.com/coders-farm-official/domovoi", Redact.Line("https://github.com/coders-farm-official/domovoi"), "a plain URL stays");
            Eq("installed /etc/sudoers.d/domovoi-update: domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl", Redact.Line("installed /etc/sudoers.d/domovoi-update: domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl"), "an ordinary line stays");
        });
        Run("redact: keys, headers and the other URL forms (review item 5)", delegate
        {
            Eq("password=[hidden] next", Redact.Line("password=hunter2 next"), "password=");
            Eq("POSTGRES_PASSWORD=[hidden]", Redact.Line("POSTGRES_PASSWORD=Xy7%q"), "*_PASSWORD=");
            Eq("LETTA_TOKEN = [hidden]", Redact.Line("LETTA_TOKEN = abc123"), "*_TOKEN with spaces");
            Eq("token=[hidden] rest", Redact.Line("token=abc rest"), "token=");
            Eq("secret: [hidden]", Redact.Line("secret: s3cr3t"), "secret:");
            Eq("{\"api_key\": \"[hidden]\", \"n\": 1}", Redact.Line("{\"api_key\": \"k-123\", \"n\": 1}"), "a JSON key, quoted");
            Eq("Authorization: Bearer [hidden]", Redact.Line("Authorization: Bearer eyJhbGciOi.abc.def"), "Authorization: Bearer");
            Eq("Authorization: Basic [hidden]", Redact.Line("Authorization: Basic ZG9tb3ZvaTpwdw=="), "Authorization: Basic");
            Eq("sent bearer [hidden] to x", Redact.Line("sent bearer abcdefghijkl to x"), "a bare Bearer token");
            Eq("postgresql://:[hidden]@127.0.0.1/db", Redact.Line("postgresql://:pw@127.0.0.1/db"), "no user name");
            Eq("https://[hidden]@github.com/x", Redact.Line("https://ghp_abc123@github.com/x"), "a token alone");
            Eq("postgresql://domovoi:[hidden]@h/db", Redact.Line("postgresql://domovoi:pa/ss@h/db"), "a / in the password");
            foreach (string keep in new[]
            {
                "provision: created the service user domovoi (password login locked)",
                "Collecting tokenizers==0.23.2 (from -r requirements-linux-py314.lock (line 2816))",
                "Please set a HF_TOKEN to enable higher rate limits",
                "/opt/domovoi/domovoi/.env: LETTA_TOKEN and SEARXNG_SECRET already set; left untouched",
                "Downloading https://download-r2.pytorch.org/whl/cpu/torch-2.14.1%2Bcpu-cp314-cp314-manylinux_2_28_x86_64.whl (196.3 MB)",
                "authorization failed for the request",
            })
                Eq(keep, Redact.Line(keep), "kept: " + keep);
        });
        Run("copy logs: a new folder the user can open, every line redacted, links refused", delegate
        {
            string root = Scratch(), victim = Scratch();
            string tmpLog = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "domovoi-uninstall-20991231-235959.log");
            try
            {
                System.IO.File.WriteAllText(tmpLog, "2099-12-31 23:59:59 helper uninstall\r\nleaked acorn-apple-arrow-autumn-badge-baker-basil-beach\r\nurl postgresql://u:pw@h/db\r\n");
                var o = new Domovoi.Windows.Helper.Output(null);
                string dest = System.IO.Path.Combine(root, "Domovoi logs");
                Domovoi.Windows.Helper.Logs.Copy(o, dest, "uninstall", UninstallLogOnly(tmpLog), NoCoreLog, null, DateTime.Now);
                var made = System.IO.Directory.GetDirectories(dest);
                Eq(1, made.Length, "one new folder");
                True(System.IO.Path.GetFileName(made[0]).StartsWith("uninstall-", StringComparison.Ordinal), "named for the uninstall: " + made[0]);
                string copy = System.IO.File.ReadAllText(System.IO.Path.Combine(made[0], "domovoi-uninstall-20991231-235959.log"));
                True(copy.Contains("helper uninstall") && !copy.Contains("acorn-apple") && !copy.Contains(":pw@") && copy.Contains("[hidden]"), "redacted: " + copy);
                True(System.IO.File.Exists(System.IO.Path.Combine(made[0], "README.txt")), "a README");
                Domovoi.Windows.Helper.Logs.Copy(o, dest, "uninstall", UninstallLogOnly(tmpLog), NoCoreLog, null, DateTime.Now);
                Eq(2, System.IO.Directory.GetDirectories(dest).Length, "a second copy goes to a second folder");

                string link = System.IO.Path.Combine(root, "linked logs");
                Junction(link, victim);
                Throws<InvalidOperationException>(delegate { Domovoi.Windows.Helper.Logs.Copy(o, link, "install", NoSources(), NoCoreLog, null, DateTime.Now); }, "a link as the folder");
                Eq(0, System.IO.Directory.GetFileSystemEntries(victim).Length, "nothing written through it");
            }
            finally
            {
                System.IO.File.Delete(tmpLog);
                Domovoi.Windows.Helper.Folders.DeleteTree(root);
                if (System.IO.Directory.Exists(victim)) System.IO.Directory.Delete(victim, true);
            }
        });
        Run("copy logs: the folder and the sources are checked on their handles, never through a link (review item 2, C12)", delegate
        {
            string root = Scratch(), victim = Scratch();
            try
            {
                string link = System.IO.Path.Combine(root, "link");
                Junction(link, victim);
                Throws<InvalidOperationException>(delegate { Domovoi.Windows.Helper.Logs.OpenDir(link).Dispose(); }, "a junction as a folder");
                using (var h = Domovoi.Windows.Helper.Logs.OpenDir(victim)) True(!h.IsInvalid, "a real folder opens");
                // Held without delete sharing: it can't be moved or swapped for a link.
                bool moved = true;
                using (Domovoi.Windows.Helper.Logs.OpenDir(victim))
                {
                    try { System.IO.Directory.Move(victim, victim + "-moved"); }
                    catch (Exception) { moved = false; }
                }
                True(!moved && System.IO.Directory.Exists(victim), "can't be moved while held");
                string x = System.IO.Path.Combine(victim, "x.log");
                System.IO.File.WriteAllText(x, "secret");
                using (var h = Domovoi.Windows.Helper.Logs.OpenSource(x)) True(!h.IsInvalid, "a plain file opens");
                Throws<InvalidOperationException>(delegate { Domovoi.Windows.Helper.Logs.OpenSource(System.IO.Path.Combine(link, "x.log")).Dispose(); }, "a source through a junction");
                string hard = System.IO.Path.Combine(root, "hard.log");
                var psi = new System.Diagnostics.ProcessStartInfo(System.IO.Path.Combine(Environment.SystemDirectory, "cmd.exe"),
                    "/c mklink /H " + CommandLine.Quote(hard) + " " + CommandLine.Quote(x));
                psi.UseShellExecute = false; psi.CreateNoWindow = true; psi.RedirectStandardOutput = true;
                using (var pr = System.Diagnostics.Process.Start(psi)) { pr.StandardOutput.ReadToEnd(); pr.WaitForExit(); }
                True(System.IO.File.Exists(hard), "made a hard link");
                Throws<InvalidOperationException>(delegate { Domovoi.Windows.Helper.Logs.OpenSource(hard).Dispose(); }, "a source with a second name");
                Throws<InvalidOperationException>(delegate { Domovoi.Windows.Helper.Logs.OpenSource(x).Dispose(); }, "its other name too");
            }
            finally
            {
                Domovoi.Windows.Helper.Folders.DeleteTree(root);
                if (System.IO.Directory.Exists(victim)) System.IO.Directory.Delete(victim, true);
            }
        });
        Run("copy logs: a link already at the new folder's name is never written through (review C12)", delegate
        {
            string root = Scratch(), victim = Scratch();
            try
            {
                var at = new DateTime(2099, 12, 31, 23, 59, 59);
                string dest = System.IO.Path.Combine(root, "Domovoi logs");
                System.IO.Directory.CreateDirectory(dest);
                Junction(System.IO.Path.Combine(dest, "install-20991231-235959"), victim);
                Domovoi.Windows.Helper.Logs.Copy(new Domovoi.Windows.Helper.Output(null), dest, "install", NoSources(), NoCoreLog, null, at);
                Eq(0, System.IO.Directory.GetFileSystemEntries(victim).Length, "nothing written through the link");
                True(System.IO.File.Exists(System.IO.Path.Combine(dest, "install-20991231-235959-2", "README.txt")), "the copy went to the next name");
                string readme = System.IO.File.ReadAllText(System.IO.Path.Combine(dest, "install-20991231-235959-2", "README.txt"));
                True(readme.Contains("copied by Domovoi Setup on") && readme.Contains("as far as Setup can recognise them"), "README: " + readme);
                Domovoi.Windows.Helper.Logs.Copy(new Domovoi.Windows.Helper.Output(null), dest, "uninstall", NoSources(), NoCoreLog, null, at);
                True(System.IO.File.ReadAllText(System.IO.Path.Combine(dest, "uninstall-20991231-235959", "README.txt")).Contains("copied by Domovoi's uninstaller on"), "the uninstaller's wording (review item 8)");
            }
            finally
            {
                Domovoi.Windows.Helper.Folders.DeleteTree(root);
                if (System.IO.Directory.Exists(victim)) System.IO.Directory.Delete(victim, true);
            }
        });
        Run("pending deletes: an earlier uninstall's restart work under Domovoi's folders is found (review item 7)", delegate
        {
            var roots = new[] { @"C:\ProgramData\Domovoi", @"C:\Program Files\Domovoi" };
            var entries = new List<string>
            {
                @"\??\C:\ProgramData\Domovoi\logs\host.log", "",
                @"\??\C:\ProgramData\Domovoi", "",
                @"\??\C:\Windows\Temp\x.tmp", "",
                @"\??\C:\ProgramData\Domovoi-kept-20261012\ext4.vhdx", "",
                @"\??\C:\Temp\new.dll", @"!\??\C:\Program Files\Domovoi\DomovoiHost.exe",
            };
            Eq(3, PendingDeletes.Under(entries, roots), "two deletes and a rename into Program Files");
            Eq(0, PendingDeletes.Under(new List<string> { @"\??\C:\Users\alex\x", "" }, roots), "someone else's");
            Eq(0, PendingDeletes.Under(null, roots), "none");
            Eq(1, PendingDeletes.Under(new List<string> { @"\??\c:\programdata\domovoi\state\install.log" }, roots), "case, and a list cut short");
        });
        Run("copy logs: only the uninstall's own log from the temp folder", delegate
        {
            Throws<ArgumentException>(delegate { Domovoi.Windows.Helper.Logs.Sources(@"C:\Windows\System32\config\SAM"); }, "another file");
            Throws<ArgumentException>(delegate { Domovoi.Windows.Helper.Logs.Sources(@"C:\Users\Public\domovoi-uninstall-20261012-133801.log"); }, "another folder");
            True(Domovoi.Windows.Helper.Logs.Sources(null).Any(s => s.Value == "provision.log"), "provision.log is among them");
        });
    }

    // The copy's inputs for tests that aren't about them: nothing from the
    // PC's own C:\ProgramData\Domovoi, and no request to its DomovoiHost.
    static readonly string NoCoreLog = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "domovoi-tests-no-such-folder", "core.log");

    static List<KeyValuePair<string, string>> NoSources() { return new List<KeyValuePair<string, string>>(); }

    static List<KeyValuePair<string, string>> UninstallLogOnly(string tmpLog)
    {
        string name = System.IO.Path.GetFileName(tmpLog);
        return Domovoi.Windows.Helper.Logs.Sources(tmpLog).Where(s => s.Value == name).ToList();
    }

    // The copy's core.log, from a copy made with the given core.log and refresh.
    static string CopyWithCoreLog(string root, string coreLog, Func<string> refresh, string kind)
    {
        string dest = System.IO.Path.Combine(root, "Domovoi logs");
        Domovoi.Windows.Helper.Logs.Copy(new Domovoi.Windows.Helper.Output(null), dest, kind, NoSources(), coreLog, refresh, DateTime.Now);
        var made = System.IO.Directory.GetDirectories(dest);
        Eq(1, made.Length, "one new folder");
        string copy = System.IO.Path.Combine(made[0], "core.log");
        True(System.IO.File.Exists(copy), "the copy has a core.log");
        return System.IO.File.ReadAllText(copy);
    }

    static Domovoi.Windows.Host.WslResult Wsl(int exit, bool timedOut, string stdout, string stderr)
    {
        var r = new Domovoi.Windows.Host.WslResult();
        r.ExitCode = exit; r.TimedOut = timedOut; r.Stdout = stdout; r.Stderr = stderr;
        return r;
    }

    const string JournalOut = "==== domovoi-core (journalctl --since -24h, at most 5000 lines)\n"
        + "2026-11-09T03:12:44+0000 domovoi python[812]: INFO turn started room=kitchen\n"
        + "2026-11-09T03:12:45+0000 domovoi python[812]: INFO 192.168.1.41:5123 - \"GET /v1/stream?token=s3cr3tT0ken HTTP/1.1\" 101\n"
        + "==== domovoi-web (journalctl --since -24h, at most 5000 lines)\n"
        + "2026-11-09T03:12:46+0000 domovoi python[901]: WARNING Authorization: Bearer eyJhbGciOi.abc.def rejected\n";

    static void CoreLogCopies()
    {
        Run("core log: the journal command (units, 24 h, lines, newest bytes, as root, no shell from outside)", delegate
        {
            string[] a = CoreLog.WslArgs(Paths.DistroName);
            SeqEq(new[] { "-d", "domovoi", "-u", "root", "--cd", "/", "--exec", "/bin/sh", "-c" }, a.Take(9), "wsl.exe arguments");
            Eq(10, a.Length, "one script argument");
            string s = a[9];
            Eq(CoreLog.Script(), s, "the fixed script");
            True(s.StartsWith("for u in domovoi-core domovoi-web; do ", StringComparison.Ordinal), "both units: " + s);
            True(s.Contains("/usr/bin/journalctl -u \"$u\" --since=-24h -n 5000 --no-pager -o short-iso 2>&1 | /usr/bin/tail -c 2097152"), "bounded: " + s);
            True(s.Contains("echo \"==== $u "), "a header per unit");
            True(CoreLog.TimeoutMs <= 15000 && CoreLog.HelperWaitSec * 1000 > CoreLog.TimeoutMs, "the helper waits longer than the host's read");
            True(CoreLog.MaxOutputChars > 2 * CoreLog.BytesPerUnit, "the output backstop is above both units' cut");
        });
        Run("core log: rendered with a header, every line redacted, long lines cut", delegate
        {
            string why;
            string longLine = "2026-11-09T03:00:00+0000 domovoi python[812]: " + new string('x', 9000);
            string t = CoreLog.Render(0, false, JournalOut + longLine + "\n", "", new DateTime(2026, 11, 9, 3, 13, 0), out why);
            True(t != null, "rendered: " + why);
            True(t.StartsWith("# Domovoi's core log: journalctl of domovoi-core and domovoi-web, the last 24 hours, at most 5000 lines each, read by DomovoiHost at 2026-11-09 03:13:00 UTC\r\n", StringComparison.Ordinal), "header: " + t.Substring(0, 200));
            True(t.Contains("turn started room=kitchen\r\n") && t.Contains("==== domovoi-web"), "the lines, CRLF");
            True(!t.Contains("s3cr3tT0ken") && t.Contains("?token=[hidden]"), "a token in a URL");
            True(!t.Contains("eyJhbGciOi") && t.Contains("Authorization: Bearer [hidden]"), "an Authorization header");
            True(t.Contains(new string('x', 100) + " [cut]") && !t.Contains(new string('x', CoreLog.MaxLineChars + 1)), "a long line is cut");
            True(!t.Contains("\n\n") && t.EndsWith("\r\n", StringComparison.Ordinal) && !t.Replace("\r\n", "").Contains("\n"), "CRLF only");
        });
        Run("core log: a timeout, a failed wsl.exe or no output gives no file and says why", delegate
        {
            string why;
            True(CoreLog.Render(-1, true, JournalOut, "", DateTime.UtcNow, out why) == null, "timed out");
            Eq("journalctl did not answer within 15 s", why, "timeout reason");
            True(CoreLog.Render(1, false, "", "There is no distribution with the supplied name.\n", DateTime.UtcNow, out why) == null, "failed");
            Eq("wsl.exe exit 1: There is no distribution with the supplied name.", why, "failure reason");
            True(CoreLog.Render(0, false, "", "", DateTime.UtcNow, out why) == null, "empty");
            Eq("the journal read printed nothing", why, "empty reason");
            True(CoreLog.Render(0, false, "==== domovoi-core (x)\n-- No entries --\n==== domovoi-web (x)\n-- No entries --\n", "", DateTime.UtcNow, out why) != null, "an empty journal is still a file that says so");
        });
        Run("core log: DomovoiHost's writer (the arguments, the bounds, a failed read keeps the last copy)", delegate
        {
            string root = Scratch();
            try
            {
                string path = System.IO.Path.Combine(root, "core.log");
                IEnumerable<string> gotArgs = null; int gotTimeout = 0, gotMax = 0;
                string why = Domovoi.Windows.Host.CoreJournal.Refresh(delegate (IEnumerable<string> args, int timeoutMs, int maxOutput)
                {
                    gotArgs = args.ToList(); gotTimeout = timeoutMs; gotMax = maxOutput;
                    return Wsl(0, false, JournalOut, "");
                }, path, new DateTime(2026, 11, 9, 3, 13, 0));
                True(why == null, "written: " + why);
                SeqEq(CoreLog.WslArgs("domovoi"), gotArgs, "the arguments");
                Eq(CoreLog.TimeoutMs, gotTimeout, "the timeout");
                Eq(CoreLog.MaxOutputChars, gotMax, "the output bound");
                string first = System.IO.File.ReadAllText(path);
                True(first.Contains("turn started") && !first.Contains("s3cr3tT0ken"), "redacted on the way in: " + first);
                Eq(0, System.IO.Directory.GetFiles(root, ".core.log.*").Length, "no temporary file left");

                why = Domovoi.Windows.Host.CoreJournal.Refresh(delegate { return Wsl(-1, true, "", ""); }, path, DateTime.UtcNow);
                Eq("journalctl did not answer within 15 s", why, "a timeout");
                Eq(first, System.IO.File.ReadAllText(path), "the last copy stays");
                why = Domovoi.Windows.Host.CoreJournal.Refresh(delegate { throw new System.ComponentModel.Win32Exception(2); }, path, DateTime.UtcNow);
                True(why != null && why.StartsWith("wsl.exe could not be started: ", StringComparison.Ordinal), "wsl.exe missing: " + why);
                Eq(first, System.IO.File.ReadAllText(path), "still the last copy");
                why = Domovoi.Windows.Host.CoreJournal.Refresh(delegate { return Wsl(0, false, JournalOut, ""); }, System.IO.Path.Combine(root, "gone", "core.log"), DateTime.UtcNow);
                True(why != null && why.StartsWith("can't write ", StringComparison.Ordinal), "an unwritable place: " + why);
            }
            finally { Domovoi.Windows.Helper.Folders.DeleteTree(root); }
        });
        Run("core log: copy logs takes a fresh copy, redacted", delegate
        {
            string root = Scratch();
            try
            {
                string core = System.IO.Path.Combine(root, "core.log");
                System.IO.File.WriteAllText(core, "2026-11-09T03:12:45+0000 x: GET /v1/stream?token=leakme HTTP/1.1\r\ncode acorn-apple-arrow-autumn-badge-baker-basil-beach\r\nturn ok\r\n");
                int asked = 0;
                string copy = CopyWithCoreLog(root, core, delegate { asked++; return null; }, "install");
                Eq(1, asked, "asked DomovoiHost once");
                True(copy.StartsWith("2026-11-09T03:12:45", StringComparison.Ordinal), "no note on a fresh copy: " + copy);
                True(copy.Contains("turn ok") && !copy.Contains("leakme") && !copy.Contains("acorn-apple"), "redacted: " + copy);
            }
            finally { Domovoi.Windows.Helper.Folders.DeleteTree(root); }
        });
        Run("core log: no fresh copy, the last one with a note saying why and when", delegate
        {
            string root = Scratch();
            try
            {
                string core = System.IO.Path.Combine(root, "core.log");
                System.IO.File.WriteAllText(core, "older line\r\n");
                System.IO.File.SetLastWriteTime(core, new DateTime(2026, 11, 9, 2, 50, 0));
                string copy = CopyWithCoreLog(root, core, delegate { return "DomovoiHost did not answer within 20 s"; }, "install");
                Eq("NOTE: a fresh copy of Domovoi's core log could not be read (DomovoiHost did not answer within 20 s); this is DomovoiHost's last copy, from 2026-11-09 02:50:00.\r\nolder line\r\n", copy, "stale note");
            }
            finally { Domovoi.Windows.Helper.Folders.DeleteTree(root); }
            root = Scratch();
            try
            {
                string core = System.IO.Path.Combine(root, "core.log");
                System.IO.File.WriteAllText(core, "older line\r\n");
                System.IO.File.SetLastWriteTime(core, new DateTime(2026, 11, 9, 2, 50, 0));
                string copy = CopyWithCoreLog(root, core, null, "uninstall");
                Eq("NOTE: this is DomovoiHost's last copy of Domovoi's core log, from 2026-11-09 02:50:00.\r\nolder line\r\n", copy, "the uninstaller's copy");
            }
            finally { Domovoi.Windows.Helper.Folders.DeleteTree(root); }
        });
        Run("core log: none at all, a note line and the copy goes on", delegate
        {
            string root = Scratch();
            try
            {
                string core = System.IO.Path.Combine(root, "core.log");
                string copy = CopyWithCoreLog(root, core, delegate { return "Domovoi's Linux system is not running"; }, "install");
                True(copy.StartsWith("NOTE: Domovoi's core log (the journal of domovoi-core and domovoi-web) could not be read: DomovoiHost has not written ", StringComparison.Ordinal), copy);
                True(copy.TrimEnd().EndsWith("; asking DomovoiHost for a fresh one: Domovoi's Linux system is not running", StringComparison.Ordinal), copy);
                Eq(1, copy.Replace("\r\n", "\n").TrimEnd('\n').Split('\n').Length, "one line");
                var made = System.IO.Directory.GetDirectories(System.IO.Path.Combine(root, "Domovoi logs"));
                True(System.IO.File.Exists(System.IO.Path.Combine(made[0], "README.txt")), "the copy finished");
            }
            finally { Domovoi.Windows.Helper.Folders.DeleteTree(root); }
        });
        Run("core log: a huge core.log is cut at 8 MB", delegate
        {
            string root = Scratch();
            try
            {
                string core = System.IO.Path.Combine(root, "core.log");
                var line = new string('y', 1023) + "\n";
                using (var w = new System.IO.StreamWriter(core, false, new System.Text.UTF8Encoding(false)))
                    for (int i = 0; i < 9 * 1024; i++) w.Write(line);
                string copy = CopyWithCoreLog(root, core, delegate { return null; }, "install");
                True(copy.EndsWith("NOTE: core.log is larger than 8 MB; the rest is left out.\r\n", StringComparison.Ordinal), "a cut note");
                True(copy.Length < CoreLog.MaxCopyBytes + 64 * 1024, "bounded: " + Inv.S(copy.Length));
                True(copy.Length > CoreLog.MaxCopyBytes - 64 * 1024, "the first 8 MB are there: " + Inv.S(copy.Length));
            }
            finally { Domovoi.Windows.Helper.Folders.DeleteTree(root); }
        });
        Run("core log: a core.log through a link, or with a second name, is refused like the others", delegate
        {
            string root = Scratch(), victim = Scratch();
            try
            {
                System.IO.File.WriteAllText(System.IO.Path.Combine(victim, "core.log"), "NOT-FOR-THE-COPY\r\n");
                string link = System.IO.Path.Combine(root, "logs");
                Junction(link, victim);
                string copy = CopyWithCoreLog(root, System.IO.Path.Combine(link, "core.log"), delegate { return null; }, "install");
                True(!copy.Contains("NOT-FOR-THE-COPY"), "nothing read through the junction");
                True(copy.StartsWith("NOTE: Domovoi's core log (the journal of domovoi-core and domovoi-web) could not be read: not copying it: ", StringComparison.Ordinal), copy);
            }
            finally
            {
                Domovoi.Windows.Helper.Folders.DeleteTree(root);
                if (System.IO.Directory.Exists(victim)) System.IO.Directory.Delete(victim, true);
            }
            root = Scratch(); victim = Scratch();
            try
            {
                string x = System.IO.Path.Combine(victim, "secret.txt");
                System.IO.File.WriteAllText(x, "NOT-FOR-THE-COPY\r\n");
                string logs = System.IO.Path.Combine(root, "logs");
                System.IO.Directory.CreateDirectory(logs);
                string hard = System.IO.Path.Combine(logs, "core.log");
                var psi = new System.Diagnostics.ProcessStartInfo(System.IO.Path.Combine(Environment.SystemDirectory, "cmd.exe"),
                    "/c mklink /H " + CommandLine.Quote(hard) + " " + CommandLine.Quote(x));
                psi.UseShellExecute = false; psi.CreateNoWindow = true; psi.RedirectStandardOutput = true;
                using (var pr = System.Diagnostics.Process.Start(psi)) { pr.StandardOutput.ReadToEnd(); pr.WaitForExit(); }
                True(System.IO.File.Exists(hard), "made a hard link");
                string copy = CopyWithCoreLog(root, hard, delegate { return null; }, "install");
                True(!copy.Contains("NOT-FOR-THE-COPY") && copy.Contains("names (hard links)"), copy);
            }
            finally
            {
                Domovoi.Windows.Helper.Folders.DeleteTree(root);
                if (System.IO.Directory.Exists(victim)) System.IO.Directory.Delete(victim, true);
            }
        });
        Run("redact: journal-style lines (access logs, JSON, headers)", delegate
        {
            Eq("INFO 192.168.1.41:5123 - \"GET /v1/stream?token=[hidden] HTTP/1.1\" 101", Redact.Line("INFO 192.168.1.41:5123 - \"GET /v1/stream?token=abc123def HTTP/1.1\" 101"), "a token in a query string");
            Eq("GET /api/x?a=1&device_token=[hidden] 200", Redact.Line("GET /api/x?a=1&device_token=zz9 200"), "a *_token parameter");
            Eq("body {\"device_token\": \"[hidden]\"}", Redact.Line("body {\"device_token\": \"abc\"}"), "a JSON token");
            Eq("X-Domovoi-Token: [hidden]", Redact.Line("X-Domovoi-Token: abc123"), "a token header");
            Eq("headers authorization=Bearer [hidden]", Redact.Line("headers authorization=Bearer abcdefghij"), "authorization=");
            Eq("2026-11-09T03:12:44+0000 domovoi python[812]: setup code is [hidden]", Redact.Line("2026-11-09T03:12:44+0000 domovoi python[812]: setup code is acorn-apple-arrow-autumn-badge-baker-basil-beach"), "a setup code");
            foreach (string keep in new[]
            {
                "2026-11-09T03:12:44+0000 domovoi python[812]: INFO turn started room=kitchen",
                "2026-11-09T03:12:44+0000 domovoi systemd[1]: Started domovoi-core.service - Domovoi core.",
                "-- No entries --",
            })
                Eq(keep, Redact.Line(keep), "kept: " + keep);
        });
    }

    const string OldSid = "S-1-5-21-1111111111-2222222222-3333333333-1003";
    const string NewSid = "S-1-5-21-1111111111-2222222222-3333333333-1005";
    const string MineSid = "S-1-5-21-1111111111-2222222222-3333333333-1001";

    static void LeftoverProfileTests()
    {
        Func<string, bool> accounts = sid => sid != OldSid;
        Run("leftover profile: a first install, and a live install in domovoi-svc.<PC name>, find nothing", delegate
        {
            Eq(0, LeftoverProfiles.Find(new[] { new ProfileEntry(MineSid, @"C:\Users\alex") }, new[] { @"C:\Users\alex" }, accounts).Count, "first install");
            Eq(0, LeftoverProfiles.Find(new[] { new ProfileEntry(MineSid, @"C:\Users\alex"), new ProfileEntry(NewSid, @"C:\Users\domovoi-svc.DESKTOP-ALEX") },
                new[] { @"C:\Users\alex", @"C:\Users\domovoi-svc.DESKTOP-ALEX" }, accounts).Count, "the account's own profile under another name");
        });
        Run("leftover profile: this PC today, the deleted account's domovoi-svc folder, with the one-line cleanup", delegate
        {
            var found = LeftoverProfiles.Find(new[] { new ProfileEntry(MineSid, @"C:\Users\alex"), new ProfileEntry(OldSid, @"C:\Users\domovoi-svc") },
                new[] { @"C:\Users\alex", @"C:\Users\domovoi-svc" }, accounts);
            Eq(1, found.Count, "one");
            Eq(@"C:\Users\domovoi-svc", found[0].Path, "path");
            Eq("Get-CimInstance Win32_UserProfile -Filter \"SID='" + OldSid + "'\" | Remove-CimInstance", found[0].Cleanup(), "cleanup");
            ParsesAsPowerShell(found[0].Cleanup(), "the cleanup");
            string m = LeftoverProfiles.Message(found);
            True(m.Contains(@"C:\Users\domovoi-svc") && m.Contains("Restart Windows") && m.Contains(found[0].Cleanup()) && m.Contains("Check again"), m);
        });
        Run("leftover profile: the old one next to a new install's domovoi-svc.<PC name>: only the old one", delegate
        {
            var found = LeftoverProfiles.Find(new[] { new ProfileEntry(OldSid, @"C:\Users\domovoi-svc"), new ProfileEntry(NewSid, @"C:\Users\domovoi-svc.DESKTOP-ALEX") },
                new[] { @"C:\Users\domovoi-svc", @"C:\Users\domovoi-svc.DESKTOP-ALEX" }, accounts);
            SeqEq(new[] { @"C:\Users\domovoi-svc" }, found.Select(f => f.Path), "the old one");
        });
        Run("leftover profile: a folder with no profile entry; look-alikes and other people left alone", delegate
        {
            var found = LeftoverProfiles.Find(new ProfileEntry[0], new[] { @"C:\Users\domovoi-svc\", @"C:\Users\domovoi-svcx", @"C:\Users\alex" }, accounts);
            Eq(1, found.Count, "one");
            True(found[0].Sid == null, "no SID");
            Eq(@"Remove-Item -LiteralPath 'C:\Users\domovoi-svc' -Recurse -Force", found[0].Cleanup(), "cleanup");
            ParsesAsPowerShell(found[0].Cleanup(), "the cleanup");
            Eq(0, LeftoverProfiles.Find(new[] { new ProfileEntry(OldSid, @"C:\Users\alex") }, new string[0], accounts).Count, "a deleted account that isn't domovoi-svc");
        });
        Run("preflight: an update that has the Ubuntu image doesn't check the image's host", delegate
        {
            var all = Domovoi.Windows.Helper.Preflight.HostsToCheck(false);
            var update = Domovoi.Windows.Helper.Preflight.HostsToCheck(true);
            True(all.Contains("releases.ubuntu.com"), "a fresh install checks it");
            True(!update.Contains("releases.ubuntu.com"), "an update with the image skips it");
            Eq(all.Count - 1, update.Count, "only that host is dropped");
            True(update.Contains("archive.ubuntu.com") && update.Contains("github.com"), "the hosts the update still uses stay");
        });
        Run("leftover profile: the account lookup Setup uses; .wslconfig goes into the real profile", delegate
        {
            True(Domovoi.Windows.Helper.Preflight.SidIsAnAccount(System.Security.Principal.WindowsIdentity.GetCurrent().User.Value), "this account");
            True(!Domovoi.Windows.Helper.Preflight.SidIsAnAccount("S-1-5-21-1-2-3-4000"), "no such account");
            Eq(@"C:\Users\domovoi-svc.DESKTOP-ALEX\.wslconfig", WslConfig.FileIn(@"C:\Users\domovoi-svc.DESKTOP-ALEX"), "where Windows made the profile");
            Throws<ArgumentException>(delegate { WslConfig.FileIn(""); }, "no profile");
            True(UninstallFacts.IsDomovoiProfilePath(@"C:\Users\domovoi-svc.DESKTOP-ALEX"), "the uninstall recognises it");
        });
    }

    // -- the satellite Setup DomovoiHost places (2026-10-21) ---------------

    static void SatelliteSetupTests()
    {
        string sha = new string('a', 64);
        Run("satellite setup: the record is the core's shape", delegate
        {
            Eq("{\"sha256\": \"" + sha + "\", \"size\": 3252640, \"release\": \"bundled\", \"fetched_at\": 1761000000}",
               SatelliteSetupPlacement.Record(sha, 3252640, 1761000000), "record");
            bool threw = false;
            try { SatelliteSetupPlacement.Record("not-hex'; rm -rf /", 1, 1); } catch (ArgumentException) { threw = true; }
            True(threw, "only a SHA-256 goes into the script");
        });
        Run("satellite setup: placed in the core's cache, then the record, then checked", delegate
        {
            string s = SatelliteSetupPlacement.PlaceScript(sha, 5, 6);
            string d = "/home/domovoi/.domovoi/satellite_media/cache/satellite_setup";
            True(s.StartsWith("set -e; umask 022; mkdir -p " + d + "; cat > "), s);
            True(s.IndexOf("mv -f " + d + "/.DomovoiSatelliteSetup.exe.placing " + d + "/DomovoiSatelliteSetup.exe") > 0, "moved in");
            True(s.IndexOf("/DomovoiSatelliteSetup.exe;") < s.IndexOf("/setup.json;"), "the file before its record");
            True(s.EndsWith("sha256sum " + d + "/DomovoiSatelliteSetup.exe"), "checked");
            Eq("sha256sum " + d + "/DomovoiSatelliteSetup.exe 2>/dev/null; echo; cat " + d + "/setup.json 2>/dev/null; true",
               SatelliteSetupPlacement.CheckScript(), "check");
        });
        Run("satellite setup: placed only when the file and its record both match", delegate
        {
            string file = sha + "  /home/domovoi/.domovoi/satellite_media/cache/satellite_setup/DomovoiSatelliteSetup.exe\n\n";
            string rec = SatelliteSetupPlacement.Record(sha, 5, 6);
            True(SatelliteSetupPlacement.IsPlaced(file + rec, sha, 5), "both match");
            True(!SatelliteSetupPlacement.IsPlaced(file, sha, 5), "the record missing");
            True(!SatelliteSetupPlacement.IsPlaced("\n" + rec, sha, 5), "the file missing");
            True(!SatelliteSetupPlacement.IsPlaced(file + SatelliteSetupPlacement.Record(new string('b', 64), 5, 6), sha, 5), "the record names another file");
            True(!SatelliteSetupPlacement.IsPlaced(file + rec, sha, 7), "another size");
            True(!SatelliteSetupPlacement.IsPlaced(file + rec.Replace("bundled", "v9.9.9"), sha, 5), "fetched, not bundled");
            True(!SatelliteSetupPlacement.IsPlaced(new string('b', 64) + file.Substring(64) + rec, sha, 5), "another file");
        });
        Run("satellite setup: none beside the host is none", delegate
        {
            string dir = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "domovoi-sat-" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(dir);
            try
            {
                True(SatelliteSetupPlacement.Source(dir) == null, "absent");
                System.IO.File.WriteAllBytes(System.IO.Path.Combine(dir, "DomovoiSatelliteSetup.exe"), new byte[] { 77, 90 });
                Eq(System.IO.Path.Combine(dir, "DomovoiSatelliteSetup.exe"), SatelliteSetupPlacement.Source(dir), "present");
            }
            finally { System.IO.Directory.Delete(dir, true); }
        });
    }

    static void UninstallTexts()
    {
        Run("uninstall progress: N|TOTAL|What it does (item 7)", delegate
        {
            var l = FullLedger();
            var plan = UninstallPlan.Make(l, FullFacts(l));
            string line = UninstallProgress.Line(2, plan.Count, plan[2]);
            Eq("3|" + plan.Count + "|Unregister the 'domovoi' WSL distro as domovoi-svc (deletes its disk)", line, "line");
            var firewall = new UninstallStep(UninstallKind.RemoveFirewallRules, "a|b");
            True(UninstallProgress.Line(0, 1, firewall).Split('|').Length == 3, "a | in the text can't add a field");
        });
        Run("uninstall texts: nothing tells the user to run an uninstaller that is gone (item 9)", delegate
        {
            foreach (UninstallKind k in UninstallRunner.Inert)
            {
                var s = new UninstallStep(k, @"C:\x");
                s.Sids.Add(Sid);
                string how = new UninstallLeftover(s, "x").HowToRemove();
                True(how.IndexOf("uninstaller again", StringComparison.OrdinalIgnoreCase) < 0 && how.IndexOf("run the uninstaller", StringComparison.OrdinalIgnoreCase) < 0, k + ": " + how);
            }
        });
        Run("uninstall messages: plain words, the details stay in the log (owner 2026-10-12)", delegate
        {
            var profile = new RestartNote(@"the profile C:\Users\domovoi-svc: Windows still had it open (error 32), so a one-time startup task, DomovoiRemoveProfile, deletes it at the next restart and then deletes itself", UninstallMessages.ProfilePlain);
            Eq("A leftover folder from Domovoi's hidden account will be removed automatically the next time Windows restarts. There's nothing for you to do.",
                UninstallMessages.AtRestart(new[] { profile }), "the profile alone");
            var files = new RestartNote(@"C:\ProgramData\Domovoi: 3 file(s) and folder(s) Windows still had open (in use); Windows deletes them at the next restart", UninstallMessages.ProgramDataPlain);
            Eq("A leftover folder from Domovoi's hidden account and a few of Domovoi's files that Windows still had open will be removed automatically the next time Windows restarts. There's nothing for you to do.",
                UninstallMessages.AtRestart(new[] { profile, files, profile }), "both, each once");
            True(UninstallMessages.AtRestart(new RestartNote[0]) == null, "nothing at the restart: no message");
            Eq("a, b and c", UninstallMessages.Join(new[] { "a", "b", "", "a", "c" }), "join");

            var left = new List<UninstallLeftover>
            {
                new UninstallLeftover(new UninstallStep(UninstallKind.DeleteService, Paths.ServiceName), "the DomovoiHost service is marked for deletion; it goes at the next restart"),
                new UninstallLeftover(new UninstallStep(UninstallKind.RemoveFirewallRules, "Domovoi-*"), "firewall rules Domovoi-Dashboard-6369"),
            };
            Eq("A few things could not be removed: Domovoi's background service and Domovoi's firewall rules. They hold none of your data and are safe to leave.",
                UninstallMessages.Leftovers(left), "finished with leftovers");
            Eq("Windows was still using some of Domovoi, so these are still here: Domovoi's background service and Domovoi's firewall rules.",
                UninstallMessages.StillThere(left), "not finished");
            True(UninstallMessages.Leftovers(new List<UninstallLeftover>()) == null, "no leftovers: no message");

            // No message carries a path, an error code, a task or service
            // name, or a line break (each is one key=value line).
            foreach (UninstallKind k in Enum.GetValues(typeof(UninstallKind)))
            {
                var step = new UninstallStep(k, @"C:\x");
                step.Sids.Add(Sid);
                string plain = new UninstallLeftover(step, "x").Plain();
                True(plain.Length > 5, k + ": says something");
                foreach (string tech in new[] { ":\\", "error", "S-1-5", "DomovoiRemoveProfile", "domovoi-svc", "sc.exe", "icacls", "reg ", "\n", "\r" })
                    True(plain.IndexOf(tech, StringComparison.OrdinalIgnoreCase) < 0, k + " plain has " + tech + ": " + plain);
            }
            foreach (string m in new[] { UninstallMessages.AtRestart(new[] { profile, files }), UninstallMessages.Leftovers(left), UninstallMessages.StillThere(left) })
                foreach (string tech in new[] { "error 32", "DomovoiRemoveProfile", "startup task", "C:\\", "\n", "\r" })
                    True(m.IndexOf(tech, StringComparison.OrdinalIgnoreCase) < 0, "message has " + tech + ": " + m);
        });
    }

    // -- the media folders from the dashboard (MediaFolders.cs, MediaOpen.cs)

    sealed class FakeMediaServer : Domovoi.Windows.Tray.IMediaServer
    {
        public Domovoi.Windows.Tray.MediaOpenRequest Req;
        public bool ClaimOk = true;
        public int Claims;
        public string Outcome, Detail, ReportedClaim;
        public Domovoi.Windows.Tray.MediaOpenRequest Pending() { return Req; }
        public string Claim(string id) { Claims++; return ClaimOk ? "token-" + id : null; }
        public void Report(string id, string claim, string outcome, string detail) { ReportedClaim = claim; Outcome = outcome; Detail = detail; }
    }

    [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern uint GetShortPathNameW(string longPath, System.Text.StringBuilder shortPath, uint size);

    static void MediaFolderTests()
    {
        const string id = "0123456789abcdef0123456789abcdef";
        Func<string, string, string> reqText = (root, at) =>
            "{\"v\":1,\"root\":" + Domovoi.Windows.Loopback.Quote(root) + ",\"id\":\"" + id + "\",\"at\":\"" + at + "\"}";
        Run("media folders: a move request", delegate
        {
            MediaRequest r = MediaFolders.Parse(reqText("D:/Media/", "2026-11-11T05:00:00Z"));
            Eq("ok", r.State, "taken");
            Eq(@"D:\Media", r.Root, "normalized");
            Eq(id, r.Id, "its id");
            Eq("none", MediaFolders.Parse(null).State, "no file");
            foreach (string bad in new[]
            {
                "not json", "[]", "{\"v\":2,\"root\":\"D:\\\\M\",\"id\":\"" + id + "\",\"at\":\"2026-11-11T05:00:00Z\"}",
                "{\"v\":1,\"root\":\"D:\\\\M\",\"id\":\"" + id + "\",\"at\":\"2026-11-11T05:00:00Z\",\"run\":\"calc\"}",
                "{\"v\":1,\"root\":\"D:\\\\M\",\"id\":\"xyz\",\"at\":\"2026-11-11T05:00:00Z\"}",
                reqText(@"\\nas\share", "2026-11-11T05:00:00Z"), reqText(@"D:\", "2026-11-11T05:00:00Z"),
                reqText(@"D:\a\..\Windows", "2026-11-11T05:00:00Z"), reqText("D:\\" + new string('x', 250), "2026-11-11T05:00:00Z"),
                reqText(@"D:\Media", "yesterday"), new string(' ', 3000),
            })
                Eq("invalid", MediaFolders.Parse(bad).State, "refused: " + (bad.Length > 60 ? bad.Substring(0, 60) : bad));
        });
        Run("media folders: which requests move them", delegate
        {
            DateTime now = new DateTime(2026, 11, 11, 5, 10, 0, DateTimeKind.Utc);
            DateTime never = DateTime.MinValue;
            Func<string, MediaRequest> req = at => MediaFolders.Parse(reqText(@"D:\Media", at));
            Eq(MediaMove.Go, MediaFolders.Decide(req("2026-11-11T05:09:00Z"), null, true, now, never, "inactive"), "fresh, nothing running");
            Eq(MediaMove.None, MediaFolders.Decide(req("2026-11-11T05:09:00Z"), id, true, now, never, "inactive"), "already done");
            Eq(MediaMove.None, MediaFolders.Decide(req("2026-11-11T05:09:00Z"), null, false, now, never, "inactive"), "host-state.json unreadable");
            Eq(MediaMove.Busy, MediaFolders.Decide(req("2026-11-11T05:09:00Z"), null, true, now, never, "activating"), "an update runs");
            Eq(MediaMove.Busy, MediaFolders.Decide(req("2026-11-11T05:09:00Z"), null, true, now, never, null), "can't tell: wait");
            Eq(MediaMove.TooSoon, MediaFolders.Decide(req("2026-11-11T05:09:00Z"), null, true, now, now.AddMinutes(-3), "inactive"), "one restart per 10 minutes, limits' included");
            Eq(MediaMove.Stale, MediaFolders.Decide(req("2026-11-11T04:30:00Z"), null, true, now, never, "inactive"), "older than 15 minutes");
            Eq(MediaMove.Stale, MediaFolders.Decide(req("2026-11-11T07:00:00Z"), null, true, now, never, "inactive"), "from the future");
            Eq(MediaMove.Invalid, MediaFolders.Decide(MediaFolders.Parse(reqText(@"\\nas\x", "2026-11-11T05:09:00Z")), null, true, now, never, "inactive"), "a bad folder: refused, reported");
            Eq(MediaMove.None, MediaFolders.Decide(MediaFolders.Parse(null), null, true, now, never, "inactive"), "nothing asked");
        });
        var setupArgs = new List<string> { "--repo-url", "https://example.org/x", "--internet", "never" };
        foreach (var m in MediaPaths.Under(MediaPaths.DefaultRoot)) { setupArgs.Add("--media"); setupArgs.Add(m.Key + "=" + m.Value); }
        setupArgs.Add("--health-timeout"); setupArgs.Add("900");
        Run("media folders: the job's folders and the dashboard's", delegate
        {
            var job = MediaFolders.MediaArgs(setupArgs);
            Eq(5, job.Count, "five folders");
            Eq(MediaPaths.DefaultRoot, MediaFolders.RootOf(job), "Setup's root");
            True(MediaFolders.RootOf(new List<KeyValuePair<string, string>> { new KeyValuePair<string, string>("music", @"D:\Songs") }) == null, "not <root>\\Music: no root");
            var ov = new MediaOverride { Root = @"D:\Media", BasedOn = MediaPaths.DefaultRoot };
            bool drop;
            var eff = MediaFolders.Effective(setupArgs, ov, out drop);
            True(!drop, "kept while Setup's root is the same");
            Eq(@"D:\Media\Music", eff[0].Value, "the dashboard's folders");
            var newer = MediaFolders.WithMedia(setupArgs, MediaPaths.Under(@"E:\Other"));
            eff = MediaFolders.Effective(newer, ov, out drop);
            True(drop, "Setup picked another folder since: Setup wins");
            Eq(@"E:\Other\Music", eff[0].Value, "Setup's folders");
            var replaced = MediaFolders.WithMedia(setupArgs, MediaPaths.Under(@"D:\Media"));
            Eq(setupArgs.Count, replaced.Count, "same number of arguments");
            Eq("--internet", replaced[2], "the rest untouched");
            Eq("--media", replaced[4], "in the same place");
            Eq(@"music=D:\Media\Music", replaced[5], "the new folder");
            Eq("--health-timeout", replaced[replaced.Count - 2], "the end untouched");
            True(MediaOverride.FromJson(Json.ParseObject("{\"root\":\"\\\\\\\\nas\\\\x\"}")) == null, "a stored root of the wrong shape is ignored");
        });
        Run("media folders: the distro's fstab block", delegate
        {
            Eq("C:/Users/Public/Domovoi/Music", MediaFolders.FstabSource(@"c:\Users\Public\Domovoi\Music\"), "as provision.sh writes it");
            Eq(@"D:/My\040Media/Videos", MediaFolders.FstabSource(@"D:\My Media\Videos"), "blanks escaped");
            string input = MediaFolders.FstabInput(MediaPaths.Under(@"D:\My Media"));
            True(input.StartsWith(@"D:/My\040Media/Music /srv/media/music" + "\n", StringComparison.Ordinal), "one line per folder: " + input);
            Eq(5, input.Split('\n').Length - 1, "five lines");
            Throws<ArgumentException>(delegate { MediaFolders.FstabInput(new List<KeyValuePair<string, string>> { new KeyValuePair<string, string>("etc", @"D:\x") }); }, "an unknown name");
            Throws<ArgumentException>(delegate { MediaFolders.FstabInput(new List<KeyValuePair<string, string>> { new KeyValuePair<string, string>("music", @"\\nas\x") }); }, "a bad folder");
            string s = MediaFolders.FstabScript();
            True(s.IndexOf('"') < 0, "no double quotes (wsl.exe's command line)");
            True(s.Contains("set -euf"), "no globbing, stops on errors");
            True(s.Contains("'" + MediaFolders.FstabBegin + "'") && s.Contains("'" + MediaFolders.FstabEnd + "'"), "provision.sh's markers");
            True(s.Contains("drvfs metadata,uid=%s,gid=%s,umask=022,fmask=133,noatime,nofail 0 0"), "provision.sh's options");
            True(s.Contains("*) exit 3"), "each line checked again");
        });
        Run("media folders: the report and the tray's list", delegate
        {
            var media = MediaPaths.Under(@"D:\Media");
            var ch = new MediaChange { State = "done", RequestId = id, Root = @"D:\Media", PreviousRoot = MediaPaths.DefaultRoot };
            var d = Json.ParseObject(MediaFolders.Report(media, "dashboard", ch, true, true, new DateTime(2026, 11, 11, 5, 0, 0, DateTimeKind.Utc)));
            Eq(1, Json.Int(d, "schema") ?? 0, "schema");
            Eq("windows", Json.Str(d, "host_kind"), "host kind");
            Eq(@"D:\Media", Json.Str(d, "root"), "root");
            Eq("/srv/media", Json.Str(d, "mount_base"), "mount base");
            Eq(@"D:\Media\Videos", Json.Str(Json.Obj(d, "folders"), "videos"), "a folder");
            Eq("dashboard", Json.Str(d, "source"), "source");
            Eq("done", Json.Str(Json.Obj(d, "change"), "state"), "the move");
            string script = MediaFolders.ReportScript();
            True(script.Contains("chown root:root /var/lib/domovoi-windows") && script.Contains("/var/lib/domovoi-windows/media.json"), "root's file");
            var map = MediaFolders.ParseTrayMap(MediaFolders.TrayMap(media, DateTime.UtcNow));
            Eq(5, map.Count, "five folders");
            Eq(@"D:\Media\Pictures", map["pictures"], "a folder");
            var bad = MediaFolders.ParseTrayMap("{\"schema\":1,\"folders\":{\"music\":\"\\\\\\\\nas\\\\x\",\"etc\":\"D:\\\\x\",\"videos\":\"D:\\\\V\"}}");
            True(!bad.ContainsKey("music") && !bad.ContainsKey("etc") && bad.ContainsKey("videos"), "only known names and drive folders");
            Eq(0, MediaFolders.ParseTrayMap("{\"schema\":2,\"folders\":{}}").Count, "another schema");
            Eq(0, MediaFolders.ParseTrayMap(null).Count, "no file");
        });
        Run("media folders: the root on this PC", delegate
        {
            var sys = SystemFolders.Typical();
            var ntfs = new MediaDrive { Ready = true, Type = System.IO.DriveType.Fixed, Format = "NTFS" };
            True(MediaFolders.RootProblem(@"D:\Media", sys, ntfs, null) == null, "a folder on an NTFS drive");
            True(MediaFolders.RootProblem(@"C:\Users\alex\Music\Domovoi", sys, ntfs, null) == null, "inside a profile's Music");
            True(MediaFolders.RootProblem(@"D:\Media", sys, new MediaDrive { Ready = true, Type = System.IO.DriveType.Removable, Format = "exFAT" }, null).Contains("NTFS"), "exFAT");
            True(MediaFolders.RootProblem(@"Z:\Media", sys, new MediaDrive { Ready = true, Type = System.IO.DriveType.Network, Format = "NTFS" }, null).Contains("local drive"), "a mapped network drive");
            True(MediaFolders.RootProblem(@"E:\Media", sys, new MediaDrive { Ready = false }, null).Contains("ready"), "no disc in it");
            True(MediaFolders.RootProblem(@"C:\Windows\Media", sys, ntfs, null).Contains("system folder"), "Windows");
            True(MediaFolders.RootProblem(@"C:\Users\alex", sys, ntfs, null) != null, "a profile itself");
            True(MediaFolders.RootProblem(@"C:\Users\alex\AppData\x", sys, ntfs, null) != null, "AppData");
            True(MediaFolders.RootProblem(@"D:\Media", sys, ntfs, @"D:\Media").Contains("link"), "a junction");
            True(MediaFolders.RootProblem(@"D:\" + new string('x', 210), sys, ntfs, null).Contains("longer"), "too long");
        });
        Run("media folders: short names are checked by their long name (security review)", delegate
        {
            var sys = SystemFolders.Typical();
            var ntfs = new MediaDrive { Ready = true, Type = System.IO.DriveType.Fixed, Format = "NTFS" };
            True(MediaFolders.RootProblem(@"D:\Media", @"C:\ProgramData\Foo", sys, ntfs, null) != null, "a long name inside ProgramData");
            True(MediaFolders.RootProblem(@"D:\Media", @"\\nas\x", sys, ntfs, null) != null, "resolves to a network folder");
            True(MediaFolders.RootProblem(@"D:\Media", @"D:\Media", sys, ntfs, null) == null, "the same name");
            True(MediaFolders.StoredProblem(@"D:\Media", sys, p => p, p => null) == null, "a stored root still fine");
            True(MediaFolders.StoredProblem(@"D:\Media", sys, p => @"C:\Windows\x", p => null) != null, "a stored root that now resolves into Windows");
            True(MediaFolders.StoredProblem(@"D:\Media", sys, p => p, p => p == @"D:\Media\Music" ? p : null).Contains("link"), "a junction put in for Music");
            True(MediaFolders.StoredProblem(@"C:\PROGRA~3", sys, p => @"C:\ProgramData", p => null) != null, "a stored short name");
            True(MediaFolders.StoredProblem(@"D:\MYMEDI~1", sys, p => @"D:\My Media", p => null).Contains("full name"), "any real short name");
            True(MediaFolders.StoredProblem(@"D:\Music~2024", sys, p => p, p => null) == null, "a legacy root really named Music~2024 (GuardStoredMedia keeps it)");
            // This PC: the short name Windows gives Program Files, if the
            // volume keeps 8.3 names, resolves to the long one.
            string pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            var sb = new System.Text.StringBuilder(260);
            if (GetShortPathNameW(pf, sb, 260) > 0 && sb.ToString().Contains("~"))
            {
                string shortName = sb.ToString();
                Eq((pf + @"\NoSuch\Deeper").ToLowerInvariant(), MediaFolders.Canonical(shortName + @"\NoSuch\Deeper").ToLowerInvariant(), "expanded, the missing rest kept");
                True(MediaFolders.RootProblem(shortName + @"\x", MediaFolders.Canonical(shortName + @"\x"), MediaFolders.SystemFoldersHere(), ntfs, null) != null, "refused here");
            }
            Eq(@"Z:\no\such", MediaFolders.Canonical(@"Z:\no\such\"), "nothing there: as written");
            Eq(@"\\nas\x", MediaFolders.Canonical(@"\\nas\x"), "not a drive path: as written");
        });
        Run("media folders: a folder really named Music~2024 keeps working (review round 2)", delegate
        {
            var sys = SystemFolders.Typical();
            var ntfs = new MediaDrive { Ready = true, Type = System.IO.DriveType.Fixed, Format = "NTFS" };
            const string legacy = @"D:\Music~2024";
            True(MediaPaths.Problem(legacy) == null && MediaFolders.ShapeProblem(legacy) == null, "its shape");
            True(MediaFolders.RootProblem(legacy, legacy, sys, ntfs, null) == null, "Setup's repair and the host: it is its own long name");
            True(MediaFolders.RootProblem(@"C:\PROGRA~3\Foo", @"C:\ProgramData\Foo", sys, ntfs, null) != null, "PROGRA~3 still refused");
            True(MediaFolders.RootProblem(@"D:\MYMEDI~1", @"D:\My Media", sys, ntfs, null).Contains("full name"), "a real short name: type the full name");
            string fstab = MediaFolders.FstabInput(MediaPaths.Under(legacy));
            True(fstab.StartsWith("D:/Music~2024/Music /srv/media/music\n", StringComparison.Ordinal), "into fstab: " + fstab);
            Eq(@"D:\Music~2024\Videos", MediaFolders.ParseTrayMap(MediaFolders.TrayMap(MediaPaths.Under(legacy), DateTime.UtcNow))["videos"], "on the tray's list");
            var d = Json.ParseObject(MediaFolders.Report(MediaPaths.Under(legacy), "setup", null, true, true, DateTime.UtcNow));
            Eq(legacy, Json.Str(d, "root"), "in the report");
            MediaRequest r = MediaFolders.Parse("{\"v\":1,\"root\":\"D:\\\\Music~2024\",\"id\":\"" + id + "\",\"at\":\"2026-11-11T05:00:00Z\"}");
            Eq("ok", r.State, "a request's shape (the host then asks Windows)");
            // A real folder of that name on this PC is its own long name.
            string dir = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "domovoi-tests-" + Guid.NewGuid().ToString("N"), "Music~2024");
            System.IO.Directory.CreateDirectory(dir);
            try
            {
                string c = MediaFolders.Canonical(dir);
                True(MediaFolders.ShortNameProblem(dir, c) == null, "a real Music~2024 folder: " + c);
                Eq(dir.ToLowerInvariant(), c.ToLowerInvariant(), "resolves to itself");
            }
            finally { System.IO.Directory.Delete(System.IO.Path.GetDirectoryName(dir), true); }
            string pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            var sb = new System.Text.StringBuilder(260);
            if (GetShortPathNameW(pf, sb, 260) > 0 && sb.ToString().Contains("~"))
                True(MediaFolders.ShortNameProblem(sb + @"\Foo", MediaFolders.Canonical(sb + @"\Foo")) != null, "this PC's real short name refused");
        });
        Run("media folders: an interrupted move does not lock the feature (security review)", delegate
        {
            var c = new MediaChange { State = "applying", RequestId = id };
            True(MediaFolders.Interrupted(c, id), "applying, and its id recorded as done: interrupted");
            True(!MediaFolders.Interrupted(c, "f" + id.Substring(1)), "another id: a move not started yet");
            c.State = "done";
            True(!MediaFolders.Interrupted(c, id), "done is done");
            True(!MediaFolders.Interrupted(null, id), "nothing stored");
        });
        Run("media folders: a BEGIN with no END keeps the rest of fstab", delegate
        {
            True(MediaFolders.FstabKeepAwk.IndexOf('"') < 0, "no double quotes");
            True(MediaFolders.FstabScript().Contains(MediaFolders.FstabKeepAwk), "the script uses it");
            True(MediaFolders.FstabKeepAwk.Contains("END{if(s)"), "what followed a stray BEGIN is printed at the end");
        });
        Run("media folders: the tray opens a kind, never a path", delegate
        {
            string mapText = MediaFolders.TrayMap(MediaPaths.Under(@"D:\Media"), DateTime.UtcNow);
            var opened = new List<string>();
            Func<Domovoi.Windows.Tray.MediaOpenEnv> env = () => new Domovoi.Windows.Tray.MediaOpenEnv
            {
                ActiveSession = true,
                ReadMap = () => mapText,
                FolderExists = p => p != @"D:\Media\Audiobooks",
                LinkIn = p => p == @"D:\Media\Documents" ? p : null,
                System = SystemFolders.Typical,
                Canonical = p => p == @"D:\Media\Pictures" ? @"C:\ProgramData\Pictures" : p,
                Open = p => opened.Add(p),
                Log = s => { },
            };
            var srv = new FakeMediaServer { Req = new Domovoi.Windows.Tray.MediaOpenRequest { Id = id, Kind = "music" } };
            Eq("opened", Domovoi.Windows.Tray.MediaOpen.Run(srv, env()), "opened");
            SeqEq(new[] { @"D:\Media\Music" }, opened, "the folder on the host's list");
            Eq("opened", srv.Outcome, "reported");
            Eq("token-" + id, srv.ReportedClaim, "with the claim's token");
            srv.Req.Kind = "pictures";
            Eq("refused", Domovoi.Windows.Tray.MediaOpen.Run(srv, env()), "a folder whose long name is a system folder");
            srv.Req.Kind = "audiobooks";
            Eq("missing", Domovoi.Windows.Tray.MediaOpen.Run(srv, env()), "its drive unplugged");
            srv.Req.Kind = "documents";
            Eq("refused", Domovoi.Windows.Tray.MediaOpen.Run(srv, env()), "a link");
            foreach (string k in new[] { "podcasts", @"C:\Windows", "..", null })
            {
                srv.Req.Kind = k;
                Eq("refused", Domovoi.Windows.Tray.MediaOpen.Run(srv, env()), "not on the list: " + (k ?? "null"));
            }
            Eq(1, opened.Count, "nothing else opened");
            var e = env();
            e.ActiveSession = false;
            srv.Claims = 0;
            True(Domovoi.Windows.Tray.MediaOpen.Run(srv, e) == null && srv.Claims == 0, "another session's tray leaves it alone");
            srv.ClaimOk = false;
            srv.Req.Kind = "music";
            True(Domovoi.Windows.Tray.MediaOpen.Run(srv, env()) == null, "another tray claimed it");
            Eq(1, opened.Count, "and nothing opened");
            mapText = "{\"schema\":1,\"folders\":{\"music\":\"C:\\\\Windows\\\\System32\"}}";
            srv.ClaimOk = true;
            Eq("refused", Domovoi.Windows.Tray.MediaOpen.Run(srv, env()), "a system folder on the list is still refused");
        });
    }
}
