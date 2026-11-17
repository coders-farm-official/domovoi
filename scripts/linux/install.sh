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

# provision.sh's options, passed through as given (a test keeps these
# lists in step with provision.sh's own).
PASS_VALUE_OPTS=" --checkout-dir --user --tier --lan-ip --ollama-url --host-stt-url --host-stt-model --vm-guard --vm-guard-dns --vm-guard-lan --media --health-timeout --allowed-signers --stop-before "
PASS_FLAG_OPTS=" --wsl --no-wsl --skip-models --no-vision-model --ai-models --no-ai-models --dry-run --keep-downloads "
# Chosen here from the checked release, never passed through.
OWN_OPTS=" --repo-url --ref --track-branch "

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
      --*=*) val=${opt#*=}; opt=${opt%%=*}; has=1 ;;
    esac
    case $opt in
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
need_tools() {
  local missing=() t
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
  WORK=$(mktemp -d "${TMPDIR:-/tmp}/domovoi-install.XXXXXX")
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
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
  if [ -n "$TAG" ]; then version="$TAG (commit ${HEAD_SHA:0:12}), signed release"
  else version="$BRANCH (commit ${HEAD_SHA:0:12}), not a release"; fi
  say ""
  say "Ready to install Domovoi on this computer:"
  say "  Version:    $version"
  say "  Internet:   $INTERNET"
  say "  Time zone:  $TIMEZONE"
  say "  Goes into:  $CHECKOUT, run by the user $SVC_USER (made for it if new)"
  [ ${#PASS[@]} -eq 0 ] || say "  Options:    ${PASS[*]}"
  [ "$DRY" = 0 ] || say "  Dry run:    it only says what it would do; nothing is changed"
  say "It downloads several GB and can take half an hour or more. If it stops,"
  say "run the same command again: it picks up where it left off."
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
