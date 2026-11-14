// The notification-area icon: Domovoi's cat (installer\art\domovoi.ico,
// embedded) with a status dot in its corner. Green running, amber starting
// or updating, grey stopped, red a problem: the dashboard's status colours.
// A blue "!" in its place while a request for the Windows step waits for a
// click here (Reminder.cs).

using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;

namespace Domovoi.Windows.Tray
{
    public static class Icons
    {
        public const string Resource = "Domovoi.Tray.domovoi.ico";

        [DllImport("user32.dll")]
        static extern bool DestroyIcon(IntPtr h);

        public static Icon App(int size)
        {
            using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream(Resource))
            {
                if (s == null) return (Icon)SystemIcons.Application.Clone();
                return new Icon(s, size, size);
            }
        }

        public static Color DotColor(TrayState state)
        {
            switch (state)
            {
                case TrayState.Running: return Color.FromArgb(34, 160, 84);
                case TrayState.Starting: return Color.FromArgb(222, 150, 20);
                case TrayState.Problem: return Color.FromArgb(214, 52, 52);
                default: return Color.FromArgb(130, 130, 130);
            }
        }

        // The attention badge (a request for the Windows step waits for a
        // click here, Reminder.cs): blue, the dashboard's info colour.
        public static readonly Color AttentionColor = Color.FromArgb(37, 99, 235);

        public static Bitmap Picture(TrayState state, int size) { return Picture(state, size, false); }

        // attention: a larger blue badge with a white "!" in place of the
        // status dot.
        public static Bitmap Picture(TrayState state, int size, bool attention)
        {
            if (attention) return AttentionPicture(size);
            var bmp = new Bitmap(size, size, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
            using (Graphics g = Graphics.FromImage(bmp))
            using (Icon app = App(size))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.DrawIcon(app, new Rectangle(0, 0, size, size));
                float d = Math.Max(6f, size * 0.46f);
                float x = size - d, y = size - d;
                float ring = Math.Max(1f, size / 16f);
                using (var white = new SolidBrush(Color.White))
                    g.FillEllipse(white, x - ring, y - ring, d + ring, d + ring);
                using (var dot = new SolidBrush(DotColor(state)))
                    g.FillEllipse(dot, x, y, d - ring, d - ring);
                if (state == TrayState.Stopped)
                    using (var pen = new Pen(Color.White, Math.Max(1f, size / 12f)))
                        g.DrawLine(pen, x + d * 0.25f, y + d * 0.42f, x + d * 0.65f, y + d * 0.42f);
            }
            return bmp;
        }

        static Bitmap AttentionPicture(int size)
        {
            var bmp = new Bitmap(size, size, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
            using (Graphics g = Graphics.FromImage(bmp))
            using (Icon app = App(size))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.DrawIcon(app, new Rectangle(0, 0, size, size));
                float d = Math.Max(8f, size * 0.62f);
                float x = size - d, y = size - d;
                float ring = Math.Max(1f, size / 16f);
                using (var white = new SolidBrush(Color.White))
                    g.FillEllipse(white, x - ring, y - ring, d + ring, d + ring);
                using (var dot = new SolidBrush(AttentionColor))
                    g.FillEllipse(dot, x, y, d - ring, d - ring);
                // The "!": a bar and a dot, drawn (no font at 16 px).
                float inner = d - ring;
                float w = Math.Max(1.5f, inner * 0.18f);
                float cx = x + inner / 2f;
                using (var mark = new SolidBrush(Color.White))
                {
                    g.FillRectangle(mark, cx - w / 2f, y + inner * 0.18f, w, inner * 0.44f);
                    g.FillRectangle(mark, cx - w / 2f, y + inner * 0.70f, w, w);
                }
            }
            return bmp;
        }

        // An Icon the caller owns; Release it when it is replaced.
        public static Icon Make(TrayState state, int size) { return Make(state, size, false); }

        public static Icon Make(TrayState state, int size, bool attention)
        {
            using (Bitmap b = Picture(state, size, attention))
            {
                IntPtr h = b.GetHicon();
                using (Icon tmp = Icon.FromHandle(h))
                {
                    var copy = (Icon)tmp.Clone();
                    DestroyIcon(h);
                    return copy;
                }
            }
        }
    }
}
