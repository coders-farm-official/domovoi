#!/usr/bin/env bash
# update-verifier.sh: the only way root takes code from the Domovoi checkout.
#
# Installed by install-update-unit.sh, root-owned, as
# /usr/local/lib/domovoi-update/verify-update.sh, and run by
# domovoi-update.service (docs/LINUX_HOST.md, "Updates from the dashboard"):
#
#   ExecStartPre=/bin/bash /usr/local/lib/domovoi-update/verify-update.sh prepare
#   ExecStart=/bin/bash /usr/local/lib/domovoi-update/verify-update.sh run
#
# The checkout belongs to the service user, and so does everything that
# runs as that user: the core, the dashboard, every plugin. Before this
# existed, root ran the checkout's own scripts/linux/apply-update.sh, its
# helpers (os-jobs.sh, gpu-runtime.sh) and installed its files (the
# journald drop-in, through a symlink if one was planted), straight from the
# working tree, after a signature check of HEAD that an uncommitted (or a
# skip-worktree'd) edit never touched (delta security review 2026-10-16,
# S4 / REV-10). Now:
#
#   prepare  fetches HEAD from the checkout into a bare mirror only root can
#            write ($DOMOVOI_UPDATE_DIR/mirror.git), with every object
#            checked as it arrives (transfer.fsckObjects). The fetch's other
#            end, git upload-pack, runs as the service user: root reads
#            nothing of the checkout itself, not its files, not its object
#            store, not its config. While signed updates are enforced (the
#            root-owned allowed-signers file exists), the commit must verify
#            there, or the run is refused before anything of it runs
#            (last-result.json says so, as apply-update.sh's own refusals
#            do). Then the files root runs or installs (scripts/linux: this
#            script, apply-update.sh, os-jobs.sh, gpu-runtime.sh,
#            install-update-unit.sh, the units and drop-ins, the pin
#            manifest; and docs/LINUX_HOST.md, whose Ollama pin provision.sh
#            reads) are written out of the mirror with git archive into
#            /run/domovoi-update/<sha>: plain files only (a symbolic link or
#            a submodule there refuses the run), root-owned, 0755
#            directories. Without the signers file there is no signature to
#            check, and the copy is simply the commit HEAD names: an edit
#            in the working tree, committed or not, hidden or not, changes
#            nothing root runs until it is a commit.
#   run      runs that copy's apply-update.sh, telling it which commit it is
#            (DOMOVOI_VERIFIED_SHA; it refuses to go on when HEAD has moved
#            since), where the copy is (DOMOVOI_VERIFIED_DIR) and where the
#            mirror is (DOMOVOI_VERIFIED_MIRROR: its signature and branch
#            checks run there, by root's own git, rather than in the
#            service user's repository).
#   relaunch both, in one go: what apply-update.sh does when it is started
#            the old way (a unit from before this script, or by hand), so
#            even that run, and every one after it, runs from the copy.
#   export   prepare without the update: prints the copy's directory (under
#            /run/domovoi-update, or the directory given) for an
#            administrator who wants to run provision.sh or
#            install-update-unit.sh from verified files.
#
# Settings, from the unit's environment (Environment=DOMOVOI_REPO_DIR and
# /etc/default/domovoi-update): DOMOVOI_REPO_DIR (the checkout, required),
# DOMOVOI_USER (else domovoi-core.service's User=, else the checkout's
# owner), DOMOVOI_UPDATE_DIR, DOMOVOI_ALLOWED_SIGNERS,
# DOMOVOI_REVOKED_SIGNERS. Every file this reads or writes as root lives in
# a directory only root can write; it stops when one isn't.
#
# /run is mounted noexec on Ubuntu: everything in the copy is run through
# bash (bash FILE), never executed directly.
#
# DOMOVOI_PROVISION_ROOT with DOMOVOI_PROVISION_TEST_HARNESS=1 is for the
# test harnesses only (scripts/linux/tests): the default paths go under that
# root, DOMOVOI_UPDATE_RUN_DIR may move the copies, and files owned by the
# harness's own user count as root's. Never set them on a host.

set -euo pipefail
umask 022

# Every git this script runs, the service user's upload-pack included,
# reads objects as they are: no refs/replace and no grafts (rc2 review C3),
# as apply-update.sh, provision.sh and install-update-unit.sh do. vx_git
# pins the same again for root's own git.
export GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null

PROG=update-verifier

ROOT=""
HARNESS=0
if [ -n "${DOMOVOI_PROVISION_ROOT:-}" ] || [ -n "${DOMOVOI_PROVISION_TEST_HARNESS:-}" ]; then
  if [ "${DOMOVOI_PROVISION_TEST_HARNESS:-}" != 1 ] || [[ ${DOMOVOI_PROVISION_ROOT:-} != /?* ]] \
      || [ ! -d "$DOMOVOI_PROVISION_ROOT" ] || [ "$(cd "$DOMOVOI_PROVISION_ROOT" && pwd -P)" = / ]; then
    printf '%s: DOMOVOI_PROVISION_ROOT and DOMOVOI_PROVISION_TEST_HARNESS are for the test harnesses only, and only together\n' "$PROG" >&2
    exit 2
  fi
  ROOT=${DOMOVOI_PROVISION_ROOT%/}
  HARNESS=1
fi

INSTALLED_PATH=$ROOT/usr/local/lib/domovoi-update/verify-update.sh
RUN_DIR=$ROOT/run/domovoi-update
if [ "$HARNESS" = 1 ] && [ -n "${DOMOVOI_UPDATE_RUN_DIR:-}" ]; then RUN_DIR=${DOMOVOI_UPDATE_RUN_DIR%/}; fi
UPDATE_DIR=${DOMOVOI_UPDATE_DIR:-$ROOT/var/lib/domovoi-update}
UPDATE_DIR=${UPDATE_DIR%/}
REPO=${DOMOVOI_REPO_DIR:-}
REPO=${REPO%/}
SVC_USER=${DOMOVOI_USER:-}
SIGNERS=${DOMOVOI_ALLOWED_SIGNERS:-$ROOT/etc/domovoi/allowed_signers}
REVOKED=${DOMOVOI_REVOKED_SIGNERS:-}
RESULT_FILE=$UPDATE_DIR/last-result.json
POINTER=$RUN_DIR/verified
STARTED_AT=""
START_S=0

log() { printf '%s: %s\n' "$PROG" "$*" >&2; }
die() { log "$*"; exit 1; }

# >>> verified-copy block >>>
# The same text in scripts/linux/provision.sh, character for character
# (domovoi/tests/test_update_verifier.py holds the two together): provision.sh
# runs alone inside the Windows installer's distro, with no copy of this file
# beside it, and needs the same copy before it reads anything of a checkout.
#
# The caller sets VX_REPO (the checkout), VX_USER (the service user, who owns
# it), VX_MIRROR (a bare repository only root can write), VX_SIGNERS (the
# allowed-signers file; signed updates are enforced while it exists),
# VX_REVOKED (a revocation file, or empty), VX_ROOT_UIDS (the owners that
# count as root: 0, and under a test harness its own user) and VX_TOP (where
# the walk up a path for anyone else's write access stops: /, or the
# harness's case directory). A vx_ function that fails returns 1 with the
# reason in VX_ERROR.

VX_PATHS=(scripts/linux docs/LINUX_HOST.md)
VX_SKIP=scripts/linux/tests/
VX_ERROR=""
VX_SHA=""
VX_DIR=""
VX_SIG_STATUS=""
VX_SIG_SIGNER=""
VX_SIG_KEY=""
VX_SIG_DETAIL=""
VX_SIG_ENFORCED=0

# git as root, in the mirror only, with nothing from the environment or the
# system's config deciding what it runs (the mirror's own config is root's).
# No replacement objects and no grafts (rc2 review C3): a refs/replace or an
# info/grafts entry could make git read another commit or tree than the one
# a SHA names, and verify or copy that. The fetch never brings refs/replace
# in, and the mirror has no alternates: its objects are its own.
vx_git() {
  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY \
    -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_NAMESPACE -u GIT_CONFIG -u GIT_CONFIG_PARAMETERS \
    -u GIT_CONFIG_COUNT -u GIT_EXEC_PATH -u GIT_TEMPLATE_DIR -u GIT_REPLACE_REF_BASE \
    -u GIT_SSH -u GIT_SSH_COMMAND -u GIT_ASKPASS -u GIT_PROXY_COMMAND -u GIT_EXTERNAL_DIFF \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="${GIT_CONFIG_GLOBAL:-/dev/null}" \
    GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null GIT_TERMINAL_PROMPT=0 \
    git -C "$VX_MIRROR" -c core.hooksPath=/dev/null -c core.fsmonitor=false -c advice.graftFileDeprecated=false "$@"
}

vx_root_uid() {
  local u
  for u in $VX_ROOT_UIDS; do [ "$1" = "$u" ] && return 0; done
  return 1
}

# vx_root_only PATH: PATH and every directory above it (to VX_TOP) belong to
# root, and no one else can write them. Root writes there by rename, and a
# directory anyone else could write would let them swap a file or a link in
# between root's steps. Above PATH itself a sticky directory (/tmp) is
# fine: no one else can rename or remove root's entry in it.
vx_root_only() {
  local p st mode first=1
  p=$(readlink -f -- "$1" 2>/dev/null) || p=""
  if [ -z "$p" ] || [ ! -e "$p" ]; then VX_ERROR="$1 is not there"; return 1; fi
  while :; do
    st=$(stat -c '%u %a' -- "$p" 2>/dev/null) || st=""
    if ! [[ $st =~ ^([0-9]+)\ ([0-7]{3,4})$ ]] || ! vx_root_uid "${BASH_REMATCH[1]}"; then
      VX_ERROR="$p can be changed by a user other than root (owner and mode: ${st:-unknown})"
      return 1
    fi
    mode=$((8#${BASH_REMATCH[2]}))
    if (( mode & 8#022 )) && { [ "$first" = 1 ] || ! (( mode & 8#1000 )); }; then
      VX_ERROR="$p can be changed by a user other than root (owner and mode: $st)"
      return 1
    fi
    first=0
    if [ "$p" = / ] || [ "$p" = "$VX_TOP" ]; then break; fi
    p=$(dirname -- "$p")
  done
}

# The mirror: a bare repository, created on first use. Its config has
# transfer.fsckObjects, so every object a fetch brings in is checked (its
# hash, and that it is well-formed) before it is kept.
vx_mirror() {
  if [ -L "$VX_MIRROR" ] || { [ -e "$VX_MIRROR" ] && [ ! -d "$VX_MIRROR" ]; }; then
    VX_ERROR="$VX_MIRROR is not a directory"; return 1
  fi
  if [ ! -d "$VX_MIRROR" ]; then
    mkdir -m 0700 -- "$VX_MIRROR" || { VX_ERROR="can't create $VX_MIRROR"; return 1; }
  fi
  vx_root_only "$VX_MIRROR" || return 1
  if [ ! -f "$VX_MIRROR/HEAD" ]; then
    vx_git -c init.defaultBranch=main init -q --bare >/dev/null || { VX_ERROR="git init --bare $VX_MIRROR failed"; return 1; }
  fi
  vx_git config transfer.fsckObjects true && vx_git config fetch.fsckObjects true \
    && vx_git config gc.autoDetach false && vx_git config core.logAllRefUpdates false \
    || { VX_ERROR="can't configure $VX_MIRROR"; return 1; }
}

# HEAD of the checkout into the mirror (refs/domovoi/head), and the
# branches the checkout last fetched (refs/remotes/origin/*), which the
# branch pin is checked against. git upload-pack, the side that reads the
# checkout, runs as the service user; root's side only takes in its pack.
# Sets VX_SHA.
vx_fetch() {
  local up out
  VX_SHA=""
  if [ "$VX_USER" = root ]; then up="git upload-pack"; else up="runuser -u $VX_USER -- git upload-pack"; fi
  if ! out=$(vx_git -c protocol.file.allow=always fetch --quiet --no-tags --no-recurse-submodules \
      --no-write-fetch-head --no-auto-gc --prune --upload-pack="$up" "$VX_REPO" \
      '+HEAD:refs/domovoi/head' '+refs/remotes/origin/*:refs/remotes/origin/*' 2>&1); then
    VX_ERROR="git fetch of $VX_REPO into $VX_MIRROR failed: $(printf '%s\n' "$out" | tail -n 3 | tr '\n' ' ' | cut -c1-400)"
    return 1
  fi
  vx_git gc --auto --quiet >/dev/null 2>&1 || true
  VX_SHA=$(vx_git rev-parse -q --verify 'refs/domovoi/head^{commit}' 2>/dev/null) || VX_SHA=""
  if ! [[ $VX_SHA =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; then
    VX_ERROR="HEAD of $VX_REPO is not a commit"; VX_SHA=""; return 1
  fi
}

# Why VX_SIGNERS can't be trusted, printed; nothing when it can.
vx_signers_problem() {
  local st
  if [ -L "$VX_SIGNERS" ]; then printf '%s is a symlink' "$VX_SIGNERS"; return 0; fi
  if [ ! -f "$VX_SIGNERS" ]; then printf '%s is not a regular file' "$VX_SIGNERS"; return 0; fi
  st=$(stat -c '%u %a' -- "$VX_SIGNERS" 2>/dev/null) || st=""
  if ! [[ $st =~ ^([0-9]+)\ ([0-7]{3,4})$ ]] || ! vx_root_uid "${BASH_REMATCH[1]}"; then
    printf '%s is not owned by root' "$VX_SIGNERS"; return 0
  fi
  if (( 8#${BASH_REMATCH[2]} & 8#022 )); then
    printf '%s can be written by a user other than root (mode %s)' "$VX_SIGNERS" "${BASH_REMATCH[2]}"
  fi
}

# VX_SHA's signature, in the mirror, into VX_SIG_*. Not enforced (no
# signers file): status 0, VX_SIG_ENFORCED=0, nothing checked. Enforced:
# status 0 only for a signature by a listed key.
vx_verify() {
  local problem err mark info
  local -a cfg=(-c "gpg.ssh.allowedSignersFile=$VX_SIGNERS" -c gpg.ssh.program=ssh-keygen
                -c gpg.program=gpg -c gpg.openpgp.program=gpg -c gpg.x509.program=gpgsm)
  VX_SIG_STATUS="" VX_SIG_SIGNER="" VX_SIG_KEY="" VX_SIG_DETAIL="" VX_SIG_ENFORCED=0
  if [ ! -e "$VX_SIGNERS" ] && [ ! -L "$VX_SIGNERS" ]; then return 0; fi
  VX_SIG_ENFORCED=1
  problem=$(vx_signers_problem)
  if [ -n "$problem" ]; then
    VX_SIG_STATUS=unverified
    VX_SIG_DETAIL="the allowed-signers file cannot be trusted: $problem"
    VX_ERROR=$VX_SIG_DETAIL
    return 1
  fi
  if [ -n "$VX_REVOKED" ]; then cfg+=(-c "gpg.ssh.revocationFile=$VX_REVOKED"); fi
  if err=$(vx_git "${cfg[@]}" verify-commit "$VX_SHA" 2>&1 >/dev/null); then
    info=$(vx_git "${cfg[@]}" log -1 --format='%GS%n%GK' "$VX_SHA" 2>/dev/null) || info=""
    VX_SIG_SIGNER=$(printf '%s\n' "$info" | sed -n 1p)
    VX_SIG_KEY=$(printf '%s\n' "$info" | sed -n 2p)
    VX_SIG_STATUS=verified
    VX_SIG_DETAIL="HEAD is signed by ${VX_SIG_SIGNER:-an allowed key}${VX_SIG_KEY:+ ($VX_SIG_KEY)}, listed in $VX_SIGNERS"
    return 0
  fi
  mark=$(vx_git "${cfg[@]}" log -1 --format='%G?' "$VX_SHA" 2>/dev/null) || mark=""
  if [ "$mark" = N ]; then
    VX_SIG_STATUS=unsigned
    VX_SIG_DETAIL="HEAD is not signed"
  else
    VX_SIG_STATUS=unverified
    VX_SIG_DETAIL="the signature on HEAD is not by a key in $VX_SIGNERS: $(printf '%s\n' "$err" | tail -n 2 | tr '\n' ' ' | sed 's/[[:space:]]*$//' | cut -c1-200)"
  fi
  VX_ERROR=$VX_SIG_DETAIL
  return 1
}

# vx_export PARENT: VX_SHA's own copy of the files root runs or installs,
# written out of the mirror into PARENT/VX_SHA (VX_DIR). Plain files only:
# the commit may hold no symbolic link and no submodule there (root would
# follow a link wherever it pointed). Root-owned, directories 0755, nothing
# writable by anyone else.
vx_export() {
  local parent=$1 p stage list entry mode path odd
  local -a want=()
  VX_DIR=""
  for p in "${VX_PATHS[@]}"; do
    if vx_git cat-file -e "$VX_SHA:$p" 2>/dev/null; then want+=("$p"); fi
  done
  case " ${want[*]-} " in
    *" scripts/linux "*) ;;
    *) VX_ERROR="commit $VX_SHA has no scripts/linux"; return 1 ;;
  esac
  vx_root_only "$parent" || return 1
  stage=$(mktemp -d "$parent/.stage-XXXXXX") || { VX_ERROR="can't write in $parent"; return 1; }
  list=$stage.list
  if ! vx_git ls-tree -r -z --full-tree "$VX_SHA" -- "${want[@]}" >"$list"; then
    rm -rf -- "$stage" "$list"; VX_ERROR="git ls-tree $VX_SHA failed"; return 1
  fi
  odd=""
  while IFS= read -r -d '' entry; do
    mode=${entry%% *}
    path=${entry#*$'\t'}
    case $path in "$VX_SKIP"*) continue ;; esac
    case $mode in
      100644|100755) ;;
      120000) odd="$path is a symbolic link"; break ;;
      160000) odd="$path is a submodule"; break ;;
      *) odd="$path has mode $mode"; break ;;
    esac
  done <"$list"
  rm -f -- "$list"
  if [ -n "$odd" ]; then
    rm -rf -- "$stage"
    VX_ERROR="commit $VX_SHA: $odd; the copy root runs from holds plain files only"
    return 1
  fi
  if ! vx_git -c tar.umask=0022 archive --format=tar "$VX_SHA" -- "${want[@]}" ":(exclude)${VX_SKIP%/}" \
      | tar -x --no-same-owner --no-same-permissions -C "$stage" -f -; then
    rm -rf -- "$stage"; VX_ERROR="git archive of $VX_SHA into $stage failed"; return 1
  fi
  odd=$(find "$stage" -mindepth 1 ! -type f ! -type d -print -quit 2>/dev/null) || odd="?"
  if [ -n "$odd" ]; then
    rm -rf -- "$stage"; VX_ERROR="the copy of $VX_SHA holds something other than plain files ($odd)"; return 1
  fi
  chmod -R u+rwX,go-w,go+rX "$stage" && chmod 0755 "$stage" || { rm -rf -- "$stage"; VX_ERROR="can't set the modes in $stage"; return 1; }
  if [ -e "$parent/$VX_SHA" ] || [ -L "$parent/$VX_SHA" ]; then rm -rf -- "${parent:?}/$VX_SHA"; fi
  mv -T -- "$stage" "$parent/$VX_SHA" || { rm -rf -- "$stage"; VX_ERROR="can't move the copy to $parent/$VX_SHA"; return 1; }
  VX_DIR=$parent/$VX_SHA
  vx_root_only "$VX_DIR" || return 1
}
# <<< verified-copy block <<<

# ─── The result an update the verifier refuses leaves behind ─────────────

json_str() {
  local s
  s=$(printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037')
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}
json_str_or_null() { if [ -n "$1" ]; then json_str "$1"; else printf 'null'; fi; }

# A SHA read from a file of root's update dir, or nothing.
read_sha() {
  local s
  [ -f "$1" ] && [ ! -L "$1" ] || return 0
  s=$(head -c 200 "$1" 2>/dev/null | tr -d '[:space:]') || s=""
  if [[ $s =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; then printf '%s' "$s"; fi
}

# write_refusal STATUS STEP STEP_STATUS ERROR: last-result.json for a run
# that never got to apply-update.sh, in its shape, so the core's Version
# card says why (and keeps the bad_sha it had).
write_refusal() {
  local status=$1 step=$2 step_status=$3 err=$4 sig=null dur group tmp keep=$VX_ERROR
  [ -d "$UPDATE_DIR" ] && [ ! -L "$UPDATE_DIR" ] || return 0
  # Only where root alone writes: a directory someone else can write gets
  # nothing (the journal says why).
  if ! vx_root_only "$UPDATE_DIR"; then VX_ERROR=$keep; return 0; fi
  VX_ERROR=$keep
  dur=$(( $(date +%s) - START_S ))
  [ "$dur" -ge 0 ] || dur=0
  if [ -n "$VX_SIG_STATUS" ]; then
    sig=$(printf '{"status": %s, "signer": %s, "key": %s, "enforced": %s, "allowed_signers": %s}' \
      "$(json_str "$VX_SIG_STATUS")" "$(json_str_or_null "$VX_SIG_SIGNER")" "$(json_str_or_null "$VX_SIG_KEY")" \
      "$([ "$VX_SIG_ENFORCED" = 1 ] && echo true || echo false)" "$(json_str "$SIGNERS")")
  fi
  tmp=$(mktemp "$UPDATE_DIR/.last-result.json.XXXXXX") || return 0
  {
    printf '{\n'
    printf '  "status": %s,\n' "$(json_str "$status")"
    printf '  "mode": null,\n'
    printf '  "from_sha": %s,\n' "$(json_str_or_null "$(read_sha "$UPDATE_DIR/applied_sha")")"
    printf '  "to_sha": %s,\n' "$(json_str_or_null "$VX_SHA")"
    printf '  "prev_source": null,\n'
    printf '  "bad_sha": %s,\n' "$(json_str_or_null "$(read_sha "$UPDATE_DIR/bad_sha")")"
    printf '  "started_at": %s,\n' "$(json_str "$STARTED_AT")"
    printf '  "finished_at": %s,\n' "$(json_str "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
    printf '  "duration_sec": %d.000,\n' "$dur"
    printf '  "deps_changed": false,\n'
    printf '  "mpd_changed": false,\n'
    printf '  "signature": %s,\n' "$sig"
    printf '  "migrations_before": null,\n  "migrations_after": null,\n'
    printf '  "plugin_migrations_before": null,\n  "plugin_migrations_after": null,\n'
    printf '  "backup": null,\n  "test_backup": null,\n'
    printf '  "db_restored": false,\n  "test_db_restored": false,\n'
    printf '  "error": %s,\n' "$(json_str "$err")"
    printf '  "steps": [{"name": %s, "status": %s, "duration_sec": %d.000, "detail": %s}]\n' \
      "$(json_str "$step")" "$(json_str "$step_status")" "$dur" "$(json_str "$err")"
    printf '}\n'
  } >"$tmp" || { rm -f "$tmp"; return 0; }
  # The core reads it as the service user, and no other account may: the
  # same 0640 to that user's group as apply-update.sh's results.
  group=$(id -gn "$SVC_USER" 2>/dev/null) || group=""
  if [ -n "$group" ] && chgrp -- "$group" "$tmp" 2>/dev/null; then chmod 0640 "$tmp"; else chmod 0644 "$tmp"; fi
  mv -f "$tmp" "$RESULT_FILE" || rm -f "$tmp"
}

# ─── Settings ────────────────────────────────────────────────────────────

settings() {
  if [ "$HARNESS" = 1 ]; then
    VX_ROOT_UIDS="0 $EUID"
    VX_TOP=$(dirname "$ROOT")
  else
    [ "$EUID" = 0 ] || die "this runs as root (domovoi-update.service runs it)"
    VX_ROOT_UIDS=0
    VX_TOP=/
    # Root runs what it finds on PATH: take it from no one's shell setup.
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    export PATH
  fi
  if [ -z "$REPO" ]; then die "DOMOVOI_REPO_DIR is not set: domovoi-update.service sets it to the checkout (Environment=DOMOVOI_REPO_DIR=...)"; fi
  if ! [[ $REPO =~ ^/[A-Za-z0-9._/@+-]+$ ]] || [[ $REPO/ == *//* || $REPO/ == */./* || $REPO/ == */../* ]]; then
    die "DOMOVOI_REPO_DIR must be an absolute, plain path (letters, digits and . _ / @ + -): $REPO"
  fi
  [ -d "$REPO" ] || die "no checkout at $REPO (DOMOVOI_REPO_DIR)"
  [[ $UPDATE_DIR == /?* ]] || die "DOMOVOI_UPDATE_DIR must be an absolute path: $UPDATE_DIR"
  if [ -z "$SVC_USER" ] && command -v systemctl >/dev/null 2>&1; then
    SVC_USER=$(systemctl show -p User --value domovoi-core.service 2>/dev/null || true)
  fi
  if [ -z "$SVC_USER" ]; then SVC_USER=$(stat -c %U "$REPO" 2>/dev/null || true); fi
  [[ $SVC_USER =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "can't tell the service user (set DOMOVOI_USER in /etc/default/domovoi-update): '${SVC_USER}'"
  VX_REPO=$REPO
  VX_USER=$SVC_USER
  VX_MIRROR=$UPDATE_DIR/mirror.git
  VX_SIGNERS=$SIGNERS
  VX_REVOKED=$REVOKED
}

# One verifier at a time (the update unit, a by-hand export).
lock() {
  command -v flock >/dev/null 2>&1 || return 0
  exec 7>"$UPDATE_DIR/.verify.lock"
  flock -w 600 7 || die "another verifier run holds $UPDATE_DIR/.verify.lock"
}

# STEP NAME for the result when a part of the copy fails: the signature
# keeps its own step, as apply-update.sh records it.
refuse() {
  local status=$1 step=$2 step_status=$3 err=$4
  log "refused: $err"
  if [ "$WRITE_RESULT" = 1 ]; then write_refusal "$status" "$step" "$step_status" "$err"; fi
  exit 1
}

# The copy of HEAD, into $1. Sets VX_SHA and VX_DIR.
make_copy() {
  local parent=$1
  # Created when missing, never re-moded: a directory someone opened up is
  # refused below rather than quietly closed again.
  [ -d "$UPDATE_DIR" ] || install -d -m 0755 "$UPDATE_DIR" || refuse failed verify failed "can't create $UPDATE_DIR"
  vx_root_only "$UPDATE_DIR" || refuse failed verify failed "$VX_ERROR: root keeps the update's state and the verified mirror there"
  lock
  vx_mirror || refuse failed verify failed "$VX_ERROR"
  vx_fetch || refuse failed verify failed "$VX_ERROR"
  if ! vx_verify; then
    refuse refused signature refused "$VX_SIG_DETAIL; signed updates are enforced by $SIGNERS, so nothing was changed. Pull a commit signed by a listed key, or remove that file to turn enforcement off (docs/LINUX_HOST.md, Signed updates)."
  fi
  [ -d "$parent" ] || install -d -m 0755 "$parent" || refuse failed verify failed "can't create $parent"
  vx_export "$parent" || refuse failed verify failed "$VX_ERROR"
  [ -f "$VX_DIR/scripts/linux/apply-update.sh" ] \
    || refuse failed verify failed "commit $VX_SHA has no scripts/linux/apply-update.sh: there is no update to run"
  if [ "$VX_SIG_ENFORCED" = 1 ]; then
    log "$VX_SHA: $VX_SIG_DETAIL"
  else
    log "$VX_SHA: signed updates are not enforced (no $SIGNERS): the copy is the commit HEAD names, and nothing checks who made it"
  fi
  log "the verified copy of $VX_SHA is $VX_DIR"
}

# Copies of other commits, from earlier runs that were not cleaned up (a
# run by hand; the unit's RuntimeDirectory= removes its own at the end).
prune_copies() {
  local d
  for d in "$RUN_DIR"/*; do
    [ -d "$d" ] && [ ! -L "$d" ] || continue
    case ${d##*/} in "$VX_SHA") continue ;; esac
    [[ ${d##*/} =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] || continue
    rm -rf -- "$d"
  done
}

cmd_prepare() {
  rm -f -- "$POINTER"
  make_copy "$RUN_DIR"
  prune_copies
  printf '%s\n' "$VX_SHA" >"$POINTER.tmp" && mv -f "$POINTER.tmp" "$POINTER" \
    || refuse failed verify failed "can't write $POINTER"
}

cmd_run() {
  local sha dir
  vx_root_only "$RUN_DIR" || die "$VX_ERROR"
  [ -f "$POINTER" ] && [ ! -L "$POINTER" ] || die "no verified copy to run ($POINTER): the prepare step did not finish"
  sha=$(tr -d '[:space:]' <"$POINTER")
  rm -f -- "$POINTER"
  [[ $sha =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] || die "$POINTER does not name a commit"
  dir=$RUN_DIR/$sha
  vx_root_only "$dir" || die "$VX_ERROR"
  [ -f "$dir/scripts/linux/apply-update.sh" ] && [ ! -L "$dir/scripts/linux/apply-update.sh" ] \
    || die "the verified copy $dir has no scripts/linux/apply-update.sh"
  cd /
  # The verifier's lock (relaunch held it through prepare) is not the
  # update's: apply-update.sh takes its own.
  exec 7>&-
  log "running $dir/scripts/linux/apply-update.sh (the verified copy of $sha)"
  exec env DOMOVOI_REPO_DIR="$REPO" DOMOVOI_USER="$SVC_USER" DOMOVOI_VERIFIED_SHA="$sha" \
    DOMOVOI_VERIFIED_DIR="$dir" DOMOVOI_VERIFIED_MIRROR="$VX_MIRROR" \
    bash "$dir/scripts/linux/apply-update.sh"
}

usage() {
  cat <<'EOF'
Usage: verify-update.sh prepare|run|relaunch|export [DIR]

  prepare    fetch HEAD of $DOMOVOI_REPO_DIR into root's mirror, verify it,
             and write the files root runs into /run/domovoi-update/<sha>
  run        run that copy's apply-update.sh
  relaunch   prepare, then run
  export     prepare without the update; print the copy's directory
             (under DIR, default /run/domovoi-update)
EOF
}

main() {
  local cmd=${1:-}
  WRITE_RESULT=0
  STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  START_S=$(date +%s)
  case $cmd in
    prepare|relaunch)
      WRITE_RESULT=1
      settings
      cmd_prepare
      if [ "$cmd" = relaunch ]; then cmd_run; fi
      ;;
    run)
      settings
      cmd_run
      ;;
    export)
      settings
      if [ -n "${2:-}" ]; then
        [[ $2 == /?* ]] || die "export DIR must be an absolute path"
        RUN_DIR=${2%/}
      fi
      make_copy "$RUN_DIR"
      printf '%s\n' "$VX_DIR"
      ;;
    -h|--help) usage ;;
    *) usage >&2; exit 2 ;;
  esac
}

main "$@"; exit $?
