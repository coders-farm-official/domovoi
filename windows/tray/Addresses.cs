// This PC's address on the home network, for "Open on this network", "Copy
// address" and the phone code. Domovoi's Linux system shares the PC's
// network (WSL's mirrored networking), so the dashboard answers on the
// address of the Windows adapter that leads to the router: one that is up,
// has an IPv4 default gateway, and a private (RFC 1918) address. A VPN or
// a virtual switch without a gateway is left out; with several, the menu
// lists each.

using System;
using System.Collections.Generic;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;

namespace Domovoi.Windows.Tray
{
    public sealed class AdapterFacts
    {
        public string Name;
        public bool Up;
        public bool LoopbackOrTunnel;
        public List<string> Gateways = new List<string>();
        public List<string> Addresses = new List<string>();
    }

    public sealed class LanAddress
    {
        public string Address;
        public string Adapter;
    }

    public static class Addresses
    {
        public static List<LanAddress> Pick(IEnumerable<AdapterFacts> adapters)
        {
            var list = new List<LanAddress>();
            var seen = new HashSet<string>();
            foreach (AdapterFacts a in adapters)
            {
                if (!a.Up || a.LoopbackOrTunnel) continue;
                bool gateway = false;
                foreach (string g in a.Gateways) if (IsUsableGateway(g)) { gateway = true; break; }
                if (!gateway) continue;
                foreach (string ip in a.Addresses)
                    if (Lan.IsPrivateIPv4(ip) && seen.Add(ip)) list.Add(new LanAddress { Address = ip, Adapter = a.Name });
            }
            return list;
        }

        // A default gateway is any real IPv4 address (a router's public
        // address is possible); 0.0.0.0 is "none".
        static bool IsUsableGateway(string g)
        {
            IPAddress ip;
            return g != null && IPAddress.TryParse(g, out ip) && ip.AddressFamily == AddressFamily.InterNetwork && g != "0.0.0.0" && g.Split('.').Length == 4;
        }

        // "http://192.168.1.30:6369", from the dashboard's local address.
        public static string Url(string localUrl, string address)
        {
            var u = new Uri(localUrl);
            return u.Scheme + "://" + address + ":" + Inv.S(u.Port);
        }

        public static List<AdapterFacts> FromWindows()
        {
            var list = new List<AdapterFacts>();
            NetworkInterface[] all;
            try { all = NetworkInterface.GetAllNetworkInterfaces(); }
            catch (NetworkInformationException) { return list; }
            foreach (NetworkInterface n in all)
            {
                var a = new AdapterFacts
                {
                    Name = n.Name,
                    Up = n.OperationalStatus == OperationalStatus.Up,
                    LoopbackOrTunnel = n.NetworkInterfaceType == NetworkInterfaceType.Loopback || n.NetworkInterfaceType == NetworkInterfaceType.Tunnel,
                };
                try
                {
                    IPInterfaceProperties p = n.GetIPProperties();
                    foreach (GatewayIPAddressInformation g in p.GatewayAddresses)
                        if (g.Address.AddressFamily == AddressFamily.InterNetwork) a.Gateways.Add(g.Address.ToString());
                    foreach (UnicastIPAddressInformation u in p.UnicastAddresses)
                        if (u.Address.AddressFamily == AddressFamily.InterNetwork) a.Addresses.Add(u.Address.ToString());
                }
                catch (NetworkInformationException) { continue; }
                list.Add(a);
            }
            return list;
        }
    }
}
