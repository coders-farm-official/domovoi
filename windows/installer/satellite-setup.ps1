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
                                       fingerprint (GET /v1/satellite-setup/codes/C)
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

The server must be on the home network (the enrolment code's own rule:
a private address, or a name ending in .local, .home.arpa, .lan or
.internal) or this PC itself: a Setup never downloads and runs code from
anywhere else, whatever a file name or a person types.

Results go to -Out as UTF-8 key=value lines (the wizard reads them; a
console's code page can't carry a room called Kueche with an umlaut).
Progress goes to stdout, a line at a time. The approval code is written
to -Out only, never to stdout: the wizard shows it and logs nothing of it.

Exit status: 0 done; 2 a code, server or file name that can't be used; 3
the server did not answer as asked; 4 the installer refused or failed
(-Out says why: install_exit is install-windows.ps1's own status).
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
    if ($c.Length -ne 8) { return '' }
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
    $m = [regex]::Match($leaf, '^DomovoiSatelliteSetup_([0-9A-Za-z]{8})(?:_([A-Za-z0-9.-]{1,253})_([0-9]{1,5}))?(?: ?\([0-9]{1,3}\))?\.exe$',
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

function Get-Answer([string]$Url) {
    # The body as UTF-8 text, decoded here (Windows PowerShell 5.1 guesses
    # a charset from the headers, and guesses wrong for JSON), and the
    # status. A refusal's body comes back too: the server says why.
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $Timeout -MaximumRedirection 0
        return @{ status = [int]$r.StatusCode; body = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) }
    } catch [Net.WebException] {
        $resp = $_.Exception.Response
        if ($null -eq $resp) { return @{ status = 0; body = $_.Exception.Message } }
        # Windows PowerShell has already read the body: it is in ErrorDetails.
        $text = ''
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            return @{ status = [int]$resp.StatusCode; body = [string]$_.ErrorDetails.Message }
        }
        try {
            $sr = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
            $text = $sr.ReadToEnd()
        } catch { }
        return @{ status = [int]$resp.StatusCode; body = $text }
    }
}

function Get-Detail([string]$Body) {
    try {
        $d = ($Body | ConvertFrom-Json).detail
        if ($d -is [string]) { return $d }
    } catch { }
    return ''
}

function Resolve-Code([string]$Base, [string]$SetupCode) {
    $a = Get-Answer ($Base + '/v1/satellite-setup/codes/' + $SetupCode)
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
    $ok = ($doc.PSObject.Properties['format'] -and $doc.format -eq 1 -and
           $doc.PSObject.Properties['payload'] -and ([string]$doc.payload).StartsWith('domovoi-room:') -and
           $doc.PSObject.Properties['bundle_sha256'] -and ([string]$doc.bundle_sha256) -match '^[0-9a-fA-F]{64}$')
    if (-not $ok) { Fail "the server at $Base did not answer like a Domovoi server" 3 }
    return $doc
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
if (-not $setupCode) { Fail 'a setup code is 8 letters and digits, like K7M4-QX2P (it is on the dashboard)' 2 }

$doc = Resolve-Code $base $setupCode
$Result['server'] = $base
$Result['code'] = $setupCode
$Result['room'] = [string]$doc.room
$Result['room_id'] = [string]$doc.room_id
$Result['core'] = [string]$doc.core
$Result['fingerprint'] = [string]$doc.fingerprint
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
    try {
        Invoke-WebRequest -UseBasicParsing -Uri ($base + $BundlePath) -OutFile $tgz -TimeoutSec 600 -MaximumRedirection 0
    } catch {
        Fail "could not download the satellite from $base`: $($_.Exception.Message)" 3
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $tgz).Hash -ne ([string]$doc.bundle_sha256).ToUpperInvariant()) {
        Fail ('this is not the download the server announced (Domovoi was updated since, or it was changed ' +
              'on the way). Nothing was run: start Setup again.') 3
    }
    Say '  checked against the SHA-256 the server gave for this code'
    & "$env:SystemRoot\System32\tar.exe" -xzf $tgz -C $dir
    if ($LASTEXITCODE -ne 0) { Fail 'could not unpack the satellite download' 4 }
    $script = Join-Path $dir 'satellite\scripts\install-windows.ps1'
    if (-not (Test-Path -LiteralPath $script)) { Fail 'the satellite download has no Windows installer' 4 }

    $argv = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $script, [string]$doc.payload,
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
