// kit-check: Setup's check of an offline kit before it changes anything
// (common\OfflineKit.cs has the decision). Read-only. Prints kit_root=,
// kit_version=, kit_tag=, kit_commit=, kit_ai=yes|no, kit_files=, kit_gb=,
// need_gb= and, for each reason the kit can't be used, a fatal= line in the
// household's words. Exit 0 either way; Setup reads the fatal lines.

using System;
using System.IO;

namespace Domovoi.Windows.Helper
{
    static class KitCheck
    {
        public static void Run(Output o, Args a)
        {
            var want = new KitExpectation
            {
                Commit = a.Req("--commit"),
                Version = a.Req("--version"),
                Key = ReleaseKey.Pinned(),
                UbuntuName = a.Req("--ubuntu-name"),
                UbuntuSha256 = a.Req("--ubuntu-sha256"),
                WslMsiName = a.Req("--wsl-msi-name"),
                WslMsiSha256 = a.Req("--wsl-msi-sha256"),
                NeedWsl = a.Flag("--need-wsl"),
                ProvisionSha256 = a.Opt("--provision-sha256"),
                MinFreeGb = Preflight.MinFreeGb,
            };
            // Domovoi's Linux system goes in C:\ProgramData\Domovoi.
            string drive = Path.GetPathRoot(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData));
            want.FreeDrive = drive;
            try { want.FreeBytes = new DriveInfo(drive).AvailableFreeSpace; }
            catch (IOException) { want.FreeBytes = -1; }
            catch (ArgumentException) { want.FreeBytes = -1; }

            KitReport r = OfflineKit.Check(a.Req("--kit"), want);
            o.LogOnly("kit-check " + (r.Root ?? "") + ": " + (r.Ok ? "ok" : string.Join(" | ", r.Problems.ToArray())));
            o.Kv("kit_root", r.Root ?? "");
            o.Kv("kit_version", r.Version ?? "");
            o.Kv("kit_tag", r.Tag ?? "");
            o.Kv("kit_commit", r.Commit ?? "");
            o.Kv("kit_ai", r.Ai ? "yes" : "no");
            o.Kv("kit_files", Inv.S(r.Files));
            o.Kv("kit_gb", Inv.S((r.Bytes + (1L << 29)) >> 30));
            o.Kv("need_gb", Inv.S(r.NeedGb));
            foreach (string p in r.Problems) o.Kv("fatal", p);
        }
    }
}
