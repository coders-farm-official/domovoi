// The Windows satellite Setup this server's Setup brings (2026-10-21).
//
// DomovoiSetup.exe installs DomovoiSatelliteSetup.exe of the same build
// beside DomovoiHost.exe (installer\DomovoiSatelliteSetup.iss). Each time
// Domovoi is up, DomovoiHost places it in the core's own cache inside the
// distro, as the service user, with the record the core serves it by
// (domovoi/satellite_setup.py: read_record, cached_file): so a Windows
// server hands its household's PCs the satellite Setup that matches its
// version, offline, and never fetches one from GitHub. The core checks the
// bytes against the record's SHA-256 before it serves them.

using System;
using System.IO;

namespace Domovoi.Windows
{
    public static class SatelliteSetupPlacement
    {
        public const string FileName = "DomovoiSatelliteSetup.exe";
        // scripts/linux/provision.sh SVC_USER, whose ~/.domovoi the core uses.
        public const string ServiceUser = "domovoi";
        public const string CacheDir = "/home/" + ServiceUser + "/.domovoi/satellite_media/cache/satellite_setup";
        // domovoi/satellite_setup.py BUNDLED: the card says it came with the server.
        public const string Release = "bundled";

        // Beside DomovoiHost.exe, where DomovoiSetup.exe put it; null when
        // this install has none (a Setup from before it).
        public static string Source(string hostDir)
        {
            string p = Path.Combine(hostDir, FileName);
            return File.Exists(p) ? p : null;
        }

        // The record, in the shape domovoi/satellite_setup.py read_record
        // takes. Only hex digits and numbers in it: safe inside sh quotes.
        public static string Record(string sha256, long size, long unixTime)
        {
            if (!Sha256.IsHex(sha256)) throw new ArgumentException("not a SHA-256");
            return "{\"sha256\": \"" + sha256 + "\", \"size\": " + Inv.S(size) + ", \"release\": \"" + Release
                + "\", \"fetched_at\": " + Inv.S(unixTime) + "}";
        }

        // Prints the SHA-256 of the copy already placed, if any.
        public static string CheckScript()
        {
            return "sha256sum " + CacheDir + "/" + FileName + " 2>/dev/null || true";
        }

        // Run as the service user with the file on stdin: write it beside
        // its final name, move it in, then the record, and print its
        // SHA-256 for the host to compare.
        public static string PlaceScript(string sha256, long size, long unixTime)
        {
            string d = CacheDir;
            return "set -e; umask 022; mkdir -p " + d + "; cat > " + d + "/." + FileName + ".placing; "
                + "mv -f " + d + "/." + FileName + ".placing " + d + "/" + FileName + "; "
                + "printf '%s' '" + Record(sha256, size, unixTime) + "' > " + d + "/.setup.json.placing; "
                + "mv -f " + d + "/.setup.json.placing " + d + "/setup.json; "
                + "sha256sum " + d + "/" + FileName;
        }
    }
}
