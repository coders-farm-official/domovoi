#!/usr/bin/env bash
# pkgtest.sh — compile and unit-test the Swift packages on the Mac, and
# nothing else: no Simulator, no xcodebuild, no screen, no signing.
#
# This is the loop every DomovoiKit batch works to. It takes about a minute,
# it needs no Mac lock (the Mac-installer track allows compiling and unit
# tests alongside its own work; only the Simulator has to wait), and it is the
# fast half of `test.sh` without needing the Mac's repository to be set up by
# hand first.
#
# Run from THIS PC (Git Bash), from inside the worktree you are working in:
#     ios/scripts/pkgtest.sh                 # every package
#     ios/scripts/pkgtest.sh DomovoiKit      # one package
#     ios/scripts/pkgtest.sh --lines 400     # more of the run's output
#
# COMMIT FIRST. It pushes HEAD and the Mac tests HEAD, not your working tree;
# it says so loudly when the two differ.
#
# How it differs from the other two scripts here: `remote.sh` runs one command
# on the Mac over SSH and `test.sh` runs ON the Mac. This one is the whole
# round trip from this PC — push the commit, check it out in a directory of
# its own, `swift test` — so the only thing an agent needs in order to
# reproduce a run is this file.
#
# Environment (same names and defaults as remote.sh)
#   DOMOVOI_MAC_HOST    required. The Mac's address: a fixed IP from the
#                       router, or its name.local.
#   DOMOVOI_MAC_USER    the build user. Default: domovoibuild.
#   DOMOVOI_MAC_KEY     the private key. Default: ~/.ssh/domovoi_mac_ed25519.
#   DOMOVOI_MAC_PORT    the ssh port. Default: 22.
#   DOMOVOI_MAC_REMOTE  the git remote pointing at the Mac's bare repository.
#                       Default: mac.
#   DOMOVOI_MAC_WORK    the checkout on the Mac, under the build user's home.
#                       Default: domovoi-ios/work-<branch>, so two branches
#                       never fight over one directory.
set -uo pipefail

say() { printf '%s\n' "$*" >&2; }
die() { say "pkgtest.sh: $*"; exit 1; }

only=""
lines=80
while [ $# -gt 0 ]; do
	case "$1" in
		--lines) lines="${2:?--lines needs a number}"; shift 2 ;;
		--help|-h)
			sed -n '2,/^set -uo/p' "$0" | sed 's/^# \{0,1\}//'
			exit 0 ;;
		-*) die "unknown option $1" ;;
		*) only="$1"; shift ;;
	esac
done

# No apostrophe in that message: bash mis-parses a single quote inside
# ${VAR:?word} that is itself inside double quotes.
host="${DOMOVOI_MAC_HOST:?set it to the address of the Mac that builds}"
user="${DOMOVOI_MAC_USER:-domovoibuild}"
key="${DOMOVOI_MAC_KEY:-$HOME/.ssh/domovoi_mac_ed25519}"
port="${DOMOVOI_MAC_PORT:-22}"
remote="${DOMOVOI_MAC_REMOTE:-mac}"

[ -f "$key" ] || die "no key at $key"
git rev-parse --git-dir >/dev/null 2>&1 || die "not in a git worktree"
git remote get-url "$remote" >/dev/null 2>&1 || die "no git remote '$remote' — add one pointing at the Mac's bare repository"

branch=$(git rev-parse --abbrev-ref HEAD)
head=$(git rev-parse HEAD)
work="${DOMOVOI_MAC_WORK:-domovoi-ios/work-${branch##*/}}"

dirty=$(git status --porcelain | head -5)
if [ -n "$dirty" ]; then
	say "!! uncommitted changes — the Mac will test HEAD ($head), NOT these:"
	say "$dirty"
fi

export GIT_SSH_COMMAND="ssh -i $key -p $port -o IdentitiesOnly=yes -o BatchMode=yes"
say ">> pushing $branch ($head) to the Mac"
git push --force "$remote" "HEAD:refs/heads/$branch" 2>&1 | tail -2 || die "push failed"

ssh -i "$key" -p "$port" -o BatchMode=yes "$user@$host" "bash -s" <<REMOTE
set -uo pipefail
export PATH="\$HOME/tools/XcodeGen/.build/release:/opt/homebrew/bin:/usr/local/bin:\$PATH"
if [ ! -d "\$HOME/$work/.git" ]; then
	git clone -q "\$HOME/domovoi-ios/domovoi.git" "\$HOME/$work" || exit 1
fi
cd "\$HOME/$work" || exit 1
git fetch -q origin && git checkout -q -B "$branch" "$head" || exit 1
echo ">> on \$(git rev-parse --short HEAD) : \$(git log -1 --format=%s)"
cd ios || exit 1
status=0
for package in Packages/*/; do
	name="\$(basename "\$package")"
	[ -n "$only" ] && [ "\$name" != "$only" ] && continue
	if [ ! -d "\$package/Tests" ]; then
		echo ">> \$name: no tests, building only"
		# nice + caffeinate: the Mac must not doze mid-build, and Kamron's
		# own work on it stays smooth.
		( cd "\$package" && caffeinate -i nice -n 10 swift build 2>&1 | tail -40 ) || status=1
		continue
	fi
	echo ">> \$name: swift test"
	( cd "\$package" && caffeinate -i nice -n 10 swift test 2>&1 \
		| grep -vE "^\[[0-9]+/[0-9]+\] (Compiling|Emitting|Write|Planning|Compiling for)" \
		| grep -vE "^Test Case .*' (started\.|passed \()" \
		| grep -vE "^Test Suite .* (started|passed) at " \
		| tail -$lines ) || status=1
done
exit \$status
REMOTE
rc=$?
say ">> pkgtest exit: $rc"
exit $rc
