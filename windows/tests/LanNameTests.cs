// The network name (common\MdnsResponder.cs): the same golden vectors as the
// Python copy (domovoi/lan_name/vectors.json), the parser against hostile
// packets, the state machine with a fake clock, the request/report files,
// the adapter choice and the firewall rule. Nothing here opens a socket or
// touches the firewall.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using Domovoi.Windows;

static partial class Tests
{
    static Dictionary<string, object> lanVectors;

    static Dictionary<string, object> LanVectors()
    {
        if (lanVectors != null) return lanVectors;
        string env = Environment.GetEnvironmentVariable("DOMOVOI_LAN_NAME_VECTORS");
        string path = !string.IsNullOrEmpty(env) ? env : Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(typeof(Tests).Assembly.Location), @"..\..\..\domovoi\lan_name\vectors.json"));
        lanVectors = Json.ParseObject(File.ReadAllText(path));
        return lanVectors;
    }

    static byte[] Unhex(string h)
    {
        var b = new byte[h.Length / 2];
        for (int i = 0; i < b.Length; i++) b[i] = Convert.ToByte(h.Substring(i * 2, 2), 16);
        return b;
    }

    static string Hex(byte[] b) { return string.Concat(b.Select(x => x.ToString("x2")).ToArray()); }

    static readonly byte[] LanIp = { 192, 168, 1, 20 };
    static readonly byte[] OtherIp = { 192, 168, 1, 30 };
    static readonly IPEndPoint Peer = new IPEndPoint(IPAddress.Parse("192.168.1.30"), 5353);

    sealed class FakeNet
    {
        public double T = 100;
        public MdnsResponder R;
        public List<MdnsOut> Sent = new List<MdnsOut>();

        public FakeNet(string name, string sticky, ICollection<byte[]> own, bool on, string ip)
        {
            R = new MdnsResponder(() => own ?? new List<byte[]>(), () => 0.0);
            Sent.AddRange(R.Configure(name, on, ip, T, sticky));
        }

        public FakeNet(string name) : this(name, null, null, true, "192.168.1.20") { }

        public void Run(double seconds, double step)
        {
            double end = T + seconds;
            while (T < end) { T = Math.Round(T + step, 4); Sent.AddRange(R.OnTimer(T)); }
        }

        public void Run(double seconds) { Run(seconds, 0.05); }

        public List<MdnsOut> Feed(byte[] data, IPEndPoint src)
        {
            var o = R.OnPacket(data, data.Length, src, T);
            Sent.AddRange(o);
            return o;
        }

        public List<MdnsOut> Feed(byte[] data) { return Feed(data, Peer); }

        public List<string> Kinds()
        {
            var l = new List<string>();
            foreach (var o in Sent)
            {
                var m = MdnsPacket.Parse(o.Data);
                if (!m.IsResponse) l.Add("probe");
                else if (m.Answers.Count > 0 && m.Answers[0].Ttl == 0) l.Add("goodbye");
                else l.Add("answer");
            }
            return l;
        }
    }

    static byte[] TakenBy(string name) { return MdnsPacket.Response(MdnsPacket.NameOf(name), OtherIp, 120); }

    static byte[] Question(string name, int qtype, int id)
    {
        var l = new List<byte> { (byte)(id >> 8), (byte)id, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0 };
        foreach (byte[] lab in MdnsPacket.NameOf(name)) { l.Add((byte)lab.Length); l.AddRange(lab); }
        l.Add(0);
        l.AddRange(new byte[] { (byte)(qtype >> 8), (byte)qtype, 0, 1 });
        return l.ToArray();
    }

    static void LanNameTests()
    {
        Run("lan name: the shared golden list", delegate
        {
            var v = LanVectors();
            foreach (object o in (object[])v["names_ok"])
            {
                var pair = (object[])o;
                Eq((string)pair[1], LanName.Normalize((string)pair[0]), "normalize " + pair[0]);
            }
            foreach (object o in (object[])v["names_bad"])
                Throws<BadLanName>(delegate { LanName.Normalize((string)o); }, "refuses " + o);
            Throws<BadLanName>(delegate { LanName.Normalize(null); }, "null");
            var fam = LanName.Family("domovoi");
            Eq(99, fam.Count, "99 names");
            Eq("domovoi2", fam[1], "no dash"); Eq((string)v["family_last"], fam[98], "last");
            Eq(1, LanName.Family("kitchen").Count, "a typed name has no family");
            Eq(null, LanName.Exact("Kitchen"), "exact means already normalized");
        });
        Run("lan name: packets match the Python copy byte for byte", delegate
        {
            var v = LanVectors();
            var n = MdnsPacket.NameOf("domovoi");
            Eq((string)v["probe_domovoi"], Hex(MdnsPacket.Probe(n, LanIp)), "probe");
            Eq((string)v["announce_domovoi"], Hex(MdnsPacket.Response(n, LanIp, 120)), "announce");
            Eq((string)v["goodbye_domovoi"], Hex(MdnsPacket.Response(n, LanIp, 0)), "goodbye");
            Eq((string)v["answer_aaaa_domovoi"], Hex(MdnsPacket.Response(n, LanIp, 120, MdnsPacket.TAAAA)), "aaaa");
            Eq((string)v["answer_any_domovoi"], Hex(MdnsPacket.Response(n, LanIp, 120, MdnsPacket.TAny)), "any");
            Eq((string)v["legacy_a_domovoi_id_4660"], Hex(MdnsPacket.LegacyResponse(0x1234, n, LanIp, MdnsPacket.TA)), "legacy a");
            Eq((string)v["legacy_aaaa_domovoi_id_4660"], Hex(MdnsPacket.LegacyResponse(0x1234, n, LanIp, MdnsPacket.TAAAA)), "legacy aaaa");
        });
        Run("lan name: hostile packets are dropped whole", delegate
        {
            var bad = (Dictionary<string, object>)LanVectors()["bad_packets"];
            foreach (var kv in bad)
            {
                string key = kv.Key;
                Throws<MdnsParseError>(delegate { MdnsPacket.Parse(Unhex((string)kv.Value)); }, key);
            }
            Throws<MdnsParseError>(delegate { MdnsPacket.Parse(new byte[MdnsPacket.MaxPacket + 1]); }, "oversize");
        });
        Run("lan name: a compressed answer is read, lowercased", delegate
        {
            var q = Question("domovoi", 1, 0).ToList();
            q[2] = 0x84; q[7] = 1;   // a response with one answer
            q.AddRange(new byte[] { 0xC0, 0x0C, 0, 1, 0x80, 1, 0, 0, 0, 120, 0, 4, 192, 168, 1, 30 });
            var m = MdnsPacket.Parse(q.ToArray());
            True(MdnsPacket.SameName(m.Answers[0].Name, MdnsPacket.NameOf("domovoi")), "pointer followed");
            True(m.Answers[0].Flush && m.Answers[0].Ttl == 120, "flush, ttl");
        });
        Run("lan name: random and mutated packets never throw anything else", delegate
        {
            var rng = new Random(6762);
            var n = MdnsPacket.NameOf("domovoi");
            byte[][] seeds = { MdnsPacket.Probe(n, LanIp), MdnsPacket.Response(n, LanIp, 120, MdnsPacket.TAny), MdnsPacket.LegacyResponse(7, n, LanIp, MdnsPacket.TAAAA) };
            var net = new FakeNet("domovoi");
            net.Run(3);
            for (int i = 0; i < 6000; i++)
            {
                byte[] d;
                if (i % 3 == 0) { d = new byte[rng.Next(0, 80)]; rng.NextBytes(d); }
                else
                {
                    d = (byte[])seeds[rng.Next(seeds.Length)].Clone();
                    for (int k = rng.Next(1, 7); k > 0; k--) d[rng.Next(d.Length)] = (byte)rng.Next(256);
                }
                try { MdnsPacket.Parse(d); } catch (MdnsParseError) { }
                net.T += 0.001;
                net.Feed(d);
            }
        });
        Run("lan name: a free name is probed three times, announced twice", delegate
        {
            var n = new FakeNet("domovoi");
            Eq("checking", n.R.State, "checking first");
            n.Run(3);
            SeqEq(new[] { "probe", "probe", "probe", "answer", "answer" }, n.Kinds(), "packets");
            Eq("announced", n.R.State, "state"); Eq("domovoi", n.R.Claimed, "claimed");
            True(n.Sent.All(o => o.Dest == null), "all to the group");
        });
        Run("lan name: the default moves to domovoi2 when taken", delegate
        {
            var n = new FakeNet("domovoi");
            n.Run(0.1);
            n.Feed(TakenBy("domovoi"));
            n.Run(3);
            Eq("domovoi2", n.R.Claimed, "claimed"); Eq("announced", n.R.State, "state"); Eq("domovoi", n.R.Taken, "taken");
        });
        Run("lan name: the sticky name first; a typed name taken keeps the old one", delegate
        {
            var n = new FakeNet("domovoi", "domovoi3", null, true, "192.168.1.20");
            n.Run(3);
            Eq("domovoi3", n.R.Claimed, "sticky");
            n.Sent.Clear();
            n.Sent.AddRange(n.R.Configure("kitchen", true, "192.168.1.20", n.T, null));
            Eq("goodbye", n.Kinds()[0], "the old name's goodbye");
            n.Run(0.1);
            n.Feed(TakenBy("kitchen"));
            n.Run(3);
            Eq("domovoi3", n.R.Claimed, "kept"); Eq("taken", n.R.State, "says taken"); Eq("kitchen", n.R.Taken, "what was taken");
        });
        Run("lan name: already ours is held without a second copy", delegate
        {
            var own = new List<byte[]> { new byte[] { 10, 0, 0, 5 } };
            var n = new FakeNet("domovoi", null, own, true, "192.168.1.20");
            n.Run(0.1);
            n.Feed(MdnsPacket.Response(MdnsPacket.NameOf("domovoi"), new byte[] { 10, 0, 0, 5 }, 120), new IPEndPoint(IPAddress.Parse("192.168.1.20"), 5353));
            n.Run(3);
            Eq("held", n.R.Phase, "held"); Eq("announced", n.R.State, "state");
            Eq(0, n.Kinds().Count(k => k == "answer"), "no copy of ours");
        });
        Run("lan name: our own echo is not a conflict", delegate
        {
            var n = new FakeNet("domovoi");
            n.Run(0.3);
            foreach (var o in n.Sent.ToList()) n.Feed(o.Data, new IPEndPoint(IPAddress.Parse("192.168.1.20"), 5353));
            n.Run(3);
            Eq("domovoi", n.R.Claimed, "kept");
        });
        Run("lan name: answers, their limits, legacy unicast to private only", delegate
        {
            var n = new FakeNet("domovoi");
            n.Run(3);
            n.T += 2;
            var a = n.Feed(Question("domovoi", 1, 0));
            Eq(1, a.Count, "answered"); True(a[0].Dest == null, "by multicast");
            Eq(0, n.Feed(Question("domovoi", 1, 0)).Count, "not twice a second");
            Eq(0, n.Feed(Question("printer", 1, 0)).Count, "never another name");
            Eq(0, n.Feed(Question("domovoi", 16, 0)).Count, "never TXT");
            var l = n.Feed(Question("domovoi", 1, 0x1234), new IPEndPoint(IPAddress.Parse("192.168.1.41"), 50000));
            Eq((string)LanVectors()["legacy_a_domovoi_id_4660"], Hex(l[0].Data), "legacy answer");
            Eq("192.168.1.41:50000", l[0].Dest.ToString(), "by unicast");
            Eq(0, n.Feed(Question("domovoi", 1, 1), new IPEndPoint(IPAddress.Parse("8.8.8.8"), 50000)).Count, "never to a public address");
            n.Sent.Clear();
            for (int i = 0; i < 500; i++) n.Feed(Question("domovoi", 1, i), new IPEndPoint(IPAddress.Parse("192.168.1.41"), 40000 + i));
            True(n.Sent.Count <= MdnsResponder.MaxSendsPerSec, "capped: " + n.Sent.Count);
        });
        Run("lan name: forced renames are capped, with goodbyes", delegate
        {
            var n = new FakeNet("domovoi");
            n.Run(3);
            var held = new List<string>();
            for (int i = 0; i < MdnsResponder.MaxForcedRenames; i++)
            {
                n.T += MdnsResponder.ReprobeGap + 1;
                n.Feed(TakenBy(n.R.Claimed));
                n.Run(0.1);
                n.Sent.Clear();
                n.Feed(TakenBy(n.R.Candidate));
                True(n.Kinds().Contains("goodbye"), "goodbye for the lost name");
                n.Run(3);
                held.Add(n.R.Claimed);
            }
            Eq("domovoi2", held[0], "first rename"); Eq("domovoi3", held[1], "second");
            Eq("taken", n.R.State, "stops"); Eq(null, n.R.Claimed, "nothing held"); True(n.R.Error != null, "says why");
            n.Run(MdnsResponder.RenameWindow, 5);
            n.Run(3);
            Eq("announced", n.R.State, "tries again after the hour");
        });
        Run("lan name: goodbye on off, a new address and stop; no LAN, no name", delegate
        {
            var n = new FakeNet("domovoi");
            n.Run(3);
            n.Sent.Clear();
            n.Sent.AddRange(n.R.Configure("domovoi", false, "192.168.1.20", n.T, null));
            SeqEq(new[] { "goodbye" }, n.Kinds(), "off"); Eq("off", n.R.State, "off");
            n.Sent.AddRange(n.R.Configure("domovoi", true, "192.168.1.21", n.T, null));
            n.Run(3);
            n.Sent.Clear();
            n.Sent.AddRange(n.R.Stop(n.T));
            SeqEq(new[] { "goodbye" }, n.Kinds(), "stop");
            Eq(0, n.R.Stop(n.T).Count, "once");
            foreach (string ip in new[] { null, "127.0.0.1", "169.254.3.4", "8.8.8.8", "100.64.0.1" })
            {
                var x = new FakeNet("domovoi", null, null, true, ip);
                x.Run(3);
                Eq("no_lan", x.R.State, "no lan for " + ip); Eq(0, x.Sent.Count, "silent for " + ip);
            }
        });
        Run("lan name: the request is data, checked again", delegate
        {
            string id = new string('a', 32);
            var ok = LanNameFiles.Parse("{\"v\": 1, \"id\": \"" + id + "\", \"name\": \"kitchen\", \"enabled\": true, \"at\": \"2026-11-12T05:00:00Z\"}");
            Eq("ok", ok.State, "ok"); Eq("kitchen", ok.Name, "name"); True(ok.Enabled, "on");
            Eq("none", LanNameFiles.Parse(null).State, "no file");
            foreach (string bad in new[] {
                "{\"v\": 1, \"id\": \"" + id + "\", \"name\": \"Kitchen\", \"enabled\": true}",
                "{\"v\": 1, \"id\": \"" + id + "\", \"name\": \"-x\", \"enabled\": true}",
                "{\"v\": 1, \"id\": \"" + id + "\", \"name\": \"kitchen\"}",
                "{\"v\": 1, \"id\": \"xyz\", \"name\": \"kitchen\", \"enabled\": true}",
                "{\"v\": 2, \"id\": \"" + id + "\", \"name\": \"kitchen\", \"enabled\": true}",
                "{\"v\": 1, \"id\": \"" + id + "\", \"name\": \"kitchen\", \"enabled\": true, \"cmd\": \"x\"}",
                "[1]", "not json", "{\"v\": 1, \"name\": \"" + new string('a', 2000) + "\"}" })
                Eq("invalid", LanNameFiles.Parse(bad).State, "refused: " + (bad.Length > 60 ? bad.Substring(0, 60) : bad));
            string args = string.Join(" ", LanNameFiles.ReadArgs("domovoi"));
            True(args.Contains("-u domovoi") && args.Contains("/home/domovoi/.domovoi/lan_name/request.json") && args.Contains("head -c 1025"), args);
        });
        Run("lan name: the report says only checked things", delegate
        {
            var s = new MdnsSnapshot { Requested = "domovoi", Claimed = "domovoi2", State = "announced", Taken = "domovoi" };
            var d = Json.ParseObject(LanNameFiles.Report(s, new string('b', 32), "192.168.1.30", new DateTime(2026, 11, 12, 5, 0, 0, DateTimeKind.Utc)));
            Eq(1, Json.Int(d, "schema").Value, "schema"); Eq("domovoi2", Json.Str(d, "claimed"), "claimed");
            Eq("192.168.1.30", Json.StrList(d, "addresses")[0], "address"); Eq("2026-11-12T05:00:00Z", Json.Str(d, "written_at"), "at");
            var odd = new MdnsSnapshot { Requested = "Bad Name", Claimed = "x.y", State = "pwned", Taken = null };
            var e = Json.ParseObject(LanNameFiles.Report(odd, "nothex", "8.8.8.8", DateTime.UtcNow));
            Eq("domovoi", Json.Str(e, "requested"), "requested falls back"); Eq(null, Json.Str(e, "claimed"), "claimed dropped");
            Eq("failed", Json.Str(e, "state"), "unknown state"); Eq(null, Json.Str(e, "request_id"), "id dropped");
            Eq(0, Json.StrList(e, "addresses").Count, "no public address");
            True(LanNameFiles.ReportScript().Contains("/var/lib/domovoi-windows/lan_name.json"), "the path the core reads");
            Eq("domovoi2.local", LanNameFiles.TrayFqdn(LanNameFiles.TrayText(s)), "tray read");
            Eq(null, LanNameFiles.TrayFqdn("{\"fqdn\": \"evil.example.com\"}"), "tray refuses another name");
            foreach (string st in MdnsResponder.States) True(LanNameFiles.Report(new MdnsSnapshot { Requested = "domovoi", State = st }, null, null, DateTime.UtcNow).Contains("\"" + st + "\""), st);
        });
        Run("lan name: the LAN adapter only, never a virtual one", delegate
        {
            var wifi = new NicFacts { Name = "Wi-Fi", Description = "Intel Wi-Fi 6E", Up = true, HasGateway = true, Addresses = { "192.168.1.30" } };
            var wsl = new NicFacts { Name = "vEthernet (WSL (Hyper-V firewall))", Description = "Hyper-V Virtual Ethernet Adapter", Up = true, HasGateway = true, Addresses = { "172.20.0.1" } };
            var vpn = new NicFacts { Name = "Office", Description = "WireGuard Tunnel", Up = true, HasGateway = true, Addresses = { "10.8.0.2" } };
            var nics = new List<NicFacts> { wsl, vpn, wifi };
            Eq("192.168.1.30", MdnsAdapter.Pick(nics, "192.168.1.30"), "the route's adapter");
            Eq(null, MdnsAdapter.Pick(nics, "172.20.0.1"), "never vEthernet");
            Eq(null, MdnsAdapter.Pick(nics, "10.8.0.2"), "never a VPN");
            Eq(null, MdnsAdapter.Pick(nics, "100.64.1.1"), "never CGNAT");
            Eq(null, MdnsAdapter.Pick(nics, null), "no route");
            wifi.HasGateway = false;
            Eq(null, MdnsAdapter.Pick(nics, "192.168.1.30"), "needs a gateway");
        });
        Run("lan name: the firewall rule is Private and Domain only, for DomovoiHost.exe", delegate
        {
            string add = Firewall.MdnsAddScript(@"C:\Program Files\Domovoi\DomovoiHost.exe");
            True(add.Contains("-Name 'Domovoi-mDNS-5353'"), "name");
            True(add.Contains("-Direction Inbound -Protocol UDP -LocalPort 5353"), "inbound UDP 5353");
            True(add.Contains("-Program 'C:\\Program Files\\Domovoi\\DomovoiHost.exe'"), "that program only");
            True(add.Contains("-RemoteAddress LocalSubnet") && add.Contains("-Profile Private,Domain") && !add.Contains("Public"), "LAN, never Public");
            True(add.IndexOf("Remove-NetFirewallRule", StringComparison.Ordinal) < add.IndexOf("New-NetFirewallRule", StringComparison.Ordinal), "replaces, never doubles");
            Eq(0, PsErrors(add).Length, "parses");
            Eq(0, PsErrors(Firewall.ListScript()).Length, "list parses");
            Throws<ArgumentException>(delegate { Firewall.MdnsAddScript(@"C:\x\evil.exe"); }, "another program");
            Throws<ArgumentException>(delegate { Firewall.MdnsAddScript(@"C:\x'; Remove-Item C:\ ;'\DomovoiHost.exe"); }, "a quote");
            Throws<ArgumentException>(delegate { Firewall.MdnsAddScript(@"DomovoiHost.exe"); }, "relative");
            True(Firewall.ListScript().Contains("Get-NetFirewallRule -Name 'Domovoi-mDNS-5353'"), "listed for the uninstaller");
            var l = FullLedger();
            l.FirewallRules.Add(Firewall.MdnsRuleName);
            var plan = UninstallPlan.Make(l, FullFacts(l));
            var step = plan.Single(s => s.Kind == UninstallKind.RemoveFirewallRules);
            True(step.Target.Contains("Domovoi-mDNS-5353"), "the uninstall removes it: " + step.Target);
            True(new UninstallLeftover(step, "x").HowToRemove().Contains("Remove-NetFirewallRule -Name 'Domovoi-mDNS-5353'"), "and says how by hand");
        });
    }
}
