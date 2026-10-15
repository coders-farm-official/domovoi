<#
.SYNOPSIS
Publish a release of the Domovoi Android app.

.DESCRIPTION
Builds the per-ABI release APKs (arm64-v8a, armeabi-v7a), signs them with
YOUR release key, checks them the way a Domovoi server will, publishes a
GitHub release on coders-farm-official/domovoi with gh, and writes
android/release.json for you to commit.

Run by the owner on his own machine; nothing here runs in CI. The
keystore's path and passwords are asked for here and never written to a
file, a log or a command line: apksigner reads the passwords from this
script's environment for the one call that needs them, and they are
removed right after. The bash twin is release.sh beside it;
android/README.md, "Publishing a release", walks through a run.

Needs: a JDK (JAVA_HOME or java on PATH), the Android SDK with build-tools
35.0.0, Python 3.11+ (the server's own check), git, and gh signed in to an
account that may publish releases on the repository.

.PARAMETER NoPublish
Build, sign and check; stop before GitHub (nothing is published and
android/release.json is not touched).

.PARAMETER SkipBuild
Sign what the last build left (after a failed signing).

.PARAMETER Keystore
The keystore file (asked for when not given).

.PARAMETER Alias
The key's alias (asked for when not given; default domovoi).

.PARAMETER KeystoreCredential
For a scripted run only: the keystore password as a PSCredential (its
user name is ignored). Without it the password is asked for.

.PARAMETER KeyPassword
For a scripted run only: the key's own password, when it differs.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\release.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoPublish,
    [switch]$SkipBuild,
    [string]$Keystore,
    [string]$Alias,
    [System.Management.Automation.PSCredential]$KeystoreCredential,
    [System.Security.SecureString]$KeyPassword
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3

$RepoSlug = 'coders-farm-official/domovoi'
$BuildTools = '35.0.0'
$Abis = @('arm64-v8a', 'armeabi-v7a')
$Package = 'com.domovoi.app'

function Die([string]$message) { Write-Host "release: $message" -ForegroundColor Red; exit 1 }
function Say([string]$message) { Write-Host "release: $message" }

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

# The first line of the last tool's output that says why it failed.
function Show-ToolError {
    $why = $script:LastToolOutput | Where-Object { $_ -match 'ERROR|Exception|Failed|failed' } | Select-Object -First 1
    if ($why) { Write-Host "  $why" }
}

# The exit code of a native program, its output thrown away.
function Test-Tool([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $null = & $exe @arguments 2>&1
    return $LASTEXITCODE
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repo

# --- tools ---------------------------------------------------------------
$java = $null
if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME 'bin\java.exe'))) {
    $java = Join-Path $env:JAVA_HOME 'bin\java.exe'
} else {
    $cmd = Get-Command java -ErrorAction SilentlyContinue
    if ($cmd) { $java = $cmd.Source }
}
if (-not $java) { Die 'no Java: install a JDK 17+ and set JAVA_HOME' }

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
$bt = Join-Path $sdk "build-tools\$BuildTools"
$apksignerJar = Join-Path $bt 'lib\apksigner.jar'
if (-not (Test-Path $apksignerJar)) { Die "no build-tools $BuildTools in $sdk (sdkmanager `"build-tools;$BuildTools`")" }
$zipalign = Join-Path $bt 'zipalign.exe'
$aapt2 = Join-Path $bt 'aapt2.exe'

$python = $null
foreach ($candidate in @('python', 'py', 'python3')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    if ((Test-Tool $cmd.Source @('-c', 'import sys; sys.exit(sys.version_info < (3, 11))')) -eq 0) { $python = $cmd.Source; break }
}
if (-not $python) { Die 'no Python 3.11+ (it runs the server''s own check of the APKs)' }
if (-not $NoPublish) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Die 'no gh (GitHub CLI): install it and run gh auth login, or use -NoPublish' }
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { Die 'no curl.exe (it checks the published files)' }
}

function Get-Sha256([string]$path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }

# --- the source ----------------------------------------------------------
$dirty = Invoke-Tool 'git' @('status', '--porcelain')
if ($null -eq $dirty) { Die 'git status failed (is this a checkout?)' }
if (@($dirty | Where-Object { $_ }).Count -gt 0) { Die 'the checkout has uncommitted changes: commit or stash them first, so the release is one commit' }
$commit = (Invoke-Tool 'git' @('rev-parse', 'HEAD'))[0].Trim()
if (-not $NoPublish) {
    if ($null -eq (Invoke-Tool 'git' @('fetch', '--quiet', 'origin'))) { Die 'git fetch origin failed' }
    $remote = Invoke-Tool 'git' @('branch', '-r', '--contains', $commit)
    if (-not $remote -or @($remote | Where-Object { $_.Trim() }).Count -eq 0) {
        Die "commit $($commit.Substring(0, 12)) is not on GitHub yet: push it first (the release's tag points at it)"
    }
}

# --- build ---------------------------------------------------------------
$outUnsigned = 'android\app\build\outputs\apk\release'
if (-not $SkipBuild) {
    Say "building the release APKs ($($Abis -join ', '))..."
    Push-Location android
    try {
        $ErrorActionPreference = 'Continue'   # Gradle writes warnings to stderr
        & .\gradlew.bat ':app:assembleRelease' '-Pdomovoi.apkPerAbi' "-Pdomovoi.abis=$($Abis -join ',')"
        $built = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($built -ne 0) { Die 'the build failed' }
    } finally { Pop-Location }
}

$versionName = $null; $versionCode = $null
foreach ($abi in $Abis) {
    $apk = Join-Path $outUnsigned "app-$abi-release-unsigned.apk"
    if (-not (Test-Path $apk)) { Die "no $apk`: build first (drop -SkipBuild)" }
    $badging = Invoke-Tool $aapt2 @('dump', 'badging', $apk)
    if ($null -eq $badging) { Die "aapt2 can't read $apk" }
    $pkgLine = $badging | Where-Object { $_ -like 'package: *' } | Select-Object -First 1
    if ($pkgLine -notmatch "name='([^']*)'.* versionCode='([0-9]+)' versionName='([^']*)'") { Die "aapt2 shows no version for $apk" }
    $pkg = $Matches[1]; $vc = [int]$Matches[2]; $vn = $Matches[3]
    $native = ($badging | Where-Object { $_ -like 'native-code: *' } | Select-Object -First 1)
    if ($pkg -ne $Package) { Die "$apk is $pkg, not $Package" }
    if ($native -ne "native-code: '$abi'") { Die "$apk carries $native, not '$abi' only" }
    if ($null -eq $versionName) { $versionName = $vn; $versionCode = $vc }
    if ($vn -ne $versionName -or $vc -ne $versionCode) { Die 'the APKs disagree on their version' }
}
$tag = "android-v$versionName"
Say "Domovoi $versionName (versionCode $versionCode), commit $($commit.Substring(0, 12))"

$prevCode = 0; $prevCert = $null
try {
    $prev = (Get-Content -Raw -Encoding UTF8 'android\release.json' | ConvertFrom-Json).release
    if ($prev) { $prevCode = [int]$prev.version_code; $prevCert = [string]$prev.cert_sha256 }
} catch { }
if ($versionCode -le $prevCode) {
    Die "versionCode $versionCode is not above the last release's ($prevCode): raise versionCode (and versionName) in android\app\build.gradle.kts, commit, and run again"
}
if (-not $NoPublish) {
    if ((Test-Tool 'gh' @('release', 'view', $tag, '--repo', $RepoSlug)) -eq 0) { Die "a release $tag already exists on $RepoSlug" }
}

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

$signed = 'android\app\build\outputs\apk\release-signed'
if (Test-Path $signed) { Remove-Item -Recurse -Force $signed }
$null = New-Item -ItemType Directory -Path $signed
try {
    $env:DOMOVOI_RELEASE_KS_PASS = ConvertFrom-Secure $ksSecure
    $keyPlain = ConvertFrom-Secure $keySecure
    $env:DOMOVOI_RELEASE_KEY_PASS = if ($keyPlain) { $keyPlain } else { $env:DOMOVOI_RELEASE_KS_PASS }
    $keyPlain = $null
    foreach ($abi in $Abis) {
        $name = "domovoi-$versionName-$abi.apk"
        Say "signing $name..."
        $result = Invoke-Tool $java @('-jar', $apksignerJar, 'sign', '--ks', $Keystore, '--ks-key-alias', $Alias,
            '--ks-pass', 'env:DOMOVOI_RELEASE_KS_PASS', '--key-pass', 'env:DOMOVOI_RELEASE_KEY_PASS',
            '--v4-signing-enabled', 'false', '--out', (Join-Path $signed $name),
            (Join-Path $outUnsigned "app-$abi-release-unsigned.apk"))
        if ($null -eq $result) { Show-ToolError; Die "signing $name failed (wrong password or alias?)" }
    }
} finally {
    Remove-Item Env:DOMOVOI_RELEASE_KS_PASS -ErrorAction SilentlyContinue
    Remove-Item Env:DOMOVOI_RELEASE_KEY_PASS -ErrorAction SilentlyContinue
}

# --- check: apksigner, alignment, and the server's own check -------------
$cert = $null
foreach ($abi in $Abis) {
    $apk = Join-Path $signed "domovoi-$versionName-$abi.apk"
    $report = Invoke-Tool $java @('-jar', $apksignerJar, 'verify', '--verbose', '--print-certs', '--min-sdk-version', '26', $apk)
    if ($null -eq $report) { Show-ToolError; Die "apksigner does not verify $apk" }
    if (-not ($report -contains 'Verified using v2 scheme (APK Signature Scheme v2): true')) { Die "$apk has no v2 signature (Android 8 needs it)" }
    if (-not ($report -contains 'Number of signers: 1')) { Die "$apk must have exactly one signer" }
    if (-not ($report -contains 'Signer #1 key algorithm: RSA')) {
        Die 'the release key must be an RSA key (what android\README.md makes): the server checks RSA signatures only'
    }
    if ($report | Where-Object { $_ -like 'Signer #1 certificate DN: CN=Android Debug*' }) { Die "$apk is signed with a debug key, not the release key" }
    $line = $report | Where-Object { $_ -match '^Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})$' } | Select-Object -First 1
    if (-not $line) { Die "apksigner printed no certificate digest for $apk" }
    $null = $line -match '([0-9a-f]{64})$'
    $apkCert = $Matches[1]
    if ($cert -and $cert -ne $apkCert) { Die 'the two APKs are signed by different keys' }
    $cert = $apkCert
    if ($null -eq (Invoke-Tool $zipalign @('-c', '-P', '16', '4', $apk))) { Die "$apk is not aligned for 16 KB pages" }
}
if ($prevCert -and $prevCert -ne $cert) {
    Die "this key ($cert) is not the last release's ($prevCert): phones refuse an update signed by another key"
}
Say "signed by certificate SHA-256 $cert"

$staged = Join-Path $signed 'release.json'
$published = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
if ($null -eq (Invoke-Tool $python @('-m', 'domovoi.android_app', 'write', $staged, $signed, '--version-name', $versionName,
        '--version-code', "$versionCode", '--cert', $cert, '--commit', $commit, '--published', $published))) {
    $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }
    Die 'writing the manifest failed'
}
$checked = Invoke-Tool $python @('-m', 'domovoi.android_app', 'check', $staged, $signed)
if ($null -eq $checked) { $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }; Die 'the server''s own check refuses these APKs' }
$checked | ForEach-Object { Write-Host "  $_" }

if ($NoPublish) {
    Say "not published (-NoPublish). Signed APKs, SHA256SUMS and the manifest it would commit: $signed\"
    exit 0
}

# --- publish -------------------------------------------------------------
$rows = foreach ($abi in $Abis) {
    $name = "domovoi-$versionName-$abi.apk"
    $who = if ($abi -eq 'arm64-v8a') { 'most phones (64-bit)' } else { 'older or cheaper phones (32-bit)' }
    "| $name | $who | ``$(Get-Sha256 (Join-Path $signed $name))`` |"
}
$notes = @(
    "Domovoi for Android $versionName (versionCode $versionCode), built from $commit.",
    '',
    'Install it from your own Domovoi server: on the dashboard, Settings -> Satellites -> add a spare device (for a room) or Settings -> Devices -> get the Domovoi app. The server checks these files against android/release.json before it hands them out.',
    '',
    '| file | for | SHA-256 |',
    '|---|---|---|'
) + $rows + @('', "Signing certificate SHA-256: ``$cert`` (check with ``apksigner verify --print-certs <file>``).")
$notesFile = Join-Path $signed 'NOTES.md'
[IO.File]::WriteAllText((Join-Path $repo $notesFile), ($notes -join "`n") + "`n", (New-Object Text.UTF8Encoding $false))

Say "publishing $tag on $RepoSlug..."
$assets = @($Abis | ForEach-Object { Join-Path $signed "domovoi-$versionName-$_.apk" }) + @(Join-Path $signed 'SHA256SUMS')
$created = Invoke-Tool 'gh' (@('release', 'create', $tag) + $assets + @('--repo', $RepoSlug, '--target', $commit,
    '--title', "Domovoi for Android $versionName", '--notes-file', $notesFile))
if ($null -eq $created) { $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }; Die 'gh release create failed' }
$created | ForEach-Object { Write-Host "  $_" }

Say 'checking the published files by their manifest URLs...'
$manifest = Get-Content -Raw -Encoding UTF8 $staged | ConvertFrom-Json
$checkDir = Join-Path ([IO.Path]::GetTempPath()) ('domovoi-release-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $checkDir
try {
    foreach ($entry in $manifest.release.apks) {
        $dest = Join-Path $checkDir $entry.file
        if ((Test-Tool 'curl.exe' @('-fsSL', '--retry', '3', '-o', $dest, $entry.url)) -ne 0) { Die "can't download $($entry.url)" }
        if ((Get-Sha256 $dest) -ne $entry.sha256) { Die "$($entry.url) is not the file that was signed" }
    }
} finally { Remove-Item -Recurse -Force $checkDir -ErrorAction SilentlyContinue }

Copy-Item -LiteralPath $staged -Destination 'android\release.json' -Force
Say "done. Now commit the manifest so servers start handing out $versionName`:"
Write-Host '    git add android/release.json'
Write-Host "    git commit -m `"android: release $versionName [skip ci]`""
Write-Host '    git push'
