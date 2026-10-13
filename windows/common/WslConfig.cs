// %UserProfile%\.wslconfig of the hidden account (PLAN 4.6). It applies to
// that account's own WSL VM only; the household's own WSL (and Docker
// Desktop's) read their own users' files. Keys and sections follow PLAN
// 4.6, which checked them against MS Learn wsl-config (updated
// 2026-09-16): https://learn.microsoft.com/windows/wsl/wsl-config
//
// One deliberate difference from PLAN 4.6: vmIdleTimeout is left at WSL's
// default (60 s) instead of -1. With instanceIdleTimeout=-1 and
// DomovoiHost's holder, the distro never stops while Domovoi runs, so the
// VM is never idle then. With -1, the VM would also stay up after the
// distro is unregistered, keeping swap.vhdx open, and uninstall could not
// finish without `wsl --shutdown` or a restart.

using System;
using System.Text;

namespace Domovoi.Windows
{
    public sealed class WslConfigOptions
    {
        public int MemoryGb;
        public int SwapGb = 4;
        public string SwapFile;
    }

    public static class WslConfig
    {
        // The VM's memory cap, from the PC's installed RAM. PLAN 4.6 gives
        // 10 GB for the 16 GB floor (S5 decides 10 vs 12). Above the floor
        // PLAN names no value; this build takes RAM minus 8 GB, at most 64
        // GB (WSL's own default is half the RAM), at least 16 GB. provision.sh
        // picks the standard model tier when the VM sees at least 22,528 MiB
        // (PLAN 4.9), so that happens from about 32 GB of RAM up. [S5]
        public static int MemoryCapGb(long totalRamBytes)
        {
            double gb = totalRamBytes / (1024.0 * 1024.0 * 1024.0);
            // Installed RAM reads a little under its label (firmware takes
            // some): a "16 GB" PC shows 15.7. 23.5 counts as the 24 GB step.
            int labelled = (int)Math.Round(gb);
            if (labelled < 24) return 10;
            int cap = labelled - 8;
            if (cap > 64) cap = 64;
            if (cap < 16) cap = 16;
            return cap;
        }

        // Where the file goes: the account's own profile as Windows made it,
        // which DomovoiHost reads from its own environment at run time. Not
        // always C:\Users\domovoi-svc: with an old folder of that name still
        // there, Windows makes C:\Users\domovoi-svc.<PC name>.
        public static string FileIn(string userProfile)
        {
            if (string.IsNullOrEmpty(userProfile)) throw new ArgumentException("no profile folder");
            return System.IO.Path.Combine(userProfile, ".wslconfig");
        }

        static string Escape(string windowsPath)
        {
            // The file's values take Windows paths with escaped backslashes (MS
            // Learn: "swapFile ... C:\\Temp\\swap.vhdx").
            return windowsPath.Replace("\\", "\\\\");
        }

        public static string Render(WslConfigOptions o)
        {
            if (o == null) throw new ArgumentNullException("o");
            if (o.MemoryGb < 4 || o.MemoryGb > 1024) throw new ArgumentException("memory cap out of range");
            if (o.SwapGb < 0 || o.SwapGb > 64) throw new ArgumentException("swap out of range");
            if (string.IsNullOrEmpty(o.SwapFile) || o.SwapFile.IndexOfAny(new[] { '\r', '\n', '"' }) >= 0)
                throw new ArgumentException("swap file path");
            var sb = new StringBuilder();
            sb.Append("# Written by Domovoi (DomovoiHost) for this account's WSL VM only.\n");
            sb.Append("# Domovoi's uninstaller deletes this account and this file with it.\n");
            sb.Append("[general]\n");
            sb.Append("# The distro would otherwise stop ~15 s after its last client.\n");
            sb.Append("instanceIdleTimeout=-1\n");
            sb.Append("\n");
            sb.Append("[wsl2]\n");
            sb.Append("# Satellites and phones reach Domovoi on this PC's own LAN address.\n");
            sb.Append("networkingMode=mirrored\n");
            sb.Append("# WSLg crash-loops in session 0 (microsoft/WSL#41110).\n");
            sb.Append("guiApplications=false\n");
            sb.Append("memory=" + Inv.S(o.MemoryGb) + "GB\n");
            sb.Append("swap=" + Inv.S(o.SwapGb) + "GB\n");
            sb.Append("swapFile=" + Escape(o.SwapFile) + "\n");
            sb.Append("# The default; Domovoi's Hyper-V firewall rules depend on it.\n");
            sb.Append("firewall=true\n");
            sb.Append("\n");
            sb.Append("[experimental]\n");
            sb.Append("# This PC's own browser reaches Domovoi on the LAN address too.\n");
            sb.Append("hostAddressLoopback=true\n");
            sb.Append("sparseVhd=true\n");
            sb.Append("autoMemoryReclaim=dropCache\n");
            return sb.ToString();
        }
    }
}
