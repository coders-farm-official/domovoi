// DomovoiTray.exe's command line.
//
//   (nothing)      start the tray (or show the running one's icon again)
//   --autostart    the sign-in start: quietly does nothing when this
//                  account turned "Start with Windows" off
//   --open         open the dashboard (the Start menu's "Domovoi")
//   --card         open "Prepare a satellite card"
//
// Developer-only (docs/WINDOWS_HOST.md, "The tray icon"): point the tray at
// a throwaway Domovoi instead of the installed one. Any of these makes it a
// developer run, with its own registry key, its own single-instance name
// and no sign-in registration, so it can't touch the real tray's settings:
//
//   --web-url URL   the dashboard (default http://localhost:6369)
//   --core-url URL  the core (default http://localhost:6370)
//   --service NAME  the service to read (default DomovoiHost; "-": none)
//   --profile NAME  the developer profile (default "dev")
//   --downloads DIR where the card helper looks for the zip
//   --fake-card DIR a folder the card helper lists as a card (repeatable)
//   --grace SECONDS how long "starting" lasts before "problem"
//   --snapshot DIR  write pictures of the menu, the icons and the card
//                   helper's steps to DIR, then exit
//   --snapshot-live NAME  with --snapshot: only the menu as the real
//                   status makes it now (DIR\menu-live-NAME.png), then exit
//   --snapshot-psk P  with --snapshot: the password the card steps type

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace Domovoi.Windows.Tray
{
    public sealed class Options
    {
        public const string DefaultWeb = "http://localhost:6369";
        public const string DefaultCore = "http://localhost:6370";
        public const string DefaultService = "DomovoiHost";

        public bool Autostart, Open, Card;
        public string WebUrl = DefaultWeb, CoreUrl = DefaultCore, Service = DefaultService;
        public string Profile;            // null: the real tray
        public string Downloads;
        public List<string> FakeCards = new List<string>();
        public TimeSpan Grace = Status.DefaultGrace;
        public string Snapshot, SnapshotLive, SnapshotPsk;

        public bool Dev { get { return Profile != null; } }

        static readonly Regex ProfileName = new Regex(@"^[A-Za-z0-9_-]{1,32}\z");
        static readonly Regex ServiceName = new Regex(@"^[A-Za-z0-9_.-]{1,80}\z");

        public static Options Parse(string[] argv)
        {
            var o = new Options();
            bool dev = false;
            for (int i = 0; i < argv.Length; i++)
            {
                string a = argv[i];
                switch (a)
                {
                    case "--autostart": o.Autostart = true; continue;
                    case "--open": o.Open = true; continue;
                    case "--card": o.Card = true; continue;
                }
                if (i + 1 >= argv.Length) throw new ArgumentException("unknown option or missing value: " + a);
                string v = argv[++i];
                switch (a)
                {
                    case "--web-url": o.WebUrl = HttpUrl(v); dev = true; break;
                    case "--core-url": o.CoreUrl = HttpUrl(v); dev = true; break;
                    case "--service":
                        if (v != "-" && !ServiceName.IsMatch(v)) throw new ArgumentException("--service: not a service name");
                        o.Service = v == "-" ? null : v;
                        dev = true;
                        break;
                    case "--profile":
                        if (!ProfileName.IsMatch(v)) throw new ArgumentException("--profile: letters, digits, - and _ only");
                        o.Profile = v;
                        dev = true;
                        break;
                    case "--downloads": o.Downloads = v; dev = true; break;
                    case "--fake-card": o.FakeCards.Add(v); dev = true; break;
                    case "--grace":
                        int s;
                        if (!int.TryParse(v, NumberStyles.None, CultureInfo.InvariantCulture, out s)) throw new ArgumentException("--grace: seconds");
                        o.Grace = TimeSpan.FromSeconds(s);
                        dev = true;
                        break;
                    case "--snapshot": o.Snapshot = v; dev = true; break;
                    case "--snapshot-live":
                        if (!ProfileName.IsMatch(v)) throw new ArgumentException("--snapshot-live: letters, digits, - and _ only");
                        o.SnapshotLive = v;
                        dev = true;
                        break;
                    case "--snapshot-psk": o.SnapshotPsk = v; dev = true; break;
                    default: throw new ArgumentException("unknown option: " + a);
                }
            }
            if (dev && o.Profile == null) o.Profile = "dev";
            return o;
        }

        static string HttpUrl(string v)
        {
            Uri u;
            if (!Uri.TryCreate(v, UriKind.Absolute, out u) || (u.Scheme != "http" && u.Scheme != "https") || !string.IsNullOrEmpty(u.UserInfo))
                throw new ArgumentException("not an http address: " + v);
            return u.GetLeftPart(UriPartial.Authority);
        }
    }
}
