# Satellites on spare devices

A room doesn't need a Raspberry Pi. A spare **Linux laptop or mini PC** can
be one today, an old **phone or tablet** through the Android app's room
mode, and a **Windows** laptop later. Each speaks the same protocol to the
Domovoi server as a Pi, joins through the same approval code, and becomes a
room like any other: wake word, answers, music, intercom, timers.

This page is for people setting one up. How it works underneath:
[uml/satellite-protocol.md](uml/satellite-protocol.md) ("Enrolling a spare
device", "The local control channel"); the security model:
[SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#spare-device-satellites-laptops-and-phones).

## Before you start, whatever the device

A spare device makes a good room only if it stays awake, stays cool and
doesn't wear out its battery.

| | Laptops and mini PCs | Phones and tablets |
|---|---|---|
| **Battery** | An old lithium battery kept on charge for months can swell. Set a charge limit (the installer offers one where the laptop supports it; otherwise in the firmware or the maker's tool), or take the battery out if it comes out. | Turn on the phone's own battery-protection or charge-limit setting where it has one. Never use a device whose battery has swollen. |
| **Sleep** | Suspend off, and the lid does nothing when closed (the installer asks). The screen may still switch off. | Room mode keeps listening with the screen off; see the phone section. |
| **Heat** | On a hard surface with its vents clear, never closed on a cushion. | Out of direct sun and off soft surfaces while it charges. |
| **Power** | Several to tens of watts (a Pi Zero 2 W uses about one). | A watt or two on the charger. |
| **Sound** | A laptop's mics are tuned for someone in front of it: a counter or a desk works, across the room is weak. A USB speakerphone helps a lot. | Fine on a nightstand or a counter. |
| **Privacy** | Use a fresh user account (or a fresh Linux install) just for the satellite. | Factory-reset it and use it only as a room. |

## How a spare device joins

1. In the dashboard, as admin: **Settings → Satellites → add a spare
   device**. Type the room's name ("Kid's Room") and press **make the
   code**. The page shows the room id it will connect as
   (`kids_room`), a **one-line install command** for Linux, and a **QR
   code** for a phone. Both carry the same short *enrolment code*:
   `domovoi-room:1?core=…&fp=…&room=…` — your server's address, its
   fingerprint, and the room's name. **Nothing in it is a password.**
2. On the device, run the command (or scan the code).
3. The device finds your server, checks it is *your* server (it must prove
   it holds the key behind the fingerprint), and asks to join. It shows a
   six-digit **approval code** on its screen.
4. Back in **Settings → Satellites**, the device is under **waiting for
   approval**: type its code and press **approve**. That is the moment it
   becomes a room.

The address in the code must be on your home network (a private address,
or a name ending in `.local`, `.home.arpa`, `.lan` or `.internal`): a code
pointing anywhere else is refused, so nobody can send a room's microphone
out of the house with a code on a web page. If the dashboard says the
address it found isn't usable (a server with several network cards), tick
**a different server address** and give the one your devices reach.

## Linux laptops and mini PCs

Tested on **Debian 12 and 13** and **Ubuntu 24.04**, with a desktop
(GNOME, KDE, Xfce, …) or without one. Other Debian-family systems will
probably work; others are not covered. x86-64 and 64-bit ARM.

**An old Windows 10 laptop** that can't run Windows 11 makes a good room
once it runs Linux: write the Debian or Ubuntu installer to a USB stick
(their download pages explain how), boot from it, and install. Create a
user account just for the satellite while you're at it.

### 1. Make the command

As above: **Settings → Satellites → add a spare device**, the room's
name, **make the code**, **copy the command**. It looks like this (one
line):

```
d=$(mktemp -d) && { wget -qO "$d/s.tgz" http://192.168.0.117:6370/v1/satellite-code/bundle.tar.gz || curl -fsSLo "$d/s.tgz" http://…; } && echo '<sha256>  '"$d/s.tgz" | sha256sum -c --quiet - && tar -xzf "$d/s.tgz" -C "$d" && bash "$d/satellite/scripts/install-pc.sh" 'domovoi-room:1?core=…&fp=…&room=Kid%27s%20Room'
```

It downloads the satellite software from your server and runs it **only if
it is exactly what the dashboard expected** (the SHA-256 in the command).
Make a fresh command after you update Domovoi: the old one refuses the new
download, which is the point.

The command is only as trustworthy as the dashboard page you copied it
from, and that page is plain HTTP on your network: someone who can rewrite
it on the way could give you a different command, and once that runs it is
their code, whatever it prints. So **check the command before you run
it**: the address it downloads from is your Domovoi server's (the one you
open the dashboard at), and the `fp=` in the code at its end is the
fingerprint under **Settings → About**, looked at on another device (your
phone).

### 2. Run it

On the laptop, **signed in as the account the satellite should use** (not
root), open a terminal, paste the command and press Enter. It asks for your
password when it needs `sudo` (installing packages, and the power settings
you agree to). In order, it:

1. checks the server named in the code is the one with that fingerprint —
   if not, it stops and changes nothing;
2. asks **what the server may do on its own** (below);
3. puts its uninstaller in `/usr/local/lib/domovoi-satellite` (root's own
   copy, which the satellite can't change);
4. installs the few packages it needs (`python3-venv`, `libportaudio2`,
   `mpg123`, `alsa-utils`; with a desktop `python3-tk` and
   `pulseaudio-utils`);
5. sets up the satellite in `~/.local/share/domovoi-satellite` (its own
   Python environment, from a list in which every package file is pinned
   by its SHA-256, and the wake-word models, each checked the same way),
   and its settings in `~/.domovoi/config.toml`;
6. starts it as a service of your account that also runs at boot before
   anyone signs in (`systemd --user` with *linger*), puts the tray icon in
   your desktop's autostart and **Domovoi Satellite** in the app menu;
7. asks, one at a time, about **keeping it awake**: ignore the lid
   switch, never sleep, stop charging at 80 %. Each defaults to **No**;
   say yes to what this computer needs;
8. waits for you to approve it, showing the **approval code**.

It refuses an account that already runs a Domovoi server (one whose
`~/.domovoi` holds the server's key): use another account, or headless
mode. Python 3.11 to 3.14 on x86-64 (and 3.11 to 3.13 on 64-bit ARM) have a
pinned list; on anything else it stops before installing and says so.

About ten minutes on a fresh install, mostly downloads.

### 3. Approve it

The code is on the terminal, in a pop-up ("Waiting for approval, code
123456"), and in the tray. Type it into the dashboard's approval card. The
pop-up then says **Approved: this device is now the Kid's Room** and the
wake word (the default "Hey Jarvis") works.

Missed it? `cd ~/.local/share/domovoi-satellite && venv/bin/python -m
satellite.client --approval-code`. The code is never written to the
satellite's log.

### "Automatic" or "ask me"

The installer asks what the server may do on its own. Either way **nothing
runs as root**: there are no root helpers on a laptop, so the satellite
never re-joins your Wi-Fi, sets the clock, or applies plugin parts that
need root. (Plugins that install something as root on a satellite stay
Pi-only for now.)

| | **automatic** | **ask me** |
|---|---|---|
| Satellite updates the server pushes (signed by your server's key) | installed, then the satellite restarts | wait for **Allow** in the tray (or `--approve`) |
| Restarts the server asks for (a settings change, "restart") | at once | wait for **Allow** |
| Settings changed in the dashboard (wake word sensitivity, …) | applied, restart | wait for **Allow** |
| A wake word pushed to the room | applied, restart | wait for **Allow** |
| What it runs as | your account, with no way to gain privileges (`NoNewPrivileges`) | the same |

**Automatic** is like a Pi: hands off. Updates then run as the account
the satellite uses, and the installer needed that account to use `sudo` —
so code your server sends could reach root the next time that account uses
sudo. Use a **dedicated account** for it, and take its sudo away once the
install is done (`sudo deluser <account> sudo`; give it back to uninstall),
or use the **headless** mode below, whose account has neither sudo nor a
login. **Ask me** suits a computer someone also uses: nothing new runs
until they say so. Change it later in `~/.domovoi/config.toml` (`[pc]
permissions = "automatic"` or `"ask"`) and restart the satellite; the
server can never change it for you, nor the pinned server, its address or
the device's profile, and a setting it sends that names an audio device
must be a plain one (`hw:1,0`, `default`, …).

### The tray

| Icon | Means |
|---|---|
| green outline microphone | listening for the wake word — nothing leaves the computer |
| **filled red microphone** | **the microphone is live**: it is sending what it hears to your server (a command, an intercom call, a recording) |
| struck-through microphone | muted: the microphone is closed |
| microphone with a blue speaker | speaking, or playing music |
| amber | waiting for approval |
| grey | offline, refused, or the server failed the identity check (hover for why), or the satellite isn't running |

The menu: the room and server, **Mute / Unmute listening**, **Volume
up/down**, **Which room is this?** (plays a chime), **Allow / Not now** for
anything waiting (ask me), **Open dashboard**, **Show logs folder**,
**About** (versions and your server's fingerprint), **Quit tray** (the
satellite keeps running) and, separately, **Stop satellite…** (asks first).

**No icon?** Plain GNOME (Debian's default) has no tray. The pop-ups still
come, and **Domovoi Satellite** in the app menu opens a small window with
the same controls and the same mic-live indicator. (Ubuntu's GNOME, KDE,
Xfce, Cinnamon and MATE show the icon.)

Mute closes the microphone itself, so your desktop's own "microphone in
use" indicator goes out too, and it stays muted across restarts until you
unmute.

### Without a desktop (a mini PC in a cupboard)

The installer notices there's no desktop and sets up a **headless**
satellite: its own account `domovoi-sat` (no login, no sudo) owns the
software under `/var/lib/domovoi-sat`, a hardened system service runs it,
and sound goes straight to the sound card (plain ALSA). **There is no
visible microphone indicator** on such a box; mute it from the command
line:

```
sudo -u domovoi-sat sh -c 'cd /var/lib/domovoi-sat/app && venv/bin/python -m satellite.client --status'
#   --mute, --unmute, --approval-code, --pending, --approve all, --decline all
```

Force either mode with `--mode desktop` or `--mode headless`.

### Sound

On a desktop the satellite uses the default speakers and microphone your
desktop has chosen (PipeWire or PulseAudio), and "turn it up" changes that
output's volume. Plug in a USB speakerphone and pick it in the desktop's
sound settings. If it cancels its own echo, you may also set `[device]
full_duplex = true` in `~/.domovoi/config.toml` (and restart): intercom
calls and chat mode then work for this room. Leave it off for built-in
speakers — a wrong "true" lets a call hear itself.

Sound with nobody signed in: the installer puts the account in the
`audio` group; for a laptop used only as a room, turning on automatic
sign-in for its account is the most reliable.

### Keeping it awake, and the battery

The installer's three questions:

* **Lid switch ignored** (`/etc/systemd/logind.conf.d/90-domovoi-satellite.conf`,
  from the next reboot). GNOME and KDE have their own "when the lid is
  closed" setting too; set it to do nothing.
* **Never sleep** (the sleep targets masked). The screen still turns off.
* **Charge limit** (80 % by default, `--charge-limit N`), where the laptop
  supports one (`charge_control_end_threshold`); a udev rule sets it again
  at every boot. Elsewhere, look for the setting in the firmware or the
  maker's tool.

The dashboard shows the battery in the room's health block and suggests a
charge limit when it sits at 98 % or more on the charger.

### What a laptop room can and can't do

It does what a Pi does with its voice: the wake word, answers, music
(through its speakers), timers and reminders, announcements, intercom
*into* it, wake-word recording and pushes, the live log, settings from the
dashboard. **Guests** on your Wi-Fi can control a laptop room's music while
it plays, like any other room's ([FAQ](FAQ.md#can-a-guest-change-the-music)).

It does not do Pi things: no LED ring, no mic-gain tuning, no Wi-Fi
self-healing, no clock setting (it keeps its own), no root plugin payloads.
The dashboard doesn't offer those for it, and the server never sends them.
No intercom calls *from* or chat mode on built-in speakers (no echo
cancellation) unless you set `full_duplex` for a speakerphone, above.

When the server's container guard is holding the satellite channel (the
dashboard's Containers page says why), install commands and code updates
wait until an admin clears it.

### Removing it

```
bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall          # keeps ~/.domovoi/config.toml
bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall --purge  # the settings too
# headless:  sudo bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall --purge
```

Run the copy in `/usr/local/lib/domovoi-satellite`, which belongs to root:
never the `install-pc.sh` inside the satellite's own folder, which the
satellite's account (and your server's updates) can change. A desktop
install is removed as its own account (it asks for your password), a
headless one with `sudo`.

It undoes what the installer changed (the service, linger if it turned it
on, the tray, the power settings it made, all from root's own record) and
nothing else; `--purge` removes the satellite's own files from
`~/.domovoi`, never a Domovoi server's, and on a headless box the
`domovoi-sat` account. Then **retire
the room in the dashboard** once it shows offline (Settings → Satellites →
the room → **Retire room**): that frees the name and removes the room's
music player, which the container guard would otherwise keep expecting.

### When it doesn't work

| What you see | Why, and what to do |
|---|---|
| "did not prove it is the one in the code" | The address answered, but not with your server's key: a different machine, or an old Domovoi. Make a new code from the dashboard you mean, and compare the fingerprint with **Settings → About**. |
| "not on the home network" | The address in the code is public or loopback. In the dashboard, tick **a different server address** and give the one your devices reach. |
| `sha256sum: WARNING: 1 computed checksum did NOT match` | Domovoi was updated since the command was made (or the download was tampered with). Make a new command. |
| Tray grey: "refused … paired to another device" | That room already has a satellite. Pick another name, or retire the old room in the dashboard first (or **Reset pairing** in its drawer if this device replaces it). |
| Tray grey: "failed the identity check" | The server at the saved address isn't yours any more (moved, or something else answers there). It never connects to it. Re-run the install command made by your dashboard. |
| It doesn't start: "no pinned Domovoi server identity" | A spare computer never trusts the first server that answers. Re-run the install command from the dashboard. |
| It doesn't start: "its settings … don't load" (the tray, or `--status`) | A value in `~/.domovoi/config.toml` it refuses, named in the message (an audio device name that is not a plain ALSA one, say). Fix it, then `systemctl --user start domovoi-satellite`: it stays stopped until then rather than retrying every few seconds. |
| No sound at the sign-in screen | See *Sound* above (automatic sign-in for its account). |
| Anything else | `journalctl --user -u domovoi-satellite -e` (headless: `sudo journalctl -u domovoi-satellite -e`), and the live log in the dashboard's room drawer. |

## Phones and tablets (room mode)

An old Android phone or tablet on its charger becomes a room through the
Domovoi app's **room mode**: scan the QR code from **add a spare device**.
The steps and what the phone holds are written up with the app (this
section is completed when room mode ships).

## Windows laptops

Not yet. Most old Windows laptops make a better room on Linux (above). A
Windows satellite will reuse the same engine and its local control channel
(docs/uml/satellite-control-fixture.json).
