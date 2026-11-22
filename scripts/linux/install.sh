#!/usr/bin/env bash
# install.sh: install the Domovoi server on Linux with one command.
#
#   curl -fsSL https://raw.githubusercontent.com/coders-farm-official/domovoi/stable/scripts/linux/install.sh | sudo bash
#
# It asks two questions (will this server have internet, and its time
# zone), downloads the latest release into a temporary folder, checks it,
# and then runs scripts/linux/provision.sh from that copy, which does the
# install (docs/LINUX_HOST.md, "Install") and asks the one question of its
# own: whether to include AI chat and questions.
#
# Unattended, give the answers instead (no questions, no confirmation):
#
#   curl -fsSL .../install.sh | sudo bash -s -- --internet never --timezone Europe/Berlin --yes
#
# Any provision.sh option passes through (--no-ai-models, --media NAME=PATH,
# --dry-run, ...); --ref, --track-branch and --repo-url don't: this script
# picks the commit itself, and checks it. --channel main installs the
# newest code instead of the latest release.
#
# How it checks the download before running any of it:
#   1. GitHub's answer for the branch (git ls-remote): the clone's HEAD must
#      be that commit.
#   2. On stable (the default), the commit must be a signed release's: the
#      release's DomovoiSetup.manifest.json names its tag and commit and is
#      signed with Domovoi's release key (RELEASE_KEY below, the same key as
#      windows/installer/release-signing.pub, which the Windows programs
#      pin; a test keeps the two the same). ssh-keygen -Y verify checks the
#      signature, and the manifest's commit must be HEAD. main has no
#      release signature: there the check is 1 alone, and this says so.
#   3. provision.sh is given that commit (--ref SHA), so the copy it clones
#      into /opt/domovoi is the same commit; it checks HEAD again there.
# Nothing else is fetched and run: no second script is piped into a shell.
#
# With --kit DIR (or --kit alone, run from the stick's linux/install.sh)
# it installs from an offline kit instead (docs/OFFLINE_KIT.md), with no
# internet at all, on Ubuntu 26.04 only, and the internet answer defaults
# to "No". The trust chain, each link checked before the next is used:
#   1. Checksums: kit.json, the release's signed manifest and the git bundle
#      against the stick's SHA256SUMS (sha256sum -c). Only a bad copy or a
#      dying stick is caught here, early; it is not trust.
#   2. The release signature: release/DomovoiSetup.manifest.json is signed
#      with RELEASE_KEY (ssh-keygen -Y verify, as online) and names the tag
#      and commit kit.json names.
#   3. That commit: the git bundle is cloned (git checks every object's
#      hash) and checked out at exactly that commit.
#   4. The checker: domovoi/offline_kit from that commit (python3 -m
#      domovoi.offline_kit verify, with RELEASE_KEY) checks the whole stick:
#      every file and part, nothing extra, the signature again. Its code is
#      the signed release's own, never loose files on the stick, so a
#      tampered stick can't bring a checker that says yes.
#   5. Every pin: provision.sh from that commit runs with --offline-kit and
#      checks each file it installs against the commit's own pins, as
#      online (apt against Ubuntu's and Docker's signed indexes).
# The first hop stays trust in the stick: a tampered stick could carry a
# tampered install.sh. START-HERE and the dashboard show its SHA-256.
#
# Everything is inside functions and main runs on the last line, so a
# download cut short runs nothing, and nothing this starts can read the
# rest of the script from the pipe.

set -euo pipefail

REPO_URL=https://github.com/coders-farm-official/domovoi
RELEASES_URL=$REPO_URL/releases/download
# Domovoi's release key: windows/installer/release-signing.pub.
RELEASE_KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILvBNVEbpKFujjOMo0O6hZ68A2HQDzuCPFbKabVts6fS'
RELEASE_SIGNER=domovoi-release
MANIFEST=DomovoiSetup.manifest.json
MANIFEST_NAMESPACE=domovoi-windows-setup-manifest
MANIFEST_KIND=domovoi-windows-setup
DOCS_URL=https://github.com/coders-farm-official/domovoi/blob/main/docs/LINUX_HOST.md
DASHBOARD_PORT=6369
TTY=/dev/tty
SYS_ROOT=""
CURL_PROTO='=https'
HARNESS=0
# python3 for the offline kit's checks (the tests point it elsewhere).
PYTHON=python3

# provision.sh's options, passed through as given (a test keeps these
# lists in step with provision.sh's own).
PASS_VALUE_OPTS=" --checkout-dir --user --tier --lan-ip --ollama-url --host-stt-url --host-stt-model --vm-guard --vm-guard-dns --vm-guard-lan --media --health-timeout --allowed-signers --stop-before "
PASS_FLAG_OPTS=" --wsl --no-wsl --skip-models --no-vision-model --ai-models --no-ai-models --host-stt-shares-gpu --dry-run --keep-downloads "
# Chosen here from the checked release, never passed through.
OWN_OPTS=" --repo-url --ref --track-branch --offline-kit "

INTERNET=""
TIMEZONE=""
CHANNEL=stable
YES=0
DRY=0
SVC_USER=domovoi
CHECKOUT=/opt/domovoi
PASS=()
HAVE_TTY=0
WORK=""
BRANCH=""
TRACK=""
HEAD_SHA=""
TAG=""
# --kit: the offline kit's folder (KIT_GIVEN=1 once the option is seen).
KIT=""
KIT_GIVEN=0
KIT_VERSION=""
# The kit format this installer reads (domovoi/offline_kit/format.py).
KIT_FORMAT_READ=1

say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'domovoi install: %s\n' "$*" >&2; exit 1; }
usage_die() { printf 'domovoi install: %s\n(--help lists the options)\n' "$*" >&2; exit 2; }

usage() {
  cat <<'EOF'
Install the Domovoi server on this computer (x86_64: Ubuntu 26.04,
Debian 13, or Linux Mint 22 / Pop!_OS 24.04).

  curl -fsSL https://raw.githubusercontent.com/coders-farm-official/domovoi/stable/scripts/linux/install.sh | sudo bash

It asks whether this server will have internet and its time zone, checks
the latest release, then installs it (and asks whether to include AI chat).

Options (after "sudo bash -s --" when piped):
  --internet ANSWER   always | sometimes | never: answer without being asked
  --timezone ZONE     e.g. America/New_York (default: this computer's zone)
  --yes, -y           ask nothing, start at once (needs --internet)
  --channel CHANNEL   stable (default): the latest release; main: the
                      newest code, which has no release signature
  --kit [DIR]         install from an offline kit (a USB stick an online
                      Domovoi server made), with no internet: DIR is the
                      stick (default: the stick this install.sh is on).
                      Ubuntu 26.04 only. sudo bash /media/alex/DOMOVOIKIT/linux/install.sh --kit
  -h, --help          this text
Any option of scripts/linux/provision.sh passes through to it, e.g.
--no-ai-models, --media music=/srv/music, --dry-run (sudo bash
provision.sh --help, or docs/LINUX_HOST.md, lists them).
EOF
}

# ─── Options ─────────────────────────────────────────────────────────────

parse_args() {
  local opt val has
  while [ $# -gt 0 ]; do
    opt=$1
    val=""
    has=0
    case $opt in
      -h|--help) usage; exit 0 ;;
      -y|--yes) YES=1; shift; continue ;;
      --kit)
        # A folder may follow; anything that looks like an option doesn't.
        KIT_GIVEN=1
        if [ $# -ge 2 ] && [[ $2 != -* ]]; then KIT=$2; shift; fi
        shift
        continue ;;
      --*=*) val=${opt#*=}; opt=${opt%%=*}; has=1 ;;
    esac
    case $opt in
      --kit)
        [ -n "$val" ] || usage_die "--kit= needs a folder (or give --kit alone)"
        KIT_GIVEN=1 KIT=$val
        shift
        continue ;;
      --internet|--timezone|--channel)
        if [ "$has" = 0 ]; then
          [ $# -ge 2 ] || usage_die "$opt needs a value"
          val=$2
          shift
        fi
        shift
        [ -n "$val" ] || usage_die "$opt needs a value"
        case $opt in
          --internet) INTERNET=$val ;;
          --timezone) TIMEZONE=$val ;;
          --channel) CHANNEL=$val ;;
        esac
        ;;
      *)
        if [[ $OWN_OPTS == *" $opt "* ]]; then
          usage_die "$opt: this installer picks the release itself and checks it; to install something else, use the by-hand route ($DOCS_URL#install)"
        elif [[ $PASS_VALUE_OPTS == *" $opt "* ]]; then
          if [ "$has" = 0 ]; then
            [ $# -ge 2 ] || usage_die "$opt needs a value"
            val=$2
            PASS+=("$1" "$2")
            shift 2
          else
            PASS+=("$1")
            shift
          fi
          case $opt in
            --user) SVC_USER=$val ;;
            --checkout-dir) CHECKOUT=${val%/} ;;
          esac
        elif [[ $PASS_FLAG_OPTS == *" $opt "* ]] && [ "$has" = 0 ]; then
          [ "$opt" != --dry-run ] || DRY=1
          PASS+=("$1")
          shift
        else
          usage_die "unknown option: $1"
        fi
        ;;
    esac
  done
  case $INTERNET in
    ""|always|sometimes|never) ;;
    *) usage_die "--internet must be always, sometimes or never: $INTERNET" ;;
  esac
  case $CHANNEL in
    stable|main) ;;
    *) usage_die "--channel must be stable or main: $CHANNEL" ;;
  esac
  if [ -n "$TIMEZONE" ] && ! zone_ok "$TIMEZONE"; then
    usage_die "--timezone must be a time zone such as America/New_York: $TIMEZONE"
  fi
  if [ "$KIT_GIVEN" = 1 ]; then
    [ "$CHANNEL" = stable ] || usage_die "--kit installs the release on the kit; --channel doesn't go with it"
    find_kit
  fi
}

# The kit's folder: --kit DIR, or, with --kit alone, the stick this script
# is on (it is the stick's linux/install.sh, so the stick is one up).
find_kit() {
  local here
  if [ -z "$KIT" ]; then
    if [ -f "${BASH_SOURCE[0]}" ] && [ ! -L "${BASH_SOURCE[0]}" ]; then
      here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
      KIT=$(dirname "$here")
    fi
    [ -n "$KIT" ] && [ -f "$KIT/kit.json" ] \
      || usage_die "--kit: there is no offline kit beside this install.sh; give the stick's folder: --kit /media/alex/DOMOVOIKIT"
  fi
  [ -d "$KIT" ] || usage_die "--kit: $KIT is not a folder (is the stick plugged in and mounted?)"
  KIT=$(cd "$KIT" && pwd -P)
  [ -f "$KIT/kit.json" ] || usage_die "--kit: $KIT has no kit.json, so it is not an offline kit (or not the stick's top folder)"
}

# The test harness (domovoi/tests/test_linux_install_script.py) points the
# script at a throwaway repository, release folder, key and answers file.
# Only with DOMOVOI_INSTALL_TEST_HARNESS=1; never set these on a real box.
harness_setup() {
  if [ "${DOMOVOI_INSTALL_TEST_HARNESS:-}" = 1 ]; then
    HARNESS=1
    REPO_URL=${DOMOVOI_INSTALL_REPO_URL:?}
    RELEASES_URL=${DOMOVOI_INSTALL_RELEASES_URL:?}
    RELEASE_KEY=${DOMOVOI_INSTALL_RELEASE_KEY:?}
    TTY=${DOMOVOI_INSTALL_TTY:-none}
    SYS_ROOT=${DOMOVOI_INSTALL_ROOT:-}
    PYTHON=${DOMOVOI_INSTALL_PYTHON:-python3}
    CURL_PROTO='=https,file'
  elif [ -n "$(compgen -v DOMOVOI_INSTALL_ || true)" ]; then
    die "the DOMOVOI_INSTALL_* variables are for this script's tests only; unset them and run it again"
  else
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    export PATH
  fi
  export GIT_TERMINAL_PROMPT=0 GIT_NO_REPLACE_OBJECTS=1
}

# ─── This computer ───────────────────────────────────────────────────────

os_field() { sed -n "s/^$1=//p" "$SYS_ROOT/etc/os-release" 2>/dev/null | tail -n 1 | tr -d '"'; }

# The systems provision.sh installs on (its detect_platform; docs/LINUX_HOST.md,
# Which distro): Ubuntu 26.04; Debian 13; Ubuntu-based derivatives on
# Ubuntu 24.04 (noble) or 26.04 (resolute), Linux Mint 22 and Pop!_OS 24.04
# the ones it was made with. Another Ubuntu, or another derivative on those
# bases, goes on after a warning (and a question at a terminal); anything
# else stops here, before anything is downloaded.
SUPPORTED="Ubuntu 26.04, Debian 13, or Linux Mint 22 / Pop!_OS 24.04"
untested() {
  warn "$1"
  if [ "$HAVE_TTY" = 1 ] && ! confirm "Continue anyway?" n; then
    die "stopped; nothing was changed"
  fi
}

check_system() {
  local arch id ver name like base
  if [ "$HARNESS" = 0 ] && [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then
    die "run it as root: curl -fsSL ... | sudo bash"
  fi
  arch=$(uname -m)
  [ "$arch" = x86_64 ] || die "Domovoi's Linux install is for x86_64 computers (this one is $arch)"
  id=$(os_field ID)
  ver=$(os_field VERSION_ID)
  name=$(os_field PRETTY_NAME)
  like=" $(os_field ID_LIKE) "
  base=$(os_field UBUNTU_CODENAME)
  # An offline kit carries Ubuntu 26.04's packages only (owner decision
  # 2026-11-22): any other system installs with internet.
  if [ -n "$KIT" ] && { [ "$id" != ubuntu ] || [ "$ver" != 26.04 ]; }; then
    die "an install from an offline kit needs Ubuntu 26.04; this is ${name:-an unknown system}. Install Ubuntu 26.04 first (from its own installer stick), then run this again; other systems install Domovoi with internet ($DOCS_URL#install)"
  fi
  case $id in
    ubuntu)
      if [ "$ver" != 26.04 ]; then
        untested "this is ${name:-Ubuntu $ver}. Domovoi is made for Ubuntu 26.04; other versions may work, but its Docker pins are for 26.04 and 24.04."
      fi ;;
    debian)
      if [ "$ver" != 13 ]; then
        die "this is ${name:-Debian $ver}: of Debian, Domovoi installs on Debian 13 (trixie) only. Supported: $SUPPORTED ($DOCS_URL#which-distro)"
      fi ;;
    *)
      if [[ $like != *" ubuntu "* ]]; then
        die "this installer sets up $SUPPORTED (this is ${name:-an unknown system}). Fedora, Arch and others are not supported yet; to ask for yours, open an issue: $REPO_URL/issues"
      fi
      case $base in
        noble|resolute) ;;
        *) die "this is ${name:-an Ubuntu-based system} on Ubuntu ${base:-(no UBUNTU_CODENAME in /etc/os-release)}, which Domovoi has no packages pinned for. Supported: $SUPPORTED ($DOCS_URL#which-distro)" ;;
      esac
      case $id:$ver in
        linuxmint:22|linuxmint:22.*|pop:24.04) ;;
        *) untested "this is ${name:-$id $ver}, an Ubuntu derivative Domovoi has not been tried on; it installs it as Ubuntu $base, as it does Linux Mint 22 and Pop!_OS 24.04." ;;
      esac ;;
  esac
}

# git, curl and ssh-keygen (the release check); apt installs any missing.
# From a kit: git, ssh-keygen, python3 and sha256sum, from the kit's own
# Ubuntu packages (apt checks them against Ubuntu's signed index with the
# key this system already has).
need_tools() {
  local missing=() t
  if [ -n "$KIT" ]; then
    for t in git:git ssh-keygen:openssh-client sha256sum:coreutils; do
      command -v "${t%%:*}" >/dev/null 2>&1 || missing+=("${t#*:}")
    done
    command -v "$PYTHON" >/dev/null 2>&1 || missing+=(python3)
    [ ${#missing[@]} -gt 0 ] || return 0
    if [ "$HARNESS" = 1 ] || [ "$(id -u)" != 0 ]; then die "needs ${missing[*]}; install them and run this again"; fi
    kit_apt_install "${missing[@]}"
    return 0
  fi
  for t in git:git curl:curl ssh-keygen:openssh-client; do
    command -v "${t%%:*}" >/dev/null 2>&1 || missing+=("${t#*:}")
  done
  [ ${#missing[@]} -gt 0 ] || return 0
  if [ "$HARNESS" = 1 ] || [ "$(id -u)" != 0 ] || ! command -v apt-get >/dev/null 2>&1; then
    die "needs ${missing[*]}; install them and run this again"
  fi
  say "Installing ${missing[*]}..."
  apt-get update -qq </dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates "${missing[@]}" </dev/null \
    || die "apt-get install ${missing[*]} failed"
}

# ─── Questions ───────────────────────────────────────────────────────────

# The terminal, for the questions: stdin is the script itself when piped.
open_tty() {
  [ "$YES" = 0 ] || return 0
  if [ "$TTY" != none ] && (: <"$TTY") 2>/dev/null; then
    exec 3<"$TTY"
    HAVE_TTY=1
  fi
}

# ask PROMPT: one answer line from the terminal, into ANSWER.
ask() {
  printf '%s' "$1" >&2
  IFS= read -r -u 3 ANSWER || die "no answer (the terminal closed); nothing was changed"
  [ -t 3 ] || printf '\n' >&2
  ANSWER=${ANSWER#"${ANSWER%%[![:space:]]*}"}
  ANSWER=${ANSWER%"${ANSWER##*[![:space:]]}"}
}

# confirm QUESTION DEFAULT(y|n): status 0 for yes.
confirm() {
  local hint='[Y/n]'
  [ "$2" = y ] || hint='[y/N]'
  while :; do
    ask "$1 $hint "
    case ${ANSWER,,} in
      "") [ "$2" = y ]; return ;;
      y|yes) return 0 ;;
      n|no) return 1 ;;
    esac
    printf '%s\n' "Please answer y (yes) or n (no)." >&2
  done
}

ask_internet() {
  if [ -n "$KIT" ]; then
    cat >&2 <<'EOF'

Will this server have internet after setup? (Setup itself needs none: it installs from the kit.)
  1) Yes, always. Turns on the extras that use it: web answers, song lyrics, podcast downloads.
  2) Sometimes. Small lookups when the internet is up; big automatic downloads stay off.
  3) No. Keep everything in the house: Domovoi won't contact the internet at all.
You can change this later in Settings > Privacy > Internet.
EOF
    while :; do
      ask "Answer 1, 2 or 3 [3]: "
      case ${ANSWER,,} in
        1|yes|always) INTERNET=always; return ;;
        2|sometimes) INTERNET=sometimes; return ;;
        ""|3|no|never) INTERNET=never; return ;;
      esac
      printf '%s\n' "Please answer 1, 2 or 3." >&2
    done
  fi
  cat >&2 <<'EOF'

Will this server have internet after setup? (Setup itself needs it now.)
  1) Yes, always. Turns on the extras that use it: web answers, song lyrics, podcast downloads.
  2) Sometimes. Small lookups when the internet is up; big automatic downloads stay off.
  3) No. Keep everything in the house: Domovoi won't contact the internet at all.
You can change this later in Settings > Privacy > Internet.
EOF
  while :; do
    ask "Answer 1, 2 or 3: "
    case ${ANSWER,,} in
      1|yes|always) INTERNET=always; return ;;
      2|sometimes) INTERNET=sometimes; return ;;
      3|no|never) INTERNET=never; return ;;
    esac
    printf '%s\n' "Please answer 1, 2 or 3." >&2
  done
}

# zone_ok ZONE: shaped like an IANA zone, and known here when the zone
# files are installed.
zone_ok() {
  [[ $1 =~ ^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,3}$ ]] || return 1
  [ ! -d "$SYS_ROOT/usr/share/zoneinfo" ] || [ -f "$SYS_ROOT/usr/share/zoneinfo/$1" ]
}

detect_timezone() {
  local z=""
  if [ "$HARNESS" = 0 ] && command -v timedatectl >/dev/null 2>&1; then
    z=$(timedatectl show -p Timezone --value 2>/dev/null </dev/null) || z=""
  fi
  if [ -z "$z" ] && [ -f "$SYS_ROOT/etc/timezone" ]; then z=$(head -n 1 "$SYS_ROOT/etc/timezone"); fi
  if [ -z "$z" ] && [ -L "$SYS_ROOT/etc/localtime" ]; then
    z=$(readlink "$SYS_ROOT/etc/localtime")
    z=${z#*zoneinfo/}
  fi
  if [ -n "$z" ] && zone_ok "$z"; then printf '%s' "$z"; else printf 'Etc/UTC'; fi
}

ask_timezone() {
  local def
  def=$(detect_timezone)
  printf '\n' >&2
  while :; do
    ask "Time zone [$def]: "
    [ -n "$ANSWER" ] || ANSWER=$def
    if zone_ok "$ANSWER"; then TIMEZONE=$ANSWER; return; fi
    printf '%s\n' "That isn't a time zone this computer knows. Examples: America/Chicago, Europe/Berlin, Etc/UTC." >&2
  done
}

get_answers() {
  # From a kit the answer defaults to No (a house installing from a stick is
  # one with no internet), so --yes needs nothing more.
  if [ -n "$KIT" ] && [ -z "$INTERNET" ] && { [ "$YES" = 1 ] || [ "$HAVE_TTY" = 0 ]; }; then INTERNET=never; fi
  if [ "$YES" = 1 ] && [ -z "$INTERNET" ]; then
    usage_die "--yes asks nothing, so it needs --internet always, sometimes or never"
  fi
  if [ "$HAVE_TTY" = 0 ] && [ -z "$INTERNET" ]; then
    printf '%s\n' \
      "domovoi install: there is no terminal to ask the questions at, and no answers were given." \
      "Run it from a terminal, or give the answers, for example:" \
      "  curl -fsSL https://raw.githubusercontent.com/coders-farm-official/domovoi/stable/scripts/linux/install.sh | sudo bash -s -- --internet always --timezone America/New_York --yes" >&2
    exit 2
  fi
  [ -n "$INTERNET" ] || ask_internet
  if [ -z "$TIMEZONE" ]; then
    if [ "$HAVE_TTY" = 1 ]; then ask_timezone; else TIMEZONE=$(detect_timezone); fi
  fi
}

# ─── Download and check ──────────────────────────────────────────────────

cleanup() { if [ -n "$WORK" ]; then rm -rf -- "$WORK"; fi; }

make_work() {
  [ -z "$WORK" ] || return 0
  WORK=$(mktemp -d "${TMPDIR:-/tmp}/domovoi-install.XXXXXX")
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
}

# ─── An offline kit ──────────────────────────────────────────────────────

# kit_apt_install PACKAGE...: from the kit's Ubuntu packages only, with an
# apt configuration of this run's own (no other source, no lists of the
# system's); apt checks the kit's InRelease with Ubuntu's keyring.
kit_apt_install() {
  local d keyring suite comps c s
  make_work
  d=$WORK/apt
  mkdir -p "$d/lists/partial" "$d/sources.list.d"
  : >"$d/sources.list"
  keyring=$(sed -n 's/^Signed-By:[[:space:]]*//p' /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null | head -n 1)
  [ -n "$keyring" ] || keyring=/usr/share/keyrings/ubuntu-archive-keyring.gpg
  : >"$d/sources.list.d/offline-kit.sources"
  for s in "$KIT"/apt/amd64/ubuntu/dists/*/; do
    [ -f "$s/InRelease" ] || continue
    suite=$(basename "$s") comps=""
    for c in "$s"*/; do [ -d "$c/binary-amd64" ] && comps+=" $(basename "$c")"; done
    [ -n "$comps" ] || continue
    printf 'Types: deb\nURIs: file:%s/apt/amd64/ubuntu\nSuites: %s\nComponents:%s\nArchitectures: amd64\nSigned-By: %s\nCheck-Valid-Until: no\nCheck-Date: no\n\n' \
      "$KIT" "$suite" "$comps" "$keyring" >>"$d/sources.list.d/offline-kit.sources"
  done
  local -a o=(-o "Dir::Etc::SourceList=$d/sources.list" -o "Dir::Etc::SourceParts=$d/sources.list.d"
    -o "Dir::State::Lists=$d/lists" -o Acquire::Languages=none -o APT::Sandbox::User=root)
  say "Installing $* from the offline kit..."
  apt-get "${o[@]}" -qq update </dev/null \
    && DEBIAN_FRONTEND=noninteractive apt-get "${o[@]}" install -y -qq --no-install-recommends "$@" </dev/null \
    || die "installing $* from the offline kit's Ubuntu packages failed (the lines above say why); the stick may be damaged: make it again"
}

# kit_sums PATH...: those files (and their parts) against the stick's
# SHA256SUMS. Status 0 when every one is listed and matches.
kit_sums() {
  local want=$WORK/sums p line n=0
  : >"$want"
  for p in "$@"; do
    line=$(grep -E "^[0-9a-f]{64} [ *]$(printf '%s' "$p" | sed 's/[.[\*^$]/\\&/g')(\.part[0-9]{2,})?\$" "$KIT/SHA256SUMS" 2>/dev/null) || line=""
    if [ -z "$line" ]; then
      KIT_BAD="$p (SHA256SUMS doesn't list it)"
      return 1
    fi
    printf '%s\n' "$line" >>"$want"
    n=$((n + 1))
  done
  if ! (cd "$KIT" && sha256sum --quiet -c "$want") >"$WORK/sums.out" 2>&1; then
    KIT_BAD=$(sed -n 's/: FAILED.*$//p; s/: No such file.*$//p' "$WORK/sums.out" | head -n 3 | tr '\n' ' ')
    KIT_BAD=${KIT_BAD:-$(head -n 1 "$WORK/sums.out")}
    return 1
  fi
}

# kit_json_info: kit.json's fields this needs, KEY=VALUE lines (the
# system's python3, isolated).
kit_json_info() {
  "$PYTHON" -I -c '
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for k in ("kit_format", "domovoi_version", "tag", "commit"):
    print("%s=%s" % (k, d.get(k)))
print("targets=" + " ".join(map(str, d.get("targets") or [])))
for c in d.get("components") or []:
    for f in c.get("files") or []:
        if f.get("path") == "source/domovoi.bundle":
            print("bundle_parts=%d" % len(f.get("parts") or []))
' "$KIT/kit.json"
}

kit_verify() {
  local info fmt commit tag targets bundle parts dir mine src
  KIT_BAD=""
  say "Checking the offline kit at $KIT..."
  # 1. The files the trust chain reads, against the stick's checksums.
  kit_sums kit.json release/DomovoiSetup.manifest.json release/DomovoiSetup.manifest.json.sig "source/domovoi.bundle" \
    || die "This stick has a damaged file: $KIT_BAD. Make the stick again."
  info=$(kit_json_info 2>&1) || die "kit.json can't be read ($(printf '%s' "$info" | tail -n 1)). Make the stick again."
  fmt=$(printf '%s\n' "$info" | sed -n 's/^kit_format=//p')
  KIT_VERSION=$(printf '%s\n' "$info" | sed -n 's/^domovoi_version=//p')
  tag=$(printf '%s\n' "$info" | sed -n 's/^tag=//p')
  commit=$(printf '%s\n' "$info" | sed -n 's/^commit=//p')
  targets=$(printf '%s\n' "$info" | sed -n 's/^targets=//p')
  parts=$(printf '%s\n' "$info" | sed -n 's/^bundle_parts=//p')
  [[ $fmt =~ ^[0-9]+$ ]] || die "kit.json has no kit format. Make the stick again."
  if [ "$fmt" -gt "$KIT_FORMAT_READ" ]; then
    die "this kit was made by a newer Domovoi (kit format $fmt) than this install.sh reads ($KIT_FORMAT_READ). Use the install.sh on the stick: sudo bash $KIT/linux/install.sh --kit"
  fi
  [[ $commit =~ ^[0-9a-f]{40}$ ]] && [[ $tag =~ ^v[0-9] ]] || die "kit.json names no release. Make the stick again."
  [[ " $targets " == *" pc-x86_64 "* ]] \
    || die "this kit was made for a Mac only. A Linux PC needs a kit with \"A Windows or Linux PC\" ticked: make the kit again with it ticked."
  # 2. The release signature, as online: the manifest names this tag and commit.
  mkdir -p "$WORK/release"
  cp "$KIT/release/$MANIFEST" "$WORK/release/m.json" && cp "$KIT/release/$MANIFEST.sig" "$WORK/release/m.json.sig" \
    || die "the kit's release/$MANIFEST can't be read. Make the stick again."
  HEAD_SHA=$commit
  verify_local_manifest "$tag" "$WORK/release" \
    || die "the offline kit is not a Domovoi release signed with Domovoi's release key (its release/$MANIFEST doesn't check out), so nothing on it was run. Use a kit from a house you trust, made again."
  TAG=$tag
  say "Checked: the kit is Domovoi $KIT_VERSION ($TAG, commit ${commit:0:12}), signed with Domovoi's release key."
  # 3. The source at that commit: git checks every object as it clones.
  bundle="$KIT/source/domovoi.bundle"
  if [ -n "$parts" ] && [ "$parts" -gt 0 ]; then
    bundle=$WORK/domovoi.bundle
    cat "$KIT/source/domovoi.bundle".part*>"$bundle" || die "can't join the kit's source/domovoi.bundle. Make the stick again."
  fi
  dir=$WORK/domovoi
  git -c transfer.fsckObjects=true -c init.defaultBranch=main clone --quiet --no-checkout "$bundle" "$dir" </dev/null \
    || die "the kit's source/domovoi.bundle can't be read by git. Make the stick again."
  git -C "$dir" cat-file -e "$commit^{commit}" 2>/dev/null \
    || die "the kit's source/domovoi.bundle doesn't hold commit $commit, the one the signed release names. Make the stick again."
  git -C "$dir" -c advice.detachedHead=false checkout -q "$commit" </dev/null || die "checking out $commit from the kit's source failed"
  [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ] || die "the kit's source is not at commit $commit after checking it out"
  # This install.sh must be that release's own: another one is another
  # version of the installer (older or newer than the kit).
  mine=${BASH_SOURCE[0]}
  src=$dir/scripts/linux/install.sh
  if [ -f "$mine" ] && [ ! -L "$mine" ] && ! cmp -s "$mine" "$src"; then
    die "this install.sh is not the one in the kit's release (Domovoi $KIT_VERSION): it is an older or newer installer. Run the install.sh on the stick: sudo bash $KIT/linux/install.sh --kit"
  fi
  PROVISION=$dir/scripts/linux/provision.sh
  [ -f "$PROVISION" ] && [ ! -L "$PROVISION" ] || die "the kit's source has no scripts/linux/provision.sh"
  grep -q -- '--offline-kit' "$PROVISION" \
    || die "the kit holds Domovoi $KIT_VERSION, which can't install without internet (offline kits start with 1.5.0). Make a kit on a newer Domovoi."
  # 4. The whole stick, with the release's own checker.
  [ -f "$dir/domovoi/offline_kit/__main__.py" ] || die "the kit's source has no domovoi/offline_kit (its checker)"
  printf '%s\n' "$RELEASE_KEY" >"$WORK/release-key.pub"
  say "Checking every file on the stick (this reads the whole stick once; a few minutes)..."
  if ! (cd / && "$PYTHON" -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from domovoi.offline_kit.__main__ import main
sys.exit(main(["verify", sys.argv[2], "--release-key", sys.argv[3]]))
' "$dir" "$KIT" "$WORK/release-key.pub"); then
    die "the offline kit has the problems above, so nothing was installed. Make the stick again."
  fi
  BRANCH=stable TRACK=stable
}
# manifest_field FILE KEY: the one "KEY": "value" line's value, or nothing.
manifest_field() {
  [ "$(grep -c "\"$2\"" "$1")" = 1 ] || return 0
  sed -n "s/^[[:space:]]*\"$2\":[[:space:]]*\"\\([^\"]*\\)\",\\{0,1\\}[[:space:]]*\$/\\1/p" "$1"
}

# verify_release TAG: the release TAG's manifest is signed by the release
# key and names TAG and HEAD.
verify_release() {
  local tag=$1 d=$WORK/release
  rm -rf -- "$d"
  mkdir -p "$d"
  if ! curl -fsSL --proto "$CURL_PROTO" -o "$d/m.json" "$RELEASES_URL/$tag/$MANIFEST" </dev/null \
      || ! curl -fsSL --proto "$CURL_PROTO" -o "$d/m.json.sig" "$RELEASES_URL/$tag/$MANIFEST.sig" </dev/null; then
    warn "$tag: can't download its signed manifest ($MANIFEST)"
    return 1
  fi
  verify_local_manifest "$tag" "$d"
}

# verify_local_manifest TAG DIR: DIR/m.json is signed (DIR/m.json.sig) with
# the release key and names TAG and HEAD_SHA.
verify_local_manifest() {
  local tag=$1 d=$2
  printf '%s namespaces="%s" %s\n' "$RELEASE_SIGNER" "$MANIFEST_NAMESPACE" "$RELEASE_KEY" >"$d/allowed_signers"
  if ! ssh-keygen -Y verify -f "$d/allowed_signers" -I "$RELEASE_SIGNER" -n "$MANIFEST_NAMESPACE" \
      -s "$d/m.json.sig" <"$d/m.json" >/dev/null 2>&1; then
    warn "$tag: its manifest is not signed with Domovoi's release key"
    return 1
  fi
  if [ "$(manifest_field "$d/m.json" kind)" != "$MANIFEST_KIND" ] \
      || [ "$(manifest_field "$d/m.json" tag)" != "$tag" ] \
      || [ "$(manifest_field "$d/m.json" commit)" != "$HEAD_SHA" ]; then
    warn "$tag: its signed manifest names another release or commit, not $tag at $HEAD_SHA"
    return 1
  fi
}

fetch_and_verify() {
  local heads stable main want tags tag dir
  make_work
  if [ -n "$KIT" ]; then kit_verify; return; fi
  say "Looking up the latest Domovoi release..."
  heads=$(git ls-remote "$REPO_URL" refs/heads/stable refs/heads/main </dev/null) \
    || die "can't reach $REPO_URL (is the internet up?)"
  stable=$(printf '%s\n' "$heads" | awk '$2 == "refs/heads/stable" {print $1}')
  main=$(printf '%s\n' "$heads" | awk '$2 == "refs/heads/main" {print $1}')
  # The release channel, as provision.sh picks it: stable, or main while
  # there is no release yet. provision.sh is told stable either way, so the
  # box moves to releases once the first one is out (Settings > About &
  # updates), and it warns about the fallback itself.
  if [ "$CHANNEL" = main ]; then
    BRANCH=main TRACK=main want=$main
  elif [ -n "$stable" ]; then
    BRANCH=stable TRACK=stable want=$stable
  else
    BRANCH=main TRACK=stable want=$main
    warn "no release has been published yet: installing main, the newest code"
  fi
  [[ $want =~ ^[0-9a-f]{40}$ ]] || die "$REPO_URL has no $BRANCH branch"
  dir=$WORK/domovoi
  say "Downloading Domovoi ($BRANCH, commit ${want:0:12})..."
  git clone --quiet --depth 1 --single-branch --no-tags --branch "$BRANCH" "$REPO_URL" "$dir" </dev/null \
    || die "downloading Domovoi from $REPO_URL failed"
  HEAD_SHA=$(git -C "$dir" rev-parse HEAD)
  if [ "$HEAD_SHA" != "$want" ]; then
    die "GitHub's $BRANCH is commit $want, but the download is $HEAD_SHA (it moved while downloading, or something in between changed it). Nothing was run; run the command again."
  fi
  if [ "$BRANCH" = stable ]; then
    tags=$(git ls-remote --tags "$REPO_URL" </dev/null \
      | awk -v h="$HEAD_SHA" '$1 == h {sub(/^refs\/tags\//, "", $2); sub(/\^\{\}$/, "", $2); print $2}' \
      | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]{1,40})?$' | sort -u -r -V) || tags=""
    [ -n "$tags" ] || die "stable is at commit $HEAD_SHA, which no release names. Nothing was run; try again later, or see $DOCS_URL#install"
    for tag in $tags; do
      if verify_release "$tag"; then TAG=$tag; break; fi
    done
    [ -n "$TAG" ] || die "no signed release names commit $HEAD_SHA, so it was not run. Nothing was changed; see $DOCS_URL#install"
    say "Checked: $TAG (commit ${HEAD_SHA:0:12}) is signed with Domovoi's release key."
  else
    warn "main is not a release, so there is no release signature to check: the download is checked against GitHub's main only."
  fi
  PROVISION=$dir/scripts/linux/provision.sh
  [ -f "$PROVISION" ] && [ ! -L "$PROVISION" ] || die "the download has no scripts/linux/provision.sh"
}

# ─── Install ─────────────────────────────────────────────────────────────

summary() {
  local version
  if [ -n "$KIT" ]; then version="$TAG (commit ${HEAD_SHA:0:12}), signed release, from the offline kit at $KIT"
  elif [ -n "$TAG" ]; then version="$TAG (commit ${HEAD_SHA:0:12}), signed release"
  else version="$BRANCH (commit ${HEAD_SHA:0:12}), not a release"; fi
  say ""
  say "Ready to install Domovoi on this computer:"
  say "  Version:    $version"
  say "  Internet:   $INTERNET"
  say "  Time zone:  $TIMEZONE"
  say "  Goes into:  $CHECKOUT, run by the user $SVC_USER (made for it if new)"
  [ ${#PASS[@]} -eq 0 ] || say "  Options:    ${PASS[*]}"
  [ "$DRY" = 0 ] || say "  Dry run:    it only says what it would do; nothing is changed"
  if [ -n "$KIT" ]; then
    say "It installs from the kit and downloads nothing; it can take half an hour or"
    say "more. Leave the stick in. If it stops, run the same command again: it picks"
    say "up where it left off."
  else
    say "It downloads several GB and can take half an hour or more. If it stops,"
    say "run the same command again: it picks up where it left off."
  fi
}

lan_ip() {
  local ip="" unknown="<this computer's address>"
  ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1) || ip=""
  [ -n "$ip" ] || ip=$(hostname -I 2>/dev/null | awk '{print $1}') || ip=""
  printf '%s' "${ip:-$unknown}"
}

run_provision() {
  local rc home
  local -a args=(--ref "$HEAD_SHA" --track-branch "$TRACK" --internet "$INTERNET" --timezone "$TIMEZONE")
  [ -z "$KIT" ] || args+=(--offline-kit "$KIT")
  [ ${#PASS[@]} -eq 0 ] || args+=("${PASS[@]}")
  say ""
  # provision.sh's "@@DOMOVOI " lines are for the Windows installer's window.
  set +e
  if [ "$HAVE_TTY" = 1 ]; then
    (cd / && bash "$PROVISION" "${args[@]}" <&3) | grep --line-buffered -v '^@@DOMOVOI '
  else
    (cd / && bash "$PROVISION" "${args[@]}" </dev/null) | grep --line-buffered -v '^@@DOMOVOI '
  fi
  rc=${PIPESTATUS[0]}
  set -e
  say ""
  if [ "$rc" != 0 ]; then
    say "The install stopped (exit $rc). The lines above say why. Fix that and run"
    say "the same command again: finished steps are skipped."
    exit "$rc"
  fi
  if [ "$DRY" = 1 ]; then
    say "Dry run finished: nothing was changed."
    return 0
  fi
  home=$(getent passwd "$SVC_USER" 2>/dev/null | cut -d: -f6) || home=""
  say "Domovoi is installed and running."
  say "Next, on a phone or computer on this network, open"
  say ""
  say "    http://$(lan_ip):$DASHBOARD_PORT"
  say ""
  say "and claim it with the setup code. To see the code on this computer:"
  say "    sudo cat ${home:-/home/$SVC_USER}/.domovoi/setup-code.txt"
}

main() {
  harness_setup
  parse_args "$@"
  open_tty
  check_system
  get_answers
  need_tools
  fetch_and_verify
  summary
  if [ "$HAVE_TTY" = 1 ] && ! confirm "Start now?" y; then
    die "stopped; nothing was changed"
  fi
  run_provision
}

main "$@"
