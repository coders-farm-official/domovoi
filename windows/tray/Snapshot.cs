// --snapshot DIR (developer only): pictures of the tray for review. The
// icon in each state; the menu as each state shows it (states made up
// here, labelled so; the real ones come from a run against a throwaway
// Domovoi); the card helper's steps, driven through a real write onto a
// --fake-card folder with the zip found in --downloads; the phone code.
// Each window is shown on screen for a moment and that part of the screen
// is saved.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace Domovoi.Windows.Tray
{
    static class Snapshot
    {
        static string dir;

        public static int Run(Options opt, ITrayStore store, string exe)
        {
            dir = opt.Snapshot;
            Directory.CreateDirectory(dir);
            string logFile = Path.Combine(dir, "snapshot-tray.log");
            var log = new Log(logFile, 1024 * 1024);

            foreach (TrayState s in Enum.GetValues(typeof(TrayState)))
                foreach (int size in new[] { 16, 24, 32, 64 })
                    using (Bitmap b = Icons.Picture(s, size)) b.Save(Path.Combine(dir, "icon-" + s.ToString().ToLowerInvariant() + "-" + size + ".png"), ImageFormat.Png);

            var one = new List<LanAddress> { new LanAddress { Address = "192.168.1.30", Adapter = "Ethernet" } };
            var two = new List<LanAddress> { new LanAddress { Address = "192.168.1.30", Adapter = "Ethernet" }, new LanAddress { Address = "192.168.1.31", Adapter = "Wi-Fi" } };
            string awake = Awake.SignedOutLine(1800, 0);
            var verdicts = new[]
            {
                new KeyValuePair<string, Verdict>("running", new Verdict { State = TrayState.Running, Line = "Domovoi is running" }),
                new KeyValuePair<string, Verdict>("starting", Status.Decide(new Facts { Service = ServiceFact.Running }, DateTime.UtcNow, DateTime.UtcNow, Status.DefaultGrace)),
                new KeyValuePair<string, Verdict>("service-starting", Status.Decide(new Facts { Service = ServiceFact.Starting }, DateTime.UtcNow, DateTime.UtcNow, Status.DefaultGrace)),
                new KeyValuePair<string, Verdict>("stopped", Status.Decide(new Facts { Service = ServiceFact.Stopped }, DateTime.UtcNow, DateTime.UtcNow, Status.DefaultGrace)),
                new KeyValuePair<string, Verdict>("problem-core", Status.Decide(new Facts { Service = ServiceFact.Running, Web = WebFact.Degraded, DbReachable = true, CoreAlive = false }, DateTime.UtcNow.AddHours(-1), DateTime.UtcNow, Status.DefaultGrace)),
                new KeyValuePair<string, Verdict>("problem-nothing", Status.Decide(new Facts { Service = ServiceFact.Running }, DateTime.UtcNow.AddHours(-1), DateTime.UtcNow, Status.DefaultGrace)),
                new KeyValuePair<string, Verdict>("not-installed", Status.Decide(new Facts { Service = ServiceFact.NotInstalled }, DateTime.UtcNow, DateTime.UtcNow, Status.DefaultGrace)),
            };
            using (var app = new TrayApp(opt, store, log, exe))
            {
                foreach (KeyValuePair<string, Verdict> kv in verdicts)
                    MenuShot(app, "menu-" + kv.Key + ".png", kv.Value, one, awake, -1);
                MenuShot(app, "menu-running-no-network.png", verdicts[0].Value, new List<LanAddress>(), Awake.SignedOutLine(0, 0), -1);
                MenuShot(app, "menu-running-two-networks.png", verdicts[0].Value, two, awake, 3);
            }

            using (var qr = new QrForm("http://192.168.1.30:6369")) FormShot(qr, "phone-code.png");

            string downloads = opt.Downloads ?? Folders.Downloads();
            var drives = new WindowsDrives(opt.FakeCards, false);
            using (var w = new CardWizard(opt.WebUrl, new WindowsDrives(null, false), Path.Combine(dir, "no-such-folder"), delegate { }))
            {
                FormShot(w, "card-1-build.png");
                w.GoTo(CardStep.Zip);
                FormShot(w, "card-2-zip-none.png");
                string bad = Path.Combine(dir, "not-a-card.zip");
                File.WriteAllBytes(bad, new byte[] { 0x50, 0x4b, 5, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 });
                w.SetZip(bad);
                FormShot(w, "card-2-zip-refused.png");
                if (CardZip.Newest(downloads) != null)
                {
                    w.SetZip(CardZip.Newest(downloads));
                    w.GoTo(CardStep.Card);
                    FormShot(w, "card-3-card-none.png");
                }
            }
            if (CardZip.Newest(downloads) == null) { log.Write("no zip in " + downloads + ": the card steps after the zip are not pictured"); return 0; }
            using (var w = new CardWizard(opt.WebUrl, drives, downloads, delegate { }))
            {
                w.GoTo(CardStep.Zip);
                FormShot(w, "card-2-zip.png");
                w.GoTo(CardStep.Card);
                FormShot(w, "card-3-card.png");
                w.SelectCard(0);
                FormShot(w, "card-3-card-chosen.png");
                Click(w, "Next >");
                if (w.Step == CardStep.Password)
                {
                    w.SetPassword("abcd0");
                    FormShot(w, "card-4-password-wrong.png");
                    w.SetPassword(SamplePsk);
                    FormShot(w, "card-4-password.png");
                    Click(w, "Next >");
                }
                FormShot(w, "card-5-write.png");
                w.StartWrite();
                DateTime until = DateTime.UtcNow.AddMinutes(5);
                while (w.Step == CardStep.Write && DateTime.UtcNow < until && !Written(w)) { Application.DoEvents(); Thread.Sleep(50); }
                FormShot(w, "card-5-written.png");
                Click(w, "Next >");
                FormShot(w, "card-6-done.png");
            }
            return 0;
        }

        // The test password the snapshot types (any ten characters from
        // the alphabet; the snapshot's card is a folder).
        public const string SamplePsk = "k7tqmzr4wd";

        static bool Written(CardWizard w)
        {
            foreach (Control c in All(w))
                if (c is Button && c.Text == "Next >" && c.Enabled) return true;
            foreach (Control c in All(w))
                if (c is Button && c.Text == "Try again" && c.Enabled) return true;
            return false;
        }

        static IEnumerable<Control> All(Control root)
        {
            foreach (Control c in root.Controls)
            {
                yield return c;
                foreach (Control d in All(c)) yield return d;
            }
        }

        static void Click(Form f, string text)
        {
            foreach (Control c in All(f))
                if (c is Button && c.Text == text && c.Visible && c.Enabled) { ((Button)c).PerformClick(); Pump(200); return; }
        }

        static void Pump(int ms)
        {
            DateTime until = DateTime.UtcNow.AddMilliseconds(ms);
            while (DateTime.UtcNow < until) { Application.DoEvents(); Thread.Sleep(15); }
        }

        static void FormShot(Form f, string name)
        {
            f.StartPosition = FormStartPosition.Manual;
            f.Location = new Point(80, 80);
            f.TopMost = true;
            if (!f.Visible) f.Show();
            f.Activate();
            Pump(400);
            Save(VisibleBounds(f), name);
        }

        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        struct RECT { public int Left, Top, Right, Bottom; }

        [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
        static extern int DwmGetWindowAttribute(IntPtr hwnd, int attr, out RECT rect, int size);

        // The window as drawn (DWMWA_EXTENDED_FRAME_BOUNDS): Bounds also
        // counts the invisible resize border, which would catch a strip of
        // whatever is behind the window.
        static Rectangle VisibleBounds(Form f)
        {
            RECT r;
            if (DwmGetWindowAttribute(f.Handle, 9, out r, System.Runtime.InteropServices.Marshal.SizeOf(typeof(RECT))) == 0)
                return Rectangle.FromLTRB(r.Left, r.Top, r.Right, r.Bottom);
            return f.Bounds;
        }

        static void MenuShot(TrayApp app, string name, Verdict v, List<LanAddress> lan, string awake, int openSubmenu)
        {
            using (var m = new ContextMenuStrip())
            {
                app.Build(m.Items, v, lan, awake);
                m.Show(new Point(120, 120));
                Pump(300);
                // Only the menu's own pixels (and its submenu's), never what
                // is on the screen around them.
                var parts = new List<Rectangle> { m.Bounds };
                if (openSubmenu >= 0)
                {
                    foreach (ToolStripItem item in m.Items)
                    {
                        var mi = item as ToolStripMenuItem;
                        if (mi != null && mi.HasDropDownItems)
                        {
                            mi.ShowDropDown();
                            Pump(300);
                            parts.Add(mi.DropDown.Bounds);
                            break;
                        }
                    }
                }
                SaveParts(parts, name);
                m.Close();
                Pump(100);
            }
        }

        // Each rectangle copied from the screen onto a plain canvas.
        static void SaveParts(List<Rectangle> parts, string name)
        {
            Rectangle all = parts[0];
            foreach (Rectangle r in parts) all = Rectangle.Union(all, r);
            using (var bmp = new Bitmap(all.Width, all.Height))
            {
                using (Graphics g = Graphics.FromImage(bmp))
                {
                    g.Clear(Color.FromArgb(243, 243, 243));
                    foreach (Rectangle r in parts)
                        g.CopyFromScreen(r.Location, new Point(r.X - all.X, r.Y - all.Y), r.Size);
                }
                bmp.Save(Path.Combine(dir, name), ImageFormat.Png);
            }
        }

        static void Save(Rectangle r, string name)
        {
            using (var bmp = new Bitmap(r.Width, r.Height))
            {
                using (Graphics g = Graphics.FromImage(bmp)) g.CopyFromScreen(r.Location, Point.Empty, r.Size);
                bmp.Save(Path.Combine(dir, name), ImageFormat.Png);
            }
        }
    }
}
