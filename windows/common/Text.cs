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
    // makes for the household: no setup code (SetupCode.Scrub) and no
    // password in a URL (scheme://user:password@host, the same rule as
    // provision.sh's scrub_log).
    public static class Redact
    {
        static readonly Regex UrlPassword = new Regex(@"(://[^:/@\s]+:)[^@/\s]+@");

        public static string Line(string line)
        {
            if (string.IsNullOrEmpty(line)) return line;
            return UrlPassword.Replace(SetupCode.Scrub(line), "$1[hidden]@");
        }
    }
}
