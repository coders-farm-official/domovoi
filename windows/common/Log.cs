// Append-only text logs under C:\ProgramData\Domovoi\logs. Every line goes
// through Redact.Line first (no setup code, no password in a URL), and
// nothing here is ever handed the account's password: it exists only
// inside the helper's account step and is never formatted into a message.

using System;
using System.Globalization;
using System.IO;
using System.Text;

namespace Domovoi.Windows
{
    public sealed class Log
    {
        readonly string path;
        readonly long maxBytes;
        readonly object gate = new object();

        public Log(string path, long maxBytes)
        {
            this.path = path;
            this.maxBytes = maxBytes;
        }

        public string Path { get { return path; } }

        public void Write(string message)
        {
            string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + " " + Redact.Line(message ?? "");
            lock (gate)
            {
                try
                {
                    var fi = new FileInfo(path);
                    if (fi.Exists && fi.Length > maxBytes)
                    {
                        string old = path + ".1";
                        if (File.Exists(old)) File.Delete(old);
                        File.Move(path, old);
                    }
                    File.AppendAllText(path, line + "\r\n", new UTF8Encoding(false));
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }
    }
}
