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
        public long Total;           // the volume: the card's boot partition
        public long Free;
        public long CardBytes;       // the whole card (the disk behind the volume); 0 unknown
        public bool Fake;            // --dev-fake-card: a folder, labelled so
    }

    public interface IDriveSource
    {
        List<DriveFacts> List();
        bool FileExists(string path);
        string ReadAllText(string path);
        // The size of the disk (the card) the volume is on, or null when
        // Windows won't say. Asked only for the drives that are offered.
        long? DiskBytes(DriveFacts drive);
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
                long? card = null;
                try { card = src.DiskBytes(d); }
                catch (Exception) { card = null; }   // the card's size is a nicety: never a reason to hide it
                d.CardBytes = card.HasValue && card.Value > 0 ? card.Value : 0;
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
                    // Shown as a flashed card's boot partition would be (the
                    // write's own free-space check reads the folder's drive).
                    if (FreeSpace.Get(dir, out free, out total)) { f.Total = 512L * 1024 * 1024; f.Free = Math.Min(free, 430L * 1024 * 1024); }
                }
                list.Add(f);
            }
            return list;
        }

        public bool FileExists(string path) { return File.Exists(path); }

        public string ReadAllText(string path) { return File.ReadAllText(path); }

        // A test folder: a made-up 32 GB card.
        public const long FakeCardBytes = 31914983424L;

        readonly Dictionary<string, long?> diskCache = new Dictionary<string, long?>(StringComparer.OrdinalIgnoreCase);

        public long? DiskBytes(DriveFacts d)
        {
            if (d.Fake) return FakeCardBytes;
            // Remembered per drive and partition size: the list refreshes
            // every few seconds and WMI is slow-ish.
            string key = d.Root + "|" + Inv.S(d.Total);
            long? v;
            if (diskCache.TryGetValue(key, out v)) return v;
            v = Wmi.DiskBytes(d.Root);
            diskCache[key] = v;
            return v;
        }
    }

    // The disk behind a drive letter, from WMI (Win32_LogicalDisk ->
    // Win32_DiskPartition -> Win32_DiskDrive.Size). No administrator needed;
    // null when it can't be read.
    public static class Wmi
    {
        public static long? DiskBytes(string root)
        {
            if (root == null || root.Length < 2 || root[1] != ':' || !char.IsLetter(root[0])) return null;
            string letter = char.ToUpperInvariant(root[0]) + ":";
            try
            {
                var opts = new System.Management.EnumerationOptions { Timeout = TimeSpan.FromSeconds(5), ReturnImmediately = false };
                var scope = new System.Management.ManagementScope(@"\\.\root\cimv2");
                using (var parts = new System.Management.ManagementObjectSearcher(scope, new System.Management.ObjectQuery(
                    "ASSOCIATORS OF {Win32_LogicalDisk.DeviceID='" + letter + "'} WHERE AssocClass=Win32_LogicalDiskToPartition"), opts))
                    foreach (System.Management.ManagementObject part in parts.Get())
                        using (part)
                        using (var disks = new System.Management.ManagementObjectSearcher(scope, new System.Management.ObjectQuery(
                            "ASSOCIATORS OF {Win32_DiskPartition.DeviceID='" + Convert.ToString(part["DeviceID"], System.Globalization.CultureInfo.InvariantCulture).Replace("'", "")
                            + "'} WHERE AssocClass=Win32_DiskDriveToDiskPartition"), opts))
                            foreach (System.Management.ManagementObject disk in disks.Get())
                                using (disk)
                                {
                                    object size = disk["Size"];
                                    if (size != null) return Convert.ToInt64(size, System.Globalization.CultureInfo.InvariantCulture);
                                }
            }
            catch (System.Management.ManagementException) { }
            catch (System.Runtime.InteropServices.COMException) { }
            catch (UnauthorizedAccessException) { }
            catch (InvalidCastException) { }
            catch (FormatException) { }
            catch (OverflowException) { }
            return null;
        }
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
