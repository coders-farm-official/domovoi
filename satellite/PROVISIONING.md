# Pi Satellite Provisioning Checklist

> **Zero-touch path first (USB adoption).** If your SD card was prepared
> with the Domovoi satellite payload (dashboard → Satellites → prepare
> media, or an image that boots into provisioning mode), you don't need
> most of this checklist: **boot the device, plug it into the Domovoi
> server's USB port**, and an "adopt" card appears on the dashboard's
> Satellites page. Name it, enter Wi-Fi, done — the device configures
> itself, pre-paired, and reboots onto your network. The device presents
> as a `DOMOVOI-SET` flash drive while waiting (see
> `satellite/provisioning_mode.py`; requires `mtools`/`dosfstools` on the
> image and the USB-gadget boot config the media-prep pipeline writes).
> A wrong Wi-Fi password re-presents the drive with the error shown on
> the dashboard — re-adopt with corrected credentials. Everything below
> is the MANUAL fallback path, still fully supported.

One-time setup per Pi Zero 2 W + ReSpeaker 2-Mics Pi HAT. Goal: a Pi that can capture audio from the HAT mics, play audio out the HAT's 3.5mm jack to powered speakers, and is ready to run the satellite client.

Do this once for **Pi #1** and confirm everything works end-to-end before provisioning the rest.

> **Two supported mic boards.** This main checklist is for the **ReSpeaker 2-Mics Pi HAT** (`[device] profile = "respeaker_2mic_hat"`, the default). For the **ReSpeaker XVF3800 USB 4-Mic Array** (`profile = "xvf3800_usb"`), follow **[Appendix: XVF3800 USB array](#appendix-xvf3800-usb-4-mic-array)** for the hardware/driver/LED/audio deltas, then come back for the shared steps (§1 flash, §6 Python env, §7 networking, §8 systemd) — those are board-agnostic. The XVF3800 is USB plug-and-play: no GPIO header, no dtoverlay, no SPI.

---

## 0. Identify your HAT version BEFORE you start

**This is the single biggest landmine.** Seeed sells two products with nearly identical names but different hardware:

- **V1 ("Pro")** — uses **WM8960** codec at I2C 0x1a
- **V2.0** — uses **TLV320AIC3104** codec at I2C 0x18

They are **not interchangeable**. Different driver, different overlay, different I2C address. Seeed's main wiki page lists "WM8960" globally and doesn't clarify, so the version you have is the source of truth.

- [ ] Find the version on the **box** ("V2.0" printed prominently) or the **silkscreen on the board**
- [ ] Note it here for later: V____

Hardware you should have on the desk:

- [ ] Pi Zero 2 W
- [ ] ReSpeaker 2-Mics Pi HAT (V1 or V2.0 — note above)
- [ ] **40-pin GPIO header soldered to the Pi** — Pi Zero 2 W ships without one. The HAT will not seat without it. If yours doesn't have headers, solder before you start (or buy a pre-soldered "Pi Zero 2 WH" variant).
- [ ] microSD card, 16 GB+, Class 10 / A1
- [ ] microSD reader for your laptop
- [ ] Micro-USB power supply, **2.5 A minimum** (Pi Zero 2 W brownouts on weak supplies — 5V / 3A is ideal)
- [ ] 3.5mm aux cable + powered speaker (for first 2 Pis)
- [ ] Laptop on the same WiFi network for SSH

---

## 1. Flash the SD card

Use **Raspberry Pi Imager** (download from raspberrypi.com/software).

- [ ] OS: **Raspberry Pi OS Lite (64-bit)**. As of 2026 the default is **Trixie** (Debian 13); Bookworm also works. No desktop — this is a headless appliance.
- [ ] Imager UI gotcha: "Lite" is hidden under **"Choose OS" → "Raspberry Pi OS (other)"**, not the top-level entry.
- [ ] Click the gear icon to pre-configure (saves a lot of pain):
  - [ ] Hostname: `domovoi-<room>` — e.g. `domovoi-livingroom`, `domovoi-kitchen`. This becomes the mDNS name (`.local`).
  - [ ] Username: pick something memorable, **not** `pi` (the default-pi username is gone in modern Pi OS anyway)
  - [ ] Password: set one
  - [ ] WiFi: SSID + password + country
  - [ ] Locale: your timezone + keyboard
  - [ ] **Enable SSH**, password auth is fine on LAN-trust
- [ ] Write to the SD card.
- [ ] Eject.

## 2. First boot + SSH in

- [ ] Seat the ReSpeaker HAT on the GPIO header. Press evenly on all four corners — a partially-seated HAT silently fails at the I2C-probe step.
- [ ] Insert SD card, plug in power.
- [ ] Wait ~2–3 min for first boot (it expands the filesystem and connects to WiFi).
- [ ] From laptop: `ssh <username>@domovoi-<room>.local`
- [ ] If `.local` doesn't resolve from Windows: install Bonjour (Apple's mDNS), or check your router's client list for the IP and SSH to that.

## 3. System update + base packages

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y \
  git \
  python3-pip \
  python3-venv \
  portaudio19-dev \
  libasound2-dev \
  alsa-utils \
  device-tree-compiler \
  mpg123
sudo reboot
```

- [ ] After reboot, SSH back in.

No DKMS / kernel headers needed — the audio driver is now a device-tree overlay only. The kernel already includes the WM8960 and TLV320AIC3x codec modules.

**Why each non-obvious package:**

- `portaudio19-dev` + `libasound2-dev` — headers for `sounddevice`/PortAudio (mic capture, TTS playback).
- `alsa-utils` — `arecord`/`aplay`/`alsamixer` for the smoke test in step 5.
- `device-tree-compiler` — needed to build the ReSpeaker dtoverlay in step 4.
- **`mpg123`** — MP3 stream consumer. The satellite client spawns `mpg123` as a subprocess to decode and play MPD's HTTP audio stream from the Domovoi server. Without it, "Hey Jarvis, play creep by radiohead" succeeds end-to-end on the server side but produces no sound on the Pi.

## 4. ReSpeaker driver — device-tree overlay

The kernel ships with both codec drivers built in. We just need to compile and install the right overlay to wire one of them up to the right pins on the Pi.

**Do NOT use the old `respeaker/seeed-voicecard` repo or HinTak's fork** — those are DKMS kernel modules for the V1, broken on modern kernels, and superseded by the dtoverlay approach below. Even for a V1 HAT, the dtoverlay path is preferred.

Clone Seeed's overlay repo:

```bash
cd ~
git clone https://github.com/Seeed-Studio/seeed-linux-dtoverlays.git
cd seeed-linux-dtoverlays
```

**Pick the overlay that matches your HAT version (from step 0):**

For **V1 ("Pro")**:

```bash
make overlays/rpi/respeaker-2mic-v1_0-overlay.dtbo
sudo cp overlays/rpi/respeaker-2mic-v1_0-overlay.dtbo /boot/firmware/overlays/respeaker-2mic-v1_0.dtbo
echo "dtoverlay=respeaker-2mic-v1_0" | sudo tee -a /boot/firmware/config.txt
```

For **V2.0**:

```bash
make overlays/rpi/respeaker-2mic-v2_0-overlay.dtbo
sudo cp overlays/rpi/respeaker-2mic-v2_0-overlay.dtbo /boot/firmware/overlays/respeaker-2mic-v2_0.dtbo
echo "dtoverlay=respeaker-2mic-v2_0" | sudo tee -a /boot/firmware/config.txt
```

Reboot:

```bash
sudo reboot
```

After reboot:

- [ ] `arecord -l` should show `seeed2micvoicec`. The codec line will mention `wm8960` (V1) or `tlv320aic3x` (V2.0) — confirms you picked the right overlay.
- [ ] `aplay -l` should show the same device on the playback side (alongside the HDMI card).

### 4a. Pin the HAT to card 0

`arecord -l` may show the HAT at card **0** or card **1** — Linux numbers cards in probe order, and the HDMI codec races the I²S overlay at boot. Whichever wins gets card 0. The number can shuffle between reboots, especially after a physical move (different USB power-up timing).

The easiest permanent fix is to remove HDMI audio from the picture entirely — a Pi Zero 2 W satellite has no use for it, and once it's gone the HAT is the only sound card and lands at card 0 by definition.

Edit the same file you added the dtoverlay to:

```bash
sudo nano /boot/firmware/config.txt
```

- Find the line `dtoverlay=vc4-kms-v3d` and change it to `dtoverlay=vc4-kms-v3d,noaudio` (this kills the HDMI audio side of the KMS driver).
- If there's a `dtparam=audio=on` line, comment it out with a `#` (this disables the BCM283x onboard audio bus, which the Pi Zero 2 W has no jack for anyway).

Reboot:

```bash
sudo reboot
```

> **Don't bother with `options snd_soc_simple_card index=0`** in `/etc/modprobe.d/alsa-base.conf` — it only expresses a *preference*. Two cards both targeting index 0 still race; whichever the kernel binds first wins, and the option doesn't renumber the loser. The HDMI codec usually wins on Pi Zero 2 W. If you tried this earlier, delete the file: `sudo rm /etc/modprobe.d/alsa-base.conf`.

After reboot, verify:

- [ ] `cat /proc/asound/cards` shows **only** `0 [seeed2micvoicec]: simple-card - seeed2micvoicec` — the HDMI card is gone.

If you skip this step and the HAT happens to land at card 1 today, every `plughw:0,0` command in this doc fails with `audio open error: No such file or directory`. The HAT is fine — just at a different number.

You may also see this in `dmesg` after the overlay loads:

```
tlv320aic3x 1-0018: supply IOVDD not found, using dummy regulator
tlv320aic3x 1-0018: Invalid supply voltage(s) AVDD: -22, DVDD: -22
```

This is harmless cosmetic noise from the V2.0 dtoverlay — the codec's actual regulators power up via I²C, not via the Linux regulator framework. The codec works fine despite the warning.

### 4b. Enable SPI for the onboard LEDs

Both HAT versions ship with **3x APA102 RGB LEDs** wired to SPI0. The satellite client uses them to surface state across the room (idle / listening / thinking / speaking / error). SPI is **disabled by default** on Pi OS — turn it on with the non-interactive raspi-config command:

```bash
sudo raspi-config nonint do_spi 0
sudo reboot
```

After reboot, verify:

- [ ] `ls /dev/spidev0.0` returns the device file (no "No such file or directory")
- [ ] `groups | grep -q spi && echo ok` prints `ok`. If it doesn't, add yourself: `sudo usermod -aG spi $USER`, then **log out and back in** (group membership doesn't apply to existing sessions).

If SPI is unavailable or the `spidev` Python package is missing, the satellite still runs — the LEDs just stay dark and a warning lands in the log. To skip the LED subsystem entirely on a satellite without the HAT, set `[leds] enabled = false` in `~/.domovoi/config.toml`.

## 5. Mic + speaker smoke test

Plug a powered speaker into the HAT's **3.5mm jack** (not the Pi's — Pi Zero 2 W has no headphone jack).

Record 5 seconds from the HAT mics:

```bash
arecord -D plughw:0,0 -f S16_LE -r 16000 -c 2 -d 5 /tmp/test.wav
```

- [ ] Speak during the recording.
- [ ] Play it back through the HAT:

```bash
aplay -D plughw:0,0 /tmp/test.wav
```

**Volume will likely be very quiet on first run** — the codec ships with conservative gain on every stage. Crank it:

```bash
alsamixer -c 0
```

- Press **F5** to show all controls (capture + playback)
- Push `Headphone`, `PCM`, `Speaker`, `Master` to ~80% on the playback side
- Push `Capture`, `Mic`, `ADC` to ~80% on the capture side (some are red-highlighted)
- **M** toggles mute; un-mute anything that's `MM`

Or one-shot via `amixer` (skips controls that don't exist on the codec):

```bash
for ctl in 'Headphone' 'Speaker' 'PCM' 'Master' 'Capture' 'Mic'; do
  amixer -c 0 sset "$ctl" 80% unmute 2>/dev/null
done
```

Persist the settings so they survive reboot:

```bash
sudo alsactl store
```

Re-record + replay. Should be loud and clear now.

**This step gating the rest is intentional** — if mic + speaker don't work standalone, the satellite client can't fix it.

## 6. Python environment for the satellite client

### 6.1 Create the venv

Don't pollute system Python — modern Pi OS's externally-managed-environment makes this annoying anyway.

```bash
python3 -m venv ~/satellite-venv
source ~/satellite-venv/bin/activate
pip install --upgrade pip wheel
```

- [ ] Add to `~/.bashrc` so it auto-activates on SSH:
  ```bash
  echo 'source ~/satellite-venv/bin/activate' >> ~/.bashrc
  ```

### 6.2 Get the satellite code on the Pi

Two options depending on whether you keep the repo on the Pi:

**Option A — clone the repo** (best if you'll iterate on satellite code from the Pi):

```bash
cd ~
git clone https://github.com/coders-farm-official/domovoi.git domovoi
cd domovoi
```

**Option B — rsync just the bits the Pi needs** (lighter, no git history):

From the Domovoi server (in the repo root):

```bash
rsync -av --exclude __pycache__ \
    satellite/ pyproject.toml \
    domovoi-<room>.local:~/domovoi/
```

The Pi only needs the `satellite/` package + `pyproject.toml` to install the right Python entry points. The Domovoi core (`domovoi/`) does NOT run on the Pi.

### 6.3 Install satellite dependencies

```bash
cd ~/domovoi
pip install -r satellite/requirements.txt
pip install --no-deps "openwakeword>=0.6.0"
python -c "import openwakeword.utils; openwakeword.utils.download_models()"
```

The `--no-deps` second line skips openwakeword's `tflite-runtime` pin, which has no Python 3.13 wheel (Trixie's default). The satellite client uses ONNX inference and never imports tflite-runtime; the runtime deps openwakeword actually needs (onnxruntime, scipy, etc.) are listed explicitly in `requirements.txt`. The third line fetches the pre-trained wake-word ONNX models — openwakeword ships the wheel without the model files (~50 MB) and the satellite client crashes at startup with `NO_SUCHFILE: Load model ... hey_jarvis_v0.1.onnx failed` until you run the downloader once.

Note that `requirements.txt` hard-pins `onnxruntime==1.20.0`. cp313 wheels start at 1.20, and 1.21+ abort on the Pi Zero 2 W's Cortex-A53 with a `vector::operator[]` assertion from the manylinux gcc-toolset-14 build. 1.20.0 is the only known-good cp313+aarch64 combo for this hardware. If you upgrade onnxruntime later and the satellite starts crashing at `import openwakeword`, this is why — pin it back.

### 6.4 Generate and edit the config

```bash
python -m satellite.client
# → Wrote example config to ~/.domovoi/config.toml. Edit it ... then re-run.
```

Edit `~/.domovoi/config.toml`:

```toml
[satellite]
room_id = "<room>"                                   # match the hostname suffix
domovoi_url = "ws://<server-ip-or-hostname>:6370"

[wake]
wake_word = "hey_jarvis"                             # dev wake word for now
threshold = 0.5
```

The example file ships with sensible defaults for everything else (barge-in, noise gate, music ALSA device, etc.).

### 6.5 Verify audio devices

```bash
python -m satellite.client --list-devices
```

You want the ReSpeaker (`seeed2micvoicec`) on **card 0** for both input and output (you pinned it there in step 4a). If it's not the system default, set explicit ids in the `[audio]` block of your config:

```toml
[audio]
input_device = 0
output_device = 0
```

### 6.6 First run

```bash
python -m satellite.client
```

Expected log lines:

```
connecting to ws://<server>:6370/v1/stream/<room>
server ready: protocol=0.1 bot=Domovoi
listening for wake word 'hey_jarvis' in room '<room>'
```

If you see all three, the Pi is provisioned. Hit Ctrl+C — the autostart wiring (systemd unit) is set up in step 9 below.

If you see the WebSocket connect immediately drop, or never see `server ready`, the Domovoi server isn't reachable from the Pi — check the server's firewall (port 6370 inbound from LAN) and `ping <server-ip-or-hostname>` from the Pi.

### 6.7 Sudoers entry for the WiFi self-heal

The satellite's WiFi watcher (added 2026-05-06 after an rx-bitrate-stuck-at-1-Mbit/s incident that chopped TTS mid-word) needs to run `wpa_cli reassociate` when it detects a wedged link. `wpa_cli` requires root, so we add a single locked-down sudoers entry — no password prompt, exactly that one command, exactly that one interface, no other arguments.

This is **least-privilege by design** — the satellite process gains the ability to reassociate the WiFi link, and nothing else.

```bash
sudo visudo -f /etc/sudoers.d/satellite-wifi
```

Paste this single line (replacing `<username>` with the user the satellite service runs as — same `User=` from step 8's systemd unit, typically the one you set in the Imager UI in step 1):

```
<username> ALL=(root) NOPASSWD: /usr/sbin/wpa_cli -i wlan0 reassociate
```

Save (`:wq` in vim, `Ctrl-X → Y → Enter` in nano). `visudo` refuses to save on a syntax error, so you can't break sudo with this — if it complains, fix and try again rather than editing `/etc/sudoers` directly.

Verify the entry is wired correctly — this should print `OK` with **no** password prompt:

```bash
sudo -n /usr/sbin/wpa_cli -i wlan0 reassociate
```

- [ ] If it prints `OK`, you're set — the watcher will autonomously recover from rate-stuck WiFi without intervention.
- [ ] If it prompts for a password or says `sudo: a password is required`, the username, path, or syntax is off. Re-check the line you pasted; `which wpa_cli` confirms the path (it's almost always `/usr/sbin/wpa_cli`).

If you skip this step, the satellite still runs — the watcher will simply log a warning every time rx bitrate dips, and you'll be back to manually running `sudo wpa_cli -i wlan0 reassociate` when chop appears.

## 7. Networking — DHCP reservation

mDNS (`*.local`) is fine for development but flaky long-term. Recommended:

- [ ] In your router's admin: assign a **DHCP reservation** for this Pi's MAC address so its IP never changes. Note the IP.
- [ ] Verify the Domovoi server is reachable from the Pi:
  ```bash
  ping -c 3 <server-ip-or-hostname>
  curl http://<server-ip>:6370/v1/health
  ```
  Should return the server's health JSON. If not — Windows Firewall on the server machine is likely blocking port 6370 from LAN. Allow it inbound for the LAN subnet only.

## 8. Auto-start the satellite at boot (systemd)

The unit file lives IN the repo now (`satellite/domovoi-satellite.service`,
with `@USER@`/`@HOME@` placeholders) so it rides the satellite-code sync
channel like everything else. Install it with the helper:

```bash
sudo -E sh ~/domovoi/satellite/scripts/install-service.sh
# add --with-provisioning for the USB-adoption unit,
# and --with-kiosk on a video satellite (see VIDEO_SATELLITE.md)
sudo systemctl start domovoi-satellite
journalctl -u domovoi-satellite -f   # tail logs
```

(Equivalent by hand: `sed` the two placeholders and `tee` the result into
`/etc/systemd/system/domovoi-satellite.service`, then `daemon-reload` +
`enable --now`.)

`Restart=on-failure` lets the Pi recover from a transient WiFi drop without intervention (the client also reconnects internally with exponential backoff, so you'll usually see the systemd restart only on hard failures).

After this, **`unplug → plug back in`** brings the Pi up, joins WiFi, and reconnects to the Domovoi server automatically. For the bulletproof path, also turn on overlayfs once the Pi's behavior is dialed in.

> **Note on the `journalctl` line above** — `enable --now` already started the service in the background. The `journalctl -u domovoi-satellite -f` tail is just for watching the startup log. Ctrl+C to exit the tail; the service keeps running.

### 8.1 Sudoers entry for self-restart

The satellite can restart **its own** service when you change a config that only takes effect on a fresh process — an audio-device or LED change pushed from the web dashboard's per-satellite Settings, or the **Restart satellite** button on the Satellites page. `systemctl` needs root, so — exactly as with the WiFi entry in §6.7 — we add one locked-down, no-password, single-command sudoers line. Least-privilege: the satellite gains the ability to restart its own unit, and nothing else.

> **Upgrading an existing satellite?** This entry is part of the rollout, not just fresh provisioning — add it to every Pi you push the new client to, or self-restart silently won't work there.

```bash
sudo visudo -f /etc/sudoers.d/satellite-restart
```

Paste this single line (same `<username>` as §6.7 — the user the service runs as):

```
<username> ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-satellite.service
```

> Match it **exactly** — sudo compares the whole argument list. The client runs precisely `sudo -n /usr/bin/systemctl --no-block restart domovoi-satellite.service`. The `--no-block` flag is load-bearing: a synchronous restart issued from *inside* the unit shares its cgroup and can be SIGTERM'd mid-job, leaving the service stopped; `--no-block` hands the job to systemd (PID 1), which finishes it after the asking process dies.

Verify — this should bounce the service (watch the journal reconnect) with **no** password prompt:

```bash
sudo -n /usr/bin/systemctl --no-block restart domovoi-satellite.service
journalctl -u domovoi-satellite -f   # a fresh startup should appear within a couple seconds
```

- [ ] If the service restarts with no prompt, you're set — config pushes that need a restart apply themselves.
- [ ] If it prompts for a password, the username, path, flag order, or unit name is off. `which systemctl` confirms the path (almost always `/usr/bin/systemctl`).

If you skip this step the satellite still runs — it just can't restart itself, so a restart-requiring config change needs a manual `sudo systemctl restart domovoi-satellite`, and the web **Restart** button reports a failure.

## 9. Label the hardware

- [ ] Stick a label on the Pi case with: hostname, room, IP, **HAT version (V1/V2.0)**. Future you will be grateful.
- [ ] Note the same in a `satellites.txt` somewhere you'll find it.

---

## When this checklist is done

You should have:

1. A Pi at `domovoi-<room>.local` (or the reserved IP) with SSH access from your laptop
2. Working mic capture + speaker playback through the ReSpeaker HAT, at audible volume
3. A Python venv with all satellite deps installed and the wake-word ONNX models downloaded
4. `~/.domovoi/config.toml` populated with the right `room_id` and `domovoi_url`
5. The satellite client running under systemd as `domovoi-satellite.service` and auto-starting on boot
6. Network reachability confirmed both directions between the Pi and the Domovoi server
7. Both sudoers entries in place: WiFi self-heal (§6.7) and self-restart (§8.1)

---

## Operating the satellite

Once the systemd unit is in place, use these commands for everyday operation. None of them require an SSH session that stays open — fire and forget.

```bash
sudo systemctl status domovoi-satellite      # is it running, is it healthy
sudo systemctl restart domovoi-satellite     # apply code changes after rsync
sudo systemctl stop domovoi-satellite        # take it down (won't auto-start until next boot or `start`)
sudo systemctl start domovoi-satellite       # start it again
sudo systemctl disable domovoi-satellite     # stop auto-starting on boot (doesn't stop the running instance)
sudo systemctl enable domovoi-satellite      # re-enable auto-start
journalctl -u domovoi-satellite -f           # tail logs (Ctrl+C exits the tail; doesn't affect service)
journalctl -u domovoi-satellite --since "10 min ago"   # historical logs without tailing
```

**Iterating on satellite code from the Domovoi server** — rsync + restart in one go:

```bash
rsync -av --exclude __pycache__ \
    satellite/ domovoi-<room>.local:~/domovoi/satellite/
ssh domovoi-<room>.local "sudo systemctl restart domovoi-satellite"
```

**Status output to expect** when healthy: `Active: active (running)` plus a recent log line like `listening for wake word 'hey_jarvis' in room '<room>'`. If `Active:` shows `failed`, `journalctl --since "10 min ago"` gets you the death log. After fixing the underlying issue, `start` brings it back.

**Hard reset path** (rare — only if the satellite is wedged in a way `restart` doesn't fix): `sudo reboot` on the Pi. The systemd unit auto-starts on boot, so the satellite comes back without manual intervention.

---

## Pairing token (WS auth)

Each satellite authenticates its WebSocket to the Domovoi server with a **pairing token** — a per-device secret that stops another machine on the LAN from opening the voice socket as one of your rooms (e.g. `kitchen`) and, via drop-in, listening in.

**It is automatic.** On its first boot the client generates a random token (`secrets.token_hex(32)`) and writes it to the sidecar:

```
~/.domovoi/pairing_token       # 64 hex chars, mode 0600, one per device
```

It's sent in every `hello` frame's `pairing_token` field. The server stores only the token's **sha256** (never the raw token) in the `satellite_pairings` table and binds the room to it **trust-on-first-use**: the first satellite to present a token for a room *claims* it, and from then on that room's connection must present the matching token or the server refuses it (logs a warning, sends `{"type":"error","reason":"pairing_rejected"}`, and closes the socket).

A room that has never paired still accepts a tokenless connection (backward-compatible with older satellites) — unless the server sets `SATELLITE_PAIRING_STRICT=true`, which requires a token for **every** room.

**Nothing to do during provisioning.** Don't copy a token between Pis — each device generates its own, and a room can only be held by one token at a time.

### Re-pairing after a re-flash or device swap

If you **re-flash the SD card**, **replace the Pi**, or **move a room to a new device**, the new device generates a *fresh* token that won't match the one the server has on file for that room — so its `hello` is refused (case: token mismatch) and it can't connect. Clear the old pairing so the new device can re-pair:

- **From the dashboard:** Satellites page → open the room → **Overview** → **Reset pairing**. The pairing status line shows "paired since …" / "unpaired". (Admin login required — resetting pairing is a security action.)
- **Effect:** the server deletes the room's `satellite_pairings` row; the next `hello` from that room re-pairs trust-on-first-use with the new device's token.

You do **not** need to reset pairing for a normal `restart`, `upgrade`, or `reboot` — the token sidecar survives those (it lives in `~/.domovoi/`, outside the code tree). Only a wiped config dir / new device needs a reset.

If a device's token sidecar is ever lost (e.g. `~/.domovoi/pairing_token` deleted) while the room is still paired to the old token, reset the pairing from the dashboard and let the device generate + claim a new one on its next connect.

---

## Appendix: XVF3800 USB 4-Mic Array

The **ReSpeaker XVF3800 USB 4-Mic Array** is an alternative to the 2-Mics HAT. It's a USB Audio Class device with an on-chip XMOS DSP (AEC, 60 dB AGC, beamforming, noise suppression, VAD, DoA), a 12× WS2812 LED ring, and its own 3.5mm/JST speaker output. The satellite handles all the board-specific differences when you set `[device] profile = "xvf3800_usb"` — this appendix covers only what differs from the HAT checklist above.

> **Swapping an existing HAT satellite over to the XVF3800?** Don't re-flash — see [`MIGRATION_HAT_TO_XVF3800.md`](MIGRATION_HAT_TO_XVF3800.md) for the in-place swap (which cords to move, config changes, and what to test). This appendix is for provisioning a *fresh* Pi.

### A. Hardware

- [ ] Pi Zero 2 W (or any Pi). **No GPIO header needed** — the array is USB, not a HAT.
- [ ] ReSpeaker XVF3800 USB 4-Mic Array + its USB cable.
- [ ] **Powered speaker plugged into the ARRAY's own 3.5mm jack (or JST connector)** — NOT the Pi. This is mandatory: the XVF3800's acoustic echo canceller uses the playback signal it drives out that jack as its echo reference. If the speaker is on a different output, AEC has nothing to cancel and barge-in will false-trigger on the bot's own voice.
- [ ] **If that speaker is a STEREO powered-pair, add a 3.5mm mono→stereo adapter.** The array's output is **mono** (single channel — `speaker-test -c2` plays only *Front Left*; the right DAC channel isn't wired to the jack, and no `xvf_host AUDIO_MGR_OP_R` reroute lights it). A full AV receiver fills all its speakers from the one input, but a stereo powered-pair (active speaker + a passive one daisy-chained off it) leaves the **second speaker dead**. A cheap **mono→stereo adapter** (mono *male* → stereo *female*) at the speaker input copies the live channel to both — verified in-field, and it evens out loudness/balance. **Standing rule: this setup gets the adapter by default.** (The output is also line-level → see the `[playback] gain` make-up-gain note in `MIGRATION_HAT_TO_XVF3800.md`.)
- [ ] microSD, power supply, etc. — same as the HAT (§1 below).

### B. Skip the HAT-only steps

You do **not** do any of these for the XVF3800:
- §0 (HAT version / WM8960-vs-TLV320) — N/A.
- §4 (dtoverlay) and §4a (card-0 pinning / HDMI removal) — the array enumerates as a USB sound card on its own; no overlay.
- §4b (SPI for APA102) — the XVF3800 LEDs are WS2812 on the array, driven over USB (see §E), not SPI.

### C. Flash + base packages

Do §1–§3 as written (the array works on Trixie or Bookworm). `mpg123`, `alsa-utils`, `portaudio19-dev`, `libasound2-dev` are all still needed.

### D. Plug in + verify the USB audio device

- [ ] Plug the array into the Pi's USB port and the speaker into the array's jack.
- [ ] `aplay -l` and `arecord -l` should list the array (a `USB Audio` / XMOS device). Note its **card number** — it is usually **not** card 0.
- [ ] Smoke test capture (the 2-channel firmware presents ch0 = Conference, ch1 = ASR beam; the client uses ch1):

```bash
arecord -D plughw:CARD=<arrayname>,DEV=0 -f S16_LE -r 16000 -c 2 -d 5 /tmp/test.wav
aplay  -D plughw:CARD=<arrayname>,DEV=0 /tmp/test.wav
```

Get the exact `CARD=` name from `arecord -L` (it's usually `Array`). The shipping firmware enumerates **S16_LE, 2 channels, 16 kHz** — confirm with `arecord -D hw:CARD=<arrayname>,DEV=0 --dump-hw-params -d 1 /dev/null` (look at `FORMAT` / `CHANNELS` / `RATE`). If yours reports something else, note it — the `xvf3800_usb` profile's `capture_dtype`/`capture_channels` would need to match. The XVF3800 does its own *input* gain (60 dB AGC) — you should **not** need `alsamixer` for capture. **Output** volume is voice-controlled at runtime ("set the volume to 50", "turn it up") — the satellite drives the array's `PCM` control via the `[audio] output_mixer_control` setting (default `PCM` on the xvf profile) and reports the level back to the server, so there's no manual mixer/`alsactl store` step. The array's 3.5mm jack is line-level and TTS is normalized below full scale, so Domovoi's voice (and the wake-word greeting clips) can sound faint even at `PCM` 100% — set a software make-up gain with `[playback] gain` (start ~`3.0`, range `2.0`–`4.0`; it boosts the voice and the local clips together, music untouched). For a bigger lift, the array's amplified JST speaker output is louder than the line-level jack.

- [ ] **Pi Zero 2 W / older Pis — full-speed USB (needed only on the `dwc_otg` driver).** The Pi's `dwc_otg` controller mis-clocks isochronous (audio) transfers at high speed, making the array deliver audio ~**8× too fast** — the symptom is relentless `mic queue overflowing` and a wake word that never fires, with `satellite/scripts/mic_probe.py` reading `frames/sec ≈ 128000`. Fix: add `dwc_otg.speed=1` to the **single line** of `/boot/firmware/cmdline.txt` (do not add a newline — a malformed cmdline can stop the Pi booting), then `sudo reboot`. Harmless here: the Zero 2 W's WiFi is on SDIO, not USB, and full-speed's 12 Mbit/s easily covers 16 kHz audio. Verify: `mic_probe.py` should read `frames/sec ≈ 16000` (not ~128000) and `dmesg | grep -i "full speed"` should show the array. **If instead you forced host mode with `dtoverlay=dwc2,dr_mode=host` (e.g. to run a plain data cable without an OTG adapter), you're on the upstream `dwc2` driver, which clocks the audio correctly on its own — `dwc_otg.speed=1` does not apply and this step is unnecessary.** Either way, let `mic_probe.py` be the judge: if `frames/sec ≈ 16000`, you're done.

### E. Install the `xvf_host` LED/control tool

The 12-LED ring is driven through Seeed's `xvf_host` CLI (it also configures the chip). Without it the satellite still runs — the ring just stays dark.

`xvf_host` is **not** a single file — it loads `libcommand_map.so` (and other companion files) from its *own* directory, so it must stay alongside them. Install the whole `rpi_64bit/` folder, don't copy just the binary (a lone binary fails with `libcommand_map.so: cannot open shared object file`).

```bash
sudo apt install -y libusb-1.0-0     # xvf_host links against libusb
git clone https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY.git ~/xvf3800-src
sudo cp -r ~/xvf3800-src/host_control/rpi_64bit /opt/xvf3800
sudo chmod +x /opt/xvf3800/xvf_host
```

- [ ] Quick LED test (runs with no missing-lib error): `sudo /opt/xvf3800/xvf_host led_effect 3 && sudo /opt/xvf3800/xvf_host led_color 0x00ff50` turns the ring solid green; `sudo /opt/xvf3800/xvf_host led_effect 0` turns it off.

`xvf_host` typically needs root for USB access. The satellite probes a plain call first and falls back to `sudo -n`, so add a passwordless sudoers entry (mirrors §6.7's wpa_cli pattern — least-privilege, one binary):

```bash
sudo visudo -f /etc/sudoers.d/satellite-xvf
# add (replace <username> with the service user):
<username> ALL=(root) NOPASSWD: /opt/xvf3800/xvf_host
```

- [ ] Verify no prompt: `sudo -n /opt/xvf3800/xvf_host led_brightness 64` returns without asking for a password.
- [ ] Point the client at it — since it's not on `PATH`, set the path in `~/.domovoi/config.toml`:
  ```toml
  [leds]
  xvf_host_path = "/opt/xvf3800/xvf_host"
  ```

### F. Python env + config

Do §6.1–§6.3 (venv, code, deps) unchanged. Then in `~/.domovoi/config.toml`:

```toml
[device]
profile = "xvf3800_usb"

[audio]
# A device-NAME substring is reboot-proof — card/index numbers can shuffle
# (especially while a HAT overlay is still loaded), but the name always
# resolves to the array. Get it from `python -m satellite.client --list-devices`.
input_device = "reSpeaker XVF3800"     # or the numeric index
output_device = "reSpeaker XVF3800"    # MUST be the array (AEC reference)

[music]
alsa_device = "plughw:CARD=Array,DEV=0"   # name-based; `Array` from `arecord -L`
```

Selecting the profile auto-disables the ALSA mic-gain tune and the software noise-gate auto-calibration (the chip does both on-chip) and keeps barge-in on normal VAD (on-chip AEC cancels the echo) — you don't set those by hand.

- [ ] First run (§6.6) — you should additionally see `device profile: xvf3800_usb (...)` and `mic capture: int16 2ch, selecting channel 1 (ASR beam) → mono int16` in the log, and the LED ring should track idle→listening→thinking→speaking. If you see the `output_device is unset` warning, pin it (§F above).

### G. Shared remaining steps

§6.7 (wifi sudoers), §7 (DHCP reservation), §8 (systemd autostart), §9 (label — note "XVF3800 USB" instead of HAT version) all apply unchanged.

---

## Known landmines

- **V1 vs V2.0 confusion** — see step 0. Different codecs, different overlays. The seeed wiki's main page lists "WM8960" but V2.0 actually has a **TLV320AIC3104**. Symptom of using the wrong overlay: `wm8960 1-001a: Failed to issue reset` in dmesg, no audio device in `arecord -l`. Fix: rebuild and install the matching `respeaker-2mic-v<N>_0-overlay.dtbo`.
- **Header not soldered** — easy to overlook, kills everything. Solder first.
- **Underpowered USB supply** — Pi Zero 2 W browns out under WiFi + audio load with weak chargers. 2.5 A minimum. Check `vcgencmd get_throttled` — anything other than `0x0` means brownout history.
- **HAT not fully seated** — partial seating presents the same symptoms as a missing/wrong driver: no I2C device on the bus, codec probe fails. Reseat with even pressure on all four corners before chasing software.
- **Old `seeed-voicecard` DKMS approach is dead** — the `respeaker/seeed-voicecard` repo (and its forks like HinTak's) is the *old* way and breaks on every kernel bump. Use `Seeed-Studio/seeed-linux-dtoverlays` instead — it's a pure dtoverlay, no kernel module to maintain.
- **Default codec gain is near-silent** — first playback after install will sound barely audible. Run `alsamixer -c 0` and push playback channels to 80%, then `sudo alsactl store` to persist.
- **`pulseaudio` vs `pipewire`** — Pi OS Lite ships neither by default; ALSA direct is fine and simpler. Don't install pulseaudio unless something else demands it.
- **Pi Zero 2 W memory** — 512 MB RAM. openWakeWord runs but is tight. If you hit OOMs later, swap is your friend; we'll deal with it then.
