// "Sign in as admin to fetch it": the dashboard's admin password, asked for
// one fetch of a card's setup network password (CardKeys). The password
// lives in this box and the one call that uses it, nowhere else: never
// saved, never logged.

using System.Drawing;
using System.Windows.Forms;

namespace Domovoi.Windows.Tray
{
    public static class AdminPrompt
    {
        // The password, or null when the person cancels.
        public static string Ask(IWin32Window owner)
        {
            using (var f = new Form())
            using (var lead = new Label())
            using (var box = new TextBox())
            using (var ok = new Button())
            using (var cancel = new Button())
            {
                f.Text = "Sign in as admin";
                f.Icon = Icons.App(32);
                f.Font = SystemFonts.MessageBoxFont;
                f.AutoScaleMode = AutoScaleMode.Dpi;
                f.AutoScaleDimensions = new SizeF(96f, 96f);
                f.FormBorderStyle = FormBorderStyle.FixedDialog;
                f.MaximizeBox = false;
                f.MinimizeBox = false;
                f.ShowInTaskbar = false;
                f.StartPosition = FormStartPosition.CenterParent;
                f.ClientSize = new Size(380, 138);
                lead.Text = "The admin password you use to sign in to Domovoi's dashboard. It's used for this one fetch and not kept.";
                lead.SetBounds(14, 12, 352, 40);
                box.SetBounds(14, 58, 352, 24);
                box.UseSystemPasswordChar = true;
                box.MaxLength = 256;
                ok.Text = "Sign in";
                ok.SetBounds(196, 98, 82, 28);
                ok.DialogResult = DialogResult.OK;
                cancel.Text = "Cancel";
                cancel.SetBounds(284, 98, 82, 28);
                cancel.DialogResult = DialogResult.Cancel;
                f.Controls.AddRange(new Control[] { lead, box, ok, cancel });
                f.AcceptButton = ok;
                f.CancelButton = cancel;
                if (f.ShowDialog(owner) != DialogResult.OK || box.Text.Length == 0) return null;
                return box.Text;
            }
        }
    }
}
