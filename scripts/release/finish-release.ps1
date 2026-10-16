<#
.SYNOPSIS
Finish a Domovoi release: the Windows Setup, one SHA256SUMS, the notes, then stable.

.DESCRIPTION
Run by the owner after android\scripts\release.ps1 has published
v<version> (the two APKs, tagged at the commit that adds
android/release.json) and the Windows Setup's pins have been moved to that
commit and pushed. From a clean checkout at the pins commit
(git pull --ff-only):

1. checks the tag, its android/release.json, the pins (DomovoiCommit is
   the tag's commit, ProvisionSha256 right), the GitHub release's APKs, and
   that stable can fast-forward (a release without a suffix);
2. builds DomovoiSetup.exe with windows\build.ps1;
3. writes one SHA256SUMS (LF) for every asset;
4. writes the notes: docs/release/v<version>.md at the tag, or a summary of
   docs/RELEASE_NOTES.md since the previous tag with links into the full
   file (GitHub caps a release body at 125,000 characters);
5. uploads the Setup and SHA256SUMS (--clobber), sets the title, the notes
   and pre-release or Latest;
6. checks every file on GitHub against SHA256SUMS;
7. LAST, a release without a suffix only: git push origin
   "v<version>^{commit}:refs/heads/stable", never forced (a stable the tag
   doesn't come after is refused).

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
A DomovoiSetup.exe already built by windows\build.ps1 from this commit,
instead of building it.

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
