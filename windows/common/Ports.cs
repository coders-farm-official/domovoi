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

        public PortSpec(int first, int last, string what, bool lan, string ruleName)
        {
            First = first; Last = last; What = what; Lan = lan; RuleName = ruleName;
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
        public static readonly PortSpec[] All =
        {
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

        public static bool Needed(int port)
        {
            foreach (var s in All) if (s.Contains(port)) return true;
            return false;
        }

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
}
