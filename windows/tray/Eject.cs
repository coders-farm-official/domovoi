// Eject: what Explorer's "Eject" does for a card, by the documented
// sequence (lock the volume, dismount it, allow removal, eject the media).
// A standard user may do this to removable media. If Windows says no (a
// file still open on the card), the card helper says to use "Safely
// remove" instead; nothing is written.

using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Domovoi.Windows.Tray
{
    public static class Eject
    {
        const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000;
        const uint SHARE_READ = 1, SHARE_WRITE = 2, OPEN_EXISTING = 3;
        const uint FSCTL_LOCK_VOLUME = 0x00090018, FSCTL_DISMOUNT_VOLUME = 0x00090020;
        const uint IOCTL_STORAGE_MEDIA_REMOVAL = 0x002D4804, IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool DeviceIoControl(SafeFileHandle h, uint code, byte[] inBuf, int inSize, IntPtr outBuf, int outSize, out int returned, IntPtr overlapped);

        // "E:\" -> "\\.\E:". Only a drive letter: never a path.
        public static string VolumePath(string root)
        {
            if (root == null || root.Length < 2 || root[1] != ':' || !char.IsLetter(root[0]) || (root.Length > 3) || (root.Length == 3 && root[2] != '\\'))
                throw new ArgumentException("not a drive root: " + root);
            return @"\\.\" + char.ToUpperInvariant(root[0]) + ":";
        }

        // True when Windows ejected it.
        public static bool Run(string root, out string problem)
        {
            problem = null;
            string vol = VolumePath(root);
            SafeFileHandle opened = CreateFileW(vol, GENERIC_READ | GENERIC_WRITE, SHARE_READ | SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
            if (opened.IsInvalid)
            {
                opened.Dispose();
                opened = CreateFileW(vol, GENERIC_READ, SHARE_READ | SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
            }
            using (SafeFileHandle h = opened)
            {
                if (h.IsInvalid) { problem = new Win32Exception(Marshal.GetLastWin32Error()).Message; return false; }
                int got;
                bool locked = false;
                for (int i = 0; i < 20 && !locked; i++)
                {
                    locked = DeviceIoControl(h, FSCTL_LOCK_VOLUME, null, 0, IntPtr.Zero, 0, out got, IntPtr.Zero);
                    if (!locked) Thread.Sleep(250);
                }
                if (!locked) { problem = "something still has a file open on the card"; return false; }
                if (!DeviceIoControl(h, FSCTL_DISMOUNT_VOLUME, null, 0, IntPtr.Zero, 0, out got, IntPtr.Zero))
                {
                    problem = new Win32Exception(Marshal.GetLastWin32Error()).Message;
                    return false;
                }
                DeviceIoControl(h, IOCTL_STORAGE_MEDIA_REMOVAL, new byte[] { 0 }, 1, IntPtr.Zero, 0, out got, IntPtr.Zero);
                if (!DeviceIoControl(h, IOCTL_STORAGE_EJECT_MEDIA, null, 0, IntPtr.Zero, 0, out got, IntPtr.Zero))
                {
                    // Dismounted and flushed: safe to pull out, even if the
                    // reader can't eject by itself.
                    return true;
                }
                return true;
            }
        }
    }
}
