// Keeping the house identity across a reinstall (S3-4): the kept folder's
// name, the backup's shape check, the --restore-identity argument, the
// job's new fields and the keep start parameter. Nothing here changes the PC.

using System;
using System.Collections.Generic;
using System.Text;
using Domovoi.Windows;

static partial class Tests
{
    const string GoodBackup = "{\"kind\": \"domovoi-server-identity-backup\", \"schema\": 1, \"algorithm\": \"ed25519\","
        + " \"private_key\": \"AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=\","
        + " \"public_key\": \"iojj3XQJ8ZX9UtstPLpdcspnCb8dlBIb83SIAbQPb1w=\","
        + " \"fingerprint\": \"SHA256:abc\"}";

    static void KeepIdentity()
    {
        Run("keep identity: the uninstaller's kept folders, and nothing else", delegate
        {
            True(KeptName.IsKept("Domovoi-kept-20261122-210501"), "the uninstaller's name");
            True(!KeptName.IsKept("Domovoi-kept-anything"), "not a time");
            True(!KeptName.IsKept("Domovoi"), "the install's own folder");
            True(!KeptName.IsKept("Domovoi-kept-20261122-210501\\..\\x"), "no tricks");
            Eq("2026-11-22 21:05", KeptName.When("Domovoi-kept-20261122-210501"), "when");
            Eq<string>(null, KeptName.When("Domovoi-kept-x"), "no time");
            True(Paths.KeptPrefix.EndsWith("Domovoi-kept-", StringComparison.Ordinal), "the prefix");
        });
        Run("keep identity: a backup's shape, never its contents in the message", delegate
        {
            Eq<string>(null, BackupShape.Problem(Encoding.UTF8.GetBytes(GoodBackup)), "a backup");
            Eq("SHA256:abc", BackupShape.Fingerprint(Encoding.UTF8.GetBytes(GoodBackup)), "fingerprint");
            string bare = GoodBackup.Replace("\"kind\": \"domovoi-server-identity-backup\", ", "");
            Eq<string>(null, BackupShape.Problem(Encoding.UTF8.GetBytes(bare)), "a plain key file");
            foreach (string bad in new[] {
                "", "not json", "[1]",
                GoodBackup.Replace("domovoi-server-identity-backup", "satellite-server-fingerprint"),
                GoodBackup.Replace("domovoi-server-identity-backup", "other"),
                GoodBackup.Replace("ed25519", "rsa"),
                GoodBackup.Replace("AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=", "short"),
                GoodBackup.Replace("SHA256:abc", "abc"),
            })
            {
                string why = BackupShape.Problem(Encoding.UTF8.GetBytes(bad));
                True(why != null, "refused: " + bad);
                True(why.IndexOf("AQEB", StringComparison.Ordinal) < 0, "no key in the message");
            }
            True(BackupShape.Problem(new byte[17 * 1024]) != null, "too large");
        });
        Run("keep identity: --restore-identity only at its in-distro path, only to a provision.sh that takes it", delegate
        {
            var o = Opts();
            o.RestoreIdentity = ProvisionArgs.InDistroRestoreIdentity;
            o.ScriptHasRestoreIdentity = true;
            var a = ProvisionArgs.Build(o);
            Eq(ProvisionArgs.InDistroRestoreIdentity, a[a.IndexOf("--restore-identity") + 1], "the path");
            o.ScriptHasRestoreIdentity = false;
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "an older provision.sh");
            o.ScriptHasRestoreIdentity = true;
            o.RestoreIdentity = "/tmp/x.json";
            Throws<ArgumentException>(delegate { ProvisionArgs.Build(o); }, "another path");
            True(ProvisionArgs.Build(Opts()).IndexOf("--restore-identity") < 0, "none by default");
            True(ProvisionScript.SupportsRestoreIdentity("usage() {\n  --restore-identity F  make the house\n}"), "the usage line");
            True(!ProvisionScript.SupportsRestoreIdentity("  --restore-identity"), "not a usage line");
        });
        Run("keep identity: the job carries the backup and the in-place import", delegate
        {
            var j = new HostJob();
            j.Image = @"C:\x\image.wsl"; j.ImageSha256 = new string('a', 64);
            j.Provision = @"C:\x\provision.sh"; j.ProvisionSha256 = new string('b', 64);
            j.WslConfig = "[wsl2]";
            j.RestoreIdentity = Paths.RestoreIdentityFile; j.RestoreIdentitySha256 = new string('c', 64);
            j.ImportInPlace = true;
            HostJob back = HostJob.FromJson(Json.ParseObject(Json.Serialize(j.ToJson())));
            Eq(Paths.RestoreIdentityFile, back.RestoreIdentity, "file");
            Eq(new string('c', 64), back.RestoreIdentitySha256, "sha");
            True(back.ImportInPlace, "in place");
            var d = j.ToJson();
            d["restore_identity"] = @"C:\Users\alex\Downloads\x.json";
            Throws<FormatException>(delegate { HostJob.FromJson(d); }, "only host\\in's copy");
            var plain = new HostJob();
            plain.Image = j.Image; plain.ImageSha256 = j.ImageSha256; plain.Provision = j.Provision;
            plain.ProvisionSha256 = j.ProvisionSha256; plain.WslConfig = j.WslConfig;
            var pd = plain.ToJson();
            True(!pd.ContainsKey("restore_identity") && !pd.ContainsKey("import_in_place"), "absent by default");
        });
        Run("keep identity: the keep start parameter is its own", delegate
        {
            True(Paths.KeepArg != Paths.UnregisterArg, "distinct");
        });
    }
}
