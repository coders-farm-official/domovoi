// The notification-area icon and its menu.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace Domovoi.Windows.Tray
{
    public sealed class TrayApp : ApplicationContext
    {
        readonly Options opt;
        readonly ITrayStore store;
        readonly Log log;
        readonly string exe;
        readonly NotifyIcon icon = new NotifyIcon();
        readonly ContextMenuStrip menu = new ContextMenuStrip();
        readonly System.Windows.Forms.Timer poll = new System.Windows.Forms.Timer();
        readonly Control ui = new Control();
        readonly Probe probe;
        readonly ReadyClock clock = new ReadyClock();
        readonly Notifier notifier = new Notifier();
        Verdict verdict = new Verdict { State = TrayState.Starting, Line = "Domovoi: checking..." };
        TrayState? shownState;
        Icon current;
        int probing;
        CardWizard wizard;

        public TrayApp(Options opt, ITrayStore store, Log log, string exe)
        {
            this.opt = opt;
            this.store = store;
            this.log = log;
            this.exe = exe;
            ui.CreateControl();
            probe = new Probe(opt.Service, opt.WebUrl, opt.CoreUrl);
            menu.Opening += delegate { Build(menu.Items, verdict, Addresses.Pick(Addresses.FromWindows()), Awake.CurrentSignedOutLine()); };
            menu.Items.Add("...");
            icon.ContextMenuStrip = menu;
            icon.MouseDoubleClick += delegate (object s, MouseEventArgs e) { if (e.Button == MouseButtons.Left) OpenUrl(opt.WebUrl + "/"); };
            ShowState();
            icon.Visible = true;
            if (Startup.KeepAwake(store)) Awake.Hold(true);
            poll.Interval = 5000;
            poll.Tick += delegate { Poll(); };
            poll.Start();
            Poll();
            log.Write("tray started (" + (opt.Dev ? "developer profile " + opt.Profile + ", " + opt.WebUrl : "installed Domovoi") + ")");
        }

        // -- status -----------------------------------------------------

        void Poll()
        {
            if (Interlocked.Exchange(ref probing, 1) == 1) return;
            ThreadPool.QueueUserWorkItem(delegate
            {
                Facts f;
                try { f = probe.Gather(); }
                catch (Exception e) { f = new Facts(); log.Write("probe failed: " + e.Message); }
                try { ui.BeginInvoke(new Action(delegate { Apply(f); })); }
                catch (InvalidOperationException) { }
                finally { Interlocked.Exchange(ref probing, 0); }
            });
        }

        void Apply(Facts f)
        {
            DateTime now = DateTime.UtcNow;
            Verdict v = Status.Decide(f, clock.Since(f, now), now, opt.Grace);
            if (v.Line != verdict.Line) log.Write(v.Line + (v.Detail != null ? " (" + v.Detail + ")" : ""));
            verdict = v;
            ShowState();
            string[] note = notifier.Next(v);
            if (note != null && icon.Visible)
                icon.ShowBalloonTip(8000, note[0], note[1], v.Up ? ToolTipIcon.Info : ToolTipIcon.Warning);
        }

        void ShowState()
        {
            if (shownState != verdict.State)
            {
                Icon old = current;
                current = Icons.Make(verdict.State, SystemInformation.SmallIconSize.Width);
                icon.Icon = current;
                if (old != null) old.Dispose();
                shownState = verdict.State;
            }
            string tip = verdict.Line;
            icon.Text = tip.Length > 63 ? tip.Substring(0, 63) : tip;
        }

        // -- the menu ---------------------------------------------------

        // The menu for a verdict (also used by --snapshot).
        public void Build(ToolStripItemCollection items, Verdict v, List<LanAddress> lan, string awakeLine)
        {
            items.Clear();
            var head = new ToolStripMenuItem(v.Line) { Enabled = true, Image = Icons.Picture(v.State, 16) };
            head.Font = new Font(menu.Font, FontStyle.Bold);
            head.Click += delegate { OpenUrl(opt.WebUrl + "/"); };
            items.Add(head);
            if (v.Detail != null) items.Add(new ToolStripMenuItem(v.Detail) { Enabled = false });
            items.Add(new ToolStripSeparator());

            var open = new ToolStripMenuItem("Open Domovoi");
            open.Font = new Font(menu.Font, FontStyle.Bold);
            open.Click += delegate { OpenUrl(opt.WebUrl + "/"); };
            items.Add(open);
            AddressItems(items, lan);
            var manual = new ToolStripMenuItem("User manual");
            manual.Click += delegate { OpenUrl(ManualUrl(opt.WebUrl)); };
            items.Add(manual);
            items.Add(new ToolStripSeparator());

            var card = new ToolStripMenuItem("Prepare a satellite card...");
            card.Click += delegate { OpenCardHelper(); };
            items.Add(card);
            items.Add(new ToolStripSeparator());

            var awake = new ToolStripMenuItem("Keep this PC awake") { Checked = Startup.KeepAwake(store), CheckOnClick = false };
            awake.Click += delegate
            {
                bool on = !Startup.KeepAwake(store);
                store.SetDword(Startup.AwakePref, on ? 1 : 0);
                Awake.Hold(on);
                log.Write("keep this PC awake: " + (on ? "on" : "off"));
            };
            items.Add(awake);
            if (awakeLine != null) items.Add(new ToolStripMenuItem(awakeLine) { Enabled = false });
            var start = new ToolStripMenuItem("Start with Windows") { Checked = Startup.StartWithWindows(store) };
            start.Click += delegate
            {
                bool on = !Startup.StartWithWindows(store);
                Startup.SetStartWithWindows(store, exe, on, File.Exists);
                log.Write("start with Windows: " + (on ? "on" : "off"));
            };
            items.Add(start);
            items.Add(new ToolStripSeparator());

            var logs = new ToolStripMenuItem("Save logs for support...");
            logs.Click += delegate { SaveLogs(); };
            items.Add(logs);
            var restart = new ToolStripMenuItem("Restart Domovoi...") { Enabled = opt.Service != null };
            restart.Click += delegate { Restart(); };
            items.Add(restart);
            items.Add(new ToolStripSeparator());
            var hide = new ToolStripMenuItem("Hide this icon");
            hide.Click += delegate { Hide(); };
            items.Add(hide);
        }

        void AddressItems(ToolStripItemCollection items, List<LanAddress> lan)
        {
            if (lan.Count == 0)
            {
                items.Add(new ToolStripMenuItem("Open on this network (no network found)") { Enabled = false });
                return;
            }
            string[] names = { "Open on this network", "Copy address", "Show a code for your phone" };
            Action<string>[] acts = { delegate (string u) { OpenUrl(u + "/"); }, CopyAddress, ShowCode };
            for (int i = 0; i < names.Length; i++)
            {
                Action<string> act = acts[i];
                if (lan.Count == 1)
                {
                    string url = Addresses.Url(opt.WebUrl, lan[0].Address);
                    var item = new ToolStripMenuItem(i == 0 ? names[i] + " (" + lan[0].Address + ")" : names[i]);
                    item.Click += delegate { act(url); };
                    items.Add(item);
                }
                else
                {
                    var parent = new ToolStripMenuItem(names[i]);
                    foreach (LanAddress a in lan)
                    {
                        string url = Addresses.Url(opt.WebUrl, a.Address);
                        var sub = new ToolStripMenuItem(a.Address + "  (" + a.Adapter + ")");
                        sub.Click += delegate { act(url); };
                        parent.DropDownItems.Add(sub);
                    }
                    items.Add(parent);
                }
            }
        }

        // -- actions ----------------------------------------------------

        // The dashboard's user manual (its #manual page), on the same
        // dashboard "Open Domovoi" opens.
        public static string ManualUrl(string webUrl) { return webUrl.TrimEnd('/') + "/#manual"; }

        public static void OpenUrl(string url)
        {
            Uri u;
            if (!Uri.TryCreate(url, UriKind.Absolute, out u) || (u.Scheme != "http" && u.Scheme != "https")) return;
            try { Process.Start(new ProcessStartInfo(u.AbsoluteUri) { UseShellExecute = true }); }
            catch (System.ComponentModel.Win32Exception) { }
        }

        void CopyAddress(string url)
        {
            try
            {
                Clipboard.SetText(url);
                if (icon.Visible) icon.ShowBalloonTip(4000, "Address copied", url, ToolTipIcon.Info);
            }
            catch (ExternalException) { }
        }

        void ShowCode(string url)
        {
            var f = new QrForm(url);
            f.FormClosed += delegate { f.Dispose(); };
            f.Show();
            f.Activate();
        }

        public void OpenCardHelper()
        {
            if (wizard != null && !wizard.IsDisposed) { wizard.WindowState = FormWindowState.Normal; wizard.Activate(); return; }
            wizard = new CardWizard(opt.WebUrl, new WindowsDrives(opt.FakeCards, !opt.Dev), opt.Downloads ?? Folders.Downloads(), OpenUrl);
            wizard.FormClosed += delegate { wizard.Dispose(); wizard = null; };
            wizard.Show();
            wizard.Activate();
        }

        void Restart()
        {
            if (MessageBox.Show("Restart Domovoi?\n\nSatellites, phones and the dashboard lose Domovoi for a minute or two while it starts again. Windows asks for an administrator's permission next.",
                    "Restart Domovoi", MessageBoxButtons.OKCancel, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) != DialogResult.OK)
                return;
            string args = Admin.RestartArguments(opt.Service);
            log.Write("restart asked for (" + opt.Service + ")");
            ThreadPool.QueueUserWorkItem(delegate
            {
                string why = Admin.RunElevated(Admin.PowerShell, args, 5 * 60 * 1000);
                log.Write("restart: " + (why ?? "done"));
                ui.BeginInvoke(new Action(delegate
                {
                    if (why == null) { if (icon.Visible) icon.ShowBalloonTip(5000, "Domovoi is restarting", "It's back in a minute or two.", ToolTipIcon.Info); }
                    else if (why != "cancelled") MessageBox.Show("Domovoi didn't restart: " + why + ".", "Restart Domovoi", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    Poll();
                }));
            });
        }

        void SaveLogs()
        {
            string dest = Admin.LogsFolder();
            string helper = Admin.Helper();
            string mine = null;
            try { mine = CopyOwnLog(dest); }
            catch (IOException e) { log.Write("copying the tray's log: " + e.Message); }
            catch (UnauthorizedAccessException e) { log.Write("copying the tray's log: " + e.Message); }
            if (helper == null)
            {
                MessageBox.Show("Domovoi's own logs are in C:\\ProgramData\\Domovoi\\logs, which only an administrator can open, and Setup's helper that copies them isn't on this PC."
                    + (mine != null ? "\n\nThis icon's own log is in " + dest + "." : ""), "Save logs for support", MessageBoxButtons.OK, MessageBoxIcon.Information);
                if (mine != null) OpenFolder(dest);
                return;
            }
            if (MessageBox.Show("Domovoi's logs are in a folder only an administrator can open. Windows asks for permission next, then they're copied to " + dest
                    + ".\n\nPasswords, tokens and setup codes are blanked as far as Domovoi can recognise them; look through the files before you share them.",
                    "Save logs for support", MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK)
            {
                if (mine != null) OpenFolder(dest);
                return;
            }
            string args = Admin.CopyLogsArguments(dest);
            ThreadPool.QueueUserWorkItem(delegate
            {
                string why = Admin.RunElevated(helper, args, 2 * 60 * 1000);
                log.Write("copy logs: " + (why ?? "done"));
                ui.BeginInvoke(new Action(delegate
                {
                    if (why != null && why != "cancelled")
                        MessageBox.Show("The logs weren't copied: " + why + ".", "Save logs for support", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    if (why == null || mine != null) OpenFolder(dest);
                }));
            });
        }

        // This icon's log, blanked like Setup's copies, into dest.
        string CopyOwnLog(string dest)
        {
            if (!File.Exists(log.Path)) return null;
            Directory.CreateDirectory(dest);
            string to = Path.Combine(dest, "tray-" + DateTime.Now.ToString("yyyyMMdd-HHmmss", System.Globalization.CultureInfo.InvariantCulture) + ".log");
            var sb = new StringBuilder();
            foreach (string line in File.ReadAllLines(log.Path)) sb.Append(Redact.Line(line)).Append("\r\n");
            using (var fs = new FileStream(to, FileMode.CreateNew, FileAccess.Write))
            {
                byte[] b = new UTF8Encoding(false).GetBytes(sb.ToString());
                fs.Write(b, 0, b.Length);
            }
            return to;
        }

        static void OpenFolder(string dir)
        {
            if (!Directory.Exists(dir)) return;
            try { Process.Start(new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe"), "\"" + dir.TrimEnd('\\') + "\\\"") { UseShellExecute = false }); }
            catch (System.ComponentModel.Win32Exception) { }
        }

        void Hide()
        {
            if (MessageBox.Show("Hide the Domovoi icon?\n\nDomovoi keeps running, and so does \"Keep this PC awake\". The icon comes back the next time you sign in, or when you open Domovoi from the Start menu.",
                    "Hide this icon", MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK)
                return;
            icon.Visible = false;
            log.Write("icon hidden");
        }

        // A second start (the Start menu's "Domovoi", or running the exe
        // again) asks the running tray for these.
        public void ShowIcon() { icon.Visible = true; }

        public void OpenDashboard() { OpenUrl(opt.WebUrl + "/"); }

        public void Invoke(Action a)
        {
            try { ui.BeginInvoke(a); }
            catch (InvalidOperationException) { }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                poll.Dispose();
                icon.Visible = false;
                icon.Dispose();
                menu.Dispose();
                if (current != null) current.Dispose();
                ui.Dispose();
            }
            base.Dispose(disposing);
        }
    }

    public static class Folders
    {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
        static extern int SHGetKnownFolderPath(ref Guid id, uint flags, IntPtr token, out IntPtr path);

        static readonly Guid DownloadsId = new Guid("374DE290-123F-4565-9164-39C4925E467B");

        public static string Downloads()
        {
            IntPtr p;
            Guid id = DownloadsId;
            if (SHGetKnownFolderPath(ref id, 0, IntPtr.Zero, out p) == 0)
            {
                try { return Marshal.PtrToStringUni(p); }
                finally { Marshal.FreeCoTaskMem(p); }
            }
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads");
        }
    }
}
