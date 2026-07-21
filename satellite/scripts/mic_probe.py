#!/usr/bin/env python3
"""Probe a capture device's real delivery timing.

Diagnoses the "mic queue overflowing" symptom. It opens an input stream
exactly the way the satellite client does and reports the sample rate and
block size PortAudio actually negotiated, plus the measured callback and
frame rates over a few seconds. The verdict at the end points at the fix:

  * frames/sec far above the requested rate (or negotiated samplerate
    differs)  → the device is clocking faster than asked and PortAudio
    isn't resampling; the client must resample the input down.
  * frames/sec right but callbacks/sec high  → PortAudio isn't honoring
    the block size; the callback should re-frame to fixed-size chunks.
  * both nominal  → overflow is a slow consumer (CPU), not capture timing.

Run on the Pi, with the satellite service stopped so the device is free:

    sudo systemctl stop domovoi-satellite
    python3 satellite/scripts/mic_probe.py --device "reSpeaker XVF3800"
    sudo systemctl start domovoi-satellite
"""

from __future__ import annotations

import argparse
import time

import sounddevice as sd


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe a capture device's delivery timing.")
    ap.add_argument("--device", default="reSpeaker XVF3800",
                    help="device name substring or numeric index (default: %(default)s)")
    ap.add_argument("--rate", type=int, default=16000, help="requested sample rate")
    ap.add_argument("--channels", type=int, default=2, help="requested channels")
    ap.add_argument("--blocksize", type=int, default=480, help="requested block size (frames)")
    ap.add_argument("--seconds", type=float, default=5.0, help="measurement window")
    args = ap.parse_args()

    # Allow either an index or a name substring.
    device: object = args.device
    try:
        device = int(args.device)
    except (TypeError, ValueError):
        pass

    cb_count = [0]
    frame_count = [0]
    statuses: set[str] = set()

    def cb(indata, frames, t, status):  # noqa: ANN001
        cb_count[0] += 1
        frame_count[0] += frames
        if status:
            statuses.add(str(status))

    st = sd.RawInputStream(
        samplerate=args.rate, channels=args.channels, dtype="int16",
        blocksize=args.blocksize, device=device, callback=cb,
    )
    st.start()
    print(f"requested : rate={args.rate} channels={args.channels} blocksize={args.blocksize}")
    print(f"negotiated: samplerate={st.samplerate} blocksize={st.blocksize}")
    time.sleep(args.seconds)
    st.stop()
    st.close()

    cps = cb_count[0] / args.seconds
    fps = frame_count[0] / args.seconds
    expect_cps = args.rate / args.blocksize
    print(f"callbacks/sec = {cps:.1f}   (expect ~{expect_cps:.0f} at blocksize {args.blocksize})")
    print(f"frames/sec    = {fps:.1f}   (should be ~{args.rate})")
    if statuses:
        print(f"stream statuses seen: {statuses}")

    print()
    if fps > args.rate * 1.5:
        print(f"VERDICT: device delivering ~{fps/args.rate:.1f}x the requested rate — "
              "it's clocking faster than asked and PortAudio isn't resampling. "
              "Fix: open at the device's real rate and resample the input down.")
    elif cps > expect_cps * 1.5:
        print("VERDICT: frame rate correct but callbacks far more frequent than the "
              "requested block size — PortAudio isn't honoring blocksize. "
              "Fix: re-frame to fixed 30 ms chunks in the capture callback.")
    else:
        print("VERDICT: capture timing is nominal — overflow is likely a slow "
              "consumer (CPU/thermal), not the capture path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
