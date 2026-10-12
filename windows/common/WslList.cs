// What `wsl.exe --list --quiet` said, read strictly: a list of names, an
// empty list only when WSL says the account has no distributions at all,
// and null (unknown) for every other failure. An unknown answer must
// never be read as "no distro": the host would import over the household's
// data, and uninstall would skip the unregister.

using System;
using System.Collections.Generic;

namespace Domovoi.Windows
{
    public static class WslList
    {
        // The error code wsl.exe prints with "Error code:" (the same in every
        // language), and the English message, for builds that print only it.
        public const string NoDistroCode = "WSL_E_DEFAULT_DISTRO_NOT_FOUND";
        public const string NoDistroText = "has no installed distributions";

        public static List<string> Parse(int exitCode, bool timedOut, string output)
        {
            if (timedOut || output == null) return null;
            string text = output.Replace("\0", "");
            if (text.IndexOf(NoDistroCode, StringComparison.Ordinal) >= 0 || text.IndexOf(NoDistroText, StringComparison.Ordinal) >= 0)
                return new List<string>();
            if (exitCode != 0) return null;
            var names = new List<string>();
            foreach (string raw in text.Split('\n'))
            {
                string line = raw.Trim();
                if (line.Length > 0) names.Add(line);
            }
            return names;
        }

        // true, false, or null when unknown.
        public static bool? Contains(List<string> names, string distro)
        {
            if (names == null) return null;
            foreach (string n in names) if (string.Equals(n, distro, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }
    }
}
