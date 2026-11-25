// Keeping the house identity across a reinstall (S3-4, owner decision
// 2026-11-22): the pure rules both the uninstaller and Setup use, kept here
// so the tests can hold them.
//
// KeptName: the folders a keep-data uninstall leaves,
//   C:\ProgramData\Domovoi-kept-<yyyyMMdd-HHmmss> (helper Uninstall.KeepOrDropDisk).
// BackupShape: whether a file looks like a house identity backup
//   (domovoi/server_identity.py backup_document) before Setup copies it; the
//   key pair is checked in the distro, by the core's own code, before use.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class KeptName
    {
        static readonly Regex Name = new Regex(@"^Domovoi-kept-(\d{8}-\d{6})\z");

        public static bool IsKept(string folderName)
        {
            return folderName != null && Name.IsMatch(folderName);
        }

        // "2026-11-22 21:05" for a kept folder's name, or null.
        public static string When(string folderName)
        {
            if (folderName == null) return null;
            Match m = Name.Match(folderName);
            DateTime t;
            if (!m.Success || !DateTime.TryParseExact(m.Groups[1].Value, "yyyyMMdd-HHmmss", CultureInfo.InvariantCulture, DateTimeStyles.None, out t))
                return null;
            return t.ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture);
        }
    }

    public static class BackupShape
    {
        public const string Kind = "domovoi-server-identity-backup";
        static readonly Regex B64 = new Regex(@"^[A-Za-z0-9+/]{42,44}={0,2}\z");

        // Null when the bytes look like a house identity backup; else why
        // not, in words that never repeat the file's contents.
        public static string Problem(byte[] raw)
        {
            if (raw == null || raw.Length == 0) return "that file is empty, not a house identity backup";
            if (raw.Length > 16 * 1024) return "that file is far too large to be a house identity backup";
            Dictionary<string, object> d;
            try { d = Json.ParseObject(new UTF8Encoding(false, true).GetString(raw)); }
            catch (Exception e)
            {
                if (e is OutOfMemoryException) throw;
                return "that file is not a house identity backup (it isn't JSON)";
            }
            if (d == null) return "that file is not a house identity backup (it isn't a JSON object)";
            string kind = Json.Str(d, "kind");
            if (kind == "satellite-server-fingerprint")
                return "that file is a room's record of its server (public only), not a house identity backup";
            if (kind != null && kind != Kind) return "that file is not a house identity backup (unknown kind)";
            if (Json.Str(d, "algorithm") != "ed25519") return "that file is not a house identity backup (wrong algorithm)";
            string priv = Json.Str(d, "private_key"), pub = Json.Str(d, "public_key"), fp = Json.Str(d, "fingerprint");
            if (priv == null || !B64.IsMatch(priv)) return "the backup's private key is missing or damaged";
            if (pub == null || !B64.IsMatch(pub)) return "the backup's public key is missing or damaged";
            if (fp == null || !fp.StartsWith("SHA256:", StringComparison.Ordinal)) return "the backup has no fingerprint";
            return null;
        }

        // The (public) fingerprint, for Setup to show; null if none.
        public static string Fingerprint(byte[] raw)
        {
            if (Problem(raw) != null) return null;
            return Json.Str(Json.ParseObject(Encoding.UTF8.GetString(raw)), "fingerprint");
        }
    }
}
