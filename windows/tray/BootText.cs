// The two text edits a satellite card needs on its boot partition, byte for
// byte what the server writes (domovoi/satellite_media/overlay.py,
// edit_config_txt, edit_cmdline_txt and the loop in write_overlay that
// applies them). Golden tests feed the same inputs to both
// (windows/tests/card-goldens.json, made and checked by
// domovoi/tests/test_windows_card_parity.py).
//
// What the server does, step by step:
// * reads the file as Python's Path.read_text(encoding="utf-8",
//   errors="replace") does: undecodable bytes become U+FFFD, and every
//   CRLF or lone CR becomes LF (universal newlines);
// * config.txt: nothing if the marker is already there; else a newline if
//   the text doesn't end with one, then the block;
// * cmdline.txt: only the first line is kept, its tokens split on Python's
//   whitespace, each wanted token added unless the same token or the same
//   key= is already there, joined with single spaces, one LF at the end;
// * writes the result (UTF-8, LF) only when it differs from what it read,
//   so a card already carrying the edits keeps its bytes (CRLF and all).
//
// The block and the tokens come from the zip's own config.txt and
// cmdline.txt (the server writes them by running the same edits on empty
// files), so a card gets exactly what the server that built the zip
// wanted.

using System;
using System.Collections.Generic;
using System.Text;

namespace Domovoi.Windows.Tray
{
    public static class BootText
    {
        public const string Marker = "# --- domovoi satellite (media-prep) ---";
        public const string EndMarker = "# --- end domovoi satellite ---";
        // The first-boot hook: without it the card boots as plain Pi OS.
        public const string FirstrunToken = "systemd.run=/boot/firmware/domovoi/firstrun.sh";

        // Python's str.isspace() (the characters str.split() and strip()
        // treat as whitespace).
        public static bool IsPySpace(char c)
        {
            if (c >= '\t' && c <= '\r') return true;
            if (c >= '\x1c' && c <= ' ') return true;
            switch (c)
            {
                case '\x85': case '\xa0': case '\u1680': case '\u2028': case '\u2029':
                case '\u202f': case '\u205f': case '\u3000':
                    return true;
            }
            return c >= '\u2000' && c <= '\u200a';
        }

        // Python's str.splitlines() line boundaries (\r\n counts once).
        static bool IsPyLineBreak(char c)
        {
            switch (c)
            {
                case '\n': case '\r': case '\x0b': case '\x0c': case '\x1c': case '\x1d': case '\x1e':
                case '\x85': case '\u2028': case '\u2029':
                    return true;
            }
            return false;
        }

        public static string PyStrip(string s)
        {
            int a = 0, b = s.Length;
            while (a < b && IsPySpace(s[a])) a++;
            while (b > a && IsPySpace(s[b - 1])) b--;
            return s.Substring(a, b - a);
        }

        public static List<string> PySplit(string s)
        {
            var parts = new List<string>();
            int i = 0;
            while (i < s.Length)
            {
                while (i < s.Length && IsPySpace(s[i])) i++;
                int start = i;
                while (i < s.Length && !IsPySpace(s[i])) i++;
                if (i > start) parts.Add(s.Substring(start, i - start));
            }
            return parts;
        }

        public static string PyFirstLine(string s)
        {
            int i = 0;
            while (i < s.Length && !IsPyLineBreak(s[i])) i++;
            return s.Substring(0, i);
        }

        // Path.read_text(encoding="utf-8", errors="replace").
        public static string Read(byte[] bytes)
        {
            string text = Utf8.Decode(bytes);
            if (text.IndexOf('\r') < 0) return text;
            var sb = new StringBuilder(text.Length);
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '\r')
                {
                    sb.Append('\n');
                    if (i + 1 < text.Length && text[i + 1] == '\n') i++;
                }
                else sb.Append(c);
            }
            return sb.ToString();
        }

        // edit_config_txt, with the block the zip carries.
        public static string EditConfig(string text, string block)
        {
            if (text.Contains(Marker)) return text;
            if (text.Length > 0 && !text.EndsWith("\n", StringComparison.Ordinal)) text += "\n";
            return text + block;
        }

        // edit_cmdline_txt, with the tokens the zip carries.
        public static string EditCmdline(string text, IList<string> wanted)
        {
            string stripped = PyStrip(text);
            string line = stripped.Length > 0 ? PyFirstLine(stripped) : "";
            List<string> tokens = line.Length > 0 ? PySplit(line) : new List<string>();
            foreach (string tok in wanted)
            {
                int eq = tok.IndexOf('=');
                string key = eq >= 0 ? tok.Substring(0, eq) : tok;
                bool have = false;
                foreach (string t in tokens)
                    if (t == tok || t.StartsWith(key + "=", StringComparison.Ordinal)) { have = true; break; }
                if (!have) tokens.Add(tok);
            }
            return string.Join(" ", tokens.ToArray()) + "\n";
        }

        // The loop in write_overlay: the bytes the file holds afterwards,
        // and whether it was written at all.
        public static byte[] Apply(byte[] original, bool exists, Func<string, string> edit, out bool changed)
        {
            string before = exists ? Read(original) : "";
            string after = edit(before);
            changed = after != before;
            return changed ? new UTF8Encoding(false).GetBytes(after) : original;
        }

        // The block in the zip's config.txt (edit_config_txt of an empty
        // file): the marker line first, the end marker last, settings in
        // between. Null when it isn't that shape.
        public static string BlockFromZip(string zipConfig)
        {
            if (zipConfig == null || zipConfig.IndexOf('\r') >= 0 || !zipConfig.EndsWith("\n", StringComparison.Ordinal)) return null;
            string[] lines = zipConfig.Substring(0, zipConfig.Length - 1).Split('\n');
            if (lines.Length < 2 || lines[0] != Marker || lines[lines.Length - 1] != EndMarker) return null;
            for (int i = 1; i < lines.Length - 1; i++)
            {
                string l = lines[i];
                if (l.Length == 0 || l.Contains(Marker) || l.Contains(EndMarker) || l.IndexOf('=') <= 0) return null;
                foreach (char c in l) if (c < ' ' || c > '~') return null;
            }
            return zipConfig;
        }

        // The tokens in the zip's cmdline.txt (edit_cmdline_txt of an
        // empty file): one line, the first-boot hook among them. Null when
        // it isn't that shape.
        public static List<string> TokensFromZip(string zipCmdline)
        {
            if (zipCmdline == null || zipCmdline.IndexOf('\r') >= 0 || !zipCmdline.EndsWith("\n", StringComparison.Ordinal)) return null;
            string line = zipCmdline.Substring(0, zipCmdline.Length - 1);
            if (line.IndexOf('\n') >= 0) return null;
            List<string> tokens = PySplit(line);
            if (tokens.Count == 0 || string.Join(" ", tokens.ToArray()) != line || !tokens.Contains(FirstrunToken)) return null;
            foreach (string t in tokens)
                foreach (char c in t) if (c <= ' ' || c > '~') return null;
            return tokens;
        }
    }

    // UTF-8 decoding with Python's errors="replace": each maximal invalid
    // subsequence (Unicode's "maximal subpart" rule, which CPython follows)
    // becomes one U+FFFD. Written out here rather than left to the
    // framework, whose replacement count differs for some broken bytes.
    public static class Utf8
    {
        public static string Decode(byte[] b)
        {
            var sb = new StringBuilder(b.Length);
            int i = 0, n = b.Length;
            while (i < n)
            {
                int c = b[i];
                if (c < 0x80) { sb.Append((char)c); i++; continue; }
                int need, cp, lo = 0x80, hi = 0xBF;
                if (c >= 0xC2 && c <= 0xDF) { need = 1; cp = c & 0x1F; }
                else if (c >= 0xE0 && c <= 0xEF)
                {
                    need = 2; cp = c & 0x0F;
                    if (c == 0xE0) lo = 0xA0;
                    else if (c == 0xED) hi = 0x9F;
                }
                else if (c >= 0xF0 && c <= 0xF4)
                {
                    need = 3; cp = c & 0x07;
                    if (c == 0xF0) lo = 0x90;
                    else if (c == 0xF4) hi = 0x8F;
                }
                else { sb.Append('\ufffd'); i++; continue; }
                int j = i + 1, got = 0;
                bool bad = false;
                while (got < need)
                {
                    if (j >= n) { bad = true; break; }
                    int d = b[j];
                    int l = got == 0 ? lo : 0x80, h = got == 0 ? hi : 0xBF;
                    if (d < l || d > h) { bad = true; break; }
                    cp = (cp << 6) | (d & 0x3F);
                    j++;
                    got++;
                }
                if (bad) { sb.Append('\ufffd'); i = j; continue; }
                if (cp >= 0x10000) sb.Append(char.ConvertFromUtf32(cp));
                else sb.Append((char)cp);
                i = j;
            }
            return sb.ToString();
        }
    }
}
