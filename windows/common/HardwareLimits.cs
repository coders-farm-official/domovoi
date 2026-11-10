// "Domovoi may use up to N GB / N processor cores": the household's
// hardware ceiling for a Windows install (owner, 2026-11-10;
// docs/WINDOWS_HOST.md, "How much of this PC Domovoi may use").
//
// The ceiling is the WSL VM's size: memory= and processors= in the hidden
// account's .wslconfig (WslConfig.cs), which WSL reads when that account's
// VM starts. The dashboard can't write a Windows file, and nothing in the
// distro can (interop and automount are off), so the channel runs through
// DomovoiHost, which polls:
//
//   * the request: ~/.domovoi/hardware_limits/request.json of the service
//     user, written by the core (domovoi/host_limits.py) for an admin. The
//     host reads it AS THAT USER (wsl.exe -u domovoi --exec /usr/bin/head
//     -c), so it reads nothing that user couldn't, at most MaxRequestBytes,
//     every Every while Domovoi runs. It is only data: Parse takes v, two
//     whole numbers or null (null: automatic, the installer's own value)
//     and an optional restart id of 32 hex digits, refuses anything else,
//     and clamps the numbers into this PC's bounds (Bounds). Nothing from
//     it reaches a shell, a path or a Windows program.
//   * the choice: kept in host\out\host-state.json ("limits"), so the host
//     can write .wslconfig at its next start before the distro is up. Apply
//     puts it into the installer's .wslconfig text (the job's), changing the
//     memory= line and adding processors= in [wsl2], and nothing else.
//   * the report: /var/lib/domovoi-windows/limits.json, written as root on
//     wsl.exe's stdin (as side.json is, WindowsSide.cs), 0644 in a folder
//     only root can write: this PC's RAM and logical processors, the bounds,
//     the default, the choice, and what became of the last request. The
//     core believes it only while root owns it alone.
//   * a restart: a request with a restart id the host hasn't seen restarts
//     Domovoi's Linux system so the VM starts with the new size: the units
//     stopped, the distro terminated, `wsl --shutdown` as domovoi-svc (this
//     account's WSL only: the household's own WSL and Docker run on), then
//     the usual bring-up. At most one per RestartGap; never while
//     domovoi-update.service runs (an update or the dashboard's Restart:
//     it waits, "busy", and asks again at the next poll); never for an id
//     older than RestartMaxAge by its request's `at`; and only once the id
//     is recorded in host-state.json, so a request can't restart it twice
//     (DecideRestart).
//
// Without a restart the new size applies when the VM next starts on its
// own: after Windows restarts, or a repair.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    // null: automatic.
    public sealed class LimitsChoice
    {
        public int? MemoryGb;
        public int? Processors;

        public bool SameAs(LimitsChoice o)
        {
            return o != null && MemoryGb == o.MemoryGb && Processors == o.Processors;
        }

        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["memory_gb"] = MemoryGb.HasValue ? (object)MemoryGb.Value : null;
            d["processors"] = Processors.HasValue ? (object)Processors.Value : null;
            return d;
        }

        public static LimitsChoice FromJson(Dictionary<string, object> d)
        {
            var c = new LimitsChoice();
            if (d == null) return c;
            c.MemoryGb = Json.Int(d, "memory_gb");
            c.Processors = Json.Int(d, "processors");
            return c;
        }
    }

    public sealed class LimitsBounds
    {
        public int RamGb;
        public int LogicalProcessors;
        public int MemoryMin;
        public int MemoryMax;
        public int ProcessorsMin;
        public int ProcessorsMax;
    }

    public sealed class LimitsRequest
    {
        // none (no file), ok, clamped (a number was outside this PC's
        // bounds and was moved inside), invalid (Problem says why; the
        // stored choice is left as it was).
        public string State = "none";
        public string Problem;
        public LimitsChoice Choice;
        public string RestartId;
        // The request's `at` (when the core wrote it), as written and as a
        // time; null when it has none.
        public string At;
        public DateTime? AtUtc;
    }

    // What to do about a request's restart id (HardwareLimits.DecideRestart).
    public enum LimitsRestart { None, Stale, TooSoon, Busy, Go }

    public static class HardwareLimits
    {
        public const string ServiceUser = "domovoi";
        public const string RequestFile = "/home/" + ServiceUser + "/.domovoi/hardware_limits/request.json";
        public const int MaxRequestBytes = 2048;
        public const string ReportDir = WindowsSideReport.Dir;
        public const string ReportFile = ReportDir + "/limits.json";
        public static readonly TimeSpan Every = TimeSpan.FromSeconds(30);
        public static readonly TimeSpan RestartGap = TimeSpan.FromMinutes(10);
        public const int TimeoutMs = 20000;

        // Never less than this for Domovoi (owner): Whisper, the database and
        // the rooms' players need it even with the AI models off.
        public const int MemoryFloorGb = 8;
        // What Windows keeps for itself at the top of the range.
        public const int WindowsReserveGb = 4;
        public const int ProcessorsFloor = 2;

        static readonly Regex HexId = new Regex("^[0-9a-f]{32}\\z");
        static readonly Regex AtShape = new Regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\\z");

        // A restart id older than this (by its request's `at`) is never
        // acted on: written before Windows restarted, say, so the VM already
        // runs the new size. The core also takes a done id back.
        public static readonly TimeSpan RestartMaxAge = TimeSpan.FromMinutes(15);
        static readonly string[] UpdateBusyStates = { "active", "activating", "deactivating", "reloading", "refreshing" };

        // What to do about the request's restart id. doneId: the last one
        // acted on (host-state.json); stateReadable: false when that file
        // can't be read, and then nothing is done (an id that can't be
        // recorded would restart again at every poll). updateState:
        // domovoi-update.service's ActiveState, null when unknown, which
        // waits too: a restart must never cut an update off.
        public static LimitsRestart DecideRestart(LimitsRequest req, string doneId, bool stateReadable, DateTime nowUtc,
                                                  DateTime lastRestartUtc, string updateState)
        {
            if (req == null || req.RestartId == null || req.State == "invalid" || !stateReadable) return LimitsRestart.None;
            if (req.RestartId == doneId) return LimitsRestart.None;
            if (req.AtUtc == null || nowUtc - req.AtUtc.Value > RestartMaxAge || req.AtUtc.Value - nowUtc > TimeSpan.FromMinutes(5))
                return LimitsRestart.Stale;
            if (nowUtc - lastRestartUtc < RestartGap) return LimitsRestart.TooSoon;
            if (updateState == null || Array.IndexOf(UpdateBusyStates, updateState.Trim()) >= 0) return LimitsRestart.Busy;
            return LimitsRestart.Go;
        }

        public static string[] UpdateStateArgs(string distro)
        {
            return new[] { "-d", distro, "-u", "root", "--cd", "/", "--exec", "/usr/bin/systemctl", "show", "-p", "ActiveState",
                           "--value", "domovoi-update.service" };
        }
        static readonly HashSet<string> Keys = new HashSet<string>(StringComparer.Ordinal)
            { "v", "memory_gb", "processors", "restart_id", "at" };

        // Installed RAM rounds to its label: a "32 GB" PC reads 31.7.
        public static int LabelledGb(long ramBytes)
        {
            return (int)Math.Round(ramBytes / (1024.0 * 1024.0 * 1024.0));
        }

        public static LimitsBounds Bounds(long ramBytes, int logicalProcessors)
        {
            return Bounds(ramBytes, 0, logicalProcessors);
        }

        // ramBytes: installed (the label the page shows); usableBytes: what
        // Windows can use (GlobalMemoryStatusEx's ullTotalPhys, a little
        // under: the firmware and the GPU take some), which the maximum is
        // counted from, whole GB down (review 2026-11-10). 0: unknown, then
        // the installed figure.
        public static LimitsBounds Bounds(long ramBytes, long usableBytes, int logicalProcessors)
        {
            var b = new LimitsBounds();
            b.RamGb = LabelledGb(ramBytes > 0 ? ramBytes : usableBytes);
            b.LogicalProcessors = Math.Max(1, logicalProcessors);
            b.MemoryMin = MemoryFloorGb;
            int usableGb = usableBytes > 0 ? (int)Math.Floor(usableBytes / (1024.0 * 1024.0 * 1024.0)) : b.RamGb;
            b.MemoryMax = Math.Max(MemoryFloorGb, usableGb - WindowsReserveGb);
            b.ProcessorsMin = Math.Min(ProcessorsFloor, b.LogicalProcessors);
            b.ProcessorsMax = b.LogicalProcessors;
            return b;
        }

        // A stored choice inside this PC's bounds as they are now (RAM can
        // be taken out between two starts).
        public static LimitsChoice Within(LimitsChoice c, LimitsBounds b)
        {
            var o = new LimitsChoice();
            if (c == null) return o;
            bool moved = false;
            if (c.MemoryGb.HasValue) o.MemoryGb = Clamp(c.MemoryGb.Value, b.MemoryMin, b.MemoryMax, ref moved);
            if (c.Processors.HasValue) o.Processors = Clamp(c.Processors.Value, b.ProcessorsMin, b.ProcessorsMax, ref moved);
            return o;
        }

        public static string Describe(LimitsChoice c)
        {
            if (c == null) c = new LimitsChoice();
            return "memory " + (c.MemoryGb.HasValue ? Inv.S(c.MemoryGb.Value) + " GB" : "automatic")
                + ", processors " + (c.Processors.HasValue ? Inv.S(c.Processors.Value) : "automatic");
        }

        static int Clamp(int v, int lo, int hi, ref bool moved)
        {
            if (v < lo) { moved = true; return lo; }
            if (v > hi) { moved = true; return hi; }
            return v;
        }

        // A number or null; anything else (a string, a fraction, a list) is
        // refused with why.
        static bool WholeOrNull(Dictionary<string, object> d, string key, out int? value, out string why)
        {
            value = null;
            why = null;
            if (Json.IsNull(d, key)) return true;
            int? v = Json.Int(d, key);
            if (v == null) { why = key + " is not a whole number"; return false; }
            value = v;
            return true;
        }

        // The request file's text (null: no file) into what it asks for.
        public static LimitsRequest Parse(string text, LimitsBounds b)
        {
            var r = new LimitsRequest();
            if (text == null) return r;
            r.State = "invalid";
            if (text.Length > MaxRequestBytes) { r.Problem = "the request is too large"; return r; }
            Dictionary<string, object> d;
            try { d = Json.ParseObject(text); }
            catch (FormatException) { r.Problem = "the request is not a JSON object"; return r; }
            foreach (string k in d.Keys)
                if (!Keys.Contains(k)) { r.Problem = "the request has a key it may not have"; return r; }
            if (Json.Int(d, "v") != 1) { r.Problem = "the request's version is not 1"; return r; }
            int? mem, cpus;
            string why;
            if (!WholeOrNull(d, "memory_gb", out mem, out why) || !WholeOrNull(d, "processors", out cpus, out why))
            {
                r.Problem = why;
                return r;
            }
            if (!Json.IsNull(d, "restart_id"))
            {
                string id = Json.Str(d, "restart_id");
                if (id == null || !HexId.IsMatch(id)) { r.Problem = "restart_id is not 32 hex digits"; return r; }
                r.RestartId = id;
            }
            if (!Json.IsNull(d, "at"))
            {
                string at = Json.Str(d, "at");
                DateTime t;
                if (at == null || !AtShape.IsMatch(at)
                    || !DateTime.TryParseExact(at, "yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture,
                        DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out t))
                {
                    r.Problem = "at is not a UTC time";
                    r.RestartId = null;
                    return r;
                }
                r.At = at;
                r.AtUtc = DateTime.SpecifyKind(t, DateTimeKind.Utc);
            }
            bool moved = false;
            var c = new LimitsChoice();
            if (mem.HasValue) c.MemoryGb = Clamp(mem.Value, b.MemoryMin, b.MemoryMax, ref moved);
            if (cpus.HasValue) c.Processors = Clamp(cpus.Value, b.ProcessorsMin, b.ProcessorsMax, ref moved);
            r.Choice = c;
            r.State = moved ? "clamped" : "ok";
            return r;
        }

        // The installer's .wslconfig text with the choice in [wsl2]: memory=
        // replaced when a size is chosen, processors= removed and, when a
        // count is chosen, put right after memory=. FormatException when the
        // text has no [wsl2] memory= line to work from.
        public static string Apply(string wslconfig, LimitsChoice c)
        {
            if (wslconfig == null) throw new FormatException("no .wslconfig text");
            if (c == null) c = new LimitsChoice();
            if (c.MemoryGb.HasValue && (c.MemoryGb.Value < 1 || c.MemoryGb.Value > 1024)) throw new ArgumentException("memory out of range");
            if (c.Processors.HasValue && (c.Processors.Value < 1 || c.Processors.Value > 1024)) throw new ArgumentException("processors out of range");
            string[] lines = wslconfig.Replace("\r\n", "\n").Split('\n');
            var sb = new StringBuilder();
            string section = "";
            bool sawMemory = false;
            for (int i = 0; i < lines.Length; i++)
            {
                string line = lines[i];
                string t = line.Trim();
                if (t.StartsWith("[", StringComparison.Ordinal)) section = t.ToLowerInvariant();
                bool last = i == lines.Length - 1;
                if (section == "[wsl2]" && KeyOf(t) == "processors") continue;
                if (section == "[wsl2]" && KeyOf(t) == "memory" && !sawMemory)
                {
                    sawMemory = true;
                    sb.Append(c.MemoryGb.HasValue ? "memory=" + Inv.S(c.MemoryGb.Value) + "GB" : line);
                    if (c.Processors.HasValue) sb.Append("\nprocessors=" + Inv.S(c.Processors.Value));
                    if (!last) sb.Append('\n');
                    continue;
                }
                sb.Append(line);
                if (!last) sb.Append('\n');
            }
            if (!sawMemory) throw new FormatException("the .wslconfig text has no memory= line in [wsl2]");
            return sb.ToString();
        }

        static string KeyOf(string trimmed)
        {
            if (trimmed.StartsWith("#", StringComparison.Ordinal) || trimmed.StartsWith(";", StringComparison.Ordinal)) return null;
            int eq = trimmed.IndexOf('=');
            return eq <= 0 ? null : trimmed.Substring(0, eq).Trim().ToLowerInvariant();
        }

        // memory= (whole GB) and processors= in [wsl2] of a .wslconfig text;
        // null for one that isn't there or isn't in that shape.
        public static LimitsChoice InFile(string wslconfig)
        {
            var c = new LimitsChoice();
            if (wslconfig == null) return c;
            string section = "";
            foreach (string raw in wslconfig.Replace("\r\n", "\n").Split('\n'))
            {
                string t = raw.Trim();
                if (t.StartsWith("[", StringComparison.Ordinal)) { section = t.ToLowerInvariant(); continue; }
                if (section != "[wsl2]") continue;
                string key = KeyOf(t);
                if (key == null) continue;
                string v = t.Substring(t.IndexOf('=') + 1).Trim();
                int n;
                if (key == "memory" && v.EndsWith("GB", StringComparison.OrdinalIgnoreCase)
                    && int.TryParse(v.Substring(0, v.Length - 2), NumberStyles.None, CultureInfo.InvariantCulture, out n))
                    c.MemoryGb = n;
                if (key == "processors" && int.TryParse(v, NumberStyles.None, CultureInfo.InvariantCulture, out n))
                    c.Processors = n;
            }
            return c;
        }

        // limits.json's text. Everything in it is a number, a bool, null or
        // one of this file's own words, serialized by the JSON writer.
        public static string Report(LimitsBounds b, LimitsChoice defaults, LimitsChoice chosen, LimitsChoice inFile,
                                    LimitsRequest last, string restartState, DateTime utc)
        {
            var d = new Dictionary<string, object>();
            d["schema"] = 1;
            d["ram_gb"] = b.RamGb;
            d["logical_processors"] = b.LogicalProcessors;
            var mem = new Dictionary<string, object>();
            mem["min"] = b.MemoryMin;
            mem["max"] = b.MemoryMax;
            mem["default"] = defaults != null && defaults.MemoryGb.HasValue ? (object)defaults.MemoryGb.Value : null;
            mem["chosen"] = chosen != null && chosen.MemoryGb.HasValue ? (object)chosen.MemoryGb.Value : null;
            mem["in_file"] = inFile != null && inFile.MemoryGb.HasValue ? (object)inFile.MemoryGb.Value : null;
            d["memory_gb"] = mem;
            var cpu = new Dictionary<string, object>();
            cpu["min"] = b.ProcessorsMin;
            cpu["max"] = b.ProcessorsMax;
            // WSL's own default: every logical processor.
            cpu["default"] = b.LogicalProcessors;
            cpu["chosen"] = chosen != null && chosen.Processors.HasValue ? (object)chosen.Processors.Value : null;
            cpu["in_file"] = inFile != null && inFile.Processors.HasValue ? (object)inFile.Processors.Value : null;
            d["processors"] = cpu;
            d["request"] = last == null ? "none" : last.State;
            d["request_problem"] = last == null ? null : last.Problem;
            // Which request this report has read (its `at`): the core tells
            // "saved, not read yet" from it, whatever was clamped.
            d["request_at"] = last == null ? null : last.At;
            d["restart"] = restartState ?? "none";
            d["written_at"] = utc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            return Json.Serialize(d) + "\n";
        }

        // Read the request as the service user: head -c stops at one byte
        // past the limit, so a bigger file reads as too large.
        public static string[] ReadArgs(string distro)
        {
            // Under timeout -s KILL 5: a FIFO put where the request goes
            // would otherwise hold the read (and the host's loop) until
            // TimeoutMs (review 2026-11-10).
            return new[] { "-d", distro, "-u", ServiceUser, "--cd", "/", "--exec", "/usr/bin/timeout", "-s", "KILL", "5",
                           "/usr/bin/head", "-c", Inv.S(MaxRequestBytes + 1), RequestFile };
        }

        // As root, the report on stdin, like side.json.
        public static string ReportScript()
        {
            return "set -e; umask 022; mkdir -p " + ReportDir + "; chown root:root " + ReportDir + "; chmod 0755 " + ReportDir + "; "
                + "cat > " + ReportDir + "/.limits.json.new; chmod 0644 " + ReportDir + "/.limits.json.new; "
                + "mv -f " + ReportDir + "/.limits.json.new " + ReportFile + "; sha256sum " + ReportFile;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct MEMORYSTATUSEX
        {
            public uint dwLength;
            public uint dwMemoryLoad;
            public ulong ullTotalPhys;
            public ulong ullAvailPhys;
            public ulong ullTotalPageFile;
            public ulong ullAvailPageFile;
            public ulong ullTotalVirtual;
            public ulong ullAvailVirtual;
            public ulong ullAvailExtendedVirtual;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool GetPhysicallyInstalledSystemMemory(out ulong kilobytes);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool GlobalMemoryStatusEx(ref MEMORYSTATUSEX status);

        // This PC's installed RAM (what the installer sized the VM from);
        // what Windows can use when the firmware won't say. 0: unknown.
        public static long InstalledRamBytes()
        {
            ulong kb;
            if (GetPhysicallyInstalledSystemMemory(out kb) && kb > 0) return (long)kb * 1024;
            var s = new MEMORYSTATUSEX();
            s.dwLength = (uint)Marshal.SizeOf(typeof(MEMORYSTATUSEX));
            if (GlobalMemoryStatusEx(ref s)) return (long)s.ullTotalPhys;
            return 0;
        }

        // What Windows can use (ullTotalPhys): the maximum is counted from
        // this. 0: unknown.
        public static long UsableRamBytes()
        {
            var s = new MEMORYSTATUSEX();
            s.dwLength = (uint)Marshal.SizeOf(typeof(MEMORYSTATUSEX));
            return GlobalMemoryStatusEx(ref s) ? (long)s.ullTotalPhys : 0;
        }
    }
}
