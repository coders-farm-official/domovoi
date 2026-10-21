<#
.SYNOPSIS
The Windows satellite Setup's worker: everything with a rule in it, here
where the tests can run it.

.DESCRIPTION
DomovoiSatelliteSetup.exe (DomovoiSatelliteSetup.iss, 2026-10-21) is only
the wizard. It packs this script, runs it from its own temporary folder
with Windows PowerShell, and shows what it prints. Three ways:

    -Name <file name>                  the setup code and the server the
                                       Setup's own file name carries (a
                                       download from the dashboard's link
                                       is named so), when it carries them
    -Resolve -Server S -Code C         what server S says code C names: the
                                       room, its enrolment payload, the
                                       fingerprint (GET /v1/satellite-setup/codes/C),
                                       held to the code (below)
    -Install -Server S -Code C -Permissions ask|automatic -Power no|standby|lid|yes
                                       what the dashboard's PowerShell
                                       command does, in the same order:
                                       download the satellite bundle from S,
                                       run nothing unless its SHA-256 is the
                                       one S gave for the code, unpack it
                                       with Windows' own tar.exe, and run
                                       satellite\scripts\install-windows.ps1
                                       with the payload (which refuses the
                                       PC that runs the Domovoi server, makes
                                       the server prove its fingerprint
                                       before anything is written, and
                                       installs for this account only); then
                                       wait up to -ApprovalWait seconds for
                                       the approval code to show it.

Whatever answers is held to the code (review of wf/win-sat-installer,
round 1). The code is 60 bits of the SHA-256 of the room's enrolment
payload (domovoi/satellite_setup.py code_for), and the payload names the
server's identity fingerprint. So the answer must carry a payload with
this code, the public key that fingerprint is of, and that key's Ed25519
signature over this run's fresh challenge, the code, the payload and the
bundle's SHA-256. Only the server the code was made on can give it: a
rogue host, a renamed download pointing elsewhere, or something on the
path of this plain-HTTP talk can't sign as that server, nor find another
payload with the same code. Then the bundle must have that signed
SHA-256. Every request goes direct (never through the system's proxy) and
follows no redirect.

The server must also be on the home network (the enrolment code's own
rule: a private address, or a name ending in .local, .home.arpa, .lan or
.internal) or this PC itself. A name is taken by its ending, not resolved
and checked (a router may hand out such names for anything): the
signature above, not the name, is what makes it the household's server.

Results go to -Out as UTF-8 key=value lines (the wizard reads them; a
console's code page can't carry a room called Kueche with an umlaut).
Progress goes to stdout, a line at a time. The approval code is written
to -Out only, never to stdout: the wizard shows it and logs nothing of it.

Exit status: 0 done; 2 a code, server or file name that can't be used; 3
the server did not answer as asked, or did not prove it is the server the
code is for; 4 the installer refused or failed (-Out says why:
install_exit is install-windows.ps1's own status).
#>
[CmdletBinding()]
param(
    [string]$Name = '',
    [switch]$Resolve,
    [switch]$Install,
    [string]$Server = '',
    [string]$Code = '',
    [ValidateSet('ask', 'automatic')] [string]$Permissions = 'ask',
    [ValidateSet('no', 'standby', 'lid', 'yes')] [string]$Power = 'no',
    [string]$Out = '',
    [string]$Work = '',
    [string]$InstallRoot = '',
    [int]$ApprovalWait = 90,
    [int]$Timeout = 60
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$CodeAlphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
$BundlePath = '/v1/satellite-code/bundle.tar.gz'
$Result = [ordered]@{}

function Say([string]$Text = '') { [Console]::Out.WriteLine($Text) }

function Save-Result {
    if (-not $Out) { return }
    $lines = foreach ($k in $Result.Keys) { $k + '=' + (([string]$Result[$k]) -replace '[\r\n]+', ' ') }
    [IO.File]::WriteAllLines($Out, [string[]]@($lines), (New-Object Text.UTF8Encoding($false)))
}

function Finish([int]$Status) {
    Save-Result
    exit $Status
}

function Fail([string]$Message, [int]$Status) {
    $Result['result'] = 'error'
    $Result['error'] = $Message
    Save-Result
    [Console]::Error.WriteLine("setup: $Message")
    exit $Status
}

# --- the rules ------------------------------------------------------------

function ConvertTo-SetupCode([string]$Text) {
    # Case, spaces and dashes don't matter; O, I and L read as 0, 1, 1
    # (Crockford's base 32, the server's alphabet). '' when it can't be one.
    if ($null -eq $Text -or $Text.Length -gt 32) { return '' }
    $c = ($Text -replace '[\s-]', '').ToUpperInvariant().Replace('O', '0').Replace('I', '1').Replace('L', '1')
    if ($c.Length -ne 12) { return '' }
    foreach ($ch in $c.ToCharArray()) { if ($CodeAlphabet.IndexOf($ch) -lt 0) { return '' } }
    return $c
}

function Test-SetupHost([string]$HostName) {
    # The enrolment code's rule (satellite\enrolment.py is_home_network_host)
    # plus this PC itself: loopback is no way out of the house.
    $h = $HostName.ToLowerInvariant().Trim('[', ']')
    $ip = $null
    if ([Net.IPAddress]::TryParse($h, [ref]$ip)) {
        if ([Net.IPAddress]::IsLoopback($ip)) { return $true }
        $b = $ip.GetAddressBytes()
        if ($ip.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork) {
            return ($b[0] -eq 10) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or
                   ($b[0] -eq 192 -and $b[1] -eq 168)
        }
        return (($b[0] -band 0xFE) -eq 0xFC) -or ($b[0] -eq 0xFE -and ($b[1] -band 0xC0) -eq 0x80)
    }
    if ($h -eq 'localhost') { return $true }
    foreach ($suffix in '.local', '.home.arpa', '.lan', '.internal') {
        if ($h.EndsWith($suffix) -and $h.Length -gt $suffix.Length) { return $true }
    }
    return $false
}

function ConvertTo-ServerUrl([string]$Text) {
    # What a person types in the "Domovoi server" box, as a base URL:
    # "192.168.1.20" is http://192.168.1.20:6370 (the core's port), a port
    # or an https:// URL as given. '' when it isn't one, or isn't on the
    # home network.
    $t = ([string]$Text).Trim()
    if (-not $t -or $t.Length -gt 256) { return '' }
    if ($t -notmatch '^[A-Za-z][A-Za-z0-9+.-]*://') { $t = 'http://' + $t }
    try { $u = New-Object Uri($t) } catch { return '' }
    if (-not $u.IsAbsoluteUri -or ($u.Scheme -ne 'http' -and $u.Scheme -ne 'https')) { return '' }
    if ($u.UserInfo -or ($u.AbsolutePath -ne '/') -or $u.Query -or $u.Fragment) { return '' }
    if (-not (Test-SetupHost $u.Host)) { return '' }
    $port = $u.Port
    if ($u.IsDefaultPort -and $u.Scheme -eq 'http') { $port = 6370 }
    $hostPart = $u.Host
    if ($u.HostNameType -eq [UriHostNameType]::IPv6 -and -not $hostPart.StartsWith('[')) { $hostPart = "[$hostPart]" }
    return $u.Scheme + '://' + $hostPart.ToLowerInvariant() + ':' + $port
}

function Read-SetupName([string]$FileName) {
    # DomovoiSatelliteSetup_<code>[_<host>_<port>].exe, as the server names
    # a download from the dashboard's link (domovoi/satellite_setup.py
    # download_name); a browser's " (1)" before .exe is fine.
    $leaf = [IO.Path]::GetFileName($FileName)
    $m = [regex]::Match($leaf, '^DomovoiSatelliteSetup_([0-9A-Za-z]{12})(?:_([A-Za-z0-9.-]{1,253})_([0-9]{1,5}))?(?: ?\([0-9]{1,3}\))?\.exe$',
                        [Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $found = @{ code = ''; server = '' }
    if (-not $m.Success) { return $found }
    $found.code = ConvertTo-SetupCode $m.Groups[1].Value
    if ($m.Groups[2].Success -and [int]$m.Groups[3].Value -ge 1 -and [int]$m.Groups[3].Value -le 65535) {
        $found.server = ConvertTo-ServerUrl ('http://' + $m.Groups[2].Value + ':' + $m.Groups[3].Value)
    }
    return $found
}

# --- talking to the server -----------------------------------------------

# Ed25519 (the server identity's key: .NET Framework has none of its own),
# the code of a payload, and HTTP that never uses the system proxy.
$ProofSource = @'
using System;
using System.IO;
using System.Net;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;

namespace DomovoiSatelliteSetup
{
    // Ed25519 verification (RFC 8032), the twin of satellite\_ed25519.py's
    // verify: .NET Framework has no Ed25519 of its own. Public data only,
    // so constant time does not matter.
    public static class Ed25519
    {
        static readonly BigInteger P = BigInteger.Pow(2, 255) - 19;
        static readonly BigInteger L = BigInteger.Pow(2, 252) + BigInteger.Parse("27742317777372353535851937790883648493");
        static readonly BigInteger D = Mod(-121665 * Inv(121666));
        static readonly BigInteger SqrtM1 = BigInteger.ModPow(2, (P - 1) / 4, P);
        static readonly BigInteger[] Base;

        static Ed25519()
        {
            BigInteger y = Mod(4 * Inv(5));
            BigInteger x = RecoverX(y, 0);
            Base = new[] { x, y, BigInteger.One, Mod(x * y) };
        }

        static BigInteger Mod(BigInteger a) { BigInteger r = a % P; return r.Sign < 0 ? r + P : r; }
        static BigInteger Inv(BigInteger a) { return BigInteger.ModPow(Mod(a), P - 2, P); }

        static BigInteger RecoverX(BigInteger y, int sign)
        {
            if (y >= P) return -1;
            BigInteger xx = Mod((y * y - 1) * Inv(D * y * y + 1));
            BigInteger x = BigInteger.ModPow(xx, (P + 3) / 8, P);
            if (Mod(x * x - xx) != 0) x = Mod(x * SqrtM1);
            if (Mod(x * x - xx) != 0) return -1;
            if (x.IsZero && sign == 1) return -1;
            if ((int)(x % 2) != sign) x = P - x;
            return x;
        }

        static BigInteger[] Add(BigInteger[] p, BigInteger[] q)
        {
            BigInteger a = Mod((p[1] - p[0]) * (q[1] - q[0]));
            BigInteger b = Mod((p[1] + p[0]) * (q[1] + q[0]));
            BigInteger c = Mod(2 * p[3] * q[3] * D);
            BigInteger dd = Mod(2 * p[2] * q[2]);
            BigInteger e = b - a, f = dd - c, g = dd + c, h = b + a;
            return new[] { Mod(e * f), Mod(g * h), Mod(f * g), Mod(e * h) };
        }

        static BigInteger[] Mult(BigInteger[] point, BigInteger e)
        {
            BigInteger[] r = { BigInteger.Zero, BigInteger.One, BigInteger.One, BigInteger.Zero };
            while (e > 0)
            {
                if (!e.IsEven) r = Add(r, point);
                point = Add(point, point);
                e >>= 1;
            }
            return r;
        }

        static BigInteger FromLittle(byte[] data, int offset, int count)
        {
            byte[] le = new byte[count + 1];
            Array.Copy(data, offset, le, 0, count);
            return new BigInteger(le);
        }

        static byte[] Encode(BigInteger[] point)
        {
            BigInteger zi = Inv(point[2]);
            BigInteger x = Mod(point[0] * zi), y = Mod(point[1] * zi);
            BigInteger v = y | ((x & 1) << 255);
            byte[] raw = v.ToByteArray();
            byte[] o = new byte[32];
            Array.Copy(raw, o, Math.Min(32, raw.Length));
            return o;
        }

        static BigInteger[] Decode(byte[] data, int offset)
        {
            BigInteger v = FromLittle(data, offset, 32);
            int sign = (int)((v >> 255) & 1);
            BigInteger y = v & ((BigInteger.One << 255) - 1);
            BigInteger x = RecoverX(y, sign);
            if (x.Sign < 0) return null;
            return new[] { x, y, BigInteger.One, Mod(x * y) };
        }

        public static bool Verify(byte[] pub, byte[] message, byte[] sig)
        {
            if (pub == null || sig == null || message == null || pub.Length != 32 || sig.Length != 64) return false;
            BigInteger[] a = Decode(pub, 0), r = Decode(sig, 0);
            if (a == null || r == null) return false;
            BigInteger s = FromLittle(sig, 32, 32);
            if (s >= L) return false;
            byte[] h;
            using (var sha = SHA512.Create())
            {
                var buf = new byte[64 + message.Length];
                Array.Copy(sig, 0, buf, 0, 32);
                Array.Copy(pub, 0, buf, 32, 32);
                Array.Copy(message, 0, buf, 64, message.Length);
                h = sha.ComputeHash(buf);
            }
            BigInteger k = FromLittle(h, 0, 64) % L;
            byte[] lhs = Encode(Mult(Base, s));
            byte[] rhs = Encode(Add(r, Mult(a, k)));
            for (int i = 0; i < 32; i++) if (lhs[i] != rhs[i]) return false;
            return true;
        }
    }

    public static class Proof
    {
        public const string Alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

        // satellite\server_identity.py fingerprint_for.
        public static string Fingerprint(byte[] pub)
        {
            using (var sha = SHA256.Create())
                return "SHA256:" + Convert.ToBase64String(sha.ComputeHash(pub)).TrimEnd('=');
        }

        // domovoi\satellite_setup.py code_for: 60 bits of SHA-256(payload).
        public static string CodeFor(string payload)
        {
            byte[] h;
            using (var sha = SHA256.Create()) h = sha.ComputeHash(Encoding.UTF8.GetBytes(payload));
            ulong top = 0;
            for (int i = 0; i < 8; i++) top = (top << 8) | h[i];
            top >>= 4;
            var sb = new StringBuilder();
            for (int i = 0; i < 12; i++) sb.Append(Alphabet[(int)((top >> (5 * (11 - i))) & 31)]);
            return sb.ToString();
        }

        public static string NewChallenge()
        {
            byte[] b = new byte[16];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(b);
            var sb = new StringBuilder();
            foreach (byte x in b) sb.Append(x.ToString("x2"));
            return sb.ToString();
        }
    }

    public sealed class Answer
    {
        public int Status;
        public byte[] Body;
        public string Error;
        public string Text { get { return Body == null ? "" : Encoding.UTF8.GetString(Body); } }
    }

    // Plain HTTP on the LAN, as the Setup wants it: never through the
    // system's proxy (a WPAD answer on the network could otherwise send
    // every request elsewhere), no redirects, at most Limit bytes.
    public static class Http
    {
        // file: where a 200's body goes; null or empty (PowerShell's $null) for memory.
        public static Answer Get(string url, string file, long limit, int timeoutMs)
        {
            var a = new Answer();
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Proxy = null;
                req.AllowAutoRedirect = false;
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                HttpWebResponse resp;
                try { resp = (HttpWebResponse)req.GetResponse(); }
                catch (WebException e)
                {
                    resp = e.Response as HttpWebResponse;
                    if (resp == null) { a.Error = e.Message; return a; }
                }
                using (resp)
                using (Stream s = resp.GetResponseStream())
                {
                    a.Status = (int)resp.StatusCode;
                    Stream o = (!string.IsNullOrEmpty(file) && a.Status == 200) ? (Stream)File.Create(file) : new MemoryStream();
                    using (o)
                    {
                        byte[] buf = new byte[1 << 16];
                        long total = 0;
                        int n;
                        while ((n = s.Read(buf, 0, buf.Length)) > 0)
                        {
                            total += n;
                            if (total > limit) { a.Error = "the answer is larger than " + limit + " bytes"; a.Status = 0; return a; }
                            o.Write(buf, 0, n);
                        }
                        var ms = o as MemoryStream;
                        if (ms != null) a.Body = ms.ToArray();
                    }
                }
            }
            catch (Exception e)
            {
                a.Status = 0;
                a.Error = e.Message;
            }
            return a;
        }
    }
}
'@

function Use-Proof {
    if (-not ('DomovoiSatelliteSetup.Proof' -as [type])) {
        Add-Type -TypeDefinition $ProofSource -ReferencedAssemblies System.Numerics -ErrorAction Stop
    }
}

function Get-Answer([string]$Url) {
    # The body as UTF-8 text and the status (0: no answer). A refusal's
    # body comes back too: the server says why.
    $a = [DomovoiSatelliteSetup.Http]::Get($Url, $null, 1MB, $Timeout * 1000)
    $body = if ($a.Status -eq 0) { [string]$a.Error } else { $a.Text }
    return @{ status = [int]$a.Status; body = $body }
}

function Get-Detail([string]$Body) {
    try {
        $d = ($Body | ConvertFrom-Json).detail
        if ($d -is [string]) { return $d }
    } catch { }
    return ''
}

function Get-Field($Doc, [string]$Field) {
    $p = $Doc.PSObject.Properties[$Field]
    if ($p) { return [string]$p.Value } else { return '' }
}

function Resolve-Code([string]$Base, [string]$SetupCode) {
    Use-Proof
    $challenge = [DomovoiSatelliteSetup.Proof]::NewChallenge()
    $a = Get-Answer ($Base + '/v1/satellite-setup/codes/' + $SetupCode + '?challenge=' + $challenge)
    if ($a.status -eq 0) {
        Fail ("no Domovoi server answered at $Base ($($a.body)). Check the address on the dashboard, and " +
              'that this PC is on the same network.') 3
    }
    if ($a.status -eq 404) {
        $why = Get-Detail $a.body
        if (-not $why) { $why = 'this server has no setup codes (is it a Domovoi server, and up to date?)' }
        Fail "the server at $Base does not know the code $SetupCode`: $why. Make a new code on the dashboard." 2
    }
    if ($a.status -ne 200) {
        $why = Get-Detail $a.body
        Fail "the server at $Base answered $($a.status)$(if ($why) { ': ' + $why }). Try again in a minute." 3
    }
    try { $doc = $a.body | ConvertFrom-Json } catch { Fail "the server at $Base did not answer like a Domovoi server" 3 }
    $payload = Get-Field $doc 'payload'
    $sha = Get-Field $doc 'bundle_sha256'
    $ok = ((Get-Field $doc 'format') -eq '1' -and $payload.StartsWith('domovoi-room:') -and $sha -match '^[0-9a-f]{64}$')
    if (-not $ok) { Fail "the server at $Base did not answer like a Domovoi server" 3 }

    # The proof: this payload is the one the code is for, and the server
    # holds the key its fingerprint names, signing this run's challenge.
    $notOurs = ("the server at $Base did not prove it is the Domovoi server this code was made on, so nothing " +
                'was installed. Check the address and the code on the dashboard (Settings > Satellites); if ' +
                'they are right, something on your network is answering in its place.')
    if ([DomovoiSatelliteSetup.Proof]::CodeFor($payload) -ne $SetupCode) { Fail $notOurs 3 }
    $m = [regex]::Match($payload, '[?&]fp=([^&#]*)')
    if (-not $m.Success) { Fail $notOurs 3 }
    $fp = [Uri]::UnescapeDataString($m.Groups[1].Value)
    try {
        $pub = [Convert]::FromBase64String((Get-Field $doc 'public_key'))
        $sig = [Convert]::FromBase64String((Get-Field $doc 'signature'))
    } catch { Fail $notOurs 3 }
    if ((Get-Field $doc 'algorithm') -ne 'ed25519' -or [DomovoiSatelliteSetup.Proof]::Fingerprint($pub) -ne $fp) {
        Fail $notOurs 3
    }
    $message = [Text.Encoding]::UTF8.GetBytes(("domovoi-satellite-setup-v1`n" + $challenge + "`n" + $SetupCode + "`n" +
                                               $payload + "`n" + $sha))
    if (-not [DomovoiSatelliteSetup.Ed25519]::Verify($pub, $message, $sig)) { Fail $notOurs 3 }
    return @{ doc = $doc; payload = $payload; sha = $sha; fp = $fp }
}

# --- the three ways --------------------------------------------------------

if ($Name) {
    $found = Read-SetupName $Name
    $Result['code'] = $found.code
    $Result['server'] = $found.server
    Finish 0
}

if (-not ($Resolve -or $Install)) { Fail 'say -Name, -Resolve or -Install' 2 }

$base = ConvertTo-ServerUrl $Server
if (-not $base) {
    Fail ('that is not the address of a Domovoi server on your home network. Type it as the dashboard ' +
          'shows it, for example 192.168.1.20.') 2
}
$setupCode = ConvertTo-SetupCode $Code
if (-not $setupCode) { Fail 'a setup code is 12 letters and digits, like K7M4-QX2P-9TRA (it is on the dashboard)' 2 }

$proven = Resolve-Code $base $setupCode
$doc = $proven.doc
$Result['server'] = $base
$Result['code'] = $setupCode
$Result['room'] = Get-Field $doc 'room'
$Result['room_id'] = Get-Field $doc 'room_id'
$Result['core'] = Get-Field $doc 'core'
# The fingerprint the server just proved (the one inside the code's payload).
$Result['fingerprint'] = $proven.fp
if ($Resolve) {
    $Result['result'] = 'ok'
    Finish 0
}

# Install: the PowerShell command's steps, then install-windows.ps1.
if (-not $Work) { $Work = $env:TEMP }
$dir = Join-Path $Work ('domovoi-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $dir
try {
    Say "Downloading the satellite from $base ..."
    $tgz = Join-Path $dir 's.tgz'
    $got = [DomovoiSatelliteSetup.Http]::Get(($base + $BundlePath), $tgz, 256MB, 600000)
    if ($got.Status -ne 200) {
        Fail "could not download the satellite from $base`: $(if ($got.Error) { $got.Error } else { 'HTTP ' + $got.Status })" 3
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $tgz).Hash -ne $proven.sha.ToUpperInvariant()) {
        Fail ('this is not the download the server signed for this code (Domovoi was updated since, or it was ' +
              'changed on the way). Nothing was run: start Setup again.') 3
    }
    Say '  checked against the SHA-256 the server signed for this code'
    & "$env:SystemRoot\System32\tar.exe" -xzf $tgz -C $dir
    if ($LASTEXITCODE -ne 0) { Fail 'could not unpack the satellite download' 4 }
    $script = Join-Path $dir 'satellite\scripts\install-windows.ps1'
    if (-not (Test-Path -LiteralPath $script)) { Fail 'the satellite download has no Windows installer' 4 }

    $argv = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $script, $proven.payload,
              '-Yes', '-Permissions', $Permissions, '-Power', $Power, '-Wait', '0')
    if ($InstallRoot) { $argv += @('-InstallRoot', $InstallRoot) }
    $last = ''
    $ErrorActionPreference = 'Continue'
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @argv 2>&1 | ForEach-Object {
        $line = if ($_ -is [Management.Automation.ErrorRecord]) { $_.Exception.Message } else { [string]$_ }
        if ($line -match '^install: (.+)$') { $last = $Matches[1] }
        Say $line
    }
    $status = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    $Result['install_exit'] = $status
    if ($status -ne 0) {
        if (-not $last) { $last = "the installer stopped (status $status)" }
        Fail $last 4
    }
} finally {
    $ErrorActionPreference = 'Continue'
    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
}
$ErrorActionPreference = 'Stop'

# The approval code, from the satellite itself (its control channel), for
# the wizard's last page. The tray and a notification show it too.
$root = if ($InstallRoot) { $InstallRoot } else { Join-Path $env:LOCALAPPDATA 'Domovoi\Satellite' }
$py = Join-Path $root 'python\python.exe'
$Result['approved'] = '0'
if (Test-Path -LiteralPath $py) {
    Say 'Waiting for the satellite to get its approval code from the server...'
    # Whatever a satellite module resolves as ~ is the install folder, as
    # when the installer runs it (satellite\win_install.py).
    $env:USERPROFILE = $root
    $deadline = (Get-Date).AddSeconds($ApprovalWait)
    $ErrorActionPreference = 'Continue'
    while ((Get-Date) -lt $deadline) {
        $got = (& $py -m satellite.client --approval-code 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $got -match '^[0-9]{4,8}$') { $Result['approval_code'] = $got; break }
        $st = (& $py -m satellite.client --status --json 2>$null | Out-String)
        try {
            if (($st | ConvertFrom-Json).connection -eq 'connected') { $Result['approved'] = '1'; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    $ErrorActionPreference = 'Stop'
}
$Result['result'] = 'ok'
Finish 0
