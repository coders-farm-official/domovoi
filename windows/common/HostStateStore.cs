// host\out\host-state.json: what DomovoiHost remembers across its own
// restarts (the provisioned job, the import, the household's limits, the
// last restart id it acted on).
//
// Read-modify-write, safely (review 2026-11-10): a change reads the file
// first, and a file that is there but can't be read or parsed (a moment
// another writer holds it, a torn file) aborts the change instead of
// writing a file that holds only the new key, which would wipe the rest
// ("imported", the provisioned job: a fresh import would then be tried
// over the household's disk, which Import() refuses, and Domovoi would
// stay down). Update says whether the change landed, so a caller that
// must not act without it (a restart id recorded before the restart) can
// refuse to act.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

namespace Domovoi.Windows
{
    public sealed class HostStateStore
    {
        readonly string path;

        public HostStateStore(string path)
        {
            this.path = path;
        }

        // Every key, or null when the file is there and can't be read; an
        // empty map when there is no file yet.
        public Dictionary<string, object> ReadStrict()
        {
            try
            {
                if (!File.Exists(path)) return new Dictionary<string, object>();
                return Json.ReadFile(path) ?? new Dictionary<string, object>();
            }
            catch (FormatException) { return null; }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
        }

        // For reads that only decide something: what can't be read reads as
        // empty (nothing recorded).
        public Dictionary<string, object> Read()
        {
            return ReadStrict() ?? new Dictionary<string, object>();
        }

        // The changes applied to the file as it is; false (and the file left
        // as it was) when it couldn't be read or written. why: what went
        // wrong, for the log.
        public bool Update(IDictionary<string, object> changes, DateTime utc, out string why)
        {
            why = null;
            Dictionary<string, object> d = ReadStrict();
            if (d == null)
            {
                why = path + " is there but can't be read; nothing changed in it";
                return false;
            }
            foreach (var kv in changes) d[kv.Key] = kv.Value;
            d["at"] = utc.ToString("o", CultureInfo.InvariantCulture);
            try
            {
                Json.WriteFile(path, d);
                return true;
            }
            catch (IOException e) { why = e.Message; }
            catch (UnauthorizedAccessException e) { why = e.Message; }
            return false;
        }
    }
}
