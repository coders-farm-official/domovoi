<!--
The words of Domovoi's user manual: the dashboard's #manual page and the
Android app's manual. This is the SOURCE. The product reads
web/static/manual-content.json, which `python scripts/manual/build_manual.py`
makes from this file and the map in that script (topics, pieces,
subtitles, search words, routes, and which handler each "try saying"
phrase reaches). Edit here, run the script, commit both;
domovoi/tests/test_manual_content.py fails while they disagree.

How to write it:
- "## Topic" and "### Piece" headings must match the map in build_manual.py,
  in its order. A topic holds at most 7 pieces: grow the manual by adding
  topics, not by packing pieces.
- Links: [Settings → Voice → Voices](#settings/voices) opens a route (ROUTES
  in the script); [Your rooms](#node:rooms) opens another page of the
  manual; [More detail](docs/FAQ.md#anchor) is a doc on GitHub, for the
  version the server runs. Settings paths name the group (Settings, then
  Chat & AI, then Models).
- *(admin)* and *(Preview)* become pills. {wake} is the wake word the
  household's rooms answer to (live); [live slot: name] is filled from the
  server.
- A paragraph or bullet starting **App:** shows only in the app,
  **Dashboard:** only on the dashboard.
- Every "try saying" phrase needs an entry in PHRASES in the script, and is
  a room phrase: the router test routes it. Typed chat examples go under a
  plain bold line ("**In a chat, type**"), never under "Try saying".
- {{name}} on a line of its own is replaced by "### name" from a Snippets
  section at the end (none today: add "## Snippets" back for copy that
  waits on other work).
-->

# Domovoi user manual

## Start here

**Domovoi is your home's own computer system.** It runs on a computer in
your house, the Domovoi server. Your music, videos, books and files live
there, and play in every room and on your phone. Small speakers with
microphones put it in every room, so you can talk to it. AI chat is there
if you want it, and it runs at home too. Your voice never leaves your home
network.

**Three ways to use it**

- **Talk to a room.** Say {wake}, then what you want: "set a timer for 10
  minutes." Your household can also train a word of its own: see [Wake
  word and greeting](#node:wake-word).
- **The dashboard.** Open it in any browser on your Wi-Fi, at the address
  whoever set up Domovoi gave you (it ends in `:6369`). You can manage
  music, files, chat, people and settings there.
- **The Domovoi app** on Android phones. It has the everyday pages, a
  music player and Chat. With no server in reach it still plays the
  phone's own music, and a chat on the phone can run on the phone itself.

[live slot: wake-word]

**Try saying**

- "set a timer for 10 minutes"
- "what's the weather tomorrow" (checks online, if you allow it)
- "play some jazz in the kitchen"
- "tell everyone dinner's ready"

**Getting set up** (a checklist; each line ticks itself when it's done)

1. **Claim the admin account** *(admin)*: [Settings → System → Advanced](#settings/advanced),
   **set up admin**. Type the 8-word setup code and choose a password.
   Where the code is: the Windows installer shows it on its last page; on
   Linux it is in a file on the server. [More detail](README.md#2-claim-the-admin-account)
2. **Answer the internet question**: Yes, Sometimes or No. You can change
   it later in [Settings → Privacy → Internet](#settings/internet).
3. **Add your first room**: [Settings → Rooms & devices → Rooms](#settings/rooms).
   See [A Raspberry Pi room](#node:pi-room).
4. **Say the wake word** in that room: {wake}, then "what time is it?"
5. **Introduce yourself** in that room: "my name is …", then "yes".
6. **Add your music**: [Music](#music), **Upload**, or copy files into the
   server's music folder.
7. **Put the app on your phones**: [Settings → Rooms & devices → Phones &
   browsers → Get the Domovoi app](#settings/phones-and-browsers).

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
Linux and on Windows. An admin can change models in [Settings → Chat & AI
→ Models](#settings/models). [More detail](docs/CPU_HOST.md)

**AI chat and questions are optional.** The installers ask whether to
include them, and an admin can turn them off or on at any time under **AI
chat and questions** in [Settings → Chat & AI → Models](#settings/models).
Off, no routing, answer or vision model is used: voice commands still work
on their usual phrases, and so do speech recognition, the voices and the
wake word. See [When AI is off](#node:ai-off).

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

- The **dashboard** is the web page where you manage everything. It's the
  only place to install plugins and change server settings.
- The **app** brings the everyday pages to your phone, with its own music
  player and Chat. Chat holds your chats on the home server and chats
  that run on the phone itself, with no server.
- **Settings** is in five groups: Rooms & devices, Voice, Chat & AI,
  Privacy, and System. The search box at the top finds a setting by name:
  try "wake", "voice history" or "token".

[More detail](README.md#what-each-interface-can-do)

---

## Things to say

### Timers and reminders

Timers count down and ring. Reminders say your message out loud when the
time comes: in a while ("in 20 minutes") or at a clock time ("at 6 pm",
"tomorrow morning", "on Friday at 5"). Both are heard around the house.
Domovoi says back the time it set. A time on its own means the next
time it comes; add morning, evening, am or pm to be sure.

**Try saying**

- "set a timer for 10 minutes"
- "set a timer for 8 minutes for the pasta"
- "how much time is left on the timer"
- "cancel the timer"
- "remind me to call Mom in 20 minutes"
- "remind me in 2 hours to take the bins out"
- "remind me at 6 pm to take the bins out"
- "wake me up at 7"
- "set an alarm for 6:30"
- "cancel the 7 am alarm"
- "what reminders do I have"

**Also:** your timers show on [Home](#home) and in each room's **Timers**
tab ([Settings → Rooms & devices → Rooms](#settings/rooms)).

**App:** if Home's timers card says "Timers may ring a few minutes late on
this phone", tap **allow alarms**.

**If something's wrong**

- A wake-up or an alarm is spoken in the room, like a reminder; it
  doesn't ring.
- Times are the server's own time, the one "what time is it" answers in.
  If they're hours off, ask whoever set up Domovoi to set the server's
  time zone.

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
  [Settings → Privacy → Internet](#settings/internet).
- "I'm set up for home commands" or "I can't look that up with AI turned
  off": AI chat and questions are off for your home. Commands still work.
  See [When AI is off](#node:ai-off).
- "My question-answering model isn't installed yet": AI is on but its
  model hasn't been downloaded. An admin can download it in [Settings →
  Chat & AI → Models](#settings/models).
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

**If something's wrong**

- Drop-ins need a room with an echo-cancelling microphone: the XVF3800
  board, or most phones in room mode.
- Whether a room picks up a drop-in by itself, asks first or rings is one
  setting for the whole house *(admin)*: [Settings → System →
  Advanced](#settings/advanced), **show the settings**, **Drop-in**,
  **Accept mode**.

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

**Also:** notes show in each room's **Notes** tab ([Settings → Rooms &
devices → Rooms](#settings/rooms)). Memories show on [People](#people),
under each person's **Memory** tab.

[More detail](docs/FAQ.md#does-it-support-multiple-people)

### Everything it can do

This lists every feature this Domovoi has, including installed plugins,
with phrases to try. It also says which features work offline, which work
less well offline, and which need the internet. Install a plugin and its
features appear here.

[live slot: things-to-say]

Ask "what can you do?" out loud and Domovoi names a few of these, with
one thing to say for each; this page has them all.

**Try saying**

- "what can you do"
- "what can I say"

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

## Music and media

### Music and room queues

Your music library, on the server, played in any room or in the browser.

**How to**

- Add music: [Music](#music) → **Upload**, or copy files into the server's
  music folder.
- Each room has one shared queue, and anyone in the house can add to it.
- **Dashboard:** [Music → Room queue](#music/queue): drag to reorder. A
  room card's **queue** button goes straight there.
- **App:** [Music](#music/queue) → **room queue**: **move up**, **move
  down** and **remove from queue** on each song. **play next** on a song
  in the library puts it next in the room's queue.
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
your internet answer is Yes. **App:** an episode's **save to this device**
keeps it on the phone.

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
phone**; the upload button (**add to server**) sends books from the phone
to the server.

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

## Chat and help

**Intro:** Type to Domovoi on the dashboard or in the app. A chat on your
home server is shared with the household; a chat on the phone stays on
the phone. Domovoi asks before anything goes online, and it answers
questions about itself.

### Chat

Type to Domovoi in [Chat](#chat). A chat on the dashboard runs on your
home server, with its models, and everyone in the household can see it.
The app also has chats that run on the phone itself: see [Chat on the
phone](#node:phone-chat).

**How to**

- [Chat](#chat) → **new chat**, then type. A chat is named by its first
  words. After the first reply Domovoi gives it a short title; a name you
  give it stays.
- Beside each chat: **rename**, **archive** and **delete** (the bin).
  **search chats** is above the list, and **archived** is at its end.
- **Pictures:** the paperclip adds up to 4 to a message (PNG, JPEG, GIF,
  WebP or BMP, 20 MB each). You can paste a screenshot too. The vision
  model reads them on your server.
- **Documents:** the paperclip takes those too, up to 4 a message. See
  [Ask about a document](#node:ask-document).
- **Drafts and letters:** Chat can write drafts for you. Read them before
  you send them; it can get details wrong. See [Write a draft](#node:drafts)
  and [Letters](#node:letters).
- **Online:** Domovoi can look things up, after asking. See [Look things
  up](#node:lookups).
- **App:** a home-server chat's paperclip offers **a picture** and **a
  document (PDF, Word, sheet, text)**. The app turns HEIC phone photos
  into JPEG first.

**If something's wrong**

- "Pictures need the vision model": an admin can install it in [Settings
  → Chat & AI → Models](#settings/models).
- Chat isn't there, or says "chat is off": AI chat and questions are off
  for your home. See [When AI is off](#node:ai-off).
- The dashboard refuses a HEIC phone photo: send it from the app, which
  converts it.

**Also:** voice chat mode, a spoken conversation in a room, is an early
experiment. It is off unless whoever set up Domovoi turned it on.

**Try saying:** "let's chat" (in a room, while voice chat mode is on)

[More detail](docs/FAQ.md#can-i-send-pictures-in-chat)

### Look things up

Domovoi can look up what its own models don't know. It asks before
anything leaves your home.

**How to**

- In a chat, ask "look up …", "what does this page say: https://…", or a
  question Domovoi doesn't know.
- It searches its own manual and your offline library first (the Kiwix
  plugin, if it's installed), without asking.
- Before anything goes online, a card shows the exact words and where
  they go: **Allow once**, **Allow in this chat** or **Not now**.
- The chip at the top of a chat says **internet off**, **internet on for
  this chat** or **internet always on**. Chats that went online show a
  globe in the list.
- Under the answer, Domovoi lists what it read, by title and site.
  Wikipedia's carry "CC BY-SA 4.0". When what it read doesn't say, the
  answer is "I couldn't find that".
- A page's words can't make Domovoi do anything. It reads them as text
  only.
- *(admin)* [Settings → Chat & AI → Search](#settings/search) has
  **Always allow internet** (off to start; on, chats don't ask), **Where
  Domovoi looks** (the order of the sources), and **Use your own
  search**: your own SearXNG, or a Brave Search key. Brave charges after a
  free monthly allowance. A saved key is never shown again. Your own
  SearXNG's address must start with `https://`, unless it is on your home
  network.
- **App:** a phone chat asks the same way. At home it looks things up
  through your home server. Away from home it asks Wikipedia itself, or
  your own search. A phone chat's **model settings** has these under
  **Looking things up**.

**If something's wrong**

- "set to stay off the internet": your home's internet answer is No
  ([Settings → Privacy → Internet](#settings/internet)). Only the manual
  and the offline library answer.
- "Brave Search refused your key": check the key in [Settings → Chat & AI
  → Search](#settings/search). Wikipedia answered meanwhile.
- "Use https:// unless the search is on your home network": your own
  search's address is on the internet, so it needs `https://`.
- By voice, "I can't look that up with AI turned off": see [When AI is
  off](#node:ai-off).

[More detail](docs/INTERNET.md)

### What Domovoi is good and not so good at

{{generated:weak-spots}}

**How to**

- A **heads-up** is a short card that comes before an answer Domovoi has
  been measured to be weak at. It names the weakness and offers what
  works there:
  - rewriting, translating or proofreading a document over about two
    pages: **try anyway**, or ask for one part at a time;
  - a sum, an average, a difference, or the days between two dates, from
    numbers spread through a document: **calculate exactly** or **try
    anyway**;
  - a summary that will take more than 3 minutes here: **go ahead**;
  - a summary of a document with scanned pages: **go ahead**;
  - a document in a language other than English or Spanish: **try
    anyway**;
  - a blurry photo in a home-server chat: **choose another** or **send
    anyway**.
- Each kind shows once per chat. **don't warn me about this again**
  silences that kind on this browser or phone.
- [Settings → Chat & AI → Chat](#settings/chat), **heads-up before shaky
  results**, turns them all off on this browser or phone. **warn again**
  brings back one you silenced. With heads-ups off, exact sums and scanned
  pages still get a one-line note under the answer.

**Calculate exactly**

- The model only lists the numbers it found, with their pages. Domovoi
  checks each one on its page and does the sum itself. The answer says so:
  "Worked out by the app from the numbers listed, not by the model."
- It works out only what it found for your question, and says how many
  ("from the 3 entries I found"). Check that's all of them.
- **It refuses rather than guesses.** It works nothing out, and says why
  in one sentence, when:
  - it can't be sure how to read a number;
  - a number isn't on its page exactly as listed;
  - one number may have been listed twice from the same place;
  - a date's day and month could be either way round;
  - the numbers are in different units or currencies.
- A total the document states itself, such as a bill's "amount due", is
  quoted, not calculated.

### When AI is off

**AI chat and questions are optional.** An admin can turn them off for
the whole home. Off, nothing in Domovoi asks a language model.

**Still works**

- Voice commands, said the usual way: timers, reminders and alarms, the
  clock and the calendar, music in every room, podcasts and audiobooks,
  announcements and drop-ins, notes and "remember that …", the news
  headlines, the Wi-Fi check, printing and scanning.
- Hearing you, the voices, the wake word, and knowing who's speaking.
- The calculator, for the phrasings it knows ("what's 15 percent of 80").
- Every page of the dashboard and the app except home-server Chat: music,
  files, documents, the calendar, people, settings.
- Help: the **?** panel and "how do I …?" answer from this manual, with
  no model.
- Chats on a phone that can run its own model: they don't use the
  server's.
- An offline library (the Kiwix plugin) still answers by voice, from the
  article's first sentences.
- Drafts already written can be opened, edited, saved and printed.
- "read me the lease": reading a document aloud needs no model.

**Stops**

- Questions, jokes and stories. Domovoi says "I'm set up for home
  commands" and suggests "what can you do".
- Weather, scores, prices and "are you sure?": "I can't look that up with
  AI turned off."
- A command said an unusual way ("could you start a five minute countdown
  for me"). Say it the usual way: "set a timer for 5 minutes".
- Chat on the dashboard, and home-server chats in the app. No pictures,
  screenshots, documents or letters are read there. A long summary stops
  at its next part, keeping what it read.
- The news briefing's summary (the headlines are still read), memory
  suggestions, titles for new chats, heads-ups, and voice chat mode.
- New drafts. "take a letter" says it needs the AI features, before you
  start.

**How to** *(admin)*

- Turn it off or on: [Settings → Chat & AI → Models](#settings/models),
  **AI chat and questions**. Turning it on shows the download size first
  (about 6.4 GB, or 9.1 GB on a server with 24 GB of memory or more), then
  downloads the models.
- Both installers ask "Include AI chat and questions?". No skips those
  downloads, and turning it on later needs no reinstall.

**Try saying**

- "what can you do"

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
  manual, offline. It can't read screenshots: use a home-server chat for
  those.

**Try saying**

- "how do I add a room"
- "how do I change the wake word"
- "how do I add a printer"

After the short answer, Domovoi asks "Want me to send the steps to your
phone?" Say yes, and they arrive as a chat called "Help: …" on the phones
marked as yours ([Settings → Rooms & devices → Phones &
browsers](#app/connection), **This phone belongs to**, in the app). If
Domovoi doesn't know your voice, it asks who to send them to. It never
sends them to every phone.

**If something's wrong**

- **AI chat and questions are off**: help still searches the manual and
  the docs, with no model, and screenshots can't be read. See [When AI is
  off](#node:ai-off).
- **Help can't solve it**: **report a problem**, in the **?** panel, builds
  a summary of your Domovoi with no passwords, codes, addresses or names.
  Read it, then copy or save it and post it yourself, on GitHub
  Discussions or Discord. Nothing is sent for you.

---

## Documents and letters

**Intro:** Ask Domovoi about a document, have it summarize a long one,
write a letter with it, or have a room read one out. It reads and writes
on your home server, or on the phone in a phone chat. Nothing is ever
sent for you.

### Ask about a document

Attach a document to a chat and ask what it says. Domovoi answers from
the document, and shows you where.

**How to**

- In a chat, the paperclip takes a PDF, Word (.docx), OpenDocument
  (.odt), Excel (.xlsx), CSV, text or markdown file: up to 100 MB each,
  and up to 4 a message.
- **App:** in a home-server chat, the paperclip's **a document (PDF,
  Word, sheet, text)**; in a phone chat, **a document** under **+**.
- Ask what you want to know. The answer names the pages or sections it
  came from; press one to see the passage, or to open a PDF at that page.
- When the document doesn't say, Domovoi answers "I couldn't find that in
  the document." It doesn't guess.
- **Dashboard:** in [Files](#files) → Documents, **Ask** on a document
  starts a chat about it.
- **App:** in [Files](#files), the speech-bubble button on a document
  (**ask about this document**) does the same. A file on this phone opens
  a phone chat; a file in the home server's Documents opens a home-server
  chat.
- A very large file: about the first 4 million characters (roughly 1,000
  pages) are read, and the attachment says how far it got.
- **App:** in a chat **on this phone**, the document is read on the phone
  and stays there. Reading a PDF on the phone needs Android 15, or 12 to
  14 with the PDF update. Otherwise **ask the home server** reads its text
  and sends it back, and nothing is kept there.

**In a chat, type**

- "when does the lease end?"
- "what does it say about pets?"
- "summarize this"

**If something's wrong**

- An old .doc or .rtf file: save it as .docx or PDF, then attach it
  again.
- "pictures of text (scanned)": some pages are scans, and Domovoi can't
  read those yet. The answer names the pages it skipped. A PDF that is all
  scan is refused ("pictures of text (a scan)"). Save the document from
  its own program as a PDF with text, or type the part you need.
- A file that isn't what its name says, or is built to overwhelm a
  reader, is refused, with a sentence saying why.
- Documents can't be added while AI chat and questions are off.

### Summaries of long documents

Ask for a summary, and Domovoi reads a long document in parts, then puts
the parts together.

**How to**

- Attach the document and ask for a summary.
- While it reads, the reply shows "reading part 3 of 8 · about 1 min
  left", with **stop**. The time comes from how fast this server or phone
  has been; until it knows, it shows only the part.
- **stop** keeps what was read: "Summary of pages 1-9 (stopped)". **carry
  on** reads the rest.
- Each point names its page. A part Domovoi couldn't cite anything from
  is named under the summary.
- **Dashboard:** you can leave the page; the summary is there when you
  come back. After a server restart, the chat asks "carry on?" before it
  reads any more. One summary reads at a time in a chat, and two in the
  house.
- **App:** a phone chat reads while Chat is open. Leave Chat and it
  pauses at the end of the part, then carries on when you come back.
- **App:** **use home server (about N min)** appears only when your home
  server has been measured faster than this phone for that document,
  sending it included. The file is read there and deleted once it's read;
  the summary waits in the server's memory until the phone takes it, and
  nothing is kept. If the server can't finish, Chat says so and offers
  **read it on this phone**.
- A question asked in a room while a summary reads goes first, after the
  part being read: a few seconds on a server with a graphics card, up to
  about a minute on one without.

**If something's wrong**

- "Points that span parts may be missed": a long summary is made part by
  part. Ask about the specific thing, and Domovoi finds it wherever it is.
- A summary that will take more than 3 minutes gets a heads-up first. See
  [What Domovoi is good and not so good at](#node:good-and-not).

### Write a draft

Chat can write drafts for you. Read them before you send them; it can get
details wrong.

Ask for a resume, a cover letter or a letter, and Domovoi shows a draft
you change until it's right. Nothing is ever sent: you save it or print
it, and send it yourself.

**How to**

- Ask in a chat: "write a cover letter for the warehouse job at Acme", or
  "draft a letter to my landlord about the heater".
- The draft shows as a card with **open**, **save** and **download**, and
  **print** when a printer is set up. **App:** **open**, **save**,
  **share** and **print**.
- To change it, say what to change as your next message ("make it
  shorter", "more formal", "mention my forklift licence"), or type it in
  the card's box (**change this draft…**) and press **revise**. Each change
  is a new version, and the reply says what changed. A message about
  something else is answered as usual and leaves the draft alone.
- **open** shows the draft beside the chat (full screen on a phone), with
  the message box still below it. Pick **v1**, **v2** … to see any
  version. **changes** marks added words and strikes out removed ones.
- **edit** changes it by hand. **save as v3** keeps that as a new version,
  marked "edited by you". If a change comes in while you're editing,
  you're told, and nothing is lost.
- When you ask for a change, a draft keeps its date, address, "Dear …"
  line, sign-off and sections. To change those, say so: "sign it Alex
  Smith", "address it to the property manager".
- **save** puts it in [Files](#files) → Documents → **From Chat**. A
  resume or cover letter saves as .docx (best for job sites), and a letter
  as PDF; the other format is one press away. Domovoi then reads the file
  back and checks every word is there, in order. **text readable · single
  column** means a job site's system can read it too.
- **print**, or typing "print it", shows a card with **Print**, which
  opens the Print dialog with a preview. Nothing prints until you press
  **Print** there. See [Printers and scanners](#node:printers).
- **App:** in a chat **on this phone**, the draft stays on the phone.
  **save** puts it in **Chat documents**, which [Files](#files) shows once
  it holds one. In **open**, the **more** menu has **save a copy to…**
  (anywhere you pick), **send to home** (the home server's Documents →
  From Chat, when it's in reach), **print** (the phone's own print dialog)
  and **read aloud**.
- Only the draft, not the whole chat, goes to the model for each change.

**If something's wrong**

- Read every draft before you use it: names, dates, numbers and anything
  it says about you. Change what's wrong with **edit**.
- After "shorter", look at **changes**: a shorter version can leave out
  something you gave.
- With AI chat and questions off, nothing new is drafted. Drafts already
  written can still be opened, edited, saved and printed.

### Letters

Domovoi can explain a letter or a bill from a photo, or take a letter you
say out loud in a room.

**What does this letter say?**

- Attach a photo of a letter or a bill (one page per photo) and ask "what
  does this letter say?" or "what do I need to do?".
- The answer says who it's from, what it's about, the deadlines, the
  amounts, the reference numbers and what to do next.
- Dates and amounts are quoted straight from the letter: under **Dates in
  this letter** and **Amounts in this letter**, each is shown inside the
  letter's own sentence, as Domovoi read it from the photo, with the
  figure in bold. Domovoi's own words never give a figure.
- A photo that was misread to begin with can still be wrong, so check what
  matters on the letter itself. Press **photo 1** under the answer to see
  the text Domovoi read.
- A blurry photo or handwriting gets a heads-up first. Real handwriting
  hasn't been measured yet.
- If a letter has text aimed at an AI assistant ("ignore previous
  instructions…"), Domovoi doesn't follow it and says so: genuine letters
  don't do this.
- Everyone in the house can see a home-server chat, so a phone chat is the
  private place for personal mail. On a home server without a graphics
  card a letter takes about 2 minutes.
- **App:** in a phone chat, **+** → **take a photo** or **choose a photo**
  on phones that read photos themselves. The photo isn't kept; the text
  read from it stays with the answer, so you can ask about the letter
  again, and goes when you delete the chat. On a phone that can't read
  photos itself, **+** offers **a letter for the home server: take a
  photo** or **choose a photo** when a paired home server has its picture
  model: your home server reads the photo and keeps nothing, and the
  explanation is written on this phone.

**Take a letter by voice**

- In any room, say "take a letter to the landlord". The room says "Go
  ahead. Say end of letter when you're done."
- Say what the letter should say. "scratch that" takes out the last thing
  you said. Say "end of letter" to finish, or just stop talking.
- Domovoi writes it up as a proper letter, signed with your name if it
  knows your voice (otherwise it asks "Who's it from?"). It saves it as a
  Word file in Documents → **From Chat**, and puts it in a home-server
  chat called "Letter to the landlord", where you can change it, save it
  as PDF or print it.
- It's never sent anywhere. The room tells you where it saved it, and
  "read it back" reads it to you.
- "cancel the letter" throws it away. Nothing said: nothing is saved.
- Writing it up needs AI chat and questions on.

**Try saying**

- "take a letter to the landlord"
- "read it back"

**If something's wrong**

- "Domovoi left out a sentence of its own that named a date, an amount or
  a number": Domovoi's explanation never gives figures itself; look for
  them in the letter's sentences quoted below it.

### Read it to me

Any room can read a document from your household's Documents out loud.

**How to**

- Say "read me" and a document's name, as it is in Documents ("read me
  the house rules"), or with "document" or "file" after part of it ("read
  me the tax document"). Domovoi finds it in Documents (PDF, Word,
  OpenDocument, text or markdown) and reads it out in that room.
- If two fit, it asks which one; if it only heard something close, it
  asks "Did you mean …?".
- Over about a page, it says how long first ("It's 2 pages, about 3
  minutes") and asks "The whole thing, or just the start?". Then it reads
  about a page at a time and asks "Shall I go on?".
- Say **stop**, **pause**, **start again** or **read that again**. Later,
  "continue reading" picks up where it stopped.
- It reads only what's in the household's Documents, never a phone's own
  files, and only in the room that asked. It works with AI chat and
  questions off.
- **App:** a phone chat's draft has **read aloud** in its **more** menu.

**Try saying**

- "read me the lease"
- "continue reading" (in the room where a reading stopped)
- "stop reading" (while it reads)
- "read it back" (the last document read or saved)

---

## Files, printing and calendar

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

**Also**

- Domovoi keeps two folders of its own in Documents: **Scans**, from the
  scanner, named by date, and **From Chat**, for drafts and letters saved
  from Chat.
- **App:** **Chat documents** holds drafts saved in a phone chat. Files
  shows it once it holds one.

[More detail](docs/FAQ.md#can-i-move-files-around-without-the-command-line)

### Documents, sheets and drawings

**How to:** [Files](#files) → Documents → **New** → **Document**,
**Spreadsheet**, **Drawing** or **Text file**. Each opens in its editor;
press **Save**.

**App:** opens text files and sheets from Files.

**Also:** to ask about a document, see [Ask about a
document](#node:ask-document); to have a room read one out, see [Read it
to me](#node:read-aloud).

### Printers and scanners

Domovoi prints to and scans from **network printers**: ones on your
Wi-Fi, or plugged into your router, that work with **AirPrint**, **IPP
Everywhere** or **Mopria**. That's almost every network printer from the
last ten years: if your phone can print to it with no app, Domovoi can
too. USB printers, and older printers that need a driver from the maker,
don't work. Scanning needs a printer whose scanner works over AirPrint
too (often called AirScan or eSCL).

**How to**

- [Settings → Rooms & devices → Printers & scanners](#settings/printers)
  shows the printers Domovoi can see. Press **Add** on yours, name it,
  then **Test page**. Not listed? Press **Look for printers** (**Look
  again** once it has looked), or type its address under **Add by
  address**. The address is on the printer's own screen or settings page.
- **Default for the house** makes one printer the house's. A room can
  have its own (**Default in rooms**): voice from that room prints there.
- **Two-sided by default** and **Colour by default** follow the printer's
  own settings unless you set them per printer.
- **Print** sits on files in [Files](#files) and Documents when a printer
  is set up. It prints PDFs, pictures, text and markdown files, and
  spreadsheets (as a plain table). You pick the pages, copies, two-sided,
  colour and paper size, and see a preview first.
- Word and OpenDocument files don't print here. Save them as PDF in Word
  or Docs first, then print the PDF.
- **A draft from Chat:** **print** on the draft, or type "print it". You
  see the preview first.
- **Scan** sits at the top of Documents when a scanner is set up. Scans
  are saved as one PDF in **Documents → Scans**, named by date.
- Renaming or removing a printer needs the admin password *(admin)*.

**Try saying**

- "print my cover letter" (it tells you the pages and asks first)
- "how much ink is left"
- "is the printer out of paper"
- "set up the printer"
- "scan this to Documents"
- "stop printing"

**If something's wrong**

- **Out of paper, jammed, cover open:** the printer's row says so in plain
  words, and a voice print that fails is said once in the room that
  asked. Fix it at the printer; the job carries on.
- **"level not reported":** some printers don't share their ink or toner
  levels. Check the printer's own screen.
- **"isn't answering":** the printer is off or asleep, or it moved to a
  different network. Press **Look again**.
- **"security certificate changed":** usually a printer update or reset.
  An admin checks it, then presses **Trust the new certificate**; others
  see "sign in as an admin to trust it".
- Each job is limited to 100 pages and 20 copies, and the house to 500
  sheets a day; an admin can change the limits. The list of recent jobs
  is kept 90 days, and an admin can clear it.

### Calendar

The household's events. [Calendar](#calendar) → **new event**; switch
between **month**, **week** and **day**.

Domovoi reads this calendar out loud, with no internet needed. Add and
change events here: by voice it only reads.

**Try saying**

- "what's on my calendar today"
- "what's next on my calendar"
- "do I have anything on Friday"

---

## Your phone

### Get the app and connect

The Domovoi app runs on Android 8.0 or newer, older 32-bit phones
included. It comes from your own server; no store is needed.

**How to**

1. On the dashboard, [Settings → Rooms & devices → Phones & browsers →
   Get the Domovoi app](#settings/phones-and-browsers) shows a QR code. If
   it says the app isn't on the server yet, ask whoever set up Domovoi to
   press **Download the app to this server**.
2. Scan the code with the phone's camera and install. Android asks you to
   allow installs from your browser once.
3. Open Domovoi. Tap **connect** at the top, pick your server (it scans
   your Wi-Fi), and press **trust this server**. Can't see it? Use **add
   manually** with the dashboard's address.
4. **pair this phone**: type the household token. An admin finds it in
   [Settings → Rooms & devices → Phones & browsers → Household
   token](#settings/phones-and-browsers).

**App:** switch or forget a server from the server name at the top, or in
[Settings → Rooms & devices → Phones & browsers](#app/connection).

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

### Chat on the phone

**Chat** holds all your chats with Domovoi in one list. Each one is marked
with where it runs:

- **on home server**: answered by your home server, and shared with the
  household. See [Chat](#node:chat).
- **on this phone**: answered by the phone itself, with no server, and
  kept only on this phone. See [Domovoi on this phone](#node:assistant).

A new chat starts where you chatted last. Until you send something, you
can move it to the other place. Away from home, Chat lists this phone's
chats only ("home server chats show when you're home").

**How to**

- Type in **Message Domovoi…** at the bottom to start a new chat. Pinned
  chats come first, then the most recent.
- Long-press a chat (or its **chat actions** button) to **rename**,
  **pin** (pins stay on this phone), **archive**, **share** or **delete**
  it; swipe to archive. Archived chats are under **archived** at the end
  of the list.
- **search chats** finds words in titles and messages, in both kinds of
  chat.
- Long-press a reply to **copy**, **share**, **try again**, see its
  **details**, or **report this reply**.
- A line saying **earlier messages are beyond what Domovoi can keep in
  mind** marks where the model stops seeing the older part of a long chat.
- **talk**: **hold to talk**, release to send, slide away to cancel. Up to
  30 seconds. "my name is …" in the chat mic teaches Domovoi your voice
  from this phone.
- **Attaching.** In a home-server chat, the paperclip offers **a
  picture** (up to 4) and **a document**. HEIC photos are turned into
  JPEG on the phone first, without their location (Android 9 or newer).
  In a phone chat, the **+** left of the message box offers **a
  document**, and on phones that can, **take a photo** and **choose a
  photo**.
- Chat can write drafts for you. Read them before you send them; it can
  get details wrong.
- More: [Ask about a document](#node:ask-document), [Summaries of long
  documents](#node:long-documents), [Write a draft](#node:drafts),
  [Letters](#node:letters), [Photos in a phone chat](#node:phone-pictures).

[More detail](docs/FAQ.md#can-i-send-pictures-in-chat)

### Domovoi on this phone *(Preview)*

A chat **on this phone** runs on the phone itself: type or talk, in
airplane mode too. It needs a 64-bit phone with 8 GB of memory or more.

**How to**

- **Set it up:** **Chat** → **new chat** → **on this phone** → **Set up
  Domovoi on this phone** → **download** (the button shows the size,
  about 2.5 GB). It waits for Wi-Fi unless you **allow mobile data**. To
  talk to it, it also downloads its speech files once.
- **What it knows, at once:** what's playing and the sleep timer; the
  battery and whether it's charging; free space and memory; Wi-Fi or
  mobile data; the next alarm; Do Not Disturb and the ringer; which phone
  and Android this is, and which model is answering; connected
  headphones; and the songs, videos and saved books on the phone. "who am
  I" answers with the person set in **This phone belongs to**.
- **With your permission**, asked the first time, with the reason: the
  phone's own calendar ("what's on my calendar today"), roughly where you
  are ("where am I"), and the Wi-Fi network's name, which needs precise
  location. Say no and it says it can't, and doesn't ask again.
- **It does things on the phone:** see [Doing things from a phone
  chat](#node:phone-actions). **Photos:** see [Photos in a phone
  chat](#node:phone-pictures).
- **Looking things up:** it asks first, like a home-server chat. See
  [Look things up](#node:lookups).
- **Help:** "how do I …?" answers from the app's own copy of this manual,
  offline.
- **model settings**, in a phone chat: where it runs (**cpu** or
  **gpu**), **read typed replies aloud too**, **Looking things up**,
  **export all phone chats** (Markdown and JSON, saved where you pick),
  **clear all conversations**, and **delete model**.
- To report a bad reply, long-press it → **report this reply**. You see
  exactly what the report holds, and you send it yourself. A reply the
  phone answered from its own data can't be reported.

**What it won't do**

- **House things.** A phone chat answers about the phone only. It never
  reads the household calendar, the house's timers or the rooms, and it
  doesn't play music in a room. Ask, and it says "Those are in your home
  server chats." Your home-server chats do them.
- **Mix the two kinds of chat.** Phone chats stay on the phone; they're
  never copied to the home server. Anything else that leaves the phone,
  you choose each time: a lookup you allow (through your home server, when
  you're at home), or a document, a letter photo or a draft you send to
  the home server.
- See timers already in the Clock app, or read screenshots. Use a
  home-server chat for screenshots.

[More detail](android/README.md#on-device-assistant)

### Doing things from a phone chat

A phone chat can do some things on the phone. It acts only on phrases it
knows, never on the model's guess, and it asks before it sets an alarm.

**Timers and alarms**, in the phone's Clock app:

**In a phone chat, type or say**

- "set a timer for 10 minutes for the pasta"
- "wake me at 6:30"
- "set an alarm for quarter to seven every weekday"
- "show my timers", or "show my alarms" (opens the Clock app)

- A card asks first: **set timer** or **set alarm**, or **cancel**. In a
  spoken chat, say yes; any "no", "wait" or "cancel" stops it. Then the
  reply says "Sent to the Clock app", with **open Clock**. (The Clock app
  doesn't tell Domovoi it took it.)
- "wake me a bit before seven" asks "How about 6:45 AM?". A time with no
  am or pm is the next one to come. Check the card before you tap.

**Music, books and videos on the phone**

- "play some jazz", "shuffle my music", "pause", "resume", "next", "go
  back", "stop", "set a sleep timer for 20 minutes" and "continue my book"
  happen at once.
- **undo** shows under the reply for 10 seconds, and saying or typing
  "undo" works for a minute. It puts back the song, the queue and the
  place. Undo is for the last action only. It leaves the player alone
  after a timer or an alarm, after you changed the music yourself, or
  while the phone plays to a room.
- "play …" plays only what's on the phone. A video saved on the phone
  ("play the beach video") asks first, then plays.
- There's no volume action: use the phone's buttons.

**Calls, messages and more**

- "text Sam", "call Mom", "email Alex", "directions to the station", "add
  lunch tomorrow at noon to my calendar", "open example.org" and "share
  this" show a card. A tap opens the app that does it, and you finish
  there. Domovoi sends nothing itself.
- Calling someone by name asks for your contacts the first time. Say no,
  and **use the picker** lets you choose from your contacts each time
  instead.
- If no app on the phone can do it, it isn't offered.

**What it won't do:** music in a room, or the house's timers ("cancel the
kitchen timer"). It says "Those are in your home server chats." It never
reads your texts, your call log or your notifications.

### Photos in a phone chat

On phones whose graphics chip can run the model's photo reader, a phone
chat can answer about a photo, in airplane mode too.

**How to**

- Press the **+** left of the message box, then **take a photo** (on
  phones with a back camera) or **choose a photo**. One photo per
  question. Type a question, or just send it ("what's in this photo?").
- The camera asks for permission the first time, after a one-line reason.
  Choosing a photo needs no permission.
- The photo is read on the phone and never leaves it. It isn't saved to
  your gallery, and it's gone once Domovoi has answered: the chat keeps a
  **photo (not kept)** mark instead, so a photo question has no **try
  again**. While a photo waits to be sent, the screen is kept out of
  screenshots.
- The words read from the photo are kept beside its answer, so you can ask
  about it afterwards ("who sent it?"). They go when you delete the chat.
  With a document in the chat too, a question goes to whichever you asked
  about last; say "the photo" or "the document" to choose.
- A photo of a letter or a bill: see [Letters](#node:letters).
- After an install or an update, the first question takes a few seconds
  longer while the app tries the photo reader once.

**If something's wrong**

- No photo buttons: this phone can't read photos quickly enough (the
  photo reader needs its graphics chip). A home-server chat's paperclip
  (**a picture**) still works.
- Photos turned themselves off: if the app closed while reading a photo,
  photos stay off until the next app update, and **model settings** says
  why. Deleting the model and downloading it again resets this.

### App settings and updates

- [Settings → Rooms & devices → App layout](#settings/app-layout): pick 2
  to 5 tabs for the bottom of the screen. Home always comes first.
- [Settings → Voice → Voices](#app/voice): how this phone speaks.
- The app's Settings has the dashboard's groups and names. What only the
  dashboard can change (greetings, wake words, models, Advanced, …) says
  so there, with **Open on the dashboard**.
- **Updates:** when your server has a newer app, Home shows "Domovoi … is
  available from your server". Tap **get the update**. [Settings → System
  → About & updates](#settings/about) → **App updates** says which version
  you have. A Google Play install updates itself.
- [Settings → System → About & updates](#settings/about) → **Privacy**
  says what the app keeps and what can leave the phone.

[More detail](docs/PRIVACY_POLICY.md)

---

## Rooms and devices

### Your rooms

[live slot: rooms]

Each room is one listening device: a Raspberry Pi, a spare laptop, or a
phone. [Settings → Rooms & devices → Rooms](#settings/rooms) lists them.
Each card shows:

- online or offline;
- what's playing;
- its Wi-Fi signal;
- when the server last heard from it.

**Dashboard:** open a room for its tabs. They are the icons along the
top: **Overview** (the "i"), **Sessions**, **Conversations**, **Recently
played**, **Notes**, **Timers**, **Logs**, and **Settings** (the gear).

**How to** *(admin)*

- Make a room hear the wake word more or less easily, swap its greeting
  for a chime, or make its voice louder: its **Settings** tab, **Wake
  sensitivity**, **Wake acknowledgement**, **Voice loudness boost**.
- A Pi or spare computer acting up: its **Overview** tab, under
  **maintenance**, **Restart satellite**. Updating it: **Upgrade
  satellite**, in the same place.
- A room moved to new hardware: **Reset pairing** (also under
  **maintenance**), then approve the new device.

**App:** [Settings → Rooms & devices → Rooms](#settings/rooms) shows the
same rooms.

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
- [Health](#node:health) lists rooms that are offline, or were set up but
  never connected.

[More detail](docs/TROUBLESHOOTING.md#satellite-wont-connect)

### A Raspberry Pi room

The usual room: a Raspberry Pi with a microphone board and a speaker.
About the price of a smart speaker. [Parts list](docs/SATELLITE_HARDWARE.md)

**How to** *(admin)*

1. Put Raspberry Pi OS Lite (64-bit) on a microSD card, and put the card
   in the server's card reader.
2. [Settings → Rooms & devices → Rooms](#settings/rooms) → **prepare
   satellite media**. Choose **wi-fi setup portal**, then **write to** the
   card's drive, and press **Prepare**.
3. **On the Windows install**, which can't see SD cards: choose
   **download for the card helper**, then write the card with the tray
   icon's **Prepare a satellite card**. It fills in the setup Wi-Fi
   password by itself. You'll still need that password for your phone:
   **show setup details** shows it.
4. Start the Pi with the card. It makes its own Wi-Fi network,
   `Domovoi-Setup-<id>`. Join it from your phone, pick your home Wi-Fi and
   name the room.
5. The Pi says a six-digit code. Under **waiting for approval**, type it
   and press **approve**.

**If something's wrong:** **show setup details** on the card lists the
setup network and its page. [More detail](README.md#3-add-your-first-satellite)

### A spare laptop or PC *(Preview)*

A Windows 10 or 11, or Linux, laptop or PC can be a room. Use one that
stays plugged in, with sleep turned off, and not the computer that runs
Domovoi.

**How to** *(admin)*

1. [Settings → Rooms & devices → Rooms](#settings/rooms) → **add a spare
   device**. Type the room's name, then press **make the code**.
2. **Windows:** on that PC, signed in as the account it should use, press
   **Download the Windows Setup** (or type the short address the card
   shows into that PC's browser). The setup code and your server are in
   the file's name, so there is nothing to type.
3. Open the file. If Windows says **"Windows protected your PC"**, click
   **More info**, then **Run anyway**. The Setup isn't signed by Microsoft
   yet. It comes from your own server, and it checks that it's talking to
   that same server before it downloads anything.
4. **Linux:** press **copy the command** and run it on that computer,
   signed in as the account it should use.
5. The computer shows a six-digit code. Approve it under **waiting for
   approval**.

A tray icon on Windows shows when the microphone is live, and mutes it.

**If something's wrong**

- No **Download the Windows Setup** on the card: a Linux server fetches
  it once from Domovoi's releases when an admin presses **get the Windows
  Setup**. Until then, or instead, open **advanced: the PowerShell command
  instead of the Setup**, press **copy the Windows command**, and paste it
  into PowerShell on the PC.
- Setup says the code is unknown: a code lasts 24 hours, and a server
  restart clears it. Make a new one.
- A processor from before about 2009 is refused.

[More detail](docs/SPARE_DEVICE_SATELLITES.md#windows-laptops-and-pcs)

### An old phone or tablet as a room *(Preview)*

An Android 8 or newer phone on its charger becomes a room ("room mode").
It hears the wake word, answers, plays the room's music, and takes
announcements.

**How to**

1. *(admin, once)* [Settings → Rooms & devices → Rooms](#settings/rooms) →
   **prepare satellite media** → **Refresh caches**. This gives phones the
   wake word.
2. *(admin)* **add a spare device**, name the room, then **make the
   code**. A QR code appears. If the card says the app isn't on the server
   yet, press **Download the app to this server** on that card first.
3. Scan the QR code with the phone's camera. The page installs the
   Domovoi app and opens it as that room.
4. Read the explainer, press **Start room mode**, allow the microphone,
   then press **Become this room**.
5. Approve the six-digit code on the dashboard.

**App:** a phone that already has the app can do this from [Settings →
Rooms & devices → Rooms](#settings/rooms) → **Use this device as a room**,
or from the **Domovoi Room** icon. A phone you also use every day needs a
screen lock, and while it's a room, the rest of the app is locked.

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

- Where: [Settings → Voice → Wake words](#settings/wake-words). The admin
  turns training on once, with **Turn on wake-word training**.
- What it needs: a server with 24 GB of memory, about 22 GB of downloads
  and about 32 GB of disk. A word takes about 20 minutes to train with an
  NVIDIA graphics card, and over an hour on the processor.
- The steps: **Create** the word, **Record** 15 or more clips on a
  satellite in the room itself, press **Train**, then **Push to room**.
- A wake word trained at home is for your household only ([why](docs/LICENSES.md#the-wake-word-trainer)).

**Greetings** *(admin)*

- What a room says when it wakes: [Settings → Voice → Greetings](#settings/greetings).
- A chime or a light instead: the room's **Settings** tab, **Wake
  acknowledgement**.

[More detail](scripts/wake_word/README.md)

### Domovoi's voice

Three voices, all on the server: **Kristin** (default), **Norman** and
**Linda**.

**How to**

- Pick one: [Settings → Voice → Voices](#settings/voices) → **Domovoi's
  voice** → play a sample, **use this voice** *(admin)*.
- A word said wrong? **Say it like**: type the word and how it sounds, the
  stressed part in capitals (Siobhan = shiv-AWN). Press play to hear it
  first.
- **App:** how *this phone* speaks: [Settings → Voice → Voices](#app/voice):
  "Same as the server" or one of the three (about 64 MB, downloaded once).

**Try saying**

- "what voices are there"
- "let me hear Norman's voice"
- "switch to Norman's voice" (changes this room's voice)

[More detail](docs/FAQ.md#which-voice-does-domovoi-speak-with-and-can-i-fix-how-it-says-a-word)

---

## People and privacy

### People and voices

Domovoi learns who's speaking, so memories, news topics and "who am I"
are personal.

**Try saying**

- "my name is Sarah", then "yes"
- "who am I"
- "forget me" (deletes your voice, your memories and what you said; it
  must be your own voice)
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

- Sign in: [Settings → System → Advanced](#settings/advanced), the
  **Admin** card at the top, **sign in**. A reload in that browser keeps
  you signed in for 60 minutes after you signed in; **sign out** ends it
  at once.
- Change those minutes: on the same page, **show the settings**, then
  **Security** → **Stay signed in after a reload**.
- Turn guest music control off: same group, **Guests can change the
  music in a playing room**.
- The app never signs in as admin; it opens the dashboard for that.

**If something's wrong:** forgot the admin password? It can only be reset
on the server itself. Ask whoever set up Domovoi.

[More detail](docs/TROUBLESHOOTING.md#admin-locked-out)

### Devices and house rules

[Settings → Rooms & devices → Phones & browsers](#settings/phones-and-browsers):

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

Everything stays on your server, or on your phone.

| What | Kept |
|---|---|
| What you said to Domovoi by voice, and its replies | 90 days, or what **Keep voice history** says |
| Chats on the home server, with the pictures, documents and letter photos attached | until you delete the chat |
| Every version of a draft in a chat | until you delete the chat |
| A draft or a letter you saved | a file in Documents → From Chat, until you delete it |
| Chats on a phone, with the text read from a photo or a letter | on that phone only, until you delete them |
| A photo in a phone chat | never kept (the words read from it are, with the chat) |
| A document or a letter photo sent with **ask the home server** or **use home server** | nothing kept: it's deleted once it's read, and a summary waits in the server's memory only until the phone takes it |
| Timers and reminders that went off | 7 days |
| Command recordings (off unless an admin turns them on for a room) | 14 days |
| Play history | 90 days |
| The list of recent print jobs | 90 days (an admin can clear it) |
| News stories | 90 days (saved ones always) |
| The server's own log | no words, only how many |

Paired phones and browsers in the house can read the voice history, in
each room's **Conversations** tab.

**How to**

- "forget me", in your own voice: deletes your profile, voice samples,
  memories and everything you said to Domovoi.
- *(admin)* [People](#people) → **Voice history** → **Keep voice
  history**: 30 days, 90 days (the default), 1 year or Forever. Older
  turns are deleted every day. It's also in [Settings → System →
  Advanced](#settings/advanced) → **show the settings** → **Privacy**.
- *(admin)* **Delete all voice history**, on the same page, deletes it all
  at once. People, voice samples, memories, notes, timers and chats stay.
- *(admin)* **forget person** on [People](#people) does what "forget me"
  does, for anyone: it deletes only what that person said.
- "never save my voice": Domovoi stops learning it.
- Delete a chat: the bin icon in [Chat](#chat). Its pictures, documents,
  letters and draft versions go with it.
- *(admin)* turn recordings off in a room's **Overview** (**Stop and
  delete**); the commands kept so far are in [Settings → Privacy → Voice
  history](#settings/voice-history).
- *(admin)* listen to a room's command recordings, mark whether each one
  ended at the right moment, and delete them: [Settings → Privacy → Voice
  history](#settings/voice-history). A room keeps them only while an admin
  has turned them on for it.

An admin can change play history, news and timer history in [Settings →
System → Advanced](#settings/advanced). [More detail](docs/SECURITY_PRIVACY.md#data-at-rest)

### Internet: yes, sometimes, no

Your voice never leaves the house, whatever you answer. The answer only
decides the online extras.

- **Yes, always**: web answers, lyrics lookups, artists' other names,
  podcast downloads, and Chat lookups when you allow them.
- **Sometimes**: the same, without automatic podcast downloads.
- **No, keep everything in the house**: nothing goes out. Online buttons
  show "needs internet", and Chat can't look anything up online. The
  manual and an offline library still answer.

Even with Yes, a chat asks before each lookup, unless an admin turns on
**Always allow internet** in [Settings → Chat & AI →
Search](#settings/search). See [Look things up](#node:lookups).

A phone away from home looks things up itself, after asking. Your home's
answer doesn't reach it there.

**How to:** [Settings → Privacy → Internet](#settings/internet)
*(admin)*. Download what you need before choosing No.

[More detail](docs/INTERNET.md)

---

## Keeping it running

### Updates

[Settings → System → About & updates](#settings/about) shows the release
you run.

**How to** *(admin)*

1. **Check for updates**.
2. **Pull the latest**.
3. **Restart to apply changes**.

A failed update rolls itself back. **updates** chooses the channel:
**stable** (releases only) or **main** (every change, as it lands).
After an update, reload any open dashboard once.

**App:** see [App settings and updates](#node:app-settings).

[More detail](docs/LINUX_HOST.md#release-channels)

### Health

[Settings → System → Health](#settings/health) shows every check on your
Domovoi, green or not. Home's "needs attention" rows are the problems
among them, and each opens its part of Health.

**What it checks**

- **Server:** the admin account, the Domovoi server, the database, speech
  recognition, and disk space *(admin)*.
- **Updates** *(admin)*: the version and channel, the last update, and
  whether a restart is waiting.
- **Rooms:** how many are online, which are offline, rooms set up but not
  connected yet, screens, and rooms waiting for approval *(admin)*.
- **Plugins:** whether each one loaded, and media requests no plugin can
  fill.
- **Internet:** your answer, and whether the connection works *(admin)*.
- **Containers:** whether the containers Domovoi runs look as they
  should.

**How to**

- A problem row says what's wrong in a sentence, and links to the page
  that fixes it ("open rooms").
- "the Domovoi server isn't answering": an admin presses **Restart the
  core** on that row. See [Restarting](#node:restart).
- **Containers:** Domovoi watches the containers it runs. If one goes
  missing, stops, or is replaced from outside Domovoi, it opens an
  incident and shows a banner. A sign of tampering pauses risky changes
  (plugin installs, updates, satellite upgrades, preparing media) until an
  admin reviews it; everything else keeps working.
- *(admin)* **acknowledge** hides the banner. **clear after review** ends
  the incident, once you know why it happened. **behind the scenes** shows
  what is watched, with **check now** and **rebuild the music player
  image**.
- An admin sees every detail. A shared screen, and other devices (as set
  by **Problems on Home** in [Settings → System →
  Advanced](#settings/advanced), under **show the settings**), may see
  only how many things need attention.

**Try saying**

- "how's domovoi doing" (the server's processor, memory and disk)

### Restarting

Some settings apply only after a restart: [Settings → System → About &
updates](#settings/about) → **Restart Domovoi** *(admin)*.

**If every room is offline but the dashboard opens**, the server's core
has stopped answering. It restarts itself within about a minute. If it
doesn't, an admin presses **Restart the core**. It's on [Home](#home), on
[Settings → Rooms & devices → Rooms](#settings/rooms), on [Settings →
System → About & updates](#settings/about), and on the Domovoi server row
of [Settings → System → Health](#settings/health). The app says the core
isn't answering, and reconnects by itself.

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

[Settings → Chat & AI → Models](#settings/models) shows the models in use:

- **Speech-to-text**: hears you.
- **Tool routing**: picks the feature.
- **Q&A**: answers questions.
- **Vision**: reads pictures in chat.

*(admin)* **Install** or **Switch** one. With an NVIDIA card, Qwen2.5-VL
7B reads pictures faster.

*(admin)* **AI chat and questions** at the top turns the AI models off or
on for the whole home. Off keeps speech-to-text and hides the rest of the
model rows. On says how much it will download (about 6.4 GB, or 9.1 GB on
a server with 24 GB of memory or more) before it starts. See [When AI is
off](#node:ai-off).

[More detail](docs/CPU_HOST.md#the-short-version)

### Troubleshooting

- **Something needs attention on Home**: it opens [Health](#node:health).
- **A room doesn't answer**: see [Your rooms](#node:rooms).
- **Every room is offline**: see [Restarting](#node:restart).
- **"Just a moment, I'm waking up my language model"**: normal once after
  a restart.
- **"I'm set up for home commands"**: AI chat and questions are off. See
  [When AI is off](#node:ai-off).
- **The dashboard looks old after an update**: reload the page once.
- **A word is said wrong**: [Say it like](#node:voice).
- **Chat says pictures need the vision model**: [Models](#node:models).
- **Something is greyed with "needs internet"**, or Chat can't look
  something up: [Internet](#node:internet).
- **A draft got a detail wrong**: change it with **edit**. See [Write a
  draft](#node:drafts).
- **Not sure where to look**: ask Domovoi: [Asking Domovoi for
  help](#node:help-desk).
- **App: can't find the server**: is the phone on the same Wi-Fi as the
  server? Use **add manually** with the dashboard's address.

[All troubleshooting](docs/TROUBLESHOOTING.md)
