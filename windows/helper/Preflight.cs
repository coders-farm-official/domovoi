// Preflight (PLAN 4.1): read-only checks before Setup changes anything.
// Prints key=value lines for Setup's pages; "fatal=" lines stop Setup,
// "warn=" lines are shown, "port_owner=" lines become one pop-up each.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Management;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32;

namespace Domovoi.Windows.Helper
{
    static class Preflight
    {
        public const int MinBuild = 22621;
        public const int MinRamGb = 16;
        public const int MinFreeGb = 40;
        // The WSL package Setup was tested against, and builds known bad
        // (PLAN 4.4: 2.9.3 pre-release, a mirrored-mode port-bind regression,
        // microsoft/WSL#40984).
        public static readonly Version MinWsl = new Version(2, 7, 14);
        public static readonly string[] DeniedWsl = { "2.9.3" };

        // PLAN 4.1 "Internet now": every host the install downloads from,
        // on the Windows side and inside provision.sh.
        public static readonly string[] InternetHosts =
        {
            "github.com", "raw.githubusercontent.com", "releases.ubuntu.com", "archive.ubuntu.com",
            "security.ubuntu.com", "download.docker.com", "registry-1.docker.io", "pypi.org",
            "files.pythonhosted.org", "download.pytorch.org", "huggingface.co", "registry.ollama.ai",
        };

        public static void Run(Output o, bool portsOnly, bool skipInternet, bool installing)
        {
            Ports(o, installing);
            if (portsOnly) return;
            Os(o);
            SmartAppControl(o);
            Memory(o);
            Virtualization(o);
            Disk(o);
            WslState(o);
            Network(o);
            if (!skipInternet) Internet(o);
        }

        // -- ports ------------------------------------------------------

        public static List<PortRange> ExcludedRanges()
        {
            try
            {
                var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "netsh.exe"), "int ipv4 show excludedportrange protocol=tcp");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                using (var p = Process.Start(psi))
                {
                    string text = p.StandardOutput.ReadToEnd();
                    p.WaitForExit(15000);
                    return PortCheck.ParseExcludedRanges(text);
                }
            }
            catch (System.ComponentModel.Win32Exception) { return new List<PortRange>(); }
        }

        // installing: Setup's check again while DomovoiHost sets Domovoi up
        // (right before it starts, or after a step failed). Then the host
        // runs, but Domovoi's own Linux services never show in Windows'
        // table (PortScan), so whatever holds a port is another program; and
        // Windows' excluded ranges are left out, because the WSL VM starting
        // may itself reserve ranges and they can't be told apart from here.
        public static void Ports(Output o, bool installing)
        {
            if (!installing)
            {
                // Setup run again over a running install (a repair): the ports
                // are Domovoi's own. Setup stops DomovoiHost before it installs.
                using (var s = HostService.Find())
                {
                    if (s != null && s.Status != System.ServiceProcess.ServiceControllerStatus.Stopped)
                    {
                        o.Kv("ports_taken", "0");
                        o.Kv("ports_note", "Domovoi is already running here; its own ports were not checked");
                        return;
                    }
                }
            }
            var owners = PortCheck.Owners(PortScan.Listeners(), installing ? null : ExcludedRanges());
            o.Kv("ports_taken", owners.Count > 0 ? "1" : "0");
            foreach (var w in owners) o.Kv("port_owner", w.Kind + "|" + w.Title + "|" + w.Advice);
        }

        // -- the machine ------------------------------------------------

        static void Os(Output o)
        {
            int build = Environment.OSVersion.Version.Build;
            string edition = "", product = "";
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion"))
            {
                if (k != null)
                {
                    edition = k.GetValue("EditionID") as string ?? "";
                    product = k.GetValue("ProductName") as string ?? "";
                }
            }
            string arch = Environment.GetEnvironmentVariable("PROCESSOR_ARCHITECTURE", EnvironmentVariableTarget.Machine) ?? "";
            o.Kv("os_build", Inv.S(build));
            o.Kv("os_edition", edition);
            o.Kv("os_arch", arch);
            if (build < MinBuild) o.Kv("fatal", "Domovoi needs Windows 11 version 22H2 or later (build " + Inv.S(MinBuild) + "); this PC has build " + Inv.S(build) + ".");
            if (!string.Equals(arch, "AMD64", StringComparison.OrdinalIgnoreCase)) o.Kv("fatal", "Domovoi needs a 64-bit Intel or AMD PC (x64); this PC is " + arch + ".");
            bool avx2 = Native.IsProcessorFeaturePresent(Native.PF_AVX2_INSTRUCTIONS_AVAILABLE);
            o.Kv("avx2", avx2 ? "1" : "0");
            if (!avx2) o.Kv("warn", "This processor has no AVX2; speech recognition and the AI models will run slower.");
        }

        // Smart App Control (Windows 11) blocks programs that have no
        // signature and no reputation. This test build is unsigned (code
        // signing is on hold, PLAN 10), so with it on, Windows blocks
        // DomovoiSetupHelper.exe and DomovoiHost.exe, and Setup itself. The
        // state is VerifiedAndReputablePolicyState: 0 off, 1 on, 2 evaluation
        // (Windows watches, blocks nothing yet, and may turn it on later).
        public static string SmartAppControlState()
        {
            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var k = hklm.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\CI\Policy"))
            {
                object v = k == null ? null : k.GetValue("VerifiedAndReputablePolicyState");
                if (!(v is int)) return "unknown";
                switch ((int)v)
                {
                    case 0: return "off";
                    case 1: return "on";
                    case 2: return "evaluation";
                    default: return "unknown";
                }
            }
        }

        public const string SmartAppControlHelp = "Turn it off in Windows Security > App & browser control > Smart App Control settings. On many Windows 11 builds it can only be turned back on by resetting or reinstalling Windows, so read what Windows Security says first (docs/WINDOWS_HOST.md, \"Unsigned\").";

        static void SmartAppControl(Output o)
        {
            string s = SmartAppControlState();
            o.Kv("smart_app_control", s);
            if (s == "on")
                o.Kv("fatal", "Smart App Control is on. It blocks programs without a signature, and this test build of Domovoi is unsigned: Windows would block Setup's helper now and the Domovoi service at every start. " + SmartAppControlHelp);
            else if (s == "evaluation")
                o.Kv("warn", "Smart App Control is in evaluation mode. If Windows turns it on later, it blocks Domovoi's unsigned service, and Domovoi stops starting. " + SmartAppControlHelp);
        }

        public static long InstalledRamBytes()
        {
            ulong kb;
            if (Native.GetPhysicallyInstalledSystemMemory(out kb)) return (long)kb * 1024;
            using (var s = new ManagementObjectSearcher("SELECT TotalPhysicalMemory FROM Win32_ComputerSystem"))
                foreach (ManagementObject m in s.Get()) using (m) return Convert.ToInt64(m["TotalPhysicalMemory"]);
            return 0;
        }

        static void Memory(Output o)
        {
            long bytes = InstalledRamBytes();
            int gb = (int)Math.Round(bytes / (1024.0 * 1024 * 1024));
            o.Kv("ram_gb", Inv.S(gb));
            o.Kv("wsl_memory_gb", Inv.S(WslConfig.MemoryCapGb(bytes)));
            if (gb < MinRamGb) o.Kv("fatal", "Domovoi needs at least " + Inv.S(MinRamGb) + " GB of memory; this PC has " + Inv.S(gb) + " GB.");
        }

        static void Virtualization(Output o)
        {
            bool? hyper = null, firmware = null;
            try
            {
                using (var s = new ManagementObjectSearcher("SELECT HypervisorPresent FROM Win32_ComputerSystem"))
                    foreach (ManagementObject m in s.Get()) using (m) hyper = (bool?)m["HypervisorPresent"];
                using (var s = new ManagementObjectSearcher("SELECT VirtualizationFirmwareEnabled FROM Win32_Processor"))
                    foreach (ManagementObject m in s.Get()) using (m) { firmware = (bool?)m["VirtualizationFirmwareEnabled"]; break; }
            }
            catch (ManagementException) { }
            // VirtualizationFirmwareEnabled reads false once a hypervisor runs
            // (PLAN 4.1), so a running hypervisor is enough.
            string v = hyper == true ? "ok" : (firmware == true ? "ok" : (hyper == null && firmware == null ? "unknown" : "off"));
            o.Kv("virtualization", v);
            if (v == "off") o.Kv("fatal", "Virtualization is turned off in this PC's firmware (BIOS/UEFI). Turn on Intel VT-x or AMD-V (often called \"SVM\" or \"Virtualization Technology\"), then run Setup again.");
            if (v == "unknown") o.Kv("warn", "Setup could not tell whether virtualization is on; if WSL fails to start, turn it on in the firmware.");
        }

        static void Disk(Output o)
        {
            string sys = Path.GetPathRoot(Environment.SystemDirectory);
            var d = new DriveInfo(sys);
            long freeGb = d.AvailableFreeSpace / (1024L * 1024 * 1024);
            o.Kv("disk_free_gb", Inv.S(freeGb));
            if (freeGb < MinFreeGb) o.Kv("fatal", "Domovoi needs at least " + Inv.S(MinFreeGb) + " GB free on " + sys + "; there are " + Inv.S(freeGb) + " GB.");
            string media = "unknown";
            try
            {
                using (var s = new ManagementObjectSearcher(@"root\Microsoft\Windows\Storage", "SELECT DiskNumber FROM MSFT_Partition WHERE DriveLetter = '" + sys.Substring(0, 1) + "'"))
                {
                    foreach (ManagementObject p in s.Get())
                    {
                        using (p)
                        {
                            string n = Convert.ToString(p["DiskNumber"]);
                            using (var q = new ManagementObjectSearcher(@"root\Microsoft\Windows\Storage", "SELECT MediaType FROM MSFT_PhysicalDisk WHERE DeviceId = '" + n + "'"))
                                foreach (ManagementObject pd in q.Get())
                                    using (pd)
                                    {
                                        int t = Convert.ToInt32(pd["MediaType"]);
                                        media = t == 3 ? "hdd" : (t == 4 ? "ssd" : (t == 5 ? "scm" : "unknown"));
                                    }
                        }
                    }
                }
            }
            catch (ManagementException) { }
            catch (COMException) { }
            o.Kv("disk_media", media);
            if (media == "hdd") o.Kv("warn", "Windows is on a spinning hard disk; Domovoi works, but an SSD makes it much faster.");
        }

        public static string WslVersion()
        {
            if (!File.Exists(Paths.WslExe)) return null;
            var v = FileVersionInfo.GetVersionInfo(Paths.WslExe);
            return Inv.F("{0}.{1}.{2}", v.FileMajorPart, v.FileMinorPart, v.FileBuildPart);
        }

        // ok, missing, old or denied (PLAN 4.4).
        public static string WslStatus(string version)
        {
            if (version == null) return "missing";
            if (DeniedWsl.Contains(version)) return "denied";
            return new Version(version) < MinWsl ? "old" : "ok";
        }

        public static bool VmpEnabled()
        {
            using (var s = new ManagementObjectSearcher("SELECT InstallState FROM Win32_OptionalFeature WHERE Name = 'VirtualMachinePlatform'"))
                foreach (ManagementObject m in s.Get()) using (m) return Convert.ToInt32(m["InstallState"]) == 1;
            return false;
        }

        static void WslState(Output o)
        {
            string v = WslVersion();
            o.Kv("wsl_version", v ?? "");
            o.Kv("wsl", WslStatus(v));
            bool vmp = false;
            try { vmp = VmpEnabled(); } catch (ManagementException) { }
            o.Kv("vmp", vmp ? "enabled" : "disabled");
        }

        // -- the network ------------------------------------------------

        public sealed class LanInfo
        {
            public string Ip;
            public int Prefix;
            public string Subnet;
            public string Mac;
            public string Alias;
            public int IfIndex;
            public bool Private;
            public string Category;
        }

        public static LanInfo Lan()
        {
            uint idx;
            // The interface Windows would use for the internet (1.1.1.1).
            if (Native.GetBestInterface(0x01010101, out idx) != 0) return null;
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                IPInterfaceProperties props = nic.GetIPProperties();
                IPv4InterfaceProperties v4;
                try { v4 = props.GetIPv4Properties(); }
                catch (NetworkInformationException) { continue; }
                if (v4 == null || v4.Index != (int)idx) continue;
                foreach (var ua in props.UnicastAddresses)
                {
                    if (ua.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                    var info = new LanInfo();
                    info.Ip = ua.Address.ToString();
                    info.Prefix = global::Domovoi.Windows.Lan.PrefixFromMask(ua.IPv4Mask.ToString());
                    info.Subnet = global::Domovoi.Windows.Lan.Subnet(info.Ip, info.Prefix < 8 ? 8 : info.Prefix);
                    info.Mac = string.Join("-", nic.GetPhysicalAddress().GetAddressBytes().Select(b => b.ToString("X2")).ToArray());
                    info.Alias = nic.Name;
                    info.IfIndex = (int)idx;
                    info.Private = global::Domovoi.Windows.Lan.IsPrivateIPv4(info.Ip) && info.Prefix >= 8;
                    info.Category = Category((int)idx);
                    return info;
                }
            }
            return null;
        }

        static string Category(int ifIndex)
        {
            try
            {
                using (var s = new ManagementObjectSearcher(@"root\StandardCimv2", "SELECT NetworkCategory FROM MSFT_NetConnectionProfile WHERE InterfaceIndex = " + Inv.S(ifIndex)))
                    foreach (ManagementObject m in s.Get())
                        using (m)
                        {
                            int c = Convert.ToInt32(m["NetworkCategory"]);
                            return c == 0 ? "Public" : (c == 1 ? "Private" : (c == 2 ? "Domain" : "unknown"));
                        }
            }
            catch (ManagementException) { }
            return "unknown";
        }

        static void Network(Output o)
        {
            LanInfo l = Lan();
            if (l == null)
            {
                o.Kv("lan", "none");
                o.Kv("warn", "This PC has no network connection Setup could find; phones and satellites will not reach Domovoi until it has one.");
                return;
            }
            o.Kv("lan_ip", l.Ip);
            o.Kv("lan_prefix", Inv.S(l.Prefix));
            o.Kv("lan_subnet", l.Subnet);
            o.Kv("lan_mac", l.Mac);
            o.Kv("lan_interface", l.Alias);
            o.Kv("lan_category", l.Category);
            o.Kv("lan_private", l.Private ? "1" : "0");
            if (!l.Private) o.Kv("warn", "This PC's address " + l.Ip + " is not a private home-network address, so Setup adds no firewall rules: Domovoi will answer on this PC only.");
            if (l.Category == "Public") o.Kv("warn", "Windows treats this network as Public. Domovoi's firewall rules name your subnet, so phones and satellites on it can still connect.");
        }

        static void Internet(Output o)
        {
            var failed = new List<string>();
            var gate = new object();
            var threads = new List<Thread>();
            foreach (string h in InternetHosts)
            {
                string host = h;
                var t = new Thread(delegate ()
                {
                    if (!Reachable("https://" + host + "/")) lock (gate) failed.Add(host);
                });
                t.IsBackground = true;
                t.Start();
                threads.Add(t);
            }
            foreach (var t in threads) t.Join(20000);
            failed.Sort();
            o.Kv("internet", failed.Count == 0 ? "ok" : "fail");
            if (failed.Count > 0)
            {
                o.Kv("internet_unreachable", string.Join(",", failed.ToArray()));
                o.Kv("fatal", "Setup needs the internet to download Domovoi (about 10-13 GB). It could not reach " + string.Join(", ", failed.ToArray()) + ". Connect this PC and run Setup again.");
            }
            // The core's connectivity probe dials 1.1.1.1:443 by default
            // (domovoi/config.py); some networks block it.
            string probe = null;
            foreach (string target in new[] { "1.1.1.1", "9.9.9.9" })
                if (Dial(target, 443)) { probe = target + ":443"; break; }
            o.Kv("probe_target", probe ?? "");
        }

        static bool Reachable(string url)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "HEAD";
                req.Timeout = 10000;
                req.AllowAutoRedirect = false;
                req.UserAgent = "DomovoiSetup/0.1";
                using (req.GetResponse()) return true;
            }
            catch (WebException e)
            {
                // Any HTTP answer, even 403 or 405, means the host is reachable.
                if (e.Response != null) { e.Response.Close(); return true; }
                return false;
            }
        }

        static bool Dial(string ip, int port)
        {
            try
            {
                using (var c = new TcpClient())
                {
                    IAsyncResult ar = c.BeginConnect(ip, port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(5000)) return false;
                    c.EndConnect(ar);
                    return true;
                }
            }
            catch (SocketException) { return false; }
        }
    }
}
