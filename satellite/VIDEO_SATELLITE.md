# Video satellite — Radxa Zero 3W kiosk build

A **video satellite** is a normal Domovoi satellite that also drives a
screen: it plays the room's music like any satellite (mpg123 ← its own MPD
stream) and renders a fullscreen now-playing page (`display.html` on the
web dashboard) in a kiosk browser. Voice is fully supported but **optional
per device** — the default video build has no microphone and boots with
`[mic] enabled = false`; add a supported mic later and flip one config key.

Target hardware: **Radxa Zero 3W** (RK3566, ≥2 GB RAM strongly
recommended — Chromium plus the client on 1 GB is OOM territory; see the
cog/WPE note below), micro-HDMI screen or DSI touch panel, speaker out of
HDMI or (better) a USB DAC.

This doc is the manual bring-up path (the golden path is the dashboard's
prepare-media + USB-adoption flow once your board is supported there).
It assumes you've read [PROVISIONING.md](PROVISIONING.md) — only the
video-specific deltas are spelled out here.

## 1. OS + packages

Flash Armbian (Bookworm/Trixie minimal) or Radxa's Debian for the Zero 3W.
Then:

```sh
sudo apt update
sudo apt install -y git python3-venv alsa-utils mpg123 \
  cage chromium        # some images name it chromium-browser — both work
```

`cage` is a single-window Wayland kiosk compositor; the kiosk unit runs
`cage -- chromium --kiosk <url>`. The kiosk user needs seat/video access:

```sh
sudo usermod -aG video,render,input $USER
```

## 2. Client install

Follow PROVISIONING.md §6 (venv + `satellite/` code + requirements) with
one difference — a mic-less build can skip the openwakeword model
download; the client never loads it while `[mic] enabled` is false.

## 3. Config

`~/.domovoi/config.toml`:

```toml
[device]
profile = "radxa_zero3w_video"

[satellite]
room_id = "livingroom_tv"          # unique per satellite, even same room
domovoi_url = "ws://<server-ip>:6370"
sat_type = "video"

# [mic] enabled defaults to false on this profile. To add voice later:
# plug in a supported mic (e.g. an XVF3800 → switch profile to
# "xvf3800_usb" and keep sat_type = "video"), then set:
# [mic]
# enabled = true

[display]
# idle_mode = "clock"              # clock | blank | art
# power_method = "auto"            # auto | wlopm | xset | backlight | none
```

Audio: HDMI audio card naming on RK3566 varies by kernel. Find yours with
`aplay -l`, then pin `[music] alsa_device` (e.g. `"plughw:0,0"`) and, for
a USB DAC, `[audio] output_mixer_card` / `output_mixer_control` so the
dashboard volume slider works (HDMI sinks expose no mixer — the slider is
a no-op without a DAC).

## 4. Services

Install BOTH units (placeholders sed'ed like the satellite unit):

```sh
for u in domovoi-satellite domovoi-kiosk; do
  sudo sed -e "s|@USER@|$USER|g" -e "s|@HOME@|$HOME|g" \
    ~/domovoi/satellite/$u.service | sudo tee /etc/systemd/system/$u.service >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now domovoi-satellite domovoi-kiosk
```

(If your checkout predates the in-repo `domovoi-satellite.service`, use
the PROVISIONING.md §8 heredoc for that one.)

Sudoers — the self-restart entry from PROVISIONING.md §8.1 **plus** one
line so the dashboard's "restart kiosk" button works:

```
<user> ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-kiosk.service
```

The kiosk URL is derived automatically (`python -m satellite.kiosk
--print-url` → `http://<server>:6369/display.html?room=<room_id>`); set
`[display] kiosk_url` to override.

## 5. What you get

- The dashboard's Satellites page shows a `video` chip on the card; the
  drawer gains a **display** block (screen on/off, restart kiosk,
  kiosk-alive pill) and transport buttons on the now-playing row.
- The screen shows cover art (library tracks; streams get the gradient),
  title/artist/album, a live progress bar, and touch transport. Idle =
  clock / blank / last art per `[display] idle_mode`.
- "Play music in <room>" through any voice satellite — or the dashboard —
  targets this satellite's own room id like any other satellite. Each
  satellite has its OWN music pipe (per-satellite MPD); group satellites
  sharing a physical room with a room label on the Satellites page.

## 6. Troubleshooting

- **Chromium crashes / white screen:** panfrost GPU accel is spotty on
  some Armbian kernels. Add `--disable-gpu` to the chromium line in
  `satellite/scripts/kiosk_launch.sh` (the page is near-static — software
  rendering is fine).
- **1 GB board / OOM:** run voice-off (default), and consider `cog` (WPE
  WebKit) instead of Chromium: `apt install cog` and swap the exec line to
  `cog -P fdo "$URL"` under cage. Chromium is the default because the
  page's Babel/React stack is verified there.
- **Screen toggle does nothing:** pin `[display] power_method`. `wlopm`
  needs the cage session's Wayland socket (`/run/user/<uid>/wayland-*`);
  `backlight` needs write access to `/sys/class/backlight/*` — add a udev
  rule or run the client with group `video`. `none` disables the toggle
  honestly.
- **No audio:** `speaker-test -D <device>`; HDMI audio may need
  `hdmi_force_hotplug`-style overlay settings depending on the image.
- **Verify the page itself** from any desktop browser:
  `http://<server>:6369/display.html?room=<room_id>`.
