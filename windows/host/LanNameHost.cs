// The network name's socket and thread inside DomovoiHost (the state machine
// and the rules are common\MdnsResponder.cs; docs/WINDOWS_HOST.md, "The
// network name").
//
// One UDP socket on 5353, shared with Windows' own responder (dnscache):
// ExclusiveAddressUse off and ReuseAddress on, the spike's finding 3. It
// joins 224.0.0.251 and sends on the LAN adapter only (finding 5): the
// address Windows picks for a route out, when an Ethernet or Wi-Fi adapter
// (by type) that is up and has a gateway holds it (MdnsAdapter.PickNic),
// never a vEthernet, Hyper-V, WSL, VPN or Docker one. A packet that arrived
// on another interface (IP_PKTINFO) or from outside that adapter's subnet is
// dropped. Nothing is announced while Windows marks that network Public
// (state "public"). Probes and QU questions are answered by multicast
// (finding 4). Inbound UDP 5353 for DomovoiHost.exe is allowed by Setup's
// firewall rule Domovoi-mDNS-5353 (Private and Domain networks only).
//
// Everything the responder holds is touched on this thread only; the host's
// loop hands it the household's choice (Desire) and reads a copy of its state
// (Snapshot), both under a lock. Nothing read from the network is logged.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Management;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Threading;

namespace Domovoi.Windows.Host
{
    sealed class LanNameHost
    {
        static readonly TimeSpan Recheck = TimeSpan.FromSeconds(5);
        static readonly TimeSpan Rebind = TimeSpan.FromSeconds(60);
        static readonly TimeSpan CategoryEvery = TimeSpan.FromSeconds(30);
        const int SioUdpConnReset = -1744830452;
        public const string PublicWhy = "not announced: this network is marked Public in Windows";

        readonly Action<string> log;
        readonly object gate = new object();
        readonly ManualResetEvent stopping = new ManualResetEvent(false);
        readonly Stopwatch clock = Stopwatch.StartNew();
        readonly MdnsResponder responder;
        Thread thread;
        string wantName;
        bool wantEnabled;
        string sticky;
        bool changed;
        MdnsSnapshot shot = new MdnsSnapshot { State = "off" };
        string shotAddress;
        Socket sock;
        string sockAddress;
        int sockIndex = -1;
        DateTime categoryAt = DateTime.MinValue;
        int categoryIndex = -1;
        bool categoryPublic;

        public LanNameHost(Action<string> log)
        {
            this.log = log;
            responder = new MdnsResponder(null);
        }

        double Now { get { return clock.Elapsed.TotalSeconds; } }

        public void Start(string name, bool enabled, string stickyName)
        {
            lock (gate) { wantName = name; wantEnabled = enabled; sticky = stickyName; changed = true; }
            thread = new Thread(Loop);
            thread.IsBackground = true;
            thread.Name = "DomovoiHost network name";
            thread.Start();
        }

        public void Desire(string name, bool enabled)
        {
            lock (gate)
            {
                if (name == wantName && enabled == wantEnabled) return;
                wantName = name; wantEnabled = enabled; changed = true;
            }
        }

        public MdnsSnapshot Snapshot(out string address)
        {
            lock (gate)
            {
                address = shotAddress;
                return new MdnsSnapshot { Requested = shot.Requested, Claimed = shot.Claimed, State = shot.State, Taken = shot.Taken, Error = shot.Error };
            }
        }

        // The goodbye goes out, the socket closes; at most a few seconds.
        public void Stop()
        {
            stopping.Set();
            Thread t = thread;
            if (t != null && t != Thread.CurrentThread) t.Join(3000);
        }

        void Loop()
        {
            DateTime nextCheck = DateTime.MinValue;
            DateTime failedAt = DateTime.MinValue;
            var buf = new byte[MdnsPacket.MaxPacket + 1];
            try
            {
                while (!stopping.WaitOne(0))
                {
                    bool kick;
                    lock (gate) { kick = changed; changed = false; }
                    if (kick || DateTime.UtcNow >= nextCheck)
                    {
                        nextCheck = DateTime.UtcNow + Recheck;
                        string name, stickyName;
                        bool enabled;
                        lock (gate) { name = wantName; enabled = wantEnabled; stickyName = sticky; }
                        NicPick nic = enabled ? LanNic() : null;
                        if (nic != null && IsPublic(nic.Index))
                        {
                            // Nothing on a network Windows calls Public.
                            if (sock != null) Send(responder.Stop(Now));
                            CloseSocket();
                            if (responder.Phase != "public") log("network name: " + PublicWhy);
                            responder.Fail(PublicWhy, "public");
                            Publish(null);
                            continue;
                        }
                        string address = nic != null ? nic.Address : null;
                        if (enabled && address != null && (sock == null || sockAddress != address))
                        {
                            if (DateTime.UtcNow - failedAt >= Rebind || kick)
                            {
                                // A new address: the goodbye for the old one
                                // leaves by the old socket first.
                                if (sock != null) Send(responder.Stop(Now));
                                string why = OpenSocket(nic);
                                if (why != null)
                                {
                                    failedAt = DateTime.UtcNow;
                                    responder.Fail("can't listen on UDP 5353: " + why);
                                    log("network name: can't listen on UDP 5353: " + why);
                                    Publish(null);
                                    continue;
                                }
                            }
                            else { Publish(null); Pause(250); continue; }
                        }
                        List<MdnsOut> outs = responder.Configure(name ?? LanName.Default, enabled, address, Now, stickyName,
                                                                 nic != null ? nic.Prefix : 0);
                        Send(outs);
                        if (!enabled || address == null) CloseSocket();
                        if (responder.Claimed != null) lock (gate) sticky = null;
                    }
                    Send(responder.OnTimer(Now));
                    Publish(sockAddress);
                    Receive(buf);
                }
            }
            catch (Exception e)
            {
                log("network name: stopped by an error: " + e.GetType().Name + ": " + e.Message);
            }
            finally
            {
                try { Send(responder.Stop(Now)); } catch (Exception) { }
                CloseSocket();
                lock (gate) { shot = new MdnsSnapshot { State = "off" }; shotAddress = null; }
            }
        }

        void Pause(int ms) { stopping.WaitOne(ms); }

        // Windows' network category for the adapter, asked at most every
        // CategoryEvery (a WMI query); unknown counts as not Public.
        bool IsPublic(int ifIndex)
        {
            if (ifIndex == categoryIndex && DateTime.UtcNow - categoryAt < CategoryEvery) return categoryPublic;
            categoryIndex = ifIndex;
            categoryAt = DateTime.UtcNow;
            categoryPublic = Category(ifIndex) == "Public";
            return categoryPublic;
        }

        // The query Setup's preflight uses (helper\Preflight.cs Category).
        public static string Category(int ifIndex)
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
            catch (System.Runtime.InteropServices.COMException) { }
            catch (UnauthorizedAccessException) { }
            return "unknown";
        }

        // Wait for a packet until the responder's next deadline (at most
        // 250 ms, so a Stop or a Desire is seen quickly).
        void Receive(byte[] buf)
        {
            double wait = 0.25;
            if (responder.NextAt.HasValue) wait = Math.Max(0, Math.Min(wait, responder.NextAt.Value - Now));
            if (sock == null) { Pause((int)(wait * 1000) + 1); return; }
            try
            {
                if (!sock.Poll((int)(wait * 1000000), SelectMode.SelectRead)) return;
                EndPoint from = new IPEndPoint(IPAddress.Any, 0);
                SocketFlags flags = SocketFlags.None;
                IPPacketInformation info;
                int n = sock.ReceiveMessageFrom(buf, 0, buf.Length, ref flags, ref from, out info);
                if (n > MdnsPacket.MaxPacket || (flags & SocketFlags.Truncated) != 0) return;
                // Only what arrived on the LAN adapter (L3).
                if (sockIndex >= 0 && info.Interface != sockIndex) return;
                Send(responder.OnPacket(buf, n, from as IPEndPoint, Now));
            }
            catch (SocketException) { }
            catch (ObjectDisposedException) { }
        }

        void Send(List<MdnsOut> outs)
        {
            if (sock == null || outs == null) return;
            foreach (MdnsOut o in outs)
            {
                try { sock.SendTo(o.Data, o.Dest ?? new IPEndPoint(IPAddress.Parse(MdnsPacket.Group), MdnsPacket.Port)); }
                catch (SocketException) { }
                catch (ObjectDisposedException) { }
            }
        }

        void Publish(string address)
        {
            MdnsSnapshot s = responder.Snapshot();
            lock (gate) { shot = s; shotAddress = s.Claimed != null ? address : null; }
        }

        string OpenSocket(NicPick nic)
        {
            CloseSocket();
            var s = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            try
            {
                s.ExclusiveAddressUse = false;
                s.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
                // An ICMP "port unreachable" for a legacy-unicast answer would
                // otherwise fail the next receive.
                try { s.IOControl(SioUdpConnReset, new byte[4], null); } catch (SocketException) { }
                s.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.PacketInformation, true);
                s.Bind(new IPEndPoint(IPAddress.Any, MdnsPacket.Port));
                IPAddress local = IPAddress.Parse(nic.Address);
                s.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.AddMembership, new MulticastOption(IPAddress.Parse(MdnsPacket.Group), local));
                s.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastInterface, local.GetAddressBytes());
                s.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastTimeToLive, 255);
                s.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastLoopback, true);
            }
            catch (SocketException e)
            {
                s.Close();
                return e.Message;
            }
            sock = s;
            sockAddress = nic.Address;
            sockIndex = nic.Index > 0 ? nic.Index : -1;
            log("network name: listening on UDP 5353 on " + nic.Address + " (" + nic.Name + ")");
            return null;
        }

        void CloseSocket()
        {
            if (sock != null)
            {
                try { sock.Close(); } catch (SocketException) { }
                sock = null;
            }
            sockAddress = null;
            sockIndex = -1;
        }

        // The address Windows picks for a route out (nothing is sent),
        // announced only on an Ethernet or Wi-Fi adapter that holds it.
        public static NicPick LanNic()
        {
            string route = null;
            using (var s = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp))
            {
                try
                {
                    s.Connect(new IPEndPoint(IPAddress.Parse("192.0.2.1"), 9));
                    route = ((IPEndPoint)s.LocalEndPoint).Address.ToString();
                }
                catch (SocketException) { route = null; }
            }
            return MdnsAdapter.PickNic(Nics(), route);
        }

        static bool IsLanType(NetworkInterfaceType t)
        {
            return t == NetworkInterfaceType.Ethernet || t == NetworkInterfaceType.GigabitEthernet || t == NetworkInterfaceType.FastEthernetT
                || t == NetworkInterfaceType.FastEthernetFx || t == NetworkInterfaceType.Ethernet3Megabit || t == NetworkInterfaceType.Wireless80211;
        }

        public static List<NicFacts> Nics()
        {
            var list = new List<NicFacts>();
            NetworkInterface[] all;
            try { all = NetworkInterface.GetAllNetworkInterfaces(); }
            catch (NetworkInformationException) { return list; }
            foreach (NetworkInterface n in all)
            {
                var f = new NicFacts
                {
                    Name = n.Name,
                    Description = n.Description,
                    Up = n.OperationalStatus == OperationalStatus.Up,
                    LoopbackOrTunnel = n.NetworkInterfaceType == NetworkInterfaceType.Loopback || n.NetworkInterfaceType == NetworkInterfaceType.Tunnel
                        || n.NetworkInterfaceType == NetworkInterfaceType.Ppp,
                    LanType = IsLanType(n.NetworkInterfaceType),
                };
                try
                {
                    IPInterfaceProperties p = n.GetIPProperties();
                    try { IPv4InterfaceProperties v4 = p.GetIPv4Properties(); if (v4 != null) f.Index = v4.Index; }
                    catch (NetworkInformationException) { }
                    foreach (GatewayIPAddressInformation g in p.GatewayAddresses)
                        if (g.Address.AddressFamily == AddressFamily.InterNetwork && !g.Address.Equals(IPAddress.Any)) f.HasGateway = true;
                    foreach (UnicastIPAddressInformation u in p.UnicastAddresses)
                    {
                        if (u.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                        f.Addresses.Add(u.Address.ToString());
                        int prefix = 0;
                        try { if (u.IPv4Mask != null) prefix = Lan.PrefixFromMask(u.IPv4Mask.ToString()); }
                        catch (ArgumentException) { prefix = 0; }
                        f.Prefixes.Add(prefix);
                    }
                }
                catch (NetworkInformationException) { continue; }
                list.Add(f);
            }
            return list;
        }
    }
}
