// Which new folder "Save logs for support..." made (owner, 2026-11-15:
// Explorer opened Documents\Domovoi logs, not the install-<time> folder
// the logs had just been copied to). The tray runs unelevated and starts
// the helper's copy-logs through Windows' permission prompt, which gives
// it the exit code and nothing else, so the helper leaves the new folder's
// name in a note beside it:
//
//   Documents\Domovoi logs\.copy-logs-<id>.txt   the folder's name only
//                                                (install-YYYYMMDD-HHMMSS
//                                                or with -N), ASCII
//
// <id> is 32 hex digits the tray picks for this one copy (--note). The
// helper creates the note the way it creates every file there (relative to
// the folder it holds open, FILE_CREATE, never through a link), after the
// copy has finished. The tray reads it and deletes it, and opens the
// folder only when the name is one the helper makes and that folder is a
// real folder directly inside the one the tray chose; anything else opens
// the parent, as before.

using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace Domovoi.Windows
{
    public static class LogsFolder
    {
        static readonly Regex FolderName = new Regex(@"^(install|uninstall)-[0-9]{8}-[0-9]{6}(-[0-9]{1,2})?\z");
        static readonly Regex NoteId = new Regex(@"^[0-9a-f]{32}\z");
        // A note is the name and nothing else; more than this is not one.
        public const int MaxNoteBytes = 64;

        // A name copy-logs gives its new folders: install-20261115-220413,
        // uninstall-20261115-220413-2. No separators, no dots, nothing else.
        public static bool IsFolderName(string s) { return s != null && FolderName.IsMatch(s); }

        public static bool IsNoteId(string s) { return s != null && NoteId.IsMatch(s); }

        public static string NewNoteId() { return Guid.NewGuid().ToString("N"); }

        public static string NoteName(string id)
        {
            if (!IsNoteId(id)) throw new ArgumentException("not a note id");
            return ".copy-logs-" + id + ".txt";
        }

        // The note's text for a folder name (the helper writes it).
        public static byte[] NoteBytes(string folderName)
        {
            if (!IsFolderName(folderName)) throw new ArgumentException("not a copy-logs folder name: " + folderName);
            return Encoding.ASCII.GetBytes(folderName);
        }

        // The folder name from a note's bytes, or null.
        public static string FromNote(byte[] bytes)
        {
            if (bytes == null || bytes.Length == 0 || bytes.Length > MaxNoteBytes) return null;
            foreach (byte b in bytes) if (b < 0x20 || b > 0x7e) return null;
            string s = Encoding.ASCII.GetString(bytes);
            return IsFolderName(s) ? s : null;
        }

        // The tray's side: the name in parent's note id, or null; the note
        // is deleted either way (a link or a folder at its name is left
        // alone and gives null).
        public static string TakeNote(string parent, string id)
        {
            if (!IsNoteId(id)) return null;
            string path = Path.Combine(parent, NoteName(id));
            try
            {
                if (!File.Exists(path)) return null;
                if ((File.GetAttributes(path) & (FileAttributes.ReparsePoint | FileAttributes.Directory)) != 0) return null;
                byte[] bytes = null;
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete))
                {
                    if (fs.Length <= MaxNoteBytes)
                    {
                        bytes = new byte[fs.Length];
                        int got = 0, n;
                        while (got < bytes.Length && (n = fs.Read(bytes, got, bytes.Length - got)) > 0) got += n;
                        if (got != bytes.Length) bytes = null;
                    }
                }
                try { File.Delete(path); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                return FromNote(bytes);
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
            catch (ArgumentException) { return null; }
            catch (NotSupportedException) { return null; }
        }

        // The folder to show: parent\name when name is a copy-logs folder
        // name and that is a real folder (not a link) directly inside
        // parent; otherwise parent.
        public static string ToOpen(string parent, string name)
        {
            if (string.IsNullOrEmpty(parent) || !IsFolderName(name)) return parent;
            try
            {
                string p = Path.GetFullPath(parent).TrimEnd(Path.DirectorySeparatorChar);
                string full = Path.GetFullPath(Path.Combine(p, name));
                if (!string.Equals(Path.GetDirectoryName(full), p, StringComparison.OrdinalIgnoreCase)) return parent;
                if (!string.Equals(Path.GetFileName(full), name, StringComparison.Ordinal)) return parent;
                if (!Directory.Exists(full)) return parent;
                if ((File.GetAttributes(full) & FileAttributes.ReparsePoint) != 0) return parent;
                return full;
            }
            catch (IOException) { return parent; }
            catch (UnauthorizedAccessException) { return parent; }
            catch (ArgumentException) { return parent; }
            catch (NotSupportedException) { return parent; }
        }
    }
}
