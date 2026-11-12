// The network name's socket and thread inside DomovoiHost (the state machine
// and the rules are common\MdnsResponder.cs; docs/WINDOWS_HOST.md, "The
// network name").
//
// One UDP socket on 5353, shared with Windows' own responder (dnscache):
// ExclusiveAddressUse off and ReuseAddress on, the spike's finding 3. It
// joins 224.0.0.251 and sends on the LAN adapter only (finding 5): the
// address Windows picks for a route out, when a real adapter that is up and
// has a gateway holds it (MdnsAdapter.Pick), never a vEthernet, Hyper-V, WSL,
// VPN or Docker one. Probes and QU questions are answered by multicast
// (finding 4). Inbound UDP 5353 for DomovoiHost.exe is allowed by Setup's
// firewall rule Domovoi-mDNS-5353 (Private and Domain networks only).
//
// Everything the responder holds is touched on this thread only; the host's
// loop hands it the household's choice (Desire) and reads a copy of its state
// (Snapshot), both under a lock. Nothing read from the network is logged.

using System;
using System.Collections.Generic;
using System.Diagnostics;
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
        const int SioUdpConnReset = -1744830452;

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
        List<byte[]> own = new List<byte[]>();
        DateTime ownAt = DateTime.MinValue;

        public LanNameHost(Action<string> log)
        {
            this.log = log;
            responder = new MdnsResponder(() => own, null);
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
                        string address = enabled ? LanAddress() : null;
                        RefreshOwn();
                        if (enabled && address != null && (sock == null || sockAddress != address))
                        {
                            if (DateTime.UtcNow - failedAt >= Rebind || kick)
                            {
                                // A new address: the goodbye for the old one
                                // leaves by the old socket first.
                                if (sock != null) Send(responder.Stop(Now));
                                string why = OpenSocket(address);
                                if (why != null)
                                {
                                    failedAt = DateTime.UtcNow;
                                    Send(responder.Stop(Now));
                                    responder.Fail("can't listen on UDP 5353: " + why);
                                    log("network name: can't listen on UDP 5353: " + why);
                                    Publish(null);
                                    continue;
                                }
                            }
                            else { Publish(null); Pause(250); continue; }
                        }
                        List<MdnsOut> outs = responder.Configure(name ?? LanName.Default, enabled, address, Now, stickyName);
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
                int n = sock.ReceiveFrom(buf, ref from);
                if (n > MdnsPacket.MaxPacket) return;
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

        string OpenSocket(string address)
        {
            CloseSocket();
            var s = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            try
            {
                s.ExclusiveAddressUse = false;
                s.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
                // An ICMP "port unreachable" for a legacy-unicast answer would
                // otherwise fail the next ReceiveFrom.
                try { s.IOControl(SioUdpConnReset, new byte[4], null); } catch (SocketException) { }
                s.Bind(new IPEndPoint(IPAddress.Any, MdnsPacket.Port));
                IPAddress local = IPAddress.Parse(address);
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
            sockAddress = address;
            log("network name: listening on UDP 5353 on " + address);
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
        }

        void RefreshOwn()
        {
            if (DateTime.UtcNow - ownAt < TimeSpan.FromSeconds(60)) return;
            ownAt = DateTime.UtcNow;
            var l = new List<byte[]>();
            foreach (NicFacts n in Nics()) foreach (string a in n.Addresses) l.Add(IPAddress.Parse(a).GetAddressBytes());
            own = l;
        }

        // The address Windows picks for a route out (nothing is sent),
        // announced only on a real adapter that holds it.
        public static string LanAddress()
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
            return MdnsAdapter.Pick(Nics(), route);
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
                };
                try
                {
                    IPInterfaceProperties p = n.GetIPProperties();
                    foreach (GatewayIPAddressInformation g in p.GatewayAddresses)
                        if (g.Address.AddressFamily == AddressFamily.InterNetwork && !g.Address.Equals(IPAddress.Any)) f.HasGateway = true;
                    foreach (UnicastIPAddressInformation u in p.UnicastAddresses)
                        if (u.Address.AddressFamily == AddressFamily.InterNetwork) f.Addresses.Add(u.Address.ToString());
                }
                catch (NetworkInformationException) { continue; }
                list.Add(f);
            }
            return list;
        }
    }
}
