// "Show a code for your phone": the dashboard's address on this network as
// a QR code a phone's camera opens.

using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace Domovoi.Windows.Tray
{
    public sealed class QrForm : Form
    {
        readonly QrCode code;
        readonly PictureBox picture;

        public QrForm(string url)
        {
            code = QrCode.EncodeText(url, QrEcc.Medium, -1, true);
            Text = "Domovoi on your phone";
            Icon = Icons.App(32);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            AutoScaleDimensions = new SizeF(96f, 96f);
            BackColor = Color.White;
            Font = SystemFonts.MessageBoxFont;

            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, Padding = new Padding(18), AutoSize = true };
            var lead = new Label
            {
                Text = "Point your phone's camera at the code to open Domovoi. The phone must be on the same Wi-Fi as this PC.",
                AutoSize = true,
                MaximumSize = new Size(340, 0),
                Margin = new Padding(0, 0, 0, 12),
            };
            int scale = Math.Max(4, 300 / (code.Size + 8));
            picture = new PictureBox
            {
                Image = Render(code, scale),
                SizeMode = PictureBoxSizeMode.AutoSize,
                Anchor = AnchorStyles.None,
                Margin = new Padding(0, 0, 0, 12),
            };
            var address = new TextBox
            {
                Text = url,
                ReadOnly = true,
                BorderStyle = BorderStyle.None,
                BackColor = Color.White,
                Font = new Font(FontFamily.GenericMonospace, 11f),
                Width = 340,
                TextAlign = HorizontalAlignment.Center,
                Margin = new Padding(0, 0, 0, 12),
            };
            var buttons = new FlowLayoutPanel { FlowDirection = FlowDirection.RightToLeft, AutoSize = true, Anchor = AnchorStyles.Right, Margin = new Padding(0) };
            var close = new Button { Text = "Close", AutoSize = true, DialogResult = DialogResult.OK };
            var copy = new Button { Text = "Copy address", AutoSize = true };
            copy.Click += delegate { try { Clipboard.SetText(url); copy.Text = "Copied"; } catch (ExternalException) { } };
            buttons.Controls.Add(close);
            buttons.Controls.Add(copy);
            layout.Controls.Add(lead);
            layout.Controls.Add(picture);
            layout.Controls.Add(address);
            layout.Controls.Add(buttons);
            Controls.Add(layout);
            AcceptButton = close;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
        }

        // Black modules on white, with the four-module quiet zone the
        // standard asks for.
        public static Bitmap Render(QrCode code, int scale)
        {
            int n = (code.Size + 8) * scale;
            var bmp = new Bitmap(n, n);
            using (Graphics g = Graphics.FromImage(bmp))
            {
                g.Clear(Color.White);
                for (int y = 0; y < code.Size; y++)
                    for (int x = 0; x < code.Size; x++)
                        if (code.Dark(x, y)) g.FillRectangle(Brushes.Black, (x + 4) * scale, (y + 4) * scale, scale, scale);
            }
            return bmp;
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && picture.Image != null) picture.Image.Dispose();
            base.Dispose(disposing);
        }
    }
}
