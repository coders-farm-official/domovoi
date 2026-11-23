// The setup-code QR (1.5.0) for Setup's last page: the dashboard's address
// on this network with the setup code in the URL FRAGMENT only,
//
//   http://192.168.1.30:6369/#setup-code=<the eight words>
//
// the same payload as the core's terminal banner (domovoi/setup_qr.py).
// A browser never sends a fragment to the server, so the code stays out of
// every request line, log and Referer; the dashboard reads it in the page
// (web/static/setup_fragment.js), takes it out of the address bar and fills
// the claim form with it.
//
// It travels to Setup the way the code itself does: on the helper's stdout
// (setup-code verb), never in a file or the log. The modules go as one
// line of '1' (dark) and '0' (light), row by row, which Setup's Pascal
// draws (DomovoiSetup.iss, ShowPhoneCode).

using System;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Domovoi.Windows.Tray;

namespace Domovoi.Windows.Helper
{
    static class SetupQr
    {
        public const string FragmentKey = "setup-code";
        public const int WebPort = 6369;

        // Null unless ip is a usable IPv4 address (a phone can't reach
        // loopback) and code is a well-formed setup code.
        public static string ClaimUrl(string ip, string code)
        {
            IPAddress a;
            if (string.IsNullOrEmpty(ip) || !IPAddress.TryParse(ip, out a) || a.AddressFamily != AddressFamily.InterNetwork) return null;
            if (IPAddress.IsLoopback(a) || a.Equals(IPAddress.Any)) return null;
            if (!SetupCode.IsWellFormed(code)) return null;
            // A setup code is lowercase letters and hyphens (IsWellFormed):
            // nothing in it needs escaping in a fragment.
            return "http://" + a.ToString() + ":" + WebPort.ToString(System.Globalization.CultureInfo.InvariantCulture)
                + "/#" + FragmentKey + "=" + code;
        }

        // The modules, row by row: '1' dark, '0' light.
        public static string Bits(QrCode q)
        {
            var sb = new StringBuilder(q.Size * q.Size);
            for (int y = 0; y < q.Size; y++)
                for (int x = 0; x < q.Size; x++)
                    sb.Append(q.Dark(x, y) ? '1' : '0');
            return sb.ToString();
        }

        public static QrCode Encode(string url)
        {
            return QrCode.EncodeText(url, QrEcc.Medium, -1, true);
        }
    }
}
