#!/usr/bin/env bash
# remote.sh — run one command on Kamron's Mac, over SSH, as the build user.
#
# Every other script that needs the Mac goes through this one, so the rules
# live in one place: the key to use, the low priority, the no-doze wrapper,
# and the ports that must never be forwarded.
#
# Run from this PC (Git Bash), not on the Mac.
set -euo pipefail

say() { printf '%s\n' "$*" >&2; }
die() { say "remote.sh: $*"; exit 1; }

usage() {
	cat <<'USAGE'
usage: remote.sh [--forward PORT] [--quiet] -- COMMAND [ARG ...]
       remote.sh --check
       remote.sh --help

Runs COMMAND on the Mac as the build user, in its home directory, wrapped in
caffeinate (so the Mac cannot doze mid-build) and at low priority (so Kamron's
own work on it stays smooth).

Options
  --forward PORT   Also reverse-forward PORT from the Mac to this PC, for a
                   throwaway server a Simulator test has to reach. Only ports
                   6600-6699 are allowed: those are this session's lane.
  --quiet          Do not print the command before running it.
  --check          Connect, print who and where we are, and exit.
  --help           This text.

Environment
  DOMOVOI_MAC_HOST   required. The Mac's address: a fixed IP from the router,
                     or its name.local.
  DOMOVOI_MAC_USER   the build user. Default: domovoibuild.
  DOMOVOI_MAC_KEY    the private key. Default: ~/.ssh/domovoi_mac_ed25519.
  DOMOVOI_MAC_PORT   the ssh port. Default: 22.

Examples
  DOMOVOI_MAC_HOST=192.168.1.40 ./remote.sh --check
  DOMOVOI_MAC_HOST=192.168.1.40 ./remote.sh -- ios/scripts/test.sh
  DOMOVOI_MAC_HOST=192.168.1.40 ./remote.sh --forward 6612 -- ios/scripts/test.sh
USAGE
}

# ---- the ports that are never touched ---------------------------------------
#
# 6369 and 6370 are Kamron's live Domovoi (core and web), 6432 is the live
# Postgres, and 11434 is Ollama. The Mac's build user must never be able to
# reach any of them, and a typo in a --forward is the realistic way that
# happens. So the refusal is in the code, not in a comment.
FORBIDDEN_PORTS="6369 6370 6432 11434"
LANE_LOW=6600
LANE_HIGH=6699

forward=""
quiet=0
check=0

while [ $# -gt 0 ]; do
	case "$1" in
		--help|-h) usage; exit 0 ;;
		--check) check=1; shift ;;
		--quiet) quiet=1; shift ;;
		--forward)
			[ $# -ge 2 ] || die "--forward needs a port"
			forward="$2"
			shift 2
			;;
		--forward=*) forward="${1#--forward=}"; shift ;;
		--) shift; break ;;
		-*) die "unknown option: $1 (try --help)" ;;
		*) break ;;
	esac
done

host="${DOMOVOI_MAC_HOST:-}"
user="${DOMOVOI_MAC_USER:-domovoibuild}"
key="${DOMOVOI_MAC_KEY:-$HOME/.ssh/domovoi_mac_ed25519}"
port="${DOMOVOI_MAC_PORT:-22}"

if [ -z "$host" ]; then
	die "DOMOVOI_MAC_HOST is not set. There is no default and there will not be one: a guessed address is a scan of someone's network. Set it and try again."
fi
case "$host" in
	*[!a-zA-Z0-9.:_-]*) die "DOMOVOI_MAC_HOST looks wrong: '$host'" ;;
esac
[ -f "$key" ] || die "no key at $key. Make it with: ssh-keygen -t ed25519 -f '$key' -C domovoi-mac -N ''"

for bad in $FORBIDDEN_PORTS; do
	if [ "$port" = "$bad" ]; then
		die "port $bad is Kamron's live house, not an ssh port"
	fi
done

ssh_options="-i $key -p $port -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30"

if [ -n "$forward" ]; then
	case "$forward" in
		''|*[!0-9]*) die "--forward takes a port number, not '$forward'" ;;
	esac
	for bad in $FORBIDDEN_PORTS; do
		if [ "$forward" = "$bad" ]; then
			die "refusing to forward port $bad: that is Kamron's live Domovoi (core, web, database or Ollama). The Mac's build user never reaches the real house."
		fi
	done
	if [ "$forward" -lt "$LANE_LOW" ] || [ "$forward" -gt "$LANE_HIGH" ]; then
		die "refusing to forward port $forward: only $LANE_LOW-$LANE_HIGH, this session's throwaway lane."
	fi
	# Bound to 127.0.0.1 at both ends, so nothing on the Mac's network can
	# reach the throwaway either.
	ssh_options="$ssh_options -R 127.0.0.1:$forward:127.0.0.1:$forward"
fi

if [ "$check" -eq 1 ]; then
	# shellcheck disable=SC2086
	exec ssh $ssh_options "$user@$host" 'printf "%s at %s, macOS %s, %s\n" "$(id -un)" "$(hostname)" "$(sw_vers -productVersion)" "$(uname -m)"'
fi

[ $# -gt 0 ] || die "nothing to run (try --help)"

# One string for the remote shell, each argument single-quoted so a path with
# a space survives. `caffeinate -i` keeps the Mac awake for as long as the
# command runs; `nice` and `taskpolicy -b` put it in the background band so
# Kamron's own work on the Mac comes first (plan 5.1, step 6).
#
# An ssh session with no login shell gets a short PATH, and neither the place
# xcodegen lands nor Homebrew's bin is on it, so they are added here rather
# than in every script. On Kamron's Mac there is no Homebrew and the build
# user is a standard account that cannot install one, so XcodeGen is BUILT
# FROM SOURCE into ~/tools (MIT, a build tool only) — that path comes first.
argv=""
for argument in "$@"; do
	quoted="'$(printf '%s' "$argument" | sed "s/'/'\\\\''/g")'"
	argv="${argv:+$argv }$quoted"
done
remote_command="export PATH=\"\$HOME/tools/XcodeGen/.build/release:/opt/homebrew/bin:/usr/local/bin:\$PATH\"; cd \"\$HOME\" && exec caffeinate -i nice -n 10 taskpolicy -b $argv"

[ "$quiet" -eq 1 ] || say "remote.sh: $user@$host: $*"

# shellcheck disable=SC2086
exec ssh $ssh_options "$user@$host" "$remote_command"
