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

1. **Claim the admin account** *(admin)*: [Settings → Advanced](#settings/advanced),
   **set up admin**. Type the 8-word setup code and choose a password.
   Where the code is: the Windows installer shows it on its last page; on
   Linux it is in a file on the server. [More detail](README.md#2-claim-the-admin-account)
2. **Answer the internet question**: Yes, Sometimes or No. You can change
   it later in [Settings → Internet](#settings/internet).
3. **Add your first room**: [Settings → Rooms](#settings/rooms).
   See [A Raspberry Pi room](#node:pi-room).
4. **Say the wake word** in that room: {wake}, then "what time is it?"
5. **Introduce yourself** in that room: "my name is …", then "yes".
6. **Add your music**: [Music](#music), **Upload**, or copy files into the
   server's music folder.
7. **Put the app on your phones**: [Settings → Phones & browsers → Get the Domovoi
   app](#settings/phones-and-browsers).

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
tab ([Settings → Rooms](#settings/rooms)).

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
set to ask before it answers: [Settings → Advanced](#settings/advanced),
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
Rooms](#settings/rooms)). Memories show on [People](#people),
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
phone. [Settings → Rooms](#settings/rooms) lists them. Each card
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

**App:** [Settings → Rooms](#settings/rooms) shows the same rooms.

**Try saying** (in the room itself)

- "how's the wifi"
- "fix the wifi"

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
2. [Settings → Rooms](#settings/rooms) → **prepare satellite
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

1. [Settings → Rooms](#settings/rooms) → **add a spare device**.
   Type the room's name, press **make the code**.
2. **Linux:** press **copy the command** and run it on that computer,
   signed in as the account it should use.
3. **Windows:** on that PC, signed in as the account it should use, press
   **Download the Windows Setup** (or type the short address the card
   shows into its browser) and open the file. The setup code is in its
   name, so there is nothing to type. If the card shows only a command,
   paste that into PowerShell on the PC instead (a Linux server may offer
   **get the Windows Setup** first).
4. Windows says **"Windows protected your PC"**: click **More info**, then
   **Run anyway**. Domovoi isn't signed by Microsoft yet; the file comes
   from your own server.
5. The computer shows a six-digit code. Approve it under **waiting for
   approval**.

A tray icon shows when the microphone is live, and mutes it. The
PowerShell command is still there for Windows, under **advanced**.

**If something's wrong:** a processor from before about 2009 is refused.
A setup code lasts 24 hours: make a new one if Setup says it's unknown.
[More detail](docs/SPARE_DEVICE_SATELLITES.md#windows-laptops-and-pcs)

### An old phone or tablet as a room *(Preview)*

An Android 8 or newer phone on its charger becomes a room ("room mode"):
it hears the wake word, answers, plays the room's music, and takes
announcements.

**How to**

1. *(admin, once)* [Settings → Rooms](#settings/rooms) →
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
Rooms](#settings/rooms) → **Use this device as a room**, or
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

- Where: [Settings → Wake words](#settings/wake-words). The admin turns
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
- **App:** how *this phone* speaks: [Settings → Voices](#app/voice): "Same
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

1. On the dashboard, [Settings → Phones & browsers → Get the Domovoi
   app](#settings/phones-and-browsers) shows a QR code. If it says the app isn't on
   the server yet, ask whoever set up Domovoi to press **Download the app
   to this server**.
2. Scan the code with the phone's camera and install. Android asks you to
   allow installs from your browser once.
3. Open Domovoi. Tap **connect** at the top, pick your server (it scans
   your Wi-Fi), and press **trust this server**. Can't see it? Use **add
   manually** with the dashboard's address.
4. **pair this phone**: type the household token. An admin finds it in
   [Settings → Phones & browsers → Household token](#settings/phones-and-browsers).

**App:** switch or forget a server from the server name at the top, or in
[Settings → Phones & browsers](#app/connection).

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

**Chat** holds your chats with Domovoi: those **on home server** (see
[Chat](#node:chat)), shared with the household, and those **on this
phone** (the assistant, below). Each chat shows where it runs, and a new
one starts where you chatted last.

Chat opens on your chats, pinned ones first, then the most recent; type
in **Message Domovoi…** at the bottom to start a new one. A chat is named
by its first words, then Domovoi gives it a short title after the first
reply; a name you give it stays.

**How to**

- Long-press a chat (or its **chat actions** button) to **rename**, **pin** (pins stay on
  this phone), **archive**, **share** or **delete** it; swipe to archive.
  Archived chats are under **archived** at the end of the list.
- **search chats** finds words in titles and messages, in both kinds of
  chat.
- Long-press a reply to **copy**, **share** or **try again**.
- A line saying **earlier messages are beyond what Domovoi can keep in
  mind** marks where the model stops seeing the older part of a long chat.
- Tap **talk** to speak a message: **hold to talk**, release to send,
  slide away to cancel. Up to 30 seconds.
- "my name is …" in the chat mic teaches Domovoi your voice from this
  phone.
- **attach image**: up to 4 pictures. Phone photos in HEIC are turned
  into a JPEG on the phone first (Android 9 or newer), without their
  location.
- **attach a document** (in a chat on this phone, under **+**): a PDF, Word, OpenDocument, Excel, CSV, text or
  markdown file, then ask about it. In a chat **on this phone** it is read
  on the phone and stays there. The answer lists the pages it came from;
  tap one to see the passage (a PDF shows the page itself).
- A PDF needs Android 15 (or 12 to 14 with the PDF update) to be read on
  the phone. Otherwise **ask the home server** reads its text and sends it
  back; nothing is kept there.
- In [Files](#files), the question mark on a document starts a chat about
  it: a file on this phone in a phone chat, a file in the home server's
  Documents in a home-server chat.
- **Drafts** (a resume, a cover letter, a letter) work in both kinds of
  chat, as in [Chat](#node:chat). **open** shows the draft full screen
  with the message box below it, so "shorter" works while you look at it;
  its bar has the versions, **changes**, **edit**, **share**, **print**
  and **read aloud**. In a chat **on this phone** a draft stays on the
  phone: **save** puts it in **Chat documents**, which [Files](#files)
  lists with the phone's own files; **save a copy to…** puts a copy
  anywhere you pick; **send to home** (when your home server is in reach)
  puts it in the home server's Documents → From Chat. **print** uses the
  phone's own print dialog. In a chat **on home server**, save and print
  go through the home server, as on the dashboard.
- **Summarize a long document:** it is read in parts, with "reading part 3
  of 8 · about 1 min left" and **stop**. Stop keeps what was read;
  **carry on** reads the rest. Leave Chat and it pauses at the end of the
  part, then carries on when you come back.
- **use home server** appears only when your home server is measured to be
  faster than this phone for that document (reading all of it, plus sending
  it). It sends the file to be read there; nothing is kept there. If the
  server can't finish, Chat says so and offers **read it on this phone**.

[More detail](docs/FAQ.md#can-i-send-pictures-in-chat)

### The assistant *(Preview)*

A Domovoi that runs on the phone itself: type or talk, in airplane mode
too. It knows the time and date, and it answers questions. It also knows
the phone: what's playing, the battery, free space, Wi-Fi or mobile data,
your next alarm, Do Not Disturb, which phone and Android this is, and
which songs, videos and saved books are on it. It can't look things up
online. House things (rooms, house timers, the household calendar) are in
your home server chats.

It also does things on the phone. "Set a timer for 10 minutes", "wake me
at 6:30" or "set an alarm for 7 every weekday" go to the phone's Clock
app, and it asks first: tap **set**, or in a voice chat say yes (any
"no", "wait" or "cancel" stops it). "Show my timers" opens the Clock app.
"Play some jazz", "shuffle my music", "pause", "next", "go back", "stop",
"set a sleep timer for 20 minutes" and "continue my book" happen at once,
with **undo** under the reply for a few seconds; saying "undo" works too.
"Play the beach video" asks first, then plays it. Music in the house's
rooms and the house's timers are in your home server chats.

"Text Sam", "call Mom", "email Alex", "directions to the station", "add
lunch tomorrow at noon to my calendar", "open a link" and "share this"
show a card first; a tap opens the app that does it, and you finish
there. Your calendar, where you are and calling people by name each ask
for permission the first time, with the reason; say no and it simply
says it can't, and never asks again. Everything is read on the phone and
never sent anywhere; a phone chat never asks your home server.

**How to:** **Chat** → **new chat** → **on this phone** → **Set up the
assistant on this phone** →
**download** (the button shows the size, about 2.6 GB). It waits for Wi-Fi
unless you **allow mobile data**.

It needs a 64-bit phone with 8 GB of memory or more. To report a bad
reply, long-press it.

**Ask about a photo:** on phones whose graphics chip can run the model's
photo reader, the **+** left of the message box offers **take a photo**
and **choose a photo** (and **a document**). One photo per question; type a question or just send it
("what's in this photo?"). The photo is read on the phone, isn't saved to
your gallery, and is gone once Domovoi has answered: the chat keeps only
a **photo (not kept)** mark. The camera asks for its permission the first
time. Phones without the buttons can't do it quickly enough; a home-server
chat's **attach image** still works there. **A letter or a bill:** ask "what does this letter say?" with a photo of
it, and the answer explains it, with its dates and amounts checked against
the text read from the photo. The photo isn't kept; the text read from it
is kept with the answer, so you can ask about the letter again, and goes
when you delete the chat. On a phone that can't read photos itself, **+**
offers **a letter for the home server: take a photo** or **choose a
photo** when a paired home server has its picture model: your home server
reads the photo and keeps nothing, and the explanation is written on this
phone. **Export all phone chats** (in a phone chat, the
model settings button) saves them as Markdown and JSON where you pick. Its chats stay on the phone, marked **on this
phone**; away from home they are the only ones Chat lists. They never mix
with your home server's chats.

[More detail](android/README.md#on-device-assistant)

### This phone as a room *(Preview)*

See [An old phone or tablet as a room](#node:phone-room). A phone you also
use every day needs a screen lock. While it is a room, the rest of the
app is locked.

### App settings and updates

- [Settings → App layout](#settings/app-layout): pick 2 to 5 tabs for the
  bottom of the screen. Home always comes first.
- [Settings → Voices](#app/voice): how this phone speaks.
- The app's Settings has the dashboard's groups and names. What only the
  dashboard can change (greetings, wake words, models, Advanced, …) says
  so there, with **Open on the dashboard**.
- **Updates:** when your server has a newer app, Home shows "Domovoi … is
  available from your server". Tap **get the update**. [Settings →
  About & updates](#settings/about) → **App updates** says which version you have. A
  Google Play install updates itself.
- [Settings → About & updates](#settings/about) → **Privacy** says what the app
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

**Try saying**

- "how many songs do I have"
- "what did I add this week"

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
4 pictures (PNG, JPEG, GIF, WebP or BMP, 20 MB each). Beside each chat in
the list: rename, archive and delete; **search chats** above the list, and
**archived** at its end.

**Ask about a document:** the paperclip also takes a PDF, Word (.docx),
OpenDocument (.odt), Excel (.xlsx), CSV, text or markdown file, up to
100 MB. Ask what it says. The answer names the pages or sections it came
from; press one to see the passage, or to open a PDF at that page. If the
document doesn't say, Domovoi answers "I couldn't find that in the
document" instead of guessing. In [Files](#files) → Documents, **Ask** on
a file starts a chat about it.

**Write a resume or a letter:** ask for one ("write a cover letter for the
warehouse job at Acme", "draft a letter to my landlord about the heater").
Domovoi writes a draft, shown as a card with **open**, **save**,
**download** and **print**. Then say what to change ("make the letter
shorter", "make it more formal", or just "shorter" as your next message),
or type it in the card's **revise** box: each change is a new version,
and the reply says what changed. Every version is kept. A message about
something else ("turn on the kitchen lights") is answered as usual and
leaves the draft alone.

- **open** shows the draft beside the chat on a wide screen (in its place
  on a narrow one), with the message box still below it. Pick **v1**,
  **v2** … to see any version; **changes** marks added words and strikes
  out removed ones.
- **edit** lets you change it by hand; saving makes a new version
  "edited by you" (on another device it says "edited on" and the device's
  name). If a change comes in while you edit, you're told, and nothing is
  lost: every version stays in the version list.
- **save** puts it in [Files](#files) → Documents → **From Chat**: a
  resume or cover letter as **.docx** (best for job sites), a letter as
  **PDF**; the other format is one press away. After saving, Domovoi reads
  the file back and checks every word is there in order: **text readable
  · single column** means a job site's applicant tracking system can read
  it too.
- **print** (or typing "print it") opens the Print dialog with a preview;
  nothing prints until you press **Print**.
- Only the draft, not the whole chat, goes to the model for each change.
  With AI chat and questions turned off, nothing new is drafted, but
  drafts already written can still be opened, edited, saved and printed.

**Look things up:** say "look up …", "what does this page say:
https://…", or ask something Domovoi doesn't know. It searches its own
manual and your offline library first, without asking. Before anything
goes online it shows a card with the exact words and where they go:
**Allow once**, **Allow in this chat** or **Not now**. The chip at the top
of a chat says whether it may go online; chats that did show a globe. The
answer lists what it read (Wikipedia's with "CC BY-SA 4.0"), and says "I
couldn't find that" when what it read doesn't say. [Settings →
Search](#settings/search) *(admin)* has **Always allow internet** (off to
start), the order of the sources, and **Use your own search** (your own
SearXNG, or a Brave Search key).

**Summarize a long document:** ask for a summary and Domovoi reads it in
parts, then combines them. The reply's place shows "reading part 3 of 8 ·
about 1 min left" (the time comes from how fast your server has been) with
**stop**. Stop keeps what was read ("Summary of pages 1-9 (stopped)"), and
**carry on** reads the rest. You can leave the page: the summary is there
when you come back. A question asked by voice while it reads goes next,
after the part being read (a few seconds on a GPU, up to about a minute on
a server without one). Each point cites a page; a part Domovoi couldn't
cite anything from is named under the summary. After a server restart the
chat asks "carry on?" before it reads any more. One summary reads at a time
per chat, and two in the house.

**What does this letter say?** Attach a photo of a letter or a bill (one
page per photo) and ask "what does this letter say?" or "what do I need
to do?". The answer says who it's from, what it's about, the deadlines,
the amounts, the reference numbers and what to do next. Its dates,
amounts and numbers are checked against the text Domovoi read from the
photo; anything it can't find there is left out, and a note says so. A
photo that was misread to begin with can still be wrong, so check what
matters on the letter itself. Press
**photo 1** under the answer to see the text Domovoi read. A blurry photo
or handwriting gets a heads-up first. If a letter has text aimed at an AI
assistant ("ignore previous instructions…"), Domovoi doesn't follow it
and says so: genuine letters don't do this. Everyone in the house can see
a home-server chat, so a phone chat is the private place for personal
mail. Real handwriting hasn't been measured yet, and on a home server
without a graphics card a letter takes about 2 minutes.

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
- An old .doc or .rtf file: save it as .docx or PDF, then attach it again.
- "pictures of text (scanned)": scanned pages aren't searched yet.
- "set to stay off the internet": your home's internet answer is No
  ([Settings → Internet](#settings/internet)); only the manual and the
  offline library are searched.
- "Brave Search refused your key": check the key in [Settings →
  Search](#settings/search); the answer came from Wikipedia meanwhile.
- "Points that span parts may be missed": a long summary is made part by
  part. Ask about the specific thing to have it found wherever it is.
- "Domovoi left out a date or amount that isn't in the letter's text":
  the explanation named one the letter doesn't have, so it isn't shown.
  Check the letter itself, or take the photo again in good light.

**Also:** voice chat mode, a spoken conversation in a room, is an early
experiment and off unless whoever set up Domovoi turned it on.

**Try saying:** "let's chat" (in a room, while voice chat mode is on)

[More detail](docs/FAQ.md#can-i-send-pictures-in-chat)

### What Domovoi is good and not so good at

{{generated:weak-spots}}

**How to:** a heads-up names the weakness and offers what works here:
**calculate exactly** (the app adds up the numbers the model lists, each
checked against its page, and says so plainly when it can't read one for
certain), **go ahead** or **try anyway**. **don't warn me
about this again** silences that kind on this device; [Settings →
Chat](#settings/chat) (under Chat & AI) turns them all off. With them off, the two that
guard against a wrong answer you can't see coming (exact sums and scanned
pages) become a one-line note under the answer instead.

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

### Printers and scanners

Domovoi prints to and scans from **network printers**: ones on your
Wi-Fi or plugged into your router that work with **AirPrint**, **IPP
Everywhere** or **Mopria**. That's almost every network printer from the
last ten years; if your phone can print to it with no app, Domovoi can
too. USB printers, and older printers that need a driver from the maker,
don't work. Scanning needs a printer whose scanner works over AirPrint
too (often called AirScan or eSCL).

**How to**

- [Settings → Printers & scanners](#settings/printers) shows the
  printers Domovoi can see. Press **Add** on yours, name it, then
  **Test page**. Not listed? Press **Look again**, or type its address
  under **Add by address** (on the printer's own screen or settings page).
- One printer is the house's **Default**. A room can have its own default
  (**Default in rooms**): voice from that room prints there.
- **Two-sided** and **Colour** follow the printer's own settings unless
  you switch them per printer.
- **Print** sits on files in [Files](#files) and Documents when a printer
  is set up: PDFs, pictures, text and markdown files, and spreadsheets
  (printed as a plain table). You pick the pages, copies, two-sided,
  colour and paper, and see a preview first.
- Word and OpenDocument files don't print here: save them as PDF in Word
  or Docs first, then print the PDF.
- **Scan** sits at the top of Documents when a scanner is set up. Scans
  are saved as one PDF in **Documents → Scans**, named by date.
- Removing or renaming a printer needs the admin password *(admin)*.

**Try saying**

- "print my cover letter" (it tells you the pages and asks first)
- "how much ink is left"
- "is the printer out of paper"
- "set up the printer"
- "scan this to Documents"
- "stop printing"

**If something's wrong**

- **Out of paper, jammed, cover open:** the printer's row says so in plain
  words, and a voice print that fails is said once in the room that asked.
  Fix it at the printer; the job carries on.
- **"Level not reported":** some printers don't share their ink or toner
  levels. Check the printer's own screen.
- **"Isn't answering":** the printer is off or asleep, or moved to a
  different network. Press **Look again**.
- **"Security certificate changed":** usually a printer update or reset.
  An admin checks it and presses **Trust the new certificate**.
- Each job is limited to 100 pages and 20 copies, and the house to 500
  sheets a day; an admin can change the limits. The list of recent jobs
  is kept 90 days, and an admin can clear it.

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

- Sign in: [Settings → Advanced](#settings/advanced) → **sign
  in**. A reload in that browser keeps you signed in for 60 minutes after
  you signed in; **sign out** ends it at once. Change the minutes in
  [Settings → Advanced](#settings/advanced) → **Security** →
  **Stay signed in after a reload**.
- Turn guest music control off: same place, **Guests can change the
  music in a playing room**.
- The app never signs in as admin; it opens the dashboard for that.

**If something's wrong:** forgot the admin password? It can only be reset
on the server itself. Ask whoever set up Domovoi.

[More detail](docs/TROUBLESHOOTING.md#admin-locked-out)

### Devices and house rules

[Settings → Phones & browsers](#settings/phones-and-browsers):

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
  delete**); the commands kept so far are in [Settings → Voice
  history](#settings/voice-history).
- *(admin)* listen to a room's command recordings, mark whether each one
  ended at the right moment, and delete them: [Settings → Voice
  history](#settings/voice-history). A room keeps them only while an admin
  has turned them on for it.

An admin can change play history, news and timer history in [Settings →
Advanced](#settings/advanced). [More detail](docs/SECURITY_PRIVACY.md#data-at-rest)

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

[Settings → About & updates](#settings/about) shows
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
About & updates](#settings/about) → **Restart
Domovoi** *(admin)*.

**If every room is offline but the dashboard opens**, the server's core
has stopped answering. It restarts itself within about a minute. If it
doesn't, an admin presses **Restart the core** (on [Home](#home),
[Settings → Rooms](#settings/rooms) or [Settings → Health](#settings/health)). The app
says the core isn't answering, and reconnects by itself.

**Try saying:** "how's domovoi doing" (how the server is: its processor,
memory and disk)

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

### Asking Domovoi for help

Domovoi answers questions about itself: from this manual, its FAQ and
troubleshooting guide, and the help of the plugins you installed. It
answers only from what it finds. When nothing covers the question, it
says "The manual doesn't cover that." rather than guessing.

**How to**

- **Dashboard:** press **?** at the top of any page. Search the help, or
  press **ask Domovoi** to start a chat called "Help: …".
- **Dashboard:** an error message has **ask Domovoi about this**: the help
  opens with the error's code and the page you were on.
- In a chat, ask "how do I …?" or "why is the kitchen offline?". Domovoi
  also reads what its own checks show: rooms that are offline and why,
  plugins, its containers, and the last update (its details only for an
  admin).
- **Something on your screen?** Paste or attach a screenshot in a chat on
  the home server. The vision model reads it on your server; it never
  leaves your home.
- **App:** a chat on this phone answers from the app's own copy of the
  manual, offline. It can't read screenshots yet: use a home-server chat
  for those.

**Try saying**

- "how do I add a room"
- "how do I change the wake word"

After the short answer, Domovoi asks "Want me to send the steps to your
phone?" Say yes, and they arrive as a chat called "Help: …" on the phones
marked as yours ([Settings → Phones & browsers](#app/connection), **This phone
belongs to**, in the app). If Domovoi doesn't know your voice, it asks who
to send them to. It never sends them to every phone.

**If something's wrong**

- **AI chat and questions are off**: help still searches the manual and
  the docs, with no model, and screenshots can't be read. See
  [Models](#node:models).
- **Help can't solve it**: **report a problem**, in the **?** panel, builds
  a summary of your Domovoi with no passwords, codes, addresses or names.
  Read it, then copy or save it and post it yourself, on GitHub
  Discussions or Discord. Nothing is sent for you.

### Troubleshooting

- **A room doesn't answer**: see [Your rooms](#node:rooms).
- **Every room is offline**: see [Restarting](#node:restart).
- **"Just a moment, I'm waking up my language model"**: normal once after
  a restart.
- **The dashboard looks old after an update**: reload the page once.
- **A word is said wrong**: [Say it like](#node:voice).
- **Chat says pictures need the vision model**: [Models](#node:models).
- **Something is greyed with "needs internet"**: [Internet](#node:internet).
- **Not sure where to look**: ask Domovoi: [Asking Domovoi for help](#node:help-desk).
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
  history" setting (People → Voice history, and Settings → Advanced
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
  Advanced](#settings/advanced) → **Privacy**.
- *(admin)* **Delete all voice history**, on the same page, deletes it all
  at once. People, voice samples, memories, notes, timers and chats stay.
- *(admin)* **forget person** on [People](#people) does what "forget me"
  does, for anyone: it deletes only what that person said.
