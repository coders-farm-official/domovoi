# Hardware validation — satellite onboarding + video satellite

Everything below shipped with full off-hardware test coverage (unit +
API + fake-volume/fake-gadget harnesses), but these flows touch kernels,
USB controllers, panels, and Wi-Fi radios — the following checklists are
what still needs a pass on real devices before calling the features
released. Check items off in a PR that records board, OS image, and
kernel version.

## A. Pi Zero 2 W — prepared media + USB adoption

Fresh card, stock Raspberry Pi OS Lite (64-bit, Trixie), no Imager
pre-configuration.

1. [ ] Dashboard **prepare satellite media** onto the inserted card
       (drive target detected; free-space check sane; build completes).
       Cold-cache build: wheels fetch succeeds for cp313/aarch64;
       docker-less machine degrades to `offline=false` with the warning
       visible on the card.
2. [ ] **Boot 1 (offline):** stage 1 completes with Wi-Fi off — verify
       `/var/log/domovoi-firstrun.log`, the `bootstrap-steps` markers, and
       that `device-info.json` on the boot partition reaches
       `awaiting_provision`. **Power-cut mid-stage-1** → next boot resumes
       idempotently.
       - Known risk: the `systemd.run=` cmdline hook's exact path semantics
         on Trixie (`/boot/firmware` vs `/boot`) — if firstrun never runs,
         compare against what Raspberry Pi Imager writes and pin ours to
         match (`domovoi/satellite_media/overlay.py:_CMDLINE_TOKENS`).
3. [ ] **Boot 2 (gadget):** plugged into the Domovoi server's USB port,
       the device enumerates as a `DOMOVOI-SET` flash drive (Windows
       assigns a letter; no driver prompts; `stall=0` + composite VID/PID
       accepted). The dashboard's pending card appears ≤ 5 s.
4. [ ] **Adopt:** name + Wi-Fi → provision file lands; device applies
       (two-stable-reads visible in its journal), wipes `provision.json`,
       reboots, joins Wi-Fi; core log shows the pairing accept as a
       case-2 match (NOT "paired (trust-on-first-use)"); MPD provisions;
       the card flips online. Repeat under `SATELLITE_PAIRING_STRICT=true`.
5. [ ] **Wrong-PSK path:** adopt with a bad password → after the retry
       window the drive re-presents with `wifi_failed` + the error on the
       dashboard card → **Re-enter Wi-Fi** (force) with the right PSK
       succeeds and rotates the token.
6. [ ] **Stage 2:** after Wi-Fi, `domovoi-bootstrap` syncs fresh code +
       plugin payloads from the server, enables the satellite unit, marks
       done; wake word fires; music plays (mpg123 present from the deb
       cache); mic-profile audio (HAT overlay or XVF3800) works;
       `vcgencmd get_throttled` = `0x0`. **Power-cut mid-stage-2** → next
       boot resumes.
7. [ ] **Plugin payload:** enable a plugin with a `[satellite]` section →
       dashboard **Upgrade satellite** → files land under
       `~/.domovoi/plugin_payloads/<slug>/`; root steps run via
       `domovoi-apply-payload` (check `~/.domovoi/payload_apply.log`);
       disabling the plugin prunes exactly its subtree on the next
       upgrade.
8. [ ] **Mid-adopt unplug:** yank the cable between the adopt click and
       the write → dashboard shows the 410 toast; no orphaned `waiting`
       room remains (rollback ran) or, if one does, its Remove button
       clears it.

## B. Radxa Zero 3W — video satellite (manual provisioning)

Per [satellite/VIDEO_SATELLITE.md](../satellite/VIDEO_SATELLITE.md), on
Armbian and/or Radxa Debian (record which).

1. [ ] Kiosk boots unattended to the display page in < 60 s (cage +
       Chromium; no cursor, no crash bubble, correct URL from
       `python -m satellite.kiosk --print-url`).
2. [ ] Voice-off build connects: dashboard shows the `video` chip +
       "voice input off"; announce speaks; drop-in shows the no-mic copy;
       wake-recording for the room is refused (400).
3. [ ] "Play music in <room>" → audio out the configured ALSA device AND
       on-screen now-playing with cover art (library track), progress
       advancing 1 s/s, staying synced across pause/resume; touch
       transport works; radio/stream falls back to the gradient tile.
4. [ ] Screen off/on from the drawer actually blanks/wakes the panel —
       **record which `power_method` worked** (wlopm under cage is the
       expected one; xset/backlight are the fallbacks; HDMI-monitor DPMS
       behavior varies). If none: pin `power_method` accordingly or
       `none`.
5. [ ] `pkill chromium` → systemd relaunches the kiosk; the dashboard's
       kiosk-alive pill flips dead → running within the watch interval;
       the drawer's **Restart kiosk** button bounces it (sudoers line
       present).
6. [ ] Voice-on variant: XVF3800 plugged in, `profile="xvf3800_usb"` +
       `[mic] enabled=true` pushed from the Settings tab → wake word,
       command, barge-in, and music-duck all work while the screen keeps
       rendering; drop-in now offered.
7. [ ] Idle modes: `clock` (dimmed clock + room name), `blank`, `art`
       each render after stop; burn-in shifter observed drifting over a
       few minutes.
8. [ ] 24 h soak: no Chromium OOM (≥ 2 GB board), progress drift < 2 s
       after a day, WS reconnect overlay appears/clears across a server
       restart.
9. [ ] **USB adoption on RK3566 (investigation, phase 2):** whether the
       USB-C port enters peripheral mode (dr_mode) and enumerates the
       mass-storage gadget on this image — result decides when the Radxa
       board flips `supported=true` in
       `domovoi/satellite_media/boards.py`.
