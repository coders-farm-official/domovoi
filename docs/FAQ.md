# Frequently Asked Questions

Short answers to common questions. For a term you don't recognize, see the [Glossary](GLOSSARY.md). When something's broken rather than confusing, head to [Troubleshooting](TROUBLESHOOTING.md).

**Privacy and the internet**
- [Is my voice data leaving my network?](#is-my-voice-data-leaving-my-network)
- [What touches the internet, and how do I turn each thing off?](#what-touches-the-internet-and-how-do-i-turn-each-thing-off)
- [Does it work offline?](#does-it-work-offline)
- [Does Domovoi look things up online?](#does-domovoi-look-things-up-online)
- [How long is what we say kept, and can we delete it?](#how-long-is-what-we-say-kept-and-can-we-delete-it)
- [Where is my data?](#where-is-my-data)
- [How do I back up and restore?](#how-do-i-back-up-and-restore)

**The server and rooms**
- [What hardware do I need?](#what-hardware-do-i-need)
- [Can it run without a GPU?](#can-it-run-without-a-gpu)
- [Is AI optional?](#is-ai-optional)
- [Linux or Windows for the server?](#linux-or-windows-for-the-server)
- [How do I add rooms?](#how-do-i-add-rooms)
- [Can a room have more than one satellite?](#can-a-room-have-more-than-one-satellite)
- [Can an old laptop or phone be a satellite?](#can-an-old-laptop-or-phone-be-a-satellite)
- [Why does Windows or my antivirus warn about the Windows satellite?](#why-does-windows-or-my-antivirus-warn-about-the-windows-satellite)
- [Can the PC that runs Domovoi also be a satellite?](#can-the-pc-that-runs-domovoi-also-be-a-satellite)
- [Can another device on my network pretend to be one of my satellites?](#can-another-device-on-my-network-pretend-to-be-one-of-my-satellites)
- [Can I drop in on a room from my phone?](#can-i-drop-in-on-a-room-from-my-phone)
- [Can I rename the assistant or change the wake word?](#can-i-rename-the-assistant-or-change-the-wake-word)
- [Which voice does Domovoi speak with, and can I fix how it says a word?](#which-voice-does-domovoi-speak-with-and-can-i-fix-how-it-says-a-word)
- [Does it support multiple people?](#does-it-support-multiple-people)

**Music, files and chat**
- [Who added that song, and can I stop someone adding more?](#who-added-that-song-and-can-i-stop-someone-adding-more)
- [Why does music keep playing after my song ends?](#why-does-music-keep-playing-after-my-song-ends)
- [Can a guest change the music?](#can-a-guest-change-the-music)
- [What music sources exist out of the box?](#what-music-sources-exist-out-of-the-box)
- [Can I save music, podcasts, or audiobooks to my phone or computer?](#can-i-save-music-podcasts-or-audiobooks-to-my-phone-or-computer)
- [Does the music visualizer keep the screen on?](#does-the-music-visualizer-keep-the-screen-on)
- [Can I move files around without the command line?](#can-i-move-files-around-without-the-command-line)
- [Why do long lists load as I scroll?](#why-do-long-lists-load-as-i-scroll)
- [Can I send pictures in chat?](#can-i-send-pictures-in-chat)
- [Can I ask a chat about a document?](#can-i-ask-a-chat-about-a-document)
- [Can Domovoi print and scan?](#can-domovoi-print-and-scan)
- [How do I ask Domovoi for help?](#how-do-i-ask-domovoi-for-help)

**Plugins**
- [What are plugins, and are they safe?](#what-are-plugins-and-are-they-safe)
- [Can I use it with Home Assistant?](#can-i-use-it-with-home-assistant)

---

## Is my voice data leaving my network?

**No.** Everything in the voice path is local:

- Your satellite streams microphone audio over your LAN to the Domovoi server (WebSocket on port 6370). It never goes further.
- Speech-to-text is **Whisper running on your own machine** (`faster-whisper`, on the processor or an NVIDIA GPU). No cloud STT.
- The language models are **local Ollama models** on the same machine.
- Voice-profile identification ("who's speaking") computes voice embeddings locally and stores them in your own Postgres.
- Domovoi's replies are spoken by **local voices** (Piper, with the computer's own system voice as a fallback). There is no online voice, so the text of a reply never leaves either.

**One thing does send audio out: song identification, and never from your microphone.** The bundled radio plugin can sample short clips of *radio streams* you've favorited, and the library enricher can fingerprint *your music files*, sending those to online identification services (Shazam via `shazamio`, optionally AcoustID/MusicBrainz). Radio-stream audio and library files, never mic audio. Both are switchable off ([every touchpoint, and how to turn each off](INTERNET.md#every-touchpoint-and-how-to-turn-each-off)).

## What touches the internet, and how do I turn each thing off?

Every outbound touchpoint, what it sends, where, and its own off switch, is one table in [Will your Domovoi have internet? → Every touchpoint, and how to turn each off](INTERNET.md#every-touchpoint-and-how-to-turn-each-off).
One answer turns them all off: **No, keep everything in the house** in Settings → Privacy → Internet.

## What hardware do I need?

**The server** — one machine that stays on:

- **Linux or Windows** — both supported; see [Linux or Windows for the server?](#linux-or-windows-for-the-server). Either way Docker runs Postgres and the per-room music daemons (Docker Engine on Linux and inside the Windows installer's private Linux; Docker Desktop on a Windows install by hand).
- An NVIDIA GPU is **not required**. The installers (the Linux script and the Windows installer) set the server up for its processor: Whisper `small.en` on the CPU, Qwen3 8B for routing (Qwen3 4B on a server with less than 24 GB of memory), Llama 3.2 3B for answers and Qwen3-VL 2B for pictures; Ollama uses a GPU by itself when there is one. A server set up by hand starts from defaults that want an NVIDIA GPU (Whisper `large-v3` on CUDA, a 14B tool-routing model); on a CPU-only or non-NVIDIA box, four settings get you a system that's instant for everyday commands and slower only for open-ended questions — see [CPU_HOST.md](CPU_HOST.md).
- **~20 GB of free disk** for the software and all default models (the two Ollama models alone are ~11 GB), **plus** room for your own media library on top (the Windows installer asks for 40 GB free on the system drive). The full breakdown and ways to shrink it are in the README's [Disk footprint](../README.md#disk-footprint) section.

**Satellites** — one small box for each place you want to talk to (a room can have several satellites, one is enough):

- Raspberry Pi Zero 2 W (or a Pi 4) with either:
  - **ReSpeaker 2-Mics Pi HAT** (V1 or V2.0 — they use different codecs, check yours), or
  - **ReSpeaker XVF3800 USB 4-Mic Array** — the nicer option: on-chip echo cancellation, gain control, and beamforming. Required for open-mic chat mode, and it makes talk-over barge-in reliable. (The spoken wake greeting works on either board: it plays before the satellite listens.)
- Powered speaker on the 3.5 mm jack, microSD card, decent power supply.

Full parts list and step-by-step setup: [Satellite Hardware](SATELLITE_HARDWARE.md) and the provisioning checklist in `satellite/PROVISIONING.md`.

## Can it run without a GPU?

Yes, with tradeoffs. A server set up by the installers already runs this way: Whisper `small.en` on the processor, and the Qwen3 router sized to its memory. By hand, Whisper's device is a setting (`WHISPER_DEVICE`, choices `cuda` or `cpu` — Settings → System → Advanced → Speech-to-text). On CPU you'll want a smaller Whisper model than the by-hand default `large-v3` (e.g. `small.en` or `medium`), and Ollama will run its models on CPU too, so responses get noticeably slower. The system works; it just stops feeling instant. If you're GPU-less, also prefer the fast-path voice commands (timers, music, announcements) — those never touch the LLM at all.

## Is AI optional?

AI chat and questions are. Both installers ask "Include AI chat and questions?" (the default is Yes) and say how much it downloads: about 6.4 GB of AI models, or about 9.1 GB on a server with 24 GB of memory or more (a Windows PC: about 32 GB). An admin can turn them off or on later under **AI chat and questions** in Settings → Chat & AI → Models (`AI_FEATURES=false` in `domovoi/.env` does the same).

With them off, Domovoi never asks a language model anything:

- **Still works:** every voice command on its usual phrases (timers, reminders, alarms, the clock and calendar, the calculator, music, playlists, podcasts and audiobooks, intercom, drop-in, announcements, voice notes, "remember that ...", news headlines, Wi-Fi and volume, "what can you do"), plugins' own commands, the dashboard and the app.
- **Stays, because voice control needs it:** speech recognition (Whisper), Domovoi's voices (Piper), the wake word and voice recognition of who is talking. These are small machine-learning models too; they run on your server and never leave it.
- **Off:** answers to open questions and jokes, commands said in your own words that no fixed phrase matches, "are you sure?" and the weather, the personal news briefing summary, pictures in chat, background memory suggestions and voice chat mode. A question gets "I can't answer that one — I'm set up for home commands. Say 'what can you do' to hear them." Chat is hidden on the dashboard; the app keeps only the chats on the phone itself, where the phone can run its own model. Help still works: the **?** on each page searches the manual and the docs, with no model. Plugins that ask the AI models (a Kiwix summary) get a clean "AI features are turned off" and skip that step.

The Ollama program stays installed either way, so turning AI on later is a switch and a download, not a reinstall: Settings → Chat & AI → Models shows the size first, then downloads the models with a progress bar. On a box set to stay off the internet, the download has to come from a registry in the house.

## Does it work offline?

Yes — local-first is a tested contract, not a slogan. Every voice handler declares how much network it needs (`no`, `degraded`, or `yes`), and any handler that isn't fully local **must** implement an offline fallback — the test suite enforces this. A background connectivity probe tells the router whether the internet is reachable, and the router picks the right path per turn.

What that looks like in practice with the internet down:

- **Fully works:** wake word, STT, intent routing, timers, reminders, clocks, calculator, music from your local library, intercom announcements, room-to-room drop-in calls, voice profiles, memory. All local.
- **Degrades:** every voice is local, so Domovoi keeps talking in the same voice (or the robotic system voice if the Piper voice is missing). Radio needs the stream, but its handler degrades per-command, and FM over an RTL-SDR keeps working.
- **Says so honestly:** web-backed answers ("double-check that", news fetches) reply that they can't check right now instead of guessing. On a server answered **No** they say "I'm set to stay off the internet" instead.
- **Satellite-side:** if a satellite loses the *server* for 30+ seconds, the next wake word plays a locally cached "having trouble reaching the network" clip instead of silence.

Running with no internet at all, for good? Answer **No, keep everything in the house** in Settings → Privacy → Internet: the server then contacts nothing outside your network. A few things can only be downloaded online, and the first time Domovoi needs one is usually the day the line is down, so do [Before you disconnect](INTERNET.md#before-you-disconnect) first.

### Can Domovoi run with no internet at all?

Yes. After setup (which downloads the software and models), answer **No, keep everything in the house** — at the end of the first-run setup, in Settings → Privacy → Internet, or `INTERNET_ACCESS=never` in `domovoi/.env`. The server then makes no connection outside your network: the internet check stops, and every feed, search, voice, download and plugin request is refused with "internet access is turned off for this box (Settings → Privacy → Internet)". Music, timers, intercom, voice, the dashboard and the app keep working. What it can't switch off is outside the server: the satellites' own operating system keeps its clock with public time servers, and your browsers and phones are your browsers and phones. [INTERNET.md](INTERNET.md#internet-in-the-house-but-domovoi-shouldnt-use-it) has the details.

## What address do I type to open Domovoi?

`domovoi.local`, in any browser on your home network: no `http://`, no port. That is the server's name on your network (Settings → System → Advanced → Network name; `domovoi2.local` if another device already had the name), and Domovoi answers on port 80 with a hand-over to the dashboard's real address, `domovoi.local:6369`. This **short address** is on by default when nothing else on the server uses port 80. When something does (a Pi-hole, a web server, IIS on the Windows PC), Domovoi leaves port 80 to it and Settings says "Short address off: something else on this computer uses port 80."; then type `domovoi.local:6369`. Where the name doesn't work at all (some older phones, or a network that blocks it), the server's IP address with `:6369` always works, for example `192.168.1.20:6369`.

The dashboard's header shows the easiest of these that works on your network; click it to copy it and send it to someone. An admin can turn the short address off under **Settings → System → Advanced → Short address** (to free port 80 for something else). Links, rooms and the phone apps keep using `:6369` either way, so turning it off breaks nothing.

## How do I add rooms?

A room is a place and a satellite is the device in it. A satellite is added
from the dashboard, as admin, in **Settings → Household → Rooms**:

1. **A Raspberry Pi:** put Raspberry Pi OS Lite (64-bit) on a microSD card,
   put the card in the server's card reader, and use **prepare a room's
   SD card → wi-fi setup portal → Prepare** (on the Windows install, which
   can't see SD cards, **download overlay zip** and unzip it onto the
   card). The Pi starts its own setup Wi-Fi (`Domovoi-Setup-<id>`): join it
   from a phone, pick your home Wi-Fi and name the room.
   **A spare laptop or PC, or an old phone**: **add a spare
   device** — see [Can an old laptop or phone be a satellite?](#can-an-old-laptop-or-phone-be-a-satellite).
2. The device says or shows a six-digit code: type it under **waiting for
   approval** and press **approve**.
3. That's it — on first connection, the server provisions that room's own
   music daemon (an MPD container with its own queue and volume) and the
   room appears in Settings → Household → Rooms. To put a second
   satellite in the same room, set it up the same way and, when you approve
   it, answer **Which room is it in?** with that room (see
   [Several satellites in one room](#can-a-room-have-more-than-one-satellite)).

Room names matter: intercom ("announce in the kitchen…") and drop-in ("drop in on the kitchen") address rooms by the name you gave. The by-hand route (installing the satellite client and pointing it at `ws://<server>:6370` yourself) is in the [Satellite hardware guide](SATELLITE_HARDWARE.md) and `satellite/PROVISIONING.md`.

## Can a room have more than one satellite?

Yes, since 1.5.0 (a Pi on the counter and a phone on the shelf, say). The
room stays one place with one name, queue, voice and set of wake words;
each satellite is a device in it. Approve the second one into the existing
room (**Which room is it in?**), or move a satellite later from the room's
drawer in **Settings → Rooms**.

- **One wake word, one answer.** The first satellite to hear it takes the
  turn; the others stay quiet, and Domovoi transcribes the microphone that
  heard you best.
- **Every speaker plays.** Music, answers, timers, alarms, announcements
  and plugins' speech come out of every speaker in the room at the same
  moment. If they sound a little apart, use **Tune the speakers** in the
  room's drawer.
- **Older satellites** (before 1.5.0) can't join in; they play only when
  they are the room's only satellite.

More in [Satellites on spare devices](SPARE_DEVICE_SATELLITES.md#rooms-and-their-satellites)
and [Troubleshooting](TROUBLESHOOTING.md#rooms-with-several-satellites).

## Can an old laptop or phone be a satellite?

Yes. A spare **Linux laptop or mini PC** (Debian or Ubuntu) or a
**Windows 10/11 laptop or PC** can be a room's satellite: in the dashboard, as admin,
**Settings → Household → Rooms → add a spare device**, name the room, and run the
one-line command it shows on that computer (one for Linux, one for Windows
PowerShell). It installs the satellite, makes sure it's talking to *your*
server, and shows an approval code to type into the dashboard. A tray
icon shows when its microphone is live and mutes it. An old **Android
phone or tablet** joins through the app's room mode, which turns it into a
satellite for a room (voice, plus the room screen), by scanning the QR code
on the same card. Either gives its room everything a Pi does: wake word,
music, timers, announcements, and guests can control the room's music while
it plays. What they
don't do is the Pi-only part (LED ring, Wi-Fi self-healing, plugins that
install something as root). Setup, power and battery advice:
[Satellites on spare devices](SPARE_DEVICE_SATELLITES.md).

## Why does Windows or my antivirus warn about the Windows satellite?

The satellite's own code isn't signed yet (Coders Farm's signing is on
hold), and its install command downloads and starts programs — exactly what
antivirus software watches for. Python, which runs it, is signed by the
Python Software Foundation, and the command runs nothing unless every file
matches the SHA-256 your own dashboard quoted. Windows' microphone icon
also stays on while it listens for the wake word: that is Windows telling
you the truth. Details:
[Windows laptops and PCs](SPARE_DEVICE_SATELLITES.md#windows-laptops-and-pcs).

The double-click Setup the same card offers (`DomovoiSatelliteSetup.exe`) is
unsigned too: Windows SmartScreen says "Windows protected your PC", and
**More info**, then **Run anyway** goes on. The same goes for the server's own
`DomovoiSetup.exe`, and Smart App Control, if it is on, blocks that one
outright ([Unsigned](WINDOWS_HOST.md#unsigned)).

## Can the PC that runs Domovoi also be a satellite?

Not with the spare-device installer: it refuses on the server PC. The
server runs whether or not anyone is signed in, a satellite needs a signed-in
account for the microphone, and two programs would compete for the sound
devices. The server PC can't be a satellite yet; nothing in the server's tray
icon on Windows does that.

## Can another device on my network pretend to be one of my satellites?

Not once the real one has connected. Each satellite generates a random **pairing token** on first boot (stored in `~/.domovoi/pairing_token`) and sends it when it connects. The server remembers only a hash of it and binds the room to that token the first time it sees one — *trust-on-first-use*. After that, a device connecting as `kitchen` must present the matching token or the server refuses it and closes the connection. So a random device that later tries to impersonate a paired satellite is turned away (it can't issue commands as that room, and can't receive that room's audio or drop-ins).

Strict pairing (`SATELLITE_PAIRING_STRICT=true`) is the default, and it closes the one gap left: a tokenless connection is refused, and a room's first pairing waits for you to type in the six-digit code the satellite is showing and saying. The honest caveat applies only if you turn strict pairing off: then the *first* token wins, so there's a one-time window when a room has never paired — whoever connects first claims it — and a device with no token at all can connect under any room name nobody has paired. Such a tokenless room can use itself (timers, music, voice commands) but can never drop in on another room or announce into one.

**Re-flashed a Pi or gave a room a new satellite?** The new device has a new token that won't match, so it'll be refused. Clear the old pairing from the dashboard: Settings → Household → Rooms → open the room → **Overview → Reset pairing** (admin login required). The next connection re-pairs. Full details: [Security & Privacy](SECURITY_PRIVACY.md#satellite-pairing-ws-auth).

One thing pairing does *not* do: encrypt the audio. traffic on your home network isn't encrypted yet (TLS is on the [roadmap](ROADMAP.md)), so keep satellites and the server on a network segment you control. See [Is my voice data leaving my network?](#is-my-voice-data-leaving-my-network).

## Can I drop in on a room from my phone?

Yes. In the Android app, open a satellite's page and tap **drop in from this phone** — your phone joins that room's live two-way audio bridge directly (it needs microphone permission and the room's satellite must support full-duplex/AEC audio). It works like a room-to-room drop-in, except your phone is one end instead of another Pi. Note it's one-directional in the sense that *you* call into a room; a room can't call your phone, because your phone isn't a room's satellite (unless it is in room mode).

## Can I rename the assistant or change the wake word?

Those are two separate things:

- **The name** — what Domovoi calls itself in speech and on screen — is the `BOT_NAME` setting (Settings → System → Advanced → Identity, takes effect on restart). Greeting and sample clips re-render with the new name.
- **The wake word** — the phrase that opens the mic — is "Domovoi" on a new install: Coders Farm's published model (CC BY-NC-SA 4.0, downloaded by your server; [LICENSES](LICENSES.md)), given to each new room. An older install's rooms keep `hey_jarvis`, one of openWakeWord's prebuilt models, and a room also wakes on it until its server has given it its word (or while the server can't download it).
  - **Settings → Voice → Wake words** sets the household's word for new rooms, and can give a room another.
  - **More than one word:** a room can wake on up to 5 (3 on a Pi Zero 2 W): **Add another wake word** in the room's Settings. Most homes need one; each extra word makes accidental wake-ups a little more likely.
  - **Your own word:** train one from the same page. An admin turns training on once (**Turn on wake-word training**: about 32 GB of disk, a one-time download of about 22 GB, and a server with 24 GB of memory; no shell needed). Then record clips *on the room's own mic*, press **Train** (about 20 minutes on an NVIDIA GPU, over an hour on the processor, where it asks first because voice answers slow down meanwhile) and push the model to a room. Training runs on your server, in a Docker container.
  - **Sharing:** **Download** packs a trained model under CC BY-NC-SA 4.0 (non-commercial), with the licence notice in the zip; a model trained on your household's voices is your call to share. Details: [wake-word training](../scripts/wake_word/README.md).

## Which voice does Domovoi speak with, and can I fix how it says a word?

One of three local voices, all public domain (recorded from LibriVox and the
LJ Speech set, trained from scratch by Bryce Beattie): **Kristin** (female,
the default), **Norman** (male) and **Linda** (female). Pick one under
Settings → Voice → Voices → *Domovoi's voice*, where each has a play
button; the phone app offers the same three. The words are turned into
sounds by Domovoi's own converter (a pronouncing dictionary, rules and a
small model, all permissively licensed).

When a word comes out wrong, add it under Settings → Voice → Voices → *Say it
like*: the word, and how it sounds in plain letters, with the stressed
syllable in capitals (`Siobhan` = `shiv-AWN`). The play button lets you
hear it before you save. Domovoi ships three entries, which you can change
or remove: Domovoi = `DOH-moe-voy`, Domo = `DOH-moe`, Nguyen = `win`.

## What are plugins, and are they safe?

Plugins add features to the server — new voice commands, dashboard pages, background workers, media providers. They never add code to the Android or iPhone app: the apps' Stations and Images screens are the apps' own, shown only when Coders Farm's Radio or image plugin is installed, and any other plugin is used from the dashboard in the phone's browser, or by voice. The bundled radio plugin (`plugins/radio`, published by Coders Farm) is the reference example; it's how Domovoi does internet radio and FM.

The trust model, in plain words:

- **A plugin is code running on your server, with the access your server has.** There is no sandbox pretending otherwise. The install screen says exactly that, and it can't be skipped.
- **You see what you're agreeing to before anything runs.** Install is two-phase: the zip is staged and inspected first, and you get a preview — publisher, permissions (network? subprocesses? hardware?), the plugin's own plain-English warnings, its dependency list — before you confirm. The radio plugin's warnings, for example, disclose that it can send stream clips to Shazam and query external station directories.
- **No plugin code executes before you confirm.** Dependencies are declared in a hash-pinned lockfile and checked in a throwaway subprocess with `pip --dry-run --require-hashes --only-binary` — so not even a package build script runs pre-confirmation. The staged files are hash-verified again at confirm time.
- **Installing, upgrading, and removing plugins requires the admin password.** So does every other risky operation ([Security & Privacy](SECURITY_PRIVACY.md) has the full model).
- **Plugins keep their data in their own database schema** and write their own log file (`~/.domovoi/logs/plugin_<slug>.log`), so you can see what one is doing and remove it cleanly (uninstall offers keep-data or purge).

Bottom line: treat a plugin like any software you install on a home server — only install from publishers you trust. Want to build one? [Plugin Development](PLUGIN_DEVELOPMENT.md).

## Can I use it with Home Assistant?

There is no integration. Domovoi doesn't control lights, locks or thermostats, and Home Assistant has no Domovoi add-on. The two run side by side on one network without conflict, and a Home Assistant automation can POST to Domovoi's announce endpoint to speak a message in any room ("the wash is done"). See [Home Assistant](HOME_ASSISTANT.md) for the recipe and [API Reference](API_REFERENCE.md) for the endpoint.

## Does it support multiple people?

Yes — voice profiles. Say "I'm Sarah" and confirm, and Domovoi enrolls your voice locally (a voice embedding, stored in your Postgres — no cloud). After that it recognizes who's speaking and personalizes: per-person memories ("remember that I…"), per-person news topics, and a "who am I?" you can ask any time. You can introduce someone else ("this is my friend Alex"), and "forget me" deletes a person's profile, voice data and what they've said to Domovoi outright — when it is their own voice saying it (a voice that only claimed the name loses just what it added; an admin can always remove a person in Settings → Household → People). Matching thresholds are tunable per household if siblings' voices collide.

If a room doesn't recognize you yet, say "I'm Sarah" there too: Domovoi asks "I already know someone called Sarah. Is that you?", and a yes teaches it your voice in that room. A no adds you as a new Sarah; with two Sarahs Domovoi won't guess which one is speaking when the name is said, so give one of you a different name in Settings → Household → People (an admin can rename anyone there), or use a full name ("I'm Sarah Lee") from the start.

## Who added that song, and can I stop someone adding more?

Every room has one queue — MPD's, the thing its speaker actually plays from —
and anyone in the household can add to it, reorder it, or drop a track from the
dashboard's **Music → Room queue** tab or the app's **room queue** tab (on a
phone, each row has **play next**, **up** and **down** buttons, since a finger
can't drag; a room's card on the Music page has a **queue** button that goes
straight there). Both have a search box to add songs from the library, and
**save as playlist** keeps the queue as a new playlist. A guest's phone can too, while the room is playing — see the
next question. Each
entry carries a quiet "added by *<device>*" note, so "who put this on?" has an
answer. Entries Domovoi has no record of — a voice request, or a cast from
before this existed — simply show nothing rather than a guess.

Devices name themselves. A browser or phone introduces itself the first time
it loads, with a name guessed from the platform ("Chrome on Windows", "Pixel
8"); you can rename it in **Settings → Household → Phones & browsers**, on the
dashboard or in the app. Renaming relabels that device's existing queue entries.

An admin can take queue editing away from a named device — one room, or every
room — in **Settings → Household → Phones & browsers → Queue access**. A blocked device can still
*see* the queue and still plays its own music; it just can't change what the
room plays. Be aware of what this is: a device tells the server who it is, and
nothing on a trusted LAN forces it to be honest, so this reliably settles a
household argument but is not a defence against someone determined. Changing
blocks needs the admin password, which is why it can't be undone from the
device it was applied to.

## Why does music keep playing after my song ends?

That's **Up next**. A song played from a list goes on with the rest of that
list; a song on its own is followed by similar songs from your library; a
whole playlist plays as it is, and similar songs join when it is nearly
over. Each pick says why it was chosen ("same artist", "90s"), and
**less like this** plays less of that artist. To stop it, switch off
**autoplay** at the top of **up next**: in the browser's player queue
(remembered in that browser) or on a room's queue (for that room). What
the mix learns from comes from the listening history, kept 1 year by
default; Settings → Privacy → **Listening history** sets 90 days or
**Don't keep**. Details: the user manual's
[Up next](../scripts/manual/manual.md#up-next).

## Can a guest change the music?

Yes, in a room that is **playing**, until you say otherwise. A friend's phone
on your Wi-Fi that was never paired can open the dashboard, see what each room
is playing, and for a room that's playing (or paused on a song): see its
queue, add songs (a track's details → **add to *room*'s queue**), reorder them,
play one next, remove one, and pause, resume, skip or go back. A guest has no
clear and no stop button, can't remove the song that's playing (it can skip
it), adds at most 50 songs at a time and removes one at a time, and can't
touch a room that isn't playing — starting music in a quiet room still needs
a paired device. (A determined guest could still empty a queue song by song;
that's what bans and the off switch are for.) A guest's songs show "added by a guest", and
a guest isn't shown the names of the household's devices.

To stop one guest, sign in as admin and open **Settings → Household → Phones & browsers → Guests**:
it lists the guests that changed the music this week, with a **ban** button
(also on any queue entry a guest added). A ban covers every room, the queue
and the playback buttons alike, and matches that phone's browser id or its
address on your network — so a guest who clears the browser is still caught
while the network gives it the same address. It never affects a paired
device or an admin. Like a queue block, a ban is household policy rather
than a lock: someone determined can come back as a new guest. To turn guest
access off altogether, switch off **Guests can change the music in a playing
room** in **Settings → System → Advanced → Security**; then only paired devices
and the admin can see or change a room's queue or its playback.

## Can I move files around without the command line?

Yes — the dashboard's **Files** tab moves things by drag and drop. Pick up a
row (or select several first) and drop it on a folder, on a breadcrumb crumb to
move it up a level, or on another library's chip to move it into that library.
Dragging files in from your desktop uploads them into the folder you're looking
at. On the phone, each row has a **move** button that opens a destination
picker, since dragging inside a scrolling list isn't a real gesture.

A few things it refuses on purpose: moving a folder into itself, moving
anything onto a name that already exists (nothing is ever silently
overwritten), and moving *out of* a read-only library — a removable drive or a
read-only plugin library can only be a destination. Copying *off* a removable
drive is still the separate **import** action. If one item in a multi-item drag
fails, the rest still move and the toast says what went wrong.

None of this needs the admin password — browsing, downloading, uploading,
moving and importing are open to every phone and browser on your network, the
same as playing music. **Deleting** is the one Files action that does need
admin, because it's the one that destroys something; the phone app can't sign
in as admin, so delete from the dashboard. If one device keeps filling the
libraries with things it shouldn't, an admin can take uploading, moving and
importing away from it in **Settings → Household → Phones & browsers → Files access** — it can still
browse and download, and the Files page tells it why it can't change anything.
The same honesty note as queue access applies: a device tells the server who it
is, so this settles a household argument rather than stopping someone
determined.

## Why do long lists load as I scroll?

The Music library, Videos, Files, Audiobooks and Podcasts (shows and each
show's episodes) never load a whole list at once — on the dashboard, in the
Android app, and for the phone's own music, videos and folders in the app.
Each shows the first 50
rows and fetches the next 50 as you near the bottom, with a line at the end
saying how many are showing out of how many there are ("showing 100 of
5,232"). If a page fails to load, that line offers **retry**.

Search boxes, filters and sorting ask the server, so they cover everything
— not just the rows already on screen — and changing one starts the list
again from the top. **Select all** (Music's library table, the Files list)
selects the rows that are loaded, and its tooltip says how many; scroll
further first if you want more of them in the selection.

Videos and Files are read straight from disk, and the server remembers each
listing between pages so scrolling stays quick. It notices files added,
removed or renamed on its own; a file edited in place by some other program
can take up to five minutes to show its new size, or press **Rescan** on the
Files page.

## What music sources exist out of the box?

- **Your local library** — files in your music folder (`MUSIC_DIR`, default `~/Music`), indexed and playable by voice per room, with playlists, album art in the browser player, and optional automatic metadata cleanup.
- **Browser uploads** — drag files into the dashboard; they land in `MUSIC_DIR/uploads/` and join the library.
- **Internet radio + FM** — the bundled radio plugin: station search, live tuning (FM needs an optional RTL-SDR dongle), a Recent strip of the last ten stations you played, and passive song detection on stations you favorite. Clicking a station plays it; favoriting is a separate act, because a favorite is also what puts a station on the detectors' poll schedule.
- **Podcasts and audiobooks** — RSS podcast subscriptions and local audiobook files, streamed per room like music.

Anything beyond that — pulling music from external sources — is the job of **provider plugins**: separately installed plugins that act as acquisition fulfillers and streaming search providers. Core deliberately speaks only a generic "media acquisition" vocabulary; a request like "add this song" is queued as a structured request, and whatever provider plugin you've installed fulfills it. No fulfiller installed? Requests wait in the queue (visibly, on the dashboard) and Domovoi tells you a provider is needed — installing one later drains the backlog.

## Can I save music, podcasts, or audiobooks to my phone or computer?

**Yes.** Any track, downloaded podcast episode, or audiobook has a **save** button (a download icon) next to its play control. It pulls the actual audio file off your Domovoi server onto the device you're using:

- **Web dashboard** — the file goes to your browser's normal downloads folder, named after the track/episode/book.
- **Android app** — tracks, episodes and videos are handed to the system DownloadManager and land in **`Downloads/Domovoi/`**, with a progress notification; they show up in your Files app afterwards.

On the dashboard, single-file audiobooks (a `.m4b`, say) save as that one file, and **folder audiobooks** (a book split into per-chapter files) come down as a single **zip** of all the chapters.

**Audiobooks in the Android app** save differently. Open a book and choose **save to phone**: the app shows the book's size first, then keeps the book in its **own storage** (not Downloads, and never in the phone's Music), with its cover and chapters, so it plays with no server at all. It needs no storage permission and goes away with the app. The download runs in the background with a notification you can cancel from; if you leave home halfway, it pauses and **finishes by itself** the next time the server is in reach (there is no mobile-data setting: the server is only on your home network). The book keeps **one place on the phone** for streaming and the saved copy, sent to the server whenever the phone is connected; if a room got further in the same book for you, the app offers to **continue from** that room. Saved books are on Audiobooks → **your device**, which shows the space they use and removes them, and on the app's Audiobooks tab when there is no server. Books saved to `Downloads/Domovoi/` by earlier versions stay there.

All of this is a plain file copy from your own server — nothing goes to the internet.

## Does the music visualizer keep the screen on?

Yes, while it plays full screen: the screen doesn't dim, lock or start the screen saver, like a video site in full screen. It lets go when you leave full screen, pause, or switch away from the tab. (The visualizer is in the dashboard's player: open it from the player bar and switch on **milkdrop visualizer**.)

How, and where that was checked:

- **Chrome and Edge** (checked on Windows): on an `https://` or `localhost` address the browser's Screen Wake Lock does it. On the usual plain `http://192.168.1.20:6369` address that lock doesn't exist, so the dashboard plays a tiny invisible, silent video while full screen, which these browsers keep the screen on for. Both ways were measured to hold the screen on and to let go.
- **Firefox and Safari**: not measured. They offer the Screen Wake Lock on `https://` addresses, and the dashboard uses it there. On plain `http://` the same silent video is tried; whether it keeps the screen on there hasn't been checked.
- **iPhone and iPad**: Safari there can only show a video full screen, not a page part, so the visualizer has no full-screen button.

The visualizer needs WebGL 2 (any current desktop browser has it). It draws only what this browser is playing; while the player is sending music to a room, there is nothing here to draw, and the player says so.

## Can I send pictures in chat?

**Yes, once the vision model is installed.** The Chat page (dashboard and Android app) takes text and pictures: PNG, JPEG, GIF, WebP or BMP, up to 4 a message and 20 MB each. The paperclip also takes documents (a PDF, Word file and the like), which chat reads instead of looking at: see [Can I ask a chat about a document?](#can-i-ask-a-chat-about-a-document). A message with pictures is answered by the **vision model** (Settings → Chat & AI → Models, the Vision row; `qwen3-vl:2b-instruct-q4_K_M` unless you picked another), because the chat model can only read text. Each picture reaches it upright and at most 1280 px on its long edge (the thread keeps the one you sent): plenty for screenshots, receipts and photos, and quick enough on a processor alone. Later text-only questions in the same chat go to the chat model with the words of the conversation, not the pictures.

**Phone photos in HEIC (and AVIF):** many phones save camera photos as HEIC, which the server doesn't read. The Android app converts those for you: it sends them as a JPEG, at most 2560 px on the long edge (about 1 MB), and does the same for a JPEG over the 40-megapixel limit. A converted photo carries no metadata, so its location and camera details stay on the phone; a JPEG, PNG, GIF or WebP that fits goes exactly as it is. Android 9 or newer converts HEIC (Android 12 or newer, AVIF); an older phone says so and asks for a JPEG. A browser can't convert HEIC, so the dashboard says what to do instead: send it from the app, or save it as a JPEG first.

The vision model isn't installed by default (a server set up by `scripts/linux/provision.sh` has it, unless it was run with `--no-vision-model`). Until it is, the paperclip explains that instead of opening the picker ("Pictures need the vision model qwen3-vl:2b-instruct-q4_K_M, which isn't installed on this Domovoi…"), and a picture sent anyway gets that same answer straight away. An admin installs it in **Settings → Chat & AI → Models** on the dashboard, or picks another vision-capable model there (a text-only model can't read pictures, and chat says so). The dashboard shows a signed-in admin a link straight to that tab; the Android app, which has no admin sign-in, opens the dashboard's Settings page instead. Pictures stay on your server: they are read by the model on your own hardware, like everything else in chat.

The default vision model is the pick for a box without a GPU: a 1.9 GB download, about 3 GB of memory while it is loaded. With an NVIDIA GPU, `qwen2.5vl:7b` is the pick for the Vision row instead: quick on the GPU, but minutes per picture on a processor alone. A change in the Vision row reaches the dashboard's chat when the dashboard restarts.

## Can I ask a chat about a document?

Yes. The paperclip in Chat takes a PDF, Word (.docx), OpenDocument (.odt), Excel (.xlsx), CSV, text or markdown file, up to 100 MB, in a chat with the home server (on the dashboard and in the app) and in a chat on the phone. Ask what it says: the answer names the pages or sections it came from, a press on one shows the passage, and when the document doesn't say, the answer is "I couldn't find that in the document" rather than a guess. Files → Documents has **Ask about this document**. "Summarize" works on long documents too: Domovoi reads them in parts ("reading part 3 of 8"), **stop** keeps what was read, and every point in the summary cites a page.

What it can't do yet: scanned pages (pictures of text) are listed but not read, and old .doc and .rtf files are refused with what to save them as.

Where the document goes: in a home-server chat the file, and the text read from it, stay on your server (`~/.domovoi/chat_uploads/documents/`) until you delete that chat, and the models that read it run on your own hardware. A chat on the phone reads the document on the phone and keeps it in the app's private storage; a PDF the phone can't read itself goes to your home server only when you tap **ask the home server**, which reads its text and keeps nothing. Nothing goes to the internet. With AI chat and questions turned off, documents are not offered ([Is AI optional?](#is-ai-optional)).

## Does Domovoi look things up online?

Only when you allow it. Say "look up …", "what does this page say: https://…", or ask something Domovoi doesn't know. It searches its own manual and an offline library (the Kiwix plugin, if you installed it) first, with no question asked. Before anything goes online, a card shows the exact words and where they go: **Allow once**, **Allow in this chat** or **Not now**. Wikipedia and Wikidata come first (through the local search helper, or directly), or your own SearXNG or Brave Search key (Settings → Chat & AI → Search). The sources under an answer are listed by Domovoi, not the model, and a page's text can't make Domovoi do anything.

To keep it all in the house, answer **No** in Settings → Privacy → Internet: every lookup that would leave the house is refused, and the manual and the offline library still answer. **Always allow internet** (Settings → Chat & AI → Search) starts off. A phone away from home looks up Wikipedia, and reads a page you name, by itself. What goes out, and to whom, is in [Every touchpoint, and how to turn each off](INTERNET.md#every-touchpoint-and-how-to-turn-each-off).

## Can Domovoi print and scan?

Yes, on network printers and scanners, with no drivers: any printer that works with AirPrint, IPP Everywhere or Mopria, and any scanner that works over eSCL ("AirScan"). USB printers and printers that need a maker's driver are not supported. **Settings → Household → Printers & scanners** finds them on your network, adds one by name or address, and prints a test page. **Print** is on PDFs, pictures, text and markdown files and spreadsheets in Files and Documents (a Word or OpenDocument file says "save it as PDF first"); **Scan** puts several pages into one PDF in Documents → Scans. By voice: "print two copies of the lease" (it says the pages and asks first), "how much ink is left", "scan this to Documents".

Everything stays on your network, and printing and scanning keep working when the internet answer is **No**. Domovoi keeps a log of print jobs (which device or room, the document's title, pages, when, which printer) for 90 days; paired devices can read it and an admin can clear it. Limits an admin can change: 100 pages and 20 copies a job, 500 sheets a day for the house.

## How do I ask Domovoi for help?

Ask it. In chat or by voice, "how do I add a room?" is answered from Domovoi's own manual, this FAQ, the troubleshooting guide, an entry for every dashboard error and your installed plugins' help; the answer names the section it came from, and when none covers the question it says "The manual doesn't cover that." The dashboard has a **?** in the top bar of every page, **ask Domovoi about this** on an error, and **report a problem**, which builds a summary with addresses, tokens and codes taken out for you to copy and post yourself: nothing is sent anywhere. By voice, Domovoi can offer to send the steps to the phone marked as yours (the app's Settings → Household → Phones & browsers → "This phone belongs to"). With AI chat and questions turned off, help is a plain search of the manual and the docs.

## How long is what we say kept, and can we delete it?

**90 days by default, and yes.** Domovoi writes down what people say to it
and what it answered, so the Conversations and Sessions tabs can show it
(to paired devices only). On the dashboard, **Settings → Household → People → Voice history** has:

- **Keep voice history**: 30 days, 90 days (the default), 1 year, or
  Forever. Anything older is deleted once a day, and right away when you
  pick a shorter time. It's also in Settings → System → Advanced → Privacy.
- **Delete all voice history**: deletes everything said until now, in every
  room and on every phone, after a confirmation that lists what goes.

Forgetting one person ("forget me" in their own voice, then "yes" when
Domovoi asks, or **forget person** on their Profile tab) deletes what *they*
said, along with their voice and memories. A turn where someone else said their name isn't theirs, so it
stays: Domovoi goes by who was speaking, not by the words.

What stays either way: memories already saved, voice samples, notes,
timers, the music played, chat threads (with the pictures and documents
attached to them, until the chat is deleted), the print and scan job log (90
days), and the backups the Linux updater
made before each update, which keep their copy until newer updates replace
them. The details, store by store:
[SECURITY_PRIVACY.md → Voice history](SECURITY_PRIVACY.md#voice-history-whats-kept-for-how-long).

## Where is my data?

All on machines you own:

| What | Where |
|---|---|
| Conversations, intents (kept 90 days by default: [how long](#how-long-is-what-we-say-kept-and-can-we-delete-it)), people/voice profiles, memories, playlists, timers, plugin data | Postgres — the `domovoi` database, in the `domovoi-pgdata` Docker volume, published on host port 6432 |
| Server config & runtime artifacts | `~/.domovoi/` on the server — setup code, rendered voice clips (`sounds/`), wake-word models and training clips, uploaded Piper voices, album-art cache, per-plugin logs (`logs/`), podcasts, audiobooks |
| Wake-word training data (once training is on) | The Docker volume `domovoi_oww_cache`: the downloaded training data, and per wake word its synthetic clips and the features computed for it (copies of the recorded clips are removed after each run) |
| Service settings | `domovoi/.env` next to the code |
| Music | `MUSIC_DIR` (default `~/Music`) |
| Documents (office pages) | `DOCUMENTS_DIR` (default `~/Documents`) |
| Pictures (Images tab) | `PICTURES_DIR` (default `~/Pictures`) |
| Videos (Videos tab) | `VIDEOS_DIR` (default `~/Videos`) |
| Podcasts, audiobooks | `PODCASTS_DIR` / `AUDIOBOOKS_DIR` (default `~/.domovoi/podcasts`, `~/.domovoi/audiobooks`) |
| Chat pictures and documents (and the text read from documents) | `~/.domovoi/chat_uploads/` on the server, removed when you delete the chat. A phone chat's documents stay in the app's own storage |
| Scans | `Scans/` inside your documents folder |
| Printers, and the print and scan job log (document titles, 90 days) | Postgres |
| Satellite config & caches | `~/.domovoi/` on each Pi |

**On the Windows installer,** "the server" is its private Linux: `~/.domovoi/`, the database and `domovoi/.env` live inside it, out of Explorer's reach. The media folder you chose in Setup (by default `C:\Users\Public\Domovoi`, with `Music`, `Pictures`, `Videos`, `Documents`, `Audiobooks` and `Podcasts` inside) is an ordinary Windows folder: uninstalling Domovoi never deletes a file in it.

**These directories are created for you on first boot.** Every media library
above is created (`mkdir -p`, ordinary permissions) when the dashboard starts,
so a fresh headless server gets a working Documents, Pictures and Videos
library even though a server account has no desktop `~/Documents`,
`~/Pictures` or `~/Videos` folder.
Nothing is overwritten: a directory that already exists is left exactly as it
is, contents and permissions untouched.

Domovoi **refuses** to create a configured path in four cases, and logs a
`WARNING` naming the library, the setting and the path instead:

- the path is **outside your home directory** — it might be the mountpoint of a
  drive that isn't mounted yet, and creating an empty folder there would mask
  the drive when it does come up (your files would appear to vanish). Point
  `MUSIC_DIR` at an external disk and you create and mount it yourself;
- the path is a whole **filesystem or drive root** (`/`, `D:\`);
- the path is inside the private config area `~/.domovoi/` but isn't one of the
  media folders that legitimately live there (podcasts, audiobooks);
- the path already exists as a **file**, not a directory.

The server still starts in every one of these cases — the library simply won't
appear on the Files page until you fix the setting or create the directory. If
a library is missing from Files, the dashboard log says which one and why.

## How do I back up and restore?

There's no one-button backup tool yet — but it's three pieces, all standard:

1. **The database** (the important one):
   ```powershell
   docker exec domovoi-postgres pg_dump -U domovoi domovoi > domovoi-backup.sql
   ```
2. **`~/.domovoi/`** on the server — copy the folder. Trained wake-word models and recorded training clips live here and are genuinely hard to recreate; the rest (rendered sounds, caches) regenerates itself.
3. **Your media** — `MUSIC_DIR` and friends, which you're presumably backing up anyway.

**On the Windows installer** there is no backup tool yet, and no shell in its private Linux to dump the database from. Uninstalling it deletes the database, the history, the voice profiles and the server's identity (satellites must be paired again after a reinstall); keep-data uninstall comes later ([Not in this build](WINDOWS_HOST.md#not-in-this-build)). Your media is in an ordinary Windows folder: back that up like any other.

On Linux with the update unit, the box keeps its own copies too: a dump of the database before every update in `/var/lib/domovoi-update/backups/` (newest 5, root only) and, after a rollback, the replaced database as `domovoi_failed_<time>` (newest one). They hold everything the database holds, so count them when you move, sell or wipe the machine ([SECURITY_PRIVACY.md → Data at rest](SECURITY_PRIVACY.md#data-at-rest)).

Restore on a new machine: install Domovoi, start Postgres, restore the dump (`docker exec -i domovoi-postgres psql -U domovoi domovoi < domovoi-backup.sql`), copy `~/.domovoi/` and your media back, copy your `.env`, start the core. Returning users note: admin credentials live in the database, so a restored database keeps your password; a *fresh* database means first-run setup again (new setup code in `~/.domovoi/setup-code.txt`). Satellites reconnect on their own — their config never left the Pi.

## Linux or Windows for the server?

**Either. Pick by what the box is.** There are three ways in, and the README's [First install](../README.md#first-install) has all of them.

**Windows 11, the installer.** One download, `DomovoiSetup.exe`, sets up the same server as a Linux box, inside a private Linux (WSL2) that Windows runs for it. It is the easy way onto the household PC, which for many homes already has the best GPU in the house. It is unsigned for now (Windows asks first), and it has no backup tool or keep-data uninstall yet. Setup and what it changes: [WINDOWS_HOST.md](WINDOWS_HOST.md).

**Linux** is the better host for a dedicated always-on machine. A headless install leaves several GB more RAM for models, speech recognition can use an NVIDIA GPU, wake-word training runs on the box's own Docker Engine (turned on from the dashboard, the GPU runtime set up by the install), and autostart is a systemd unit. On Ubuntu 26.04, Debian 13, Linux Mint 22 or Pop!_OS 24.04, one command installs it all. Setup: [LINUX_HOST.md](LINUX_HOST.md).

**Windows by hand** (cloning the repository and running it in Windows itself, with Docker Desktop) still works, and is mostly for people working on Domovoi.

Postgres, MPD and SearXNG run in containers in every case, and the application code is the same Python.

---

*Still curious? The [Architecture](ARCHITECTURE.md) doc explains how the pieces fit; [Troubleshooting](TROUBLESHOOTING.md) is for when they don't.*
