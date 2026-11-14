#!/usr/bin/env bash
# Finish a Domovoi release: the Windows Setup, one SHA256SUMS, the notes,
# then stable. The release is v<Domovoi's version> (pyproject.toml). When
# the app changed, run by the owner after android/scripts/release.sh (or
# .ps1) has published that tag with the APKs; when it didn't, alone, from
# the release commit pushed to main: it tags that commit and creates the
# release (no APKs; android/release.json still names the release holding
# them). From a clean checkout at the tag or after it.
# The twin of finish-release.ps1; the work is finish_release.py beside it
# (Python 3.11+, standard library), which says what each step checks.
#
#   bash scripts/release/finish-release.sh                    # check, build Setup, SHA256SUMS, notes, publish, stable
#   bash scripts/release/finish-release.sh --no-publish       # all but GitHub: what it would do
#   bash scripts/release/finish-release.sh --tag v1.0.0       # default: v + pyproject.toml's version
#   bash scripts/release/finish-release.sh --prerelease       # a release it creates (app unchanged): a pre-release
#   bash scripts/release/finish-release.sh --setup FILE       # a DomovoiSetup.exe built by build.ps1 -Pin <tag> from the tag
#   bash scripts/release/finish-release.sh --setup F --tray T # ...and its DomovoiTray.exe (default: bin/ beside the Setup)
#   ... --setup F --satellite-setup S                         # ...and its DomovoiSatelliteSetup.exe (default: beside the Setup)
#   bash scripts/release/finish-release.sh --since v1.0.0-rc2 # the tag the notes count from
#   ... --signing-key ~/keys/domovoi-release                  # the release key's private half (or DOMOVOI_RELEASE_SIGNING_KEY)
#   ... --signing-agent                                       # the key an ssh-agent holds (the release workflow)
#   bash scripts/release/finish-release.sh --plan plan.json   # the release workflow's gate: what to run, changes nothing
#
# The Setup builds on Windows only (windows/build.ps1 -Pin in a clean
# worktree of the tag, run through Windows PowerShell from Git Bash);
# elsewhere pass --setup. Needs git with push
# access and gh signed in, and ssh-keygen for a tag that pins the release
# key (it signs the Setup for the Windows update). Never reads a key or a
# password itself.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
cd "$repo"

python=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    python="$candidate"; break
  fi
done
[ -n "$python" ] || { echo "finish-release: no Python 3.11+ (it runs finish_release.py)" >&2; exit 1; }

exec "$python" "$here/finish_release.py" "$@"
