<#
.SYNOPSIS
Make this Windows laptop or PC a Domovoi room satellite: for the signed-in
user only, with no administrator rights.

.DESCRIPTION
Spare-device satellites on Windows (2026-10-13). Run by the one-line command
the dashboard shows under Settings > Satellites > Add a spare device >
Windows, which has already checked this bundle's SHA-256 before running it:

    powershell -NoProfile -ExecutionPolicy Bypass -File install-windows.ps1 'domovoi-room:1?core=...&fp=...&room=...'
    powershell -NoProfile -ExecutionPolicy Bypass -File install-windows.ps1 -Uninstall [-Purge]

In order:
  1. Refuses a PC that runs the Domovoi server (its Windows service or
     install record, its data folder, a server's files in this account, or
     a server answering on this PC): "use this PC as a room" will come with
     the server's own tray instead.
  2. Before any download, refuses a PC without Microsoft's Visual C++
     runtime (14.44 or newer: the wake word's onnxruntime needs it and
     doesn't carry it), saying where to get it, and says plainly when Smart
     App Control may block parts of the satellite. Then it gets the
     embeddable Python that satellite\windows-runtime.json (in this
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
runs the Domovoi server; 6 this PC lacks Microsoft's Visual C++ runtime;
1 anything else.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string]$Payload = '',
    [ValidateSet('', 'automatic', 'ask')] [string]$Permissions = '',
    [ValidateSet('ask', 'no', 'yes')] [string]$Power = 'ask',
    [switch]$Yes,
    [switch]$NoStart,
    [int]$Wait = 900,
    [string]$InstallRoot = '',
    [switch]$Uninstall,
    [switch]$Purge,
    # Not yet available (the owner's decision): put Microsoft's two C++
    # runtime DLLs, pinned in windows-runtime.json, beside the satellite's
    # Python instead of asking for the system-wide redistributable.
    [switch]$LocalVcRuntime,
    # Tests only, and only ever given on the command line (nothing in an
    # install or in the dashboard's command passes them; no environment
    # variable is read): the Run/Uninstall values and the Start-menu
    # shortcut go to this JSON file instead of the registry; the core-PC
    # signals are pointed elsewhere (a JSON file naming them); and the
    # window is treated as elevated (the refusal path only).
    [string]$TestRegistryFile = '',
    [string]$TestCorePcSignals = '',
    [switch]$TestAsAdministrator,
    # Tests only: accept an older C++ runtime (a test PC's own).
    [string]$TestVcRuntimeMinimum = ''
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
    Fail "give the enrolment code ('domovoi-room:1?...') from the dashboard: Settings > Satellites > Add a spare device" 2
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

# --- what Windows must already have --------------------------------------

# Microsoft's C++ runtime: onnxruntime (the wake word) imports msvcp140.dll
# and msvcp140_1.dll, at least the version it was built with, and does not
# carry them; the embeddable Python brings only vcruntime140, and Windows
# doesn't always have them. satellite\engine_platform.py VC_RUNTIME_* holds
# the same (a test keeps the two equal), and the engine checks again at
# every start.
$VcRuntimeMinimum = [version]'14.44'

function Get-VcRuntimeProblem([string]$Dir, [version]$Minimum) {
    $missing = @()
    $old = @()
    foreach ($name in 'msvcp140.dll', 'msvcp140_1.dll') {
        $f = Join-Path $Dir $name
        if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { $missing += $name; continue }
        $v = [Diagnostics.FileVersionInfo]::GetVersionInfo($f)
        if ((New-Object Version($v.FileMajorPart, $v.FileMinorPart)) -lt $Minimum) {
            $old += ('{0} {1}.{2}' -f $name, $v.FileMajorPart, $v.FileMinorPart)
        }
    }
    if ($missing.Count -eq 0 -and $old.Count -eq 0) { return $null }
    if ($missing.Count -gt 0) {
        $what = "Microsoft's Visual C++ runtime is not on this PC ($($missing -join ', ') missing)"
    } else {
        $what = "Microsoft's Visual C++ runtime on this PC is older than $Minimum ($($old -join ', '))"
    }
    return ("$what, and the satellite's wake word can't load without it. Install " +
            '"Microsoft Visual C++ Redistributable (x64)" from https://aka.ms/vc14/vc_redist.x64.exe ' +
            '(it asks for an administrator once), then paste the command again. Nothing was changed.')
}

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

if (-not $LocalVcRuntime) {
    $vcMinimum = if ($TestVcRuntimeMinimum) { [version]$TestVcRuntimeMinimum } else { $VcRuntimeMinimum }
    $vc = Get-VcRuntimeProblem (Join-Path $env:SystemRoot 'System32') $vcMinimum
    if ($vc) { Fail $vc 6 }
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
    if ($LocalVcRuntime) { $argv += '--local-vc-runtime' }
    if ($TestRegistryFile) { $argv += @('--registry-file', $TestRegistryFile) }
    & (Join-Path $py 'python.exe') @argv
    $code = $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
Leave $code
