<#
.SYNOPSIS
Build the Google Play App Bundle (.aab) of the Domovoi Android app, signed
with YOUR release key.

.DESCRIPTION
Builds bundleRelease (all four ABIs; Play splits it per phone), signs it
with jarsigner and your release key (apksigner cannot sign a bundle), checks
the signature, and writes the .aab and its SHA-256 to
android\app\build\outputs\bundle\release-signed\. Uploading it is yours to
do in the Play Console (android\play\PUBLISH-CHECKLIST.md); nothing here
talks to Google or GitHub.

The key is the same one release.ps1 signs the GitHub APKs with: with Play
App Signing set to "use my own key" (the checklist's PEPK step), Play
signs the APKs it delivers with that key too, so a phone that installed the
app from the household's server or GitHub can take updates from Play, and
the other way round. When android\release.json names a release
certificate, this script refuses a bundle signed by any other key.

Run by the owner on his own machine; nothing here runs in CI. The
keystore's path and passwords are asked for here and never written to a
file, a log or a command line: jarsigner reads the passwords from this
script's environment for the one call that needs them, and they are removed
right after. The bash twin is play-bundle.sh beside it.

Needs: a JDK 17+ (JAVA_HOME or java on PATH; jarsigner and keytool come
with it) and the Android SDK (local.properties, ANDROID_HOME, or
%LOCALAPPDATA%\Android\Sdk).

.PARAMETER SkipBuild
Sign what the last build left (after a failed signing).

.PARAMETER Keystore
The keystore file (asked for when not given).

.PARAMETER Alias
The key's alias (asked for when not given; default domovoi).

.PARAMETER KeystoreCredential
For a scripted run only: the keystore password as a PSCredential (its user
name is ignored). Without it the password is asked for.

.PARAMETER KeyPassword
For a scripted run only: the key's own password, when it differs.

.PARAMETER AllowDirty
Build from a checkout with uncommitted changes (a test run; a bundle for
Play should be one commit).

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\play-bundle.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [string]$Keystore,
    [string]$Alias,
    [System.Management.Automation.PSCredential]$KeystoreCredential,
    [System.Security.SecureString]$KeyPassword,
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3

$Package = 'com.domovoi.app'

function Die([string]$message) { Write-Host "play-bundle: $message" -ForegroundColor Red; exit 1 }
function Say([string]$message) { Write-Host "play-bundle: $message" }

# Run a native program; its output lines (stdout and stderr, as text), or
# $null when it failed. Windows PowerShell turns a redirected stderr line
# into an error, which 'Stop' would make fatal, so this scope continues.
$script:LastToolOutput = @()
function Invoke-Tool([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $out = & $exe @arguments 2>&1
    $script:LastToolOutput = @($out | ForEach-Object { "$_" })
    if ($LASTEXITCODE -ne 0) { return $null }
    return , $script:LastToolOutput
}

# The first line of the last tool's output that says why it failed (the
# JDK's tools never print a password).
function Show-ToolError {
    $why = $script:LastToolOutput | Where-Object { $_ -match 'ERROR|Exception|Failed|failed|error' } | Select-Object -First 1
    if ($why) { Write-Host "  $why" }
}

function Get-Sha256([string]$path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repo

# --- tools ---------------------------------------------------------------
$jdkBin = $null
if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME 'bin\jarsigner.exe'))) {
    $jdkBin = Join-Path $env:JAVA_HOME 'bin'
} else {
    $cmd = Get-Command jarsigner -ErrorAction SilentlyContinue
    if ($cmd) { $jdkBin = Split-Path $cmd.Source }
}
if (-not $jdkBin) { Die 'no JDK (jarsigner): install a JDK 17+ and set JAVA_HOME' }
$jarsigner = Join-Path $jdkBin 'jarsigner.exe'
$keytool = Join-Path $jdkBin 'keytool.exe'
if (-not (Test-Path $keytool)) { Die "no keytool beside $jarsigner" }

$sdk = $null
foreach ($candidate in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)) {
    if ($candidate -and (Test-Path $candidate)) { $sdk = $candidate; break }
}
if (-not $sdk -and (Test-Path 'android\local.properties')) {
    $line = Get-Content 'android\local.properties' | Where-Object { $_ -match '^sdk\.dir=' } | Select-Object -First 1
    if ($line) { $sdk = ($line -replace '^sdk\.dir=', '') -replace '\\:', ':' -replace '\\\\', '\' }
}
if (-not $sdk) { $sdk = Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
if (-not (Test-Path $sdk)) { Die 'no Android SDK found (set ANDROID_HOME)' }

# --- the source ----------------------------------------------------------
$dirty = Invoke-Tool 'git' @('status', '--porcelain')
if ($null -eq $dirty) { Die 'git status failed (is this a checkout?)' }
if (@($dirty | Where-Object { $_ }).Count -gt 0) {
    if (-not $AllowDirty) { Die 'the checkout has uncommitted changes: commit or stash them first, so the bundle is one commit' }
    Say 'WARNING: uncommitted changes (-AllowDirty): this bundle is not one commit; do not upload it'
}
$commit = (Invoke-Tool 'git' @('rev-parse', 'HEAD'))[0].Trim()

# The version, from its one source (android/app/build.gradle.kts).
$gradleFile = Get-Content -Raw -Encoding UTF8 'android\app\build.gradle.kts'
if ($gradleFile -notmatch '(?m)^\s*versionCode\s*=\s*([0-9]+)\s*$') { Die 'no versionCode in android\app\build.gradle.kts' }
$versionCode = [long]$Matches[1]
if ($gradleFile -notmatch '(?m)^\s*versionName\s*=\s*"([^"]+)"\s*$') { Die 'no versionName in android\app\build.gradle.kts' }
$versionName = $Matches[1]
if ($gradleFile -notmatch '(?m)^\s*applicationId\s*=\s*"([^"]+)"\s*$' -or $Matches[1] -ne $Package) { Die "the applicationId is not $Package" }
Say "Domovoi $versionName (versionCode $versionCode), commit $($commit.Substring(0, 12))"

# versionCode is YYMMDDNN: the build's date and that day's number, one
# sequence for the GitHub APKs and this bundle (release.ps1 checks the same
# shape).
if ("$versionCode" -notmatch '^[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])(0[1-9]|[1-9][0-9])$') {
    Die "versionCode $versionCode is not YYMMDDNN (the build's date and that day's number, e.g. 26101501): set it in android\app\build.gradle.kts"
}

# The last GitHub release (android/release.json, when this checkout has
# one): the same key, and a versionCode above it (one sequence for both).
$prevCode = 0; $prevCert = $null
if (Test-Path 'android\release.json') {
    try {
        $prev = (Get-Content -Raw -Encoding UTF8 'android\release.json' | ConvertFrom-Json).release
        if ($prev) { $prevCode = [long]$prev.version_code; $prevCert = [string]$prev.cert_sha256 }
    } catch { Die "android\release.json can't be read: $($_.Exception.Message)" }
}
if ($prevCode -gt 0 -and $versionCode -lt $prevCode) {
    Die "versionCode $versionCode is below the last GitHub release's ($prevCode): one sequence for both (YYMMDDNN), raise it"
}

# --- build ---------------------------------------------------------------
$unsigned = 'android\app\build\outputs\bundle\release\app-release.aab'
if (-not $SkipBuild) {
    Say 'building the App Bundle (all four ABIs)...'
    Push-Location android
    try {
        $ErrorActionPreference = 'Continue'   # Gradle writes warnings to stderr
        & .\gradlew.bat ':app:bundleRelease' '--console=plain'
        $built = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($built -ne 0) { Die 'the build failed' }
    } finally { Pop-Location }
}
if (-not (Test-Path $unsigned)) { Die "no $unsigned`: build first (drop -SkipBuild)" }

# The release build is minified (android\app\build.gradle.kts): the bundle
# must carry its R8 mapping, which Play uses to turn crash reports back into
# class and method names.
$mapEntry = 'BUNDLE-METADATA/com.android.tools.build.obfuscation/proguard.map'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead((Join-Path $repo $unsigned))
try { $hasMap = @($zip.Entries | Where-Object { $_.FullName -eq $mapEntry }).Count -eq 1 } finally { $zip.Dispose() }
if (-not $hasMap) { Die "$unsigned carries no R8 mapping ($mapEntry): is the release build still minified?" }

# --- sign ----------------------------------------------------------------
if (-not $Keystore) {
    $default = Join-Path $HOME 'domovoi-release.jks'
    $Keystore = Read-Host "Keystore file [$default]"
    if (-not $Keystore) { $Keystore = $default }
}
if (-not (Test-Path -LiteralPath $Keystore -PathType Leaf)) { Die "no keystore at $Keystore (android\README.md says how to make one, once)" }
$Keystore = (Resolve-Path -LiteralPath $Keystore).Path
if ($Keystore.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase)) {
    Die 'the keystore is inside the checkout: keep it outside (and backed up)'
}
if (-not $Alias) {
    $Alias = Read-Host 'Key alias [domovoi]'
    if (-not $Alias) { $Alias = 'domovoi' }
}
$ksSecure = if ($KeystoreCredential) { $KeystoreCredential.Password } else { Read-Host 'Keystore password' -AsSecureString }
$keySecure = if ($KeyPassword) { $KeyPassword } elseif ($KeystoreCredential) { $null } else { Read-Host 'Key password (Enter if the same)' -AsSecureString }

function ConvertFrom-Secure([System.Security.SecureString]$secure) {
    if ($null -eq $secure -or $secure.Length -eq 0) { return $null }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

$outDir = 'android\app\build\outputs\bundle\release-signed'
if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
$null = New-Item -ItemType Directory -Path $outDir
$name = "domovoi-$versionName-$versionCode.aab"
$signedAab = Join-Path $outDir $name
try {
    $env:DOMOVOI_RELEASE_KS_PASS = ConvertFrom-Secure $ksSecure
    $keyPlain = ConvertFrom-Secure $keySecure
    $env:DOMOVOI_RELEASE_KEY_PASS = if ($keyPlain) { $keyPlain } else { $env:DOMOVOI_RELEASE_KS_PASS }
    $keyPlain = $null
    Say "signing $name..."
    $result = Invoke-Tool $jarsigner @('-keystore', $Keystore, '-storepass:env', 'DOMOVOI_RELEASE_KS_PASS',
        '-keypass:env', 'DOMOVOI_RELEASE_KEY_PASS', '-sigalg', 'SHA256withRSA', '-digestalg', 'SHA-256',
        '-signedjar', $signedAab, $unsigned, $Alias)
    if ($null -eq $result) { Show-ToolError; Die "signing $name failed (wrong password or alias, or not an RSA key?)" }
} finally {
    Remove-Item Env:DOMOVOI_RELEASE_KS_PASS -ErrorAction SilentlyContinue
    Remove-Item Env:DOMOVOI_RELEASE_KEY_PASS -ErrorAction SilentlyContinue
}

# --- check ---------------------------------------------------------------
$verify = Invoke-Tool $jarsigner @('-verify', $signedAab)
if ($null -eq $verify -or -not ($verify -match '^jar verified\.')) { Show-ToolError; Die "jarsigner does not verify $signedAab" }
$certs = Invoke-Tool $keytool @('-printcert', '-jarfile', $signedAab)
if ($null -eq $certs) { Show-ToolError; Die "keytool can't read the signature of $signedAab" }
$signers = @($certs | Where-Object { $_ -match '^Signer #\d+:' })
if ($signers.Count -ne 1) { Die "$signedAab must have exactly one signer (has $($signers.Count))" }
if ($certs | Where-Object { $_ -match 'CN=Android Debug' }) { Die 'this is the debug key, not the release key' }
if (-not ($certs | Where-Object { $_ -match 'Subject Public Key Algorithm: .*RSA' })) {
    Die 'the release key must be an RSA key (what android\README.md makes): the household server checks RSA signatures only'
}
$shaLine = $certs | Where-Object { $_ -match '^\s*SHA256:\s*([0-9A-Fa-f:]{95})\s*$' } | Select-Object -First 1
if (-not $shaLine) { Die "keytool printed no SHA-256 for the signing certificate" }
$null = $shaLine -match 'SHA256:\s*([0-9A-Fa-f:]{95})'
$cert = ($Matches[1] -replace ':', '').ToLowerInvariant()
if ($prevCert -and $prevCert -ne $cert) {
    Die "this key ($cert) is not the GitHub release's ($prevCert): use the same key for Play, or phones can't move between them"
}

$sha = Get-Sha256 $signedAab
$sumLine = "$sha  $name"
[IO.File]::WriteAllText((Join-Path $repo "$signedAab.sha256"), "$sumLine`n", (New-Object Text.UTF8Encoding $false))
$size = (Get-Item -LiteralPath $signedAab).Length
# The same mapping beside the bundle, for the release's records (Play has
# its own copy from the bundle).
$mapping = 'android\app\build\outputs\mapping\release\mapping.txt'
$keptMap = Join-Path $outDir "domovoi-$versionName-$versionCode-mapping.txt"
if (Test-Path -LiteralPath $mapping) { Copy-Item -LiteralPath $mapping -Destination $keptMap } else { $keptMap = '(none: the build left no mapping.txt)' }

Say 'done.'
Write-Host "  bundle:      $signedAab ($([math]::Round($size / 1MB, 1)) MB)"
Write-Host "  SHA-256:     $sha"
Write-Host "  certificate: $cert (SHA-256 of the signing certificate)"
Write-Host "  version:     $versionName ($versionCode), commit $commit"
Write-Host "  R8 mapping:  in the bundle (Play reads it), and $keptMap"
Write-Host ''
Write-Host '  Next: Play Console -> Test and release -> Testing -> Internal testing -> Create new release, upload the .aab'
Write-Host '  (android\play\PUBLISH-CHECKLIST.md; the first upload needs App signing set up first).'
