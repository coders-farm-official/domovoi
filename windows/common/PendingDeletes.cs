// What an earlier uninstall left for Windows to finish at the next restart,
// so Setup can ask for that restart first: a delete Windows does at boot
// goes by path, and would take a new install's files of the same name with
// it (the review of 2026-10-12).
//
// PendingFileRenameOperations (HKLM\SYSTEM\CurrentControlSet\Control\
// Session Manager, REG_MULTI_SZ; also PendingFileRenameOperations2) holds
// pairs: the source as an NT path ("\??\C:\ProgramData\Domovoi\..."), then
// the target, empty for a delete (MoveFileEx, MOVEFILE_DELAY_UNTIL_REBOOT:
// https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-movefileexa).

using System;
using System.Collections.Generic;

namespace Domovoi.Windows
{
    public static class PendingDeletes
    {
        public const string Key = @"SYSTEM\CurrentControlSet\Control\Session Manager";
        public static readonly string[] Values = { "PendingFileRenameOperations", "PendingFileRenameOperations2" };

        // How many pending operations act on something under one of ROOTS.
        // A rename into a root counts too: it would put an old file there.
        public static int Under(IList<string> entries, IEnumerable<string> roots)
        {
            if (entries == null) return 0;
            var rs = new List<string>();
            foreach (string r in roots) rs.Add(r.TrimEnd('\\'));
            int n = 0;
            for (int i = 0; i < entries.Count; i += 2)
            {
                string src = Strip(entries[i]);
                string dst = i + 1 < entries.Count ? Strip(entries[i + 1]) : "";
                foreach (string r in rs)
                {
                    if (Inside(src, r) || Inside(dst, r)) { n++; break; }
                }
            }
            return n;
        }

        // "\??\C:\x" and "!\??\C:\x" (the "!" marks replace-existing on a
        // target) as "C:\x".
        static string Strip(string p)
        {
            if (string.IsNullOrEmpty(p)) return "";
            if (p.StartsWith("!", StringComparison.Ordinal)) p = p.Substring(1);
            if (p.StartsWith(@"\??\", StringComparison.Ordinal)) p = p.Substring(4);
            return p;
        }

        static bool Inside(string path, string root)
        {
            if (path.Length == 0) return false;
            return string.Equals(path.TrimEnd('\\'), root, StringComparison.OrdinalIgnoreCase)
                || path.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase);
        }
    }
}
