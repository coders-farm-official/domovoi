// "Prepare a satellite card": the card helper. Domovoi's Linux system on a
// Windows PC can't see SD cards, so the dashboard builds the card files as
// a zip and this window puts them on the card, exactly as a server that
// can see the card would (CardWriter).
//
// Steps: build the files in the dashboard; the zip; the card; the setup
// network password (Wi-Fi setup cards); write, check, eject; done.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace Domovoi.Windows.Tray
{
    public enum CardStep { Build, Zip, Card, Password, Write, Done }

    public sealed class CardWizard : Form
    {
        readonly string webUrl;
        readonly IDriveSource drives;
        readonly Action<string> openUrl;
        readonly Func<string> serverFingerprint;
        readonly Label title = new Label(), lead = new Label();
        readonly Panel body = new Panel();
        readonly Button back = new Button(), next = new Button(), cancel = new Button();
        readonly System.Windows.Forms.Timer rescan = new System.Windows.Forms.Timer();
        readonly Dictionary<CardStep, Control> pages = new Dictionary<CardStep, Control>();

        CardStep step = CardStep.Build;
        CardPackage package;
        List<CardTarget> targets = new List<CardTarget>();
        CardTarget chosen;
        bool written, writing;

        // Zip page
        readonly TextBox zipPath = new TextBox();
        readonly Label zipResult = new Label();
        // Card page
        readonly ListView cardList = new ListView();
        readonly Label cardNote = new Label();
        // Password page
        readonly TextBox ssidBox = new TextBox(), pskBox = new TextBox();
        readonly Label pskNote = new Label();
        // Write page
        readonly Label writeLead = new Label(), writeStatus = new Label();
        readonly ProgressBar writeBar = new ProgressBar();
        readonly ListBox writeLog = new ListBox();
        readonly Button writeButton = new Button(), ejectButton = new Button();
        // Done page
        readonly Label doneText = new Label();

        static readonly Color ErrorColor = Color.FromArgb(185, 28, 28);
        static readonly Color WarnColor = Color.FromArgb(146, 84, 0);
        static readonly Color OkColor = Color.FromArgb(22, 120, 60);

        public CardStep Step { get { return step; } }

        // serverFingerprint: this PC's Domovoi's identity (Probe.ServerFingerprint), null when it doesn't answer.
        public CardWizard(string webUrl, IDriveSource drives, string downloads, Action<string> openUrl, Func<string> serverFingerprint)
        {
            this.webUrl = webUrl.TrimEnd('/');
            this.drives = drives;
            this.openUrl = openUrl;
            this.serverFingerprint = serverFingerprint;
            Text = "Prepare a satellite card";
            Icon = Icons.App(32);
            Font = SystemFonts.MessageBoxFont;
            AutoScaleMode = AutoScaleMode.Dpi;
            AutoScaleDimensions = new SizeF(96f, 96f);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = true;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(600, 440);
            BackColor = SystemColors.Window;

            title.SetBounds(20, 16, 560, 30);
            title.Font = new Font(Font.FontFamily, 13f, FontStyle.Bold);
            lead.SetBounds(20, 50, 560, 60);
            body.SetBounds(20, 112, 560, 268);
            var line = new Label { BorderStyle = BorderStyle.Fixed3D, Bounds = new Rectangle(0, 388, 600, 2) };
            back.Text = "< Back";
            next.Text = "Next >";
            cancel.Text = "Cancel";
            back.SetBounds(300, 400, 88, 28);
            next.SetBounds(392, 400, 88, 28);
            cancel.SetBounds(492, 400, 88, 28);
            back.Click += delegate { GoBack(); };
            next.Click += delegate { GoNext(); };
            cancel.Click += delegate { Close(); };
            Controls.AddRange(new Control[] { title, lead, body, line, back, next, cancel });

            pages[CardStep.Build] = BuildPage();
            pages[CardStep.Zip] = ZipPage(downloads);
            pages[CardStep.Card] = CardPage();
            pages[CardStep.Password] = PasswordPage();
            pages[CardStep.Write] = WritePage();
            pages[CardStep.Done] = DonePage();
            foreach (Control p in pages.Values) { p.Dock = DockStyle.Fill; p.Visible = false; body.Controls.Add(p); }

            rescan.Interval = 3000;
            rescan.Tick += delegate { if (step == CardStep.Card && !writing) RefreshCards(); };
            GoTo(CardStep.Build);
        }

        static Label Para(string text, int top, int height)
        {
            return new Label { Text = text, Bounds = new Rectangle(0, top, 560, height) };
        }

        // A label beside a field: only as wide as the gap before it, so it
        // never covers the field.
        static Label FieldLabel(string text, int top)
        {
            return new Label { Text = text, Bounds = new Rectangle(0, top, 132, 20) };
        }

        static LinkLabel Link(string text, int top, Action click)
        {
            var l = new LinkLabel { Text = text, AutoSize = true, Location = new Point(0, top) };
            l.LinkClicked += delegate { click(); };
            return l;
        }

        Control BuildPage()
        {
            var p = new Panel();
            p.Controls.Add(Para("1.  Flash a microSD card with Raspberry Pi OS Lite (64-bit), using Raspberry Pi Imager. When Imager offers to apply OS customisation settings, choose No: Domovoi sets the card up itself.", 0, 48));
            p.Controls.Add(Para("2.  Build the card files in the dashboard: Settings > Satellites > prepare satellite media. Pick the board and the microphone, click Prepare, and download the files when the build is done.", 56, 48));
            p.Controls.Add(Para("3.  The dashboard shows the card's setup network password once. Keep it on screen: this helper asks for it.", 112, 34));
            var open = new Button { Text = "Open Settings > Satellites", AutoSize = true, Location = new Point(0, 160) };
            open.Click += delegate { openUrl(webUrl + "/#settings/satellites/media"); };
            p.Controls.Add(open);
            p.Controls.Add(Para("Already have the files and the password? Click Next.", 206, 24));
            return p;
        }

        Control ZipPage(string downloads)
        {
            var p = new Panel();
            p.Controls.Add(Para("The zip the dashboard built (domovoi-satellite-overlay.zip). The newest one in your Downloads folder is picked for you.", 0, 36));
            zipPath.SetBounds(0, 44, 452, 24);
            zipPath.ReadOnly = true;
            var browse = new Button { Text = "Browse...", Bounds = new Rectangle(460, 43, 100, 26) };
            browse.Click += delegate
            {
                using (var d = new OpenFileDialog())
                {
                    d.Title = "Choose the card files from the dashboard";
                    d.Filter = "Domovoi card files (*.zip)|*.zip";
                    if (Directory.Exists(downloads)) d.InitialDirectory = downloads;
                    if (d.ShowDialog(this) == DialogResult.OK) SetZip(d.FileName);
                }
            };
            zipResult.SetBounds(0, 80, 560, 120);
            p.Controls.AddRange(new Control[] { zipPath, browse, zipResult });
            string newest = CardZip.Newest(downloads);
            if (newest != null) SetZip(newest);
            else Note(zipResult, "No card files in " + (downloads ?? "your Downloads folder") + " yet. Build them in the dashboard (step 1), or Browse to where you saved them.", WarnColor);
            return p;
        }

        Control CardPage()
        {
            var p = new Panel();
            cardList.SetBounds(0, 0, 560, 150);
            cardList.View = View.Details;
            cardList.FullRowSelect = true;
            cardList.MultiSelect = false;
            cardList.HideSelection = false;
            cardList.HeaderStyle = ColumnHeaderStyle.Nonclickable;
            cardList.Columns.Add("Drive", 160);
            cardList.Columns.Add("Size", 80);
            cardList.Columns.Add("Free", 80);
            cardList.Columns.Add("", 230);
            cardList.SelectedIndexChanged += delegate { CardChosen(); };
            var refresh = new Button { Text = "Refresh", Bounds = new Rectangle(460, 158, 100, 26) };
            refresh.Click += delegate { RefreshCards(); };
            cardNote.SetBounds(0, 160, 450, 100);
            p.Controls.AddRange(new Control[] { cardList, refresh, cardNote });
            return p;
        }

        Control PasswordPage()
        {
            var p = new Panel();
            p.Controls.Add(FieldLabel("Setup network", 4));
            ssidBox.SetBounds(140, 0, 260, 24);
            ssidBox.ReadOnly = true;
            p.Controls.Add(ssidBox);
            p.Controls.Add(FieldLabel("Password", 40));
            pskBox.SetBounds(140, 36, 260, 24);
            pskBox.Font = new Font(FontFamily.GenericMonospace, 11f);
            pskBox.MaxLength = 40;
            pskBox.TextChanged += delegate { PasswordChanged(); };
            p.Controls.Add(pskBox);
            pskNote.SetBounds(140, 68, 420, 50);
            p.Controls.Add(pskNote);
            p.Controls.Add(Para("Where to find it: the dashboard showed it when it built these files (Settings > Satellites > prepare satellite media > show setup details). It's ten characters: lower-case letters and the digits 2 to 9.", 130, 54));
            return p;
        }

        Control WritePage()
        {
            var p = new Panel();
            writeLead.SetBounds(0, 0, 560, 50);
            writeButton.Text = "Write to the card";
            writeButton.SetBounds(0, 54, 150, 30);
            writeButton.Click += delegate { StartWrite(); };
            ejectButton.Text = "Eject the card";
            ejectButton.SetBounds(160, 54, 150, 30);
            ejectButton.Enabled = false;
            ejectButton.Click += delegate { EjectCard(); };
            writeBar.SetBounds(0, 94, 560, 18);
            writeStatus.SetBounds(0, 118, 560, 40);
            writeLog.SetBounds(0, 162, 560, 104);
            writeLog.IntegralHeight = false;
            p.Controls.AddRange(new Control[] { writeLead, writeButton, ejectButton, writeBar, writeStatus, writeLog });
            return p;
        }

        Control DonePage()
        {
            var p = new Panel();
            doneText.SetBounds(0, 0, 560, 100);
            p.Controls.Add(doneText);
            p.Controls.Add(Link("Open Settings > Satellites", 110, delegate { openUrl(webUrl + "/#settings/satellites"); }));
            var another = new Button { Text = "Prepare another card", AutoSize = true, Location = new Point(0, 146) };
            another.Click += delegate { written = false; chosen = null; GoTo(CardStep.Card); };
            p.Controls.Add(another);
            return p;
        }

        static void Note(Label l, string text, Color color)
        {
            l.Text = text;
            l.ForeColor = color;
        }

        // -- steps ------------------------------------------------------

        static readonly Dictionary<CardStep, string[]> Words = new Dictionary<CardStep, string[]>
        {
            { CardStep.Build, new[] { "Prepare a satellite card", "Domovoi runs inside Windows' Linux system here, which can't see SD cards, so this helper puts the card files on the card for it." } },
            { CardStep.Zip, new[] { "The card files", "Pick the zip you downloaded from the dashboard." } },
            { CardStep.Card, new[] { "The card", "Put the freshly flashed card in this PC. Only removable drives that look like a new Raspberry Pi card are listed: never a hard disk." } },
            { CardStep.Password, new[] { "The setup network password", "This card raises its own Wi-Fi so you can set it up from your phone. Type that network's password as the dashboard showed it." } },
            { CardStep.Write, new[] { "Write the card", "" } },
            { CardStep.Done, new[] { "The card is ready", "" } },
        };

        public void GoTo(CardStep s)
        {
            step = s;
            foreach (KeyValuePair<CardStep, Control> kv in pages) kv.Value.Visible = kv.Key == s;
            title.Text = Words[s][0];
            lead.Text = Words[s][1];
            rescan.Enabled = s == CardStep.Card;
            if (s == CardStep.Card) RefreshCards();
            if (s == CardStep.Password)
            {
                ssidBox.Text = package.ApSsid;
                PasswordChanged();
                pskBox.Focus();
            }
            if (s == CardStep.Write) PrepareWritePage();
            if (s == CardStep.Done)
            {
                doneText.Text = package.IsPortal
                    ? "Put the card in the Pi and switch it on. After a few minutes it raises its own Wi-Fi, " + package.ApSsid + ": join it from your phone, with the password you typed here, and follow the page that opens. Then approve the satellite in Settings > Satellites."
                    : "Put the card in the Pi and switch it on, then plug it into the Domovoi server's USB port and adopt it in Settings > Satellites.";
            }
            UpdateButtons();
        }

        void UpdateButtons()
        {
            back.Enabled = step != CardStep.Build && step != CardStep.Done && !writing;
            cancel.Text = step == CardStep.Done ? "Close" : "Cancel";
            cancel.Enabled = !writing;
            next.Visible = step != CardStep.Done;
            switch (step)
            {
                case CardStep.Zip: next.Enabled = package != null; break;
                case CardStep.Card: next.Enabled = chosen != null; break;
                case CardStep.Password: next.Enabled = SetupPassword.Problem(pskBox.Text) == null; break;
                case CardStep.Write: next.Enabled = written && !writing; break;
                default: next.Enabled = true; break;
            }
            AcceptButton = step == CardStep.Write && !written ? writeButton : (next.Visible && next.Enabled ? next : null);
        }

        void GoNext()
        {
            switch (step)
            {
                case CardStep.Build: GoTo(CardStep.Zip); break;
                case CardStep.Zip: if (package != null) GoTo(CardStep.Card); break;
                case CardStep.Card:
                    if (chosen == null) return;
                    string why = CardChecks.Problem(drives, chosen.Drive.Root, package);
                    if (why != null) { MessageBox.Show(this, why, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
                    if (chosen.Prepared && !Program.Snapshotting
                        && MessageBox.Show(this, "This card already has Domovoi's files on it. Write this zip's files over them?\n\nThe card's other files stay as they are.", Text,
                            MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) != DialogResult.Yes)
                        return;
                    GoTo(package.IsPortal ? CardStep.Password : CardStep.Write);
                    break;
                case CardStep.Password: if (SetupPassword.Problem(pskBox.Text) == null) GoTo(CardStep.Write); break;
                case CardStep.Write: if (written) GoTo(CardStep.Done); break;
            }
        }

        void GoBack()
        {
            switch (step)
            {
                case CardStep.Zip: GoTo(CardStep.Build); break;
                case CardStep.Card: GoTo(CardStep.Zip); break;
                case CardStep.Password: GoTo(CardStep.Card); break;
                case CardStep.Write: GoTo(package.IsPortal ? CardStep.Password : CardStep.Card); break;
            }
        }

        public void SetZip(string path)
        {
            zipPath.Text = path;
            package = null;
            try
            {
                Cursor = Cursors.WaitCursor;
                CardPackage p = CardZip.Open(path);
                string where = "\n" + Path.GetFileName(path) + ", saved " + File.GetLastWriteTime(path).ToString("d MMM yyyy, HH:mm") + ".";
                switch (CardZip.FromThisServer(p.ServerFingerprint, serverFingerprint()))
                {
                    case CardZip.Origin.OtherServer:
                        Note(zipResult, "These card files were built by a different Domovoi server, not the one on this PC, so a satellite made from them would never pair with it. Build them in this PC's dashboard (step 1)." + where, ErrorColor);
                        break;
                    case CardZip.Origin.Unknown:
                        package = p;
                        Note(zipResult, package.Summary() + where + "\nDomovoi on this PC isn't answering, so the helper can't check these files came from it. Use them only if you downloaded them from its dashboard.", WarnColor);
                        break;
                    default:
                        package = p;
                        Note(zipResult, package.Summary() + " Built by the Domovoi on this PC." + where, OkColor);
                        break;
                }
            }
            catch (CardZipException e) { Note(zipResult, e.Message, ErrorColor); }
            catch (Exception e) { Note(zipResult, "That zip couldn't be read: " + e.Message, ErrorColor); }
            finally { Cursor = Cursors.Default; }
            chosen = null;
            UpdateButtons();
        }

        public void RefreshCards()
        {
            if (package == null) return;
            string keep = chosen != null ? chosen.Drive.Root : null;
            int others;
            try { targets = CardDrives.Find(drives, package.BootMarker, out others); }
            catch (IOException) { targets = new List<CardTarget>(); others = 0; }
            cardList.BeginUpdate();
            cardList.Items.Clear();
            foreach (CardTarget t in targets)
            {
                var item = new ListViewItem(new[] { t.Title(), Bytes(t.Drive.Total), Bytes(t.Drive.Free), t.Prepared ? "already has Domovoi's files" : "freshly flashed" });
                if (t.Prepared) item.ForeColor = WarnColor;
                item.Tag = t;
                cardList.Items.Add(item);
                if (t.Drive.Root == keep) item.Selected = true;
            }
            cardList.EndUpdate();
            if (cardList.SelectedItems.Count == 0 && cardList.Items.Count == 1) cardList.Items[0].Selected = true;
            CardChosen();
            string text;
            if (targets.Count == 0)
                text = "No card found yet. Put the card in (a card reader or an SD slot); it shows up here by itself.";
            else
                text = targets.Count == 1 ? "One card found." : Inv.S(targets.Count) + " cards found: pick the one to write.";
            if (others > 0)
                text += "\n" + (others == 1 ? "One other removable drive is" : Inv.S(others) + " other removable drives are") + " left out: not a freshly flashed Raspberry Pi card.";
            Note(cardNote, text, targets.Count == 0 ? WarnColor : SystemColors.ControlText);
        }

        static string Bytes(long bytes)
        {
            if (bytes <= 0) return "";
            if (bytes >= 1024L * 1024 * 1024) return (bytes / (1024.0 * 1024 * 1024)).ToString("0.0") + " GB";
            return CardZip.Mib(bytes);
        }

        void CardChosen()
        {
            chosen = cardList.SelectedItems.Count == 1 ? (CardTarget)cardList.SelectedItems[0].Tag : null;
            UpdateButtons();
        }

        public void SelectCard(int index)
        {
            if (index < cardList.Items.Count) cardList.Items[index].Selected = true;
            CardChosen();
        }

        public void SetPassword(string text) { pskBox.Text = text; }

        void PasswordChanged()
        {
            string why = pskBox.Text.Length == 0 ? null : SetupPassword.Problem(pskBox.Text);
            if (why != null) Note(pskNote, why, ErrorColor);
            else if (pskBox.Text.Length > 0) Note(pskNote, "That looks right.", OkColor);
            else Note(pskNote, "", SystemColors.ControlText);
            UpdateButtons();
        }

        void PrepareWritePage()
        {
            title.Text = "Write the card";
            lead.Text = "Domovoi's files go onto " + chosen.Title() + ". Nothing is formatted or deleted: config.txt and cmdline.txt get Domovoi's lines added, and a domovoi folder is added.";
            writeLead.Text = CardZip.Mib(package.CopyBytes) + " to copy" + (chosen.Drive.Free > 0 ? ", " + Bytes(chosen.Drive.Free) + " free on the card." : ".")
                + " Don't take the card out until this says it's done.";
            if (!written)
            {
                writeBar.Value = 0;
                writeStatus.Text = "";
                writeStatus.ForeColor = SystemColors.ControlText;
                writeLog.Items.Clear();
                writeButton.Enabled = true;
                ejectButton.Enabled = false;
            }
        }

        public void StartWrite()
        {
            if (writing || written) return;
            writing = true;
            writeButton.Enabled = false;
            UpdateButtons();
            string root = chosen.Drive.Root;
            string psk = pskBox.Text;
            CardPackage pkg = package;
            var t = new Thread(delegate ()
            {
                CardWriteResult result = null;
                string error = null;
                try
                {
                    result = CardWriter.Write(pkg, root, psk, delegate (string msg, int pct)
                    {
                        BeginInvoke(new Action(delegate { writeBar.Value = Math.Max(0, Math.Min(100, pct)); writeStatus.Text = msg + "..."; writeLog.Items.Add(msg); writeLog.TopIndex = writeLog.Items.Count - 1; }));
                    });
                }
                catch (CardWriteException e) { error = e.Message; }
                catch (IOException e) { error = "Windows couldn't write to the card: " + e.Message + " Check the card is still in, then try again."; }
                catch (UnauthorizedAccessException) { error = "Windows won't let this program write to the card. If the card has a lock switch, slide it to unlocked, then try again."; }
                catch (InvalidDataException) { error = "The zip couldn't be read. Download the card files again."; }
                catch (Exception e) { error = "Writing the card stopped: " + e.Message; }
                BeginInvoke(new Action(delegate { WriteDone(result, error); }));
            });
            t.IsBackground = true;
            t.Start();
        }

        void WriteDone(CardWriteResult result, string error)
        {
            writing = false;
            if (error != null)
            {
                writeStatus.Text = error;
                writeStatus.ForeColor = ErrorColor;
                writeButton.Text = "Try again";
                writeButton.Enabled = true;
            }
            else
            {
                written = true;
                writeBar.Value = 100;
                writeStatus.Text = "Everything was written and checked (" + Inv.S(result.Checked.Count) + " checks). Eject the card, then click Next.";
                writeStatus.ForeColor = OkColor;
                foreach (string c in result.Checked) writeLog.Items.Add("checked " + c);
                writeLog.TopIndex = writeLog.Items.Count - 1;
                ejectButton.Enabled = !chosen.Drive.Fake;
            }
            UpdateButtons();
        }

        void EjectCard()
        {
            string problem;
            bool ok;
            try { ok = Eject.Run(chosen.Drive.Root, out problem); }
            catch (ArgumentException e) { ok = false; problem = e.Message; }
            if (ok)
            {
                writeStatus.Text = "The card is ejected: take it out and click Next.";
                ejectButton.Enabled = false;
            }
            else
                writeStatus.Text = "Windows didn't eject it (" + problem + "). Use \"Safely Remove Hardware\" in the taskbar, or close any window showing the card, then try again.";
        }

        protected override void WndProc(ref Message m)
        {
            // WM_DEVICECHANGE: a card went in or out.
            if (m.Msg == 0x0219 && step == CardStep.Card && !writing) BeginInvoke(new Action(RefreshCards));
            base.WndProc(ref m);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (writing) { e.Cancel = true; return; }
            rescan.Enabled = false;
            base.OnFormClosing(e);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) rescan.Dispose();
            base.Dispose(disposing);
        }
    }

    // A card the zip's edits can't make a Domovoi card of.
    public static class CardChecks
    {
        public static string Problem(IDriveSource drives, string root, CardPackage pkg)
        {
            string cmdline;
            try { cmdline = drives.ReadAllText(Path.Combine(root, "cmdline.txt")); }
            catch (IOException) { return "Windows can't read the card. Put it back in and click Refresh."; }
            catch (UnauthorizedAccessException) { return "Windows can't read the card. Put it back in and click Refresh."; }
            return CmdlineProblem(cmdline, pkg.CmdlineTokens);
        }

        // Another first-boot script already on the kernel line (Raspberry Pi
        // Imager's settings add one): Domovoi's edits keep it, the way the
        // server's do, so Domovoi's would never run.
        public static string CmdlineProblem(string cmdline, IList<string> tokens)
        {
            string after = BootText.EditCmdline(BootText.Read(System.Text.Encoding.UTF8.GetBytes(cmdline)), tokens);
            if (!BootText.PySplit(after).Contains(BootText.FirstrunToken))
                return "This card already runs another first-boot script (Raspberry Pi Imager's settings add one), so Domovoi's setup would never start. Flash it again, and when Imager offers to apply OS customisation settings, choose No.";
            return null;
        }
    }
}
