<#
.SYNOPSIS
Finish a Domovoi release: the Windows Setup, one SHA256SUMS, the notes, then stable.

.DESCRIPTION
Run by the owner after android\scripts\release.ps1 has published
v<version> (the two APKs, tagged at the commit that adds
android/release.json to the commit they were built from). From a clean
checkout at the tag or after it:

1. checks origin is the GitHub repository, the tag (annotated is fine)
   and its pyproject version, that the tag's commit is only
   android/release.json on the build commit, the GitHub release's APKs,
   and that stable can fast-forward (a release without a suffix);
2. builds DomovoiSetup.exe from the tag's own files: a clean worktree of
   the tag, where windows\build.ps1 -Pin <tag> moves the Setup's pins to
   the tag's commit and builds (anything but pins.iss changed, or a bundled
   provision.sh, is refused);
3. writes one SHA256SUMS (LF) for every asset;
4. writes the notes: docs/release/v<version>.md at the tag, or a summary of
   docs/RELEASE_NOTES.md since the previous tag with links into the full
   file (GitHub caps a release body at 125,000 characters);
5. uploads the Setup and SHA256SUMS (--clobber), sets the title and the
   notes, and keeps a pre-release one (only when GitHub marks it so; every
   other release, release candidates included, is a full one, Latest);
6. checks every file on GitHub against SHA256SUMS;
7. LAST, a release without a suffix (and not a pre-release) only: the
   tag on GitHub is checked again, then git push origin
   <its commit>:refs/heads/stable, the SHA checked, never forced (a stable
   the tag doesn't come after is refused).

Nothing on GitHub changes until every check has passed. The work is done by
finish_release.py beside this script (Python 3.11+, standard library); the
bash twin is finish-release.sh. Needs git with push access, gh signed in,
Inno Setup 6 (windows\build.ps1). Never touches a keystore or a password.

.PARAMETER Tag
The release, v<version>. Default: v + the Android versionName.

.PARAMETER NoPublish
Run the checks, build the Setup, write SHA256SUMS and the notes into
windows\build\release\<tag>\, and print what a real run would do. Nothing
on GitHub changes (it fetches and reads the release).

.PARAMETER Setup
A DomovoiSetup.exe already built by windows\build.ps1 -Pin <tag> in a
clean worktree of the tag, instead of building it.

.PARAMETER Since
The tag the notes' summary counts from (default: the previous one).

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release\finish-release.ps1 -NoPublish
#>
[CmdletBinding()]
param(
    [string]$Tag,
    [switch]$NoPublish,
    [string]$Setup,
    [string]$Since
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3

$python = $null
foreach ($candidate in @('python', 'py', 'python3')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $ErrorActionPreference = 'Continue'
    $null = & $cmd.Source -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>&1
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = 'Stop'
    if ($ok) { $python = $cmd.Source; break }
}
if (-not $python) { Write-Host 'finish-release: no Python 3.11+ (it runs finish_release.py)' -ForegroundColor Red; exit 1 }

$script = Join-Path $PSScriptRoot 'finish_release.py'
$pyArgs = @($script)
if ($Tag) { $pyArgs += @('--tag', $Tag) }
if ($NoPublish) { $pyArgs += '--no-publish' }
if ($Setup) { $pyArgs += @('--setup', $Setup) }
if ($Since) { $pyArgs += @('--since', $Since) }

Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
& $python @pyArgs
exit $LASTEXITCODE
