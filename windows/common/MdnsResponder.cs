// The server's name on the home network (design note MDNS-NAME-PLAN;
// docs/WINDOWS_HOST.md, "The network name"): DomovoiHost answers to
// domovoi.local for this PC, because the core runs in a private WSL distro
// that isn't the one on the network.
//
// The C# copy of domovoi/lan_name/ (names.py, packet.py, responder.py): the
// same rules, the same packets byte for byte (domovoi/lan_name/vectors.json,
// run by tests\Tests.cs), the same state machine with no socket and no
// clock of its own. The socket and the thread are windows\host\LanNameHost.cs.
//
// Anyone on the home network can send these packets, and DomovoiHost runs as
// a service account, so the parser is bounded everywhere (a security review
// reads this file): a packet over MaxPacket bytes, a non-zero opcode or
// rcode, more than MaxQuestions questions or MaxRecords records is dropped
// whole; names are read in a loop, never by recursion; a compression pointer
// must point strictly before the run of labels it was found in, so every
// jump goes backwards and no loop can be built; at most MaxPointerJumps
// jumps, MaxLabels labels and 255 bytes per name; every length is checked
// against the packet before it is read; labels stay bytes, compared after
// an ASCII-only lowercase, never decoded or logged.
//
// The core asks through a file, as for the hardware limits and the media
// folders: ~/.domovoi/lan_name/request.json of the service user
//   {"v": 1, "id": "<32 hex>", "name": "kitchen", "enabled": true, "at": "<UTC>"}
// which the host reads AS that user (head -c, at most MaxRequestBytes) every
// LanNameFiles.Every and checks again (Parse: only these keys, the name by
// the shared rule). It answers with /var/lib/domovoi-windows/lan_name.json,
// written as root:
//   {"schema": 1, "request_id": ..., "requested": ..., "claimed": ..., "state": ...,
//    "taken": ..., "addresses": [...], "error": ..., "written_at": ...}
// The Mac (phase 4) writes the same report from its helper; phase 5
// (DNS-SD) adds service records beside the host record here.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    // -- the name rule (domovoi/lan_name/names.py) ------------------------

    public sealed class BadLanName : Exception
    {
        public BadLanName(string why) : base(why) { }
    }

    public static class LanName
    {
        public const string Default = "domovoi";
        public const int MaxSuffix = 99;
        public const int MaxLen = 63;
        const int MaxInput = 80;
        static readonly Regex Label = new Regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\z");

        // The label a typed name stands for; BadLanName with why.
        public static string Normalize(string raw)
        {
            if (raw == null) throw new BadLanName("no name given");
            string s = raw.Trim(' ', '\t', '\n', '\r', '\f', '\v');
            if (s.Length > MaxInput) throw new BadLanName("too long: up to 63 letters, digits and hyphens");
            s = s.ToLowerInvariant();
            if (s.EndsWith(".", StringComparison.Ordinal)) s = s.Substring(0, s.Length - 1);
            if (s.EndsWith(".local", StringComparison.Ordinal)) s = s.Substring(0, s.Length - 6);
            if (s.Length == 0) throw new BadLanName("no name given");
            if (s.Length > MaxLen) throw new BadLanName("too long: up to 63 letters, digits and hyphens");
            bool ascii = true;
            foreach (char c in s) if (c > 127) { ascii = false; break; }
            if (!ascii || !Label.IsMatch(s))
            {
                if (s.IndexOf('.') >= 0) throw new BadLanName("one word only: no dots");
                if (s.StartsWith("-", StringComparison.Ordinal) || s.EndsWith("-", StringComparison.Ordinal))
                    throw new BadLanName("can't start or end with a hyphen");
                throw new BadLanName("letters, digits and hyphens only");
            }
            if (s == "localhost" || s == "local") throw new BadLanName(s + " is kept for this device itself");
            return s;
        }

        public static bool IsValid(string raw)
        {
            try { Normalize(raw); return true; }
            catch (BadLanName) { return false; }
        }

        // A checked label exactly as given (not normalized), or null.
        public static string Exact(string raw)
        {
            if (raw == null) return null;
            try { return Normalize(raw) == raw ? raw : null; }
            catch (BadLanName) { return null; }
        }

        public static List<string> Family(string name)
        {
            var l = new List<string>();
            l.Add(name);
            if (name != Default) return l;
            for (int n = 2; n <= MaxSuffix; n++) l.Add(Default + Inv.S(n));
            return l;
        }

        public static bool InFamily(string requested, string name)
        {
            return name != null && Family(requested).Contains(name);
        }

        public static string Fqdn(string name) { return name == null ? null : name + ".local"; }
    }

    // -- packets (domovoi/lan_name/packet.py) ------------------------------

    public sealed class MdnsParseError : Exception
    {
        public MdnsParseError(string why) : base(why) { }
    }

    public sealed class MdnsQuestion
    {
        public byte[][] Name;
        public int Type;
        public int Class;
        public bool Unicast;
    }

    public sealed class MdnsRecord
    {
        public byte[][] Name;
        public int Type;
        public int Class;
        public bool Flush;
        public long Ttl;
        public byte[] Data;
    }

    public sealed class MdnsMessage
    {
        public int Id;
        public int Flags;
        public List<MdnsQuestion> Questions = new List<MdnsQuestion>();
        public List<MdnsRecord> Answers = new List<MdnsRecord>();
        public List<MdnsRecord> Authority = new List<MdnsRecord>();
        public List<MdnsRecord> Additional = new List<MdnsRecord>();
        public bool IsResponse { get { return (Flags & 0x8000) != 0; } }

        public IEnumerable<MdnsRecord> Records()
        {
            foreach (var r in Answers) yield return r;
            foreach (var r in Authority) yield return r;
            foreach (var r in Additional) yield return r;
        }
    }

    public static class MdnsPacket
    {
        public const string Group = "224.0.0.251";
        public const int Port = 5353;
        public const int MaxPacket = 9000;
        public const int MaxQuestions = 32;
        public const int MaxRecords = 64;
        public const int MaxPointerJumps = 16;
        public const int MaxLabels = 128;
        public const int MaxNameBytes = 255;
        public const int TA = 1, TAAAA = 28, TNsec = 47, TAny = 255, CIn = 1;
        public const int TopBit = 0x8000;
        public const int HostTtl = 120, LegacyTtl = 10;
        const int FlagQr = 0x8000, FlagAa = 0x0400;

        static int U16(byte[] b, int off) { return (b[off] << 8) | b[off + 1]; }

        static byte LowerAscii(byte c) { return c >= (byte)'A' && c <= (byte)'Z' ? (byte)(c + 32) : c; }

        // The name at off: labels lowercased (ASCII only), and the offset just
        // past it where it was written.
        public static byte[][] ReadName(byte[] buf, int off, out int next)
        {
            var labels = new List<byte[]>();
            int total = 1;
            int end = -1;
            int jumps = 0;
            int runStart = off;
            int n = buf.Length;
            while (true)
            {
                if (off >= n) throw new MdnsParseError("name runs past the packet");
                int ln = buf[off];
                if (ln == 0) { off += 1; break; }
                int kind = ln & 0xC0;
                if (kind == 0xC0)
                {
                    if (off + 1 >= n) throw new MdnsParseError("truncated pointer");
                    int ptr = ((ln & 0x3F) << 8) | buf[off + 1];
                    if (end < 0) end = off + 2;
                    jumps++;
                    if (jumps > MaxPointerJumps) throw new MdnsParseError("too many pointers");
                    if (ptr >= runStart) throw new MdnsParseError("pointer does not point backwards");
                    off = runStart = ptr;
                    continue;
                }
                if (kind != 0) throw new MdnsParseError("unsupported label type");
                if (off + 1 + ln > n) throw new MdnsParseError("label runs past the packet");
                total += 1 + ln;
                if (total > MaxNameBytes) throw new MdnsParseError("name too long");
                var label = new byte[ln];
                for (int i = 0; i < ln; i++) label[i] = LowerAscii(buf[off + 1 + i]);
                labels.Add(label);
                if (labels.Count > MaxLabels) throw new MdnsParseError("too many labels");
                off += 1 + ln;
            }
            next = end >= 0 ? end : off;
            return labels.ToArray();
        }

        static MdnsRecord ReadRecord(byte[] buf, int off, out int next)
        {
            var r = new MdnsRecord();
            r.Name = ReadName(buf, off, out off);
            if (off + 10 > buf.Length) throw new MdnsParseError("record header runs past the packet");
            r.Type = U16(buf, off);
            int cls = U16(buf, off + 2);
            r.Class = cls & ~TopBit;
            r.Flush = (cls & TopBit) != 0;
            r.Ttl = ((long)buf[off + 4] << 24) | ((long)buf[off + 5] << 16) | ((long)buf[off + 6] << 8) | buf[off + 7];
            int len = U16(buf, off + 8);
            off += 10;
            if (off + len > buf.Length) throw new MdnsParseError("record data runs past the packet");
            r.Data = new byte[len];
            Buffer.BlockCopy(buf, off, r.Data, 0, len);
            next = off + len;
            return r;
        }

        public static MdnsMessage Parse(byte[] buf, int length)
        {
            if (buf == null || length < 0 || length > buf.Length) throw new MdnsParseError("no packet");
            if (length > MaxPacket) throw new MdnsParseError("packet too large");
            if (length < 12) throw new MdnsParseError("short header");
            if (length != buf.Length)
            {
                var copy = new byte[length];
                Buffer.BlockCopy(buf, 0, copy, 0, length);
                buf = copy;
            }
            var m = new MdnsMessage();
            m.Id = U16(buf, 0);
            m.Flags = U16(buf, 2);
            int qd = U16(buf, 4), an = U16(buf, 6), ns = U16(buf, 8), ar = U16(buf, 10);
            if ((m.Flags & 0x7800) != 0 || (m.Flags & 0x000F) != 0) throw new MdnsParseError("opcode or rcode not zero");
            if (qd > MaxQuestions) throw new MdnsParseError("too many questions");
            if (an + ns + ar > MaxRecords) throw new MdnsParseError("too many records");
            int off = 12;
            for (int i = 0; i < qd; i++)
            {
                var q = new MdnsQuestion();
                q.Name = ReadName(buf, off, out off);
                if (off + 4 > buf.Length) throw new MdnsParseError("question runs past the packet");
                q.Type = U16(buf, off);
                int c = U16(buf, off + 2);
                q.Class = c & ~TopBit;
                q.Unicast = (c & TopBit) != 0;
                off += 4;
                m.Questions.Add(q);
            }
            for (int i = 0; i < an; i++) m.Answers.Add(ReadRecord(buf, off, out off));
            for (int i = 0; i < ns; i++) m.Authority.Add(ReadRecord(buf, off, out off));
            for (int i = 0; i < ar; i++) m.Additional.Add(ReadRecord(buf, off, out off));
            return m;
        }

        public static MdnsMessage Parse(byte[] buf) { return Parse(buf, buf == null ? 0 : buf.Length); }

        // -- building ----------------------------------------------------

        public static byte[][] NameOf(string label)
        {
            return new[] { Encoding.ASCII.GetBytes(label), Encoding.ASCII.GetBytes("local") };
        }

        public static bool SameName(byte[][] a, byte[][] b)
        {
            if (a == null || b == null || a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; i++)
            {
                if (a[i].Length != b[i].Length) return false;
                for (int j = 0; j < a[i].Length; j++) if (a[i][j] != b[i][j]) return false;
            }
            return true;
        }

        static void Name(List<byte> o, byte[][] name)
        {
            foreach (byte[] l in name) { o.Add((byte)l.Length); o.AddRange(l); }
            o.Add(0);
        }

        static void U16(List<byte> o, int v) { o.Add((byte)(v >> 8)); o.Add((byte)v); }

        static void U32(List<byte> o, long v) { o.Add((byte)(v >> 24)); o.Add((byte)(v >> 16)); o.Add((byte)(v >> 8)); o.Add((byte)v); }

        static void Header(List<byte> o, int id, int flags, int qd, int an, int ns, int ar)
        {
            U16(o, id); U16(o, flags); U16(o, qd); U16(o, an); U16(o, ns); U16(o, ar);
        }

        static void ARecord(List<byte> o, byte[][] name, byte[] addr, long ttl, bool flush)
        {
            Name(o, name);
            U16(o, TA); U16(o, CIn | (flush ? TopBit : 0)); U32(o, ttl); U16(o, 4);
            o.AddRange(addr);
        }

        static void NsecRecord(List<byte> o, byte[][] name, long ttl, bool flush)
        {
            var rdata = new List<byte>();
            Name(rdata, name);
            rdata.Add(0); rdata.Add(1); rdata.Add(0x40);
            Name(o, name);
            U16(o, TNsec); U16(o, CIn | (flush ? TopBit : 0)); U32(o, ttl); U16(o, rdata.Count);
            o.AddRange(rdata);
        }

        public static byte[] Probe(byte[][] name, byte[] addr)
        {
            var o = new List<byte>();
            Header(o, 0, 0, 1, 0, 1, 0);
            Name(o, name);
            U16(o, TAny); U16(o, CIn | TopBit);
            ARecord(o, name, addr, HostTtl, false);
            return o.ToArray();
        }

        public static byte[] Response(byte[][] name, byte[] addr, long ttl, int qtype)
        {
            var o = new List<byte>();
            if (qtype == TAAAA)
            {
                Header(o, 0, FlagQr | FlagAa, 0, 1, 0, 1);
                NsecRecord(o, name, ttl, true);
                ARecord(o, name, addr, ttl, true);
            }
            else if (qtype == TAny)
            {
                Header(o, 0, FlagQr | FlagAa, 0, 2, 0, 0);
                ARecord(o, name, addr, ttl, true);
                NsecRecord(o, name, ttl, true);
            }
            else
            {
                Header(o, 0, FlagQr | FlagAa, 0, 1, 0, 1);
                ARecord(o, name, addr, ttl, true);
                NsecRecord(o, name, ttl, true);
            }
            return o.ToArray();
        }

        public static byte[] Response(byte[][] name, byte[] addr, long ttl) { return Response(name, addr, ttl, TA); }

        public static byte[] LegacyResponse(int id, byte[][] name, byte[] addr, int qtype)
        {
            var o = new List<byte>();
            Header(o, id, FlagQr | FlagAa, 1, 1, 0, 0);
            Name(o, name);
            U16(o, qtype); U16(o, CIn);
            if (qtype == TAAAA) NsecRecord(o, name, LegacyTtl, false);
            else ARecord(o, name, addr, LegacyTtl, false);
            return o.ToArray();
        }
    }

    // -- the state machine (domovoi/lan_name/responder.py) -----------------

    public sealed class MdnsOut
    {
        public byte[] Data;
        // null: the group (224.0.0.251:5353).
        public IPEndPoint Dest;
    }

    public sealed class MdnsSnapshot
    {
        public string Requested;
        public string Claimed;
        public string State;
        public string Taken;
        public string Error;
    }

    public sealed class MdnsResponder
    {
        public const int ProbeCount = 3;
        public const double ProbeGap = 0.25, AnnounceGap = 1.0;
        public const int AnnounceCount = 2;
        public const int ConflictBurst = 15;
        public const double ConflictWindow = 10, ConflictPause = 5;
        public const double HeldRecheck = 15 * 60, ReprobeGap = 10;
        public const int MaxForcedRenames = 3;
        public const double RenameWindow = 3600;
        public const int MaxSendsPerSec = 20;
        const double EchoWindow = 2;
        const int EchoMax = 32;

        // The API's words (domovoi/lan_name/responder.py STATES).
        public static readonly string[] States = { "announced", "checking", "taken", "off", "no_lan", "failed" };

        readonly Func<ICollection<byte[]>> own;
        readonly Func<double> rng;

        public string Requested;
        public bool Enabled;
        public byte[] Address;
        // off, no_lan, probing, announcing, announced, held, waiting, gave_up, failed
        public string Phase = "off";
        public string Candidate;
        public string Claimed;
        public string Taken;
        public string Error;
        public double? NextAt;
        List<string> queue = new List<string>();
        HashSet<string> tried = new HashSet<string>();
        int probes;
        int announces;
        bool reprobe;
        double reprobeNotBefore;
        readonly Queue<double> renames = new Queue<double>();
        readonly Queue<double> conflicts = new Queue<double>();
        byte[][] announcedName;
        byte[] announcedAddr;
        readonly List<KeyValuePair<byte[], double>> sent = new List<KeyValuePair<byte[], double>>();
        readonly Dictionary<int, double> lastMcast = new Dictionary<int, double>();
        double budget = MaxSendsPerSec;
        double? budgetAt;

        public MdnsResponder(Func<ICollection<byte[]>> ownAddresses, Func<double> random)
        {
            own = ownAddresses ?? (() => new List<byte[]>());
            rng = random ?? (() => new Random().NextDouble());
        }

        public static bool PrivateSource(IPAddress ip)
        {
            if (ip == null || ip.AddressFamily != AddressFamily.InterNetwork) return false;
            byte[] b = ip.GetAddressBytes();
            return b[0] == 10 || (b[0] == 172 && (b[1] & 0xF0) == 16) || (b[0] == 192 && b[1] == 168) || (b[0] == 169 && b[1] == 254);
        }

        static byte[] AddrBytes(string ip)
        {
            if (!Lan.IsPrivateIPv4(ip)) return null;
            return IPAddress.Parse(ip).GetAddressBytes();
        }

        static bool SameBytes(byte[] a, byte[] b)
        {
            if (a == null || b == null || a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; i++) if (a[i] != b[i]) return false;
            return true;
        }

        // -- configuration -----------------------------------------------

        public List<MdnsOut> Configure(string requested, bool enabled, string address, double now, string sticky)
        {
            byte[] addr = AddrBytes(address);
            bool sameAddr = addr == null ? Address == null : SameBytes(addr, Address);
            if (requested == Requested && enabled == Enabled && sameAddr && Phase != "failed")
                return new List<MdnsOut>();
            var outs = Goodbye(now);
            string previous = Claimed ?? sticky;
            Requested = requested; Enabled = enabled; Address = addr;
            Candidate = null; Claimed = null; Taken = null; Error = null;
            tried = new HashSet<string>();
            reprobe = false;
            conflicts.Clear();
            NextAt = null;
            if (!enabled) { Phase = "off"; return outs; }
            if (addr == null) { Phase = "no_lan"; return outs; }
            queue = Order(requested, previous);
            NextCandidate(now, true, null);
            outs.AddRange(OnTimer(now));
            return outs;
        }

        public void Fail(string error)
        {
            Phase = "failed";
            Error = error;
            NextAt = null;
        }

        public static List<string> Order(string requested, string previous)
        {
            var fam = LanName.Family(requested);
            var o = new List<string>();
            if (requested == LanName.Default)
            {
                if (previous != null && fam.Contains(previous)) o.Add(previous);
                o.AddRange(fam);
            }
            else
            {
                o.Add(requested);
                if (previous != null && previous != requested)
                {
                    o.Add(previous);
                    if (LanName.InFamily(LanName.Default, previous)) o.AddRange(LanName.Family(LanName.Default));
                }
            }
            var seen = new HashSet<string>();
            var d = new List<string>();
            foreach (string s in o) if (s != null && seen.Add(s)) d.Add(s);
            return d;
        }

        void NextCandidate(double now, bool first, double? delay)
        {
            string nxt = null;
            foreach (string n in queue) if (!tried.Contains(n)) { nxt = n; break; }
            if (nxt == null)
            {
                Phase = "gave_up"; Candidate = null; Claimed = null; NextAt = null;
                return;
            }
            StartProbe(nxt, now, delay.HasValue ? delay.Value : (first ? rng() * ProbeGap : 0));
        }

        void StartProbe(string name, double now, double delay)
        {
            Phase = "probing";
            Candidate = name;
            probes = 0;
            NextAt = now + Math.Max(0, delay);
        }

        // -- time --------------------------------------------------------

        public List<MdnsOut> OnTimer(double now)
        {
            var outs = new List<MdnsOut>();
            for (int step = 0; step < 8; step++)
            {
                if (!NextAt.HasValue || now < NextAt.Value) break;
                if (Phase == "probing")
                {
                    if (probes < ProbeCount)
                    {
                        outs.AddRange(Send(MdnsPacket.Probe(MdnsPacket.NameOf(Candidate), Address), null, now, false));
                        probes++;
                        NextAt = now + ProbeGap;
                        continue;
                    }
                    Claimed = Candidate;
                    reprobe = false;
                    Phase = "announcing";
                    announces = 0;
                    NextAt = now;
                    continue;
                }
                if (Phase == "announcing")
                {
                    var name = MdnsPacket.NameOf(Claimed);
                    outs.AddRange(Send(MdnsPacket.Response(name, Address, MdnsPacket.HostTtl), null, now, false));
                    announcedName = name;
                    announcedAddr = Address;
                    lastMcast[MdnsPacket.TA] = now;
                    announces++;
                    if (announces >= AnnounceCount) { Phase = "announced"; NextAt = null; }
                    else NextAt = now + AnnounceGap;
                    continue;
                }
                if (Phase == "held" || Phase == "announced")
                {
                    StartProbe(Claimed, now, 0);
                    reprobe = true;
                    continue;
                }
                if (Phase == "waiting")
                {
                    renames.Clear();
                    tried = new HashSet<string>();
                    NextCandidate(now, false, null);
                    continue;
                }
                break;
            }
            return outs;
        }

        // -- packets -----------------------------------------------------

        public List<MdnsOut> OnPacket(byte[] data, int length, IPEndPoint src, double now)
        {
            var none = new List<MdnsOut>();
            if (Phase == "off" || Phase == "no_lan" || Phase == "failed" || Phase == "gave_up" || Address == null) return none;
            if (data == null || length < 0 || length > data.Length || length > MdnsPacket.MaxPacket) return none;
            byte[] exact = data;
            if (length != data.Length) { exact = new byte[length]; Buffer.BlockCopy(data, 0, exact, 0, length); }
            if (IsEcho(exact, now)) return none;
            MdnsMessage m;
            try { m = MdnsPacket.Parse(exact); }
            catch (MdnsParseError) { return none; }
            if (m.IsResponse) return OnResponse(m, now);
            return OnQuery(m, src, now);
        }

        bool IsOwn(byte[] a)
        {
            if (SameBytes(a, Address)) return true;
            foreach (byte[] o in own()) if (SameBytes(a, o)) return true;
            return false;
        }

        List<MdnsOut> OnResponse(MdnsMessage m, double now)
        {
            var none = new List<MdnsOut>();
            if (Phase == "probing")
            {
                var name = MdnsPacket.NameOf(Candidate);
                bool any = false, allOwn = true;
                int aCount = 0;
                foreach (var r in m.Records())
                {
                    if (!MdnsPacket.SameName(r.Name, name)) continue;
                    any = true;
                    if (r.Type == MdnsPacket.TA && r.Data.Length == 4)
                    {
                        aCount++;
                        if (!IsOwn(r.Data)) allOwn = false;
                    }
                }
                if (!any) return none;
                if (aCount > 0 && allOwn)
                {
                    Claimed = Candidate;
                    Phase = "held";
                    NextAt = now + HeldRecheck;
                    reprobe = false;
                    return none;
                }
                return Lost(now);
            }
            if ((Phase == "announcing" || Phase == "announced") && Claimed != null)
            {
                var name = MdnsPacket.NameOf(Claimed);
                foreach (var r in m.Records())
                {
                    if (MdnsPacket.SameName(r.Name, name) && r.Type == MdnsPacket.TA && r.Class == MdnsPacket.CIn && r.Ttl > 0
                        && r.Data.Length == 4 && !IsOwn(r.Data))
                        return ConflictAfterAnnounce(now);
                }
            }
            return none;
        }

        List<MdnsOut> ConflictAfterAnnounce(double now)
        {
            if (Phase == "announced" && NextAt.HasValue) return new List<MdnsOut>();
            double when = Math.Max(now, reprobeNotBefore);
            reprobeNotBefore = when + ReprobeGap;
            if (when > now)
            {
                Phase = "announced";
                NextAt = when;
                return new List<MdnsOut>();
            }
            StartProbe(Claimed, now, 0);
            reprobe = true;
            return new List<MdnsOut>();
        }

        List<MdnsOut> Lost(double now)
        {
            string lost = Candidate;
            var outs = new List<MdnsOut>();
            tried.Add(lost);
            if (Taken == null) Taken = lost;
            if (reprobe)
            {
                outs.AddRange(Goodbye(now));
                Claimed = null;
                reprobe = false;
                renames.Enqueue(now);
                while (renames.Count > 0 && now - renames.Peek() > RenameWindow) renames.Dequeue();
                if (renames.Count >= MaxForcedRenames)
                {
                    Phase = "waiting";
                    Candidate = null;
                    Error = "the name kept being taken by another device; trying again within the hour";
                    NextAt = renames.Peek() + RenameWindow;
                    return outs;
                }
            }
            if (lost == Claimed) Claimed = null;
            conflicts.Enqueue(now);
            while (conflicts.Count > 0 && now - conflicts.Peek() > ConflictWindow) conflicts.Dequeue();
            double pause = conflicts.Count >= ConflictBurst ? ConflictPause : 0;
            if (pause > 0) conflicts.Clear();
            NextCandidate(now, false, pause);
            return outs;
        }

        List<MdnsOut> OnQuery(MdnsMessage m, IPEndPoint src, double now)
        {
            var none = new List<MdnsOut>();
            if (Phase == "probing") return Tiebreak(m, now);
            if (Phase != "announced" || Claimed == null) return none;
            var name = MdnsPacket.NameOf(Claimed);
            bool legacy = src == null || src.Port != MdnsPacket.Port;
            bool isProbe = false;
            foreach (var r in m.Authority) if (MdnsPacket.SameName(r.Name, name)) { isProbe = true; break; }
            bool known = false;
            foreach (var r in m.Answers)
                if (MdnsPacket.SameName(r.Name, name) && r.Type == MdnsPacket.TA && SameBytes(r.Data, Address) && r.Ttl >= MdnsPacket.HostTtl / 2)
                { known = true; break; }
            foreach (var q in m.Questions)
            {
                if (!MdnsPacket.SameName(q.Name, name) || (q.Class != MdnsPacket.CIn && q.Class != MdnsPacket.TAny)) continue;
                if (q.Type != MdnsPacket.TA && q.Type != MdnsPacket.TAAAA && q.Type != MdnsPacket.TAny) continue;
                if (legacy)
                {
                    if (src == null || !PrivateSource(src.Address) || src.Port == 0) return none;
                    return Send(MdnsPacket.LegacyResponse(m.Id, name, Address, q.Type), src, now, false);
                }
                if (known && q.Type != MdnsPacket.TAAAA) continue;
                double gap = isProbe ? ProbeGap : 1.0;
                double last;
                if (lastMcast.TryGetValue(q.Type, out last) && now - last < gap) continue;
                lastMcast[q.Type] = now;
                return Send(MdnsPacket.Response(name, Address, MdnsPacket.HostTtl, q.Type), null, now, false);
            }
            return none;
        }

        List<MdnsOut> Tiebreak(MdnsMessage m, double now)
        {
            var none = new List<MdnsOut>();
            var name = MdnsPacket.NameOf(Candidate);
            bool asked = false;
            foreach (var q in m.Questions) if (MdnsPacket.SameName(q.Name, name)) { asked = true; break; }
            if (!asked) return none;
            var theirs = new List<MdnsRecord>();
            foreach (var r in m.Authority) if (MdnsPacket.SameName(r.Name, name)) theirs.Add(r);
            if (theirs.Count == 0) return none;
            theirs.Sort(CompareRecord);
            var ours = new MdnsRecord { Class = MdnsPacket.CIn, Type = MdnsPacket.TA, Data = Address };
            if (theirs.Count == 1 && CompareRecord(theirs[0], ours) == 0) return none;
            int c = CompareRecord(theirs[0], ours);
            bool greater = c > 0 || (c == 0 && theirs.Count > 1);
            if (greater) StartProbe(Candidate, now, 1.0);
            return none;
        }

        static int CompareRecord(MdnsRecord a, MdnsRecord b)
        {
            if (a.Class != b.Class) return a.Class.CompareTo(b.Class);
            if (a.Type != b.Type) return a.Type.CompareTo(b.Type);
            int n = Math.Min(a.Data.Length, b.Data.Length);
            for (int i = 0; i < n; i++) if (a.Data[i] != b.Data[i]) return a.Data[i].CompareTo(b.Data[i]);
            return a.Data.Length.CompareTo(b.Data.Length);
        }

        // -- stopping ----------------------------------------------------

        public List<MdnsOut> Stop(double now)
        {
            var outs = Goodbye(now);
            Phase = "off";
            Requested = null;
            NextAt = null;
            return outs;
        }

        List<MdnsOut> Goodbye(double now)
        {
            if (announcedName == null) return new List<MdnsOut>();
            var name = announcedName;
            var addr = announcedAddr;
            announcedName = null;
            announcedAddr = null;
            return Send(MdnsPacket.Response(name, addr, 0), null, now, true);
        }

        // -- helpers -----------------------------------------------------

        List<MdnsOut> Send(byte[] data, IPEndPoint dest, double now, bool force)
        {
            if (budgetAt.HasValue) budget = Math.Min(MaxSendsPerSec, budget + (now - budgetAt.Value) * MaxSendsPerSec);
            budgetAt = now;
            var outs = new List<MdnsOut>();
            if (budget < 1 && !force) return outs;
            budget = Math.Max(0, budget - 1);
            sent.Add(new KeyValuePair<byte[], double>(data, now));
            while (sent.Count > EchoMax) sent.RemoveAt(0);
            outs.Add(new MdnsOut { Data = data, Dest = dest });
            return outs;
        }

        bool IsEcho(byte[] data, double now)
        {
            sent.RemoveAll(kv => now - kv.Value > EchoWindow);
            foreach (var kv in sent) if (SameBytes(kv.Key, data)) return true;
            return false;
        }

        public string State
        {
            get
            {
                string p = Phase;
                if (p == "off" || p == "no_lan" || p == "failed") return p;
                if (p == "gave_up" || p == "waiting") return "taken";
                if (p == "probing" || (p == "announcing" && announces == 0))
                {
                    if (!(reprobe && Claimed != null)) return "checking";
                }
                if (Requested != null && Claimed != null && !LanName.InFamily(Requested, Claimed)) return "taken";
                return "announced";
            }
        }

        public MdnsSnapshot Snapshot()
        {
            var s = new MdnsSnapshot();
            s.Requested = Requested;
            string p = Phase;
            s.Claimed = (p == "announcing" || p == "announced" || p == "held" || p == "probing") ? Claimed : null;
            s.State = State;
            s.Taken = Taken;
            s.Error = Error;
            return s;
        }
    }

    // -- the request and the report (domovoi/lan_name/service.py) ---------

    public sealed class LanNameRequest
    {
        // none (no file), ok, invalid (Problem says why).
        public string State = "none";
        public string Problem;
        public string Id;
        public string Name;
        public bool Enabled = true;
    }

    public static class LanNameFiles
    {
        public const string ServiceUser = "domovoi";
        public const string RequestFile = "/home/" + ServiceUser + "/.domovoi/lan_name/request.json";
        public const int MaxRequestBytes = 1024;
        public const string ReportDir = WindowsSideReport.Dir;
        public const string ReportFile = ReportDir + "/lan_name.json";
        // How often the request is read while Domovoi runs: often enough for
        // Settings' "checking...", rare enough for one wsl.exe each time.
        public static readonly TimeSpan Every = TimeSpan.FromSeconds(5);
        // The tray's copy, readable by the household's accounts.
        public const string TrayName = "lan-name.json";

        static readonly Regex HexId = new Regex("^[0-9a-f]{32}\\z");
        static readonly HashSet<string> Keys = new HashSet<string>(StringComparer.Ordinal) { "v", "id", "name", "enabled", "at" };

        public static LanNameRequest Parse(string text)
        {
            var r = new LanNameRequest();
            if (text == null) return r;
            r.State = "invalid";
            if (text.Length > MaxRequestBytes) { r.Problem = "the request is too large"; return r; }
            Dictionary<string, object> d;
            try { d = Json.ParseObject(text); }
            catch (FormatException) { r.Problem = "the request is not a JSON object"; return r; }
            foreach (string k in d.Keys) if (!Keys.Contains(k)) { r.Problem = "the request has a key it may not have"; return r; }
            if (Json.Int(d, "v") != 1) { r.Problem = "the request's version is not 1"; return r; }
            string id = Json.Str(d, "id");
            if (id == null || !HexId.IsMatch(id)) { r.Problem = "id is not 32 hex digits"; return r; }
            string name = LanName.Exact(Json.Str(d, "name"));
            if (name == null) { r.Problem = "name is not a network name"; return r; }
            bool? en = Json.Bool(d, "enabled");
            if (en == null) { r.Problem = "enabled is not true or false"; return r; }
            r.Id = id;
            r.Name = name;
            r.Enabled = en.Value;
            r.State = "ok";
            return r;
        }

        public static string[] ReadArgs(string distro)
        {
            return new[] { "-d", distro, "-u", ServiceUser, "--cd", "/", "--exec", "/usr/bin/timeout", "-s", "KILL", "5",
                           "/usr/bin/head", "-c", Inv.S(MaxRequestBytes + 1), RequestFile };
        }

        // The report's text; everything in it is a checked label, one of the
        // state words, an RFC 1918 address, null, or the error (ours).
        public static string Report(MdnsSnapshot s, string requestId, string address, DateTime utc)
        {
            var d = new Dictionary<string, object>();
            d["schema"] = 1;
            d["request_id"] = requestId != null && HexId.IsMatch(requestId) ? requestId : null;
            d["requested"] = LanName.Exact(s.Requested) ?? LanName.Default;
            d["claimed"] = LanName.Exact(s.Claimed);
            d["state"] = Array.IndexOf(MdnsResponder.States, s.State) >= 0 ? s.State : "failed";
            d["taken"] = LanName.Exact(s.Taken);
            d["addresses"] = s.Claimed != null && address != null && Lan.IsPrivateIPv4(address) ? new object[] { address } : new object[0];
            d["error"] = s.Error != null && s.Error.Length <= 200 ? s.Error : null;
            d["written_at"] = utc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            return Json.Serialize(d) + "\n";
        }

        public static string ReportScript()
        {
            return "set -e; umask 022; mkdir -p " + ReportDir + "; chown root:root " + ReportDir + "; chmod 0755 " + ReportDir + "; "
                + "cat > " + ReportDir + "/.lan_name.json.new; chmod 0644 " + ReportDir + "/.lan_name.json.new; "
                + "mv -f " + ReportDir + "/.lan_name.json.new " + ReportFile + "; sha256sum " + ReportFile;
        }

        // For the tray and Setup: the name held, or null.
        public static string TrayText(MdnsSnapshot s)
        {
            var d = new Dictionary<string, object>();
            d["v"] = 1;
            d["fqdn"] = (s.State == "announced" || s.State == "taken") ? LanName.Fqdn(LanName.Exact(s.Claimed)) : null;
            d["state"] = s.State;
            return Json.Serialize(d) + "\n";
        }

        // The tray's read of it: the fqdn when it is a network name, else null.
        public static string TrayFqdn(string text)
        {
            if (text == null || text.Length > 1024) return null;
            try
            {
                string f = Json.Str(Json.ParseObject(text), "fqdn");
                if (f == null || !f.EndsWith(".local", StringComparison.Ordinal)) return null;
                return LanName.Exact(f.Substring(0, f.Length - 6)) != null ? f : null;
            }
            catch (FormatException) { return null; }
        }
    }

    // -- which adapter (the plan's rule, as Lan.cs and lan_address.py) -----

    public sealed class NicFacts
    {
        public string Name;
        public string Description;
        public bool Up;
        public bool LoopbackOrTunnel;
        public bool HasGateway;
        public List<string> Addresses = new List<string>();
    }

    public static class MdnsAdapter
    {
        static readonly string[] Virtual = { "vethernet", "hyper-v", "wsl", "vpn", "virtual", "tap-", "wireguard", "tailscale",
                                             "zerotier", "docker", "vmware", "virtualbox", "loopback", "bluetooth" };

        public static bool LooksVirtual(NicFacts n)
        {
            string s = ((n.Name ?? "") + " " + (n.Description ?? "")).ToLowerInvariant();
            foreach (string v in Virtual) if (s.Contains(v)) return true;
            return false;
        }

        // The address to announce: routeAddress (the one Windows picks for a
        // route out, the UDP-connect trick) when a real adapter that is up,
        // has a gateway and isn't virtual holds it and it is RFC 1918; null
        // otherwise. v1 answers on that one adapter only.
        public static string Pick(IEnumerable<NicFacts> nics, string routeAddress)
        {
            if (routeAddress == null || !Lan.IsPrivateIPv4(routeAddress)) return null;
            foreach (NicFacts n in nics)
            {
                if (!n.Up || n.LoopbackOrTunnel || !n.HasGateway || LooksVirtual(n)) continue;
                if (n.Addresses.Contains(routeAddress)) return routeAddress;
            }
            return null;
        }
    }
}
