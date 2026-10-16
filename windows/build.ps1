<#
.SYNOPSIS
Builds the Windows installer (a preview).

.DESCRIPTION
1. DomovoiHost.exe and DomovoiSetupHelper.exe, with the C# compiler that
   ships inside .NET Framework 4.8.1 (C# 5; nothing to install).
2. The unit tests in tests\Tests.cs (the same compiler), run at once.
3. DomovoiSetup.exe, with Inno Setup 6's ISCC.exe.

Output goes to windows\build\ (ignored by git). Windows PowerShell 5.1.
Building changes nothing on this PC beyond that folder.

.PARAMETER SkipInstaller
Stop after the tests (no Inno Setup needed).

.PARAMETER Iscc
Path to ISCC.exe. Default: Inno Setup 6 per-user, then machine-wide.

.PARAMETER Sign
Code signing is on hold (owner decision 2026-10-11; PLAN.md section 10):
builds are unsigned. The switch and Invoke-Sign below are the place it
goes back in; today -Sign refuses.

.PARAMETER Pin
Before building, move every pin that belongs to the commit Setup installs,
together, in installer\pins.iss: DomovoiCommit (the commit), DomovoiChannel
(-Channel), ProvisionSha256 (that commit's scripts/linux/provision.sh, the
bytes GitHub serves; LF only), ProvisionBundled (off: provision.sh is
fetched from that commit) and DashboardTabRoutes (whether its dashboard has
the tab addresses). Takes a release tag (v1.0.0, v1.0.0-rc2: refs/tags only,
never a branch) or a full commit SHA, which must be in this clone and on
origin/main's history. The release scripts run it for the tag
(design-notes V1-RELEASE-PLAN 3.2); pins.iss then changes, and is
committed after the tag.

.PARAMETER Channel
With -Pin: stable (the default: the install follows releases only) or
main. stable needs a commit a v* tag names.

.PARAMETER PinOnly
With -Pin: rewrite the pins and stop, building nothing.

.PARAMETER PinsFile
With -Pin: the pins file to rewrite (default installer\pins.iss; the tests
pass a copy).
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipTests,
    [string]$Iscc,
    [switch]$Sign,
    [string]$Pin,
    [ValidateSet('stable', 'main')]
    [string]$Channel = 'stable',
    [switch]$PinOnly,
    [string]$PinsFile
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

# git's stdout as bytes (PowerShell 5.1 would decode a native program's
# output as text, and provision.sh's hash is of its exact bytes).
function Invoke-GitBytes([string]$Git, [string[]]$GitArgs) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Git
    $psi.Arguments = ($GitArgs | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' '
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $err = $p.StandardError.ReadToEndAsync()
    $ms = New-Object System.IO.MemoryStream
    $p.StandardOutput.BaseStream.CopyTo($ms)
    $p.WaitForExit()
    if ($p.ExitCode -ne 0) { throw ('git ' + ($GitArgs -join ' ') + ' failed: ' + $err.Result.Trim()) }
    return ,$ms.ToArray()
}

function Invoke-GitText([string]$Git, [string[]]$GitArgs) {
    $bytes = Invoke-GitBytes $Git $GitArgs
    return [System.Text.Encoding]::UTF8.GetString($bytes).Trim()
}

# Replace the one line matching $Pattern in $Text, or stop.
function Set-PinLine([string]$Text, [string]$Pattern, [string]$Line) {
    $rx = New-Object System.Text.RegularExpressions.Regex($Pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    $n = $rx.Matches($Text).Count
    if ($n -ne 1) { throw "pins.iss: expected one line matching $Pattern, found $n" }
    return $rx.Replace($Text, $Line.Replace('$', '$$'))
}

function Set-Pins([string]$Ref, [string]$Channel, [string]$File) {
    $repo = Split-Path -Parent $here
    $git = (Get-Command git -ErrorAction Stop).Source
    if ($Ref -match '^[0-9a-f]{40}$') { $spec = $Ref + '^{commit}' }
    elseif ($Ref -match '^v[0-9]') { $spec = 'refs/tags/' + $Ref + '^{commit}' }
    else { throw "-Pin takes a release tag (v1.0.0) or a full 40-character commit SHA, never a branch: $Ref" }
    $sha = Invoke-GitText $git @('-C', $repo, 'rev-parse', '--verify', '--quiet', $spec)
    if ($sha -notmatch '^[0-9a-f]{40}$') { throw "$Ref is not a commit in this clone (git fetch --tags first)" }
    # On origin/main's history, as this clone last fetched it; a clone
    # without origin/main can't tell, so it doesn't pin (rc2 review C9).
    try { [void](Invoke-GitText $git @('-C', $repo, 'rev-parse', '--verify', '--quiet', 'refs/remotes/origin/main')) }
    catch { throw "this clone has no origin/main to check $Ref against (git fetch origin first)" }
    try { [void](Invoke-GitBytes $git @('-C', $repo, 'merge-base', '--is-ancestor', $sha, 'refs/remotes/origin/main')) }
    catch { throw "$Ref ($sha) is not on origin/main's history, so a household's updates could never fast-forward from it" }
    $tags = Invoke-GitText $git @('-C', $repo, 'tag', '--points-at', $sha, '--list', 'v[0-9]*')
    if ($Channel -eq 'stable' -and -not $tags) {
        throw "$Ref ($sha) is not a release: no v* tag names it, so the stable channel would not hold it. Pin the release's tag, or pass -Channel main"
    }
    $script = Invoke-GitBytes $git @('-C', $repo, 'cat-file', 'blob', ($sha + ':scripts/linux/provision.sh'))
    if ([Array]::IndexOf($script, [byte]13) -ge 0) { throw "provision.sh at $sha has CR line endings; Setup checks the bytes GitHub serves" }
    $text = [System.Text.Encoding]::UTF8.GetString($script)
    if (-not ($text.Contains("`n  --stop-before STEP ") -and $text.Contains('start_diagnostics()'))) {
        throw "provision.sh at $sha lacks --stop-before or the start diagnostics, which DomovoiHost relies on"
    }
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    $provSha = (($hasher.ComputeHash($script) | ForEach-Object { $_.ToString('x2') }) -join '')
    $jsx = [System.Text.Encoding]::UTF8.GetString((Invoke-GitBytes $git @('-C', $repo, 'cat-file', 'blob', ($sha + ':web/static/components.jsx'))))
    $routes = $jsx.Contains("const SETUP_ROUTE = 'settings/configuration/setup';")

    $pins = [System.IO.File]::ReadAllText($File)
    if ($pins.Contains("`r")) { throw "$File has CR line endings" }
    $pins = Set-PinLine $pins '^#define DomovoiCommit "[0-9a-f]{40}"$' ('#define DomovoiCommit "' + $sha + '"')
    $pins = Set-PinLine $pins '^#define DomovoiChannel "[a-z]+"$' ('#define DomovoiChannel "' + $Channel + '"')
    $pins = Set-PinLine $pins '^#define ProvisionSha256 "[0-9a-f]{64}"$' ('#define ProvisionSha256 "' + $provSha + '"')
    $pins = Set-PinLine $pins '^(; )?#define ProvisionBundled[ \t]*$' '; #define ProvisionBundled'
    if ($routes) { $line = '#define DashboardTabRoutes' } else { $line = '; #define DashboardTabRoutes' }
    $pins = Set-PinLine $pins '^(; )?#define DashboardTabRoutes[ \t]*$' $line
    [System.IO.File]::WriteAllBytes($File, [System.Text.Encoding]::ASCII.GetBytes($pins))
    $tagText = if ($tags) { ' (' + (($tags -split "`n") -join ', ') + ')' } else { '' }
    Write-Output ("pinned $sha$tagText, channel $Channel, provision.sh sha256 $provSha (" + $script.Length + " bytes, fetched from that commit), tab addresses " + $(if ($routes) { 'on' } else { 'off' }) + ": $File")
}

# Disabled hook: when signing comes back, sign DomovoiHost.exe and
# DomovoiSetupHelper.exe before ISCC packs them, and DomovoiSetup.exe after
# (Inno's SignTool directive can sign the setup and the uninstaller).
function Invoke-Sign([string[]]$Files) {
    throw 'Code signing is on hold (PLAN.md section 10): this build stays unsigned.'
}
if ($Sign) { Invoke-Sign @() }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Pin) {
    if (-not $PinsFile) { $PinsFile = Join-Path $here 'installer\pins.iss' }
    Set-Pins $Pin $Channel $PinsFile
    if ($PinOnly) { return }
} elseif ($PinOnly -or $PinsFile) {
    throw '-PinOnly and -PinsFile go with -Pin'
}
$out = Join-Path $here 'build'
$bin = Join-Path $out 'bin'
$obj = Join-Path $out 'obj'
New-Item -ItemType Directory -Force -Path $bin, $obj | Out-Null

$fw = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$csc = Join-Path $fw 'csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { throw "No in-box C# compiler at $csc (.NET Framework 4.8.1 is part of Windows 11)." }

$refs = @('System.dll', 'System.Core.dll', 'System.Web.Extensions.dll', 'System.ServiceProcess.dll',
          'System.Management.dll', 'System.Security.dll') | ForEach-Object { '/r:' + (Join-Path $fw $_) }
$common = @(Get-ChildItem -LiteralPath (Join-Path $here 'common') -Filter '*.cs' | Sort-Object Name | ForEach-Object { $_.FullName })
$manifest = Join-Path $here 'common\asInvoker.manifest.xml'

function Invoke-Csc([string]$OutFile, [string[]]$Sources, [string[]]$ExtraArgs) {
    $cscArgs = @('/nologo', '/noconfig', '/target:exe', '/platform:x64', '/optimize+', '/warn:4', '/warnaserror+',
              '/utf8output', "/out:$OutFile") + $refs + $ExtraArgs + $Sources
    & $csc @cscArgs | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "csc failed for $OutFile" }
}

$manifestArg = '/win32manifest:' + $manifest
# Domovoi's icon (installer\art\make_art.py makes it from docs/assets/domovoi-icon.png).
$iconArg = '/win32icon:' + (Join-Path $here 'installer\art\domovoi.ico')
Invoke-Csc (Join-Path $bin 'DomovoiHost.exe') ($common + @(Get-ChildItem -LiteralPath (Join-Path $here 'host') -Filter '*.cs' | ForEach-Object { $_.FullName })) @($manifestArg, $iconArg)
Invoke-Csc (Join-Path $bin 'DomovoiSetupHelper.exe') ($common + @(Get-ChildItem -LiteralPath (Join-Path $here 'helper') -Filter '*.cs' | ForEach-Object { $_.FullName })) @($manifestArg, $iconArg)
Write-Output "built $bin\DomovoiHost.exe and $bin\DomovoiSetupHelper.exe"

if (-not $SkipTests) {
    # The tests parse the generated firewall scripts with PowerShell's own
    # parser, so they reference this PowerShell's engine assembly.
    $sma = [psobject].Assembly.Location
    $testExe = Join-Path $obj 'DomovoiWindowsTests.exe'
    # The helper's and the host's sources are compiled in too (their own
    # Main methods are skipped with /main), so the tests see the same code
    # the exes run.
    $helperSrc = @(Get-ChildItem -LiteralPath (Join-Path $here 'helper') -Filter '*.cs' | ForEach-Object { $_.FullName })
    $hostSrc = @(Get-ChildItem -LiteralPath (Join-Path $here 'host') -Filter '*.cs' | ForEach-Object { $_.FullName })
    Invoke-Csc $testExe ($common + $helperSrc + $hostSrc + @((Join-Path $here 'tests\Tests.cs'))) @("/r:$sma", '/main:Tests')
    & $testExe
    if ($LASTEXITCODE -ne 0) { throw "unit tests failed" }
}

if (-not $SkipInstaller) {
    if (-not $Iscc) {
        foreach ($c in @((Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
                         (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
                         (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'))) {
            if (Test-Path -LiteralPath $c) { $Iscc = $c; break }
        }
    }
    if (-not $Iscc) { throw 'Inno Setup 6 not found (ISCC.exe). Install it, or pass -Iscc.' }
    & $Iscc '/Q' "/O$out" (Join-Path $here 'installer\DomovoiSetup.iss')
    if ($LASTEXITCODE -ne 0) { throw 'ISCC failed' }
    $setup = Join-Path $out 'DomovoiSetup.exe'
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setup).Hash.ToLowerInvariant()
    Write-Output "built $setup ($((Get-Item -LiteralPath $setup).Length) bytes, sha256 $hash)"
}
