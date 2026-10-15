// JSON through the framework's own serializer (System.Web.Extensions, in
// every .NET Framework 4.x install), so nothing ships beside the exes.
// DeserializeObject gives Dictionary<string, object> for objects, object[]
// for arrays, and int, long or decimal for numbers.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace Domovoi.Windows
{
    public static class Json
    {
        static JavaScriptSerializer Make()
        {
            var s = new JavaScriptSerializer();
            s.MaxJsonLength = 16 * 1024 * 1024;
            s.RecursionLimit = 32;
            return s;
        }

        // An object, or FormatException (also for a JSON array, string or number).
        public static Dictionary<string, object> ParseObject(string text)
        {
            if (text == null) throw new FormatException("no JSON text");
            object o;
            try
            {
                o = Make().DeserializeObject(text);
            }
            catch (ArgumentException e)
            {
                throw new FormatException("not valid JSON: " + e.Message);
            }
            catch (InvalidOperationException e)
            {
                throw new FormatException("not valid JSON: " + e.Message);
            }
            var d = o as Dictionary<string, object>;
            if (d == null) throw new FormatException("not a JSON object");
            return d;
        }

        public static string Serialize(object o)
        {
            return Make().Serialize(o);
        }

        public static bool Has(IDictionary<string, object> d, string key)
        {
            return d != null && d.ContainsKey(key);
        }

        public static bool IsNull(IDictionary<string, object> d, string key)
        {
            object v;
            return d == null || !d.TryGetValue(key, out v) || v == null;
        }

        public static string Str(IDictionary<string, object> d, string key)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return null;
            return v as string;
        }

        public static long? Long(IDictionary<string, object> d, string key)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return null;
            if (v is int) return (int)v;
            if (v is long) return (long)v;
            if (v is decimal)
            {
                decimal m = (decimal)v;
                if (m != decimal.Truncate(m) || m > long.MaxValue || m < long.MinValue) return null;
                return (long)m;
            }
            return null;
        }

        public static int? Int(IDictionary<string, object> d, string key)
        {
            long? v = Long(d, key);
            if (v == null || v.Value > int.MaxValue || v.Value < int.MinValue) return null;
            return (int)v.Value;
        }

        public static bool? Bool(IDictionary<string, object> d, string key)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return null;
            if (v is bool) return (bool)v;
            return null;
        }

        // A list of strings; null when the key is missing, null, or not a list of strings.
        public static List<string> StrList(IDictionary<string, object> d, string key)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return null;
            var arr = v as IEnumerable;
            if (arr == null || v is string) return null;
            var list = new List<string>();
            foreach (object item in arr)
            {
                var s = item as string;
                if (s == null) return null;
                list.Add(s);
            }
            return list;
        }

        public static Dictionary<string, object> Obj(IDictionary<string, object> d, string key)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return null;
            return v as Dictionary<string, object>;
        }

        public static List<Dictionary<string, object>> ObjList(IDictionary<string, object> d, string key)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return null;
            var arr = v as IEnumerable;
            if (arr == null || v is string) return null;
            var list = new List<Dictionary<string, object>>();
            foreach (object item in arr)
            {
                var o = item as Dictionary<string, object>;
                if (o == null) return null;
                list.Add(o);
            }
            return list;
        }

        // Reads a JSON object file; null when the file does not exist.
        public static Dictionary<string, object> ReadFile(string path)
        {
            if (!File.Exists(path)) return null;
            string text = File.ReadAllText(path, new UTF8Encoding(false));
            return ParseObject(text);
        }

        // Writes by rename from a temporary file beside the target, so a reader
        // never sees half a file.
        public static void WriteFile(string path, object value)
        {
            AtomicFile.WriteAllText(path, Serialize(value) + "\n");
        }
    }

    public static class AtomicFile
    {
        public static void WriteAllText(string path, string text)
        {
            string dir = Path.GetDirectoryName(path);
            string tmp = Path.Combine(dir, "." + Path.GetFileName(path) + "." + Guid.NewGuid().ToString("N") + ".tmp");
            File.WriteAllText(tmp, text, new UTF8Encoding(false));
            try
            {
                if (File.Exists(path)) File.Replace(tmp, path, null, true);
                else File.Move(tmp, path);
            }
            catch
            {
                try { File.Delete(tmp); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                throw;
            }
        }
    }

    public static class Inv
    {
        public static string S(long n) { return n.ToString(CultureInfo.InvariantCulture); }
        public static string S(int n) { return n.ToString(CultureInfo.InvariantCulture); }
        public static string F(string format, params object[] args) { return string.Format(CultureInfo.InvariantCulture, format, args); }
    }
}
