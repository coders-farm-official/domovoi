// How the installer (elevated) and DomovoiHost (the hidden account) talk
// in this step. There is no pipe: the step-3 supervisor brings the host's
// SYSTEM-only pipe (PLAN 5.1). Until then:
//
//   * host\in\job.json        what the host should bring up; written by the
//                             installer; the account can read, not write
//   * host\in\req-<id>.json   a one-off request (the setup code; the
//                             household's AI_FEATURES); same ACL
//   * host\in\<image>, provision.sh   the pinned downloads; same ACL, so
//                             the account (and the distro) can't change them
//   * host\out\status.json    what the host is doing; written by the host
//   * host\out\resp-<id>.json the answer to a request; the host deletes it
//                             once its request is gone
//   * the service's start parameter "unregister" (uninstall): only
//     Administrators and SYSTEM may start the service (the SCM's default
//     DACL), so only they can ask for it.
//
// The elevated helper only reads host\out; it never writes, moves or
// deletes anything in a folder the account can write.
//
// The setup code never touches the disk in clear: the request carries a
// one-time RSA public key the installer holds the private half of in
// memory, and the host answers with the code encrypted to it.

using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace Domovoi.Windows
{
    public static class Paths
    {
        public const string ServiceName = "DomovoiHost";
        public const string ServiceDisplayName = "Domovoi Host";
        public const string AccountName = "domovoi-svc";
        public const string DistroName = "domovoi";
        public const string UnregisterArg = "unregister";

        public static string ProgramData
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Domovoi"); }
        }

        public static string Distro { get { return Path.Combine(ProgramData, "distro"); } }
        public static string Wsl { get { return Path.Combine(ProgramData, "wsl"); } }
        public static string Backups { get { return Path.Combine(ProgramData, "backups"); } }
        public static string Logs { get { return Path.Combine(ProgramData, "logs"); } }
        public static string State { get { return Path.Combine(ProgramData, "state"); } }
        public static string HostDir { get { return Path.Combine(ProgramData, "host"); } }
        public static string HostIn { get { return Path.Combine(HostDir, "in"); } }
        public static string HostOut { get { return Path.Combine(HostDir, "out"); } }
        public static string Job { get { return Path.Combine(HostIn, "job.json"); } }
        public static string Status { get { return Path.Combine(HostOut, "status.json"); } }
        public static string UnregisterResult { get { return Path.Combine(HostOut, "unregister.json"); } }
        // state\ is Administrators and SYSTEM only: the account can't touch it.
        public static string InstallLog { get { return Path.Combine(State, "install.log"); } }
        public static string HostLog { get { return Path.Combine(Logs, "host.log"); } }
        public static string ProvisionLog { get { return Path.Combine(Logs, "provision.log"); } }
        public static string SwapFile { get { return Path.Combine(Wsl, "swap.vhdx"); } }
        public static string DistroDisk { get { return Path.Combine(Distro, "ext4.vhdx"); } }

        // The install record (the ledger): admin-written, readable by all,
        // outside ProgramData so it outlives a half-finished uninstall.
        public const string RecordKey = @"SOFTWARE\Domovoi";
        public const string RecordValue = "Install";

        // From a service, the System32 stub doesn't work in session 0
        // (microsoft/WSL#9231): always the WSL package's own wsl.exe.
        public static string WslExe
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"WSL\wsl.exe"); }
        }

        public static string RequestFile(string id) { return Path.Combine(HostIn, "req-" + id + ".json"); }
        public static string ResponseFile(string id) { return Path.Combine(HostOut, "resp-" + id + ".json"); }
    }

    // job.json: everything the host needs to bring Domovoi up.
    public sealed class HostJob
    {
        public int Generation;
        public int Attempt;
        public string Image;
        public string ImageSha256;
        public string Provision;
        public string ProvisionSha256;
        public List<string> ProvisionArgs = new List<string>();
        public string WslConfig;

        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["v"] = 1;
            d["generation"] = Generation;
            d["attempt"] = Attempt;
            d["distro"] = Paths.DistroName;
            d["image"] = Image;
            d["image_sha256"] = ImageSha256;
            d["install_dir"] = Paths.Distro;
            d["provision"] = Provision;
            d["provision_sha256"] = ProvisionSha256;
            d["provision_args"] = ProvisionArgs.ToArray();
            d["wslconfig"] = WslConfig;
            return d;
        }

        public static HostJob FromJson(Dictionary<string, object> d)
        {
            if (Json.Int(d, "v") != 1) throw new FormatException("job.json: unknown version");
            var j = new HostJob();
            j.Generation = Json.Int(d, "generation") ?? 0;
            j.Attempt = Json.Int(d, "attempt") ?? 0;
            j.Image = Json.Str(d, "image");
            j.ImageSha256 = Json.Str(d, "image_sha256");
            j.Provision = Json.Str(d, "provision");
            j.ProvisionSha256 = Json.Str(d, "provision_sha256");
            j.ProvisionArgs = Json.StrList(d, "provision_args") ?? new List<string>();
            j.WslConfig = Json.Str(d, "wslconfig");
            if (string.IsNullOrEmpty(j.Image) || string.IsNullOrEmpty(j.Provision) || string.IsNullOrEmpty(j.WslConfig))
                throw new FormatException("job.json: missing image, provision or wslconfig");
            if (!Sha256.IsHex(j.ImageSha256) || !Sha256.IsHex(j.ProvisionSha256))
                throw new FormatException("job.json: missing pins");
            return j;
        }
    }

    // status.json: what the host is doing, for the installer's progress page.
    public sealed class HostStatus
    {
        // starting, waiting, config, importing, provisioning, ports (a port
        // Domovoi needs is taken, right before the start: Setup shows the
        // pop-ups, the host waits), restarting, verifying, health, ready,
        // failed, unregistering, unregistered
        public string State = "starting";
        public string Phase = HostProgress.PhaseConfig;
        public int Percent;
        public string Message = "";
        public string Step;
        public long? Bytes;
        public long? BytesTotal;
        public int Generation;
        public int Attempt;
        public string Error;
        public string FailedStep;
        public string Head;
        public string Tier;
        public List<string> Gpu;
        public bool GpuKnown;
        // provision.sh's final "ai" in this host's run: AI chat and
        // questions on (true) or left out (false); null: unknown (no run
        // yet, or one that didn't say).
        public bool? Ai;
        public string Updated;
        public string Holder = "stopped";
        public int HostPid;

        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["v"] = 1;
            d["state"] = State;
            d["phase"] = Phase;
            d["percent"] = Percent;
            d["message"] = Message;
            d["step"] = Step;
            d["bytes"] = Bytes;
            d["bytes_total"] = BytesTotal;
            d["generation"] = Generation;
            d["attempt"] = Attempt;
            d["error"] = Error;
            d["failed_step"] = FailedStep;
            d["head"] = Head;
            d["tier"] = Tier;
            d["gpu"] = GpuKnown && Gpu != null ? (object)Gpu.ToArray() : null;
            d["ai"] = Ai == null ? null : (object)Ai.Value;
            d["updated"] = Updated;
            d["holder"] = Holder;
            d["host_pid"] = HostPid;
            return d;
        }

        public static HostStatus FromJson(Dictionary<string, object> d)
        {
            var s = new HostStatus();
            s.State = Json.Str(d, "state") ?? "unknown";
            s.Phase = Json.Str(d, "phase") ?? "";
            s.Percent = Json.Int(d, "percent") ?? 0;
            s.Message = Json.Str(d, "message") ?? "";
            s.Step = Json.Str(d, "step");
            s.Bytes = Json.Long(d, "bytes");
            s.BytesTotal = Json.Long(d, "bytes_total");
            s.Generation = Json.Int(d, "generation") ?? 0;
            s.Attempt = Json.Int(d, "attempt") ?? 0;
            s.Error = Json.Str(d, "error");
            s.FailedStep = Json.Str(d, "failed_step");
            s.Head = Json.Str(d, "head");
            s.Tier = Json.Str(d, "tier");
            s.Gpu = Json.StrList(d, "gpu");
            s.GpuKnown = s.Gpu != null;
            s.Ai = Json.Bool(d, "ai");
            s.Updated = Json.Str(d, "updated");
            s.Holder = Json.Str(d, "holder") ?? "";
            s.HostPid = Json.Int(d, "host_pid") ?? 0;
            return s;
        }
    }

    public static class Sha256
    {
        public static bool IsHex(string s)
        {
            if (s == null || s.Length != 64) return false;
            foreach (char c in s) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
            return true;
        }

        public static string OfFile(string path)
        {
            using (var sha = SHA256.Create())
            using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20))
            {
                return Hex(sha.ComputeHash(f));
            }
        }

        public static string Hex(byte[] b)
        {
            var sb = new StringBuilder(b.Length * 2);
            foreach (byte x in b) sb.Append(x.ToString("x2"));
            return sb.ToString();
        }
    }

    // The setup code's trip from the host to the installer. RSACng keys are
    // ephemeral (never in a key container on disk); OAEP with SHA-256.
    public sealed class SetupCodeKey : IDisposable
    {
        readonly RSACng rsa;

        public SetupCodeKey()
        {
            rsa = new RSACng(2048);
        }

        public string PublicKey
        {
            get
            {
                RSAParameters p = rsa.ExportParameters(false);
                return Convert.ToBase64String(p.Modulus) + "." + Convert.ToBase64String(p.Exponent);
            }
        }

        public string Decrypt(string base64)
        {
            byte[] plain = rsa.Decrypt(Convert.FromBase64String(base64), RSAEncryptionPadding.OaepSHA256);
            try
            {
                return Encoding.UTF8.GetString(plain);
            }
            finally
            {
                Array.Clear(plain, 0, plain.Length);
            }
        }

        public static string Encrypt(string publicKey, string text)
        {
            if (publicKey == null) throw new ArgumentNullException("publicKey");
            string[] parts = publicKey.Split('.');
            if (parts.Length != 2) throw new FormatException("public key");
            var p = new RSAParameters();
            p.Modulus = Convert.FromBase64String(parts[0]);
            p.Exponent = Convert.FromBase64String(parts[1]);
            if (p.Modulus.Length < 256) throw new FormatException("public key too small");
            using (var rsa = new RSACng())
            {
                rsa.ImportParameters(p);
                byte[] plain = Encoding.UTF8.GetBytes(text);
                try
                {
                    return Convert.ToBase64String(rsa.Encrypt(plain, RSAEncryptionPadding.OaepSHA256));
                }
                finally
                {
                    Array.Clear(plain, 0, plain.Length);
                }
            }
        }

        public void Dispose()
        {
            rsa.Dispose();
        }
    }
}
