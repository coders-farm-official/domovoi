# Migrating a satellite from the 2-Mics HAT to the XVF3800 USB array

This is for an **already-provisioned, in-the-field** satellite that currently runs a ReSpeaker 2-Mics Pi HAT, and you want to swap it to a ReSpeaker XVF3800 USB 4-Mic Array. It's the same Pi, the same room, the same `room_id` — you're only changing the mic board and a bit of config.

**You do NOT re-flash the SD card.** Re-flashing is only for a brand-new Pi (the full `PROVISIONING.md` checklist). Migration is: swap hardware → update code → update config → install the LED tool → restart → test.

> Provisioning a fresh Pi instead? Use `PROVISIONING.md` → [Appendix: XVF3800 USB array](PROVISIONING.md#appendix-xvf3800-usb-4-mic-array). This migration doc reuses those same steps but frames them as "what changes from a working HAT."

What actually changes:

| | HAT (before) | XVF3800 USB (after) |
|---|---|---|
| Connection | GPIO header (40-pin) | USB |
| Speaker plugs into | the **HAT's** 3.5mm jack | the **array's** 3.5mm/JST jack (required for echo cancellation) |
| ALSA card | I2S codec on card 0 | a USB Audio Class card (index varies) |
| Mic gain / noise gate | software auto-tune + gate | on-chip 60 dB AGC + noise suppression (auto-tune disabled) |
| LEDs | 3× APA102 over SPI | 12× WS2812 via `xvf_host` over USB |
| Config `[device] profile` | `respeaker_2mic_hat` (or absent) | `xvf3800_usb` |

The Domovoi server side does **not** change at all — same `room_id`, so the room's MPD container, history, and favorites all carry over. This is purely satellite-local.

---

## 0. Before you start — have these on hand

- [ ] The ReSpeaker XVF3800 USB 4-Mic Array + its USB cable.
- [ ] The array connects over **USB-C** (power + host data on the one port).
- [ ] **If this Pi is a Pi Zero 2 W:** a **micro-USB-OTG → USB-A adapter** (then a USB-A→USB-C cable to the array). The Zero 2 W's data port is micro-USB **OTG** — it only becomes a USB *host* when the plug grounds the ID pin, which is exactly what an OTG adapter does.
  - ⚠️ A plain micro-USB↔USB-C cable will **not** work, even if it "supports power and data" — generic ones leave the ID pin floating, so the Pi stays in device mode and never sees the array. The cable/adapter must say **OTG**. (Alternative: force host mode with `dtoverlay=dwc2,dr_mode=host` in `/boot/firmware/config.txt`, then a plain data cable works. **Bonus:** the `dwc2` driver this loads also clocks the array's USB audio correctly, so going this route makes the `dwc_otg.speed=1` full-speed fix in the troubleshooting table *unnecessary* — whereas the OTG-adapter route stays on the `dwc_otg` driver and may still need it. The OTG adapter is simpler hardware; the overlay is one fewer gotcha.)
  - On a Pi 3/4/5 with full-size USB-A ports, just use a USB-A→USB-C cable — no OTG adapter needed.
- [ ] The powered speaker that's currently on the HAT (you'll move it).
- [ ] A solid power supply — **2.5 A minimum, 3 A ideal**. The array now draws its power over the same USB as the Pi's load; a weak supply that was fine for the bare HAT can brown out the Pi + array combo. (`vcgencmd get_throttled` should read `0x0` after; anything else = brownout history.) ⚠️ **An underpowered supply doesn't just brown out — it can leave the array's chip unable to boot, so it never appears in `lsusb` at all (empty bus).** That reads like a dead or disconnected array, not a power problem. The tell: the array's LED ring stays **off** when there's no power. On a Pi Zero 2 W the reliable combo is a ~3 A supply *and* powering the array through a **powered USB hub** rather than the Pi's lone port.
- [ ] SSH access to the Pi (`ssh <user>@domovoi-<room>.local`).
- [ ] 10 quiet minutes — this is a per-Pi manual swap.

Note the Pi's `room_id` / hostname before you touch anything; you'll reuse the exact same value (the config keeps it).

---

## 1. Push the new code first (before unplugging anything)

The device-profile code, the resampler, and the rewritten LED module all live under `satellite/`, so a normal rsync delivers them. Do this **while the HAT still works** so a botched rsync is obvious before you've changed hardware.

From the Domovoi server (in the repo root):

```bash
rsync -av --exclude __pycache__ \
    satellite/ domovoi-<room>.local:~/domovoi/satellite/
```

- [ ] Confirm the new files landed: `ssh domovoi-<room>.local "ls ~/domovoi/satellite/devices.py ~/domovoi/satellite/_resample.py"` — both should exist.
- [ ] Confirm `scipy` is installed in the venv (it's an existing dep, so almost certainly yes — but check on older images):
  ```bash
  ssh domovoi-<room>.local "~/satellite-venv/bin/python -c 'import scipy; print(scipy.__version__)'"
  ```
  If it errors: `ssh domovoi-<room>.local "~/satellite-venv/bin/pip install -r ~/domovoi/satellite/requirements.txt"`.

Don't restart the service yet — the config still points at the HAT, and the hardware is still the HAT. That's fine; the new code with no `[device]` block behaves exactly like the old code.

---

## 2. Physical swap

- [ ] **Stop the service** so nothing is holding the audio device while you unplug:
  ```bash
  ssh domovoi-<room>.local "sudo systemctl stop domovoi-satellite"
  ```
- [ ] **Power the Pi down** before touching the GPIO header (hot-removing a HAT can brown out or short):
  ```bash
  ssh domovoi-<room>.local "sudo shutdown -h now"
  ```
  Wait ~15 s for the green LED to stop blinking, then pull power.
- [ ] **Unplug the speaker's 3.5mm cable from the HAT.**
- [ ] **Lift the HAT off the 40-pin GPIO header.** Pull straight up, even pressure. Set it aside (keep it — it's your rollback).
- [ ] Leave the GPIO header on the Pi (harmless; the array doesn't use it).
- [ ] **Connect the array:**
  - Pi Zero 2 W: micro-USB OTG adapter into the Pi's **data** USB port (the inner one, labelled `USB` — *not* `PWR`) → the array's USB cable into the adapter.
  - Pi 3/4/5: array's USB cable straight into a USB-A port.
- [ ] **Plug the powered speaker into the ARRAY's own 3.5mm jack** (or its JST speaker connector). **This is mandatory** — the XVF3800's echo canceller uses what it plays out that jack as its reference. If the speaker is anywhere else (the Pi, an old HAT), barge-in will false-trigger on the bot's own voice.
- [ ] Re-connect the Pi's power supply (the `PWR` micro-USB port on a Zero 2 W). The Pi boots; the service is still `stop`ped from earlier, which is what we want.

---

## 3. Identify the array's audio device

SSH back in once it's booted (`ssh <user>@domovoi-<room>.local`).

- [ ] Confirm the array enumerated:
  ```bash
  aplay -l        # playback devices
  arecord -l      # capture devices
  arecord -L      # full device names, incl. the CARD= name you need below
  ```
  You should see a `USB Audio` / XMOS device. **Note its card number** (likely *not* 0) and its `CARD=<name>` from `arecord -L`.
- [ ] Get the sounddevice indices the client uses:
  ```bash
  ~/satellite-venv/bin/python -m satellite.client --list-devices
  ```
  Note the **index** of the array for both input and output.
- [ ] Quick standalone capture/playback check (the shipping firmware gives **S16_LE, 2-channel, 16 kHz**; ch1 is the ASR beam). Confirm the format first if unsure: `arecord -D hw:CARD=<name>,DEV=0 --dump-hw-params -d 1 /dev/null` and read `FORMAT`/`CHANNELS`/`RATE`.
  ```bash
  arecord -D plughw:CARD=<name>,DEV=0 -f S16_LE -r 16000 -c 2 -d 5 /tmp/t.wav
  aplay  -D plughw:CARD=<name>,DEV=0 /tmp/t.wav
  ```
  (If `--dump-hw-params` reports a different `FORMAT` — e.g. `S32_LE` — the `xvf3800_usb` profile's `capture_dtype` must match it; the profile defaults to `int16`.)
  Speak during the recording; you should hear it back out the array's speaker. The array does its own gain — you should **not** need `alsamixer`. If it's silent, recheck the speaker is on the array and powered.

---

## 4. Install the `xvf_host` LED/control tool

The 12-LED ring is driven through Seeed's `xvf_host` CLI. Skip this and the satellite still runs — the ring just stays dark and logs a warning.

`xvf_host` is **not** a single file — it loads `libcommand_map.so` (and other companion files) from its *own* directory, so install the whole `rpi_64bit/` folder, not just the binary (a lone binary fails with `libcommand_map.so: cannot open shared object file`).

- [ ] Install the folder + its libusb dependency:
  ```bash
  sudo apt install -y libusb-1.0-0
  git clone https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY.git ~/xvf3800-src
  sudo cp -r ~/xvf3800-src/host_control/rpi_64bit /opt/xvf3800
  sudo chmod +x /opt/xvf3800/xvf_host
  ```
- [ ] Test the ring directly (no missing-lib error):
  ```bash
  sudo /opt/xvf3800/xvf_host led_effect 3 && sudo /opt/xvf3800/xvf_host led_color 0x00ff50   # solid green
  sudo /opt/xvf3800/xvf_host led_effect 0                                                     # off
  ```
- [ ] Add a passwordless sudoers entry so the client can drive the ring without a prompt (mirrors the wifi entry, least-privilege — one binary):
  ```bash
  sudo visudo -f /etc/sudoers.d/satellite-xvf
  # add, replacing <username> with the service user (the User= in the systemd unit):
  <username> ALL=(root) NOPASSWD: /opt/xvf3800/xvf_host
  ```
- [ ] Verify no password prompt (`sudo -n /opt/xvf3800/xvf_host led_brightness 64` runs silently), then point the client at it in `~/.domovoi/config.toml`:
  ```toml
  [leds]
  xvf_host_path = "/opt/xvf3800/xvf_host"
  ```

---

## 5. Update the config

Edit `~/.domovoi/config.toml`:

```bash
nano ~/.domovoi/config.toml
```

- [ ] Add (or change) the device profile **at the top**:
  ```toml
  [device]
  profile = "xvf3800_usb"
  ```
- [ ] Pin the audio devices to the array. Use a **device-name substring**, not an index — it's reboot-proof (card numbers can shuffle, especially with the old HAT overlay still loaded):
  ```toml
  [audio]
  input_device = "reSpeaker XVF3800"
  output_device = "reSpeaker XVF3800"   # MUST point at the array (AEC reference)
  ```
- [ ] Pin the music ALSA device to the array by name (don't rely on the `"default"` fallback):
  ```toml
  [music]
  alsa_device = "plughw:CARD=Array,DEV=0"
  ```
- [ ] **Remove or comment out any HAT-specific overrides** you'd set previously, so the XVF profile's defaults take over. In particular, if your config has explicit `[mic_gain] enabled = true`, `[noise_gate] auto_calibrate = true / dbfs = -45`, `[barge_in] vad_aggressiveness_during_tts = 3`, or `[leds] num_leds = 3 / brightness = 16`, comment them out. (If you never set them — they were profile defaults — there's nothing to remove.)
  - ⚠️ **Watch for `[barge_in] require_wake_word = true`** specifically — a common HAT-era workaround (the HAT's speaker leaks into the mic and false-triggers barge-in). The XVF3800's on-chip AEC removes that echo, so the profile default is `false`. Leaving it `true` silently disables the XVF's marquee feature — interrupting Domovoi by **just talking over him** — forcing you to say "hey jarvis stop" instead. Comment it out unless you specifically want wake-word-gated barge-in.
- [ ] **Plan on a playback make-up gain.** The array's 3.5mm output is **line-level**, and TTS is normalized well below full scale, so Domovoi's voice (and the wake-word greeting clips) can sound *faint even with `PCM` maxed* — the HAT's codec drove the speaker much hotter, so this is new on the XVF. Set a software make-up gain (start around `3.0`):
  ```toml
  [playback]
  gain = 3.0
  ```
  This boosts the spoken voice **and** the locally-played greeting/canned clips together (it's applied to the streamed TTS PCM and, via `mpg123 --scale`, to the clips — music is already hot and is left untouched). Try `2.0`–`4.0`; ~`5.0` is roughly the clip ceiling before peaks distort. You can also set this later in §7 once you hear how loud it actually is.
- [ ] **Mono output — use a mono→stereo adapter for a stereo speaker pair.** The array's 3.5 mm jack is **single-channel by design** (a mono voice pipeline: `speaker-test -c2` plays only *Front Left*, and no `xvf_host AUDIO_MGR_OP_R` reroute lights the right channel — the right DAC channel isn't wired to the jack). A full AV receiver hides this (it fills all its speakers from the one input), but a **stereo powered-pair** (one active speaker + a passive one daisy-chained off it) leaves the **second speaker silent**. Fix: a cheap **3.5 mm mono→stereo adapter** (mono *male* → stereo *female*) at the speaker input copies the one live channel to both — verified working in-field, and it evens out perceived loudness/balance too. **Standing rule: any satellite feeding a stereo-split powered-pair gets this adapter by default.**

You do **not** set mic-gain/noise-gate/barge-in tuning by hand — selecting `xvf3800_usb` auto-disables the ALSA mic-gain tune and the software noise-gate auto-calibration (the chip does both) and keeps barge-in on normal VAD.

---

## 6. Start it and watch the boot log

```bash
sudo systemctl start domovoi-satellite
journalctl -u domovoi-satellite -f
```

Expected new log lines:

- [ ] `device profile: xvf3800_usb (ReSpeaker XVF3800 USB 4-Mic Array ...)`
- [ ] `mic capture: int16 2ch, selecting channel 1 (ASR beam) → mono int16`
- [ ] `xvf_host LED control via: ...` (or `sudo -n ...`)
- [ ] `opening output stream at 16000 Hz (src=... resampling)` the first time it speaks
- [ ] the usual `server ready: ...` and `listening for wake word ...`

What you will **no longer** see (this is expected, not a failure): the `mic-gain auto-tune` and `noise gate calibrated` lines — those are HAT-only and the profile disables them.

If you see a warning like `device profile 'xvf3800_usb' expects playback through the array ... output_device is unset` → you missed pinning `output_device` in step 5. Fix it and restart.

---

## 7. Verify the migration end-to-end

- [ ] **LEDs track state** — idle (off) → say the wake word → **listening**: a green dot that points toward your voice over a dim blue ring (the chip's direction-of-arrival mode) → **thinking**: a slow yellow pulse → **speaking**: a slow blue pulse. While **music** plays the ring shows a rotating rainbow. If the ring stays dark, re-check step 4 (xvf_host + sudoers). (The dot only tracks during *listening* — thinking/speaking use a whole-ring pulse because the chip's DoA needs live mic input, which AEC suppresses while the bot talks.)
- [ ] **Wake + respond** — "Hey Jarvis, what time is it?" → it captures (listening dot tracks you), thinks (yellow pulse), and answers out the array's speaker (blue pulse).
- [ ] **Capture quality / channel** — ask something with a specific answer and confirm the transcript in the log is accurate. A garbled transcript can mean the wrong channel was selected; if so, that's a one-line profile change (`capture_select_channel`) — but channel 1 is correct for the standard firmware.
- [ ] **Barge-in (the big XVF win)** — start a long response ("tell me a joke"), then talk over it. With on-chip AEC it should interrupt cleanly and **not** false-trigger on the bot's own voice. If it interrupts itself with no one speaking, the speaker isn't on the array (step 2) so AEC has no reference.
- [ ] **Music** — "Hey Jarvis, play <something>." Confirm it plays out the array's speaker. If TTS works but music is silent or comes out the wrong device, `[music] alsa_device` is wrong (step 5) — this is the single most common miss.
- [ ] **Volume** — "Hey Jarvis, set the volume to 30," then "turn it up." Both Domovoi's voice *and* music should change together (the command drives the array's `PCM` control, which both pass through). `amixer -c Array` should reflect the level, and the Domovoi server log shows a `volume_status` line. If nothing changes, confirm `[audio] output_mixer_control = "PCM"` (the xvf profile default) and that `amixer -c Array scontrols` lists `PCM`.
- [ ] **Voice loudness** — if Domovoi's voice and the greeting clips still sound faint with the volume turned up (music is fine), that's the line-level jack, not a fault: set `[playback] gain` (§5) and restart. The fix is software make-up gain, not the `PCM` mixer.
- [ ] **No brownout** — `vcgencmd get_throttled` should be `0x0`. A non-zero value means the supply can't feed the Pi + array; use a stronger PSU (or a powered USB hub for the array).
- [ ] **Survives reboot** — `sudo reboot`, then after it comes back, confirm the array still enumerates at the expected index and the satellite reconnects. USB enumeration order is usually stable per-Pi, but verify once. If the index shifts, prefer the name-based `plughw:CARD=<name>,DEV=0` form for `[music]` and re-check the sounddevice indices.

If the wake word feels less reliable than on the HAT: the XVF3800's beamforming/AGC reshapes the captured audio, so a wake-word model trained on HAT clips may not match it. See `scripts/wake_word/README.md` — re-record clips on the array and retrain for best accuracy. Lowering `[wake] threshold` to ~0.4 is a quick stopgap.

---

## 8. Label and finish

- [ ] Update the physical label on the Pi: "XVF3800 USB" instead of "HAT V1/V2.0".
- [ ] Update your `satellites.txt` (or wherever you track them) with the board change and the array's card name.

---

## Rollback to the HAT

If anything's wrong and you want to revert (the HAT's provisioning — dtoverlay, SPI, card-0 pinning — was never removed, so it just works again):

1. `sudo systemctl stop domovoi-satellite`
2. `sudo shutdown -h now`, pull power.
3. Unplug the array (and its speaker). Reseat the HAT on the GPIO header. Move the speaker cable back to the **HAT's** 3.5mm jack.
4. In `~/.domovoi/config.toml`: set `[device] profile = "respeaker_2mic_hat"` (or delete the `[device]` block entirely), and remove the `[audio]` / `[music]` array pins from step 5.
5. Power on, `sudo systemctl start domovoi-satellite`. You're back to the original setup.

---

## Troubleshooting quick table

| Symptom | Likely cause | Fix |
|---|---|---|
| **Array never appears in `lsusb` at all** (only the root hub), no `CARD=Array` in `arecord -L` | **In priority order:** (1) underpowered supply — the chip can't boot, so it never enumerates (array LEDs *off* = no power); (2) a **charge-only USB cable** (very common — looks identical to a data cable); (3) wrong port (`PWR` instead of the data `USB` port); (4) not in USB host mode. | Use a ≥3 A supply / a powered hub feeding the array — its LEDs should light = powered. Swap to a known-good **data** cable; isolate it by plugging a USB flash drive into the same cable/port — if *that* doesn't show in `lsusb` either, the cable/port is the dead link. Force host mode with `dtoverlay=dwc2,dr_mode=host`. |
| `mic queue overflowing … frames dropped` **constantly**, wake word never fires, transcripts chipmunk-fast | Pi `dwc_otg` mis-clocks high-speed USB audio — the array delivers ~**8× the sample rate** (`mic_probe.py` shows `frames/sec ≈ 128000`). **Only on the `dwc_otg` driver** (OTG-adapter route). | Add `dwc_otg.speed=1` to the **single line** of `/boot/firmware/cmdline.txt` (forces full-speed USB; harmless on the Zero 2 W since WiFi is SDIO, not USB), then reboot; `mic_probe.py` should then read `frames/sec ≈ 16000`. **If you forced host mode with `dtoverlay=dwc2,dr_mode=host`, you're on the `dwc2` driver, which clocks audio correctly — this param doesn't apply and isn't needed.** |
| `mic queue overflowing` **occasionally** (a few frames / 30 s) but `mic_probe.py` reads `frames/sec ≈ 16000` | Capture timing is fine — the *consumer* is slow: the Zero 2 W's CPU falling behind on wake-word inference (thermal throttling, swap, another CPU hog). Not USB. | Benign at a few frames — wake word still fires. If frequent / you miss detections: check `vcgencmd measure_temp` + `get_throttled`, shed other CPU load, ensure swap isn't thrashing. |
| `LED ring dark`, `libcommand_map.so: cannot open shared object file` | Installed the lone `xvf_host` binary; it needs its companion `.so` in the same dir | Install the whole `rpi_64bit/` folder (step 4) and point `[leds] xvf_host_path` at it |
| `device profile 'xvf3800_usb' expects playback through the array ... output_device is unset` | `[audio] output_device` not pinned | Set it to the array (step 5) |
| Barge-in interrupts the bot with no one talking | Speaker not on the array → AEC has no reference | Move speaker to the array's jack (step 2) |
| TTS works, music silent / wrong speaker | `[music] alsa_device` wrong | Pin `plughw:CARD=<name>,DEV=0` (step 5) |
| Domovoi's voice + greeting clips faint even at max volume (music is fine) | XVF3800's 3.5mm out is line-level; TTS is normalized low | Set `[playback] gain` (start ~`3.0`, range `2.0`–`4.0`) — step 5 |
| LED ring stays dark | `xvf_host` missing or sudoers prompt | Install binary + sudoers entry (step 4); check the `xvf_host` log line |
| Garbled transcripts | Wrong capture channel | Confirm `capture_select_channel = 1` (default); re-run the §3 capture test |
| Random reboots / `get_throttled` ≠ 0x0 | Underpowered PSU for Pi + array | Stronger supply, or powered USB hub for the array |
| Wake word less reliable than on HAT | Model trained on HAT clips ≠ XVF signal | Re-record/retrain (`scripts/wake_word/README.md`); stopgap: lower `[wake] threshold` |
| No `mic-gain` / `noise gate` log lines | Expected — XVF profile disables them | Not a problem |
