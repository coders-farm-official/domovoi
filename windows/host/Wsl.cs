// wsl.exe as DomovoiHost runs it: always C:\Program Files\WSL\wsl.exe
// (the System32 stub doesn't work from session 0, microsoft/WSL#9231),
// always WSL_UTF8=1 so wsl.exe's own messages come back as UTF-8 rather
// than UTF-16, and always --cd / so no Windows working directory has to
// be translated (the distro has no /mnt/c once provisioned).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;

namespace Domovoi.Windows.Host
{
    sealed class WslResult
    {
        public int ExitCode;
        public string Stdout = "";
        public string Stderr = "";
        public bool TimedOut;
    }

    static class Wsl
    {
        public static ProcessStartInfo Start(IEnumerable<string> args)
        {
            var psi = new ProcessStartInfo(Paths.WslExe, CommandLine.Join(args));
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WorkingDirectory = Environment.SystemDirectory;
            psi.EnvironmentVariables["WSL_UTF8"] = "1";
            return psi;
        }

        // Runs wsl.exe to the end (or the timeout, then kills it), with
        // stdout and stderr captured up to maxOutput characters each.
        public static WslResult Run(IEnumerable<string> args, int timeoutMs, byte[] stdin, int maxOutput)
        {
            var psi = Start(args);
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.RedirectStandardInput = stdin != null;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            var r = new WslResult();
            var o = new StringBuilder();
            var e = new StringBuilder();
            using (var p = new Process())
            {
                p.StartInfo = psi;
                p.OutputDataReceived += delegate (object s, DataReceivedEventArgs a) { if (a.Data != null) lock (o) if (o.Length < maxOutput) o.Append(a.Data).Append('\n'); };
                p.ErrorDataReceived += delegate (object s, DataReceivedEventArgs a) { if (a.Data != null) lock (e) if (e.Length < maxOutput) e.Append(a.Data).Append('\n'); };
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                if (stdin != null)
                {
                    try
                    {
                        p.StandardInput.BaseStream.Write(stdin, 0, stdin.Length);
                        p.StandardInput.BaseStream.Flush();
                    }
                    catch (IOException) { }
                    p.StandardInput.Close();
                }
                if (!p.WaitForExit(timeoutMs))
                {
                    r.TimedOut = true;
                    try { p.Kill(); } catch (InvalidOperationException) { } catch (System.ComponentModel.Win32Exception) { }
                    p.WaitForExit(5000);
                }
                else
                {
                    p.WaitForExit();
                }
                r.ExitCode = r.TimedOut ? -1 : p.ExitCode;
            }
            lock (o) r.Stdout = o.ToString();
            lock (e) r.Stderr = e.ToString();
            return r;
        }

        // The distros this account has, or null when WSL can't be asked.
        public static List<string> List()
        {
            WslResult r = Run(new[] { "--list", "--quiet" }, 60000, null, 64 * 1024);
            if (r.TimedOut) return null;
            var names = new List<string>();
            foreach (string raw in r.Stdout.Split('\n'))
            {
                // Older wsl.exe builds ignore WSL_UTF8 and print UTF-16:
                // drop the NULs either way.
                string line = raw.Replace("\0", "").Trim();
                if (line.Length > 0) names.Add(line);
            }
            // No distros at all: wsl.exe says so on stdout and exits non-zero.
            if (r.ExitCode != 0)
            {
                foreach (string n in names) if (string.Equals(n, Paths.DistroName, StringComparison.OrdinalIgnoreCase)) return names;
                return new List<string>();
            }
            return names;
        }

        public static bool? Running()
        {
            WslResult r = Run(new[] { "--list", "--running", "--quiet" }, 30000, null, 64 * 1024);
            if (r.TimedOut) return null;
            foreach (string raw in r.Stdout.Split('\n'))
                if (string.Equals(raw.Replace("\0", "").Trim(), Paths.DistroName, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        public static bool? Registered()
        {
            List<string> names = List();
            if (names == null) return null;
            foreach (string n in names) if (string.Equals(n, Paths.DistroName, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }
    }
}
