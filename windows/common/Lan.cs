// The home network as the installer sees it: is an address a private LAN
// address, and which subnet does it belong to. "LAN only" in PLAN 4.10
// means the firewall rules name this subnet, because the Hyper-V firewall
// cmdlets take no LocalSubnet keyword.

using System;
using System.Globalization;
using System.Net;
using System.Net.Sockets;

namespace Domovoi.Windows
{
    public static class Lan
    {
        static bool TryV4(string s, out uint value)
        {
            value = 0;
            IPAddress ip;
            if (s == null || !IPAddress.TryParse(s, out ip) || ip.AddressFamily != AddressFamily.InterNetwork) return false;
            // IPAddress.TryParse takes "1" or "1.2" too; a LAN address has four parts.
            if (s.Split('.').Length != 4) return false;
            byte[] b = ip.GetAddressBytes();
            value = ((uint)b[0] << 24) | ((uint)b[1] << 16) | ((uint)b[2] << 8) | b[3];
            return true;
        }

        static string Dotted(uint v)
        {
            return Inv.F("{0}.{1}.{2}.{3}", (v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255);
        }

        // RFC 1918 only. Not CGNAT (100.64/10, e.g. a VPN overlay), not
        // link-local, not loopback: none of those is the household's LAN.
        public static bool IsPrivateIPv4(string s)
        {
            uint v;
            if (!TryV4(s, out v)) return false;
            if ((v & 0xFF000000u) == 0x0A000000u) return true;      // 10.0.0.0/8
            if ((v & 0xFFF00000u) == 0xAC100000u) return true;      // 172.16.0.0/12
            if ((v & 0xFFFF0000u) == 0xC0A80000u) return true;      // 192.168.0.0/16
            return false;
        }

        // "192.168.0.0/24" for 192.168.0.240 and prefix 24. Refuses prefixes
        // shorter than /8: no LAN is that big, and a rule for it would be a
        // rule for the internet.
        public static string Subnet(string ip, int prefix)
        {
            uint v;
            if (!TryV4(ip, out v)) throw new ArgumentException("not an IPv4 address: " + ip);
            if (prefix < 8 || prefix > 32) throw new ArgumentException("prefix out of range: /" + Inv.S(prefix));
            uint mask = prefix == 32 ? 0xFFFFFFFFu : ~(0xFFFFFFFFu >> prefix);
            return Dotted(v & mask) + "/" + Inv.S(prefix);
        }

        public static int PrefixFromMask(string mask)
        {
            uint v;
            if (!TryV4(mask, out v)) throw new ArgumentException("not a mask: " + mask);
            int n = 0;
            bool zero = false;
            for (int i = 31; i >= 0; i--)
            {
                bool bit = ((v >> i) & 1) == 1;
                if (bit && zero) throw new ArgumentException("not a contiguous mask: " + mask);
                if (bit) n++; else zero = true;
            }
            return n;
        }

        public static bool IsCidr(string s)
        {
            if (s == null) return false;
            int slash = s.IndexOf('/');
            if (slash < 0) return false;
            uint v;
            int prefix;
            if (!TryV4(s.Substring(0, slash), out v)) return false;
            if (!int.TryParse(s.Substring(slash + 1), NumberStyles.None, CultureInfo.InvariantCulture, out prefix)) return false;
            if (prefix < 8 || prefix > 32) return false;
            // The network address itself, nothing set below the prefix.
            return Subnet(s.Substring(0, slash), prefix) == s;
        }
    }
}
