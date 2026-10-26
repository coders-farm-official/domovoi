# Satellites on spare devices

A room doesn't need a Raspberry Pi. A spare **Linux laptop or mini PC** can
be one, a **Windows laptop or PC** too, an old **phone or tablet**
through the Android app's room mode, and an **old computer** you are done
with (a PC, a laptop, an Intel Mac) from a **USB stick** your server makes
([below](#old-computers-usb-stick)). Each speaks the same protocol to the
Domovoi server as a Pi, joins through the same approval code, and becomes a
room like any other: wake word, answers, music, intercom, timers.

**Spare devices as rooms are a preview** in Domovoi 1.0, labelled so on the
dashboard's **add a spare device** card and in the app: they work, but have
been tried on fewer devices than a Pi, and may still change.

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

1. In the dashboard, as admin: **Settings → Rooms & devices → Rooms → add a spare
   device**. Type the room's name ("Kid's Room") and press **make the
   code**. The page shows the room id it will connect as
   (`kids_room`), a **one-line install command** for Linux, a **Setup to
   download** for Windows (with a short *setup code* that stands for the
   enrolment code, and a PowerShell command under "advanced"), a **QR
   code** for a phone, and **Prepare the stick** for an old computer. All
   of them carry the same short *enrolment code*:
   `domovoi-room:1?core=…&fp=…&room=…` — your server's address, its
   fingerprint, and the room's name. **Nothing in it is a password.** The
   QR code wraps it in a link to your server's own join page
   (`http://<server>:6370/join#domovoi-room:1?…`), so a phone's ordinary
   camera can open it: the page hands the phone the Domovoi app and opens
   it as the room (the code after `#` stays in the phone; no server sees
   it).
2. On the device, run the command (or scan the code).
3. The device finds your server, checks it is *your* server (it must prove
   it holds the key behind the fingerprint), and asks to join. It shows a
   six-digit **approval code** on its screen.
4. Back in **Settings → Rooms & devices → Rooms**, the device is under **waiting for
   approval**: type its code and press **approve**. That is the moment it
   becomes a room.

The address in the code must be on your home network (a private address,
or a name ending in `.local`, `.home.arpa`, `.lan` or `.internal`): a code
pointing anywhere else is refused, so nobody can send a room's microphone
out of the house with a code on a web page. If the dashboard says the
address it found isn't usable (a server with several network cards), tick
**a different server address** and give the one your devices reach.

## Linux laptops and mini PCs

Tested on **Debian 11, 12 and 13**, **Ubuntu 22.04 and 24.04** and **Linux
Mint 21**, with a desktop (GNOME, KDE, Xfce, …) or without one. **Fedora**,
**openSUSE** and **Arch** work too: the installer knows their package
names (it was tried in their containers, not on their laptops). Other
Debian-family systems (Pop!_OS, elementary, …) will probably work.
x86-64 and 64-bit ARM, with a GNU C library from 2018 or later (glibc 2.28:
Debian 10, Ubuntu 18.10, Fedora 29 and newer). An Intel or AMD processor
from about 2009 or later (x86-64-v2: Intel Core i3/i5/i7 and later, AMD
from 2011; not a Core 2 or an Athlon 64): the satellite's maths library
needs it, and the installer says so before it downloads anything. An older Debian or Ubuntu
needs nothing extra: see [Older Linux, and Fedora, openSUSE,
Arch](#older-linux-and-fedora-opensuse-arch) below.

**An old Windows 10 laptop** can be a room as it is
([Windows](#windows-laptops-and-pcs), below). One that can't run Windows 11
and will only ever be a room makes a better one on Linux: write the Debian
or Ubuntu installer to a USB stick
(their download pages explain how), boot from it, and install. Create a
user account just for the satellite while you're at it.

### 1. Make the command

As above: **Settings → Rooms & devices → Rooms → add a spare device**, the room's
name, **make the code**, **copy the command**. It looks like this (one
line):

```
d=$(mktemp -d) && { wget -qO "$d/s.tgz" http://192.168.1.20:6370/v1/satellite-code/bundle.tar.gz || curl -fsSLo "$d/s.tgz" http://…; } && echo '<sha256>  '"$d/s.tgz" | sha256sum -c --quiet - && tar -xzf "$d/s.tgz" -C "$d" && bash "$d/satellite/scripts/install-pc.sh" 'domovoi-room:1?core=…&fp=…&room=Kid%27s%20Room'
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
fingerprint under **Settings → System → About & updates**, looked at on another device
(your phone).

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
4. installs the few packages it needs (PortAudio, `mpg123`, the ALSA
   utilities, with a desktop `pactl`; on Debian and Ubuntu with their own
   Python also `python3-venv` and, with a desktop, `python3-tk`), with
   apt, dnf, zypper or pacman;
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
mode.

About ten minutes on a fresh install, mostly downloads.

### Older Linux, and Fedora, openSUSE, Arch

The satellite needs Python 3.11 or newer. A **Debian or Ubuntu that has
it** (Debian 12 and 13, Ubuntu 24.04 and later, Mint 22) uses its own.
**Everything else gets the satellite's own Python**, with nothing for you
to do: Ubuntu 22.04 and Mint 21 (their Python is 3.10), Debian 11 (3.9),
and Fedora, openSUSE and Arch whatever their Python. The installer says
which, before it starts:

```
Ubuntu 22.04.5 LTS: its Python is 3.10.12, older than the 3.11 the satellite needs.
This computer gets the satellite's own Python 3.14.8 (36 MB, pinned by its SHA-256)...
  from the Domovoi server
```

* **Which Python.** CPython 3.14.8 on x86-64 and 3.13.16 on 64-bit ARM,
  built by the python-build-standalone project (Astral) to run on any Linux
  with glibc 2.17 or newer. The exact file is pinned by its size and SHA-256
  in `satellite/locks/linux-python.txt`; the installer checks it before it
  unpacks anything, and refuses any other.
* **Where it comes from.** Your Domovoi server, if an admin pressed **get
  the Linux Python** on the add-a-spare-device card (66 MB for both
  kinds of computer, once); otherwise the laptop downloads the same file
  from GitHub itself. The satellite's libraries come from PyPI either way,
  as on any laptop.
* **Where it goes.** `/usr/local/lib/domovoi-satellite/python`, owned by
  root, beside root's copy of the uninstaller: the satellite's account can
  use it but not change it, as with the system's own `/usr/bin/python3`.
  Uninstalling removes it.
* **What's in it, licence-wise.** CPython (PSF licence) and permissive
  libraries only: OpenSSL (Apache-2.0), libffi, expat, libX11 (MIT), SQLite
  (public domain), zlib, bzip2, xz, zstd, mpdecimal, libuuid, ncurses,
  libedit (in place of GNU readline), Tcl/Tk. No GNU readline and no GDBM.
  Its `dbm` module's Berkeley DB (a copyleft licence) is deleted at install;
  the satellite never uses it.
* **Updates.** Your system's package manager doesn't update it. A Domovoi
  update can pin a newer one; it reaches a laptop when you make a new
  command and run it again (the settings and the approval are kept).
* `--python system` or `--python pinned` forces the choice (`system` only
  on Debian and Ubuntu with 3.11 or newer).

The **system packages** still come from the computer's own package
manager: apt on the Debian family, dnf on Fedora, zypper on openSUSE,
pacman on Arch (bring an Arch system up to date with `sudo pacman -Syu`
first). On any other Linux the installer stops and lists what to install;
install PortAudio, mpg123, the ALSA utilities and (with a desktop)
`pactl` yourself, then run the command again with `--skip-packages`.

Two systems it refuses, saying why: a **Debian upgraded from 10 or older
without merged /usr** (its programs still in `/bin`; `sudo apt install
usrmerge` converts it, and Debian needs that before its next upgrade
anyway), and one with a C library older than glibc 2.28 or no glibc at all
(Alpine). **Debian 11's support ended in August 2026**: its packages still
install from Debian's archive for now, but it gets no more security
updates, so prefer Debian 12 on a laptop you reinstall anyway.

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
sudo (see "What the satellite can do as your account", below). If you'd
rather keep it apart, use a **dedicated account** and take its sudo away
once the install is done (`sudo deluser <account> sudo`), or the
**headless** mode below, whose account has neither sudo nor a login. **Ask me** suits a computer someone also uses: nothing new runs
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

In the account the satellite runs as:

```
bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall          # keeps ~/.domovoi/config.toml
bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall --purge  # the settings too
# headless:  sudo bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall --purge
```

It asks for your password for the last steps (linger, the power settings).
If you gave the satellite a **dedicated account**, remove it from your own
administrator's account instead, naming that account:

```
sudo bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall --purge --account <its account>
```

Run the copy in `/usr/local/lib/domovoi-satellite`, which belongs to root:
never the `install-pc.sh` inside the satellite's own folder, which the
satellite's account (and your server's updates) can change.

**What the satellite can do as your account.** A desktop satellite runs as
the account you installed it in, with that account's rights: with
"automatic", code your server sends runs as you, and could do whatever a
program you run can — including use sudo the next time you type your
password in that account. That is the same trust you give any program you
install, and for most homes the simple choice is your everyday account.
If you want the satellite kept apart from it, make a dedicated account,
take its sudo away after the install, never type an administrator's
password into it, and uninstall from your administrator's account with
`--account`, as above.

It undoes what the installer changed (the service, linger if it turned it
on, the tray, the power settings it made, all from root's own record) and
nothing else; `--purge` removes the satellite's own files from
`~/.domovoi`, never a Domovoi server's, and on a headless box the
`domovoi-sat` account. Then **retire
the room in the dashboard** once it shows offline (Settings → Rooms & devices → Rooms →
the room → **Retire room**): that frees the name and removes the room's
music player, which the container guard would otherwise keep expecting.

### When it doesn't work

| What you see | Why, and what to do |
|---|---|
| "did not prove it is the one in the code" | The address answered, but not with your server's key: a different machine, or an old Domovoi. Make a new code from the dashboard you mean, and compare the fingerprint with **Settings → System → About & updates**. |
| "not on the home network" | The address in the code is public or loopback. In the dashboard, tick **a different server address** and give the one your devices reach. |
| `sha256sum: WARNING: 1 computed checksum did NOT match` | Domovoi was updated since the command was made (or the download was tampered with). Make a new command. |
| "neither the Domovoi server nor GitHub gave the pinned Python" | This computer needs the satellite's own Python and couldn't get the exact file: your server hasn't fetched it and the laptop can't reach GitHub (or something on the way changed it). On the dashboard's add-a-spare-device card press **get the Linux Python**, or connect the laptop to the internet, and run the command again. |
| "this computer's processor is too old for Domovoi" | The processor is from before about 2009 (a Core 2, an Athlon 64, a first Atom): the satellite's maths library (numpy) can't run on it. Nothing was installed. A Raspberry Pi satellite or a newer spare computer will do. |
| "keeps its programs in /bin and /sbin" | A Debian upgraded from version 10 or older without merged /usr. `sudo apt install usrmerge`, then the command again. |
| "installs its few packages with apt … dnf … zypper … pacman" | A Linux the installer doesn't know. Install PortAudio, mpg123, the ALSA utilities and (with a desktop) `pactl` with its own package manager, then run the command again with `--skip-packages` added at the end. |
| Tray grey: "refused … paired to another device" | That room already has a satellite. Pick another name, or retire the old room in the dashboard first (or **Reset pairing** in its drawer if this device replaces it). |
| Tray grey: "failed the identity check" | The server at the saved address isn't yours any more (moved, or something else answers there). It never connects to it. Re-run the install command made by your dashboard. |
| It doesn't start: "no pinned Domovoi server identity" | A spare computer never trusts the first server that answers. Re-run the install command from the dashboard. |
| It doesn't start: "its settings … don't load" (the tray, or `--status`) | A value in `~/.domovoi/config.toml` it refuses, named in the message (an audio device name that is not a plain ALSA one, say). Fix it, then `systemctl --user start domovoi-satellite`: it stays stopped until then rather than retrying every few seconds. |
| No sound at the sign-in screen | See *Sound* above (automatic sign-in for its account). |
| Anything else | `journalctl --user -u domovoi-satellite -e` (headless: `sudo journalctl -u domovoi-satellite -e`), and the live log in the dashboard's room drawer. |

## Phones and tablets (room mode)

An old phone or tablet on its charger can be a **room**: it listens for the
wake word, sends what you say after it to your Domovoi server, speaks the
answer, plays the room's music and takes drop-ins and announcements, like a
Raspberry Pi satellite. It runs inside the normal Domovoi Android app as
**room mode**, with its own home-screen icon, **Domovoi Room**.

### What you need

| | |
|---|---|
| A phone or tablet | Android 8.0 or later, 32-bit or 64-bit (the join page offers the app for most phones first and the 32-bit one, for older or cheaper phones, under it: [ANDROID_COMPATIBILITY.md](ANDROID_COMPATIBILITY.md)). About 1-2 GB of RAM is enough; room mode loads none of the on-device assistant's libraries. |
| The Domovoi app | From your own server: the QR code's page downloads it from there. The server gets the release named in its `android/release.json` from the project's GitHub release once, when you press **Download the app to this server** on the card (about 130 MB; the first download is never by itself, and until the app is on the server the card shows that step instead of the phone's QR code), or you **upload an APK** of that release on the card when internet access is off; it hands out a file only when it is exactly that release's, signed by the project's release key. After an update brings a newer release, the release already on the server keeps serving until the new one is here, and a server whose internet answer is **Yes, always** fetches it by itself, once per start with retries; under **Sometimes**, **No** or no answer it waits for an admin's press. Until the first app release is published, the card and the page say "no app release yet". |
| Its charger | A room listens all day. Turn on the phone's battery-protection (charge limit) setting if it has one, and never use a phone whose battery has swollen. |
| Your home Wi-Fi | Room mode uses Wi-Fi (or a wired dock) only, never mobile data: with no Wi-Fi it is offline. |
| The server's wake-word models | The phone runs the same openWakeWord models as a Pi, fetched from your server. On the dashboard, **Settings → Rooms & devices → Rooms → prepare a room's SD card → Refresh caches** once (it downloads them; it needs the internet that one time). A Pi you prepared already did this. |
| A room code | On the dashboard, **Settings → Rooms & devices → Rooms → add a spare device**: a QR code (a link to your server's join page with the code in it) and a line of text that starts `domovoi-room:1?…`. It names your server, the server's key and the room. It holds no secret. |

### Setting it up (about ten minutes)

1. On the spare phone (factory-reset it first if you can: it becomes a
   shared device), on your home Wi-Fi, **scan the QR code with the
   phone's camera**. It opens a page on your Domovoi server.
2. **Install the app** from that page: **download the app** (or, on an
   older or cheaper 32-bit phone, the 32-bit one under it; if the phone
   says *App not installed*, take the other). The first time, the browser
   warns about this kind of file (**Download anyway**) and Android asks to
   allow installs from it (**Settings → Allow from this source**, then
   back); then **Install**. Play Protect may offer to scan the app: let
   it. The page shows these steps in pictures. A phone that already has
   the app skips this.
3. Back on the page, press **open as this room**. It opens **Domovoi
   Room** with the room code filled in. (Or open **Domovoi Room** from the
   home screen and scan the code there; on a phone that already uses the
   app with your household: Settings → Rooms & devices → Rooms → **Use this device as
   a room**.)
4. Read the explainer to the end and press **Start room mode**. It says
   what room mode means: always listening, a shared device, power and
   heat, Android's rules, what stops working, how to leave.
5. Allow the **microphone** (and, on Android 13+, notifications).
6. The room code is already filled in when the page opened the app;
   otherwise **scan the room code** (the camera is asked for only now), or
   paste the text. The phone shows the server's address and the start of
   its key: compare the key with the dashboard's Settings → System → About & updates. Change
   the room name if you like, then press **Become this room**.
7. The room screen shows **waiting for approval** and a six-digit code. On
   the dashboard, Settings → Rooms & devices → Rooms shows an approval card for the
   room: type the code the phone shows and approve. Within about half a
   minute the phone says **Say "Hey Jarvis"** (press **check now** to skip
   the wait).
8. Say "Hey Jarvis, what time is it?".

A household phone (not a room) gets the app the same way: **Settings →
Rooms & devices → Phones & browsers → get the Domovoi app** shows a QR code and a link to the same
page without a room code; open it on the phone, install, then open
Domovoi, which finds your server on the Wi-Fi.

The wake word is the server's default, `hey_jarvis`; a wake word you train
and push from the dashboard (Settings → Voice → Wake words) reaches the phone the
same way it reaches a Pi.

### The room screen

A dark full-screen display, deliberately unlike the app: the room's name,
a clock, a ring that says what the room is doing — **idle**, **listening**,
**thinking**, **speaking**, **muted**, **offline**, **mic blocked** — the
words under it, and:

- **the mic chip** (top right), always visible: red **mic live** while what
  the room hears is going to the server (a command, or a drop-in), plain
  **mic** while it only listens for the wake word, struck-through **mic
  off** when muted;
- **mute**: closes the microphone (Android's green dot goes off). Anyone in
  the room can mute it, from the lock screen too;
- **exit room mode**: hold it, then unlock with the phone's PIN, pattern or
  fingerprint, and choose to **keep this room** (come back to it later) or
  **forget this room** (its key is deleted from the phone). A phone used
  only as a room may have no screen lock: Exit then asks for a
  confirmation, which anyone can give. A phone that is also paired with
  your household **needs a screen lock to be a room**: without one room
  mode does not start, and if the lock is removed while it runs the room
  stops listening and asks for one.

The screen moves its contents a few pixels every minute so a display left
on does not burn in. It is shown over the lock screen.

### What a phone room does

| | |
|---|---|
| Wake word, commands, answers, follow-up questions | yes (wake word on the phone, speech-to-text and everything after it on the server, as for a Pi) |
| The wake acknowledgement | a greeting clip from the server, or a chime when music was playing; what the mic hears under it is dropped |
| Early commit ("pause the music" answered at once) | yes |
| Timer, reminder and intercom announcements | yes |
| The room's music | yes, in its own player (not the app's); paused for an answer, resumed by the server after |
| Volume ("turn it up") | yes: the phone's media volume |
| Drop-ins | on phones with an echo canceller only (most phones; the app checks). The room says so to the server, which refuses a drop-in otherwise |
| Chat mode | no (not in this version) |
| Restart, upgrade, live log, wake-word clip recording on the dashboard | no: the app updates from its store or a new APK; the dashboard greys these out for an "Android room" |
| Settings tab | only the two battery switches below; everything else is set on the phone |

The dashboard shows the room with an **Android room** badge, its battery
and % on the card (with a charging marker), and its health block shows the
battery and **mic blocked** when Android is handing the room silence.

### The battery (2026-10-25)

Keep the phone on its charger: rooms are always on. The phone sends its
battery with its health sample once a minute, and again when the charger
goes on or off or the level moves 5 % (1 % under 20 %, off its charger). Off its charger, at **20 %** and
again at **10 %**, once per charge (`domovoi/room_battery.py`):

- Home and Health → Rooms show "Pako's battery is low (15%) · plug it in",
  and the Domovoi app on the household's phones notifies "Pako is at 15%.
  Plug it in." (**Room batteries** channel). The notification goes away
  once the room charges.
- The room itself says "This is Pako. My battery is low. Please plug me
  in." Not during the room's quiet hours (Settings → Advanced → Voice & speech, or
  the room's Settings tab); then only the notice and the notification go.
- A phone room that went offline after a last report at 5 % or less, off
  its charger, reads **probably out of battery** instead of a plain
  offline line.

The room's **Settings** tab has **Say when the battery is low** and
**Notify when the battery is low**, both on by default, kept on the server
(V040) and read at each warning. Pis and laptops have none of this.

### Android's rules

- **A notification stays** while the room listens ("Domovoi Room ·
  kitchen"), with **Stop listening**, which turns the microphone off but
  does not leave room mode (the normal app stays locked until someone
  resumes or exits on the room screen). On Android 12 and later it asks for
  the phone's unlock first; on older Android anyone who can open the
  notifications can press it. The **green microphone dot** stays on. Both are expected.
- **After a restart** Android lets no app start listening by itself: the
  phone shows **"…is not listening — tap to resume"**. One tap.
- **Battery optimisation**: turn it off for Domovoi (the explainer has a
  button for the settings page), or Android may put the room to sleep.
- **The microphone privacy toggle** (Android 12+, quick settings) makes
  the room deaf: the ring says **mic blocked**, and so does the dashboard.
  A phone call, or another app using the microphone, does the same until
  it ends.
- **Chromebooks**: keep the lid open, plugged in, with sleep off.

### A phone that is also your household phone

You can turn your own phone into a room for a while. Then:

- the normal app is **locked** (no files, browsing, chat, assistant)
  until you exit room mode with the phone's unlock;
- the app's **household pairing stays on the phone but is not used**: no
  request carries it while room mode is on, the app's player stops and its
  live connection to the server closes (timer alerts for the household
  stop reaching this phone until you exit);
- the room has its **own key**, made on the phone, and the server knows
  the room by it alone. Removing the room on the dashboard and forgetting
  the phone as a household device are two separate things.

### When something is wrong

| The room screen says | What to do |
|---|---|
| waiting for approval | Approve it on the dashboard with the code on the phone. |
| Another device is this room on the server | That room is paired with another device. On the dashboard, open the room and reset its pairing, or hold Exit, forget this room and choose another name. |
| This room was removed on the dashboard | The room was removed (or its pairing reset) after it had been approved. Approve it again with the code shown, or hold Exit and forget it. |
| Not listening: … needs a screen lock | This phone is also paired with your household. Set a PIN, pattern or password; the room starts again. |
| Another device is already waiting for approval as this room | Reject the other request on the dashboard, or wait three minutes. |
| …this is a different server… / …would not sign for… | The address in the room code answered with a different key, or something is relaying it. Make a new room code on the right server. A server reached through a name or a forwarded port needs that name in its `TRUSTED_HOSTS`. |
| The server has no wake-word models yet | Dashboard: Settings → Rooms & devices → Rooms → prepare a room's SD card → Refresh caches. |
| The server does not hand wake-word models to phones yet | Update the Domovoi server. |
| waiting for Wi-Fi | The phone has no Wi-Fi. Room mode never uses mobile data. |
| (on the room code screen) … is not on the home network | The room code names a server outside your home network. A room only talks to a server at a private address (10.x, 172.16-31.x, 192.168.x, IPv6 fc00::/7 or fe80::/10) or a name ending in `.local`, `.home.arpa`, `.lan` or `.internal`; not loopback, not 100.64.x (carrier NAT), not 169.254.x. |
| mic blocked | Turn the microphone back on in quick settings, or end the call. |
| Not listening. Tap Resume | Someone pressed Stop listening, or the phone restarted. |

### For developers

The code is `android/app/src/main/java/com/domovoi/app/roommode/`; how it
is built and tested is in [android/README.md](../android/README.md#room-mode),
and what the phone holds in [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#a-room-phone-android-room-mode).

## Windows laptops and PCs

A Windows 10 or 11 laptop or PC (64-bit, Intel or AMD, a processor from
about 2009 or later, as for Linux) can be a room for
**the account that is signed in on it**: the satellite runs in that
person's session, because that is where Windows lets a program use the
microphone. Nothing is installed for other accounts, nothing needs an
administrator, and **it stops when that account signs out** (it starts
again at the next sign-in). A laptop that is only a room works best with
an account of its own that signs in automatically.

**Not on the PC that runs your Domovoi server.** The installer refuses
there (it looks for the server's Windows service, its install record, its
data folder, a server's own files in your account, and a server answering on
this PC). Using the server PC as a room will come with the server's own
tray icon instead; today that icon shows Domovoi's state, opens the
dashboard and prepares Raspberry Pi satellite cards
([WINDOWS_HOST.md, The tray icon](WINDOWS_HOST.md#the-tray-icon)).

**It brings Microsoft's Visual C++ runtime with it.** The wake word's
onnxruntime needs `msvcp140.dll` and `msvcp140_1.dll` (14.44 or newer) and
does not carry them, and Windows doesn't always have them. So the installer
puts Microsoft's own copies **beside the satellite's Python** — unmodified,
Microsoft-signed files, taken from the `msvc-runtime` package on PyPI and
each checked against its SHA-256 in `windows-runtime.json`; nothing is
installed for the rest of the PC, and no administrator is needed. **These
copies are updated with Domovoi's lock, not by Windows Update.** If they
have gone missing, the satellite says so at start (and how to put them
back: paste the command again, or install Microsoft's own
[redistributable](https://aka.ms/vc14/vc_redist.x64.exe)).

**Smart App Control** (Windows 11) checks every program file, DLLs
included, and some of the satellite's libraries are not signed yet (its
Python and onnxruntime are). The installer reads its state (never changes
it) and says so when it is **on** or in **evaluation**. With it on, Windows
may block part of the satellite, with no "run anyway": the satellite then
doesn't start, or Windows says it "blocked part of this app". The only way
round it is turning Smart App Control off (**Windows Security → App &
browser control → Smart App Control settings**); read what Windows Security
says first — on many Windows 11 builds it can only be turned back on by
resetting Windows.

**Unsigned, for now.** Coders Farm's signing is on hold, so neither the
Setup nor the satellite's own code carries a signature. Python itself,
which runs it, is signed by the Python Software Foundation. So Windows
SmartScreen stops the Setup once, with **"Windows protected your PC"**
(what to press is below), and Windows Security or another antivirus may
look twice at a program that downloads and starts programs. Install it
only on a PC whose owner agrees. When signing comes back, the same Setup
is signed and the SmartScreen step goes away.

There are two ways in: **the Setup** (a program you double-click: the
way for most households) and, under [Advanced](#advanced-the-powershell-command),
a one-line PowerShell command. Both install exactly the same thing.

### 1. Get the Setup

**Settings → Rooms & devices → Rooms → add a spare device**, the room's name, **make
the code**. Under **windows laptop or pc** the card shows:

- a **setup code**, like `K7M4-QX2P-9TRA`, and your **server**'s address.
  The code works for **24 hours**, for this room only (make a new one after
  that, or after the server restarts). It is **not a password**: it tells
  the Setup which server and which room, the PC still has to be approved
  with its own six-digit code (step 3), and the Setup checks that the
  server answering is really the one the code was made on (the code is
  made from that server's fingerprint and the room);
- **Download the Windows Setup**, and a short address to type into the
  PC's browser instead, like `http://192.168.1.20:6370/win/K7M4-QX2P-9TRA`.

The file comes **from your own Domovoi server**, over your home network,
and its name carries the code and the server
(`DomovoiSatelliteSetup_K7M4QX2P9TRA_192.168.1.20_6370.exe`), so the Setup
fills them in by itself: don't rename it (if you do, it just asks for
both).

**Where your server gets the Setup.** A server installed with Domovoi's
Windows Setup has it already: `DomovoiSetup.exe` carries the satellite
Setup of the same version and puts it where the dashboard finds it, so it
works with no internet at all. A **Linux** server downloads it once from
Domovoi's release on GitHub: when a release carries it, the card offers
**get the Windows Setup** (a few MB, checked against that release's
`SHA256SUMS`), and every Windows PC in the house then gets it from your
server. Until a release carries it (the first is the release after
1.0.0-rc3), or under `INTERNET_ACCESS=never`, the card shows the
PowerShell command first, which installs exactly the same thing.

The card may also say the server doesn't hold the **Windows files** yet
(Python and its libraries, about 96 MB): press **get the Windows files**
and the server downloads them from python.org and PyPI, each file checked
against the SHA-256 pinned in the satellite code. If you don't, the PC
downloads the same pinned files from there itself.

### 2. Run the Setup

On the PC, signed in as the person whose account it will run in:

1. **Download it.** Your browser may say the file "isn't commonly
   downloaded" or "can't be downloaded securely": it comes over your home
   network without https. Choose **Keep** (in Edge: the **…** beside the
   download, then **Keep**, then **Keep anyway**).
2. **Open it.** Windows shows a blue box, **"Windows protected your PC:
   Microsoft Defender SmartScreen prevented an unrecognized app from
   starting"**. Click **More info**: it shows the app's name
   (`DomovoiSatelliteSetup_…`) and *Publisher: Unknown publisher*. Click
   **Run anyway**. Why: Domovoi isn't signed by Microsoft yet, so Windows
   doesn't recognise it; the file comes from your own Domovoi server. No
   "Do you want to allow this app to make changes" question follows: the
   Setup needs no administrator rights (and stops if you start it with
   **Run as administrator**).
3. **Follow it.** The code and the server are filled in; it asks the
   server which room the code is for, and the server has to **prove** it
   is the one the code was made on (it signs a fresh challenge with the
   key behind its fingerprint, which is part of the code); if anything
   else answers, Setup stops before it downloads anything. It then shows
   the room, the code (check it is the one on the dashboard) and your
   server's **fingerprint** (the same as **Settings → System → About & updates**). Then two
   choices:
   whether the server may **update and restart the satellite on its own**
   ("Ask me first" or "Automatic", [below](#automatic-or-ask-me-on-windows))
   and, while plugged in, **never sleep** and **keep running with the lid
   closed** (both unticked unless you tick them; see the PowerShell steps
   below for what they change). **Install** does the rest, the same steps
   as the command: it downloads the satellite from your server and runs
   nothing unless it is exactly what the server signed for that code,
   refuses the PC that runs your Domovoi server, makes your server prove
   its fingerprint before anything is written, and installs into
   `%LOCALAPPDATA%\Domovoi\Satellite` for this account only.
4. Its last page shows the six-digit **approval code** (or, if it has
   not arrived yet, where to find it). The Setup itself leaves nothing
   behind: no files, no Installed apps entry of its own. The satellite
   it installs has its own entry, **Domovoi Satellite (room)**.

A few minutes, mostly downloads. It takes about 350 MB on disk.

For admins: the Setup also runs silently, `DomovoiSatelliteSetup.exe
/VERYSILENT /SERVER=192.168.1.20 /CODE=K7M4-QX2P-9TRA /PERMISSIONS=ask
/POWER=no` (`/POWER=standby`, `lid` or `yes` for the power choices).

### 3. Approve it

The code is on the Setup's last page (or in the PowerShell window), in a
notification ("Kid's Room: waiting for approval, code 123456"), in the
tray icon's tooltip and its menu. Type it into the dashboard's approval
card; the notification then says **Approved**. It is never written to the
satellite's log.

### Advanced: the PowerShell command

The same install, without the Setup: for someone who would rather read
what runs, or a PC where a program from the network is not allowed to
start. Under **windows laptop or pc**, open **advanced: the PowerShell
command instead of the Setup** and **copy the Windows command**.

On the laptop, signed in as the person whose account it will run in, open
**Windows PowerShell** (or Terminal) — a normal window, **not "Run as
administrator"**: the installer refuses an elevated window — paste the
command and press Enter. It:

1. downloads the satellite software from your server into a new folder in
   `%TEMP%` and runs nothing unless it is **exactly** the bundle the
   dashboard quoted (its SHA-256 is in the command);
2. refuses if this PC runs the Domovoi server;
3. gets the pinned Python and checks it, then makes your server **prove
   the fingerprint in the code** before it writes anything — if it can't,
   nothing is installed;
4. asks **what the server may do on its own** ("automatic" or "ask me",
   the same as on Linux, below);
5. installs into `%LOCALAPPDATA%\Domovoi\Satellite` (or an empty folder
   inside your own user folder, `-InstallRoot`; it sets that folder's
   permissions to you, SYSTEM and Administrators): Python, its libraries
   (each by its SHA-256, nothing resolved on the laptop), the satellite,
   the wake-word models (checked against their pins), and its settings in
   `.domovoi\config.toml` with your server pinned;
6. registers it **for this account only**: it starts at sign-in
   (**Settings → Apps → Startup** lists it under Python's name and can
   turn it off), it is in **Installed apps** as *Domovoi Satellite (room)*, and
   **Domovoi Satellite** is in the Start menu (it starts the satellite and
   its tray icon again after you stopped them);
7. asks, one at a time, about **keeping it awake while plugged in**: never
   sleep, and keep running with the lid closed. Each defaults to **No**,
   only the plugged-in setting changes (on battery it still sleeps). The
   change is to the PC's **active power plan, which every account on this
   PC shares**; the uninstaller puts the exact values back in that plan
   (and does not switch plans if you have chosen another since). A
   standard account may not be allowed to change them; the installer says
   so and carries on;
8. starts it and waits for you to approve it, showing the **approval
   code**.

A few minutes, mostly downloads. It takes about 350 MB on disk.

-ExecutionPolicy Bypass in the command applies to that one PowerShell
process: the PC's script policy is never changed. A policy your
organisation set through Group Policy still wins, and then the installer
can't run.

### What warnings you may see

| What | Why |
|---|---|
| Windows Security or another antivirus warns about, or blocks, PowerShell downloading and starting programs | The command does exactly that, and the satellite's code is unsigned. If it is blocked, the antivirus's history says what; allow it only if the command came from your own dashboard. |
| The microphone icon in the taskbar stays on | The satellite listens for the wake word all the time; Windows shows that any program has the mic open. Hovering it names *Python*. The satellite's own tray icon says whether audio is leaving for the server (below). |
| "Let desktop apps access your microphone" is off | Then Windows gives the satellite silence and the wake word never fires. Turn it on in **Settings → Privacy & security → Microphone**. |
| A "Windows protected your PC" (SmartScreen) window | Expected once, for the Setup: it is not signed by Microsoft yet. **More info**, then **Run anyway** (step 2). The PowerShell command starts nothing that was downloaded through a browser, so it shows none; if one appears for *Domovoi Satellite* from the Start menu, it is the shortcut to Python's own `pythonw.exe`. |

### The tray

The same icon and menu as on Linux (see [The tray](#the-tray) above): a
**filled red microphone while audio is leaving for your server**, an
outline while it only listens for the wake word, struck through when
muted, a speaker badge while it speaks or plays, amber while it waits for
approval, grey when it is offline, refused, unpinned or not running (the
tooltip says why). **Allow** on a waiting change first lists *everything*
it changes and asks once more. **Stop satellite…** asks first; afterwards
the menu offers **Start satellite**. **Quit tray** leaves the satellite
running; **Domovoi Satellite** in the Start menu brings the tray back.

Without the tray, from PowerShell:

```
& "$env:LOCALAPPDATA\Domovoi\Satellite\python\python.exe" -m satellite.client --status
#   --mute, --unmute, --approval-code, --pending, --approve all, --decline all
```

### "Automatic" or "ask me" on Windows

As on Linux (the table above). Either way the satellite runs as the
signed-in account with that account's rights and nothing more: no
administrator, no service, no scheduled task. With "automatic", your
server's key can run code as that account — which is why a dedicated
account is the better choice for a laptop that is only a room. Change it
later in `%LOCALAPPDATA%\Domovoi\Satellite\.domovoi\config.toml`
(`[pc] permissions`), then stop and start the satellite from the tray.

### Sound on Windows

It uses the microphone and speakers Windows has chosen (**Settings →
System → Sound**), and "turn it up" moves Windows' own volume — the same
slider the taskbar shows. To pin other devices, put a device number or
part of a device's name (as `python.exe -m satellite.client
--list-devices` prints it) in `[audio] input_device` / `output_device`
and `[music] alsa_device`. Only plain names are accepted there (letters,
digits, spaces and `()._,:+[]-`): the dashboard's Settings tab can't send
anything else, and a satellite whose `config.toml` holds anything else —
or any value it doesn't know — **doesn't start**, like a Linux laptop: the
tray says *Domovoi satellite didn't start* with the line to fix. Fix it,
then choose **Start satellite** in the tray menu. (Only a Pi, which has no
keyboard, starts on its board's default instead and says so.)

### Battery and power

Windows has no general charge-limit setting: use the laptop maker's tool
(often called *battery health*, *conservation* or *charge threshold*
mode) to stop charging around 80 % for a laptop that lives on the charger.
The dashboard shows the battery in the room's health block.

### When it moves, and the network

The satellite finds your server at the address in the code, and if that
stops answering, by your server's name (`domovoi.local` and the names it
announced). A Windows satellite never sweeps the home network looking for
it — to security software that looks like a port scan. Set
`[pc] server_search = "sweep"` to allow it, or `"address"` to use only
the address.

### Removing it

**Settings → Apps → Installed apps → Domovoi Satellite (room) →
Uninstall**, or from PowerShell:

```
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Domovoi\Satellite\uninstall.ps1"           # keeps its settings (.domovoi)
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Domovoi\Satellite\uninstall.ps1" -Purge    # the settings too
```

It stops the satellite and its tray, removes the sign-in entry, the
Installed apps entry and the Start-menu shortcut (each only if it points
into its own folder), puts back the power settings it changed, and
deletes what it installed — by name, nothing it doesn't recognise. Its
settings (`.domovoi`, with the room's pairing key) stay unless `-Purge`,
which removes only the satellite's own files there. Then **retire the room
in the dashboard** once it shows offline, as on Linux.

The installer goes only into an empty folder or an earlier install of
itself, and refuses an account where a Domovoi server keeps its files.

### What it installs, and their licences

Everything comes from your server's cache or from upstream, each file
checked against the SHA-256 in `satellite/windows-runtime.json`:

| Component | Licence |
|---|---|
| CPython 3.14 (embeddable package, python.org) | PSF-2.0; it bundles OpenSSL (Apache-2.0), libffi (MIT), SQLite (public domain), zlib-ng (Zlib), bzip2, xz (0BSD), zstd (BSD-3-Clause), expat (MIT), mpdecimal (BSD-2-Clause), HACL* (MIT) |
| numpy, scipy | BSD-3-Clause; their Windows wheels bundle OpenBLAS and LAPACK (BSD-3-Clause) and the GCC runtime (GPL-3.0-or-later **with the GCC Runtime Library Exception**, which permits exactly this use) — the same wheels every Pi gets |
| scikit-learn, joblib, threadpoolctl, cloudpickle, idna, websockets, protobuf, pycparser | BSD-3-Clause |
| onnxruntime, sounddevice (with PortAudio), miniaudio (with dr_mp3, public domain or MIT-0), webrtcvad-wheels, narwhals, charset-normalizer, urllib3, cffi (MIT-0) | MIT |
| openwakeword, requests, flatbuffers | Apache-2.0 |
| cryptography | Apache-2.0 or BSD-3-Clause |
| packaging | Apache-2.0 or BSD-2-Clause |
| colorama | BSD-3-Clause |
| tqdm | MPL-2.0 and MIT (unmodified) |
| certifi | MPL-2.0 (unmodified) |
| pip (used to install the others) | MIT |
| The Setup itself (`DomovoiSatelliteSetup.exe`, built with Inno Setup 6, as the server's Setup is) | Inno Setup's own licence: permissive (use for any purpose, commercial included, keeping its copyright notice); it installs no file of its own |
| Microsoft Visual C++ runtime (`msvcp140.dll`, `msvcp140_1.dll` 14.44, from the `msvc-runtime` wheel) | Microsoft's, not open source: the Visual Studio 2022 licence's "Distributable Code" ([REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution), Visual C++ Runtime Files) lets them be copied and distributed unmodified with a program — the same terms as the `vcruntime140.dll` Python itself ships |

Nothing GPL or LGPL is bundled: there is no mpg123 on Windows (MP3 is
decoded by dr_mp3), and sounddevice's ASIO-enabled PortAudio builds
(Steinberg's ASIO SDK) are deleted after install — they are never loaded
anyway. The wake-word models are openWakeWord's (their licence is the
same question as on every Pi; see the wake-word notes).

### When it doesn't work (Windows)

| What you see | Why, and what to do |
|---|---|
| The Setup says the server "does not know the code" | The code is more than 24 hours old, the server restarted since, or it was mistyped. Make a new code on the dashboard (the same room gets the same code again while it lives). |
| The Setup says "that is not the address of a Domovoi server on your home network" | Type the server as the card shows it (`192.168.1.20`, or `192.168.1.20:6400` for another port). The Setup talks only to an address on your home network (or this PC), never anywhere else. |
| The Setup says "no Domovoi server answered" | This PC is on another network (a guest Wi-Fi), or the server is off. |
| The Setup says the server "did not prove it is the Domovoi server this code was made on" | The address or the code is not the one on the dashboard, or another machine answers at that address. Check both on the dashboard (Settings → Satellites); nothing was downloaded or installed. |
| "This is not the download the dashboard expected" (or "the server signed for this code", in the Setup) | Domovoi was updated since the command was made (or the download was changed on the way). Make a new command. |
| "this window runs as administrator" (or the Setup says it runs as administrator) | Open a normal PowerShell window and paste it again; for the Setup, start it with a double-click, not **Run as administrator**. |
| "never installed on the PC that runs the Domovoi server" | This PC runs the server. Use another PC. |
| "did not prove it is the one in the code" | As on Linux: a different machine answers at that address. Compare the fingerprint with **Settings → System → About & updates**. |
| "running scripts is disabled on this system" | A Group Policy forbids scripts here; the installer can't run on this PC. |
| "Microsoft's Visual C++ runtime is not on this PC" / "is older than 14.44" (the tray) | The copies installed beside its Python are gone. Paste the command again (it puts them back), or install **Microsoft Visual C++ Redistributable (x64)** from <https://aka.ms/vc14/vc_redist.x64.exe> (an administrator, once); then **Start satellite** in the tray. |
| "Windows blocked part of this app", or it never starts, with Smart App Control on | Smart App Control blocks a file of the satellite's that isn't signed yet (see above). |
| Grey tray icon, "not running: no pinned server identity" | Re-run the install command from the dashboard. |
| "Domovoi satellite didn't start" with a line from `config.toml` | A value there it won't run on (a device name that is not "default", a number or a plain name; a typo'd setting). Fix that line in `%LOCALAPPDATA%\Domovoi\Satellite\.domovoi\config.toml` (the old file is `config.toml.bak` after a dashboard edit), then **Start satellite** in the tray menu. |
| The wake word never fires | **Settings → Privacy & security → Microphone**: "Let desktop apps access your microphone" on; the right microphone chosen in **Settings → System → Sound**. |
| It goes quiet while someone else uses the PC | It runs in its own account's session; while another account is signed in at the screen, Windows may give it no microphone (not yet checked on hardware). |
| Anything else | `%LOCALAPPDATA%\Domovoi\Satellite\logs\satellite.log` (the tray's **Show logs folder**), and the live log in the dashboard's room drawer. |

## Old computers (USB stick)

An old **64-bit PC, laptop or mini PC**, or an **Intel Mac from 2012 to
2017**, can become a room that does nothing else: your Domovoi server makes
a USB stick for the room, the computer starts from it, you type one word,
and about half an hour later it is a room. No terminal, no password, no
account to make. The stick puts a fresh system on the computer
([Debian](https://www.debian.org/) 13, with no desktop) and the satellite
on top of it, the same headless install a Linux mini PC gets.

**The stick erases that computer's disk.** Everything on it (Windows, files,
photos) is gone for good. It asks first, on the computer itself, naming the
disk, and nothing is touched until you type ERASE.

**The USB stick is a preview**, like every spare device as a room (above),
labelled so on the card's **old computer (usb stick)** column: so far it
has been tried in virtual machines (BIOS, UEFI, Secure Boot); real
computers are next.

### What it needs

| | |
|---|---|
| **Processor** | About **2009 or newer**: Intel Core i3/i5/i7 or later, AMD from about 2011 (x86-64-v2). The wake-word software can't run on older ones (Core 2, the first Atoms, AMD K8/K10); the stick says so and stops before erasing anything. |
| **Network** | A **network cable** from your router to the computer while it sets up (or a USB network adapter). Wi-Fi on the stick comes later, once it has been tried on real laptops; without a cable the stick says "plug in a network cable" before anything is erased. |
| **Internet** | The computer downloads part of Debian (~155 MB) and the satellite's libraries while it sets up. |
| **The stick** | 2 GB or more (everything on it is replaced; the image is about 793 MB). |
| **The computer's disk** | 8 GB or more. An installed room uses about 1.6 GB. |
| **Your server** | Linux, or the Windows install (its Linux side). It downloads Debian's installer image from Debian the first time (793 MB, kept for the next stick) and keeps each stick's image (about 800 MB, removable on the card); it needs `xorriso`, which the installer and the next update add. |

**Not suitable:** 32-bit-only PCs; tablets and netbooks with 32-bit UEFI
(Bay Trail, Cherry Trail); Macs with Apple's T2 chip (2018-2020: the stick
recognises them and stops); and, until a firmware setting is changed, the
"Secured-core" PCs that refuse it (see Secure Boot below).

**What it costs to run.** A room runs day and night. An old desktop tower
draws about 30-80 W, $50-130 a year at US prices: more than a new Pi
satellite costs. An old laptop (6-15 W, $10-25 a year) or a mini PC is the
better room.

### 1. Prepare the stick

**Settings → Rooms & devices → Rooms → add a spare device**, name the room, **make the
code**, then under **old computer (usb stick)** press **Prepare the
stick**. The first time, the server downloads Debian's netinst image from
Debian (it uses it only if its size and SHA-256 are the ones Domovoi pins);
then it makes this room's image in a minute or two. The card shows the
progress; if something goes wrong it says what, and keeps saying it until
you try again.

The card refuses, with the reason, when the server's internet access is set
to **never** and Debian's image isn't on it yet, when its disk is short, or
when it has no `xorriso` yet.

### 2. Write it to a USB stick

- **Raspberry Pi Imager** (first choice; 2.0.3 or newer,
  [raspberrypi.com/software](https://www.raspberrypi.com/software/)): press
  **Open in Raspberry Pi Imager**. Imager opens with your server's list:
  choose **A computer**, then **Domovoi room stick**, then your stick. It
  checks every byte against the SHA-256 while it writes. If the button
  opens nothing, download **the Imager file** and double-click it. Imager
  sends usage statistics only for Raspberry Pi's own list, never for a list
  like this one.
- **USBImager** ([bztsrc.gitlab.io/usbimager](https://bztsrc.gitlab.io/usbimager/),
  MIT): **download the image**, open it in USBImager, pick the stick,
  **Write**.
- balenaEtcher and Rufus work too (Rufus: "DD image" mode).

The image's name is generic (`domovoi-room-stick.img`); the room is inside.

### 3. Start the old computer from it

Plug the stick in, switch the computer on, and keep tapping its boot-menu
key; pick the USB stick. Common keys (a model can differ):

| Maker | Key |
|---|---|
| Dell | F12 |
| HP | Esc, then F9 |
| Lenovo | F12 (some: the small Novo button) |
| Acer | F12 (turn on "F12 Boot Menu" in the firmware first) |
| ASUS | Esc (desktops: F8) |
| Toshiba / Dynabook | F12 |
| MSI | F11 |
| Samsung | Esc or F10 |
| Sony VAIO | Assist button, or F11 |
| Fujitsu | F12 |
| Gigabyte / ASRock | F12 / F11 |
| Intel NUC | F10 |
| Intel Mac (2012-2017) | hold Option (Alt) from the start chime |

If no key works, the firmware settings (often F2 or Del at power-on) can
put USB first; "Fast Boot" may need turning off.

The stick's menu says **Make this computer the Domovoi room "Kitchen" (asks
before erasing)** and starts it after 10 seconds; **Leave this computer as
it is** starts the computer as before. If the screen stays black, restart
and pick **The same, in text mode**.

### 4. Type ERASE

The installer works by itself for about a minute, then shows one page:

> This will ERASE EVERYTHING on this computer's disk: Samsung SSD 860, 256
> GB (/dev/sda) … Type ERASE to make this computer the Kitchen room.

It names the disk it will use: never the stick itself, never another USB
or Thunderbolt drive or a memory card; a solid-state disk before a spinning
one, then the largest. To be sure, unplug any other external disk first
(an eSATA one can't be told from an internal one, nor a Thunderbolt one on
some computers from before about 2019: see Known limits), and read the
disk's name on the page before you type. It also says whether your
Domovoi server answered (a server that is off just now is fine: the room
keeps trying after it is set up). Anything but ERASE, three times, or **Go
Back**: "Nothing was changed", and the computer turns off.

Before that page, the stick stops on a plain page, with nothing changed,
when the computer has no usable disk, its disks are joined together (LVM or
RAID: the page names them), its processor is too old, it is a T2
Mac, or it is not on the internet. If the computer is **already a room**
(the stick was left in and it started from it again), the page says so:
turn it off, take the stick out, and it stays that room.

If it says **"Network autoconfiguration failed"** (Debian's own page, when
no network answered at all): plug in the cable and choose **Retry network
autoconfiguration**.

Then it installs (5 to 20 minutes, depending on the line) and restarts by
itself. **Take the stick out once it restarts.**

### 5. Approve it

At its first start the computer installs the satellite (a few more minutes;
its screen says what it is doing, and why if it can't reach your server),
then shows the room's **six-digit code in large digits**, and says it
through its speaker if one works. Type it in **Settings → Rooms & devices → Rooms**,
under **waiting for approval**. The screen says "This computer is now the
room …" and turns off a minute later.

### What it does by itself

- **Satellite updates** come from your server, signed by its key, as on a
  Pi: the stick installs with **automatic** permissions (this computer is a
  room, not someone's laptop).
- **Debian's security updates** install every day by themselves
  (unattended-upgrades), and the computer restarts at **03:30** when one
  needs it (a new kernel).
- **The clock**: a time client keeps it right; a flat CMOS battery that
  starts it in 2009 is set forward to the day the stick was made, so the
  downloads work.
- Lid, sleep and screen: the installer's power settings (lid ignored, never
  sleep); the screen turns off a minute after approval.

### Secure Boot

The stick starts with Secure Boot on: its boot loader (Debian's shim) is
signed by Microsoft with both its **2011** and its **2023** certificates.

- **A PC that refuses the stick** ("Access Denied", "Secure Boot
  violation", or straight back to its boot menu) has Microsoft's
  third-party certificate switched off ("Secured-core" PCs, some Lenovo and
  Surface models since 2022). Turn on **Allow Microsoft 3rd Party UEFI CA**
  (Lenovo's name; other makers word it differently) in its firmware
  settings, or turn Secure Boot off.
- **The boot loader is frozen.** Microsoft's 2011 certificate expired in
  June 2026 and new shims are signed with the 2023 one only; firmware that
  never got the 2023 certificate (no maker update, and Windows, which would
  have brought it, is gone) would not start a room that an update moved to
  such a shim, with nobody at a keyboard. So the room keeps the shim and
  GRUB the stick installed: their packages are held (`apt-mark hold`) and
  kept out of the automatic updates, and nothing on the room changes the
  firmware's certificates (no fwupd). Kernels still update.
- **The dashboard warns** on the room's health card ("firmware needs the
  2023 certificate") when Secure Boot is on and the firmware lacks
  Microsoft's 2023 certificate (the room checks at every start). It keeps
  starting as it is; once Debian ships a boot loader signed with the 2023
  certificate only, a stick made after that will stop starting on it while
  Secure Boot is on. **To update
  the firmware by hand:** install the maker's latest BIOS/UEFI update for
  that model from their support site (many have a bootable USB updater; if
  the computer still has Windows, run it from there, and let Windows Update
  finish first, which may bring the certificate too), or turn Secure Boot
  off in the firmware settings.

### What runs as root on it

The same as on a headless Linux mini PC, plus three things the stick adds,
none of which the server can reach or start:

| | What | When |
|---|---|---|
| `domovoi-stick-firstboot.service` | checks the bundle's SHA-256, waits for the network, sets a stale clock forward, runs `install-pc.sh --mode headless` with the stick's room code | once, at the first start (until it succeeds) |
| `domovoi-stick-boot-check.service` | reads the firmware's Secure Boot state and certificates from `/sys/firmware/efi`, writes `/run/domovoi-stick/boot.json` | every start |
| unattended-upgrades | Debian's security updates, the 03:30 restart | daily |

The satellite runs as `domovoi-sat`, which has no login and no sudo, as on
any headless install. The room's screen (`console.py`) runs as
`domovoi-sat` too, and so does `/usr/local/sbin/domovoi-status`, which says
the code through the speaker: the satellite starts it with its own rights
(the same mechanism a Pi card's indicator uses; no sudo entry, no setuid).
Root's password is locked and there is no user account: nobody can log in
at the keyboard (the other consoles, Ctrl+Alt+F2, show a login prompt that
no password opens).

### Wi-Fi

Off for now: the stick keeps every Wi-Fi
driver from loading while it installs, so a computer without a cable gets
the "plug in a network cable" page. **USB_STICK_WIFI** (Settings →
System → Advanced, "USB sticks may use Wi-Fi") makes the next stick ask Debian's own
Wi-Fi questions instead (network, password); that path has not been tried
on a real laptop yet, and a room set up that way has no way to change its
Wi-Fi later. Older Macs and laptops with Broadcom Wi-Fi need a cable either
way.

### Reusing the computer, or retiring the room

The room is the whole computer. To use it for something else, install
another system over it (or start it from a new stick for another room).
Retire the room in the dashboard too (**Settings → Rooms & devices → Rooms → the room →
Retire room**).

### When it doesn't work

| What you see | Why, and what to do |
|---|---|
| The computer starts as before | It didn't start from the stick: the boot-menu key (above), or USB first in its firmware settings. |
| "Access Denied", "Secure Boot violation" | Secure Boot with Microsoft's third-party certificate off: see Secure Boot. |
| "Plug in a network cable" | No internet: a cable from the router (or a USB network adapter), then start again. |
| "This computer's processor is too old" | Older than about 2009: it can't be a room. |
| "There is no disk to put the room on" | No internal disk of 8 GB or more. |
| "This computer's disks are joined together" | Its disk holds part of an LVM volume or a RAID, and so does another disk (a USB one counts). The stick erases only one disk. Unplug or take out the others, or wipe the ones you don't need first (see Known limits), then start again. |
| "Something on this network tried to set up this computer" | A device on the network handed the installer setup answers (a "DHCP boot file"). Start again with a cable straight to your router. |
| The screen stays black after the menu | Restart and pick **The same, in text mode**. |
| "Not ready yet: … Can't reach your Domovoi server" | Is the server on, and the computer on the same network? It keeps trying every minute. |
| "did not prove it is the Domovoi server this stick was made by" | Something else answers at that address now (the server moved, or another machine). Make a new stick. |
| "The satellite files on this computer are damaged" | Make a new stick and start again. |
| The ERASE page again after the install | The stick is still in and the computer starts from USB first: take it out. |
| Anything else | The room's log in the dashboard's room drawer; on the computer, `/var/log/domovoi-stick-firstboot.log`. |

### Known limits

- **Thunderbolt on older computers.** The stick never picks a disk behind
  a port the computer marks as external. Thunderbolt computers from before
  about 2019 often don't mark their ports, so a Thunderbolt drive plugged
  into one looks like an internal disk and could be the one chosen (a
  solid-state one even comes first). The ERASE page always names the disk
  by make, size and device: if it isn't the computer's own disk, type
  anything else, unplug the drive and start again.
- **Two unrelated LVM or RAID disks.** The stick can see that a disk holds
  part of an LVM volume or a RAID, but not which one. If the chosen disk
  holds one and any other disk does too, it refuses, even when the two have
  nothing to do with each other, and there is no way past that page. Unplug
  the other disk if you can. If it's built in and you don't need what's on
  it, wipe it first from any Linux live stick (Debian's or Ubuntu's): `lsblk
  -f` lists the disks; for that disk (`/dev/sdX` here), `sudo wipefs -a` on
  each of its partitions (`/dev/sdX1`, `/dev/sdX2`, …) and then on
  `/dev/sdX` itself. That erases what's on it. A RAID made in the
  computer's own firmware (Intel RST and the like) is deleted in that
  firmware's RAID setup instead. Then start from the stick again.

### For developers

`domovoi/usb_stick/`: `image.py` makes our files (pure, reproducible:
`SOURCE_DATE_EPOCH`, sorted, root-owned), `repack.py` runs xorriso,
`jobs.py` is the core's download, job and cache; the stick's own files are
in `stick/` (`installer/` runs inside Debian's installer, `room/` on the
installed room). The pinned image is `debian-image.json` (its `about` says
how to move it). The QEMU kit is `scripts/dev/usb-stick/` (Windows host,
WHPX): `make_stick.py` (offline, or from a throwaway stack's own route),
`matrix.py` (BIOS, UEFI, Secure Boot with Microsoft's 2011 and 2023 keys,
the refusals, the "already a room" page, the Wi-Fi switch),
`qemu_drive.py`, and the OVMF and disk tools. Two test seams on the core,
never for a household: `DOMOVOI_USB_STICK_REPACK` (run repack.py
elsewhere, for a core with no xorriso) and `DOMOVOI_USB_STICK_DEBUG=1` (the
kit's serial markers).
