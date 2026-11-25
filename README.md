<p align="center">
  <img src="docs/assets/banner.jpg" width="100%"
       alt="Domovoi, the home computer system that just works. Keeps it in the house. The amber cat saying &quot;Hello Domo&quot;, with music, movies, files, every room and your phone. Private AI at home, when you want it. Free, open source, no cloud, no subscription. github.com/coders-farm-official/domovoi">
</p>

<h1 align="center">Domovoi</h1>

<p align="center">
  <strong>The home computer system that just works.</strong><br>
  Keeps it in the house. Made in the open by <a href="https://codersfarm.org">Coders Farm</a>, a nonprofit
  in Lansing, Michigan that teaches people to code for free.
</p>

<p align="center">
  <a href="https://github.com/coders-farm-official/domovoi/releases/latest"><img alt="latest release" src="https://img.shields.io/github/v/release/coders-farm-official/domovoi?display_name=tag&sort=semver&label=latest%20release&color=2EA043&style=flat-square&labelColor=1F1A14"></a>
  <a href="docs/LINUX_HOST.md#release-channels"><img alt="server, Windows and rooms: the version on stable" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcoders-farm-official%2Fdomovoi%2Fstable%2Fpyproject.toml&query=%24.project.version&prefix=v&label=server%20%C2%B7%20Windows%20%C2%B7%20rooms&color=2EA043&style=flat-square&labelColor=1F1A14"></a>
  <a href="https://play.google.com/store/apps/details?id=com.domovoi.app"><img alt="Android app: the version on stable" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcoders-farm-official%2Fdomovoi%2Fstable%2Fandroid%2Frelease.json&query=%24.release.version_name&prefix=v&label=Android%20app&color=2EA043&style=flat-square&labelColor=1F1A14"></a>
  <img alt="iPhone app: coming soon" src="https://img.shields.io/badge/iPhone%20app-coming%20soon-8B8178?style=flat-square&labelColor=1F1A14">
  <br>
  <a href="docs/CONTRIBUTING.md#running-the-tests"><img alt="tests: 30,500+ (server and rooms, Android app, Windows)" src="https://img.shields.io/badge/tests-30%2C500%2B-2EA043?style=flat-square&labelColor=1F1A14"></a>
  <a href="https://github.com/coders-farm-official/domovoi/actions/workflows/ci.yml?query=branch%3Amain"><img alt="CI on main" src="https://img.shields.io/github/actions/workflow/status/coders-farm-official/domovoi/ci.yml?branch=main&label=CI&style=flat-square&labelColor=1F1A14"></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/coders-farm-official/domovoi"><img alt="OpenSSF Scorecard" src="https://img.shields.io/ossf-scorecard/github.com/coders-farm-official/domovoi?label=OpenSSF%20Scorecard&style=flat-square&labelColor=1F1A14"></a>
  <a href="LICENSE"><img alt="license: MIT" src="https://img.shields.io/badge/license-MIT-F2A618?style=flat-square&labelColor=1F1A14"></a>
  <br>
  <img alt="runs on: Linux, Windows, Raspberry Pi, Android" src="https://img.shields.io/badge/runs%20on-Linux%20%C2%B7%20Windows%20%C2%B7%20Raspberry%20Pi%20%C2%B7%20Android-F2A618?style=flat-square&labelColor=1F1A14">
  <img alt="built with: Python, Kotlin, C#" src="https://img.shields.io/badge/built%20with-Python%20%C2%B7%20Kotlin%20%C2%B7%20C%23-F2A618?style=flat-square&labelColor=1F1A14">
</p>

<p align="center">
  <a href="https://play.google.com/store/apps/details?id=com.domovoi.app"><img alt="Get it on Google Play" src="https://img.shields.io/badge/Get%20it%20on%20Google%20Play-414141?style=for-the-badge&logo=googleplay&logoColor=white"></a>
</p>

<p align="center">
  <a href="https://discord.gg/j3H5XamSxg"><img alt="Join us on Discord" src="https://img.shields.io/badge/Join%20us%20on%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white"></a>
  <a href="https://forms.gle/wbz194RhuLmJdg3D8"><img alt="Get updates by email" src="https://img.shields.io/badge/Get%20updates%20by%20email-F2A618?style=for-the-badge"></a>
</p>

<p align="center">
  <a href="#status-and-roadmap">Status and roadmap</a> ·
  <a href="https://learn.codersfarm.org">Learn to code, free</a> ·
  <a href="https://donate.codersfarm.org">Support Coders Farm</a>
</p>

Domovoi turns a computer you already own into your home's own system: your
music, movies, audiobooks, files and documents in one place, playing in every
room and on your phone. No cloud, no subscription, and nothing leaves your
network unless you say so. Small Raspberry Pi speakers (or a spare laptop or
an old phone) put it in every room, and the Android app brings it with you:
save an audiobook or your music to the phone and it plays with no server at
all.

When you want it, Domovoi also listens and answers: a voice in every room and
a private AI chat, all running at home. Your voice never leaves your network:
speech recognition (Whisper, on a GPU or the CPU), language models (Ollama),
and text-to-speech all run locally. The AI is your choice: setup asks whether
to include AI chat and questions, and Settings → Chat & AI → Models turns them
off or on later; with them off, voice commands for music, timers and the rest
keep working. When your internet goes out, Domovoi keeps working: every skill
declares up front whether it needs the network, and anything that can run
offline does. Whether it uses the internet at all is your choice:
[Will your Domovoi have internet?](docs/INTERNET.md)

Out of the box it handles music and your media library, videos, audiobooks and
podcasts, files and documents (with printing and scanning on network
printers), internet radio (via the bundled [radio plugin](plugins/radio)),
then timers and reminders, room-to-room intercom and drop-in, voice notes,
per-person voice profiles, news briefings, song lyrics, a calendar, and a text
chat that can read pictures and documents. Everything else is a plugin away —
installed from the dashboard as a zip upload or a GitHub URL.

In short: one always-on computer is the Domovoi server. Each room of the
house (the Kitchen, the Living room) has a satellite, a small device that
listens for the wake word and plays the room's music. Phones use the app,
any browser on your network opens the dashboard, and the internet is
optional.

```mermaid
flowchart LR
    subgraph HOME["Your home network"]
        SERVER["Domovoi server<br/>an always-on PC"]
        subgraph KITCHEN["Kitchen room"]
            SATK["Satellite<br/>a Raspberry Pi, spare PC<br/>or phone in room mode"]
        end
        subgraph LIVING["Living room"]
            SATL["Satellite"]
        end
        PHONE["Phones<br/>the Domovoi app"]
        BROWSER["Browser<br/>the dashboard"]
    end
    INET["Internet<br/>optional"]

    SATK <--> SERVER
    SATL <--> SERVER
    PHONE <--> SERVER
    BROWSER <--> SERVER
    SERVER -. "only if you allow it" .-> INET
```

The full picture, with ports and containers, is in
[How the pieces fit together](#how-the-pieces-fit-together).

> **Status: released and maintained.** 1.0.0 shipped in October 2026 and
> the current release is **1.4.1**. The USB stick for old PCs has been
> tested in virtual machines only: see
> [Status and roadmap](#status-and-roadmap). Ask questions in
> [Discussions](https://github.com/coders-farm-official/domovoi/discussions).

> **Why "Domovoi"?** In Slavic folklore, a *domovoi* is the guardian spirit of
> the household — a small, usually benevolent creature that lives behind the
> stove, watches over the family, and keeps the house in order. It often takes
> the form of a cat. Ours does too: the cat mascot you'll see around the
> dashboard is the house spirit itself, minding your home from inside your
> network — never someone else's cloud.

## Install

**Linux** (an x86_64 computer running Ubuntu 26.04, Debian 13, or Linux Mint 22 / Pop!_OS 24.04;
other Ubuntu versions may work, and it says so). Open a terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/coders-farm-official/domovoi/stable/scripts/linux/install.sh | sudo bash
```

It asks whether the server will have internet after setup, its time zone, and whether to include AI chat,
checks that it downloaded a signed release, installs everything, and ends with the address to open from your phone.
To read the script first, run it without questions, or install by hand: [Running the server on Linux](docs/LINUX_HOST.md#install).
Fedora, Arch and other systems are not supported yet ([which systems, and why](docs/LINUX_HOST.md#which-distro));
to ask for yours, [open an issue](https://github.com/coders-farm-official/domovoi/issues).

**Windows 11:** download [DomovoiSetup.exe](https://github.com/coders-farm-official/domovoi/releases/latest/download/DomovoiSetup.exe)
and run it ([more about the Windows installer](docs/WINDOWS_HOST.md)).

**Mac** (Apple silicon, macOS 26 or later): download [DomovoiSetup-mac.pkg](https://github.com/coders-farm-official/domovoi/releases/latest/download/DomovoiSetup-mac.pkg)
and open it. The first time, macOS stops it: go to System Settings › Privacy & Security and choose **Open Anyway**.

Every file, with its checksums and notes, is on the [latest release](https://github.com/coders-farm-official/domovoi/releases/latest).
Then open the address it gives you, [claim the admin account](#2-claim-the-admin-account) and
[add your first room](#3-add-your-first-satellite).

---

## Table of contents

- [Install](#install)
- [Status and roadmap](#status-and-roadmap)
- [Who it's for](#who-its-for)
- [First install](#first-install)
- [Returning users: moving or reinstalling](#returning-users-moving-or-reinstalling)
- [How the pieces fit together](#how-the-pieces-fit-together)
- [What each interface can do](#what-each-interface-can-do)
- [Plugins](#plugins)
- [Documentation](#documentation)
- [License & credits](#license--credits)

---

## Status and roadmap

We build Domovoi in public. Here's where it stands.

**Works today**

What a household gets. Each line links to the page that covers it; what
changed when is in the [release notes](docs/RELEASE_NOTES.md).

- **Music in every room:** your library, podcasts, audiobooks and videos,
  a shared queue per room, and [Up next](scripts/manual/manual.md#up-next)
  to keep it playing ([music and media](scripts/manual/manual.md#music-and-media)).
- **Voice in every room:** a Raspberry Pi satellite in each room wakes on "Domovoi",
  with barge-in, intercom and drop-in, and wake words you train yourself
  ([rooms and devices](scripts/manual/manual.md#rooms-and-devices) ·
  [satellite hardware](docs/SATELLITE_HARDWARE.md)).
- **Everyday answers by voice:** alarms, reminders, the calendar, lists and
  the news, without a language model ([things to say](scripts/manual/manual.md#things-to-say)).
- **Files, photos and documents:** a shared Files page, a word processor and
  spreadsheets, and network printing and scanning with no drivers
  ([files, printing and calendar](scripts/manual/manual.md#files-printing-and-calendar)).
- **Plugins for more media:** radio stations and other extras, installed
  from the dashboard ([plugins](#plugins)).
- **Your phone:** the Android app (8.0 or later) brings the dashboard, music,
  chat and drop-in, and plays the phone's own media with no server
  ([your phone](scripts/manual/manual.md#your-phone) ·
  [Google Play](https://play.google.com/store/apps/details?id=com.domovoi.app)).
- **Linux or Windows, with updates from the dashboard:** NVIDIA or CPU
  only, a one-script Ubuntu install, release channels and automatic
  rollback ([Linux host](docs/LINUX_HOST.md) · [CPU host](docs/CPU_HOST.md)).
- **Keeps itself running:** a frozen core restarts itself, and one Health
  page lists every check ([keeping it running](scripts/manual/manual.md#keeping-it-running)).
- **Private and local-first:** speech, voices and models run on your
  server, the internet is a yes / sometimes / no question, and voice
  history is kept 90 days by default ([internet](docs/INTERNET.md) ·
  [security and privacy](docs/SECURITY_PRIVACY.md)).
- **Optional AI:** a chat that reads documents and photographed letters,
  writes letters and looks things up only with your permission; one switch
  turns it off and voice commands keep working
  ([chat and help](scripts/manual/manual.md#chat-and-help) ·
  [documents and letters](scripts/manual/manual.md#documents-and-letters)).
- **Its own help desk:** ask how to do something, by voice or in chat, and
  it answers from its manual ([asking for help](scripts/manual/manual.md#asking-domovoi-for-help)).
- **Licences in the open:** no GPL or LGPL libraries in the server's Python
  code, and every model and voice it downloads is listed with its licence
  in [docs/LICENSES.md](docs/LICENSES.md).
- **Chat on your phone, no server needed:** the Android app can run chat on
  the phone itself, even in airplane mode. It needs a 64-bit phone with 8 GB
  of memory or more and a large download
  ([Android compatibility](docs/ANDROID_COMPATIBILITY.md)).
- **A Windows installer:** `DomovoiSetup.exe` turns a Windows 11 PC into a
  Domovoi server. It is not code-signed yet, so Windows asks before it runs
  ([Windows installer](docs/WINDOWS_HOST.md)).
- **A Mac installer:** `DomovoiSetup-mac.pkg` turns a Mac with Apple silicon
  (macOS 26 or later) into a Domovoi server: the server runs in a small
  Linux virtual machine, and the AI models and speech recognition use the
  Mac's graphics chip. It isn't from an identified developer yet, so macOS
  asks once before it opens ([latest release](https://github.com/coders-farm-official/domovoi/releases/latest)).
- **Satellites on spare devices:** a spare laptop or PC, or an old phone or
  tablet in the app's room mode, can be a room's satellite instead of a Raspberry Pi
  ([spare-device satellites](docs/SPARE_DEVICE_SATELLITES.md)).
- **A USB stick that turns an old PC into a satellite:** the dashboard prepares
  the stick and the old PC installs from it. It has been tested in virtual
  machines; real computers are next
  ([old computers](docs/SPARE_DEVICE_SATELLITES.md#old-computers-usb-stick)).

**This release: Domovoi v1.4.1**

Version 1.4.1 follows [v1.4.0](https://github.com/coders-farm-official/domovoi/releases/tag/v1.4.0).
It is the server, the Android app, a Windows Setup (with the Windows
satellite Setup beside it) and the Mac server's installer, with one
`SHA256SUMS` for every file, software bills of materials, and GitHub build
provenance for each file
([verifying a release](SECURITY.md#verifying-a-release)), published as
described in [Releasing](docs/CONTRIBUTING.md#releasing). `stable` points
at 1.4.1, and new installs follow it. A plain-language summary is in
[docs/release/v1.4.1.md](docs/release/v1.4.1.md); every change is in the
[release notes](docs/RELEASE_NOTES.md). Since 1.4.0: chat lookups that
reach the web when the offline library has nothing relevant; weather
answers for a new Home location, and holiday questions answered offline;
music that comes back after every question; Friend Houses open to the
whole household and by voice; "print this" on a picture or file in a
chat; a chime before announcements; microphone presets for XVF3800
rooms; and many tighter security checks and limits.

**Coming in 1.5.0**

Rooms with more than one satellite, and moving a satellite to another
room, are on the way: see the 1.5.0 entries in the
[release notes](docs/RELEASE_NOTES.md).

What's planned is in the [roadmap](docs/ROADMAP.md), grouped Now / Next /
Later: an iPhone app, the Android app on F-Droid, the Windows installer
code-signed, and a few wishes beyond those.

**How it's built.** Domovoi is written with AI coding agents and held to the
same bar Coders Farm teaches: automated tests, review, and a written
[security model](docs/SECURITY_PRIVACY.md). The code is all here; read it.

**Follow along and get help.** Updates go out on [Discord](https://discord.gg/j3H5XamSxg)
and by [email](https://forms.gle/wbz194RhuLmJdg3D8). For questions and install help, use
[Discussions](https://github.com/coders-farm-official/domovoi/discussions);
replies can take a few days while we build.

**Found a security problem?** Please report it privately, not in an issue:
[SECURITY.md](SECURITY.md) says how, and we reply within 7 days. Everyone
in the project's spaces follows the [code of conduct](CODE_OF_CONDUCT.md).

---

## Who it's for

### The everyday household

You want one system for your home's music, movies, files and voice that
doesn't send any of it to a datacenter. Domovoi gives you that on your own
Wi-Fi:

- **Out of the box:** wake-word voice control in any room with a satellite —
  music, timers, reminders, questions, news, intercom ("tell the kitchen
  dinner's ready"), drop-in, voice notes. Domovoi learns who's speaking (say
  "I'm Sarah" once) and can forget you on request, when you ask in your
  own voice. A web dashboard on your LAN manages all of it, and an Android
  app brings the dashboard, a music player and chat to your phone.
- **Hardware you need:** one always-on PC as the server, Linux or Windows.
  An NVIDIA GPU makes speech recognition and the language models faster (the
  Windows installer always runs speech recognition on the CPU); without a GPU
  it all runs on the CPU ([Running without an NVIDIA GPU](docs/CPU_HOST.md)).
  Then, per room,
  a Raspberry Pi Zero 2 W with a ReSpeaker mic board and a speaker — roughly
  the price of a commercial smart speaker per room. Details and shopping
  list: [Satellite hardware guide](docs/SATELLITE_HARDWARE.md). A spare
  laptop, PC or old phone can be the room's satellite instead
  ([spare-device satellites](docs/SPARE_DEVICE_SATELLITES.md)).
- **The wake word** is **"Domovoi"** on a new install: Coders Farm's own
  model, trained on synthetic voices, which your server downloads once and
  gives to each room (until it has, or if the server can't download it, a
  satellite wakes on "Hey Jarvis", which older installs keep). You can train
  your own word on your server from the dashboard (it needs 24 GB of
  memory and a one-time download of about 22 GB), and a satellite that wakes on
  the TV or misses you has **Wake sensitivity** and **Wake confirmation**
  in its Settings tab. More in the
  [FAQ](docs/FAQ.md#can-i-rename-the-assistant-or-change-the-wake-word) and
  [wake-word training](scripts/wake_word/README.md).

---

## First install

> Setting up a system for the first time? This section is the quickstart.
> The **[day-one setup runbook](docs/SETUP_RUNBOOK.md)** walks the same
> ground in order, with the decisions you can't easily reverse, the
> verification gates between steps, and the fleet build-out after.

### Prerequisites

The one-command Linux install and the Windows installer set all of this up
for you, with AI models sized to the computer. The table is what an
install by hand needs.

| What | Why |
|---|---|
| A PC that stays on, ideally with an **NVIDIA GPU** | With one, Whisper speech-to-text runs on CUDA. No NVIDIA GPU? It still runs, on the CPU — see [Running without an NVIDIA GPU](docs/CPU_HOST.md). The steps below are written Windows-first; on Linux see [Running the server on Linux](docs/LINUX_HOST.md). |
| **Python 3.11+** | The core service and dashboard run natively (they need GPU access, so they're not containerized). |
| **Docker** (Docker Desktop on Windows, Docker Engine on Linux) | Runs Postgres, database migrations, the web-search helper, and per-room audio (MPD) containers. |
| **[Ollama](https://ollama.com)** | Local language models. Domovoi uses two: a small conversational model (`llama3.2:3b`) and a tool-routing model (`qwen2.5:14b`) — pull both (on a CPU-only server, `qwen2.5:7b` in place of the 14B). Pictures in chat use a third, small vision model (`qwen3-vl:2b-instruct-q4_K_M`), which an admin can add later from **Settings → Chat & AI → Models**. AI is optional: with it off, voice commands keep working ([Is AI optional?](docs/FAQ.md#is-ai-optional)). |
| **Git** | To clone the repo. |
| **Internet, while you install** | Packages, Docker images and the models all download during setup. After that it's your choice — see [Will your Domovoi have internet?](#will-your-domovoi-have-internet) below. |
| **~20 GB free disk** | For the software and all default models — **plus** room for your own media library. See [Disk footprint](#disk-footprint) below. |

#### Disk footprint

An install by hand (no plugins) with every default model runs **~15–20 GB**,
dominated by the two Ollama models. The installers' smaller models need
less: about 6.4 GB of AI models, or about 9.1 GB on a server with more
memory.

| Item | Approx. |
|---|---:|
| Ollama `qwen2.5:14b` (tool routing) | ~9 GB |
| Ollama `llama3.2:3b` (Q&A) | ~2 GB |
| Ollama `qwen3-vl:2b-instruct-q4_K_M` (pictures in chat, optional) | ~1.9 GB |
| Whisper `large-v3` (STT, fp16) | ~1.5 GB |
| Python env — mostly the NVIDIA cuBLAS/cuDNN wheels | ~2–3.5 GB |
| Docker images (Postgres, Flyway, per-room MPD) | ~1 GB |
| Piper voice + voice ID model + wake-word model + sound cache | ~0.3 GB |

**On top of that** sits your own **music/podcast/audiobook library** (your
files, any size) — so provision ~20 GB for Domovoi itself and add whatever
your media needs. Not counted here: Docker Desktop the application (~1–2 GB)
and your GPU drivers, which are prerequisites. Wake-word training, if you
turn it on, takes about 32 GB more the first time. The Windows installer
asks for 40 GB free on the system drive
([Which PCs](docs/WINDOWS_HOST.md#which-pcs)).

**Shrink it:** swap the tool model to `qwen2.5:7b` (~4.7 GB) to save ~4 GB —
the biggest single win; use a smaller Whisper (`medium` ~0.8 GB, `small`
~0.5 GB) at some accuracy cost.

### Will your Domovoi have internet?

Setup itself needs the internet. After that, Domovoi runs with or without
it, and your voice never leaves the house either way. Domovoi asks once —
**Yes, always**, **Sometimes**, or **No, keep everything in the house** —
in the dashboard right after you claim the admin account (step 2), and
the answer sets the defaults of the online extras:

- **Yes, always** turns on web answers (it starts the local search helper,
  SearXNG), other names for artists from MusicBrainz, synced lyrics from
  LRCLIB, podcast downloads, and song recognition once you add a free
  AcoustID key. Still yours to do: your town for local news, the AcoustID
  key, your radio market:
  [If your Domovoi will have internet](docs/INTERNET.md#if-your-domovoi-will-have-internet).
- **Sometimes** is the same without automatic podcast downloads.
- **No** makes the server contact nothing outside your network, and greys
  what needs the internet. Download what it will need first:
  [Running Domovoi with no internet](docs/INTERNET.md#running-domovoi-with-no-internet).

Settings you set yourself always win, and you can change the answer later
in **Settings → Privacy → Internet**. Answering from the command line instead:
`python -m domovoi.env_bootstrap --internet always|sometimes|never` before
the first start (it writes the answer into the fresh `domovoi/.env`).

### 1. Install the server

Four ways in:

- **Linux (Ubuntu 26.04, Debian 13, Mint 22, Pop!_OS 24.04), one command:** [Install](#install)
  above. It installs the latest release, its services and the models in
  one run, and the server then follows the `stable` channel, releases only
  ([Release channels](docs/LINUX_HOST.md#release-channels)). Details:
  [One command](docs/LINUX_HOST.md#one-command), or
  [run the install script yourself](docs/LINUX_HOST.md#or-run-the-install-script-yourself).
- **Windows 11, the installer.** `DomovoiSetup.exe` runs that
  same script inside a private WSL2 distro. It is unsigned; download it from
  the [latest release](https://github.com/coders-farm-official/domovoi/releases/latest),
  or build it with `windows\build.ps1`
  ([Windows installer](docs/WINDOWS_HOST.md)).
- **Mac (Apple silicon, macOS 26 or later), the installer.**
  `DomovoiSetup-mac.pkg` from the [latest release](https://github.com/coders-farm-official/domovoi/releases/latest) sets up the
  same server in a small Linux virtual machine, with the AI models and
  speech recognition on the Mac's graphics chip. The first time, macOS
  won't open it (it isn't from an identified developer yet): open System
  Settings → Privacy & Security, click **Open Anyway** under Security, and
  enter your login password.
- **By hand, on Windows or Linux:** the commands below.

```powershell
git clone https://github.com/coders-farm-official/domovoi   # every change (main); add
cd domovoi                          # --branch stable for releases only

# Python dependencies (run from the repo root, where pyproject.toml lives).
# A Linux server installs from the hash-pinned lock instead:
# docs/LINUX_HOST.md#install. Add `dev` to the extras only to run the tests.
pip install -e ".[real-clients,voice-profile]"
pip install -e ".[cuda]"            # NVIDIA hosts only — CUDA runtime wheels
pip install --no-deps -r requirements-no-deps.in   # faster-whisper, without its
                                    # declared deps (PyAV: the file says why)

# One-shot bootstrap: starts Postgres, runs migrations (and the search
# helper, when the internet question is answered Yes or Sometimes), then
# starts the core service
./domovoi/scripts/dev.sh            # bash
./domovoi/scripts/dev.ps1           # PowerShell
```

Prefer to see each step? The manual equivalent:

```powershell
python -m domovoi.env_bootstrap      # first run only: .env with a random Postgres password
                                     # (add --internet always|sometimes|never to answer now)
cd domovoi
docker compose up -d postgres        # Postgres 16 on 127.0.0.1:6432 (loopback only)
docker compose run --rm flyway       # database migrations
docker compose run --rm flyway-test  # migrate the test DB (to run pytest, see
                                     # docs/CONTRIBUTING.md, "Running the tests")
docker compose up -d searxng         # optional: the web-answers helper (saving Yes or Sometimes starts it too)
cd ..
python -m domovoi.main               # core voice service on :6370
```

Then, in a second terminal, start the dashboard:

```powershell
python -m web.backend.main           # web dashboard on :6369
```

### 2. Claim the admin account

Open the dashboard at `http://domovoi.local` (the server's name on your
network; the server answers on port 80 too and sends the browser on to the
dashboard). Where that doesn't open, use `http://domovoi.local:6369`, or
the server's address, `http://<server>:6369`, which always works. While
nobody has claimed it, every page opens a welcome that asks for the
**setup code** and a new admin password.

- **The setup code** is 8 words. The Windows installer shows it on its
  last page, and on a Mac the Domovoi Setup window shows it (closed it?
  the Domovoi cat in the menu bar, **Repair or Uninstall Domovoi...**,
  then **Show Setup Code...**). Elsewhere it is in
  `~/.domovoi/setup-code.txt` on the server (in the home of the account
  the core runs as). The core also
  prints it when it runs in a terminal, but never as a service, so it
  stays out of the journal. It is valid for 24 hours (restart the core for
  a fresh one), and the file is deleted once setup is done.
- **Not now** puts the welcome away for that browser; Home's **claim it**
  brings it back. The same form is under **Settings → System → Advanced →
  Admin → "set up admin"**, and opens by itself the first time you try
  anything that needs an admin. Until setup is done, those changes are
  refused (501).
- **Signed in,** a reload or a new tab in the same browser keeps you
  signed in for an hour (**Stay signed in after a reload**, in Settings →
  System → Advanced → Security, changes it); **sign out** ends it at once.
- **Forgot the password?** `python -m domovoi.main --reset-admin` on the
  server clears it and writes a fresh setup code to the same file.

Day-to-day features need no sign-in; the admin password guards the risky
ones (plugins, configuration, credentials). Phones and browsers join the
household once with the **household token**, which is not the admin
password: it is shown after the claim ("You're all set!"), later in
Settings → Household → Phones & browsers → Household token, and kept in
`~/.domovoi/device-token.txt`. **Want an easier one?** there sets your own
(at least 12 characters, e.g. `maple-river-lamp`).

After that, the dashboard asks whether this
Domovoi will have internet ([above](#will-your-domovoi-have-internet)).
**Decide later** leaves a reminder on the Home page.

### 3. Add your first satellite

A satellite is the device in a room that listens and plays; a room can
have several (1.5.0). However you set one up, it ends the same way: the
device shows or says a six-digit approval code, and you type that code into
**Settings → Household → Rooms** on the dashboard (as admin) to make it that
room's satellite. Three routes:

- **A Raspberry Pi.** Flash stock Raspberry Pi OS Lite (64-bit), put the
  card in the server, and use **Settings → Household → Rooms → prepare a room's
  SD card** (on the Windows installer, which can't see SD cards, download the
  zip and unzip it onto the card). On its first boot the Pi raises its own
  setup Wi-Fi (`Domovoi-Setup-<id>`): join it from a phone, pick your home
  Wi-Fi, name the room, then approve the code the Pi says out loud.
  Hardware list, the two mic boards, and the by-hand route (installing the
  satellite client and pointing it at `ws://<server>:6370` yourself) are in
  the [Satellite hardware guide](docs/SATELLITE_HARDWARE.md).
- **A spare Linux or Windows laptop or PC**. **Settings → Household → Rooms → add
  a spare device**: type the room's name, press **make the code**, and run
  the one-line command it shows on that computer (an older Linux, or
  Fedora, openSUSE or Arch, gets the satellite's own Python). The computer
  checks it reached *your* server, then shows its approval code
  ([Linux](docs/SPARE_DEVICE_SATELLITES.md#linux-laptops-and-mini-pcs) ·
  [Windows](docs/SPARE_DEVICE_SATELLITES.md#windows-laptops-and-pcs)). On
  Windows the card can also hand you a Setup to double-click instead of
  the command (not code-signed yet).
- **An old phone or tablet** (Android 8.0 or later). The same **add a spare
  device** card shows a QR code: scan it with the phone's camera, and your
  server's page installs the Domovoi Android app (press **Download the app
  to this server** on the card once first) and opens it as that room's satellite. A
  phone that already has the app can scan the code from its **Domovoi
  Room** icon instead. The phone gets
  its wake-word models from the server, so press **prepare a room's SD card
  → Refresh caches** once first
  ([room mode](docs/SPARE_DEVICE_SATELLITES.md#phones-and-tablets-room-mode)).

Say the wake word. The house spirit is home.

---

## Returning users: moving or reinstalling

Ran Domovoi before and setting up a new machine (or restoring after a wipe)?
Three things hold your state:

1. **The Postgres database** — settings, people and voice profiles, timers,
   reminders, library metadata, play history, plugin data, admin credentials.
   It lives in the Docker volume `domovoi-pgdata`. Either move the volume, or
   `pg_dump` the `domovoi` database on the old machine and restore it on the
   new one (Postgres publishes on `127.0.0.1:6432` — run `pg_dump` on the
   server itself — user/db `domovoi`, password in `domovoi/.env`).
2. **`~/.domovoi/` on the server** — the server's identity key
   (`server-identity.json`), downloaded Piper voices, chime sounds, trained
   wake-word models and their training clips, cover art, and your
   podcast/audiobook files. Copy the whole directory across, and keep any
   backup of it as safe as you would a private key. (Music, videos,
   pictures and documents live wherever their folders point — `~/Music`,
   `~/Videos`, `~/Pictures` and `~/Documents` by default — and move with
   them.)
3. **`domovoi/.env` in the repo checkout** — settings you changed from the
   dashboard are persisted here, and so is the Postgres password the
   database was initialised with. Copy it into the fresh clone before first
   start (the bootstrap leaves an existing `.env` alone).

Then start the stack as in [First install](#first-install) — migrations bring
a restored database up to the current schema automatically. Your admin
password rides along in the database; if you restored `~/.domovoi` without
the database, first boot simply writes a new setup code.

**Satellites** don't need reflashing, as long as `~/.domovoi` came across:
every satellite holds itself to the server's identity key (a prepared card
has it built in, a spare device gets it when it joins, a hand-built Pi
records the first server it meets). A server with a new key is "a different
server" to them, and each one has to be prepared or added again
([Server identity](docs/SECURITY_PRIVACY.md#server-identity-which-core-a-satellite-belongs-to)).
A new address is usually found on its own: a Pi or a Linux laptop looks for
the server by name, then at its last address, then across its own network.
A Pi that still can't find it: edit `domovoi_url` in its
`~/.domovoi/config.toml`. Satellites re-sync sounds and wake-word models
from the server automatically.

**The Windows installer** can't yet keep the data through an uninstall and
reinstall onto it; that is not in it yet
([Not in this build](docs/WINDOWS_HOST.md#not-in-this-build)).

---

## How the pieces fit together

```mermaid
flowchart TB
    subgraph LAN["Your home network"]
        subgraph Server["Domovoi server (an always-on PC)"]
            CORE["Core voice service<br/>FastAPI :6370<br/>Whisper (GPU or CPU) · routing · TTS"]
            WEB["Web dashboard backend<br/>FastAPI :6369"]
            OLLAMA["Ollama :11434<br/>local language models"]
            GUARD["Container guard<br/>(runs in the core)"]

            subgraph DOCKER["Docker containers"]
                subgraph WATCHED["Watched by the container guard"]
                    PG[("Postgres 16<br/>host :6432<br/>Flyway migrations at start")]
                    MPD["Music player (MPD), one per room<br/>control 6650+N · stream 8050+N"]
                    SEARX["SearXNG: internet lookups<br/>127.0.0.1:6888, only while<br/>the internet answer allows it"]
                    LETTA["Letta: voice chat mode<br/>opt-in"]
                    PLUG["Plugin containers<br/>declared by their plugin"]
                    subgraph FH["Network domovoi-fh, firewalled off the house's ports"]
                        TOR["Friend Houses Tor<br/>domovoi-tor"]
                    end
                end
                TRAIN["Wake-word trainer<br/>only while training · not watched"]
            end
        end

        subgraph KITCHEN["Kitchen room"]
            SAT1["Satellite<br/>Pi + mic board"]
        end
        subgraph BEDROOM["Bedroom"]
            SAT2["Satellite<br/>spare laptop or old phone"]
        end
        BROWSER["Browser<br/>dashboard on :6369"]
        DROID["Android app"]

        SAT1 -- "WebSocket audio :6370" --> CORE
        SAT2 -- "WebSocket audio :6370" --> CORE
        CORE --- OLLAMA
        CORE --- PG
        WEB --- PG
        WEB -- "admin proxy" --> CORE
        CORE -- "starts and controls" --> MPD
        CORE -- "lookups" --> SEARX
        CORE -- "shared sockets" --> TOR
        CORE -- "plugin code" --> PLUG
        CORE -- "runs a training" --> TRAIN
        GUARD -. watches .-> WATCHED
        MPD -- "audio stream 8050+N" --> SAT1
        MPD -- "audio stream 8050+N" --> SAT2
        BROWSER --> WEB
        DROID --> WEB
    end

    INET["Internet"]
    CORE -. "optional: news feeds, radio directories,<br/>model downloads — never your voice" .-> INET
    SEARX -. "when allowed" .-> INET
    TOR -. "Friend Houses, when on" .-> INET
```

Two server processes share one Postgres: the **core** (`:6370`) owns
everything real-time — speech-to-text, intent routing, handlers,
text-to-speech, satellite WebSockets, background workers, the plugin runtime
— while the **dashboard** (`:6369`) serves the web UI and proxies
live-state/admin actions to the core. Each room gets its own MPD music
daemon, provisioned lazily in Docker when the room's satellite first
connects. The other containers come and go with what you use: SearXNG
while the internet answer is Yes or Sometimes, Letta only in voice chat
mode, the Friend Houses Tor container (on its own network, which the
installers firewall off the house) only while Friend Houses is on, a
plugin's own containers while that plugin runs, and the wake-word trainer
only while a word trains. The container guard watches all of them except
the trainer ([container guard](docs/CONTAINER_GUARD.md)). TTS is local by default — three public-domain neural **Piper**
voices rendered on the server through Domovoi's own permissively licensed
text-to-phoneme converter, so Domovoi's replies are spoken on your own
hardware, and the engine chain falls back gracefully (`piper → system`)
so the house keeps talking whatever happens. No online voice is offered
([what goes out](docs/INTERNET.md#what-goes-out)).

Deep dive: [Architecture](docs/ARCHITECTURE.md) ·
[UML diagrams](docs/uml/) · [API reference](docs/API_REFERENCE.md)

---

## What each interface can do

| | Voice satellites (Pi; spare computer) | Phone room mode | Web dashboard | Android app |
|---|---|---|---|---|
| Hands-free voice, wake word | ✅ the whole point | ✅ | — | — |
| Barge-in (interrupt Domovoi mid-sentence) | ✅ off until you turn it on | — | — | — |
| Push-to-talk voice | — | — | — | ✅ chat mic, and the phone's own chat |
| Intercom & drop-in between rooms | ✅ initiate & receive; drop-in needs an echo-cancelling mic (the XVF3800 board, or a speakerphone on a laptop) | ✅ as a satellite; drop-in on phones with an echo canceller | — | ✅ drop into a room from your phone |
| Music playback | ✅ in-room via MPD | ✅ the room's music | ✅ full web player | ✅ native local player |
| Edit a room's shared queue (jump, reorder, add, drop, save as playlist) | voice: play / skip / stop, "add <song> after this" | voice: play / skip / stop | ✅ drag to reorder, search to add | ✅ move, remove, play next, search to add |
| Browse & organise files across every library | — | — | ✅ drag and drop to move | ✅ move via a destination picker |
| Library, playlists, podcasts, audiobooks, news | voice control | voice control | ✅ full management | ✅ dashboard parity |
| Videos | — | — | ✅ | ✅ |
| Text chat with Domovoi, pictures included | — | — | ✅ | ✅ |
| Read a document aloud; take a letter | voice: "read me the lease", "take a letter to the landlord" | — | — | — |
| Explain a photographed letter | — | — | ✅ in chat | ✅ in chat (on the phone, or via the home server) |
| Print and scan with network printers | voice: "print my cover letter", "scan this to Documents" | — | ✅ Settings, Files, Documents | ✅ Settings, Files (on the home server's files) |
| A chat that answers with no server | — | — | — | ✅ 64-bit phones with 8 GB or more |
| Save music, podcasts & audiobooks to your device | — | — | ✅ browser download | ✅ music & podcasts via DownloadManager; audiobooks into the app, played offline |
| Calendar, documents, people & voice profiles | voice where it makes sense | voice where it makes sense | ✅ | ✅ |
| Household lists (to-do, shopping, your own) | voice: "add milk to the shopping list" | voice | ✅ Lists page | ✅ Lists screen (Tools): add, cross off, reorder, rename; updates live |
| Edit documents and spreadsheets (documents co-edited live) | — | — | ✅ | ✅ the server's Documents, same editors |
| Satellite management & per-room settings | — | — | ✅ edits a Pi's or spare computer's config remotely | ✅ view/manage |
| Custom wake-word recording & training | records the clips | wakes on a pushed model; no recording | ✅ record → train → push to room | — |
| **Plugin install & administration** | — | — | ✅ **only here** — admin-gated, two-phase trust screen | — (plugins add nothing to the app) |
| Admin setup / login | — | — | ✅ | — (no admin session; the Settings sections only an admin changes open the dashboard; removing an audiobook from the server asks for the admin password for that one delete) |

The differences are deliberate:

- **Satellites** are ears and a voice — no screens, no admin surface. With
  phones in room mode, they're the only interfaces with a wake word and
  *receiving* an intercom announcement or drop-in, because that needs an
  always-listening mic and a speaker in the room. A spare laptop or PC does
  what a Pi does with its voice, without the Pi's LEDs, mic-gain tuning or
  Wi-Fi self-healing. (The Android app can *initiate* a drop-in into a
  room — see below — since your phone already has a mic and speaker; it
  just isn't a satellite others can call into unless it is in room mode.)
- **Phone room mode** turns an old phone or tablet on its charger into a
  satellite for a room (voice, plus the room screen): wake word, answers,
  the room's music, announcements, and drop-ins where the phone has an
  echo canceller. It has no barge-in and no chat
  mode, and the dashboard doesn't restart, upgrade or reconfigure it
  (beyond its voice, which the server keeps for it); the app updates like
  any other app
  ([what a phone in room mode does](docs/SPARE_DEVICE_SATELLITES.md#what-a-phone-in-room-mode-does)).
- **The dashboard** is the seat of management. It is the *only* place plugins
  can be installed, and installs are admin-gated behind an explicit trust
  screen. It's also where you record, train, and deploy custom wake words and
  edit a Pi's or a spare computer's settings remotely.
- **The room queue belongs to the room, not to a device.** Each client also
  has its own private player queue, but the queue a room's speaker plays from
  is shared: anyone can add to it, reorder it, or drop a track, and every
  entry is tagged with the device that queued it. Devices name themselves in
  Settings, and an admin can take queue editing away from a named device —
  per room or everywhere — which is the answer to one household member
  commandeering the kitchen. A guest's phone that was never paired can
  change the queue of a room that is playing, and pause or skip it, until
  an admin bans that guest (or switches guest access off).
- **The Android app** mirrors the dashboard plus a native music player.
  Plugins add nothing to it. Its Stations and Images screens are the app's
  own, written by Coders Farm for its Radio and image plugins, and they show
  only while the server has that exact plugin; any other plugin's pages
  open in the phone's browser, on the dashboard.
  Server administration (greetings, voices, wake words, models,
  configuration) stays on the dashboard: the app's Settings keeps what
  belongs to the phone (Rooms, People, Phones & browsers, Printers &
  scanners, App layout, Voices, About & updates, Health), and the sections only the
  dashboard changes open the dashboard.
  From a satellite's page it can also **drop into that room** — your phone
  joins the room's live two-way audio bridge directly, so you can talk to
  whoever's there from anywhere on the network. Its **Chat** lists the
  home server's chats and the phone's own (answered on the phone
  itself with no server), each marked with where it runs.

---

## Plugins

Plugins extend the Domovoi server: a single package can add voice handlers,
background workers, dashboard pages, live connections (SDK 1.11), its own
database schema, and its own settings. Plugins never ship code to the
Android or iPhone app. The apps' Stations and Images screens are the apps'
own, shown only when Coders Farm's Radio or image plugin is installed
([what a plugin does not get](docs/PLUGIN_DEVELOPMENT.md#1-concepts--what-a-plugin-can-do)). The bundled **[radio plugin](plugins/radio)** (publisher:
Coders Farm, MIT) is the reference example — internet radio and FM via
RTL-SDR, with station search, favorites, live tuning, and passive song
detection that feeds the media-acquisition queue.

Installing (dashboard → Plugins, admin login required):

1. **Upload a zip** or **paste a GitHub URL**.
2. Domovoi stages and validates the package, then shows a **trust screen**:
   the permissions the plugin requests (network? subprocess? hardware?), its
   own plain-language warnings, and the resolved dependency tree.
3. You read it and confirm — or don't. Plugins run with real access on your
   server, so only install ones you trust.

A plugin's dashboard pages sit in a frame with its name, publisher, a
"plugin" badge and its own accent colour, so they read as the plugin's
rather than Domovoi's.

Media-provider functionality (pulling from external sources) ships as
separately installed provider plugins — the core stays provider-neutral.

Want to write one? Start at the
[Plugin development guide](docs/PLUGIN_DEVELOPMENT.md).

---

## Documentation

| Doc | What's in it |
|---|---|
| [User manual](scripts/manual/manual.md) | The household's guide, read in the dashboard (**User Manual**) and in the app: what to say, every page, getting set up, with a map |
| [Setup runbook](docs/SETUP_RUNBOOK.md) | Day-one bring-up in order — server, first satellite, fleet, verification gates |
| [Will your Domovoi have internet?](docs/INTERNET.md) | The internet question: what Yes, Sometimes and No each switch, what's left to set up, and what to download before a server goes without |
| [FAQ](docs/FAQ.md) | Quick answers — privacy, hardware, common "can it…?" questions |
| [Screenshots](docs/screenshots/README.md) | The web dashboard page by page, from a demo house with made-up content |
| [Glossary](docs/GLOSSARY.md) | The words we use (satellite, handler, capability, band…) |
| [Architecture](docs/ARCHITECTURE.md) | How the system works, for the curious and the contributing |
| [API reference](docs/API_REFERENCE.md) | HTTP + WebSocket endpoints on :6370 and :6369 |
| [Plugin development](docs/PLUGIN_DEVELOPMENT.md) | Manifest, SDK, lifecycle, publishing your own plugin |
| [Contributing](docs/CONTRIBUTING.md) | Dev setup, tests, conventions, how to send changes |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | When the house spirit sulks — fixes for common problems |
| [Home Assistant](docs/HOME_ASSISTANT.md) | No integration; running both side by side and spoken announcements from Home Assistant |
| [Security & privacy](docs/SECURITY_PRIVACY.md) | Threat model, what leaves your network (and what never does) |
| [Security policy](SECURITY.md) | How to report a vulnerability privately, what's supported, what we promise in reply |
| [Licences of what Domovoi downloads](docs/LICENSES.md) | Every model, voice and dataset a server, the app or the trainer downloads, its licence, and what that means for a household and for a redistributor |
| [Container guard](docs/CONTAINER_GUARD.md) | Which Docker containers Domovoi watches, the alerts when one fails, and the freeze when one looks tampered with |
| [Satellite hardware](docs/SATELLITE_HARDWARE.md) | Parts list and step-by-step Pi satellite build |
| [Pi provisioning checklist](satellite/PROVISIONING.md) | Every command and gotcha for building a Pi satellite by hand |
| [Video satellites](satellite/VIDEO_SATELLITE.md) | A satellite with a screen: the now-playing kiosk build on a Radxa Zero 3W |
| [Satellites on spare devices](docs/SPARE_DEVICE_SATELLITES.md) | A Linux or Windows laptop or PC, an old phone or tablet in room mode, or an old computer from a USB stick, as a room's satellite (the USB stick is a preview): the command, QR code or stick, the approval, what each can and can't do |
| [Wake-word training](scripts/wake_word/README.md) | Training your own wake word from the dashboard: what it needs, what a training does to the server, the licence of what it makes |
| [Hardware validation](docs/HARDWARE_VALIDATION.md) | For contributors: the checks to run on real devices for room setup and video satellites |
| [Android compatibility](docs/ANDROID_COMPATIBILITY.md) | Which phones and Android versions the app runs on, which APK to install, what each version gets |
| [The Android app](android/README.md) | Building and releasing the app, and how it works: offline mode, the phone's own chat, room mode, what the phone holds |
| [Android app privacy policy](docs/PRIVACY_POLICY.md) | What the app keeps on the phone, what it sends and to whom (nothing reaches Coders Farm unless you email it) |
| [Running without an NVIDIA GPU](docs/CPU_HOST.md) | CPU-host settings, model sizing, what gets slower and what doesn't |
| [Running the server on Linux](docs/LINUX_HOST.md) | Linux install (one script or by hand), what differs from the Windows-first docs, systemd units, updates from the dashboard |
| [The Windows installer](docs/WINDOWS_HOST.md) | `DomovoiSetup.exe`: the Linux server in a private WSL2 distro, every change it makes on the PC and how uninstall undoes it |
| [UML diagrams](docs/uml/) | Sequence and component diagrams |
| [Roadmap](docs/ROADMAP.md) | What's being worked on now, next and later, without dates |
| [Release notes](docs/RELEASE_NOTES.md) | What changed, newest first, and what an operator has to do after an update |

---

## License & credits

Domovoi is [MIT licensed](LICENSE) — copyright © 2026
[Coders Farm](https://github.com/coders-farm-official).

Coders Farm is a 501(c)(3) nonprofit in Lansing, Michigan. Everything we
build is open source, and everything we teach is free: start learning to code
at [learn.codersfarm.org](https://learn.codersfarm.org). If Domovoi is useful
to you, you can support the work at
[donate.codersfarm.org](https://donate.codersfarm.org); donations are
tax-deductible to the extent the law allows.

Built on the shoulders of excellent open source: Whisper &
faster-whisper, Ollama, Piper, openWakeWord, MPD, Postgres, Flyway, and
FastAPI, among others. Reading PDFs and printing use pypdfium2 (Apache-2.0
or BSD-3-Clause) and ReportLab (BSD); the PDFium inside pypdfium2 is
BSD-style licensed, and portions of it are copyright The FreeType Project
(www.freetype.org), all rights reserved, under the FreeType licence. The
PDFs Domovoi makes (chat drafts, print templates) are set in the Liberation
Sans and Serif fonts (SIL Open
Font License 1.1, which ships beside them). The document editor is built
on Tiptap and ProseMirror, co-editing on Yjs and pycrdt (all MIT), and the
spreadsheet editor on Univer (Apache-2.0); the editors' fonts are OFL or
Apache-licensed families from the google/fonts collection, each with its
licence ([licences](docs/LICENSES.md)).

Domovoi's voices are three public-domain Piper voices by Bryce Beattie
(Norman and Kristin from LibriVox recordings, Linda from the LJ Speech
dataset), run on ONNX Runtime (MIT). The text-to-phoneme converter that
feeds them (`domovoi/phonemizer`) is Domovoi's own (MIT) on the Carnegie
Mellon Pronouncing Dictionary (BSD-2-Clause style; its licence ships in
`domovoi/phonemizer/data/cmudict/LICENSE`); no espeak-ng code or data is
used.

A new install's wake word, **Domovoi** (`domovoi_v1`), is Coders Farm's own
model, published in
[domovoi-wake-word-models](https://github.com/coders-farm-official/domovoi-wake-word-models)
with a model card naming every dataset it was trained on. Like
`hey_jarvis`, one of openWakeWord's pre-trained models (an older install's
default), it is licensed CC BY-NC-SA 4.0 (non-commercial), and each server
downloads it itself: neither is in this repository or the app. A wake word
you train on your own server can be shared under the same CC BY-NC-SA 4.0:
Settings → Voice → Wake words → **Download** gives a zip with the model,
`SHA256SUMS`, `MODEL_CARD.md` and the `LICENSE-NOTICE.md` to pass on with
it. A model trained on your household's voices is your call to share
([details](scripts/wake_word/README.md#5-licence-sharing-a-model-trained-here)).
Domovoi itself stays MIT.

The Android app's own chat runs Gemma 4 E2B (Apache-2.0) on
LiteRT-LM (Apache-2.0), and listens with Moonshine and Silero VAD (both
MIT) through sherpa-onnx (Apache-2.0). Room mode wakes with openWakeWord's
models on ONNX Runtime (MIT).

The question-answering model a server installs, `llama3.2:3b`, is under the
Llama 3.2 Community License. Every model, voice and dataset Domovoi
downloads, with its licence and what that means for a household and for
anyone passing it on: [docs/LICENSES.md](docs/LICENSES.md).

*May your stove stay warm and your wake word never misfire.* 🐈
