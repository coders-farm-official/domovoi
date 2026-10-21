<#
.SYNOPSIS
Publish a release of the Domovoi Android app.

.DESCRIPTION
Builds the per-ABI release APKs (arm64-v8a, armeabi-v7a), signs them with
YOUR release key, checks them the way a Domovoi server will, writes
android/release.json, commits it ("android: release <v> [skip ci]") and
pushes it to the branch's upstream, then publishes the GitHub release
v<versionName> on coders-farm-official/domovoi with gh, tagged at THAT
commit: the tag holds the manifest naming its own APKs (they are built from
the commit before it, which differs only by that file). Last it downloads
each file back by the manifest's URL.

If a run stops after the commit (a failed push, gh or download), run it
again from the same checkout: it sees the release commit at HEAD and
carries on from there, with the signed files it left; nothing is built or
signed again.

Then scripts\release\finish-release.ps1 adds the Windows Setup, one
SHA256SUMS and the release notes, and moves the stable branch.

Run by the owner on his own machine; nothing here runs in CI. The
keystore's path and passwords are asked for here and never written to a
file, a log or a command line: apksigner reads the passwords from this
script's environment for the one call that needs them, and they are
removed right after. The bash twin is release.sh beside it;
android/README.md, "Publishing a release", walks through a run.

Needs: a JDK (JAVA_HOME or java on PATH), the Android SDK with build-tools
35.0.0, Python 3.11+ (the server's own check), git with push access, and gh
signed in to an account that may publish releases on the repository.

.PARAMETER NoPublish
Build, sign and check; stop before GitHub (nothing is committed, pushed
or published, and android/release.json is not touched).

.PARAMETER SkipBuild
Sign what the last build left (after a failed signing).

.PARAMETER Prerelease
Mark the GitHub release a pre-release. Off by default: every release is a
full one, marked Latest, release candidates (1.0.0-rc2) included (owner
decision 2026-10-15: the suffix says what it is).

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
    [switch]$Prerelease,
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

# git, gh and curl by their absolute paths, never a program inside this checkout.
function Resolve-Tool([string]$name) {
    $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { return $null }
    $path = (Resolve-Path -LiteralPath $cmd.Source).Path
    if ($path.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase)) { Die "$name resolves to $path, inside the checkout: refusing to run it" }
    return $path
}
$Git = Resolve-Tool 'git'
if (-not $Git) { Die 'no git' }
$Gh = $null; $Curl = $null
if (-not $NoPublish) {
    $Gh = Resolve-Tool 'gh'
    if (-not $Gh) { Die 'no gh (GitHub CLI): install it and run gh auth login, or use -NoPublish' }
    $Curl = Resolve-Tool 'curl.exe'
    if (-not $Curl) { Die 'no curl.exe (it checks the published files)' }
    # origin, every fetch and push URL, is the GitHub repository: the release
    # commit and its tag are pushed there, and gh makes the release on GitHub.
    foreach ($query in @(@('remote', 'get-url', '--all', 'origin'), @('remote', 'get-url', '--push', '--all', 'origin'))) {
        $urls = Invoke-Tool $Git $query
        if ($null -eq $urls) { Die 'this checkout has no remote named origin' }
        foreach ($url in @($urls | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })) {
            $ok = if ($env:DOMOVOI_RELEASE_ORIGIN_URL) { $url.TrimEnd('/') -eq $env:DOMOVOI_RELEASE_ORIGIN_URL.TrimEnd('/') }
                  else { $url -match '^(https://github\.com/|ssh://git@github\.com/|git@github\.com:)coders-farm-official/domovoi(\.git)?/?$' }
            if (-not $ok) { Die "origin is $url, not github.com/$RepoSlug`: the release commit and its tag go there, the release to GitHub" }
        }
    }
}

function Get-Sha256([string]$path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }

# --- the source ----------------------------------------------------------
$dirty = Invoke-Tool $Git @('status', '--porcelain')
if ($null -eq $dirty) { Die 'git status failed (is this a checkout?)' }
if (@($dirty | Where-Object { $_ }).Count -gt 0) { Die 'the checkout has uncommitted changes: commit or stash them first, so the release is one commit' }
$commit = (Invoke-Tool $Git @('rev-parse', 'HEAD'))[0].Trim()

# A release commit at HEAD: android/release.json, committed on top of the
# commit its APKs were built from (and nothing else changed), by an earlier
# run that stopped before it finished. Then this run carries on from there.
$resume = $null
$builtFrom = $null
try {
    $headRelease = (Get-Content -Raw -Encoding UTF8 'android\release.json' | ConvertFrom-Json).release
    if ($headRelease -and $headRelease.PSObject.Properties['commit']) { $builtFrom = [string]$headRelease.commit }
} catch { $headRelease = $null }
if ($builtFrom -match '^[0-9a-f]{40}$' -and $builtFrom -ne $commit -and
        (Test-Tool $Git @('merge-base', '--is-ancestor', $builtFrom, $commit)) -eq 0) {
    $changed = Invoke-Tool $Git @('diff', '--name-only', $builtFrom, $commit)
    if ($null -ne $changed -and (@($changed | Where-Object { $_ }) -join ',') -eq 'android/release.json') { $resume = $headRelease }
}

# The branch the release commit goes on: HEAD must be its tip on GitHub
# (the release is committed on top of it, and the tag points there).
$branch = $null; $upstream = $null
if (-not $NoPublish) {
    if ($null -eq (Invoke-Tool $Git @('fetch', '--quiet', 'origin'))) { Die 'git fetch origin failed' }
    $onBranch = Invoke-Tool $Git @('symbolic-ref', '--quiet', '--short', 'HEAD')
    if ($null -eq $onBranch) { Die 'HEAD is not on a branch: switch to the branch the release goes on (git switch main)' }
    $branch = $onBranch[0].Trim()
    $tracks = Invoke-Tool $Git @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    if ($null -eq $tracks -or $tracks[0].Trim() -ne "origin/$branch") { Die "$branch does not track origin/$branch (git branch --set-upstream-to=origin/$branch)" }
    $upstream = (Invoke-Tool $Git @('rev-parse', "origin/$branch"))[0].Trim()
    $atTip = if ($resume) { @($resume.commit, $commit) } else { @($commit) }
    if ($atTip -notcontains $upstream) {
        if ((Test-Tool $Git @('merge-base', '--is-ancestor', $commit, $upstream)) -eq 0) {
            Die "origin/$branch has commits this checkout lacks: git pull --ff-only, then run again"
        }
        if ($resume) {
            Die "origin/$branch moved on after this release's commit was made here: drop it (git reset --keep HEAD~1), git pull --ff-only, and run again (the APKs are built and signed again)"
        }
        Die "commit $($commit.Substring(0, 12)) is not on GitHub yet: push it first (the release is committed on top of it, and its tag points there)"
    }
}

$signed = 'android\app\build\outputs\apk\release-signed'
$sumsFile = Join-Path $signed 'SHA256SUMS'
$isPrerelease = $false

# The commit the tag names on GitHub (an annotated tag peeled to its
# commit), or $null.
function Get-RemoteTag {
    $lines = Invoke-Tool $Git @('ls-remote', 'origin', "refs/tags/$tag", "refs/tags/$tag^{}")
    if ($null -eq $lines) { Die 'git ls-remote origin failed' }
    $plain = $null; $peeled = $null
    foreach ($line in @($lines | Where-Object { $_.Trim() })) {
        $parts = $line.Trim() -split '\s+'
        if ($parts.Count -ne 2) { continue }
        if ($parts[1] -eq "refs/tags/$tag^{}") { $peeled = $parts[0] } elseif ($parts[1] -eq "refs/tags/$tag") { $plain = $parts[0] }
    }
    if ($peeled) { return $peeled }
    return $plain
}

# Check the release commit is exactly android/release.json on top of the
# commit the APKs were built from, push it and its tag to GitHub together
# (unless GitHub has them), publish the release at that tag (unless it
# exists), check the tag is that commit, and download each file back by the
# URL android/release.json names.
function Publish-Release([string]$ReleaseCommit, [string]$BuiltFrom, [bool]$Exists) {
    $parent = Invoke-Tool $Git @('rev-parse', '--verify', '--quiet', "$ReleaseCommit^")
    $changed = Invoke-Tool $Git @('diff', '--name-only', $BuiltFrom, $ReleaseCommit)
    if ($null -eq $parent -or $parent[0].Trim() -ne $BuiltFrom -or $null -eq $changed -or
            (@($changed | Where-Object { $_ }) -join ',') -ne 'android/release.json') {
        Die "the release commit $($ReleaseCommit.Substring(0, 12)) is not android/release.json on top of $($BuiltFrom.Substring(0, 12)), the commit the APKs were built from: refusing to tag it"
    }
    $manifestText = Invoke-Tool $Git @('show', "$($ReleaseCommit):android/release.json")
    if ($null -eq $manifestText) { Die "the release commit has no android/release.json" }
    $manifest = (($manifestText -join "`n") | ConvertFrom-Json).release
    if (-not $manifest -or [string]$manifest.commit -ne $BuiltFrom) { Die "the release commit's android/release.json doesn't name $BuiltFrom as the commit its APKs were built from" }

    $remoteTag = Get-RemoteTag
    if ($remoteTag -and $remoteTag -ne $ReleaseCommit) {
        Die "the tag $tag already exists on GitHub at another commit ($remoteTag): a published tag never moves"
    }
    # A tag of that name only here would stop the tag being fetched after publishing.
    $localTag = Invoke-Tool $Git @('rev-parse', '--quiet', '--verify', "refs/tags/$tag^{commit}")
    if ($null -ne $localTag -and $localTag[0].Trim() -ne $ReleaseCommit) {
        Die "this checkout has a tag $tag at $($localTag[0].Trim().Substring(0, 12)) that GitHub doesn't: delete it (git tag -d $tag) and run again"
    }
    # The branch and the tag in one atomic push: a tag pushed meanwhile by
    # anyone else fails both, so gh never attaches the release elsewhere.
    $refspecs = @()
    if ($script:upstream -ne $ReleaseCommit) { $refspecs += "$($ReleaseCommit):refs/heads/$branch" }
    if (-not $remoteTag) { $refspecs += "$($ReleaseCommit):refs/tags/$tag" }
    if ($refspecs.Count -gt 0) {
        Say "pushing the release commit $($ReleaseCommit.Substring(0, 12)) to origin/$branch and tagging it $tag, together..."
        if ($null -eq (Invoke-Tool $Git (@('push', '--atomic', '--quiet', 'origin') + $refspecs))) {
            $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }
            Die 'the push failed (nothing was pushed): android/release.json is committed here but not on GitHub. Fix that and run this script again; it carries on from the release commit (nothing is signed again)'
        }
    }
    if (-not $Exists) {
        $rows = foreach ($entry in $manifest.apks) {
            $who = if ($entry.abi -eq 'arm64-v8a') { 'most phones (64-bit)' } else { 'older or cheaper phones (32-bit)' }
            "| $($entry.file) | $who | ``$($entry.sha256)`` |"
        }
        $notes = @(
            "Domovoi $versionName. The Android app (versionCode $versionCode), built from $($manifest.commit); the release is tagged at $ReleaseCommit, which adds android/release.json naming these files. scripts/release/finish-release adds the Windows Setup, SHA256SUMS for every file and the release notes.",
            '',
            'Install the app from your own Domovoi server: on the dashboard, Settings -> Rooms -> add a spare device (for a room) or Settings -> Phones & browsers -> get the Domovoi app. The server checks these files against android/release.json before it hands them out.',
            '',
            '| file | for | SHA-256 |',
            '|---|---|---|'
        ) + $rows + @('', "Signing certificate SHA-256: ``$($manifest.cert_sha256)`` (check with ``apksigner verify --print-certs <file>``).")
        $notesFile = Join-Path $signed 'NOTES.md'
        $utf8 = New-Object Text.UTF8Encoding $false
        [IO.File]::WriteAllText((Join-Path $repo $notesFile), ($notes -join "`n") + "`n", $utf8)
        [IO.File]::WriteAllText((Join-Path $repo $sumsFile), (($manifest.apks | ForEach-Object { "$($_.sha256)  $($_.file)" }) -join "`n") + "`n", $utf8)

        $kind = if ($isPrerelease) { 'a pre-release' } else { 'the Latest release' }
        Say "publishing $tag on $RepoSlug at $($ReleaseCommit.Substring(0, 12)), $kind..."
        $assets = @($manifest.apks | ForEach-Object { Join-Path $signed $_.file }) + @($sumsFile)
        $ghArgs = @('release', 'create', $tag) + $assets + @('--repo', $RepoSlug, '--target', $ReleaseCommit, '--verify-tag',
            '--title', "Domovoi v$versionName", '--notes-file', $notesFile)
        $ghArgs += if ($isPrerelease) { '--prerelease' } else { '--latest' }
        $created = Invoke-Tool $Gh $ghArgs
        if ($null -eq $created) {
            $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }
            Die 'gh release create failed: run this script again; it carries on from the release commit'
        }
        $created | ForEach-Object { Write-Host "  $_" }
    } else {
        Say "$tag is already on $RepoSlug, so checking it"
    }

    # The tag must be the release commit.
    if ($null -eq (Invoke-Tool $Git @('fetch', '--quiet', 'origin', "refs/tags/$($tag):refs/tags/$tag"))) {
        $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }
        Die "can't fetch the tag $tag from GitHub (a different tag $tag here? git tag -d $tag, then run again to check)"
    }
    $tagged = (Invoke-Tool $Git @('rev-parse', "$tag^{commit}"))[0].Trim()
    if ($tagged -ne $ReleaseCommit) { Die "the tag $tag is $tagged, not the release commit $ReleaseCommit" }

    Say 'checking the published files by their manifest URLs...'
    $checkDir = Join-Path ([IO.Path]::GetTempPath()) ('domovoi-release-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $checkDir
    try {
        foreach ($entry in $manifest.apks) {
            $dest = Join-Path $checkDir $entry.file
            if ((Test-Tool $Curl @('-fsSL', '--retry', '3', '-o', $dest, $entry.url)) -ne 0) { Die "can't download $($entry.url) (run this script again to check once more)" }
            if ((Get-Sha256 $dest) -ne $entry.sha256) { Die "$($entry.url) is not the file that was signed" }
        }
    } finally { Remove-Item -Recurse -Force $checkDir -ErrorAction SilentlyContinue }

    Say "done: $tag is published at $($ReleaseCommit.Substring(0, 12)), with the android/release.json naming its APKs."
    Write-Host 'Next: the Windows Setup (built from the tag, pinned to it), one SHA256SUMS and the notes:'
    Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release\finish-release.ps1 -Tag $tag"
}

# --- carry on from a release commit --------------------------------------
if ($resume) {
    $versionName = [string]$resume.version_name; $versionCode = [long]$resume.version_code
    $tag = "v$versionName"
    $isPrerelease = [bool]$Prerelease
    Say "Domovoi $versionName (versionCode $versionCode): HEAD $($commit.Substring(0, 12)) is its release commit (android/release.json on top of $($resume.commit.Substring(0, 12)), which the APKs were built from), so this run carries on from there; nothing is built or signed again"
    # The first run's checks against the release before it, on what is committed.
    $prevCode = 0; $prevCert = $null
    $prevText = Invoke-Tool $Git @('show', "$($resume.commit):android/release.json")
    if ($null -ne $prevText) {
        try {
            $prev = (($prevText -join "`n") | ConvertFrom-Json).release
            if ($prev) { $prevCode = [long]$prev.version_code; $prevCert = [string]$prev.cert_sha256 }
        } catch { }
    }
    if ($versionCode -le $prevCode) { Die "android/release.json at HEAD names versionCode $versionCode, not above the last release's ($prevCode)" }
    if ($prevCert -and $prevCert -ne [string]$resume.cert_sha256) { Die "android/release.json at HEAD names another key ($($resume.cert_sha256)) than the last release's ($prevCert)" }
    $exists = (-not $NoPublish) -and ((Test-Tool $Gh @('release', 'view', $tag, '--repo', $RepoSlug)) -eq 0)
    if (-not $exists) {
        $checked = Invoke-Tool $python @('-m', 'domovoi.android_app', 'check', 'android\release.json', $signed)
        if ($null -eq $checked) {
            $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }
            Die "the signed APKs android\release.json pins are not in $signed (or have changed), so they can't be published from here: drop the release commit (git reset --keep HEAD~1), raise versionCode, and release again"
        }
        $checked | ForEach-Object { Write-Host "  $_" }
    }
    if ($NoPublish) {
        Say "not published (-NoPublish). A real run pushes the release commit if GitHub lacks it, publishes $tag at it unless it exists, and checks the files."
        exit 0
    }
    Publish-Release -ReleaseCommit $commit -BuiltFrom ([string]$resume.commit) -Exists $exists
    exit 0
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
    $pkg = $Matches[1]; $vc = [long]$Matches[2]; $vn = $Matches[3]
    $native = ($badging | Where-Object { $_ -like 'native-code: *' } | Select-Object -First 1)
    if ($pkg -ne $Package) { Die "$apk is $pkg, not $Package" }
    if ($native -ne "native-code: '$abi'") { Die "$apk carries $native, not '$abi' only" }
    if ($null -eq $versionName) { $versionName = $vn; $versionCode = $vc }
    if ($vn -ne $versionName -or $vc -ne $versionCode) { Die 'the APKs disagree on their version' }
}
# The project-wide tag, v<versionName> (v1.0.0-rc1): domovoi.android_app's
# tag_for, which the manifest's URLs are checked against.
$tag = "v$versionName"
# A full release, marked Latest, unless -Prerelease (a release candidate too:
# the suffix says what it is, owner decision 2026-10-15).
$isPrerelease = [bool]$Prerelease
Say "Domovoi $versionName (versionCode $versionCode), commit $($commit.Substring(0, 12))"

# versionCode is YYMMDDNN (android\app\build.gradle.kts): the build's date
# and that day's number, one sequence for these APKs and the Play bundle
# (play-bundle.ps1 checks the same shape).
if ("$versionCode" -notmatch '^[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])(0[1-9]|[1-9][0-9])$') {
    Die "versionCode $versionCode is not YYMMDDNN (the build's date and that day's number, e.g. 26101501): set it in android\app\build.gradle.kts, commit, and run again"
}

$prevCode = 0; $prevCert = $null
try {
    $prev = (Get-Content -Raw -Encoding UTF8 'android\release.json' | ConvertFrom-Json).release
    if ($prev) { $prevCode = [long]$prev.version_code; $prevCert = [string]$prev.cert_sha256 }
} catch { }
if ($versionCode -le $prevCode) {
    Die "versionCode $versionCode is not above the last release's ($prevCode): raise versionCode (YYMMDDNN: the build's date and that day's number) and versionName in android\app\build.gradle.kts, commit, and run again"
}
if (-not $NoPublish) {
    if ((Test-Tool $Gh @('release', 'view', $tag, '--repo', $RepoSlug)) -eq 0) { Die "a release $tag already exists on $RepoSlug" }
    $remoteTag = Invoke-Tool $Git @('ls-remote', '--tags', 'origin', "refs/tags/$tag")
    if ($null -eq $remoteTag) { Die 'git ls-remote origin failed' }
    if (@($remoteTag | Where-Object { $_.Trim() }).Count -gt 0) { Die "the tag $tag already exists on GitHub (a published tag never moves: raise the version)" }
    if ($null -ne (Invoke-Tool $Git @('rev-parse', '--quiet', '--verify', "refs/tags/$tag"))) {
        Die "this checkout has a tag $tag that GitHub doesn't: delete it (git tag -d $tag) and run again"
    }
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

# The R8 mapping of the build these APKs came from (the release build is
# minified: android\app\build.gradle.kts): what turns a stack trace from them
# back into class and method names (R8's retrace). Kept beside the signed
# files for the release's records; not attached to the GitHub release (a
# household has no use for it, and the same commit builds the same mapping).
$mapping = 'android\app\build\outputs\mapping\release\mapping.txt'
if (-not (Test-Path -LiteralPath $mapping)) { Die "no $mapping`: the release build writes it (build again: drop -SkipBuild)" }
Copy-Item -LiteralPath $mapping -Destination (Join-Path $signed "domovoi-$versionName-mapping.txt")
Say "kept the R8 mapping as $signed\domovoi-$versionName-mapping.txt"

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
    $kind = if ($isPrerelease) { 'a pre-release' } else { 'the Latest release' }
    Say "not published (-NoPublish). Signed APKs, SHA256SUMS and the manifest it would commit: $signed\"
    Say "A real run commits that manifest (android: release $versionName [skip ci]), pushes it, and publishes $tag at that commit as $kind."
    exit 0
}

# --- publish: commit the manifest on the build commit, push, tag it --------
# The release commit is made with git's plumbing on the build commit itself,
# and HEAD moves to it only if HEAD is still the build commit: a commit made
# in this checkout meanwhile (another terminal, an agent) is refused, never
# released on top of.
$blob = Invoke-Tool $Git @('hash-object', '-w', '--', $staged)
if ($null -eq $blob) { Die 'git hash-object failed' }
$index = Join-Path $repo (Join-Path $signed 'release.index')
Remove-Item -LiteralPath $index -Force -ErrorAction SilentlyContinue
$env:GIT_INDEX_FILE = $index
try {
    $tree = $null
    if ($null -ne (Invoke-Tool $Git @('read-tree', $commit)) -and
            $null -ne (Invoke-Tool $Git @('update-index', '--add', '--cacheinfo', "100644,$($blob[0].Trim()),android/release.json"))) {
        $tree = Invoke-Tool $Git @('write-tree')
    }
} finally {
    Remove-Item Env:GIT_INDEX_FILE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $index -Force -ErrorAction SilentlyContinue
}
if ($null -eq $tree) { $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }; Die 'making the release commit failed' }
$made = Invoke-Tool $Git @('commit-tree', $tree[0].Trim(), '-p', $commit, '-m', "android: release $versionName [skip ci]")
if ($null -eq $made) { $script:LastToolOutput | ForEach-Object { Write-Host "  $_" }; Die 'git commit-tree failed' }
$releaseCommit = $made[0].Trim()
if ($null -eq (Invoke-Tool $Git @('update-ref', '-m', "release: android/release.json for $versionName", 'HEAD', $releaseCommit, $commit))) {
    $now = Invoke-Tool $Git @('rev-parse', 'HEAD')
    $nowText = if ($now) { $now[0].Trim().Substring(0, 12) } else { 'unknown' }
    Die "HEAD moved while the release was being made (it is $nowText now; the APKs are built from $($commit.Substring(0, 12))): nothing was committed or pushed. Look at that commit, then release again from a clean checkout of the branch's tip"
}
# The working tree and the index follow HEAD for that one file.
Copy-Item -LiteralPath $staged -Destination 'android\release.json' -Force
$null = Invoke-Tool $Git @('reset', '--quiet', '--', 'android/release.json')
Say "committed android/release.json as $($releaseCommit.Substring(0, 12)) (android: release $versionName [skip ci]) on $($commit.Substring(0, 12)): the tag goes there"
Publish-Release -ReleaseCommit $releaseCommit -BuiltFrom $commit -Exists $false
