# Case selection for the hermetic harnesses in this directory. Sourced by
# test-apply-update.sh, test-install-update-unit.sh and test-provision.sh;
# never run on its own.
#
#   bash scripts/linux/tests/test-<x>.sh [--case NAME|GLOB]... [--list]
#
# --case NAME   runs that case. NAME is the case function without its case_
#               prefix (case_noop_restart is noop_restart; the prefixed name
#               works too). Repeatable; a case runs once however many
#               patterns pick it, in the harness's own order.
# --case GLOB   a bash glob over the same names: --case 'searxng_*'.
# --list        prints every case name, in run order, and exits.
#
# A --case that picks nothing is an error (exit 2), never "0 passed,
# 0 failed": a typo must not pass for a green run. ONLY=<regex>, the older
# knob, still narrows whatever is selected.

HARNESS_CASE_PATTERNS=()
HARNESS_LIST=0

# harness_parse_args "$@": read the options above; anything else is a usage
# error. Call it before the harness does any work.
harness_parse_args() {
  while [ $# -gt 0 ]; do
    case $1 in
      --case)
        if [ $# -lt 2 ] || [ -z "$2" ]; then
          echo "$(basename "$0"): --case needs a case name or glob (see --list)" >&2
          exit 2
        fi
        HARNESS_CASE_PATTERNS+=("$2")
        shift 2
        ;;
      --case=?*) HARNESS_CASE_PATTERNS+=("${1#--case=}"); shift ;;
      --list) HARNESS_LIST=1; shift ;;
      -h | --help)
        sed -n '/^#   bash/,/^# knob/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
      *)
        echo "$(basename "$0"): unknown argument: $1 (options: --case NAME|GLOB, --list)" >&2
        exit 2
        ;;
    esac
  done
}

# harness_select: CASES (the harness's ordered list of case functions) in,
# SELECTED_CASES out. With --list it prints the names and exits 0; a --case
# that matches no case exits 2.
harness_select() {
  local c name p hit bad=0
  if [ "$HARNESS_LIST" = 1 ]; then
    for c in "${CASES[@]}"; do echo "${c#case_}"; done
    exit 0
  fi
  for p in ${HARNESS_CASE_PATTERNS[@]+"${HARNESS_CASE_PATTERNS[@]}"}; do
    hit=0
    for c in "${CASES[@]}"; do
      # shellcheck disable=SC2053  # the right-hand side is a glob on purpose
      if [[ ${c#case_} == ${p#case_} ]]; then hit=1; break; fi
    done
    if [ "$hit" = 0 ]; then
      echo "$(basename "$0"): no case matches --case '$p' (--list shows them)" >&2
      bad=1
    fi
  done
  [ "$bad" = 0 ] || exit 2
  SELECTED_CASES=()
  for c in "${CASES[@]}"; do
    name=${c#case_}
    if [ ${#HARNESS_CASE_PATTERNS[@]} -gt 0 ]; then
      hit=0
      for p in "${HARNESS_CASE_PATTERNS[@]}"; do
        # shellcheck disable=SC2053
        if [[ $name == ${p#case_} ]]; then hit=1; break; fi
      done
      [ "$hit" = 1 ] || continue
    fi
    if [ -n "${ONLY:-}" ] && ! [[ $c =~ ${ONLY} ]]; then continue; fi
    SELECTED_CASES+=("$c")
  done
}

# harness_selection_note: " (N of M cases)" when the run is narrowed, for
# the harness's last line; nothing for a full run.
harness_selection_note() {
  if [ ${#SELECTED_CASES[@]} -ne ${#CASES[@]} ]; then
    printf ' (%d of %d cases selected)' "${#SELECTED_CASES[@]}" "${#CASES[@]}"
  fi
}
