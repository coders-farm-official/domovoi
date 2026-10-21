#!/usr/bin/env bash
# Printing harness: Domovoi's own IPP client, PWG Raster and URF encoders
# against a REAL IPP Everywhere printer simulator, and its eSCL client
# against our fake scanner.
#
# ippeveprinter (CUPS 2.4, Apache-2.0; Debian/Ubuntu cups-ipp-utils) is a
# test tool only, never shipped. Each case starts its own instance on
# loopback with `-r off` (no DNS-SD: nothing is advertised anywhere) and
# `-k -d <spool>` (every job's file is kept, so the bytes we sent can be
# checked):
#
#   * a PDF printer (-f application/pdf,image/pwg-raster,image/jpeg): the
#     PDF must arrive unchanged;
#   * a raster-only, two-sided printer (-f image/pwg-raster,image/urf -2,
#     -c ippeveps): ippeveprinter's own raster reader (ippeveps) must
#     accept our PWG Raster and URF, the kept .pwg must have 3 valid
#     headers at the printer's resolution, and the back page must be turned
#     the way the printer's pwg-raster-document-sheet-back says;
#   * a Create-Job canceled before its document is sent.
#
# The eSCL case runs domovoi/tests/printing_fakes.FakeScanner (ours, stdlib)
# and needs no printer.
#
# Usage: bash scripts/linux/tests/test-printing.sh [--case NAME|GLOB]... [--list]
# (scripts/dev/run-linux-harness.sh printing runs it in its container:
# scripts/dev/printing-harness.Dockerfile, which has ippeveprinter.)
# Cases that need ippeveprinter are skipped where it isn't installed.

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$HERE/case-filter.sh"
harness_parse_args "$@"
SRC=$(cd "$HERE/../../.." && pwd)
PY=${HARNESS_PYTHON:-python3}
WORK=$(mktemp -d)
KEEP_WORK=${KEEP_WORK:-0}
IPPEVE=$(command -v ippeveprinter || ls /usr/sbin/ippeveprinter 2>/dev/null || true)
IPPEVEPS=$(command -v ippeveps || ls /usr/sbin/ippeveps /usr/lib/cups/command/ippeveps 2>/dev/null | head -n 1 || true)
PRINTER_PID=""
DBUS_PID=""

# ippeveprinter's Avahi client library refuses to start without a D-Bus,
# even with DNS-SD off (`-r off`): give it a private bus of its own,
# unprivileged, with no Avahi on it (CUPS 2.4.16).
start_bus() {
  [ -n "$DBUS_PID" ] && return 0
  command -v dbus-daemon >/dev/null 2>&1 || return 0
  local out
  out=$(dbus-daemon --session --fork --address="unix:path=$WORK/bus" --print-pid 2>/dev/null) || return 0
  DBUS_PID=$out
  export DBUS_SYSTEM_BUS_ADDRESS="unix:path=$WORK/bus"
}

cleanup() {
  stop_printer
  [ -n "$DBUS_PID" ] && kill "$DBUS_PID" 2>/dev/null
  if [ "$KEEP_WORK" = 1 ]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

PASSED=0
FAILED=0
SKIPPED=0
CASE_FAILED=0
CASE_NAME=""
PORT_NEXT=18631

new_case() { CASE_NAME=$1; CASE_FAILED=0; echo "case $CASE_NAME"; }
end_case() {
  if [ "$CASE_FAILED" = 0 ]; then PASSED=$((PASSED + 1)); echo "pass $CASE_NAME"; else FAILED=$((FAILED + 1)); echo "FAIL $CASE_NAME"; fi
}
fail() { echo "    FAIL: $*"; CASE_FAILED=1; }
skip_case() { SKIPPED=$((SKIPPED + 1)); echo "skip $CASE_NAME ($1)"; }

# start_printer FORMATS [ippeveprinter options...]: one printer on a fresh
# loopback port; sets PORT and SPOOL.
start_printer() {
  local formats=$1
  shift
  PORT=$PORT_NEXT
  PORT_NEXT=$((PORT_NEXT + 1))
  SPOOL=$WORK/spool-$PORT
  mkdir -p "$SPOOL"
  start_bus
  "$IPPEVE" -p "$PORT" -r off -k -d "$SPOOL" -f "$formats" "$@" \
    -M Domovoi -m "Harness printer" "harness$PORT" >"$WORK/ippeve-$PORT.log" 2>&1 &
  PRINTER_PID=$!
  local i
  for i in $(seq 1 50); do
    if "$PY" -c "import socket,sys; s=socket.socket(); s.settimeout(0.2); sys.exit(s.connect_ex(('127.0.0.1', $PORT)))" 2>/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  fail "ippeveprinter didn't start on $PORT: $(tail -n 5 "$WORK/ippeve-$PORT.log")"
  return 1
}

stop_printer() {
  if [ -n "$PRINTER_PID" ]; then
    kill "$PRINTER_PID" 2>/dev/null
    wait "$PRINTER_PID" 2>/dev/null
    PRINTER_PID=""
  fi
}

drive() {
  (cd "$SRC" && PYTHONDONTWRITEBYTECODE=1 "$PY" scripts/linux/tests/printing_harness.py "$1" "${PORT:-0}" "${SPOOL:-$WORK}") \
    || fail "$1 checks failed (above)"
}

need_printer() {
  if [ -z "$IPPEVE" ]; then
    skip_case "no ippeveprinter here (scripts/dev/run-linux-harness.sh printing has it)"
    return 1
  fi
  return 0
}

# ─── cases ───────────────────────────────────────────────────────────────

case_printing_attributes_and_validate() {
  new_case printing_attributes_and_validate
  need_printer || return 0
  if start_printer application/pdf,image/pwg-raster,image/jpeg; then drive attributes; fi
  stop_printer
  end_case
}

case_printing_pdf_arrives_unchanged() {
  new_case printing_pdf_arrives_unchanged
  need_printer || return 0
  if start_printer application/pdf,image/pwg-raster,image/jpeg; then drive pdf; fi
  stop_printer
  end_case
}

case_printing_pwg_raster_two_sided() {
  new_case printing_pwg_raster_two_sided
  need_printer || return 0
  if [ -z "$IPPEVEPS" ]; then skip_case "no ippeveps"; return 0; fi
  if start_printer image/pwg-raster,image/urf -2 -c "$IPPEVEPS"; then drive pwg_duplex; fi
  stop_printer
  end_case
}

case_printing_urf() {
  new_case printing_urf
  need_printer || return 0
  if [ -z "$IPPEVEPS" ]; then skip_case "no ippeveps"; return 0; fi
  if start_printer image/pwg-raster,image/urf -c "$IPPEVEPS"; then drive urf; fi
  stop_printer
  end_case
}

case_printing_cancel_before_the_document() {
  new_case printing_cancel_before_the_document
  need_printer || return 0
  if start_printer application/pdf,image/pwg-raster; then drive cancel; fi
  stop_printer
  end_case
}

case_printing_escl_fake_scanner() {
  new_case printing_escl_fake_scanner
  PORT=0 SPOOL=$WORK drive escl
  end_case
}

CASES=(
  case_printing_attributes_and_validate
  case_printing_pdf_arrives_unchanged
  case_printing_pwg_raster_two_sided
  case_printing_urf
  case_printing_cancel_before_the_document
  case_printing_escl_fake_scanner
)

harness_select
for c in ${SELECTED_CASES[@]+"${SELECTED_CASES[@]}"}; do
  "$c"
done

echo "printing harness: $PASSED passed, $FAILED failed, $SKIPPED skipped$(harness_selection_note)"
[ "$FAILED" -eq 0 ]
