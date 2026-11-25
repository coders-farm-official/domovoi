#!/usr/bin/env bash
# Switch one XVF3800 satellite's DSP between the noisy-room trial settings,
# over SSH, and put it back. Run from any machine that can SSH to the Pi.
#
#   xvf_dsp.sh <user@satellite> read            print the current values
#   xvf_dsp.sh <user@satellite> set A|B|C|D     save the originals once, apply a setting
#   xvf_dsp.sh <user@satellite> mark <text>     a marker in the satellite's journal ("1m", "3m")
#   xvf_dsp.sh <user@satellite> --revert        write the saved originals back
#
# Settings (A is the firmware's own; each later one builds on the one before):
#   A  defaults: nothing written
#   B  AGC maximum gain 31.6 (30 dB) instead of the firmware's maximum; a near
#      voice then stays louder than the crowd after the AGC
#   C  B + stronger noise suppression: gain floors 0.05 for stationary
#      (PP_MIN_NS) and non-stationary (PP_MIN_NN, the one babble hits) noise
#   D  C + AGC off at a fixed gain of 10 (20 dB): no level levelling at all
#
# Safety:
#   * Only runtime writes. SAVE_CONFIGURATION is never sent, so nothing reaches
#     the chip's flash: unplugging the array (or `xvf_host REBOOT 1`, or a Pi
#     reboot that power-cycles USB) brings back the firmware defaults whatever
#     happened here.
#   * `set` saves the values it is about to change to ~/xvf-trial-originals.txt
#     on the satellite, once (a later `set` keeps the first copy); --revert
#     writes them back and deletes the file.
#   * Every action leaves `xvf-trial: ...` in the journal, so the report script
#     can split the run into blocks.
#
# xvf_host is the pinned Seeed tool the satellite already uses for its LEDs
# (/opt/xvf3800/xvf_host via `sudo -n`, satellite/leds.py). Parameter names:
# python_control/xvf_host.py in respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY at
# the pinned commit (XVF_HOST_COMMIT in domovoi/satellite_media/fetchers.py).
# Check the `read` output first: if a name is refused, stop and report it.
set -euo pipefail

HOST=${1:?usage: xvf_dsp.sh <user@satellite> read|set X|mark TEXT|--revert}
ACTION=${2:?action}
XVF=${XVF_HOST:-/opt/xvf3800/xvf_host}
PARAMS="PP_AGCONOFF PP_AGCMAXGAIN PP_AGCGAIN PP_AGCDESIREDLEVEL PP_MIN_NS PP_MIN_NN PP_ATTNS_MODE AEC_FIXEDBEAMSONOFF AEC_ASROUTONOFF AEC_ASROUTGAIN"
WRITABLE="PP_AGCONOFF PP_AGCMAXGAIN PP_AGCGAIN PP_MIN_NS PP_MIN_NN"
SAVED='~/xvf-trial-originals.txt'

remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

read_all() {
  remote "for p in $PARAMS; do printf '%s ' \"\$p\"; sudo -n $XVF \"\$p\" 2>&1 | tail -n 1; done"
}

case "$ACTION" in
  read)
    read_all
    ;;
  set)
    S=${3:?setting A, B, C or D}
    case "$S" in
      A) W="" ;;
      B) W="PP_AGCMAXGAIN 31.6" ;;
      C) W="PP_AGCMAXGAIN 31.6;PP_MIN_NS 0.05;PP_MIN_NN 0.05" ;;
      D) W="PP_AGCMAXGAIN 31.6;PP_MIN_NS 0.05;PP_MIN_NN 0.05;PP_AGCONOFF 0;PP_AGCGAIN 10" ;;
      *) echo "unknown setting $S" >&2; exit 2 ;;
    esac
    # Keep the originals once: the values before the FIRST set of the trial.
    remote "test -s $SAVED || for p in $WRITABLE; do printf '%s ' \"\$p\"; sudo -n $XVF \"\$p\" | tail -n 1; done > $SAVED"
    remote "cat $SAVED" | sed 's/^/original: /'
    # Back to the originals first, so the settings don't stack in odd orders.
    remote "while read -r p rest; do vals=\$(echo \"\$rest\" | grep -oE -- '-?[0-9][0-9.]*(e[-+]?[0-9]+)?' | tr '\n' ' '); sudo -n $XVF \"\$p\" \$vals >/dev/null; done < $SAVED"
    IFS=';' read -ra CMDS <<< "$W"
    for c in "${CMDS[@]}"; do
      [ -n "$c" ] && remote "sudo -n $XVF $c" && echo "wrote: $c"
    done
    remote "logger -t xvf-trial 'setting=$S'"
    read_all
    ;;
  mark)
    remote "logger -t xvf-trial 'mark=${3:?text}'"
    ;;
  --revert)
    remote "test -s $SAVED" || { echo "no saved originals on $HOST: nothing was changed, or it was already reverted"; exit 0; }
    remote "while read -r p rest; do vals=\$(echo \"\$rest\" | grep -oE -- '-?[0-9][0-9.]*(e[-+]?[0-9]+)?' | tr '\n' ' '); sudo -n $XVF \"\$p\" \$vals && echo \"restored \$p \$vals\"; done < $SAVED"
    remote "rm -f $SAVED; logger -t xvf-trial 'setting=reverted'"
    read_all
    echo "If anything looks off: unplug the array's USB for 5 s (firmware defaults; nothing was saved to flash),"
    echo "then: sudo systemctl restart domovoi-satellite"
    ;;
  *)
    echo "unknown action $ACTION" >&2; exit 2 ;;
esac
