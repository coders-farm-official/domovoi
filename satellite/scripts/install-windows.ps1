<#
.SYNOPSIS
Make this Windows laptop or PC a Domovoi room satellite: for the signed-in
user only, with no administrator rights.

.DESCRIPTION
Spare-device satellites on Windows (2026-10-13). Run by the one-line command
the dashboard shows under Settings > Rooms > Add a spare device >
Windows, which has already checked this bundle's SHA-256 before running it:

    powershell -NoProfile -ExecutionPolicy Bypass -File install-windows.ps1 'domovoi-room:1?core=...&fp=...&room=...'
    powershell -NoProfile -ExecutionPolicy Bypass -File install-windows.ps1 -Uninstall [-Purge]

DomovoiSatelliteSetup.exe (windows\installer, 2026-10-21) runs it the same way,
with -Yes, -Permissions, -Power and -Wait 0 from its own pages.

In order:
  0. Refuses a processor older than about 2009 (the satellite's numpy needs
     x86-64-v2: SSSE3, SSE4.2, POPCNT, CMPXCHG16B), before anything is
     downloaded.
  1. Refuses a PC that runs the Domovoi server (its Windows service or
     install record, its data folder, a server's files in this account, or
     a server answering on this PC): "use this PC as a room" will come with
     the server's own tray instead.
  2. Says plainly when Smart App Control may block parts of the satellite.
     Then it gets the embeddable Python that satellite\windows-runtime.json (in this
     same, already checked bundle) pins, from the Domovoi server or else
     python.org, and checks its SHA-256 and size.
  3. Hands over to satellite\win_install.py with that Python, which makes
     the server prove the fingerprint in the code BEFORE anything is
     written or stopped, then (stopping a satellite already installed there)
     installs into %LOCALAPPDATA%\Domovoi\Satellite, registers it for this
     user (sign-in, Installed apps, Start menu), asks before any power
     change, starts it and shows the approval code.

-ExecutionPolicy Bypass applies to this one PowerShell process: nothing
changes the PC's policy, and a policy set by Group Policy still wins.

Exit status: 0 done; 2 a usage error or a refused code; 3 the server did not
prove its identity; 4 not approved in time (it keeps waiting); 5 this PC
runs the Domovoi server; 1 anything else.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string]$Payload = '',
    [ValidateSet('', 'automatic', 'ask')] [string]$Permissions = '',
    # ask (one question each), no, yes (both), or one of them: standby (never
    # sleep while plugged in) or lid (keep running with the lid closed).
    [ValidateSet('ask', 'no', 'yes', 'standby', 'lid')] [string]$Power = 'ask',
    [switch]$Yes,
    [switch]$NoStart,
    [int]$Wait = 900,
    [string]$InstallRoot = '',
    [switch]$Uninstall,
    [switch]$Purge,
    # Tests only, and only ever given on the command line (nothing in an
    # install or in the dashboard's command passes them; no environment
    # variable is read): the Run/Uninstall values and the Start-menu
    # shortcut go to this JSON file instead of the registry; the core-PC
    # signals are pointed elsewhere (a JSON file naming them); the window
    # is treated as elevated (the refusal path only); and the satellite it
    # starts gets its supervisor's --test-audio-hooks (a file-fed
    # microphone, for an end-to-end run that lets the installer start it).
    [string]$TestRegistryFile = '',
    [string]$TestCorePcSignals = '',
    [switch]$TestAsAdministrator,
    [switch]$TestAudioHooks,
    # Tests only, like the ones above: the processor features (by
    # IsProcessorFeaturePresent number) to treat as missing, and the Windows
    # build to treat this one as.
    [int[]]$TestCpuMissing = @(),
    [int]$TestWindowsBuild = 0
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA 'Domovoi\Satellite' }
$BundleRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent    # holds satellite\

function Say([string]$Text = '') { Write-Host $Text }
function Leave([int]$Code) {
    # The one-liner's temporary folder goes, whatever happened, if this run
    # came from one (a domovoi-<32 hex> folder directly in %TEMP%).
    $tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
    if (-not $Uninstall -and (Split-Path $BundleRoot -Leaf) -match '^domovoi-[0-9a-f]{32}$' -and
        [IO.Path]::GetFullPath((Split-Path $BundleRoot -Parent)).TrimEnd('\') -eq $tempRoot) {
        Remove-Item -LiteralPath $BundleRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit $Code
}
function Fail([string]$Text, [int]$Code = 1) {
    [Console]::Error.WriteLine("install: $Text")
    Leave $Code
}

if ($Uninstall) {
    $extra = @{ Root = $InstallRoot }
    if ($Purge) { $extra.Purge = $true }
    if ($TestRegistryFile) { $extra.TestRegistryFile = $TestRegistryFile }
    & (Join-Path $PSScriptRoot 'uninstall-windows.ps1') @extra
    exit $LASTEXITCODE
}

# --- what this PC is -----------------------------------------------------

if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    Fail 'this satellite needs 64-bit Windows on an Intel or AMD processor (x64)' 2
}

function Get-MissingCpuFeatures {
    # The satellite's numpy (2.4 and later) runs only on an x86-64-v2
    # processor: SSSE3, SSE4.2, POPCNT and CMPXCHG16B (Intel from 2008-2009,
    # AMD from 2011; a Core 2 lacks SSE4.2 and POPCNT), and would otherwise
    # fail at its first use, after the whole install. Windows reports
    # CMPXCHG16B on every version, SSSE3 and SSE4.2 from Windows 10 2004
    # (build 19041) on, POPCNT not at all (every x64 processor with SSE4.2
    # has it). The names of what is missing; none when Windows can't say.
    $features = [ordered]@{ '14' = 'CMPXCHG16B' }
    $build = [Environment]::OSVersion.Version.Build
    if ($TestWindowsBuild) { $build = $TestWindowsBuild }
    if ($build -ge 19041) { $features['36'] = 'SSSE3'; $features['38'] = 'SSE4.2' }
    try {
        if (-not ('DomovoiSatellite.Cpu' -as [type])) {
            Add-Type -Namespace DomovoiSatellite -Name Cpu -ErrorAction Stop -MemberDefinition (
                '[DllImport("kernel32.dll")] public static extern bool IsProcessorFeaturePresent(uint feature);')
        }
    } catch {
        return @()
    }
    $missing = @()
    foreach ($f in $features.Keys) {
        $present = [DomovoiSatellite.Cpu]::IsProcessorFeaturePresent([uint32]$f)
        if ($TestCpuMissing -contains [int]$f) { $present = $false }
        if (-not $present) { $missing += $features[$f] }
    }
    return $missing
}
$cpuMissing = @(Get-MissingCpuFeatures)
if ($cpuMissing.Count -gt 0) {
    Fail ("this computer's processor is too old for Domovoi (needs roughly 2009 or newer; it has no " +
          ($cpuMissing -join ', ') + '), so nothing was installed') 2
}
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($TestAsAdministrator -or $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail ('this window runs as administrator. Open a normal PowerShell window (not "Run as administrator") ' +
          'and paste the command again: the satellite installs for you only and must never run with ' +
          'administrator rights.') 2
}

function Get-CorePcReason {
    # Every signal is a read: a service's existence, a registry key, a
    # folder, a file, one GET on this PC's own core port.
    $s = @{
        service     = 'DomovoiHost'
        registry    = 'HKLM:\SOFTWARE\Domovoi'
        programdata = (Join-Path $env:ProgramData 'Domovoi')
        # The folder a Domovoi server keeps its files in, in this account.
        profile     = (Join-Path $env:USERPROFILE '.domovoi')
        probe       = 'http://127.0.0.1:6370/v1/health'
    }
    if ($TestCorePcSignals) {
        # Tests only (see the parameter): point each signal somewhere else.
        $t = Get-Content -Raw -LiteralPath $TestCorePcSignals | ConvertFrom-Json
        foreach ($p in $t.PSObject.Properties) { $s[$p.Name] = [string]$p.Value }
    }
    if ($s.service -and (Get-Service -Name $s.service -ErrorAction SilentlyContinue)) {
        return "the Domovoi server's Windows service ($($s.service)) is installed on this PC"
    }
    if ($s.registry -and (Test-Path -LiteralPath $s.registry)) {
        return "the Domovoi server is installed on this PC ($($s.registry))"
    }
    if ($s.programdata -and (Test-Path -LiteralPath $s.programdata)) {
        return "the Domovoi server's data folder is on this PC ($($s.programdata))"
    }
    if ($s.profile) {
        # What only a server keeps there (satellite\pc_engine.py CORE_MARKERS).
        foreach ($marker in 'server-identity.json', 'device-token.txt', 'setup-code.txt') {
            $f = Join-Path $s.profile $marker
            if (Test-Path -LiteralPath $f) {
                return "a Domovoi server keeps its files in this account ($f)"
            }
        }
    }
    if ($s.probe) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri $s.probe -TimeoutSec 3
            if ($r.Content -match '"bot_name"' -or $r.Content -match '"identity"') {
                return "a Domovoi server answers on this PC ($($s.probe))"
            }
        } catch { }
    }
    return $null
}

$reason = Get-CorePcReason
if ($reason) {
    Fail ("$reason. A room satellite is never installed on the PC that runs the Domovoi server: " +
          'using this PC as a room will come with the server''s own tray. Nothing was changed.') 5
}

# --- the code from the dashboard -----------------------------------------

if (-not $Payload) {
    Fail "give the enrolment code ('domovoi-room:1?...') from the dashboard: Settings > Rooms > Add a spare device" 2
}

function Test-HomeCore([string]$Url) {
    # The same rule as satellite\enrolment.py (which checks again, fully):
    # a private address, or a name only a home network answers for.
    try { $u = [Uri]$Url } catch { return $false }
    if (-not $u.IsAbsoluteUri -or ($u.Scheme -ne 'http' -and $u.Scheme -ne 'https')) { return $false }
    $h = $u.DnsSafeHost.ToLowerInvariant()
    $ip = $null
    if ([Net.IPAddress]::TryParse($h, [ref]$ip)) {
        $b = $ip.GetAddressBytes()
        if ($ip.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork) {
            return ($b[0] -eq 10) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or
                   ($b[0] -eq 192 -and $b[1] -eq 168)
        }
        return (($b[0] -band 0xFE) -eq 0xFC) -or ($b[0] -eq 0xFE -and ($b[1] -band 0xC0) -eq 0x80)
    }
    foreach ($suffix in '.local', '.home.arpa', '.lan', '.internal') {
        if ($h.EndsWith($suffix) -and $h.Length -gt $suffix.Length) { return $true }
    }
    return $false
}

$m = [regex]::Match($Payload, '[?&]core=([^&#]*)')
$core = if ($m.Success) { [Uri]::UnescapeDataString($m.Groups[1].Value) } else { '' }
if (-not (Test-HomeCore $core)) {
    Fail 'that is not a usable enrolment code: its server address is missing or not on the home network' 2
}

# A satellite already installed here keeps running until the server has
# proven its fingerprint: win_install.py stops it just before it writes.

# --- what Windows may block ---------------------------------------------

# Smart App Control (Windows 11) checks every program file, DLLs included;
# some of the satellite's libraries are not signed. Read only, never changed.
function Get-SmartAppControlState {
    try {
        $v = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' `
                              -Name 'VerifiedAndReputablePolicyState' -ErrorAction Stop
        switch ([int]$v.VerifiedAndReputablePolicyState) {
            0 { return 'off' }
            1 { return 'on' }
            2 { return 'evaluation' }
        }
    } catch { }
    return 'unknown'
}

function Get-SmartAppControlNote([string]$State) {
    if ($State -eq 'on') {
        return ('Smart App Control is on. It may block parts of the satellite that are not signed yet ' +
                '(some of its Python libraries), with no "run anyway": if the satellite does not start, or ' +
                'Windows says it blocked part of an app, see "Smart App Control" in the guide ' +
                '(docs\SPARE_DEVICE_SATELLITES.md).')
    }
    if ($State -eq 'evaluation') {
        return ('Smart App Control is in evaluation mode. Windows may turn it on later, and then block ' +
                'parts of the satellite that are not signed yet; see "Smart App Control" in the guide ' +
                '(docs\SPARE_DEVICE_SATELLITES.md).')
    }
    return $null
}

$sac = Get-SmartAppControlNote (Get-SmartAppControlState)
if ($sac) { Say ''; Say "Note: $sac"; Say '' }

# --- the pinned Python ---------------------------------------------------

function Get-Pinned($Entry, [string]$Dest, [string]$Core) {
    $name = [string]$Entry.file
    $urls = @()
    if ($Core) { $urls += "$($Core.TrimEnd('/'))/v1/satellite-windows/files/$name" }
    $urls += [string]$Entry.url
    foreach ($u in $urls) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $Dest -TimeoutSec 900
            $item = Get-Item -LiteralPath $Dest
            if ($item.Length -eq [long]$Entry.size -and
                (Get-FileHash -Algorithm SHA256 -LiteralPath $Dest).Hash -eq [string]$Entry.sha256) {
                return $u
            }
            Say "  $u did not serve the pinned file; not using it"
        } catch {
            Say "  ${u}: $($_.Exception.Message)"
        }
        Remove-Item -LiteralPath $Dest -Force -ErrorAction SilentlyContinue
    }
    Fail "could not get $name from the Domovoi server or from its upstream; nothing was installed" 1
}

$lock = Get-Content -Raw -LiteralPath (Join-Path $BundleRoot 'satellite\windows-runtime.json') | ConvertFrom-Json
if ($lock.lock_version -ne 1 -or -not $lock.python) { Fail 'the bundle''s windows-runtime.json is not usable' 1 }
if ([string]$lock.python.url -notlike 'https://www.python.org/ftp/python/*') { Fail 'the bundle names an unexpected Python' 1 }

$stage = Join-Path $BundleRoot 'stage'
$null = New-Item -ItemType Directory -Force -Path $stage
$zip = Join-Path $stage ([string]$lock.python.file)
$py = Join-Path $stage 'python'
try {
    Say "Getting Python $($lock.python.version) (checked against its pinned SHA-256)..."
    $from = Get-Pinned $lock.python $zip $core
    Say "  from $from"
    if (Test-Path -LiteralPath $py) { Remove-Item -LiteralPath $py -Recurse -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $py)
    $pth = @(Get-ChildItem -LiteralPath $py -Filter 'python3*._pth')
    if ($pth.Count -ne 1) { Fail 'the Python download has no ._pth file' 1 }
    # This Python sees only its own library and the checked bundle (no
    # site: nothing from this account's user site-packages). UTF-8 without
    # a BOM, which is how Python reads a ._pth: the bundle's folder is under
    # %TEMP%, inside the account's own folder, whose name may be anything
    # (-Encoding Ascii turned "Jose" with an accent into "Jos?").
    $zipname = $pth[0].Name -replace '\._pth$', '.zip'
    [IO.File]::WriteAllLines($pth[0].FullName, [string[]]@($zipname, '.', $BundleRoot),
                             (New-Object Text.UTF8Encoding($false)))

    $argv = @('-m', 'satellite.win_install', 'install', '--payload', $Payload, '--bundle', $BundleRoot,
              '--staged-python', $py, '--root', $InstallRoot, '--power', $Power, '--wait', [string]$Wait)
    if ($Permissions) { $argv += @('--permissions', $Permissions) }
    if ($Yes) { $argv += '--yes' }
    if ($NoStart) { $argv += '--no-start' }
    if ($TestAudioHooks) { $argv += '--test-audio-hooks' }
    if ($TestRegistryFile) { $argv += @('--registry-file', $TestRegistryFile) }
    & (Join-Path $py 'python.exe') @argv
    $code = $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
Leave $code
