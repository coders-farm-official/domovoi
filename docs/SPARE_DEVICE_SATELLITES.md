# Satellites on spare devices

A room doesn't need a Raspberry Pi. A spare **Linux laptop or mini PC** can
be one, a **Windows laptop or PC** too, and an old **phone or tablet**
through the Android app's room mode. Each speaks the same protocol to the
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
   (`kids_room`), a **one-line install command** for Linux, another for
   Windows, and a **QR code** for a phone. All three carry the same short *enrolment code*:
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

**An old Windows 10 laptop** can be a room as it is
([Windows](#windows-laptops-and-pcs), below). One that can't run Windows 11
and will only ever be a room makes a better one on Linux: write the Debian
or Ubuntu installer to a USB stick
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

## Windows laptops and PCs

A Windows 10 or 11 laptop or PC (64-bit, Intel or AMD) can be a room for
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
tray icon instead.

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

**Unsigned, for now.** Coders Farm's signing is on hold, so the satellite's
own code carries no signature. Python itself, which runs it, is signed by
the Python Software Foundation. Expect Windows Security or another
antivirus to look twice at a command that downloads and starts programs;
the command shows you exactly what it checks before it runs anything
(below). Install it only on a PC whose owner agrees.

### 1. Make the command

**Settings → Satellites → add a spare device**, the room's name, **make
the code**. Under **windows laptop or pc** is a one-line command for
Windows PowerShell; **copy the Windows command**.

The first time, the card may say the server doesn't hold the Windows files
yet (Python and its libraries, about 96 MB): press **get the Windows
files** and the server downloads them from python.org and PyPI, each file
checked against the SHA-256 pinned in the satellite code. If you don't, the
laptop downloads the same pinned files from there itself. Under
`INTERNET_ACCESS=never` the server can't fetch them; a laptop that can
reach the internet still can.

### 2. Run it

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

### 3. Approve it

The code is in the PowerShell window, in a notification ("Kid's Room:
waiting for approval, code 123456"), in the tray icon's tooltip and its
menu. Type it into the dashboard's approval card; the notification then
says **Approved**. It is never written to the satellite's log.

### What warnings you may see

| What | Why |
|---|---|
| Windows Security or another antivirus warns about, or blocks, PowerShell downloading and starting programs | The command does exactly that, and the satellite's code is unsigned. If it is blocked, the antivirus's history says what; allow it only if the command came from your own dashboard. |
| The microphone icon in the taskbar stays on | The satellite listens for the wake word all the time; Windows shows that any program has the mic open. Hovering it names *Python*. The satellite's own tray icon says whether audio is leaving for the server (below). |
| "Let desktop apps access your microphone" is off | Then Windows gives the satellite silence and the wake word never fires. Turn it on in **Settings → Privacy & security → Microphone**. |
| A "Windows protected your PC" (SmartScreen) window | Not expected: nothing the command starts was downloaded through a browser. If one appears for *Domovoi Satellite* from the Start menu, it is the shortcut to Python's own `pythonw.exe`. |

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
| Microsoft Visual C++ runtime (`msvcp140.dll`, `msvcp140_1.dll` 14.44, from the `msvc-runtime` wheel) | Microsoft's, not open source: the Visual Studio 2022 licence's "Distributable Code" ([REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution), Visual C++ Runtime Files) lets them be copied and distributed unmodified with a program — the same terms as the `vcruntime140.dll` Python itself ships |

Nothing GPL or LGPL is bundled: there is no mpg123 on Windows (MP3 is
decoded by dr_mp3), and sounddevice's ASIO-enabled PortAudio builds
(Steinberg's ASIO SDK) are deleted after install — they are never loaded
anyway. The wake-word models are openWakeWord's (their licence is the
same question as on every Pi; see the wake-word notes).

### When it doesn't work (Windows)

| What you see | Why, and what to do |
|---|---|
| "This is not the download the dashboard expected" | Domovoi was updated since the command was made (or the download was changed on the way). Make a new command. |
| "this window runs as administrator" | Open a normal PowerShell window and paste it again. |
| "never installed on the PC that runs the Domovoi server" | This PC runs the server. Use another PC. |
| "did not prove it is the one in the code" | As on Linux: a different machine answers at that address. Compare the fingerprint with **Settings → About**. |
| "running scripts is disabled on this system" | A Group Policy forbids scripts here; the installer can't run on this PC. |
| "Microsoft's Visual C++ runtime is not on this PC" / "is older than 14.44" (the tray) | The copies installed beside its Python are gone. Paste the command again (it puts them back), or install **Microsoft Visual C++ Redistributable (x64)** from <https://aka.ms/vc14/vc_redist.x64.exe> (an administrator, once); then **Start satellite** in the tray. |
| "Windows blocked part of this app", or it never starts, with Smart App Control on | Smart App Control blocks a file of the satellite's that isn't signed yet (see above). |
| Grey tray icon, "not running: no pinned server identity" | Re-run the install command from the dashboard. |
| "Domovoi satellite didn't start" with a line from `config.toml` | A value there it won't run on (a device name that is not "default", a number or a plain name; a typo'd setting). Fix that line in `%LOCALAPPDATA%\Domovoi\Satellite\.domovoi\config.toml` (the old file is `config.toml.bak` after a dashboard edit), then **Start satellite** in the tray menu. |
| The wake word never fires | **Settings → Privacy & security → Microphone**: "Let desktop apps access your microphone" on; the right microphone chosen in **Settings → System → Sound**. |
| It goes quiet while someone else uses the PC | It runs in its own account's session; while another account is signed in at the screen, Windows may give it no microphone (not yet checked on hardware). |
| Anything else | `%LOCALAPPDATA%\Domovoi\Satellite\logs\satellite.log` (the tray's **Show logs folder**), and the live log in the dashboard's room drawer. |
