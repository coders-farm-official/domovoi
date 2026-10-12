// copy-logs: Domovoi's logs, which only administrators can open (they are
// in C:\ProgramData\Domovoi), copied to a new folder the household can
// open, for Setup's and the uninstaller's "Copy logs" buttons. Setup passes
// Documents\Domovoi logs of the account running it.
//
// What it copies: logs\host.log and logs\provision.log (with their .1
// rotations), state\install.log, host\out\status.json, and the uninstall's
// own log from the temp folder when the uninstaller names it. Every line
// goes through Redact.Line on the way: the logs never held the setup code
// or a password (Log.Write redacts too, and provision.sh's scrub_log), and
// the copies can't either.
//
// The helper runs elevated and the destination is a folder the household
// can write, so it never writes through a link: the "Domovoi logs" folder
// and the new folder inside it must not be links (junctions, symbolic
// links), the new folder is created fresh (never reused), and each file is
// created new (never overwritten).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows.Helper
{
    static class Logs
    {
        static readonly Regex UninstallLogName = new Regex(@"^domovoi-uninstall-\d{8}-\d{6}\.log\z");

        // The files to copy, as (source, name in the copy).
        public static List<KeyValuePair<string, string>> Sources(string uninstallLog)
        {
            var list = new List<KeyValuePair<string, string>>();
            foreach (string name in new[] { "host.log", "host.log.1", "provision.log", "provision.log.1" })
                list.Add(new KeyValuePair<string, string>(Path.Combine(Paths.Logs, name), name));
            list.Add(new KeyValuePair<string, string>(Paths.InstallLog, "install.log"));
            list.Add(new KeyValuePair<string, string>(Paths.InstallLog + ".1", "install.log.1"));
            list.Add(new KeyValuePair<string, string>(Paths.Status, "host-status.json"));
            if (!string.IsNullOrEmpty(uninstallLog))
            {
                // Only the uninstall's own record, in this account's temp folder.
                string full = Path.GetFullPath(uninstallLog);
                string dir = Path.GetDirectoryName(full);
                if (!UninstallLogName.IsMatch(Path.GetFileName(full))
                    || !string.Equals(dir.TrimEnd('\\'), Path.GetTempPath().TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("--uninstall-log must be a domovoi-uninstall-*.log in " + Path.GetTempPath());
                list.Add(new KeyValuePair<string, string>(full, Path.GetFileName(full)));
            }
            return list;
        }

        public static void Copy(Output o, string destRoot, string kind, string uninstallLog)
        {
            if (kind != "install" && kind != "uninstall") throw new ArgumentException("--kind must be install or uninstall");
            string root = MediaPaths.Normalize(destRoot);
            string why = MediaPaths.Problem(root);
            if (why != null) throw new ArgumentException("--dest: " + why);
            var sources = Sources(uninstallLog);

            Directory.CreateDirectory(root);
            if (Folders.IsReparse(root)) throw new InvalidOperationException(root + " is a link; not copying anything through it");
            string stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
            string dest = null;
            for (int i = 1; i < 100 && dest == null; i++)
            {
                string candidate = Path.Combine(root, kind + "-" + stamp + (i > 1 ? "-" + Inv.S(i) : ""));
                if (Directory.Exists(candidate) || File.Exists(candidate)) continue;
                Directory.CreateDirectory(candidate);
                dest = candidate;
            }
            if (dest == null) throw new InvalidOperationException("no free folder name under " + root);
            if (Folders.IsReparse(dest)) throw new InvalidOperationException(dest + " is a link; not copying anything through it");

            int copied = 0;
            foreach (var src in sources)
            {
                if (!File.Exists(src.Key)) continue;
                if ((File.GetAttributes(src.Key) & FileAttributes.ReparsePoint) != 0) { o.Say("skipped " + src.Key + " (a link)"); continue; }
                CopyRedacted(src.Key, Path.Combine(dest, src.Value));
                copied++;
            }
            File.WriteAllText(Path.Combine(dest, "README.txt"),
                "Domovoi's logs, copied by Domovoi " + (kind == "install" ? "Setup" : "'s uninstaller") + " on "
                + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + ".\r\n"
                + "They hold no password and no setup code. docs/WINDOWS_HOST.md, \"When something goes wrong\", says what each file is.\r\n",
                new UTF8Encoding(false));
            o.Say("copied " + Inv.S(copied) + " log file(s) to " + dest);
            o.Kv("folder", dest);
            o.Kv("copied", Inv.S(copied));
        }

        static void CopyRedacted(string src, string dest)
        {
            using (var r = new StreamReader(new FileStream(src, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete), Encoding.UTF8, true))
            using (var w = new StreamWriter(new FileStream(dest, FileMode.CreateNew, FileAccess.Write, FileShare.None), new UTF8Encoding(false)))
            {
                string line;
                while ((line = r.ReadLine()) != null)
                {
                    w.Write(Redact.Line(line));
                    w.Write("\r\n");
                }
            }
        }
    }
}
