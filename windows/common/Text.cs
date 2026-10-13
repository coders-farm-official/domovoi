// Small text rules shared by the host and the helper: quoting an argument
// for a Windows command line, and keeping anything shaped like a setup
// code out of logs.

using System;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class CommandLine
    {
        // One argument, quoted the way CommandLineToArgvW and the MSVC runtime
        // read it back: backslashes are literal except before a quote, so a
        // run of them before a quote (or before the closing quote) doubles.
        // https://learn.microsoft.com/cpp/c-language/parsing-c-command-line-arguments
        public static string Quote(string arg)
        {
            if (arg == null) throw new ArgumentNullException("arg");
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0) return arg;
            var sb = new StringBuilder();
            sb.Append('"');
            int backslashes = 0;
            foreach (char c in arg)
            {
                if (c == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (c == '"')
                {
                    sb.Append('\\', backslashes * 2 + 1);
                    sb.Append('"');
                }
                else
                {
                    sb.Append('\\', backslashes);
                    sb.Append(c);
                }
                backslashes = 0;
            }
            sb.Append('\\', backslashes * 2);
            sb.Append('"');
            return sb.ToString();
        }

        public static string Join(IEnumerable<string> args)
        {
            var parts = new List<string>();
            foreach (string a in args) parts.Add(Quote(a));
            return string.Join(" ", parts.ToArray());
        }
    }

    public static class SetupCode
    {
        // domovoi/admin_auth.py: eight lowercase words joined with hyphens.
        static readonly Regex Shape = new Regex(@"^[a-z]{2,16}(-[a-z]{2,16}){7}\z");
        // Anything like it inside a line, the rule provision.sh's scrub_log
        // uses: a word followed by seven or more -word parts.
        static readonly Regex InLine = new Regex("[A-Za-z]+(-[A-Za-z]+){7,}");

        public static bool IsWellFormed(string code)
        {
            return code != null && Shape.IsMatch(code);
        }

        // A log line with every setup-code-shaped run replaced by [hidden].
        public static string Scrub(string line)
        {
            if (string.IsNullOrEmpty(line)) return line;
            return InLine.Replace(line, "[hidden]");
        }
    }

    // Every line that goes into a log, or into the copies "Copy logs"
    // makes for the household: secrets blanked as far as they can be
    // recognised. A setup code (SetupCode.Scrub); what sits before the @ of
    // a URL (scheme://user:password@, scheme://:password@, a bare
    // scheme://token@), keeping a user name; the value of a key that names
    // a password, token, secret or API key (password=..., POSTGRES_PASSWORD
    // =..., "token": "...", api_key: ...); and an Authorization header or a
    // Bearer token. A pattern match, not a guarantee.
    public static class Redact
    {
        // Up to the first @ after the scheme, '/' included (a password may
        // hold one); over-blanking an @ in a path is the safe mistake.
        static readonly Regex UrlUserInfo = new Regex(@"(?i)\b([a-z][a-z0-9+.\-]*://)([^\s@""'<>]+)@");
        static readonly Regex KeyValue = new Regex(
            @"(?i)\b((?:[a-z0-9]+[_-])*(?:password|passwd|pwd|passphrase|token|secret|api[_-]?key|access[_-]?key|private[_-]?key)(?:[_-][a-z0-9]+)*[""']?\s*[=:]\s*)(""[^""]*""|'[^']*'|\S+)");
        static readonly Regex Authorization = new Regex(@"(?i)\b(authorization\s*[=:]\s*)(?:(bearer|basic|token|digest)\s+)?\S+");
        static readonly Regex Bearer = new Regex(@"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}");

        public static string Line(string line)
        {
            if (string.IsNullOrEmpty(line)) return line;
            string s = SetupCode.Scrub(line);
            s = UrlUserInfo.Replace(s, delegate (Match m)
            {
                string info = m.Groups[2].Value;
                int colon = info.IndexOf(':');
                return m.Groups[1].Value + (colon >= 0 ? info.Substring(0, colon + 1) : "") + "[hidden]@";
            });
            s = Authorization.Replace(s, delegate (Match m)
            {
                return m.Groups[1].Value + (m.Groups[2].Success ? m.Groups[2].Value + " " : "") + "[hidden]";
            });
            s = Bearer.Replace(s, "$1[hidden]");
            s = KeyValue.Replace(s, delegate (Match m)
            {
                string v = m.Groups[2].Value;
                if (v.StartsWith("[hidden]", StringComparison.Ordinal)) return m.Value;
                string q = v.Length > 1 && (v[0] == '"' || v[0] == '\'') ? v.Substring(0, 1) : "";
                return m.Groups[1].Value + q + "[hidden]" + q;
            });
            return s;
        }
    }
}
