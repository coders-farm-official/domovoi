<#
.SYNOPSIS
Remove the Domovoi room satellite from this Windows account.

.DESCRIPTION
Spare-device satellites on Windows (2026-10-13). The installer copies this
script to %LOCALAPPDATA%\Domovoi\Satellite\uninstall.ps1, outside the code
the Domovoi server may update, and points the Installed apps entry at that
copy. It runs nothing from the satellite's own code, and takes no path from
a file the satellite can write: the folder it removes is the one it sits in
(or -Root), the registry values and the shortcut have fixed names, and a
value it finds there is only compared, never followed.

    powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1 [-Purge] [-Quiet]

  1. Asks the satellite, its supervisor and its tray to stop (the user's
     stop events), and ends whatever of them is still running from this
     folder after 20 seconds.
  2. Removes this user's sign-in entry (HKCU Run), the Installed apps entry
     and the Start-menu shortcut - each only if it points into this folder.
  3. Puts back the plugged-in power settings the installer changed, from
     the values it recorded (numbers and a plan id, checked as such).
  4. Deletes what an install of this puts in the folder, by name, keeping
     its settings (.domovoi) unless -Purge; -Purge removes only the
     satellite's own files there. A link is removed, never followed.

Then retire the room in the dashboard (Settings > Rooms > the room >
Retire room) once it shows offline.
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$Purge,
    [switch]$Quiet,
    # Tests only: read and edit this JSON file instead of the registry.
    [string]$TestRegistryFile = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$AppKey = 'DomovoiSatellite'
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppKey"
$Shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Domovoi Satellite.lnk'

function Say([string]$Text = '') { Write-Host $Text }
function Get-Prop($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $p = $Object.PSObject.Properties[$Name]
    if ($p) { return $p.Value } else { return $null }
}
function Done([int]$Code) {
    if (-not $Quiet) { Say ''; Say 'Press Enter to close.'; [void][Console]::ReadLine() }
    exit $Code
}

if (-not $Root) {
    $Root = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'python\pythonw.exe')) { $PSScriptRoot }
            else { Join-Path $env:LOCALAPPDATA 'Domovoi\Satellite' }
}
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
if (-not (Test-Path -LiteralPath (Join-Path $Root '.domovoi-satellite-root'))) {
    Say "uninstall: $Root is not a Domovoi Satellite install folder; nothing was changed."
    Done 2
}
$Inside = $Root + '\'
function Test-Inside([string]$Text) {
    return [bool]$Text -and $Text.IndexOf($Inside, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

Say "Removing the Domovoi satellite from $Root..."

# 1. Stop it.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach ($kind in 'stop', 'tray-quit') {
    try {
        $e = [Threading.EventWaitHandle]::OpenExisting("Local\domovoi-satellite-$kind-$sid")
        [void]$e.Set()
        $e.Dispose()
    } catch { }
}
$deadline = (Get-Date).AddSeconds(20)
while ($true) {
    $left = @(Get-Process -ErrorAction SilentlyContinue |
              Where-Object { $_.Path -and $_.Path.StartsWith($Inside, [StringComparison]::OrdinalIgnoreCase) })
    if ($left.Count -eq 0) { break }
    if ((Get-Date) -gt $deadline) {
        $left | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        break
    }
    Start-Sleep -Milliseconds 500
}
Say '  stopped'

# 2. This user's entries, each only if it is this install's.
if ($TestRegistryFile) {
    $doc = if (Test-Path -LiteralPath $TestRegistryFile) {
        Get-Content -Raw -LiteralPath $TestRegistryFile | ConvertFrom-Json
    } else { New-Object psobject }
    $keep = [ordered]@{}
    foreach ($p in $doc.PSObject.Properties) { $keep[$p.Name] = $p.Value }
    if ($keep.Contains('run') -and (Test-Inside ([string](Get-Prop $keep['run'] $AppKey)))) { $keep.Remove('run') }
    if ($keep.Contains('uninstall') -and
        ([string](Get-Prop (Get-Prop $keep['uninstall'] $AppKey) 'InstallLocation')).TrimEnd('\') -eq $Root) {
        $keep.Remove('uninstall')
    }
    if ($keep.Contains('shortcut') -and (Test-Inside ([string](Get-Prop $keep['shortcut'] 'target')))) {
        $keep.Remove('shortcut')
    }
    ($keep | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath $TestRegistryFile -Encoding UTF8
} else {
    $run = (Get-ItemProperty -LiteralPath $RunKey -Name $AppKey -ErrorAction SilentlyContinue)
    if ($run -and (Test-Inside ([string](Get-Prop $run $AppKey)))) {
        Remove-ItemProperty -LiteralPath $RunKey -Name $AppKey
        Say '  it no longer starts at sign-in'
    }
    $entry = Get-ItemProperty -LiteralPath $UninstallKey -ErrorAction SilentlyContinue
    if ($entry -and ([string](Get-Prop $entry 'InstallLocation')).TrimEnd('\') -eq $Root) {
        Remove-Item -LiteralPath $UninstallKey -Recurse
        Say '  removed from Installed apps'
    }
    if (Test-Path -LiteralPath $Shortcut) {
        try {
            $target = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut).TargetPath
            if (Test-Inside $target) { Remove-Item -LiteralPath $Shortcut -Force; Say '  removed from the Start menu' }
        } catch { }
    }
}

# 3. The power settings the installer changed (validated: a plan id and
# numbers), with Windows' own powercfg (never one found on PATH).
$powercfg = Join-Path $env:SystemRoot 'System32\powercfg.exe'
$stateFile = Join-Path $Root 'install-windows.json'
if (Test-Path -LiteralPath $stateFile) {
    try { $state = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json } catch { $state = $null }
    $power = if ($state -and $state.PSObject.Properties['power']) { $state.power } else { $null }
    if ($power -and [string]$power.scheme -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
        $settings = @{ standby = @('SUB_SLEEP', 'STANDBYIDLE'); hibernate = @('SUB_SLEEP', 'HIBERNATEIDLE');
                       lid = @('SUB_BUTTONS', 'LIDACTION') }
        foreach ($k in $settings.Keys) {
            if (-not $power.PSObject.Properties[$k]) { continue }
            $v = $power.$k
            if ($v -isnot [int] -and $v -isnot [long]) { continue }
            if ($v -lt 0 -or $v -gt 4294967295) { continue }
            & $powercfg /setacvalueindex $power.scheme $settings[$k][0] $settings[$k][1] ([string]$v) 2>$null
        }
        # The values go back into the plan they were changed in. That plan
        # is applied again only if it is still the active one: a plan the
        # person has switched to since is left as it is.
        $active = [string](& $powercfg /getactivescheme 2>$null)
        if ($active.IndexOf([string]$power.scheme, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            & $powercfg /setactive $power.scheme 2>$null
        }
        Say '  power settings put back as they were'
    }
}

# 4. The files: only what an install of this puts there, by name. rmdir /s
# removes a link inside a folder, never what it points to.
function Remove-Tree([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        # A link: the link goes, what it points to stays.
        if ($item.PSIsContainer) { [IO.Directory]::Delete($item.FullName) } else { $item.Delete() }
    } elseif ($item.PSIsContainer) {
        & "$env:SystemRoot\System32\cmd.exe" /d /c rmdir /s /q "$Path" 2>$null
    } else {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    }
}
foreach ($name in 'python', 'app', 'logs', 'cache', 'uninstall.ps1', 'install-windows.json') {
    Remove-Tree (Join-Path $Root $name)
}
$cfg = Join-Path $Root '.domovoi'
if ($Purge) {
    # The satellite's own files (satellite\pc_install.py SATELLITE_FILES,
    # held equal by a test) and nothing else; its sound and wake-word
    # folders only when no Domovoi server keeps files here.
    $files = @('config.toml', 'config.toml.bak', 'approved', 'approval_code', 'pairing_token',
               'pending-server.json', 'server-fingerprint.json', 'server.url', 'server-names.json',
               'synced_sha', 'synced_manifest.json', 'upgrade_pending.json', 'satellite_backup.tar',
               'log-spool.txt', 'last-health.json', 'self-restarts.json', 'muted', 'voice', 'wake',
               'wake_threshold', 'manifest-serials.json', 'net-settings.logged',
               'plugin_payload_state.json', 'plugin_payload_manifest.json', 'pending_payload.json',
               'provisioning_state.json', 'image_sat_type', 'image_device_profile', 'ap.json',
               'setup_gadget.img', 'control.sock', 'install-pc.json', 'not-started.txt',
               'plugin_payloads')
    foreach ($name in $files) { Remove-Tree (Join-Path $cfg $name) }
    foreach ($pattern in 'config.toml.bak-*', 'previous-enrolment-*') {
        foreach ($item in @(Get-ChildItem -LiteralPath $cfg -Filter $pattern -Force -ErrorAction SilentlyContinue)) {
            Remove-Tree $item.FullName
        }
    }
    $server = @('server-identity.json', 'device-token.txt', 'setup-code.txt' |
                Where-Object { Test-Path -LiteralPath (Join-Path $cfg $_) })
    if ($server.Count -eq 0) {
        Remove-Tree (Join-Path $cfg 'sounds')
        Remove-Tree (Join-Path $cfg 'wake_models')
    } else {
        Say "  left $cfg\sounds and wake_models alone: a Domovoi server's files are there too"
    }
    # Empty folders go; anything left in them is not this install's.
    if ((Test-Path -LiteralPath $cfg) -and -not @(Get-ChildItem -LiteralPath $cfg -Force)) {
        [IO.Directory]::Delete($cfg)
    }
    if (-not (Test-Path -LiteralPath $cfg)) {
        Remove-Tree (Join-Path $Root '.domovoi-satellite-root')
    }
    if (-not @(Get-ChildItem -LiteralPath $Root -Force)) {
        [IO.Directory]::Delete($Root)
        Say '  the satellite''s settings and records are gone'
    } else {
        Say "  left in ${Root}: what this install did not put there"
    }
} else {
    Say "  kept its settings in $cfg (-Purge removes them)"
}

Say 'Done. In the dashboard, retire the room once it shows offline'
Say '(Settings > Rooms > the room > Retire room): that frees its name and removes its music player.'
Done 0
