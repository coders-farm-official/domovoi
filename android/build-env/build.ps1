<#
.SYNOPSIS
Build the Domovoi app's unsigned release APKs from one commit in the pinned
Linux build image (Docker), the way CI's reproducibility check and F-Droid
build them.

.DESCRIPTION
The PowerShell twin of build.sh beside it; release.ps1 runs it. A clean,
shallow checkout of exactly one commit (by default HEAD) from this
checkout's .git, mounted read-only, built inside the image that
android\build-env\Dockerfile makes (built once per change to that folder).
So no line ending, path, locale, JDK or SDK of this PC reaches the APK, and
only committed files are built.

Writes to -Out (default android\app\build\outputs\apk\release, where
release.ps1 signs from): app-<abi>-release-unsigned.apk, mapping.txt and
BUILD-ENV.txt; with the default -Out, mapping.txt also goes where Gradle puts
it (android\app\build\outputs\mapping\release\).

Needs Docker Desktop running Linux containers. On a PC with 32 or more
logical processors the build runs on 16-31 (the E-cores of the owner's PC);
$env:DOMOVOI_BUILD_CPUS overrides that (a --cpuset-cpus value). Gradle's
downloads are kept between runs in the Docker volume domovoi-android-gradle
($env:DOMOVOI_BUILD_GRADLE_VOLUME, or 'none' for a fresh one each time);
gradle\verification-metadata.xml checks every one of them either way.

.PARAMETER Commit
The commit to build (default HEAD).

.PARAMETER Out
The folder the APKs go to.

.PARAMETER Abis
Each item is one Gradle run (default 'arm64-v8a,armeabi-v7a', a release's
two APKs); see build-apks.sh.

.PARAMETER ImageOnly
Only make sure the commit's image exists, and write its name (release.ps1
runs apksigcopier in it).

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File android\build-env\build.ps1
#>
[CmdletBinding()]
param(
    [string]$Commit = 'HEAD',
    [string]$Out,
    [string[]]$Abis = @(),
    [switch]$ImageOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3

function Die([string]$message) { Write-Host "build.ps1: $message" -ForegroundColor Red; exit 1 }
function Say([string]$message) { Write-Host "build.ps1: $message" }

# A native program's first output line, or $null when it failed (Windows
# PowerShell turns stderr into errors, which 'Stop' would make fatal).
function Get-ToolLine([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $out = & $exe @arguments 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return (@($out) | Select-Object -First 1)
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repo

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die 'no docker: install Docker Desktop, or build without it (android\README.md)' }
if ((Get-ToolLine 'docker' @('info', '--format', '{{.OSType}}')) -ne 'linux') {
    Die 'docker info fails or the engine is not a Linux one: start Docker Desktop (Linux containers)'
}

$sha = Get-ToolLine 'git' @('rev-parse', '--verify', "$Commit^{commit}")
if (-not $sha) { Die "no commit $Commit" }
$sha = $sha.Trim()
$gitDir = Get-ToolLine 'git' @('rev-parse', '--path-format=absolute', '--git-common-dir')
if (-not $gitDir) { Die 'git rev-parse --git-common-dir failed (git 2.31 or later?)' }
$tree = Get-ToolLine 'git' @('rev-parse', '--verify', '--quiet', "$($sha):android/build-env")
if (-not $tree) { Die "commit $($sha.Substring(0, 12)) has no android/build-env" }
$image = "domovoi-android-build-env:$($tree.Trim().Substring(0, 12))"

$defaultOut = -not $Out
if ($defaultOut) { $Out = 'android\app\build\outputs\apk\release' }
if (-not $ImageOnly) {
    $null = New-Item -ItemType Directory -Force -Path $Out
    $Out = (Resolve-Path $Out).Path
}

$work = Join-Path ([IO.Path]::GetTempPath()) ('domovoi-android-build-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $work
$name = "domovoi-android-build-$PID"
try {
    # The image, from the commit's own android/build-env, named by its tree.
    $ErrorActionPreference = 'Continue'
    $null = & docker image inspect $image 2>$null
    $have = $LASTEXITCODE -eq 0
    $ErrorActionPreference = 'Stop'
    if (-not $have) {
        Say "building the image $image (once per android/build-env)..."
        $ctx = Join-Path $work 'context'
        $null = New-Item -ItemType Directory -Path $ctx
        $tar = Join-Path $work 'context.tar'
        & git archive --format=tar -o $tar "$($sha):android/build-env"
        if ($LASTEXITCODE -ne 0) { Die 'git archive failed' }
        & tar.exe -xf $tar -C $ctx
        if ($LASTEXITCODE -ne 0) { Die 'unpacking the image context failed' }
        $ErrorActionPreference = 'Continue'
        $null = & docker build -q -t $image $ctx
        $built = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($built -ne 0) { Die 'building the image failed' }
    }

    $run = @('run', '--rm', '--name', $name)
    if ($ImageOnly) { Write-Output $image; return }

    if ($env:DOMOVOI_BUILD_CPUS) { $run += @('--cpuset-cpus', $env:DOMOVOI_BUILD_CPUS) }
    elseif ([Environment]::ProcessorCount -ge 32) { $run += @('--cpuset-cpus', '16-31') }
    $volume = if ($env:DOMOVOI_BUILD_GRADLE_VOLUME) { $env:DOMOVOI_BUILD_GRADLE_VOLUME } else { 'domovoi-android-gradle' }
    if ($volume -ne 'none') { $run += @('-v', "$($volume):/gradle", '-e', 'GRADLE_USER_HOME=/gradle') }
    $outMount = $work -replace '\\', '/'
    $run += @('--mount', "type=bind,source=$($gitDir.Trim()),target=/git,readonly",
              '--mount', "type=bind,source=$outMount/out,target=/out",
              $image, 'domovoi-build-commit', $sha) + $Abis
    $null = New-Item -ItemType Directory -Path (Join-Path $work 'out')

    Say "building $($sha.Substring(0, 12)) in $image..."
    $ErrorActionPreference = 'Continue'   # Gradle writes warnings to stderr
    & docker @run
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { Die 'the build failed' }

    $result = Join-Path $work 'out'
    if ($defaultOut) {
        Get-ChildItem -LiteralPath $Out -Filter '*.apk' | Remove-Item -Force
        $mapDir = 'android\app\build\outputs\mapping\release'
        $null = New-Item -ItemType Directory -Force -Path $mapDir
        Copy-Item -LiteralPath (Join-Path $result 'mapping.txt') -Destination (Join-Path $mapDir 'mapping.txt') -Force
    }
    Copy-Item -Path (Join-Path $result '*') -Destination $Out -Recurse -Force
    Say "done: $Out"
    Get-ChildItem -LiteralPath $Out -Filter '*.apk' | ForEach-Object { Write-Host "  $($_.Name)  $($_.Length) bytes" }
} finally {
    $ErrorActionPreference = 'Continue'
    $null = & docker rm -f $name 2>$null
    Remove-Item -Recurse -Force -LiteralPath $work -ErrorAction SilentlyContinue
}
exit 0
