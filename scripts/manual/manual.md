<!--
The words of Domovoi's user manual: the dashboard's #manual page and the
Android app's manual. This is the SOURCE. The product reads
web/static/manual-content.json, which `python scripts/manual/build_manual.py`
makes from this file and the map in that script (topics, pieces,
subtitles, search words, routes, and which handler each "try saying"
phrase reaches). Edit here, run the script, commit both;
domovoi/tests/test_manual_content.py fails while they disagree.

How to write it:
- "## Topic" and "### Piece" headings must match the map in build_manual.py.
- Links: [Settings → Voices](#settings/voices) opens a route (ROUTES in the
  script); [Your rooms](#node:rooms) opens another page of the manual;
  [More detail](docs/FAQ.md#anchor) is a doc on GitHub, for the version
  the server runs.
- *(admin)* and *(Preview)* become pills. {wake} is the wake word the
  household's rooms answer to (live); [live slot: name] is filled from the
  server.
- A paragraph or bullet starting **App:** shows only in the app,
  **Dashboard:** only on the dashboard.
- {{name}} on a line of its own is replaced by "### name" from the
  Snippets section at the end: copy that waits on other work, kept in one
  place.
- Every "try saying" phrase needs an entry in PHRASES in the script.
-->

# Domovoi user manual

## Start here

**Domovoi is your home's own voice assistant.** It runs on a computer in
your house, the Domovoi server. Small speakers with microphones put it in
every room. Your voice never leaves your home network.

**Three ways to use it**

- **Talk to a room.** Say {wake}, then what you want: "set a timer for 10
  minutes." Your household can also train a word of its own: see [Wake
  word and greeting](#node:wake-word).
- **The dashboard.** Open it in any browser on your Wi-Fi, at the address
  whoever set up Domovoi gave you (it ends in `:6369`). You can manage
  music, files, chat, people and settings there.
- **The Domovoi app** on Android phones. It has the everyday pages, a
  music player and chat, and it plays the phone's own music when there is
  no server.

[live slot: wake-word]

**Try saying**

- "set a timer for 10 minutes"
- "what's the weather tomorrow" (checks online, if you allow it)
- "play some jazz in the kitchen"
- "tell everyone dinner's ready"

**Getting set up** (a checklist; each line ticks itself when it's done)

1. **Claim the admin account** *(admin)*: [Settings → Configuration](#settings/configuration),
   **set up admin**. Type the 8-word setup code and choose a password.
   Where the code is: the Windows installer shows it on its last page; on
   Linux it is in a file on the server. [More detail](README.md#2-claim-the-admin-account)
2. **Answer the internet question**: Yes, Sometimes or No. You can change
   it later in [Settings → Internet](#settings/internet).
3. **Add your first room**: [Settings → Satellites](#settings/satellites).
   See [A Raspberry Pi room](#node:pi-room).
4. **Say the wake word** in that room: {wake}, then "what time is it?"
5. **Introduce yourself** in that room: "my name is …", then "yes".
6. **Add your music**: [Music](#music), **Upload**, or copy files into the
   server's music folder.
7. **Put the app on your phones**: [Settings → Devices → Get the Domovoi
   app](#settings/devices).

---

## How it works

**Intro:** A room hears you and the server thinks. The answer plays in the
room you're in. Everything runs on machines in your home.

### Hears

The wake word is heard by the room itself: a Raspberry Pi, a spare
computer, or a phone in room mode. Nothing is sent until it hears it.
Then the room streams what you say to the server over your home network.
There, **Whisper** turns it into text.

- On a server set up by the installer (Linux script or Windows), Whisper
  runs on the processor, with its small English model.
- A server set up by hand can run Whisper on an NVIDIA graphics card
  instead.

[More detail](docs/CPU_HOST.md#the-short-version)

### Thinks

Most commands ("set a timer", "pause the music") are recognised instantly,
with no AI model. When a command isn't, a **routing model** picks the
right feature. Open questions go to an **answer model**, and pictures in
chat go to a **vision model**. All of them run on the server, through
Ollama.

| | Server with 24 GB of memory or more | Smaller server |
|---|---|---|
| Routing | Qwen3 8B | Qwen3 4B |
| Answers | Llama 3.2 3B | Llama 3.2 3B |
| Pictures | Qwen3-VL 2B | Qwen3-VL 2B |

Those are what the installers pick. A server set up by hand starts with
Qwen2.5 14B for routing.

An NVIDIA graphics card makes them faster: Ollama uses it by itself, on
Linux and on Windows. An admin can change models in [Settings →
Models](#settings/models). [More detail](docs/CPU_HOST.md)

**AI chat and questions are optional.** The installers ask whether to
include them, and an admin can turn them off or on at any time under **AI
chat and questions** in [Settings → Models](#settings/models). Off, no
routing, answer or vision model is used: voice commands still work on
their usual phrases, and so do speech recognition, the voices and the wake
word. A question then gets "I'm set up for home commands", and Chat is
hidden. Turning them on shows the download size first, then downloads the
models.

### Speaks

Replies are spoken on the server, in one of three voices: **Kristin** (the
default), **Norman** or **Linda**. Each room plays the reply through its
own speaker. If a voice is missing, a plain system voice speaks instead,
so Domovoi is never silent. See [Domovoi's voice](#node:voice).

[More detail](docs/FAQ.md#which-voice-does-domovoi-speak-with-and-can-i-fix-how-it-says-a-word)

### Remembers

A database on the server keeps everything: settings, people, timers,
conversations and play history. See [What's kept](#node:kept) for how long
each is kept.

[More detail](docs/FAQ.md#where-is-my-data)

### Plays

Every room gets its own music player on the server. It streams to that
room's speaker, so two rooms can play different things at once. Your
music, videos, pictures and documents stay in ordinary folders on the
server.

[More detail](docs/FAQ.md#what-music-sources-exist-out-of-the-box)

### The server

One computer that stays on. Either:

- **Linux** (Ubuntu 26.04: one script installs everything), or
- **a Windows 11 PC** with the Windows installer *(Preview)*, which puts
  the same Linux server inside Windows, out of sight.

On a Windows PC:

- With 16 GB of memory it gets the smaller models; with 32 GB, the
  standard ones and wake-word training.
- Whisper always runs on the processor there.

A graphics card is optional everywhere. [More detail](docs/WINDOWS_HOST.md#which-pcs)

### Dashboard and app

- The **dashboard** is the web page where you manage everything; it's the
  only place to install plugins and change server settings.
- The **app** brings the everyday pages to your phone, plus its own
  player, chat mic and offline assistant.

[More detail](README.md#what-each-interface-can-do)

---

## Things to say

### Timers and reminders

{{clock-reminders-intro}}

**Try saying**

- "set a timer for 10 minutes"
- "set a timer for 8 minutes for the pasta"
- "how much time is left on the timer"
- "cancel the timer"
- "remind me to call Mom in 20 minutes"
- "remind me in 2 hours to take the bins out"
{{clock-reminders-phrases}}
- "what reminders do I have"

**Also:** your timers show on [Home](#home) and in each room's **Timers**
tab ([Settings → Satellites](#settings/satellites)).

**App:** if Home's timers card says "Timers may ring a few minutes late on
this phone", tap **allow alarms**.

{{clock-reminders-wrong}}

[More detail](docs/SECURITY_PRIVACY.md#timers-and-reminders-house-wide)

### Quick answers

Domovoi knows the time, does maths and answers questions. For anything
that changes (weather, scores, prices) it offers to check online, if your
household allows the internet.

**Try saying**

- "what time is it" · "what day is it"
- "what's 15 percent of 80"
- "convert 5 miles to kilometers"
- "how many days until Christmas"
- "split $120 4 ways with a 20 percent tip"
- "why is the sky blue"
- "what's the weather tomorrow", then "yes"
- "are you sure?" (checks its last answer online)

**If something's wrong**

- "I'm set to stay off the internet": your household chose No in
  [Settings → Internet](#settings/internet).
- "I'm set up for home commands" or "I can't look that up with AI turned
  off": AI chat and questions are off for your home. Commands still work;
  an admin can turn AI on in [Settings → Models](#settings/models).
- "My question-answering model isn't installed yet": AI is on but its
  model hasn't been downloaded. An admin can download it in [Settings →
  Models](#settings/models).
- "My search helper isn't running": ask whoever set up Domovoi.
- An answer sounds wrong? Say "are you sure?".

[More detail](docs/TROUBLESHOOTING.md#questions-land-on-the-wrong-handler)

### Music by voice

Play your own music in the room you're in, or in any other room by naming
it.

**Try saying**

- "play some jazz" · "play Norah Jones" · "shuffle my music"
- "pause the music" · "skip this song" · "turn it up"
- "what's playing"
- "play my favorites"
- "play some music in the garage"
- "in the garage, stop the music" (say the room first to stop, pause or
  skip somewhere else)
- "what's playing in the office"
- "play the song that goes …" (needs lyrics; see [Lyrics](#node:lyrics))
- "when I say Gramps I mean Hearth Ensemble" (a nickname for an artist in
  your library)

**If something's wrong:** "no speakers set up" means no room has connected
yet. "That room's speaker is offline": check [Your rooms](#node:rooms).
More in [Music and room queues](#node:music).

[More detail](docs/RELEASE_NOTES.md#2026-10-14--play-music-in-another-room-by-voice)

### Talk to other rooms

An **announcement** is spoken in other rooms. A **drop-in** is a live
two-way call into a room.

**Try saying**

- "tell everyone dinner's ready"
- "tell the kitchen dinner's ready"
- "drop in on the kitchen"
- "hang up"

**App:** on a room's page, tap **drop in from this phone** to talk to that
room.

**If something's wrong:** drop-ins need a room with an echo-cancelling
microphone (the XVF3800 board, or most phones in room mode). A room may be
set to ask before it answers: [Settings → Configuration](#settings/configuration),
**Drop-in** *(admin)*.

[More detail](docs/FAQ.md#can-i-drop-in-on-a-room-from-my-phone)

### Notes and memory

Notes are quick jottings, kept by the room and the time. Memory keeps
facts about *you*, so it needs to know your voice first (see [People and
voices](#node:people)).

**Try saying**

- "jot down buy milk and eggs"
- "read my notes" · "what was my last note"
- "remember that I parked on level 3"
- "my favorite band is Weezer"
- "what do you remember about me"
- "forget that I parked on level 3"

**Also:** notes show in each room's **Notes** tab ([Settings →
Satellites](#settings/satellites)). Memories show on [People](#people),
under each person's **Memory** tab.

[More detail](docs/FAQ.md#does-it-support-multiple-people)

### Everything it can do

This lists every feature this Domovoi has, including installed plugins,
with phrases to try. It also says which features work offline, which work
less well offline, and which need the internet. Install a plugin and its
features appear here.

[live slot: things-to-say]

{{what-can-you-do}}

### When it mishears

**Try saying**

- "say that again"
- "never mind"

Wait for the greeting or chime after the wake word, then speak. Words said
over the greeting aren't heard.

**If something's wrong**

- It doesn't wake: speak closer, or check the room is online in [Your
  rooms](#node:rooms). An admin can make it more sensitive in the room's
  **Settings** tab.
- It wakes on the TV: the same setting, the other way.
- It misheard a name: introduce yourself again ("my name is …"), or fix
  how it *says* a word in [Domovoi's voice](#node:voice).

[More detail](docs/TROUBLESHOOTING.md#wake-word-not-triggering)

---

## Rooms and devices

### Your rooms

[live slot: rooms]

Each room is one listening device: a Raspberry Pi, a spare laptop or a
phone. [Settings → Satellites](#settings/satellites) lists them. Each card
shows:

- online or offline;
- what's playing;
- its Wi-Fi signal;
- when it last heard someone.

**Dashboard:** open a room for its tabs. They are the icons along the
top: **Overview** (the "i"), **Sessions**, **Conversations**, **Recently
played**, **Notes**, **Timers**, **Logs**, and **Settings** (the gear).

**How to** *(admin)*

- Make a room hear the wake word more or less easily, swap its greeting
  for a chime, or make its voice louder: its **Settings** tab, **Wake
  sensitivity**, **Wake acknowledgement**, **Voice loudness boost**.
- A Pi or spare computer acting up: **Restart satellite**. Updating it:
  **Upgrade satellite**.
- A room moved to new hardware: **Reset pairing**, then approve the new
  device.

**App:** [Settings → Satellites](#settings/satellites) shows the same rooms.

**If something's wrong**

- *offline*: power and Wi-Fi first. If **every** room says *can't
  connect*, see [Restarting](#node:restart).
- The Wi-Fi bar is amber or red: move the room device or the router
  closer.
- A room says "having trouble reaching the network": it has lost the
  server.

[More detail](docs/TROUBLESHOOTING.md#satellite-wont-connect)

### A Raspberry Pi room

The usual room: a Raspberry Pi with a microphone board and a speaker.
About the price of a smart speaker. [Parts list](docs/SATELLITE_HARDWARE.md)

**How to** *(admin)*

1. Put Raspberry Pi OS Lite (64-bit) on a microSD card, and put the card
   in the server's card reader.
2. [Settings → Satellites](#settings/satellites) → **prepare satellite
   media** → **wi-fi setup portal** → **Prepare**. On the Windows install,
   which can't see SD cards, **download overlay zip** and unzip it onto
   the card.
3. Start the Pi with the card. It makes its own Wi-Fi network,
   `Domovoi-Setup-<id>`. Join it from your phone, pick your home Wi-Fi and
   name the room.
4. The Pi says a six-digit code. Under **waiting for approval**, type it
   and press **approve**.

**If something's wrong:** **show setup details** on the card lists the
setup network and its page. [More detail](README.md#3-add-your-first-satellite)

### A spare laptop or PC *(Preview)*

A Linux or Windows 10/11 laptop or PC can be a room. Use one that stays
plugged in, with sleep turned off. Not the computer that runs Domovoi.

**How to** *(admin)*

1. [Settings → Satellites](#settings/satellites) → **add a spare device**.
   Type the room's name, press **make the code**.
2. Press **copy the command** (Linux) or **copy the Windows command**, and
   run it on that computer, signed in as the account it should use.
3. The computer shows a six-digit code. Approve it under **waiting for
   approval**.

A tray icon shows when the microphone is live, and mutes it.

**If something's wrong:** a processor from before about 2009 is refused.
Windows may warn about the unsigned satellite. [More detail](docs/SPARE_DEVICE_SATELLITES.md)

### An old phone or tablet as a room *(Preview)*

An Android 8 or newer phone on its charger becomes a room ("room mode"):
it hears the wake word, answers, plays the room's music, and takes
announcements.

**How to**

1. *(admin, once)* [Settings → Satellites](#settings/satellites) →
   **prepare satellite media** → **Refresh caches**. This gives phones the
   wake word.
2. *(admin)* **add a spare device**, name the room, **make the code**. A QR
   code appears.
3. Scan it with the phone's camera. The page installs the Domovoi app
   (press **Download the app to this server** there once first) and opens
   it as that room.
4. Read the explainer, press **Start room mode**, allow the microphone,
   press **Become this room**.
5. Approve the six-digit code on the dashboard.

**App:** a phone that already has the app can do this from [Settings →
Satellites](#settings/satellites) → **Use this device as a room**, or
from the **Domovoi Room** icon.

To leave, hold **exit room mode**, unlock, then choose **Keep this room**
or **Forget this room**.

**If something's wrong**

- *mic blocked*: turn the microphone back on in quick settings.
- After a restart: tap the notification to resume.
- Turn off battery optimisation for Domovoi.

[More detail](docs/SPARE_DEVICE_SATELLITES.md#phones-and-tablets-room-mode)

### An old computer, from a USB stick *(Preview)*

The server makes a USB stick. An old 64-bit PC, laptop or 2012–2017 Intel
Mac starts from it and becomes a room that does nothing else. **This erases
that computer's disk.** It needs a network cable while it sets up.

**How to** *(admin)*

1. **add a spare device** → name the room → **make the code** → under
   **old computer (usb stick)**, **Prepare the stick**.
2. **Open in Raspberry Pi Imager** to write it to a USB stick (2 GB or
   more).
3. Start the old computer from the stick (its boot-menu key, often F12),
   type **ERASE** when asked, and wait about half an hour.
4. Approve the code it shows.

[More detail](docs/SPARE_DEVICE_SATELLITES.md#old-computers-usb-stick)

### Wake word and greeting

A new room wakes to {wake}. After waking, it greets you or chimes, then
listens.

[live slot: wake-word]

**Train your own wake word** *(admin)*

- Where: [Settings → Wake Words](#settings/wake-words). The admin turns
  training on once, with **Turn on wake-word training**.
- What it needs: a server with 24 GB of memory, about 22 GB of downloads
  and about 32 GB of disk. A word takes about 20 minutes to train with an
  NVIDIA graphics card, and over an hour on the processor.
- The steps: **Create** the word, **Record** 15 or more clips on a
  satellite in the room itself, press **Train**, then **Push to room**.
- A wake word trained at home is for your household only ([why](docs/LICENSES.md#the-wake-word-trainer)).

**Greetings** *(admin)*

- What a room says when it wakes: [Settings → Greetings](#settings/greetings).
- A chime or a light instead: the room's **Settings** tab, **Wake
  acknowledgement**.

[More detail](scripts/wake_word/README.md)

### Domovoi's voice

Three voices, all on the server: **Kristin** (default), **Norman** and
**Linda**.

**How to**

- Pick one: [Settings → Voices](#settings/voices) → **Domovoi's voice** →
  play a sample, **use this voice** *(admin)*.
- A word said wrong? **Say it like**: type the word and how it sounds, the
  stressed part in capitals (Siobhan = shiv-AWN). Press play to hear it
  first.
- **App:** how *this phone* speaks: [Settings → Voice](#app/voice): "Same
  as the server" or one of the three (about 64 MB, downloaded once).

**Try saying**

- "what voices are there"
- "let me hear Norman's voice"
- "switch to Norman's voice" (changes this room's voice)

[More detail](docs/FAQ.md#which-voice-does-domovoi-speak-with-and-can-i-fix-how-it-says-a-word)

---

## Your phone

### Get the app and connect

The Domovoi app runs on Android 8.0 or newer, older 32-bit phones
included. It comes from your own server; no store is needed.

**How to**

1. On the dashboard, [Settings → Devices → Get the Domovoi
   app](#settings/devices) shows a QR code. If it says the app isn't on
   the server yet, ask whoever set up Domovoi to press **Download the app
   to this server**.
2. Scan the code with the phone's camera and install. Android asks you to
   allow installs from your browser once.
3. Open Domovoi. Tap **connect** at the top, pick your server (it scans
   your Wi-Fi), and press **trust this server**. Can't see it? Use **add
   manually** with the dashboard's address.
4. **pair this phone**: type the household token. An admin finds it in
   [Settings → Devices → Household token](#settings/devices).

**App:** switch or forget a server from the server name at the top, or in
[Settings → Connection](#app/connection).

**If something's wrong:** "App not installed" means a 32-bit phone; take
the 32-bit app from the same page. [More detail](docs/ANDROID_COMPATIBILITY.md)

### Music on the phone

**Play here** plays on the phone; **play in room** plays in a room.

**How to**

- Save a song, episode or video to the phone with **save to device**
  (or **save**). Files go to Downloads/Domovoi and play with no server.
- Save a book with **save to phone** on its page. The size shows first.
  Books stay inside the app, play with their chapters with no server, and
  keep one place in step with the server. A save that stops when you leave
  home finishes by itself when the server is in reach again.
- Music, Videos, Audiobooks and Files switch between the server and **your
  device**. From your device you can upload to the server. In Files,
  **add folder** shows a folder of the phone's own.

[More detail](docs/FAQ.md#can-i-save-music-podcasts-or-audiobooks-to-my-phone-or-computer)

### Chat and the chat mic

**Chat** talks to your Domovoi server (see [Chat](#node:chat)).

**How to**

- Tap **talk** to speak a message: **hold to talk**, release to send,
  slide away to cancel. Up to 30 seconds.
- "my name is …" in the chat mic teaches Domovoi your voice from this
  phone.
- **attach image**: up to 4 pictures. Phone photos in HEIC are turned
  into a JPEG on the phone first (Android 9 or newer), without their
  location.

[More detail](docs/FAQ.md#can-i-send-pictures-in-chat)

### The assistant *(Preview)*

A Domovoi that runs on the phone itself: type or talk, in airplane mode
too. It knows the time and date, and it answers questions. It can't set
timers, play music or look things up.

**How to:** **Assistant** → **Set up the assistant on this phone** →
**download** (the button shows the size, about 2.6 GB). It waits for Wi-Fi
unless you **allow mobile data**.

It needs a 64-bit phone with 8 GB of memory or more. To report a bad
reply, long-press it. Its chats stay on the phone. It is separate from
**Chat**, which talks to your server.

[More detail](android/README.md#on-device-assistant)

### This phone as a room *(Preview)*

See [An old phone or tablet as a room](#node:phone-room). A phone you also
use every day needs a screen lock. While it is a room, the rest of the
app is locked.

### App settings and updates

- [Settings → Phone bar](#settings/phone-bar): pick 2 to 5 tabs for the
  bottom of the screen. Home always comes first.
- [Settings → Voice](#app/voice): how this phone speaks.
- [Settings → Server settings](#app/server-settings) → **Open the
  dashboard**: everything else.
- **Updates:** when your server has a newer app, Home shows "Domovoi … is
  available from your server". Tap **get the update**. [Settings →
  About](#settings/about) → **App updates** says which version you have. A
  Google Play install updates itself.
- [Settings → About](#settings/about) → **Privacy** says what the app
  keeps.

[More detail](docs/PRIVACY_POLICY.md)

---

## Music and media

### Music and room queues

Your music library, on the server, played in any room or in the browser.

**How to**

- Add music: [Music](#music) → **Upload**, or copy files into the server's
  music folder.
- Each room has one shared queue, and anyone in the house can add to it.
- **Dashboard:** [Music → Room queue](#music/queue): drag to reorder. A
  room card's **queue** button goes straight there.
- **App:** [Music](#music/queue) → **room queue**: **play next**, **move
  up** and **move down** on each song.
- Playlists and favorites: [Music → Playlists](#music/playlists).
- Each queued song says which device added it.

**Also:** guests on your Wi-Fi can change the queue of a room that's
playing. See [Guests, household, admin](#node:guests).

[More detail](docs/FAQ.md#who-added-that-song-and-can-i-stop-someone-adding-more)

### Lyrics

The **lyrics** button in the player shows a song's words, timed when it
has them. Words come from the song file, or, if your household allows the
internet, from LRCLIB.

**Try saying:** "play the song that goes …".

[More detail](docs/RELEASE_NOTES.md#2026-10-06--lyrics-see-the-words-and-find-a-song-by-them)

### Podcasts

**How to:** [Podcasts](#podcasts) → **subscribe**: search by name, or
paste a feed (**by RSS URL**). New episodes download by themselves only if
your internet answer is Yes.

**Try saying**

- "play the latest episode of The Daily"
- "subscribe to The Daily"
- "skip forward 30 seconds"
- "set speed to 1.5"
- "resume the podcast in the kitchen"

[More detail](docs/INTERNET.md#what-each-answer-changes)

### Audiobooks

Books in the server's audiobooks folder, with chapters, resumed where you
left off.

**How to:** [Audiobooks](#audiobooks) → **play**; **reindex** after
adding books. In the app, open a book for its chapters and **save to
phone**; **add** sends books from the phone to the server.

**Try saying**

- "resume my audiobook"
- "next chapter"
- "play my audiobook in the office"

### Videos

Every video in your libraries, played in the browser or the app, resuming
where you stopped. [Videos](#videos)

### News

A spoken briefing from the feeds your household picks, plus each person's
own topics.

**How to:** [News](#news) → **add topic**, or add a feed.

**Try saying**

- "what's the news"
- "read me the headlines"
- "any news about the city council"

### Radio *(plugin)*

Shown when the radio plugin is installed. Find stations on the
**Stations** page and star the ones you like. Domovoi plays favorites by
name.

**Try saying**

- "stream the jazz station" (a station you starred)
- "stop the radio"

[More detail](plugins/radio/README.md)

---

## Chat, files and calendar

### Chat

Type to Domovoi. Answers come from the models on your server.

**How to:** [Chat](#chat) → **new chat**. Press the paperclip to add up to
4 pictures (PNG, JPEG, GIF, WebP or BMP, 20 MB each).

**App:** the picture button is **attach image**, and the app also takes
HEIC photos. See [Chat and the chat mic](#node:phone-chat).

**If something's wrong**

- "Pictures need the vision model": ask whoever set up Domovoi to install
  it in [Settings → Models](#settings/models).
- Chat isn't there, or says "chat is off": AI chat and questions are off
  for your home. An admin can turn them on in [Settings →
  Models](#settings/models).
- A HEIC phone photo is refused on the dashboard: send it from the app,
  which converts it.

[More detail](docs/FAQ.md#can-i-send-pictures-in-chat)

### Files and photos

Every library in one place: Music, Documents, Pictures, Videos,
Podcasts, Audiobooks, and plugged-in drives.

**How to**

- [Files](#files) → **Upload**.
- **Dashboard:** drag files in from your computer to upload them. Move a
  file by dragging it onto a folder.
- **App:** **move to another folder** on a file.
- **Import** copies from a plugged-in drive.
- Deleting needs the admin password *(admin)*.
- Something changed on disk and isn't showing? Press **Rescan**.

[More detail](docs/FAQ.md#can-i-move-files-around-without-the-command-line)

### Documents, sheets and drawings

**How to:** [Files](#files) → Documents → **New** → **Document**,
**Spreadsheet**, **Drawing** or **Text file**. Each opens in its editor;
press **Save**.

**App:** opens text files and sheets from Files.

### Calendar

The household's events. [Calendar](#calendar) → **new event**; switch
between **month**, **week** and **day**.

{{calendar-voice}}

---

## People and privacy

### People and voices

Domovoi learns who's speaking, so memories, news topics and "who am I"
are personal.

**Try saying**

- "my name is Sarah", then "yes"
- "who am I"
{{forget-me-phrase}}
- "never save my voice"

**How to**

- Teach a new room your voice: say "my name is …" there. It asks "Is that
  you?"; say yes.
- [People](#people) shows everyone, when they were last heard, and their
  memories.
- *(admin)* **rename**, **forget person**, or delete one wrong voice
  sample.

**If something's wrong:** two people with the same name: an admin renames
one on [People](#people).

[More detail](docs/FAQ.md#does-it-support-multiple-people)

### Guests, household and admin

- **Guests**: a phone or browser on your Wi-Fi that isn't paired. Guests
  can look around, and can change the music of a room that's playing.
- **Household**: a paired phone or browser holds the household token. It
  can do everything day to day.
- **Admin**: whoever has the admin password. Changes settings, installs
  plugins, approves rooms, deletes files.

**How to** *(admin)*

- Sign in: [Settings → Configuration](#settings/configuration) → **sign
  in**. A reload in that browser keeps you signed in for 60 minutes after
  you signed in; **sign out** ends it at once. Change the minutes in
  [Settings → Configuration](#settings/configuration) → **Security** →
  **Stay signed in after a reload**.
- Turn guest music control off: same place, **Guests can change the
  music in a playing room**.
- The app never signs in as admin; it opens the dashboard for that.

**If something's wrong:** forgot the admin password? It can only be reset
on the server itself. Ask whoever set up Domovoi.

[More detail](docs/TROUBLESHOOTING.md#admin-locked-out)

### Devices and house rules

[Settings → Devices](#settings/devices):

- **This device**: give this browser or phone a name. It's what queues
  show as "added by".
- **Queue access**, **Files access** *(admin)*: stop one device changing a
  room's music or the files.
- **Guests** *(admin)*: ban a guest who keeps changing the music.
- **mark as a shared screen** *(admin)*: for a kitchen tablet. Personal
  pages (People, Chat, Files, News) are left off it.

These settle household arguments; they aren't locks.

[More detail](docs/FAQ.md#can-a-guest-change-the-music)

### What's kept, and forgetting

Everything stays on your server.

{{voice-history-table}}

Paired phones and browsers in the house can read the voice history, in
each room's **Conversations** tab.

**How to**

{{voice-history-how}}
- "never save my voice": Domovoi stops learning it.
- Delete a chat: the bin icon in [Chat](#chat).
- *(admin)* turn recordings off in a room's **Overview** (**Stop and
  delete**).

An admin can change play history, news and timer history in [Settings →
Configuration](#settings/configuration). [More detail](docs/SECURITY_PRIVACY.md#data-at-rest)

### Internet: yes, sometimes, no

Your voice never leaves the house, whatever you answer. The answer only
decides the online extras.

- **Yes, always**: web answers, lyrics lookups, artists' other names,
  podcast downloads.
- **Sometimes**: the same, without automatic podcast downloads.
- **No, keep everything in the house**: nothing goes out. Online buttons
  show "needs internet".

**How to:** [Settings → Internet](#settings/internet) *(admin)*. Download
what you need before choosing No.

[More detail](docs/INTERNET.md)

---

## Keeping it running

### Updates

[Settings → Configuration](#settings/configuration) → **Version** shows
the release you run.

**How to** *(admin)*

1. **Check for updates**.
2. **Pull the latest**.
3. **Restart to apply changes**.

A failed update rolls itself back. **updates** chooses the channel:
**stable** (releases only) or **main** (every change, as it lands).
After an update, reload any open dashboard once.

**App:** see [App settings and updates](#node:app-settings).

[More detail](docs/LINUX_HOST.md#release-channels)

### Restarting

Some settings apply only after a restart: [Settings →
Configuration](#settings/configuration) → **Version** → **Restart
Domovoi** *(admin)*.

**If every room is offline but the dashboard opens**, the server's core
has stopped answering. It restarts itself within about a minute. If it
doesn't, an admin presses **Restart the core** (on [Home](#home),
[Settings → Satellites](#settings/satellites) or **Version**). The app
says the core isn't answering, and reconnects by itself.

[More detail](docs/TROUBLESHOOTING.md#everything-offline-dashboard-up)

### Backups and moving

There's no backup button yet. What to keep safe:

- the database;
- the server's Domovoi folder: its identity, voices and trained wake
  words;
- your media folders.

Keep the identity and the rooms won't need setting up again. A server set
up by the installers also saves the database before every update.

The Windows installer can't keep your data through an uninstall yet.
Ask whoever set up Domovoi. [More detail](docs/FAQ.md#how-do-i-back-up-and-restore)

### Plugins

Plugins add features: voice commands, pages, app screens.

**How to** *(admin)*: [Plugins](#plugins) → **Install from zip**, or
**install from GitHub** → **fetch & preview**. Read the trust screen.
**A plugin runs with full access to your server**, so install only from
publishers you trust.

[live slot: plugins]

[More detail](docs/FAQ.md#what-are-plugins-and-are-they-safe)

### Models

[Settings → Models](#settings/models) shows the models in use:

- **Speech-to-text**: hears you.
- **Tool routing**: picks the feature.
- **Q&A**: answers questions.
- **Vision**: reads pictures in chat.

*(admin)* **Install** or **Switch** one. With an NVIDIA card, Qwen2.5-VL
7B reads pictures faster.

*(admin)* **AI chat and questions** at the top turns the AI models off or
on for the whole home. Off keeps speech-to-text and hides the rest of the
model rows. On says how much it will download (about 6.4 GB, or 9.1 GB on
a server with 24 GB of memory or more) before it starts.

[More detail](docs/CPU_HOST.md#the-short-version)

### Troubleshooting

- **A room doesn't answer**: see [Your rooms](#node:rooms).
- **Every room is offline**: see [Restarting](#node:restart).
- **"Just a moment, I'm waking up my language model"**: normal once after
  a restart.
- **The dashboard looks old after an update**: reload the page once.
- **A word is said wrong**: [Say it like](#node:voice).
- **Chat says pictures need the vision model**: [Models](#node:models).
- **Something is greyed with "needs internet"**: [Internet](#node:internet).
- **App: can't find the server**: same Wi-Fi as the server? **add
  manually** with the dashboard's address.

[All troubleshooting](docs/TROUBLESHOOTING.md)

---

## Snippets

<!--
Copy that waits on work in flight elsewhere, kept here so it can be
adjusted in one place. Each "### name" replaces the {{name}} line above.

- clock-reminders-*, calendar-voice, what-can-you-do: the behaviour
  wf/v1-voice-fixes builds (clock-time reminders, calendar by voice, a
  spoken "what can you do"). Their phrases are marked pending in PHRASES
  (build_manual.py) until that work lands.
- voice-history-*, forget-me-phrase: wf/voice-history's "Keep voice
  history" setting (People → Voice history, and Settings → Configuration
  → Privacy; 30 days, 90 days, 1 year or Forever, 90 by default), "Delete
  all voice history", and "forget me" / forget person deleting that
  person's own turns. Labels as that branch has them (f31ed01e).
-->

### clock-reminders-intro

Timers count down and ring. Reminders say your message out loud when the
time comes: in a while ("in 20 minutes") or at a clock time ("at 6 pm",
"tomorrow morning", "on Friday at 5"). Both are heard around the house.
Domovoi says back the time it set. A time on its own means the next
time it comes; add morning, evening, am or pm to be sure.

### clock-reminders-phrases

- "remind me at 6 pm to take the bins out"
- "wake me up at 7"
- "set an alarm for 6:30"
- "cancel the 7 am alarm"

### clock-reminders-wrong

**If something's wrong**

- A wake-up or an alarm is spoken in the room, like a reminder; it
  doesn't ring.
- Times are the server's own time, the one "what time is it" answers in.
  If they're hours off, ask whoever set up Domovoi to set the server's
  time zone.

### what-can-you-do

Ask "what can you do?" out loud and Domovoi names a few of these, with
one thing to say for each; this page has them all.

**Try saying**

- "what can you do"
- "what can I say"

### calendar-voice

Domovoi reads this calendar out loud, with no internet needed. Add and
change events here: by voice it only reads.

**Try saying**

- "what's on my calendar today"
- "what's next on my calendar"
- "do I have anything on Friday"

### forget-me-phrase

- "forget me" (deletes your voice, your memories and what you said; it
  must be your own voice)

### voice-history-table

| What | Kept |
|---|---|
| What you said to Domovoi by voice, and its replies | 90 days, or what **Keep voice history** says |
| Text chats | until you delete the chat |
| Timers and reminders that went off | 7 days |
| Command recordings (off unless an admin turns them on for a room) | 14 days |
| Play history | 90 days |
| News stories | 90 days (saved ones always) |
| The server's own log | no words, only how many |

### voice-history-how

- "forget me", in your own voice: deletes your profile, voice samples,
  memories and everything you said to Domovoi.
- *(admin)* [People](#people) → **Voice history** → **Keep voice
  history**: 30 days, 90 days (the default), 1 year or Forever. Older
  turns are deleted every day. It's also in [Settings →
  Configuration](#settings/configuration) → **Privacy**.
- *(admin)* **Delete all voice history**, on the same page, deletes it all
  at once. People, voice samples, memories, notes, timers and chats stay.
- *(admin)* **forget person** on [People](#people) does what "forget me"
  does, for anyone: it deletes only what that person said.
