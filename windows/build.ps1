<#
.SYNOPSIS
Builds the Windows installer test build.

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
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipTests,
    [string]$Iscc,
    [switch]$Sign
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

# Disabled hook: when signing comes back, sign DomovoiHost.exe and
# DomovoiSetupHelper.exe before ISCC packs them, and DomovoiSetup.exe after
# (Inno's SignTool directive can sign the setup and the uninstaller).
function Invoke-Sign([string[]]$Files) {
    throw 'Code signing is on hold (PLAN.md section 10): this build stays unsigned.'
}
if ($Sign) { Invoke-Sign @() }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
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
Invoke-Csc (Join-Path $bin 'DomovoiHost.exe') ($common + @(Get-ChildItem -LiteralPath (Join-Path $here 'host') -Filter '*.cs' | ForEach-Object { $_.FullName })) @($manifestArg)
Invoke-Csc (Join-Path $bin 'DomovoiSetupHelper.exe') ($common + @(Get-ChildItem -LiteralPath (Join-Path $here 'helper') -Filter '*.cs' | ForEach-Object { $_.FullName })) @($manifestArg)
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
