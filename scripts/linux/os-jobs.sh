#!/usr/bin/env bash
# os-jobs.sh: Ubuntu's own background jobs, set to follow the household's
# internet answer (owner decision 2026-10-11).
#
#   sudo bash os-jobs.sh --internet never            # mask the jobs that go online
#   sudo bash os-jobs.sh --internet always           # put them back as Ubuntu ships them
#   sudo bash os-jobs.sh --internet sometimes --check   # status 0: already so
#
# Two scripts call it, so the jobs follow the answer wherever the box
# re-applies its configuration: scripts/linux/provision.sh (its os-jobs
# step) and scripts/linux/apply-update.sh (after every healthy restart or
# update that domovoi-update.service runs, which is what the dashboard's
# Restart button starts). An answer changed in Settings, Internet takes
# effect here at the next of those, not at the moment it is saved: the
# core runs as the service user and can't change systemd units.
#
# What it does:
#   never              masks the units below that are installed, are not
#                      masked yet, and have no unit file of the
#                      administrator's in /etc/systemd/system, then stops
#                      them; each one is written down in
#                      /var/lib/domovoi-os-jobs/masked BEFORE it is masked
#                      (and crossed off again if the mask fails), so a
#                      killed run never leaves a mask nobody wrote down
#                        apt-daily.timer apt-daily-upgrade.timer
#                        unattended-upgrades.service  (package updates)
#                        motd-news.timer               (login news)
#                        ua-timer.timer                (Ubuntu Pro)
#                        apport-autoreport.path apport-autoreport.timer
#                                                      (crash reports)
#                        snapd.snap-repair.timer       (snap repairs)
#   always, sometimes  unmasks exactly the units that file names (a mask
#                      somebody else made stays), and starts the ones that
#                      are enabled, so a timer runs again as shipped
#                      without waiting for a reboot
#   unanswered ("")    leaves them as they are
#   WSL, any answer    wsl-pro.service and snapd (snapd.service,
#                      snapd.socket and every other snapd.* service, socket
#                      and timer installed) are disabled, stopped and
#                      masked: the Windows installer's distro uses neither.
#                      Masked as well as disabled, so that neither socket
#                      activation nor snapd.seeded.service starts snapd
#                      again. Never unmasked by this script. On bare metal
#                      they are left alone (an administrator's server may
#                      use snaps).
#
# A mask is what `systemctl mask` makes, a link to /dev/null in
# /etc/systemd/system, made by hand so it works the same before systemd
# runs (and in the tests). systemctl is called only while systemd is PID 1.
#
# Options:
#   --internet ANSWER   always | sometimes | never | "" (unanswered)
#   --wsl yes|no|auto   WSL mode (default auto: the kernel says microsoft,
#                       the rule domovoi/host_kind.py uses)
#   --check             change nothing; status 0 when every unit is already
#                       as the answer wants, 1 when not
#   --dry-run           say what it would do; change nothing
#
# Output: one line per unit it changes, then one summary line that starts
# with "os-jobs: " ("os-jobs: nothing to change", or what changed).
# Exit status: 0 done (or --check: as wanted), 1 a change failed (or
# --check: not as wanted), 2 bad usage.
#
# DOMOVOI_PROVISION_ROOT with DOMOVOI_PROVISION_TEST_HARNESS=1 is for the
# tests only (scripts/linux/tests/test-provision.sh and
# test-apply-update.sh), exactly as in provision.sh: a directory put in
# front of every system path this reads or writes. Either one without the
# other makes it refuse to run.

set -euo pipefail
umask 022

PROG=os-jobs

ROOT=""
if [ -n "${DOMOVOI_PROVISION_ROOT:-}" ] || [ -n "${DOMOVOI_PROVISION_TEST_HARNESS:-}" ]; then
  if [ "${DOMOVOI_PROVISION_TEST_HARNESS:-}" != 1 ] || [[ ${DOMOVOI_PROVISION_ROOT:-} != /* ]] \
      || [ ! -d "$DOMOVOI_PROVISION_ROOT" ] || [ "$(cd "$DOMOVOI_PROVISION_ROOT" && pwd -P)" = / ]; then
    printf '%s: DOMOVOI_PROVISION_ROOT and DOMOVOI_PROVISION_TEST_HARNESS are for the test harnesses only, and only together\n' "$PROG" >&2
    exit 2
  fi
  ROOT=${DOMOVOI_PROVISION_ROOT%/}
else
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export PATH
fi

# The jobs that go online by themselves (windows-installer step 1: the
# timers and services a stock Ubuntu 26.04 runs).
NEVER_UNITS=(apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service
  motd-news.timer ua-timer.timer apport-autoreport.path apport-autoreport.timer
  snapd.snap-repair.timer)
WSL_UNITS=(wsl-pro.service)
SNAPD_PREFIX=snapd.

UNIT_DIR=$ROOT/etc/systemd/system
VENDOR_DIRS=("$ROOT/usr/lib/systemd/system" "$ROOT/lib/systemd/system")
STATE_DIR=$ROOT/var/lib/domovoi-os-jobs
RECORD=$STATE_DIR/masked
PROC=$ROOT/proc

INTERNET=""
INTERNET_SET=0
WSL_OPT=auto
CHECK=0
DRY_RUN=0

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
usage_error() { printf '%s: %s\n' "$PROG" "$1" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case $1 in
    --internet) [ $# -ge 2 ] || usage_error "--internet needs a value (always, sometimes, never, or \"\")"; INTERNET=$2; INTERNET_SET=1; shift 2 ;;
    --internet=*) INTERNET=${1#*=}; INTERNET_SET=1; shift ;;
    --wsl) [ $# -ge 2 ] || usage_error "--wsl needs yes, no or auto"; WSL_OPT=$2; shift 2 ;;
    --wsl=*) WSL_OPT=${1#*=}; shift ;;
    --check) CHECK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage_error "unknown option: $1" ;;
  esac
done
[ "$INTERNET_SET" = 1 ] || usage_error "--internet is required (always, sometimes, never, or \"\" while unanswered)"
case $INTERNET in ""|always|sometimes|never) ;; *) usage_error "--internet must be always, sometimes, never or empty: $INTERNET" ;; esac

detect_wsl() {
  local f k
  for f in "$PROC/sys/kernel/osrelease" "$PROC/version"; do
    if k=$(cat "$f" 2>/dev/null); then
      case ${k,,} in *microsoft*) echo 1 ;; *) echo 0 ;; esac
      return 0
    fi
  done
  echo 0
}
case $WSL_OPT in
  yes) WSL=1 ;;
  no) WSL=0 ;;
  auto) WSL=$(detect_wsl) ;;
  *) usage_error "--wsl must be yes, no or auto: $WSL_OPT" ;;
esac

systemd_running() { [ "$(cat "$PROC/1/comm" 2>/dev/null)" = systemd ]; }
masked() { [ "$(readlink "$UNIT_DIR/$1" 2>/dev/null)" = /dev/null ]; }
# An administrator's own unit file (or a link to one) where the mask would go.
admins_file() { { [ -e "$UNIT_DIR/$1" ] || [ -L "$UNIT_DIR/$1" ]; } && ! masked "$1"; }
installed() {
  local d
  masked "$1" && return 0
  for d in "${VENDOR_DIRS[@]}" "$UNIT_DIR"; do [ -e "$d/$1" ] && return 0; done
  return 1
}
recorded() { [ -f "$RECORD" ] && grep -qxF -- "$1" "$RECORD"; }
in_list() { local x=$1; shift; case " $* " in *" $x "*) return 0 ;; esac; return 1; }

# Every snapd.* service, socket and timer installed (vendor directories, or
# already masked in /etc).
snapd_units() {
  local d f n
  for d in "${VENDOR_DIRS[@]}" "$UNIT_DIR"; do
    for f in "$d/$SNAPD_PREFIX"*; do
      [ -e "$f" ] || [ -L "$f" ] || continue
      n=${f##*/}
      case $n in *.service|*.socket|*.timer) printf '%s\n' "$n" ;; esac
    done
  done | LC_ALL=C sort -u
}


# What the answer wants.
#   WSL_OFF        WSL only: off whatever the answer
#   WANT_MASKED    to mask now for "never" (and write down)
#   WANT_UNMASKED  written down earlier, to unmask now for always/sometimes
#   DROP           written down earlier, to cross off without touching
#   KEPT           an administrator's own unit file sits where the mask
#                  would go: left alone, every time, and said so
WSL_OFF=() WANT_MASKED=() WANT_UNMASKED=() DROP=() KEPT=() WSL_TODO=()
if [ "$WSL" = 1 ]; then
  for u in "${WSL_UNITS[@]}"; do
    if installed "$u"; then WSL_OFF+=("$u"); fi
  done
  while IFS= read -r u; do
    if [ -n "$u" ]; then WSL_OFF+=("$u"); fi
  done < <(snapd_units)
fi
for u in "${WSL_OFF[@]}"; do
  if admins_file "$u"; then KEPT+=("$u")
  elif ! masked "$u"; then WSL_TODO+=("$u"); fi
done
case $INTERNET in
  never)
    for u in "${NEVER_UNITS[@]}"; do
      if in_list "$u" "${WSL_OFF[@]}" || ! installed "$u" || masked "$u"; then continue; fi
      if admins_file "$u"; then KEPT+=("$u"); else WANT_MASKED+=("$u"); fi
    done ;;
  always|sometimes)
    if [ -f "$RECORD" ]; then
      while IFS= read -r u; do
        [ -n "$u" ] || continue
        # On WSL the WSL rule owns snapd's timer; a mask that is gone
        # (somebody unmasked it by hand) is only crossed off.
        if in_list "$u" "${WSL_OFF[@]}" || ! masked "$u"; then DROP+=("$u"); else WANT_UNMASKED+=("$u"); fi
      done <"$RECORD"
    fi ;;
esac

where() { printf 'internet: %s; WSL: %s' "${INTERNET:-unanswered}" "$([ "$WSL" = 1 ] && echo yes || echo no)"; }
kept_note() {
  [ "${#KEPT[@]}" -eq 0 ] || printf '; left alone, a unit file of their own in %s: %s' "/etc/systemd/system" "${KEPT[*]}"
}

if [ "$CHECK" = 1 ]; then
  if [ "${#WANT_MASKED[@]}" -eq 0 ] && [ "${#WANT_UNMASKED[@]}" -eq 0 ] && [ "${#WSL_TODO[@]}" -eq 0 ]; then
    echo "$PROG: as the answer wants ($(where))$(kept_note)"
    exit 0
  fi
  echo "$PROG: not as the answer wants ($(where)):${WSL_TODO[*]:+ disable and mask ${WSL_TODO[*]};}${WANT_MASKED[*]:+ mask ${WANT_MASKED[*]};}${WANT_UNMASKED[*]:+ unmask ${WANT_UNMASKED[*]};}"
  exit 1
fi

if [ "${#WANT_MASKED[@]}" -eq 0 ] && [ "${#WANT_UNMASKED[@]}" -eq 0 ] && [ "${#WSL_TODO[@]}" -eq 0 ] && [ "${#DROP[@]}" -eq 0 ]; then
  echo "$PROG: nothing to change ($(where))$(kept_note)"
  exit 0
fi

if [ "$DRY_RUN" = 1 ]; then
  for u in "${WSL_TODO[@]}"; do echo "would disable and mask $u (WSL: Domovoi uses neither wsl-pro nor snapd)"; done
  for u in "${WANT_MASKED[@]}"; do echo "would mask $u (internet: never)"; done
  for u in "${WANT_UNMASKED[@]}"; do echo "would unmask $u (internet: $INTERNET)"; done
  echo "$PROG: dry run, nothing changed ($(where))$(kept_note)"
  exit 0
fi

if [ -z "$ROOT" ] && [ "$(id -u)" != 0 ]; then
  printf '%s: run it as root: it changes units in /etc/systemd/system\n' "$PROG" >&2
  exit 1
fi

FAILED=0
DONE_WSL=() DONE_MASKED=() DONE_UNMASKED=() NOT_MASKED=()

mask_unit() {
  mkdir -p "$UNIT_DIR" && ln -s /dev/null "$UNIT_DIR/$1" && return 0
  echo "could not mask $1"
  FAILED=1
  return 1
}

# The record: what this script masked for "never" and has not put back.
# record_with ADD... -- GONE...: the record's units plus ADD, less GONE,
# sorted, one per line.
record_with() {
  local add=() gone=" "
  while [ $# -gt 0 ] && [ "$1" != -- ]; do add+=("$1"); shift; done
  [ $# -eq 0 ] || shift
  gone+="$* "
  { if [ -f "$RECORD" ]; then cat "$RECORD"; fi; printf '%s\n' "${add[@]}"; } \
    | awk -v gone="$gone" 'NF && index(gone, " " $0 " ") == 0' | LC_ALL=C sort -u
}
# write_record CONTENT: the record by rename (none at all when empty);
# status 1 when it could not be written.
write_record() {
  local tmp
  if [ -z "$1" ]; then rm -f "$RECORD"; return; fi
  mkdir -p "$STATE_DIR" && tmp=$(mktemp "$STATE_DIR/.masked.XXXXXX") || return 1
  if ! { printf '%s\n' "$1" >"$tmp" && chmod 0644 "$tmp" && mv -f "$tmp" "$RECORD"; }; then
    rm -f "$tmp"
    return 1
  fi
}

# never: written down FIRST, masked after. A run killed in between leaves a
# unit written down and not masked, which the next run masks (never) or
# crosses off (always); the other order could leave a mask nobody wrote
# down, which "always" would then never take back. Nothing written down,
# nothing masked.
if [ "${#WANT_MASKED[@]}" -gt 0 ]; then
  if ! write_record "$(record_with "${WANT_MASKED[@]}" -- "${DROP[@]}")"; then
    echo "could not write $RECORD: nothing masked, so nothing goes unrecorded"
    FAILED=1
    WANT_MASKED=()
  fi
fi

# WSL: off whatever the answer. Disabled first (its install links go), and
# stopped, while systemd can still load it. Never written down: no answer
# takes these back.
if [ "${#WSL_TODO[@]}" -gt 0 ]; then
  if systemd_running; then systemctl disable --now "${WSL_TODO[@]}" >/dev/null 2>&1 || true; fi
  for u in "${WSL_TODO[@]}"; do
    if mask_unit "$u"; then DONE_WSL+=("$u"); echo "disabled and masked $u (WSL: Domovoi uses neither wsl-pro nor snapd)"; fi
  done
fi

for u in "${WANT_MASKED[@]}"; do
  if mask_unit "$u"; then DONE_MASKED+=("$u"); echo "masked $u (internet: never)"; else NOT_MASKED+=("$u"); fi
done

# always / sometimes: unmask what was written down (systemctl unmask
# removes the same link).
for u in "${WANT_UNMASKED[@]}"; do
  if rm -f "$UNIT_DIR/$u"; then DONE_UNMASKED+=("$u"); echo "unmasked $u (internet: $INTERNET)"
  else echo "could not unmask $u"; FAILED=1; fi
done

# Then the record drops what is no longer masked by this script: what was
# unmasked, crossed off, or could not be masked after all.
if [ $(( ${#DONE_UNMASKED[@]} + ${#DROP[@]} + ${#NOT_MASKED[@]} )) -gt 0 ]; then
  if ! write_record "$(record_with -- "${DONE_UNMASKED[@]}" "${DROP[@]}" "${NOT_MASKED[@]}")"; then
    echo "could not write $RECORD"
    FAILED=1
  fi
fi

if systemd_running && [ $(( ${#DONE_WSL[@]} + ${#DONE_MASKED[@]} + ${#DONE_UNMASKED[@]} )) -gt 0 ]; then
  systemctl daemon-reload || FAILED=1
  # A running timer keeps its schedule until it is stopped; a service
  # (unattended-upgrades) is left to finish what it is doing.
  for u in "${DONE_MASKED[@]}"; do
    case $u in *.timer|*.path) systemctl stop "$u" >/dev/null 2>&1 || true ;; esac
  done
  # An enabled unit that is unmasked starts now, as it would at boot.
  for u in "${DONE_UNMASKED[@]}"; do
    if [ "$(systemctl is-enabled "$u" 2>/dev/null || true)" = enabled ]; then
      systemctl start "$u" >/dev/null 2>&1 || echo "could not start $u (it starts at the next boot)"
    fi
  done
fi

summary=""
[ "${#DONE_WSL[@]}" -eq 0 ] || summary="disabled and masked ${DONE_WSL[*]}"
[ "${#DONE_MASKED[@]}" -eq 0 ] || summary="${summary:+$summary; }masked ${DONE_MASKED[*]}"
[ "${#DONE_UNMASKED[@]}" -eq 0 ] || summary="${summary:+$summary; }unmasked ${DONE_UNMASKED[*]}"
[ "${#DROP[@]}" -eq 0 ] || summary="${summary:+$summary; }crossed off ${DROP[*]} (masked no longer, or WSL's)"
[ -n "$summary" ] || summary="changed nothing"
echo "$PROG: $summary ($(where))$(kept_note)"
exit "$FAILED"
