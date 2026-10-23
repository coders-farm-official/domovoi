// DomovoiSetupHelper.exe: the work Domovoi Setup and its uninstaller do on
// Windows, one verb per call, so Setup's Pascal script stays a UI. It is
// installed in C:\Program Files\Domovoi (admin-owned) and every verb that
// changes the PC runs from there, elevated, as Setup's child. Only the
// read-only preflight, check-media and saved-ai (which changes nothing but
// a one-off request file in host\in, deleted again) also run from Setup's
// temporary folder, before anything is installed, the way Setup's own
// engine does.
//
// Output: key=value lines on stdout (Setup reads them); "error=" and exit
// code 1 on failure. install.log gets a line per change. The account's
// password and the setup code never reach either (the setup-code verb
// prints the code to stdout only, for Setup's finish page).
//
//   preflight [--ports-only] [--skip-internet] [--installing]   read-only
//   vmp-status | wsl-status | host-status | plan    read-only
//   host-stop
//   saved-ai                 what Setup's AI page starts at (a re-run: the
//                            household's answer, through DomovoiHost)
//   prepare --internet A [--ai yes|no|keep] --media-root DIR --commit SHA
//   account --exe PATH [--no-deny-logon]
//   media
//   firewall [--subnet CIDR]
//   job --image P --image-sha256 H --provision P --provision-sha256 H --repo-url U --ref SHA [--track-branch stable|main] [--lan-ip IP|auto|none]
//   check-media --root DIR                          read-only
//   host-start | host-retry | host-park | setup-code | record-ai
//   run-update --from DIR --run NAME --tag T [--commit SHA] [--linuxfirst]   the Windows step, elevated (Update.cs)
//   linux-update [--wait] [--run NAME]              the Linux part of the Windows update (Update.cs)
//   saved-answers                                   read-only: the answers Setup's update mode keeps
//   uninstall [--dry-run]                           exit 3: done, with leftovers
//   copy-logs --kind install|uninstall --dest DIR [--uninstall-log FILE]

using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Principal;
using System.Text;

namespace Domovoi.Windows.Helper
{
    sealed class Output
    {
        readonly Log log;

        public Output(Log log) { this.log = log; }

        public void Kv(string key, string value)
        {
            string v = (value ?? "").Replace("\r", " ").Replace("\n", " ");
            Console.Out.WriteLine(key + "=" + v);
            Console.Out.Flush();
        }

        // A change made, or a step taken: stdout and the log.
        public void Say(string message)
        {
            Kv("info", message);
            if (log != null) log.Write(message);
        }

        public void LogOnly(string message)
        {
            if (log != null) log.Write(message);
        }
    }

    static class Program
    {
        static readonly HashSet<string> ReadOnly = new HashSet<string> { "preflight", "vmp-status", "wsl-status", "host-status", "plan", "check-media", "saved-answers", "help" };

        static int Main(string[] argv)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            if (argv.Length == 0 || argv[0] == "help" || argv[0] == "--help")
            {
                Console.Out.WriteLine("DomovoiSetupHelper: run by Domovoi Setup and its uninstaller; see docs/WINDOWS_HOST.md.");
                return argv.Length == 0 ? 2 : 0;
            }
            string verb = argv[0];
            Args a;
            try { a = new Args(argv); }
            catch (ArgumentException e) { Console.Out.WriteLine("error=" + e.Message); return 2; }

            bool dry = a.Flag("--dry-run");
            bool elevated = new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
            if (!ReadOnly.Contains(verb) && !(verb == "uninstall" && dry) && !elevated)
            {
                Console.Out.WriteLine("error=this needs an elevated (administrator) prompt");
                return 2;
            }
            Log log = null;
            if (verb == "uninstall" && !dry)
            {
                // install.log goes with C:\ProgramData\Domovoi; the uninstall's
                // own record stays in the caller's temp folder.
                string tmp = Path.Combine(Path.GetTempPath(), "domovoi-uninstall-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
                log = new Log(tmp, 10L * 1024 * 1024);
                Console.Out.WriteLine("log=" + tmp);
            }
            else if (!ReadOnly.Contains(verb) && Directory.Exists(Paths.State))
            {
                // state\ is Administrators and SYSTEM only: domovoi-svc can't
                // touch the elevated helper's log.
                log = new Log(Paths.InstallLog, 10L * 1024 * 1024);
            }
            var o = new Output(log);
            try
            {
                if (log != null) log.Write("helper " + verb + " (" + WindowsIdentity.GetCurrent().Name + ")");
                switch (verb)
                {
                    case "preflight": Preflight.Run(o, a.Flag("--ports-only"), a.Flag("--skip-internet"), a.Flag("--installing")); return 0;
                    case "vmp-status": o.Kv("vmp", Preflight.VmpEnabled() ? "enabled" : "disabled"); return 0;
                    case "wsl-status":
                        {
                            string v = Preflight.WslVersion();
                            o.Kv("wsl_version", v ?? "");
                            o.Kv("wsl", Preflight.WslStatus(v));
                            return 0;
                        }
                    case "host-stop": HostService.Stop(180, o.Say); return 0;
                    case "prepare": Steps.Prepare(o, a.Req("--internet"), a.Opt("--ai") ?? "yes", a.Req("--media-root"), a.Req("--commit")); return 0;
                    case "account": Steps.AccountAndService(o, a.Req("--exe"), !a.Flag("--no-deny-logon")); return 0;
                    case "media": Steps.Media(o); return 0;
                    case "firewall": Steps.FirewallRules(o, a.Opt("--subnet")); return 0;
                    case "job":
                        Steps.WriteJob(o, a.Req("--image"), a.Req("--image-sha256"), a.Req("--provision"), a.Req("--provision-sha256"),
                            a.Req("--repo-url"), a.Req("--ref"), a.Opt("--track-branch") ?? "stable", a.Opt("--lan-ip") ?? "auto");
                        return 0;
                    case "host-start": Steps.HostStart(o); return 0;
                    case "host-status": Steps.HostStatusOut(o); return 0;
                    case "host-retry": Steps.HostRetry(o); return 0;
                    case "host-park": Steps.HostPark(o); return 0;
                    case "check-media": Steps.CheckMedia(o, a.Req("--root")); return 0;
                    case "setup-code": return Steps.ReadSetupCode(o);
                    case "saved-ai": Steps.SavedAi(o); return 0;
                    case "record-ai": Steps.RecordAi(o); return 0;
                    case "plan": Steps.Plan(o); return 0;
                    case "linux-update": return Update.LinuxUpdate(o, a.Flag("--wait"), a.Opt("--run"));
                    case "run-update": return Update.RunUpdate(o, a.Req("--from"), a.Req("--run"), a.Req("--tag"), a.Opt("--commit"), a.Flag("--linuxfirst"));
                    case "saved-answers": Update.SavedAnswers(o); return 0;
                    case "uninstall": return Uninstall.Run(o, dry);
                    case "copy-logs": Logs.Copy(o, a.Req("--dest"), a.Req("--kind"), a.Opt("--uninstall-log")); return 0;
                    default:
                        o.Kv("error", "unknown verb: " + verb);
                        return 2;
                }
            }
            catch (Exception e)
            {
                o.Kv("error", e.Message);
                if (log != null) log.Write("ERROR " + verb + ": " + e);
                return 1;
            }
        }
    }

    sealed class Args
    {
        readonly Dictionary<string, string> values = new Dictionary<string, string>();
        readonly HashSet<string> flags = new HashSet<string>();

        public Args(string[] argv)
        {
            for (int i = 1; i < argv.Length; i++)
            {
                string k = argv[i];
                if (!k.StartsWith("--", StringComparison.Ordinal)) throw new ArgumentException("unexpected argument: " + k);
                if (k == "--dry-run" || k == "--ports-only" || k == "--skip-internet" || k == "--no-deny-logon" || k == "--installing" || k == "--wait" || k == "--linuxfirst") { flags.Add(k); continue; }
                if (i + 1 >= argv.Length) throw new ArgumentException(k + " needs a value");
                values[k] = argv[++i];
            }
        }

        public bool Flag(string k) { return flags.Contains(k); }

        public string Opt(string k)
        {
            string v;
            return values.TryGetValue(k, out v) ? v : null;
        }

        public string Req(string k)
        {
            string v = Opt(k);
            if (string.IsNullOrEmpty(v)) throw new ArgumentException(k + " is required");
            return v;
        }
    }
}
