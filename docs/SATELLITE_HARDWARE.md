# Building a Domovoi Satellite

A satellite is the small Raspberry Pi box that gives Domovoi ears and a
voice in one room: it listens for the wake word on-device, streams your
speech to the server, plays the answer, shows state on its LEDs, and pipes
that room's music. This guide takes you from a shopping cart to a working
satellite, then covers living with a fleet of them.

The **canonical step-by-step checklist** (every command, every gotcha,
kept battle-tested) is [`satellite/PROVISIONING.md`](../satellite/PROVISIONING.md).
This page is the friendly tour of the same journey — when the two ever
disagree, PROVISIONING.md wins.

Related: [ARCHITECTURE.md](ARCHITECTURE.md) for how satellites talk to the
core, [TROUBLESHOOTING.md](TROUBLESHOOTING.md) when something squeaks,
[SECURITY_PRIVACY.md](SECURITY_PRIVACY.md) for what a satellite is trusted
to do, and the [GLOSSARY.md](GLOSSARY.md) for terms like *barge-in* and
*drop-in*.

---

## Shopping list

Per room:

| Item | Notes |
|---|---|
| **Raspberry Pi Zero 2 W** (the workhorse) or a Pi 3/4/5 | Zero 2 W is cheap, silent, and plenty — 512 MB RAM is tight but proven. A Pi 4 gives headroom and full-size USB ports (nice with the XVF3800). For the HAT, a Zero 2 W needs a **soldered 40-pin GPIO header** — buy the "2 WH" variant or bring a soldering iron. |
| **A mic board** — one of the two below | See the tradeoff table. |
| **microSD card**, 16 GB+, Class 10 / A1 | Plus a reader for your laptop. |
| **Power supply**, 2.5 A minimum (5 V/3 A ideal) | The #1 cause of mystery flakiness is a weak charger — the Zero 2 W browns out under Wi-Fi + audio load. |
| **Powered speaker + 3.5 mm aux cable** | Any powered speaker works. |
| *(XVF3800 on a Pi Zero 2 W only)* **micro-USB OTG adapter** | The Zero 2 W's data port needs a true OTG adapter to act as USB host — a plain micro-USB↔USB-C cable will *not* work. Pi 3/4/5 owners just use a USB-A→USB-C cable. |

### The two supported mic boards

| | **ReSpeaker 2-Mics Pi HAT** (`respeaker_2mic_hat`, the default) | **ReSpeaker XVF3800 USB 4-Mic Array** (`xvf3800_usb`) |
|---|---|---|
| Connection | GPIO (seats on the header) | USB — plug and play, no overlay, no soldering |
| Setup effort | More: device-tree overlay, card pinning, mixer tuning | Less: enumerates as a USB sound card |
| Echo cancellation (AEC) | **None** | **On-chip**, plus 60 dB AGC, beamforming, noise suppression, direction-of-arrival |
| What AEC unlocks | — | The **spoken wake greeting** (Domovoi acknowledges while still listening) and **chat mode** (multi-turn conversation with an open mic) — both refuse to run on a HAT because the mic would hear the speaker |
| Barge-in | Works, but echo-sensitive (tunable; can be restricted to wake-word-only) | Reliable plain-VAD barge-in |
| LEDs | 3× APA102 (solid state colors) | 12× WS2812 ring (DoA dot points at your voice, animated states) |
| Speaker plugs into | the HAT's 3.5 mm jack | **the array's own jack** (mandatory — it's the AEC echo reference); output is mono line-level, so budget a mono→stereo adapter for stereo speaker pairs and expect to set a software make-up gain |
| Landmines | **V1 vs V2.0** — two near-identical products with different codecs; identify yours *before* starting | Needs Seeed's `xvf_host` tool for the LEDs; OTG adapter on a Zero 2 W |
| Verdict | Cheapest working satellite | The nicer experience — get this one if the budget allows |

Buy one full kit first, get it working end-to-end, then provision the rest.

## Step 1 — Flash the SD card

Use Raspberry Pi Imager with **Raspberry Pi OS Lite (64-bit)** (hidden
under "Raspberry Pi OS (other)"). In the pre-configure gear:

- Hostname: **`domovoi-<room>`** (e.g. `domovoi-kitchen`) — this becomes
  the mDNS name and matches the room naming everywhere else.
- A username (not `pi`), a password, your Wi-Fi credentials, your locale.
- **Enable SSH.**

Boot it, wait a few minutes, then `ssh <username>@domovoi-<room>.local`.

## Step 2 — Base packages

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git python3-pip python3-venv portaudio19-dev \
  libasound2-dev alsa-utils device-tree-compiler mpg123
sudo reboot
```

`mpg123` matters more than it looks: the satellite spawns it to play the
server's music stream. Without it, music "plays" server-side and the room
stays silent.

## Step 3 — Make the mic board work

This is the board-specific part, and PROVISIONING.md is the authority:

- **2-Mics HAT:** identify V1 vs V2.0 (**step 0** — the single biggest
  landmine, they use different codecs), build and install the matching
  device-tree overlay from `Seeed-Studio/seeed-linux-dtoverlays`, pin the
  HAT to ALSA card 0 by disabling HDMI audio, enable SPI for the LEDs
  (`sudo raspi-config nonint do_spi 0`), then smoke-test with
  `arecord`/`aplay` and crank the shy default mixer levels
  (`alsamixer -c 0`, then `sudo alsactl store`). Full detail:
  [PROVISIONING.md §0–§5](../satellite/PROVISIONING.md).
- **XVF3800 USB:** plug it in, confirm it shows up in `aplay -l` /
  `arecord -l`, plug the **speaker into the array's own jack**, install
  the `xvf_host` LED tool to `/opt/xvf3800`, and on a Zero 2 W check the
  USB full-speed note. Full detail:
  [PROVISIONING.md — Appendix: XVF3800](../satellite/PROVISIONING.md#appendix-xvf3800-usb-4-mic-array).

Don't skip the smoke test. If `arecord`/`aplay` don't work standalone, the
satellite client can't fix it.

## Step 4 — Install the satellite client

```bash
python3 -m venv ~/satellite-venv
source ~/satellite-venv/bin/activate
pip install --upgrade pip wheel

cd ~
git clone https://github.com/coders-farm-official/domovoi.git domovoi
cd domovoi

pip install -r satellite/requirements.txt
pip install --no-deps "openwakeword>=0.6.0"
python -c "import openwakeword.utils; openwakeword.utils.download_models()"
```

Only the `satellite/` package runs on the Pi — the Domovoi core never
does. The `--no-deps` line and the model download are both load-bearing;
[PROVISIONING.md §6.3](../satellite/PROVISIONING.md) explains why (a Python
3.13 wheel gap and a wheel that ships without its models). The
`onnxruntime==1.20.0` pin in `requirements.txt` is also deliberate: 1.20.0 is
the only release that is *both* available as a Python 3.13 wheel (cp313 wheels
start at 1.20) *and* runs on the Zero 2 W's Cortex-A53 — 1.21+ abort with a
`vector::operator[]` assertion from their manylinux (gcc-toolset-14) build.

## Step 5 — Configure

```bash
python -m satellite.client
# → Wrote example config to ~/.domovoi/config.toml. Edit it ... then re-run.
```

Minimum edits in `~/.domovoi/config.toml`:

```toml
[device]
profile = "respeaker_2mic_hat"      # or "xvf3800_usb"

[satellite]
room_id = "kitchen"                 # match the hostname suffix
domovoi_url = "ws://<server-ip-or-hostname>:6370"

[wake]
wake_word = "hey_jarvis"
threshold = 0.5
```

Verify your audio devices with `python -m satellite.client --list-devices`
(the client also accepts `--config`, `--input-device`, and
`--output-device` overrides). On the XVF3800 you **must** pin
`input_device`/`output_device` (and `[music] alsa_device`) to the array —
a name substring like `"reSpeaker XVF3800"` is reboot-proof where card
numbers aren't.

### First run

```bash
python -m satellite.client
```

Three log lines mean you're done:

```
connecting to ws://<server>:6370/v1/stream/<room>
server ready: protocol=0.1 bot=Domovoi
listening for wake word 'hey_jarvis' in room '<room>'
```

If the WebSocket won't connect, it's almost always the server machine's
firewall — allow port 6370 inbound from your LAN subnet.

## Step 6 — Make it an appliance

Three finishing moves, all from PROVISIONING.md:

1. **systemd unit** ([§8](../satellite/PROVISIONING.md)) — installs
   `domovoi-satellite.service` so the satellite starts at boot and
   restarts on failure. After this, recovery from any weirdness is
   "unplug it and plug it back in."
2. **Two sudoers entries** — least-privilege single-command grants:
   - *Wi-Fi self-heal* ([§6.7](../satellite/PROVISIONING.md)): lets the
     client run exactly `wpa_cli -i wlan0 reassociate` to un-wedge a
     rate-stuck Wi-Fi link on its own.
   - *Self-restart* ([§8.1](../satellite/PROVISIONING.md)): lets the
     client run exactly `systemctl --no-block restart
     domovoi-satellite.service`, which is how dashboard config pushes,
     the **Restart** button, and self-upgrades apply themselves.
   - *(XVF3800 only)* a third entry for the `xvf_host` LED binary
     ([Appendix §E](../satellite/PROVISIONING.md)).
3. **DHCP reservation** ([§7](../satellite/PROVISIONING.md)) and a
   physical label (hostname, room, IP, board + HAT version). Future you
   says thanks.

Everyday operation is plain systemd:

```bash
sudo systemctl status domovoi-satellite
sudo systemctl restart domovoi-satellite
journalctl -u domovoi-satellite -f
```

## Anatomy of `config.toml`

The example file (`satellite/config.toml.example`) is heavily commented —
it's the real reference. The map:

| Section | What it controls |
|---|---|
| `[device]` | The mic-board **profile** (`respeaker_2mic_hat` / `xvf3800_usb`). The profile sets sane per-board defaults for audio, gain, noise gate, barge-in, LEDs, and music below; anything you set explicitly still wins. |
| `[satellite]` | `room_id` and the server WebSocket URL (`ws://…:6370`). |
| `[wake]` | Wake word + detection threshold. Overridden at runtime by the `~/.domovoi/wake` sidecar once you push a custom model (below). |
| `[greeting]` | The instant spoken acknowledgment when the wake word fires. Needs AEC — set `enabled = false` on a HAT. |
| `[chat]` | Documentation-only: chat mode is entirely server-gated, and the satellite refuses it on a non-AEC board. |
| `[sounds]` | Auto-sync of rendered sound clips (greetings, the offline apology) from the server. |
| `[voice]` | Which registered server voice this room speaks in — usually you change this by voice ("switch to Ryan") and the choice persists in the `~/.domovoi/voice` sidecar. |
| `[audio]` | Input/output device pinning and the hardware volume mixer control that voice volume commands drive. |
| `[listen]`, `[barge_in]`, `[noise_gate]`, `[mic_gain]` | The capture pipeline: VAD strictness, silence endpointing, interrupt-the-bot behavior, ambient noise gating with auto-calibration, and boot-time hardware mic-gain tuning. Profile defaults are good; the comments explain every knob if a room needs tuning. |
| `[playback]`, `[music]` | TTS pre-buffering, TTS make-up gain (important on the XVF3800's quiet line-level jack), and the ALSA device + priming handshake for music streaming. |
| `[leds]`, `[wifi]`, `[log]` | LED backend/brightness, the Wi-Fi self-heal watchdog, log level. |

Most settings can also be edited from the dashboard: **Satellites → (your
room) → Settings**. Saving there rewrites the Pi's `config.toml` in place —
preserving your comments, keeping a `.bak` — and restarts the satellite to
apply (that's what the self-restart sudoers entry is for).

## Custom wake words

`hey_jarvis` is the out-of-the-box default; **"Hey Domovoi" is the
documented destination**, and you train it in-product:

1. Dashboard → **Settings → Wake Words**: record positive clips — the
   recording happens **on the satellite itself**, through the actual mic
   board and room acoustics it will listen with.
2. Train on the server (clips land in `~/.domovoi/wake_clips/`, models in
   `~/.domovoi/wake_models/`). See `scripts/wake_word/README.md` for the
   training pipeline details.
3. **Push to room.** The push writes the model slug to the Pi's
   `~/.domovoi/wake` sidecar, which overrides `[wake] wake_word` at
   runtime and survives restarts. Delete the sidecar to revert.

**Model sync is automatic and verified.** Satellites mirror the server's
wake-model set over HTTP (`/v1/wake-models/manifest` +
`/v1/wake-models/<file>`) into `~/.domovoi/wake_models/` on the Pi,
checking each downloaded model's sha256 against the manifest before it's
trusted. You never scp `.onnx` files around.

## Self-upgrade

You also never scp *code* around. From the dashboard's Satellites page (or
`POST /v1/admin/satellite/upgrade` — admin-gated, since it makes a Pi
execute freshly-synced code; see
[SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#what-the-admin-password-actually-gates)),
a connected satellite:

1. **Backs itself up** — tarballs its current `satellite/` tree.
2. **Mirrors the server's code** via the `/v1/satellite-code` manifest,
   verifying every file's sha256 before writing it.
3. Records the new synced version in a sidecar and **restarts itself**
   (the §8.1 sudoers entry again).
4. **Rolls back automatically** — if the upgraded client doesn't reconnect
   to the server within the deadline, a watchdog restores the tarball and
   restarts on the previous code. A botched push costs you a minute, not a
   ladder-and-SD-card trip.

The dashboard flags satellites whose synced version trails the server, so
"upgrade the fleet" is a few clicks.

## Switching mic boards later

Already running a HAT satellite and upgrading it to the XVF3800? **Don't
re-flash.** There's a dedicated in-place migration guide —
[`satellite/MIGRATION_HAT_TO_XVF3800.md`](../satellite/MIGRATION_HAT_TO_XVF3800.md) —
covering which cables move where, the exact config changes, the OTG cable
trap on the Zero 2 W, and what to test after. Same Pi, same `room_id`,
same room history; the server side doesn't change at all.

## Multi-room tips

- **Name rooms once, well.** `room_id` is the identity everywhere: the
  WebSocket path, intent logs, music queues, and how intercom addresses a
  room ("tell the kitchen…"). Match it to the hostname suffix and keep it
  boring (`kitchen`, not `kitchen-new-2`).
- **Music is per-room by design.** The server lazily provisions one MPD
  instance per room (control ports 6650+N, stream ports 8050+N) the first
  time a room plays something — no setup on your part. Different rooms,
  different queues, simultaneously.
- **Intercom and drop-in** turn the fleet into a house-wide comm system:
  announce to one room or all of them, or open a two-way drop-in between
  rooms. How a target room answers a drop-in (mic opens immediately vs.
  asks first) is a server-side setting.
- **Volume is a voice command.** "Set the volume to 50" / "turn it up"
  drives the room's single hardware mixer control (configured in
  `[audio]`), so voice, music, and greetings all move together, and the
  dashboard shows each room's current level.
- **Voices are per-room too.** Give each room its own TTS voice ("switch
  to Ryan") — the choice sticks to that satellite.
- **First two rooms:** provision #1 fully, live with it for a few days,
  *then* batch the rest. Every acoustic lesson from room #1 (gain, noise
  gate, barge-in sensitivity) makes rooms #2–#N faster.
- **Label everything.** Hostname, room, IP, board type on the case. A
  `satellites.txt` in your notes. This is the cheapest observability you
  will ever buy.

---

*Something not behaving? [TROUBLESHOOTING.md](TROUBLESHOOTING.md) has the
satellite section; the "Known landmines" list at the bottom of
[PROVISIONING.md](../satellite/PROVISIONING.md) covers the hardware
classics.*
