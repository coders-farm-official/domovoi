// Which drives the card helper offers: removable drives only (never a
// fixed disk, never the drive Windows runs from) that look like a freshly
// flashed Raspberry Pi boot partition, the same test the server makes
// (overlay.looks_like_pi_boot: config.txt, cmdline.txt and the board's
// .dtb). The list comes from an IDriveSource, so the tests use a fake one;
// the real one is DriveInfo.

using System;
using System.Collections.Generic;
using System.IO;

namespace Domovoi.Windows.Tray
{
    public sealed class DriveFacts
    {
        public string Root;          // "E:\" (a folder for a developer's fake card)
        public string Label;
        public string Format;        // "FAT32"
        public bool Removable;
        public bool Ready;
        public long Total;
        public long Free;
        public bool Fake;            // --dev-fake-card: a folder, labelled so
    }

    public interface IDriveSource
    {
        List<DriveFacts> List();
        bool FileExists(string path);
        string ReadAllText(string path);
    }

    public sealed class CardTarget
    {
        public DriveFacts Drive;
        public bool Prepared;        // Domovoi's marker is in its config.txt

        public string Title()
        {
            string label = string.IsNullOrEmpty(Drive.Label) ? "card" : Drive.Label;
            string root = Drive.Fake ? Drive.Root + " (test folder)" : Drive.Root.TrimEnd('\\');
            return root + "  " + label;
        }
    }

    public static class CardDrives
    {
        // C: and wherever Windows itself is.
        public static bool IsSystem(string root)
        {
            string sys = Path.GetPathRoot(Environment.GetFolderPath(Environment.SpecialFolder.Windows)) ?? @"C:\";
            return string.Equals(Norm(root), Norm(sys), StringComparison.OrdinalIgnoreCase)
                || string.Equals(Norm(root), @"C:\", StringComparison.OrdinalIgnoreCase);
        }

        static string Norm(string root)
        {
            if (string.IsNullOrEmpty(root)) return "";
            return root.Length == 2 && root[1] == ':' ? root + "\\" : root;
        }

        // A Pi boot partition is FAT (FAT32, or FAT16 on a small card), never exFAT or NTFS.
        public static bool IsFat(string format)
        {
            return format != null && (format.Equals("FAT32", StringComparison.OrdinalIgnoreCase) || format.Equals("FAT", StringComparison.OrdinalIgnoreCase)
                || format.Equals("FAT16", StringComparison.OrdinalIgnoreCase) || format.Equals("vfat", StringComparison.OrdinalIgnoreCase));
        }

        public static bool LooksLikeBoot(IDriveSource src, string root, string marker)
        {
            return src.FileExists(Path.Combine(root, "config.txt")) && src.FileExists(Path.Combine(root, "cmdline.txt"))
                && (string.IsNullOrEmpty(marker) || src.FileExists(Path.Combine(root, marker)));
        }

        // The drives to offer, in drive-letter order, and how many other
        // removable drives were left out (for "not a Raspberry Pi card").
        public static List<CardTarget> Find(IDriveSource src, string marker, out int others)
        {
            var list = new List<CardTarget>();
            others = 0;
            foreach (DriveFacts d in src.List())
            {
                if (!d.Fake && (!d.Removable || IsSystem(d.Root))) continue;
                if (!d.Ready) { others++; continue; }
                if (!d.Fake && !IsFat(d.Format)) { others++; continue; }
                bool looks;
                try { looks = LooksLikeBoot(src, d.Root, marker); }
                catch (IOException) { looks = false; }
                catch (UnauthorizedAccessException) { looks = false; }
                if (!looks) { others++; continue; }
                bool prepared = false;
                try { prepared = src.ReadAllText(Path.Combine(d.Root, "config.txt")).Contains(BootText.Marker); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                list.Add(new CardTarget { Drive = d, Prepared = prepared });
            }
            list.Sort(delegate (CardTarget a, CardTarget b) { return string.Compare(a.Drive.Root, b.Drive.Root, StringComparison.OrdinalIgnoreCase); });
            return list;
        }
    }

    // The PC's drives, as Windows reports them. A drive that answers slowly
    // or not at all (an empty card reader) is skipped, not waited on.
    public sealed class WindowsDrives : IDriveSource
    {
        readonly List<string> fakeCards;
        readonly bool real;

        // real: list the PC's drives (false in a developer run, which sees
        // only its fake cards and never looks at a real drive).
        public WindowsDrives(List<string> fakeCards, bool real)
        {
            this.fakeCards = fakeCards ?? new List<string>();
            this.real = real;
        }

        public List<DriveFacts> List()
        {
            var list = new List<DriveFacts>();
            foreach (DriveInfo d in real ? DriveInfo.GetDrives() : new DriveInfo[0])
            {
                var f = new DriveFacts { Root = d.Name, Removable = d.DriveType == DriveType.Removable };
                if (!f.Removable) { list.Add(f); continue; }
                try
                {
                    f.Ready = d.IsReady;
                    if (f.Ready)
                    {
                        f.Label = d.VolumeLabel;
                        f.Format = d.DriveFormat;
                        f.Total = d.TotalSize;
                        f.Free = d.AvailableFreeSpace;
                    }
                }
                catch (IOException) { f.Ready = false; }
                catch (UnauthorizedAccessException) { f.Ready = false; }
                list.Add(f);
            }
            foreach (string dir in fakeCards)
            {
                var f = new DriveFacts { Root = dir, Label = "test card", Format = "FAT32", Removable = true, Ready = Directory.Exists(dir), Fake = true };
                if (f.Ready)
                {
                    long free, total;
                    if (FreeSpace.Get(dir, out free, out total)) { f.Free = free; f.Total = total; }
                }
                list.Add(f);
            }
            return list;
        }

        public bool FileExists(string path) { return File.Exists(path); }

        public string ReadAllText(string path) { return File.ReadAllText(path); }
    }

    public static class FreeSpace
    {
        [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
        [return: System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)]
        static extern bool GetDiskFreeSpaceExW(string dir, out ulong freeToCaller, out ulong total, out ulong totalFree);

        public static bool Get(string dir, out long free, out long total)
        {
            ulong a, t, f;
            free = 0; total = 0;
            if (!GetDiskFreeSpaceExW(dir.EndsWith("\\", StringComparison.Ordinal) ? dir : dir + "\\", out a, out t, out f)) return false;
            free = (long)a;
            total = (long)t;
            return true;
        }
    }
}
