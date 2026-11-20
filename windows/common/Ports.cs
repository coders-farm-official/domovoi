// The ports Domovoi needs on this PC, and who holds them when they are
// taken. Owner decision 2026-10-09 (PLAN 4.1, "Coexistence"): check the
// ports, not the programs. A free port is fine whatever else runs here; a
// taken one gets one pop-up per owning program (Docker, Ollama, anything
// else by its name), with Retry and Cancel.
//
// Mirrored networking shares this PC's port space with the distro (PLAN
// 4.1, [S7]), so a Windows program listening on one of these ports, even
// on 127.0.0.1 only, stops the matching Linux service from binding.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public sealed class PortSpec
    {
        public readonly int First;
        public readonly int Last;
        public readonly string What;
        // Reached from the LAN, so it gets a firewall rule (PLAN 4.10).
        public readonly bool Lan;
        public readonly string RuleName;
        // Nice to have, never needed: a program of the household's on it
        // is no clash (no pop-up, Setup goes on), and Domovoi does without
        // it. The short address on port 80 (web/backend/short_address.py).
        public readonly bool Optional;

        public PortSpec(int first, int last, string what, bool lan, string ruleName)
            : this(first, last, what, lan, ruleName, false) { }

        public PortSpec(int first, int last, string what, bool lan, string ruleName, bool optional)
        {
            First = first; Last = last; What = what; Lan = lan; RuleName = ruleName; Optional = optional;
        }

        public bool Contains(int port) { return port >= First && port <= Last; }

        public string Ports { get { return First == Last ? Inv.S(First) : Inv.S(First) + "-" + Inv.S(Last); } }
    }

    public sealed class Listener
    {
        public int Port;
        public int Pid;
        // As Windows names the process, without ".exe"; null when it is gone
        // or can't be read.
        public string Process;

        public Listener(int port, int pid, string process) { Port = port; Pid = pid; Process = process; }
    }

    public sealed class PortOwner
    {
        // docker, ollama, windows (a reserved range or the System process), other.
        public string Kind;
        // What the pop-up calls it: "Docker Desktop", "Ollama", "nginx (PID 1234)".
        public string Name;
        public List<int> Ports = new List<int>();
        public string Title;
        public string Advice;
    }

    public static class DomovoiPorts
    {
        // AGENTS.md: dashboard 6369, core 6370, Postgres 6432 (published on
        // 127.0.0.1), Ollama 11434, per-room MPD control 6650+N and stream
        // 8050+N. PLAN 4.1/4.10: the plugin ports Jellyfin 8096, RomM 7676,
        // Kiwix 9467, and 8050-8099 for the room streams. 6650-6699 (the MPD
        // control ports, loopback in the distro) is checked too: under
        // mirrored networking a Windows listener there would stop a room.
        //
        // Port 80 is the short address (1.4.0): the dashboard listens there
        // too, only to send browsers on to 6369, so domovoi.local opens
        // without :6369. Optional: when IIS or another program holds it,
        // the dashboard leaves it alone and Settings says so; it still gets
        // its firewall rule, so it works once the port is free.
        public static readonly PortSpec[] All =
        {
            new PortSpec(80, 80, "the short address (domovoi.local without :6369)", true, "Domovoi-ShortAddress-80", true),
            new PortSpec(6369, 6369, "the dashboard", true, "Domovoi-Dashboard-6369"),
            new PortSpec(6370, 6370, "the voice service (satellites connect here)", true, "Domovoi-Core-6370"),
            new PortSpec(6432, 6432, "the database", false, null),
            new PortSpec(6650, 6699, "the music rooms' control ports", false, null),
            new PortSpec(8050, 8099, "the music rooms' streams", true, "Domovoi-MPD-8050-8099"),
            new PortSpec(8096, 8096, "the Jellyfin plugin", true, "Domovoi-Jellyfin-8096"),
            new PortSpec(7676, 7676, "the RomM plugin", true, "Domovoi-RomM-7676"),
            new PortSpec(9467, 9467, "the Kiwix plugin", true, "Domovoi-Kiwix-9467"),
            new PortSpec(11434, 11434, "the AI models (Ollama)", false, null),
        };

        // A port Domovoi can't do without (an optional one is not).
        public static bool Needed(int port)
        {
            foreach (var s in All) if (!s.Optional && s.Contains(port)) return true;
            return false;
        }

        // The short address's port (Optional above).
        public const int ShortAddressPort = 80;

        public static PortSpec SpecFor(int port)
        {
            foreach (var s in All) if (s.Contains(port)) return s;
            return null;
        }
    }

    public sealed class PortRange
    {
        public int First;
        public int Last;
        public PortRange(int first, int last) { First = first; Last = last; }
    }

    public static class PortCheck
    {
        static readonly Regex RangeLine = new Regex(@"^\s*(\d{1,5})\s+(\d{1,5})\s*\*?\s*$");

        // The ranges in `netsh int ipv4 show excludedportrange protocol=tcp`.
        // Only the number rows count, so the localized headings don't matter.
        public static List<PortRange> ParseExcludedRanges(string netshOutput)
        {
            var list = new List<PortRange>();
            if (netshOutput == null) return list;
            foreach (string raw in netshOutput.Split('\n'))
            {
                Match m = RangeLine.Match(raw.TrimEnd('\r'));
                if (!m.Success) continue;
                int a = int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
                int b = int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture);
                if (a < 1 || b > 65535 || b < a) continue;
                list.Add(new PortRange(a, b));
            }
            return list;
        }

        static string Kind(string process)
        {
            if (process == null) return "other";
            string p = process.Trim().ToLowerInvariant();
            if (p.EndsWith(".exe", StringComparison.Ordinal)) p = p.Substring(0, p.Length - 4);
            if (p.StartsWith("com.docker.", StringComparison.Ordinal) || p == "docker" || p == "dockerd"
                || p == "docker desktop" || p == "vpnkit" || p == "wslrelay")
                return "docker";
            if (p == "ollama" || p == "ollama app" || p.StartsWith("ollama_", StringComparison.Ordinal) || p.StartsWith("ollama ", StringComparison.Ordinal))
                return "ollama";
            if (p == "system") return "windows";
            return "other";
        }

        // One owner per program that holds a Domovoi port, then one for the
        // ports inside Windows' excluded ranges that nothing listens on.
        // Listeners on ports Domovoi doesn't need are ignored: a household
        // Docker Desktop or Ollama on its own ports gets no warning at all.
        public static List<PortOwner> Owners(IEnumerable<Listener> listeners, IEnumerable<PortRange> excluded)
        {
            var owners = new List<PortOwner>();
            var byKey = new Dictionary<string, PortOwner>();
            var taken = new HashSet<int>();
            foreach (var l in listeners)
            {
                if (!DomovoiPorts.Needed(l.Port)) continue;
                string kind = Kind(l.Process);
                string key, name;
                switch (kind)
                {
                    case "docker": key = "docker"; name = "Docker Desktop"; break;
                    case "ollama": key = "ollama"; name = "Ollama"; break;
                    case "windows": key = "windows-system"; name = "Windows (the System process)"; break;
                    default:
                        if (string.IsNullOrEmpty(l.Process))
                        {
                            key = "pid:" + Inv.S(l.Pid);
                            name = "A program that has exited or can't be read (PID " + Inv.S(l.Pid) + ")";
                        }
                        else
                        {
                            key = "proc:" + l.Process.ToLowerInvariant();
                            name = l.Process;
                        }
                        break;
                }
                PortOwner o;
                if (!byKey.TryGetValue(key, out o))
                {
                    o = new PortOwner();
                    o.Kind = kind;
                    o.Name = name;
                    byKey[key] = o;
                    owners.Add(o);
                }
                if (!o.Ports.Contains(l.Port)) o.Ports.Add(l.Port);
                taken.Add(l.Port);
            }
            var reserved = new PortOwner();
            reserved.Kind = "windows";
            reserved.Name = "Windows (a reserved port range)";
            if (excluded != null)
            {
                foreach (var r in excluded)
                {
                    foreach (var s in DomovoiPorts.All)
                    {
                        if (s.Optional) continue;
                        int a = Math.Max(r.First, s.First), b = Math.Min(r.Last, s.Last);
                        for (int p = a; p <= b; p++)
                            if (!taken.Contains(p) && !reserved.Ports.Contains(p)) reserved.Ports.Add(p);
                    }
                }
            }
            if (reserved.Ports.Count > 0) owners.Add(reserved);
            foreach (var o in owners)
            {
                o.Ports.Sort();
                Describe(o);
            }
            return owners;
        }

        static void Describe(PortOwner o)
        {
            string ports = Format(o.Ports);
            string what = Uses(o.Ports);
            bool one = o.Ports.Count == 1;
            o.Title = o.Name + (one ? " is using port " : " is using ports ") + ports + ", which Domovoi needs";
            switch (o.Kind)
            {
                case "docker":
                    o.Advice = "Docker Desktop publishes " + (one ? "port " : "ports ") + ports + " (" + what + "). "
                        + "Stop the container that uses " + (one ? "it" : "them") + " (Docker Desktop, Containers), "
                        + "or give that container another port, then click Retry. "
                        + "Docker Desktop itself can keep running on its other ports.";
                    break;
                case "ollama":
                    o.Advice = "Ollama for Windows listens on " + ports + " (" + what + "). "
                        + "Domovoi runs its own copy. Quit Ollama from its icon in the notification area, "
                        + "or move it to another port (the OLLAMA_HOST setting), then click Retry.";
                    break;
                case "windows":
                    if (o.Name.StartsWith("Windows (a reserved", StringComparison.Ordinal))
                        o.Advice = "Windows has reserved " + (one ? "port " : "ports ") + ports + " (" + what + ") for itself "
                            + "(an excluded port range, often taken by Hyper-V or WSL when Windows starts). "
                            + "Restarting Windows usually moves the range; then run Setup again.";
                    else
                        o.Advice = "A Windows service (often a web server through HTTP.sys) holds " + ports + " (" + what + "). "
                            + "Stop that service or move it to another port, then click Retry.";
                    break;
                default:
                    o.Advice = o.Name + " is listening on " + ports + " (" + what + "). "
                        + "Close it or move it to another port, then click Retry.";
                    break;
            }
        }

        // One line for a log or a status message: "6432 (Docker Desktop);
        // 11434 (Ollama)".
        public static string Summary(List<PortOwner> owners)
        {
            var parts = new List<string>();
            foreach (var o in owners) parts.Add(Format(o.Ports) + " (" + o.Name + ")");
            return string.Join("; ", parts.ToArray());
        }

        // "6432", "8050-8052", "6369, 8050-8052".
        public static string Format(List<int> ports)
        {
            var sorted = new List<int>(ports);
            sorted.Sort();
            var sb = new StringBuilder();
            int i = 0;
            while (i < sorted.Count)
            {
                int j = i;
                while (j + 1 < sorted.Count && sorted[j + 1] == sorted[j] + 1) j++;
                if (sb.Length > 0) sb.Append(", ");
                sb.Append(Inv.S(sorted[i]));
                if (j > i) sb.Append('-').Append(Inv.S(sorted[j]));
                i = j + 1;
            }
            return sb.ToString();
        }

        static string Uses(List<int> ports)
        {
            var whats = new List<string>();
            foreach (int p in ports)
            {
                var s = DomovoiPorts.SpecFor(p);
                if (s != null && !whats.Contains(s.What)) whats.Add(s.What);
            }
            return string.Join("; ", whats.ToArray());
        }
    }

    // Is port 80 free on this PC for the short address? Read-only and for
    // the log only: Setup never stops for it, and never changes what it
    // finds. Under mirrored networking a Windows program on port 80 (IIS,
    // or anything through HTTP.sys, which shows as the System process)
    // stops the dashboard binding it inside the distro, and the dashboard
    // then says in Settings that the short address is off. A URL
    // reservation (`netsh http show urlacl`) holds no port by itself, but
    // says a program may take port 80 through HTTP.sys later.
    public static class ShortAddressCheck
    {
        static readonly Regex UrlLine = new Regex(@"^\s*[^:]+:\s*(https?://\S+)\s*$", RegexOptions.IgnoreCase);
        static readonly Regex PortIn = new Regex(@"^https?://[^/]*:(\d{1,5})(/|$)", RegexOptions.IgnoreCase);

        // The URL Windows reserves on every PC (WCF's), which no program
        // listens on unless it asks: not worth a line.
        public const string WindowsDefault = "http://+:80/Temporary_Listen_Addresses/";

        // The reserved URLs on port 80 in `netsh http show urlacl`, the
        // Windows default left out. Only lines whose value is a URL count,
        // so the localized labels ("Reserved URL", "Reservierte URL")
        // don't matter.
        public static List<string> ParseUrlAcl(string netshOutput)
        {
            var list = new List<string>();
            if (netshOutput == null) return list;
            foreach (string raw in netshOutput.Split('\n'))
            {
                Match m = UrlLine.Match(raw.TrimEnd('\r'));
                if (!m.Success) continue;
                string url = m.Groups[1].Value;
                Match p = PortIn.Match(url);
                if (!p.Success || p.Groups[1].Value != Inv.S(DomovoiPorts.ShortAddressPort)) continue;
                if (string.Equals(url, WindowsDefault, StringComparison.OrdinalIgnoreCase)) continue;
                if (!list.Contains(url)) list.Add(url);
            }
            return list;
        }

        // "free", or "taken|<who>" when a Windows program listens on port 80.
        public static string State(IEnumerable<Listener> listeners)
        {
            foreach (var l in listeners)
            {
                if (l.Port != DomovoiPorts.ShortAddressPort) continue;
                string who = string.IsNullOrEmpty(l.Process) ? "PID " + Inv.S(l.Pid)
                    : string.Equals(l.Process, "System", StringComparison.OrdinalIgnoreCase) ? "Windows (the System process: IIS or another HTTP.sys program)"
                    : l.Process;
                return "taken|" + who;
            }
            return "free";
        }
    }

    // WIN-10 (round 6): the port-80 firewall rule follows the short
    // address's switch. The switch lives in the distro
    // (~/.domovoi/short_address.json, web/backend/short_address.py:
    // read_enabled, on unless it says "enabled": false). DomovoiHost reads
    // it every 30 s as the service user, like the other requests, and keeps
    // the answer in host-state.json ("short_address_enabled"); Setup and
    // every update add the rule only when that answer isn't false and no
    // Windows program holds port 80. DomovoiHost can't change the firewall
    // itself (it is not an administrator), so a switch turned off takes
    // the rule away at the next update or Setup run.
    public static class ShortAddressSwitch
    {
        public const string ServiceUser = "domovoi";
        public const string StateFile = "/home/" + ServiceUser + "/.domovoi/short_address.json";
        public const string HostStateKey = "short_address_enabled";
        public const int MaxBytes = 4096;

        // As the service user, under timeout, never more than MaxBytes + 1.
        public static string[] ReadArgs(string distro)
        {
            return new[] { "-d", distro, "-u", ServiceUser, "--cd", "/", "--exec", "/usr/bin/timeout", "-s", "KILL", "5",
                           "/usr/bin/head", "-c", Inv.S(MaxBytes + 1), StateFile };
        }

        // The switch as the dashboard reads it: false only for a JSON object
        // whose "enabled" is false; true for no file (exitCode 1 from head),
        // anything else, or a file it can't parse (read_enabled's rule).
        // Null when the read itself failed (keep what was known).
        public static bool? Parse(int exitCode, string stdout)
        {
            if (exitCode == 1) return true;           // head: no such file
            if (exitCode != 0 || stdout == null) return null;
            if (stdout.Length > MaxBytes) return true;
            try
            {
                Dictionary<string, object> d = Json.ParseObject(stdout);
                bool? v = Json.Bool(d, "enabled");
                return v == false ? false : true;
            }
            catch (FormatException) { return true; }
        }

        // Whether Setup or an update adds the port-80 rule: the switch isn't
        // known to be off, and no Windows program holds the port (then the
        // dashboard can't bind it either, and the rule would only open port
        // 80 of the household's own WSL VMs).
        public static bool RuleWanted(bool? switchOn, string portState)
        {
            if (switchOn == false) return false;
            return portState == null || portState == "free";
        }
    }
}
