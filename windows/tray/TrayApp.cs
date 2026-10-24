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

        // The Windows update (Updater.cs).
        readonly System.Windows.Forms.Timer updatePoll = new System.Windows.Forms.Timer();
        readonly IUpdateServer updates;
        readonly string updatesRoot;
        UpdateState updateState;
        string seenRequest;
        bool stepRunning;
        int updating;
        Action balloonAction;

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
            updates = new HttpUpdateServer(opt.CoreUrl);
            updatesRoot = UpdateRun.Root(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData));
            // A developer run keeps its runs apart from the real tray's.
            if (opt.Dev) updatesRoot = updatesRoot + "-dev-" + opt.Profile;
            icon.BalloonTipClicked += delegate { Action a = balloonAction; balloonAction = null; if (a != null) a(); };
            icon.BalloonTipClosed += delegate { balloonAction = null; };
            updatePoll.Interval = 30000;
            updatePoll.Tick += delegate { PollUpdates(); };
            if (!Program.Snapshotting)
            {
                PendingRun keep = PendingRun.Load(updatesRoot);
                UpdateRun.Prune(updatesRoot, DateTime.UtcNow, keep != null ? keep.Run : null);
                updatePoll.Start();
                PollUpdates();
            }
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

            var card = new ToolStripMenuItem("Prepare a room's SD card...");
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
            bool finish = updateState != null && updateState.RequestId != null && updateState.StepNeeded;
            var update = new ToolStripMenuItem(stepRunning ? "Updating Domovoi..." : finish ? "Finish updating Domovoi..." : "Update Domovoi...")
            {
                Enabled = opt.Service != null && !stepRunning,
            };
            if (finish && !stepRunning) update.Font = new Font(menu.Font, FontStyle.Bold);
            UpdateState asOf = updateState;
            update.Click += delegate { if (finish) StartWindowsStep(asOf, false, true); else UpdateFromMenu(); };
            items.Add(update);
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
            string web = opt.WebUrl;
            wizard = new CardWizard(web, new WindowsDrives(opt.FakeCards, !opt.Dev), opt.Downloads ?? Folders.Downloads(), OpenUrl,
                delegate { return Probe.ServerFingerprint(web); });
            wizard.FormClosed += delegate { wizard.Dispose(); wizard = null; };
            wizard.Show();
            wizard.Activate();
        }

        void Restart()
        {
            if (MessageBox.Show("Restart Domovoi?\n\nRooms, phones and the dashboard lose Domovoi for a minute or two while it starts again. Windows asks for an administrator's permission next.",
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

        // -- the Windows update (Updater.cs) ------------------------------

        void PollUpdates()
        {
            if (Interlocked.Exchange(ref updating, 1) == 1) return;
            bool running = stepRunning;
            string seen = seenRequest;
            ThreadPool.QueueUserWorkItem(delegate
            {
                UpdateState st = null;
                PendingDone done = null;
                UpdateAction act = UpdateAction.Nothing;
                try
                {
                    // An earlier run's outcome first, then the state: never
                    // a decision on a state read before that outcome landed.
                    act = UpdatePoll.Run(updates, updatesRoot, UpdateRun.AdminRoot, DateTime.UtcNow, seen, running, out st, out done);
                }
                catch (Exception e) { log.Write("update poll failed: " + e.Message); }
                finally { Interlocked.Exchange(ref updating, 0); }
                Invoke(delegate { OnUpdateState(st, act, done); });
            });
        }

        void OnUpdateState(UpdateState st, UpdateAction act, PendingDone done)
        {
            updateState = st;
            if (done != null && done.Show)
            {
                log.Write("update (finished while the icon was away): " + done.Result.Outcome + (done.Result.Detail != null ? " (" + done.Result.Detail + ")" : ""));
                if (icon.Visible)
                    icon.ShowBalloonTip(15000, done.Result.Ok ? "Domovoi is up to date" : "Domovoi's update didn't finish",
                        WindowsStep.Words(done.Result, done.AfterLinux), done.Result.Ok ? ToolTipIcon.Info : ToolTipIcon.Warning);
            }
            if (act == UpdateAction.Nothing || stepRunning || st == null || st.RequestId == seenRequest) return;
            seenRequest = st.RequestId;
            log.Write("the dashboard asked for the Windows step (" + (st.RequestOnThisPc ? "on this PC" : "from another device") + ", " + st.TargetTag + ")");
            if (act == UpdateAction.StartNow) { StartWindowsStep(st, false, false); return; }
            if (!icon.Visible) icon.Visible = true;
            balloonAction = delegate { StartWindowsStep(updateState ?? st, false, true); };
            icon.ShowBalloonTip(30000, "Finish updating Domovoi",
                (st.RequestAfterLinux ? "The Linux part is updated. " : "") + "Click here to update the Windows part (Domovoi " + st.TargetVersion + "); Windows asks for an administrator's permission.", ToolTipIcon.Info);
        }

        void UpdateFromMenu()
        {
            if (MessageBox.Show("Update Domovoi?\n\nDomovoi checks for updates and installs them: the Linux part first (a backup, then a restart: rooms, phones and the dashboard lose Domovoi for a minute or two), then the Windows part when a newer one is out, which this icon downloads and checks against Domovoi's release key first.\n\nWindows asks for an administrator's permission next.",
                    "Update Domovoi", MessageBoxButtons.OKCancel, MessageBoxIcon.Question, MessageBoxDefaultButton.Button1) != DialogResult.OK)
                return;
            StartWindowsStep(updateState, true, true);
        }

        StepEnv Env()
        {
            return new StepEnv
            {
                Files = new GitHubReleaseFiles(),
                Elevator = new ShellElevator(),
                Key = ReleaseKey.Pinned(),
                Installed = BuildVersion.Version,
                Helper = Admin.Helper(),
                UpdatesRoot = updatesRoot,
                Dev = opt.Dev,
            };
        }

        // linuxFirst: the menu's "Update Domovoi" (both halves); false: a
        // dashboard request (claimed first: one tray only). asked: someone
        // here clicked, so a problem gets a message box rather than a
        // notification.
        void StartWindowsStep(UpdateState known, bool linuxFirst, bool asked)
        {
            if (stepRunning) return;
            stepRunning = true;
            string requestId = linuxFirst ? null : (known != null ? known.RequestId : null);
            bool afterLinux = !linuxFirst && known != null && known.RequestAfterLinux;
            log.Write("update: " + (linuxFirst ? "both halves, from the menu" : "the Windows step"));
            ThreadPool.QueueUserWorkItem(delegate
            {
                StepResult r;
                string runDir = null, claim = null;
                try
                {
                    if (requestId != null)
                    {
                        claim = updates.Claim(requestId);
                        if (claim == null)
                        {
                            log.Write("update: request " + requestId + " is another tray's, or gone; leaving it");
                            Invoke(delegate
                            {
                                stepRunning = false;
                                if (asked) MessageBox.Show("Another signed-in account's Domovoi icon is already updating Domovoi (or the dashboard withdrew the request).", "Update Domovoi", MessageBoxButtons.OK, MessageBoxIcon.Information);
                            });
                            return;
                        }
                    }
                    UpdateState st = updates.State() ?? known;
                    r = WindowsStep.Start(Env(), st, linuxFirst, out runDir);
                }
                catch (Exception e)
                {
                    if (!(e is IOException || e is UnauthorizedAccessException || e is InvalidOperationException || e is ArgumentException || e is System.Security.Cryptography.CryptographicException)) throw;
                    r = new StepResult { Outcome = Outcome.Failed, Detail = "The update couldn't start: " + e.Message };
                }
                if (!r.Launched)
                {
                    Done(r, requestId, claim, afterLinux, asked);
                    return;
                }
                bool setup = r.Kind == "setup";
                var pending = new PendingRun
                {
                    Run = r.Run, RequestId = requestId, Claim = claim, Kind = r.Kind, AfterLinux = afterLinux,
                    Started = DateTime.UtcNow.ToString("o", System.Globalization.CultureInfo.InvariantCulture), Version = r.Version,
                };
                pending.Save(updatesRoot);
                log.Write("update: " + (setup ? "the helper checks and starts Setup " + r.Version : "the Linux part runs") + " (" + runDir + ")");
                Invoke(delegate
                {
                    if (icon.Visible)
                        icon.ShowBalloonTip(15000, "Updating Domovoi", setup
                            ? "Setup updates the Windows side now. This icon goes away while it does, and comes back when it's done."
                            : "The Linux part is updating. Domovoi is back in a few minutes.", ToolTipIcon.Info);
                });
                int code;
                try { code = r.Process.WaitForExit(); }
                catch (InvalidOperationException) { code = -1; }
                finally { if (r.Held != null) r.Held.Dispose(); }
                // The helper has handed over to Setup, which ends this icon to
                // replace its file: until then (or a refusal), its result.
                StepResult fin = WindowsStep.Finish(UpdateRun.AdminRoot, r.Run, code, r.Kind, r.Version);
                DateTime until = DateTime.UtcNow.AddHours(3);
                while (fin == null && DateTime.UtcNow < until)
                {
                    Thread.Sleep(3000);
                    fin = WindowsStep.Finish(UpdateRun.AdminRoot, r.Run, code, r.Kind, r.Version);
                }
                if (fin == null) fin = new StepResult { Outcome = Outcome.RepairFailed, Detail = "Setup ended without saying how it went.", Log = r.Log };
                pending.Outcome = fin.Outcome; pending.Detail = fin.Detail; pending.Log = fin.Log; pending.Version = fin.Version ?? pending.Version;
                pending.Save(updatesRoot);
                Done(fin, requestId, claim, afterLinux, asked);
            });
        }

        // An outcome: reported to the core (with the claim), said here, and
        // the run forgotten once the core has it.
        void Done(StepResult r, string requestId, string claim, bool afterLinux, bool asked)
        {
            log.Write("update: " + r.Outcome + (r.Detail != null ? " (" + r.Detail + ")" : "") + (r.Log != null ? " log " + r.Log : ""));
            bool reported = updates.Report(requestId, claim, r.Outcome, r.Detail, r.Log, r.Version);
            PendingRun p = PendingRun.Load(updatesRoot);
            if (p != null && p.Outcome != null)
            {
                if (reported) PendingRun.Clear(updatesRoot);
                else { p.Shown = true; p.Save(updatesRoot); }
            }
            string words = WindowsStep.Words(r, afterLinux);
            Invoke(delegate
            {
                stepRunning = false;
                if (asked && !r.Ok && r.Outcome != Outcome.Declined)
                    MessageBox.Show(words, "Update Domovoi", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                else if (icon.Visible)
                    icon.ShowBalloonTip(15000, r.Ok ? "Domovoi is up to date" : "Domovoi's update didn't finish", words, r.Ok ? ToolTipIcon.Info : ToolTipIcon.Warning);
                Poll();
                PollUpdates();
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
            if (MessageBox.Show("Hide the Domovoi icon?\n\nDomovoi keeps running, and so does \"Keep this PC awake\". The icon comes back the next time you sign in, or when you start it again (Domovoi in the Start menu, or DomovoiTray.exe).",
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
                updatePoll.Dispose();
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
