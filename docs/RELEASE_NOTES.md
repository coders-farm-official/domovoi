# Release notes

Newest first. Only things an operator has to KNOW go here — a change that
needs an action, changes an answer a client depends on, or is invisible in
a way that would otherwise get reported as a bug.

## 2026-10-16 — Android app: "a newer app is available from your server"

A new APK, and a small change to the server's join page (`/join`); no
migration.

* **The app says when its server has a newer release** than the one on
  the phone: one quiet card on Home, "Domovoi 1.0.1 is available from
  your server", with **get the update** (it opens the server's app page in
  the browser, with this phone's APK, 64-bit or 32-bit, first) and
  **dismiss** (hidden until the next release). It asks the household
  server only, never the internet.
* **If the server hasn't downloaded the release yet**, the card says an
  admin can press **Download the app to this server** on the dashboard
  (Settings → Devices). After a server update that changes
  `android/release.json`, that press is still needed, as before.
* **Never for a Google Play install** (Play updates it), a debug build, or
  a copy signed with another key. Settings → About → App updates says
  which, or "up to date".
* **Room-mode phones** show it as a faint line at the foot of the room
  screen (update, after the phone's unlock; or hide).
* **The join page** takes `?abi=arm64-v8a|armeabi-v7a` (that APK first,
  when the server holds it) and `?update=1` (worded as an update).

## 2026-10-15 — Android app: Android 16, report a reply, privacy, licences

Android app only (a new APK); nothing changes on the server.

* **The app targets Android 16 (API 36)**, as Google Play requires of new
  apps, and still runs on Android 8.0 and on 32-bit phones. On Android 16
  back gestures show where they lead (predictive back), now on Android 13
  to 15 too. The room mode screens draw under the status and navigation
  bars like the rest of the app.
* **Phones with 16 KB memory pages** run the app normally (no
  compatibility warning): every native library is 16 KB aligned, and the
  build refuses one that isn't. The room code's QR scanner moved to
  CameraX 1.4.2 for it.
* **Report this reply** (the on-device assistant): long-press a reply.
  The report shows exactly what it holds and takes a note; **save on this
  phone** keeps it (model settings → reports), **email draft to coders
  farm** opens your email app with it addressed to codersfarm@gmail.com
  (sent only when you press send there), **share as text** opens the
  share menu. The app itself sends nothing.
* **The microphone** is asked for with a one-line reason first; after a
  refusal Android won't ask about again, the mic button leads to the app's
  settings instead of doing nothing.
* **Settings → About → Privacy**: what the app keeps and where it goes,
  and a link to the privacy policy (`docs/PRIVACY_POLICY.md` on GitHub).
* **Timers on Android 14 and later** ring exactly once you allow "Alarms
  & reminders": Home's timers card offers it when it is off. Until then
  they can ring a few minutes late. (The app no longer asks for the
  alarm-clock-only permission Google Play restricts.)
* **Settings → About → Open-source licences** now lists every library in
  the app (139 in a debug build), with its licence and notices, made from
  the build itself.
* **Screen readers (TalkBack)** hear the assistant's state as it changes
  (listening, thinking, speaking), can copy or report a reply from the
  actions menu, and find every control labelled, on the assistant and
  room mode screens alike.

## 2026-10-15 — One name, one person: satellites ask before a second you

A server update (core and dashboard; no migration).

* **"My name is Kamron" at a satellite no longer makes a second Kamron
  without asking.** If someone already has that name and the room
  doesn't recognize the voice as them, Domovoi asks "I already know
  someone called Kamron. Is that you?". A "yes" as your very next answer
  (within a minute) adds that clip to Kamron's voice **for that room**,
  so the room knows you from then on: this is how you teach a new room
  your voice. A "no" adds you as a new Kamron right away ("Okay, I've
  added you as a new Kamron. An admin can give one of you a different
  name on the People page."). The phone's chat mic has its own question
  ("Add this phone's voice to …?"), where "no" still adds nothing.
* **A yes is a claim, not a proof.** Whoever answers "yes" adds their
  voice to that person's, as anyone in a room could already enrol a
  second person with the name. An admin sees every voice sample with its
  room and date on the People page and can delete a wrong one.
* **Two people with one name are never guessed between**, at a
  satellite or on the phone: "my name is …", "who am I" and "forget me"
  say so and send you to the People page ("An admin can rename or remove
  one of them on the People page"). "Who am I" also says when your voice
  matches two people, and whether they share a name.
* **Already have two Kamrons?** (the old satellite path made them.) The
  People page marks everyone who shares a name ("same name", with each
  one's number). If they are the same person, forget the extra one and
  say "my name is Kamron" again in any room that then doesn't know the
  voice; if they are different people, rename one.
* **The People page's rename works** (it used to say "not implemented").
  It is admin only and refuses a name someone else already has.
  `GET /api/people` rows carry `namesakes` (how many others share the
  name).

## 2026-10-15 — Licence clean-up: Domovoi's own tag reader and music-player client

Server update; no migration. Domovoi takes permissively licensed
dependencies only, and the last copyleft ones a server installed are gone:

* **mutagen (GPL)**, which read the tags inside audio files, is replaced
  by Domovoi's own reader (`domovoi/media_tags`). The library and
  audiobook indexers, the cover art, the lyrics and the podcast chapters
  read the same answers from the same files: checked on 42 files (18
  encoded and tagged by FFmpeg: MP3, FLAC, Ogg, Opus, M4A, M4B, WMA, WAV,
  AIFF, AAC; 24 taggers' quirks) and on 284 more encoder variants. Where
  the answer changed, it is a fix:
  * **A tagged FLAC, Ogg or Opus audiobook indexes.** It failed with an
    error before, and the book never appeared.
  * **WMA files get their artist and album** in the library, and an M4A
    with only an album artist gets that as its artist.
  * A tag with two artists (ID3v2.4) stores the first, not both joined by
    an invisible character the database refused; an ID3v2.3 tag written
    unsynchronised reads correctly (title, cover, lyrics); tags on a raw
    `.aac` file, or on an `.mp3` with no audio in it, are read.
  * Songs already in the library keep their rows (the indexer only adds
    new files); audiobooks are re-read at their next index.
* **python-mpd2 (LGPL)** is replaced by Domovoi's own client for the
  rooms' music players (`domovoi/clients/mpd_protocol.py`). Nothing
  changes in use. A title or search with a line break in it is now
  refused instead of being sent (it could end the command early).
* **lameenc (LGPL)**, which encoded the satellites' greeting and status
  clips to MP3, is replaced by the server's own `ffmpeg`, which a Linux
  server and the Windows install already have. On a server without
  `ffmpeg` on its PATH no clip is re-rendered, and the satellites keep the
  clips they have.
* **librosa** (with **soxr** and **soundfile**, which carry the LGPL
  libraries libsoxr and libsndfile) and **PyAV** (FFmpeg's LGPL libraries)
  are no longer installed either. Voice ID runs Resemblyzer's encoder
  itself (`domovoi/clients/speaker_encoder.py`) and gives the same voice
  embeddings, so every enrolled voice keeps matching; Whisper is handed the
  audio directly instead of through a temporary file, and transcribes the
  same words; the radio plugin's fingerprinter decodes with `ffmpeg`, and
  finds the same songs. `faster-whisper` and `resemblyzer` are now
  installed without the dependencies they declare
  (`requirements-no-deps.in`); a Linux server gets them from a second
  hash-pinned lock, `requirements-linux-py314-no-deps.lock`, which the
  update installs with the first.
* **Installing by hand** (a Windows PC, or a Linux box without the lock):
  after `pip install -e ".[real-clients,voice-profile]"`, run
  `pip install --no-deps -r requirements-no-deps.in` (it replaces
  `pip install --no-deps resemblyzer`).
* The packages left behind stay in an existing install's Python
  environment, unused, until removed by hand; a fresh install doesn't get
  them.

## 2026-10-14 — Play music in another room by voice

Server only (the app's manual gains the examples in its next build).

* **Name a room and the music plays there**: "play some music in the
  garage", "play Weezer in the office", "put on some jazz in the dining
  room", "in the garage, play jazz", and "stop / pause / skip / turn it up
  in the garage", "what's playing in the garage". The room you asked
  answers ("Playing Weezer in the garage, shuffled."); the room you named
  plays. Before, "play some music in the garage" searched the library for
  "some music in the garage" and played it in the room that asked. The
  language model's music tool takes a room too, for other phrasings.
* **Rooms are matched by their id and their label** in Settings →
  Satellites ("the kids room", "Kamron's office"), room-mode phones and
  laptop satellites included, never by a near miss. "In here" is the room
  you're in. A room the house doesn't have is answered with the ones it
  has. "Everywhere": stop and pause act on every room that is playing; a
  play is one room at a time.
* **A song is still a song**: "play in the garage" plays the song "In the
  Garage". When a request reads both ways ("play dancing in the garage"
  with a song "Dancing in the Garage" and an artist "Dancing"), Domovoi
  asks; a yes plays the song where you are, a no plays the other in the
  garage.
* **What it won't do**: start music in a room whose speaker is offline (it
  says so); play from `/v1/intent`, which has no satellite to reach; let a
  satellite that connected without a pairing token start, stop or change
  the volume in another room (it may pause, resume, skip or ask what's
  playing there while that room is playing, as a guest may). A room whose
  music player isn't running is started on request, under its own room
  id. Radio stations still play only where they're asked.
* **Podcasts and audiobooks too**: "play the latest episode of The Daily
  in the garage", "play the audiobook Dune in the office", "resume the
  podcast in the kitchen", "play my audiobook in the office" — with the
  same rooms, answers and rules, picking up where that room left off for
  you. "Play my audiobook" on its own now resumes your book (it used to
  search the music library for "my audiobook").
* **A pause before the room name** ("stop the music … in the garage")
  still acts in the room that asked, as it did before: stop, pause and
  skip stay instant. To stop, pause or skip in another room, say the room
  first: "in the garage, stop the music".

## 2026-10-14 — A reload no longer signs the admin out

Server update (core, dashboard and a database migration, V028); the
update applies the migration.

* **Reloading the dashboard keeps you signed in for an hour.** For 60
  minutes after you enter the admin password in a browser, a reload, or
  a new tab in that browser, is fully signed in: no password prompt, and
  saving settings, restarting, plugins and the rest all work as before
  the reload. The hour counts from when you signed in, not from when you
  last did something. After it, a reload is **view only** as before, and
  the first change asks for the password.
* **Only that browser, only that sign-in.** Another phone or computer
  still needs the password, and **sign out** ends it at once (so do
  revoking the session and changing the password from another device).
  On a computer other people use, sign out when you're done.
* **The Admin card says until when** ("keeps you signed in until 14:32"),
  once, with no countdown; the sidebar says **Admin** rather than
  **Admin (view only)** while it lasts.
* **Settings → Configuration → Security → Stay signed in after a reload**
  sets the minutes (0 to 1440); **0** is the old behaviour, where every
  reload is view only.
* **Sign in once after updating.** The dashboard's sign-in cookie is now a
  secret of its own rather than the admin token itself (a browser sends a
  cookie to every port of the server, so other services there used to see
  the token). A browser signed in before the update therefore shows
  **signed out** after its next reload; sign in once and it is back, with
  the hour. A tab you haven't reloaded stays signed in.
* **The household token and the command recordings need the sign-in in
  the tab.** A tab reloaded more than the hour after you signed in (view
  only) no longer shows them; it says "sign in again" there instead. Inside
  the hour a reload is signed in and shows them as before.
* **A sign-out that didn't reach the server says so** ("Not signed out —
  the Domovoi server did not answer … try again") instead of showing
  signed out while the server still had you signed in.
* **API:** `POST /api/auth/resume` (new), and `GET /api/auth/status`,
  `POST /api/auth/login` and `/setup` now also answer
  `reload_window_min`, `resume_until` and `resume_in_sec`
  ([API_REFERENCE.md](API_REFERENCE.md) §3.1,
  [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#staying-signed-in-across-a-reload)).

## 2026-10-13 — Wake-word training: turn it on in Settings, train on the GPU

Server and dashboard update; no migration. A Linux server or the Windows
install with an NVIDIA GPU also gets the NVIDIA Container Toolkit at its
next update. What changes:

* **Training is switched on from the dashboard.** Settings → Wake Words has
  a **Wake-word training** card (admin). Off, it lists what training needs
  against this server: about 32 GB of disk the first time, a download of
  about 22 GB, once, a server with 24 GB of memory, and the time per
  word (about 20 minutes on an NVIDIA GPU, about 75 on 8 CPU cores). It
  refuses, saying why, when the internet answer is **No**, the disk or
  memory is short, or Docker isn't running. **Turn on wake-word training**
  builds the trainer image, checks the GPU, downloads the training data and
  switches training on, in the background, with progress and errors that
  stay; no shell, no restart. Before, training had to be set up by hand
  (`WAKE_WORD_TRAINER_ENABLED`, `WAKE_WORD_TRAIN_COMMAND`), which the
  Windows install couldn't do at all. **Train** is greyed until training is
  on.
* **"Hey Domovoi" trains.** A phrase with a word openWakeWord's dictionary
  lacks ("domovoi", "domo") failed every run: the model upstream downloads
  for such words is no longer served. The core now sends the phrase's
  pronunciations from Domovoi's own phonemizer, the "say it like" list
  first (Domovoi = DOH-moe-voy); espeak-ng inside the trainer image covers
  only a word it sent nothing for. Training again on a new set of clips now
  uses them (it reused the first run's features), and under WSL a training
  lands on the card it picked (it took the first card whatever it chose).
* **A training never outlives its run.** A time limit, a cancel or a core
  shutdown left the training container running, and the next try started a
  second one. The container is now named and labelled, a leftover is
  removed before each run, the wrapper removes its container at its
  deadline and when told to stop, and the core sweeps once after each
  start. A word cut off by a restart stays queued and trains again
  afterwards. Each word's whole output is kept in
  `~/.domovoi/wake_trainer/logs/<slug>.log`. A word that is training has a
  **Stop training** button (it goes back to *recording* with its clips).
* **No copy of a recording is left behind.** The copies of the recorded
  clips a run makes in the trainer's cache volume go however the run ends,
  stopped and timed out included (the container gets a stop signal and a
  grace period first); a run killed outright is scrubbed by the next stop,
  run or start, and deleting a wake word removes its training files.
  **Turn off and free the space** removes the trainer and its data, after
  showing their size.
* **Voice keeps priority while a word trains.** The container is capped
  (all but a quarter of the CPUs, a low CPU weight, 12 GB of memory with no
  swap) and lowers its own priority; on a GPU server it takes the least
  busy card, away from the chat model. A CPU-only server asks before
  training is turned on and before each **Train**, with the estimate and
  "voice answers more slowly".
* **New settings** in Settings → Configuration → Wake words (advanced):
  training on or off, the **Training time limit** (a fixed hour before; now
  sized to the server unless set: an hour on a GPU, at least two on the
  CPU), synthetic clips, validation clips, steps, the share of recorded
  clips, CPUs and memory. Every `WAKE_TRAINER_*` line in `domovoi/.env`
  now reaches the trainer; the systemd drop-in the docs used to ask for is
  no longer needed. The command, the image and the docker binary stay out
  of the dashboard on purpose: they name what runs as the service user.
* **The trainer image is pinned:** the base image by digest, openWakeWord
  and piper-sample-generator by commit, every Python package by version
  (`scripts/wake_word/docker/constraints.txt`), the synthetic voice and the
  feature models by SHA-256. The training data is pinned too: the two
  Hugging Face datasets at fixed commits, the feature files and the
  background zips by SHA-256 (a cache filled before checks once, about a
  minute).
* **Set up for the GPU, a training needs the GPU:** when Docker can't reach
  it any more, the word fails saying so instead of training for over an
  hour on the CPU under the GPU's time limit.
* **A setting can't hold a newline any more** (Settings → Configuration, and
  any `.env` write): a line-based reader saw the text after it as a setting
  of its own. The update unit reads `domovoi/.env` the way the core does.
* **The NVIDIA Container Toolkit** on a Linux server or the Windows install,
  only where an NVIDIA GPU is visible (`scripts/linux/gpu-runtime.sh`):
  NVIDIA's apt repository with its key checked against a pinned
  fingerprint, the toolkit at 1.20.1-1 (not held), the `nvidia` runtime in
  Docker's configuration and a Docker restart; never a driver. A fresh
  install runs it as `provision.sh`'s new `gpu-runtime` step, an existing
  one at its next update or **Restart Domovoi**, while core and web are
  stopped (Postgres and the rooms' players restart once). A try that fails
  waits a day before the next (no `apt-get update` and Docker restart at
  every Restart), unless the admin presses **Turn on** or **Check the GPU
  and trainer again** first. Under **No** the update downloads nothing, and
  answering **No** stops a download or rebuild already running; `DOMOVOI_MANAGE_GPU_RUNTIME=0` in
  `/etc/default/domovoi-update` leaves it alone. The update unit also
  rebuilds the trainer image after an update that changes it, only where a
  household built it (`DOMOVOI_MANAGE_WAKE_TRAINER=0` leaves it alone).
  docs/LINUX_HOST.md, "Wake-word training".
* **Licence:** a model trained at home is for that household. It can't be
  shipped as a Domovoi default or shared, because the training data is
  non-commercial (`scripts/wake_word/README.md`, section 5).
* API: `GET /v1/admin/wake/trainer` and `GET /v1/admin/wake/trainer/footprint`
  (admin reads), `POST /v1/admin/wake/trainer/enable` and
  `POST /v1/admin/wake/trainer/disable` (security tier: Bearer only, 501
  before setup, 409 with the reason), `POST /v1/admin/wake/train/stop` and
  `POST /v1/admin/wake/forget` (admin), and the dashboard's
  `/api/wake-words/trainer`, `/trainer/footprint`, `/trainer/enable`,
  `/trainer/disable` and `/{id}/train/stop` (docs/API_REFERENCE.md).

**What to do:** on a server with an NVIDIA GPU, update first (**Pull the
latest**, then **Restart to apply changes**), so Docker can reach the card.
Then, as the admin, Settings → Wake Words → **Turn on wake-word training**,
confirm, and wait: about 13 minutes on a fast line for the build and the
data download (you can leave the page). Create a wake word, record at least 15 clips on a
satellite (hundreds for an XVF3800), **Train** (about 20 minutes on the
GPU), and push it to a room. On the Windows install Domovoi can't see the
system drive's free space: the card asks you to confirm that C: has at
least 40 GB free (docs/WINDOWS_HOST.md, "Training wake words"); until the
installer is re-pinned past this release, a new Windows install also gets
the NVIDIA Container Toolkit at its first update, not during Setup.
A trainer set up by hand keeps working: its time limit is now two hours
unless you set one.

## 2026-10-13 — Android: a spare phone or tablet as a room (room mode)

Server and Android update; no migration, no satellite upgrade.

* **An old phone or tablet can be a room.** On its charger, it listens for
  the wake word, answers, plays the room's music, takes announcements and
  (on phones with an echo canceller) drop-ins, like a Pi satellite. Open
  **Domovoi Room** (a second icon the app adds) or Settings → Satellites →
  **Use this device as a room**, read the explainer, allow the
  microphone, scan or paste the room code from the dashboard, and approve
  the six-digit code the phone shows. Guide:
  [SPARE_DEVICE_SATELLITES.md](SPARE_DEVICE_SATELLITES.md#phones-and-tablets-room-mode).
* **Before the first phone room: refresh the satellite media caches once**
  (dashboard, Settings → Satellites → prepare satellite media → Refresh
  caches; needs the internet). The phone runs the same openWakeWord models
  as a Pi and fetches them from the server's new signed `/v1/wake-base`
  channel, which serves only what that cache holds. Without it the room
  connects but says the server has no wake-word models yet.
* **A room phone holds only its room's own key** and the server's pinned
  fingerprint, never the household pairing. On a phone that is also a
  household device, the normal app is locked while it is a room, the
  household's timers, live updates and downloads stop on it, and leaving
  takes the phone's PIN, pattern or fingerprint: such a phone **needs a
  screen lock to be a room**. Leaving offers to keep the room or forget it
  ([SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#a-room-phone-android-room-mode)).
* **A room's server must be on the home network** (a private address, or a
  `.local`, `.home.arpa`, `.lan` or `.internal` name), https or not: a room
  code naming anything else is refused.
* **It works on 32-bit phones and from Android 8.0** (the armeabi-v7a APK
  too): room mode uses none of the on-device assistant's libraries.
* **What Android asks of it:** a notification and the green mic dot for as
  long as it listens; after a restart, one tap on "tap to resume"; the
  microphone privacy toggle makes it deaf, and the screen and the
  dashboard then say **mic blocked**.
* **The app's other microphone users take turns:** a phone drop-in call,
  the chat mic and the on-device assistant now say "the microphone is in
  use by …" instead of being silenced by Android when another one has it.
* Building: CameraX and ZXing's core (Apache-2.0) join the app for the
  room code's scanner. The dashboard side of "add a spare device" (the
  room code and its QR) is in the Linux spare-device notes below.

## 2026-10-13 — A spare Windows laptop or PC can be a room

Server, dashboard and satellite update; no migration. Pis and Linux laptops
are unchanged.

* **Settings → Satellites → add a spare device** now gives a **one-line
  command for Windows PowerShell** too. Paste it on a Windows 10/11 PC
  (64-bit), signed in as the person whose account it will run in, in a
  normal (not administrator) window. It installs **for that account only,
  with no administrator rights**, into `%LOCALAPPDATA%\Domovoi\Satellite`,
  starts at sign-in and **stops at sign-out**. Guide:
  [SPARE_DEVICE_SATELLITES.md](SPARE_DEVICE_SATELLITES.md#windows-laptops-and-pcs).
* **Unsigned for now**: the satellite's code carries no signature (Python
  itself is signed by its foundation). Antivirus may warn about a command
  that downloads and starts programs; the card and the guide say so.
* **The same trust model as Linux**: the bundle runs only if its SHA-256 is
  the quoted one, the server proves its fingerprint before anything is
  written, and the Python and every library are pinned by SHA-256 in
  `satellite/windows-runtime.json`. The server can fetch them once (**get
  the Windows files** on the card, ~96 MB from python.org and PyPI, each
  file checked) and serve them to laptops at
  `/v1/satellite-windows/files/…`; otherwise a laptop downloads the same
  pinned files itself.
* **Never on the PC that runs the server**: the installer refuses there.
  "Use this PC as a room" will come with the server's own tray.
* **A tray icon** (filled red while audio leaves for the server, mute, the
  approval code, Allow / Not now for "ask me" with every change listed),
  power questions (plugged-in sleep and lid, each default No, undone on
  uninstall), and **Installed apps → Uninstall**.
* A Windows satellite looks for a server that moved by its name and
  address only, never by sweeping the network (new `[pc] server_search`:
  `sweep`, `names`, `address`).
* The satellite code channel carries `.ps1` files now (the installer); a
  Pi downloads none of them.
* A Windows satellite **refuses to start** on a `config.toml` value it
  won't use (a device name that is not "default", a number or a plain
  name; an unknown setting), like a Linux laptop: the tray shows *Domovoi
  satellite didn't start* and the line to fix, and it stays stopped until
  **Start satellite**. It sends no `settings_ignored` (that is a Pi's).
* Fixed: retiring a room and then adding a room with the same name in the
  same server session left the new room's music player without its record,
  so the next retire left its container behind.
* Every satellite: a sound or wake-word list can write only inside its own
  cache, and only sound files / wake-word models; a path with a drive
  letter, a colon, a backslash or one leading out through a link is
  refused (it could reach a Windows PC's Startup folder).
* A Windows satellite whose control pipe is already taken by another
  program stops and says why in the tray, rather than listening with a
  tray that can't see or mute it.
* **A Windows satellite brings Microsoft's Visual C++ runtime** (14.44;
  the wake word's onnxruntime needs it and doesn't carry it): Microsoft's
  own copies, from the `msvc-runtime` wheel, pinned in the lock and placed
  beside the satellite's Python — no administrator, nothing for the rest of
  the PC. They are updated with Domovoi's lock, not by Windows Update. The
  installer also says when Smart App Control is on or in evaluation: it may
  block parts of the satellite that aren't signed yet.

## 2026-10-13 — A spare Linux laptop or mini PC can be a room

Server, dashboard and satellite update; no migration. Pis are unchanged.

* **Settings → Satellites → add a spare device** (admin): name the room and
  get a **one-line install command** for a Debian or Ubuntu laptop or mini
  PC, and a **QR code** of the same enrolment code for a phone's room mode.
  The code is not a password: the device still waits for its approval code
  in the dashboard, like a Pi. Guide:
  [SPARE_DEVICE_SATELLITES.md](SPARE_DEVICE_SATELLITES.md).
* **The command checks what it runs**: it runs the download only if its
  SHA-256 is the one the dashboard quoted, and the installer makes the
  server prove its fingerprint before it writes anything; the device is
  pinned to that server from then on and won't run unpinned. The command
  itself is only as trustworthy as the dashboard page it is shown on, which
  is plain HTTP on the LAN: before running it, check that it downloads from
  your server's address and that its `fp=` is the fingerprint under
  **Settings → About** on another device. Make a fresh command after
  updating Domovoi (an old one refuses the new download). The server
  address in a code must be on the home network.
* **Python packages from a hash-pinned list** (`satellite/locks/`, one per
  Python version and machine): pip takes only the files whose SHA-256 the
  list names, and builds nothing from source.
* **Uninstall from root's own copy**,
  `bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall` in the
  satellite's account, or, for a dedicated account, from your
  administrator's account with `sudo … --uninstall --account <its account>`.
  The installer and uninstaller fix their PATH and name every tool in full.
  It acts only
  on the paths written into it and on root's own record of what it
  changed, never on anything the satellite's account can write.
  `--purge` removes the satellite's files (root's copy of its purger, under
  the system's Python: never the satellite's own code), and the installer
  refuses an account that already runs a Domovoi server. On a desktop every
  sudo step comes first; sudo then forgets the password before the
  satellite's code runs, in a fresh venv and a session with no terminal.
* **A spare computer whose settings don't load stays stopped** (exit 78)
  instead of restarting every few seconds, and its tray and
  `python -m satellite.client --status` say why.
* **The dashboard refuses a satellite setting the satellite would refuse**
  (an ALSA device name that is not a plain one, text over 120 characters
  or with a line break), with the reason, instead of "sent".
* **The laptop asks you** whether the server may update and restart it on
  its own ("automatic", like a Pi) or must wait for your OK in the tray
  ("ask me"). Either way it gets **no root helpers**: no Wi-Fi re-joins, no
  clock setting, and **plugins that install something as root on a
  satellite stay Pi-only** for now. The dashboard doesn't offer those for
  a laptop.
* **A tray icon** shows when the microphone is live (filled red), mutes it,
  shows the approval code and the waiting updates; plain GNOME gets the
  same controls in a small window from the app menu. Headless boxes use
  `python -m satellite.client --status|--approval-code|--mute|--unmute`.
* **Battery in a room's health block** (a laptop's level, charging, on the
  charger), with a nudge to set a charge limit when it sits full on the
  charger. The installer offers one (and lid/sleep settings), each asked,
  each defaulting to No.
* The core serves the satellite code as one file too
  (`/v1/satellite-code/bundle.tar.gz` and its `bundle.sha256`), open like
  the rest of the satellite channel and held by the container guard's
  freeze.
* Satellite media prep now evaluates `satellite/requirements.txt`'s
  platform markers for the board it prepares (the `onnxruntime==1.20.0`
  pin and `spidev` are ARM-only lines now); a Pi card gets what it got.
* **Every satellite, Pis included: tighter settings from the server.** A
  settings change from the server may name only the settings the
  dashboard's form has, by their exact name (a near-miss such as a trailing
  space is refused, not tidied up), and never the pinned server, its
  address or the device section. An audio device name, from the server or
  in `config.toml`, must be a plain ALSA device (`hw:1,0`,
  `plughw:CARD=Device`, `default`, …): one that makes ALSA run a command
  is refused, and so is the player's use of one. A **Pi** whose stored
  config already names one (no SSH to fix it) starts on its board's
  default for that setting and says so: **"a setting ignored"** on its
  dashboard card and in its health block. A spare computer refuses to
  start instead and says why (it has a terminal). A satellite's sound sync
  now prunes only the clips it put there.

## 2026-10-13 — Android: talk to Domovoi on the phone

New Android build only; nothing changes on the server.

* **The Assistant tab listens**: tap the mic, ask, and Domovoi answers on
  the phone, in text and aloud, with no server and no internet (airplane
  mode included). It stops listening when you stop talking; one tap stops
  it talking. Replies to typed questions stay silent unless "read typed
  replies aloud too" is on in its model settings.
* **It speaks with the phone's voice from Settings → Voice** (Kristin
  unless you picked another, once downloaded there), else the phone's own
  text-to-speech voice.
* **First-time setup now downloads 2.45 GB**: the language model plus the
  speech files (44 MB: Moonshine speech to text and Silero end of speech,
  both MIT). A phone set up for text only gets the speech files offered on
  its first tap of the mic.
* **The microphone opens only on that tap**, one app feature at a time,
  and the audio is never kept: only the words, as the question.

## 2026-10-13 — Android: Domovoi on the phone (text chat, no server needed)

New Android build only; nothing changes on the server.

* **An Assistant tab answers on the phone itself**, with no server and no
  internet once its model is on the phone (airplane mode included). It is
  a tab of local mode, beside Music, Videos and Files; with a server
  connected it is on Home's grid, the drawer and the rail, and can be put
  on the bottom bar from Settings → Phone bar. It always says "on this
  phone": it is separate from Chat, which talks to the server, and the two
  never share a conversation. Text only in this build; voice comes next.
* **First use downloads the model once: 2.41 GB** (Gemma 4 E2B,
  Apache-2.0), shown with the free space it needs before it starts. Wi-Fi
  only unless "allow mobile data" is on; it carries on in the background,
  resumes after a dropped connection or a restart, can be paused or
  cancelled, and is checked against its SHA-256 before use. **Delete
  model** in the assistant's model settings frees the space.
* **Which phones:** the 64-bit app on a phone sold with 8 GB of memory or
  more. Others see why it isn't available, and no download button; the
  32-bit app has no assistant at all, as before. The model runs on the CPU;
  the GPU is an opt-in in the model settings.
* **Conversations are kept only on the phone** (outside backups, never sent
  to a server), with delete and "clear all conversations". A shared screen
  (the kitchen tablet) doesn't offer the assistant, as it doesn't offer
  Chat.

## 2026-10-12 — Android: 32-bit phones, and Android 8 fixes

New Android build only; nothing changes on the server.

* **The app installs on 32-bit phones again** (old and budget phones,
  Android Go), from Android 8.0 up. Since the on-device assistant arrived
  it installed on 64-bit phones only (`INSTALL_FAILED_NO_MATCHING_ABIS`).
  A release now attaches one APK per processor type: **arm64-v8a** for
  almost every phone, **armeabi-v7a** for 32-bit ones, x86_64 and x86 for
  emulators. The 32-bit APKs have **no on-device assistant** (its engine
  is 64-bit only): its card is simply not there, and everything else works
  the same. Which APK, and what each Android version gets:
  [ANDROID_COMPATIBILITY.md](ANDROID_COMPATIBILITY.md).
* **Android 8: the keyboard no longer opens by itself** over Music's
  filter or Home's announce box at every start.
* **Problem reports on Android 8 to 10 say what they record** (crashes and
  freezes); "not responding" closes are recorded from Android 11.
* Building: `./gradlew assembleDebug` still makes one APK for a phone and
  the emulator; `-Pdomovoi.apkPerAbi` makes one per ABI, and an App Bundle
  carries all four (android/README.md, "ABIs and APKs").

## 2026-10-12 — Kristin is the house voice; the Microsoft Edge voices are gone

Server, dashboard and Android update; no migration. What changes:

* **The default voice is now Kristin** (it was Norman). A server still on
  Norman moves to Kristin once, on its first start after the update, and
  only once Kristin is on disk: it downloads her first (a box answered
  **No** to the internet question keeps Norman until her files are there;
  the provisioning already fetched all three voices). Nothing recorded
  whether Norman was picked on purpose, so every server on Norman moves:
  **pick Norman again in Settings → Voices if you prefer him**, and he
  stays. A room that chose its own voice keeps it. The phone app speaks
  Kristin when it knows no server voice.
* **The Microsoft Edge online voices are dropped.** Microsoft's service had
  started refusing them, every reply they spoke went to Microsoft, and
  their library (edge-tts) is LGPL. The "Add a cloud voice" card is gone,
  every registered cloud voice is removed on the update, and a room or a
  default that was set to one speaks in the house voice, Kristin. The
  speech chain is local only: Piper, then the system voice. An old
  `TTS_ENGINE=edge` in `domovoi/.env` reads as `piper`, and a
  `TTS_EDGE_VOICE` line is ignored.
* **The update uninstalls edge-tts** (LGPL-3.0) from the server's Python
  environment, after a successful re-sync and never during a rollback, the
  way it removed piper-tts; its MP3 decoder `miniaudio` and their
  dependencies (aiohttp and friends) leave the Linux lock too. This removes
  an LGPL package from Domovoi's dependencies.

## 2026-10-12 — Norman says short sentences right; Settings → Voices play buttons say why they can't play

Server, dashboard and Android update; no migration, nothing to do but
update (and update the app).

* **Norman no longer babbles short sentences.** Norman (the default until
  this batch) ran away on a short sentence said on its own: a "say it like" preview
  ("Kamron.", "Domovoi."), a one-word reply ("Okay.", "Done."), the
  "Hi, I'm Norman." of its own sample, came out as two to six seconds of
  sound in no language. The phonemes were right; Kristin and Linda say the
  same ones correctly. A voice is now checked once when it first says a
  short sentence, and one that runs away (Norman) says its short sentences
  after a lead-in sentence that is cut off again, so only the sentence is
  heard. Longer sentences are said exactly as before. The phone does the
  same, and Norman's built-in sample clip is re-made.
* **"Say it like" reads respellings better.** A syllable no dictionary
  knows goes by the usual respelling key (uh as in but, ay as in say, zh as
  in measure: `SEER-shuh` was "SEER-shoo"), a light last "ren", "sel" or
  "dem" is said like the end of "Cameron" (`Kamron = KAM-ren`), and a
  respelling that runs together into a dictionary word with the same
  stress (`CAM-er-on`) is read as that word. The preview toast says what
  was previewed in words ("Kamron → KAM-ren"); the voice's phonemes are on
  the play button's hover. An entry saved before may sound slightly
  different: `NOO-yen` now ends like "Cameron" too; write `noo-YEN` for a
  full "yen".
* **Settings → Voices play buttons say why they can't play**, instead of
  "sample failed: 502 Bad Gateway". A registered voice's sample is made by
  the server on the household device tier, and the page now passes the
  server's own sentence through ("Cori can't speak right now: ..."). (That
  day Microsoft's service was refusing the Edge voices with HTTP 403; they
  are gone now, see the entry above.) A
  catalogue voice an install kept (Cori) downloads on its first sample,
  as on its first reply. The upload form's file pickers and fields are
  the dashboard's own controls now.

## 2026-10-12 (later) — The router learns a satellite's new name after one reboot

Server, dashboard, satellite and Android update; no migration. After the
Wi-Fi fixes below, each satellite renamed itself `domovoi-<room>` at the
first reboot. But it did so after it had already asked the router for its
address as `raspberrypi`, so the router showed the new name only after a
**second** reboot. On 2026-10-12, dining-room had been rebooted twice and
showed its name; office, garage and basement had been rebooted once and
did not. What changes:

* **The name is set before the Wi-Fi joins.** A new unit,
  `domovoi-hostname.service`, sets the host name on every boot before
  NetworkManager starts. It runs a small root-owned helper, not the
  satellite's own code. A new card gets it from media prep. An older card
  installs it at its next boot after the satellite upgrade, once:
  `systemctl disable` or `mask` turns it off for good. A hand-built
  satellite gets it only if you ask for it (PROVISIONING.md §8.4). That same boot,
  having renamed the unit, asks the router for its address once more under
  the new name, so **one reboot is enough**. A new card is named before
  setup even joins the house Wi-Fi.
* **The Health card says what the boot step did.** It has a new **boot
  network step** line: *applied* (what changed), *nothing to change*,
  *skipped* (and why, e.g. a host name you chose yourself), *failed* (the
  error), *not run* (and since when), *running*, or *not installed*. It
  also shows the satellite's current **host name**, the one the router
  should show. The Android app's satellite overview shows the same. A
  satellite not yet upgraded shows neither.
* **"Reboot needed" comes back once.** The boot step's version moved
  from 1 to 2, so every upgraded satellite asks for one more reboot.
  That reboot installs the unit that sets the name before the network
  starts.

**What to do:** update the server, upgrade each satellite (Settings →
Satellites → the room → **Upgrade satellite**), then unplug each one for a
few seconds once it says **reboot needed**. A satellite already renamed by
an earlier reboot needs only that one. Check the router's client list
afterwards, or the room's **boot network step** and **host name**.

## 2026-10-12 — Every tab has its own address

A dashboard-only change (reload open tabs once).

* **The address bar follows the tabs.** Settings' tabs are
  `#settings/about`, `greetings`, `voices`, `wake-words`, `recordings`,
  `devices`, `phone-bar`, `satellites`, `models`, `internet` and
  `configuration`; Music's are `#music/library`, `player`, `queue`,
  `playlists`, `stats` and `jobs`; Calendar's views are `#calendar/month`,
  `week` and `day`. Clicking a tab changes the address, Back goes to the
  tab before, and a bookmark or link opens its tab. Old addresses keep
  working: `#settings` is Settings on Greetings, `#satellites` is
  `#settings/satellites`, and a tab a page doesn't have opens its first
  one.
* **`#settings/configuration/setup`** is where the Windows installer's
  "Open Domovoi" goes. On a box nobody has claimed, Settings →
  Configuration now opens the **first-run admin setup** by itself (at
  this address and at `#settings/configuration`), with the setup code
  field ready for a paste; on a claimed box it only says the admin
  account is already set up.
* **Where the setup code is** is now said for the kind of server: on a
  Windows install, the installer's last page (run the installer again for
  a fresh code); on a Linux server, `~/.domovoi/setup-code.txt`.

## 2026-10-12 — Satellites stay on the Wi-Fi (upgrade each satellite, then reboot it once)

Server, dashboard, satellite and Android update; no migration. The router
kept counting the satellites as new devices and they kept dropping off the
server. What changes:

* **A server restart no longer knocks the satellites off the Wi-Fi.** A
  satellite used to read the server's "connection refused" during a restart
  as a dead network, and drop and rejoin the house Wi-Fi (a fresh DHCP
  exchange every time, and again every 5 minutes while the server stayed
  down). It now touches the Wi-Fi only when neither the server's machine
  nor the router answers — plus, for a lossy link, once (at most once an
  hour) after the server has been silent for 10 minutes while the router
  answers.
* **A satellite never falls back onto its own setup hotspot.** Wi-Fi
  recovery brings back the house network's profile by name; the setup
  hotspot's saved profile (`domovoi-setup`) is deleted once setup is over.
  A satellite found hosting its setup network anyway says so (the room's
  Health card: **setup hotspot on**), recovers, and reboots after 15
  minutes if nothing else works.
* **Wi-Fi power saving is off** on the satellites, and the server waits
  20 s instead of 5 s for a satellite's keep-alive answer before dropping
  it (`WS_PING_TIMEOUT_SEC`; a dead connection is still noticed within
  about 30 s).
* **Each satellite is named after its room**: host name `domovoi-<room>`,
  in the router's client list instead of a row of `raspberrypi`, and
  `domovoi-<room>.local` over mDNS. A host name you chose yourself is left
  alone.
* **A satellite finds the server by name first** (`domovoi.local`, or the
  name the server tells it), then at the address that worked last time,
  and sweeps the network only as a last resort; it saves what worked. So a
  server whose router hands it a new address keeps its satellites. Every
  candidate must prove it holds the server key the satellite is paired
  with, so a name cannot hand a satellite to another machine. A DHCP
  reservation on the router is now optional, for the server and the
  satellites alike; never give a satellite a static address on the Pi
  itself.
* **The dashboard shows Wi-Fi by signal strength.** The number on each
  satellite card is the signal at the satellite, in dBm, coloured green at
  -60 dBm or better, amber from -60 to -70, red below -70 (move the
  satellite or the router closer). The Mbit/s rate is shown next to it but
  no longer colours anything: an idle link reads the 1 Mbit/s beacon rate,
  which is why every idle satellite used to look red. A satellite that has
  not been upgraded keeps the old display until it is. The Android app's
  Satellites and Home screens follow the same rule. "How's your wifi?"
  answers with the signal too.

**What to do, in order:**

1. Update the server (Settings → Version → Update) — it serves the new
   satellite code.
2. Upgrade each satellite: Settings → Satellites → the room → **Upgrade
   satellite**. It restarts in a few seconds; from then on it leaves the
   Wi-Fi alone through server restarts, never recovers onto its hotspot,
   finds the server by name, and reports its signal.
3. **Reboot each satellite once** (unplug it for a few seconds and plug it
   back in). The upgrade restarts only the satellite program; the three
   settings that need root (hotspot profile gone, power saving off, host
   name) are applied at boot. Until it has rebooted (or while that step
   reports an error), its Health card and the Android app say **reboot
   needed**, with the reason. What the reboot changed is in the room's retained log
   (`network settings (applied as root at boot): changed: …`).

A newly prepared card has all of this from its first boot; nothing to do.
A hand-built satellite without `domovoi-provisioning.service` applies the
three settings with `sudo ~/satellite-venv/bin/python -m
satellite.net_settings` (satellite/PROVISIONING.md §8.4).

## 2026-10-12 — Your own American-English voices speak again

* **An uploaded Piper voice trained on American English now speaks.**
  Most voices people train (and most `en_US` voices) use espeak's
  `"en-us"` phonemes; since the update below they fell through to the
  robotic system voice. Domovoi's converter now writes that set too, picked
  per voice from its `.onnx.json` (`"espeak": {"voice": "en-us"}`), so such
  a voice sounds like itself again with nothing to do. A voice trained on
  another set (not English) still can't speak and falls through to the
  system voice, but Settings → Voices now says so beside it, and at upload.
* **Play buttons play the voice they name.** A voice not downloaded yet
  plays a short built-in clip of itself (web and phone); a voice with
  neither shows no play button.
* **The phone's own voice pick speaks the chat mic's replies.** With
  Settings → Voice on a voice of its own (not "same as the server") and
  that voice downloaded, the phone speaks each reply itself, with the
  server's "say it like" list, and asks the server for no audio
  (`speak=false` on the voice turn; an older app or server keeps today's
  behaviour).

## 2026-10-12 — A new voice, and words it says the way you tell it

A server update (it adds migration **V027**; the update runs it, and its
dependency change needs the internet once).

* **Domovoi sounds different after this update.** It now speaks with one
  of three public-domain voices: **Norman** (male, the new default),
  **Kristin** or **Linda** (female). A server that spoke with the old
  default (`en_US-lessac-medium`) or a voice of the old catalog built on
  it (amy, ryan, kusal, joe, kathleen, alan, alba) moves to Norman at its
  first start: `TTS_PIPER_VOICE` in `domovoi/.env` is rewritten, the old
  voices leave the Voices list (a room that had picked one speaks in the
  default), their model files are deleted from `~/.domovoi/piper_voices/`
  (nothing outside that folder), and Norman is downloaded (63 MB, checked
  against its pinned SHA-256). The greetings and clips re-render in it. A
  server already set to one of the three keeps it, and so does one set to
  the old catalog's Cori (public domain too, no longer offered); your own
  uploaded voices stay (one trained on American-English "en-us"
  phonemes, such as most `en_US` voices, fell through to the system voice
  until the next entry above: the converter writes that set too now).
* **To pick another:** Settings → Voices → **Domovoi's voice**, with a play
  button for each. The phone app offers the same three, so the house and
  the phone can sound alike.
* **Say it like.** When a word comes out wrong, add it under Settings →
  Voices → **Say it like**: the word and how it sounds in plain letters,
  stressed syllable in capitals (`Siobhan` = `shiv-AWN`); play it before
  you save. Three entries ship and can be changed or removed: Domovoi =
  `DOH-moe-voy`, Domo = `DOH-moe`, Nguyen = `win`. The list is stored in
  the database (V027) and read by the phone (`GET /api/voice`).
* **Why:** the old voice engine, `piper-tts`, is GPL-3.0 and carries
  espeak-ng (GPL-3.0), and the old voices' licences don't allow every use.
  Domovoi now turns text into speech sounds with its own converter
  (`domovoi/phonemizer`: CMUdict, BSD-style; the rest MIT) and runs the
  voice on ONNX Runtime (MIT). The update **uninstalls `piper-tts`** from
  the venv (a step on the Version card says so). If you install by hand,
  `pip uninstall -y piper-tts piper-phonemize` after updating.
* **`espeak-ng` is no longer installed** by the Linux provisioning; it was
  only the robotic last-resort voice. A server that has it keeps it, and
  uses it if the Piper voice is ever missing.
* **The fast lane's `fastlane` extra is gone.** Its recognizer,
  sherpa-onnx, compiles espeak-ng into its PyPI wheels. To keep trying the
  lane, install sherpa-onnx yourself ([CPU_HOST.md](CPU_HOST.md#the-streaming-fast-lane));
  a copy already in your venv stays.
* Text with no Latin letters at all (another script) is answered with
  "Sorry, I can't read that out loud." instead of silence.

## 2026-10-12 — Satellites: ready for spare devices (nothing changes for a Pi)

Server and dashboard update; no migration, no satellite upgrade needed.
Groundwork for running satellites on spare Linux laptops and Android
phones later. **Pi satellites behave exactly as before.**

* **A satellite may now say what it can do.** Its hello can carry a
  `supports` list and a `board` (`linux_pc`, `android_room`). The server
  then sends it only what it listed, and the dashboard offers only those
  actions. A satellite that sends no list (every Pi) gets everything, as
  before. The names and what each one unlocks are in
  docs/uml/satellite-protocol.md ("Declared abilities").
* **Settings → Satellites shows a board badge** ("Linux PC", "Android
  room") on such a device's card. Restart, Upgrade, the Settings tab, the
  live log, announcements and wake-word recording are greyed with a short
  reason when the device didn't offer them. Pi cards look the same as
  before.
* **The health block can show a battery and "mic blocked"** for devices
  that report them. A Pi doesn't.
* **The Settings → Satellites sections have their spacing back.** The
  Broadcast card no longer touches the satellite cards above it.

## 2026-10-12 — Phone voice profiles: "my name is …" from the chat mic

A server update. It adds migration **V026** (the update runs it).

* **Your voice on the phone is kept apart from your voice in the rooms.**
  A phone's mic hears a voice very differently from a satellite's, so a
  person now has phone samples next to their satellite samples.
  Satellites only ever match satellite samples, so nothing said into a
  phone changes how the rooms recognize anyone. The phone matches your
  phone samples when you have any, otherwise your satellite ones as
  before.
* **Saying "my name is …" into the phone no longer makes a second you.**
  If that name is already someone, Domovoi asks "Add this phone's voice
  to …?"; "yes" on your very next press (within a minute) adds it, up to
  three phone samples per person, the oldest dropped
  (`VOICE_PROFILE_PHONE_SAMPLE_CAP`); "no", or saying anything else,
  changes nothing. If two people share the name, it won't guess which one
  you are and says so. A new name is enrolled as before, from the phone's
  sample. The same goes for "this is …" introductions on the phone.
* **Someone enrolled only from a phone is unknown to every satellite**:
  their only samples are phone samples, which satellites never use. If
  they then introduce themselves at a satellite ("my name is …"), the
  satellite enrols a second person with the same name, as it does today
  for any existing name (that duplicate-name gap on satellites is
  unchanged in this release); an admin can delete the extra one on the
  People page.
* **People page:** a person's profile now lists their voice samples in two
  groups, satellite and phone, each with a delete button (admin).
* **The migration** files any sample a phone enrolled before this update
  (stored under the phone's `phone-…` id) as a phone sample, so it stops
  counting towards that person's room voice.

## 2026-10-12 — Chat mic fixes: "Hey Domovoi, …" and "what's playing"

A server update and a new Android build.

* **Saying "Hey Domovoi" into the phone's mic no longer hides the
  request.** A satellite's transcript starts after its wake word; the
  phone's held it ("Hey Domovoit, play Suicide Boys."), which no command
  matched, so the turn went to the language model with the name buried
  in it, and a random song played. The server now takes a leading wake
  phrase off before routing, however speech-to-text spells the name;
  the chat keeps what was said. (Server.)
* **"What's playing" from the phone answered that the playing song was
  paused** — the mic pauses the phone's music while you hold the button,
  and the turn reported it then. It now reports the player as it was
  when you pressed. (App.)
* **Connecting the app to a different Domovoi clears the phone's player.**
  What was queued from the old server (its songs, stations, episodes,
  books) stops and leaves the mini player, and a cast to one of its rooms
  ends on the phone; nothing of it is asked of the new server. Servers are
  told apart by the identity the app pinned, so the same Domovoi at a new
  address, a reconnect, or switching between this phone and the server
  keeps everything playing. Songs saved on the phone stay. (App.)
* Not a bug: when a request picks one song, that song is the phone's
  whole queue, so the mini player shows its play button once the song
  ends. Music playing before a turn pauses while you hold the button,
  comes back when you let go, and dips under the spoken reply.

## 2026-10-12 — Android: an on-device assistant benchmark, and a bigger app

New Android build only; nothing changes on the server.

* **The app is much bigger: about 82 MB built for phones only
  (`./gradlew assembleDebug -Pdomovoi.abis=arm64-v8a`), about 147 MB as
  built by default** (which also carries the emulator's x86_64
  libraries), up from about 25 MB. It now carries the speech-recognition
  and language-model engines the coming on-device assistant needs
  (sherpa-onnx, built here without its text to speech; ONNX Runtime;
  LiteRT-LM), stored uncompressed. The phone's own voice is Android's
  text-to-speech engine.
* **Settings → About has an "On-device assistant benchmark" card**, which
  measures how fast this phone runs the assistant's models. With no server
  paired, the gear on the phone's own Music / Videos / Files screen reaches
  it. Nothing is downloaded until you ask: "download all" fetches the
  models (2.58 GB as the card counts it, about 2.8 billion bytes) from
  their public hosts (never with the household token), on Wi-Fi only
  unless you allow mobile data, in the background with a notification you
  can pause or cancel. The assistant itself is not
  in this build yet.

## 2026-10-12 — Chat: talk to Domovoi from the app's chat screen

New Android build and a server update (it adds migration **V025**; the
update runs it). Update the server first: the new app's mic needs it.

* **A mic in the chat composer.** With nothing typed, the send button is a
  mic. It opens a big hold-to-talk button: press and hold to talk, let go
  to send, slide your finger off the button to cancel; pressing again while
  Domovoi is speaking stops the reply. The thread sits underneath showing
  the latest exchange; scroll down into it for the whole chat, back up past
  its top for the button again. The app asks for the microphone the first
  time.
* **Spoken turns are answered like a satellite's** — the same commands, the
  same follow-ups ("did you mean …?" is answered by your next press, and
  "yes" picks it up) — and both sides are saved into the thread, so voice
  and typed messages scroll together. The reply is spoken on the phone.
* **The phone is its own device.** Music, an artist, an album, "play
  something", playlists, an audiobook or a podcast's latest episode asked
  for by voice play **on the phone**, with its own player (a book or an
  episode picks up where you left off); pause, next, volume and "what's
  playing" act on it. Timers and reminders set from the phone are
  house-wide, as from any room. Intercom ("tell the kitchen dinner's
  ready") works. A satellite's own settings (its Wi-Fi, its voice) are
  politely refused. Not yet from the phone: radio, chapters / skipping /
  speed in a book, "add this song to …" (the phone says it can't yet), and
  "drop in on …" (use a room's drop-in button).
* **Who spoke.** Each spoken message carries a small marker naming who
  Domovoi recognized, in the app and on the dashboard's chat, lighter when
  it isn't sure (and, in the app, when it names someone other than the
  phone's owner, set per server under Settings → Connection → **This
  phone belongs to**). Voice profiles enrolled on a
  satellite may not match a phone's mic as well, so the marker can say
  "voice not recognized" for someone a satellite knows.
* **For operators:** turns are logged under `phone-<device id>` like a
  room. A recording stays in memory for the turn only. Before first-run
  admin setup the phone's mic is refused (`501`), like the drop-in call.
  New endpoints: `POST /v1/phone/voice` (core) and
  `POST /api/chat/threads/{id}/voice` (dashboard); chat messages gain
  `source` and the speaker fields (API_REFERENCE.md §2.1, §3.18).

## 2026-10-12 — Chat: a vision model that suits a processor, and pictures shrunk for it

A server update. To read pictures in chat, install the new default vision
model (below) unless you already picked one.

* **The default vision model is now `qwen3-vl:2b-instruct-q4_K_M`**
  (Qwen3-VL 2B Instruct, Apache-2.0: a 1.9 GB download, ~3.2 GB in
  memory), not `qwen2.5vl:7b`, which takes minutes per picture without a
  GPU. It came out on top of a benchmark of ten vision models on a
  processor alone: it reads screenshots, receipts and charts well, and it
  started answering in about half a minute rather than minutes. **If you never
  picked a vision model**, chat now names this one: install it in
  Settings → Models (its card in the catalog, or pull it by name). If you
  picked one, nothing changes. **On a box with an NVIDIA GPU**, pick
  `qwen2.5vl:7b` there instead (the catalog calls it the GPU pick). Also in
  the catalog now: `qwen3-vl:4b-instruct-q4_K_M` (better answers, ~1.5x
  slower, for a box with 24 GB or more) and `gemma4:e2b-it-q4_K_M`
  (quickest, but misreads small text at the size chat sends). The vision
  cards show each model's licence. A model picked in the Vision row reaches
  the dashboard's chat when the dashboard restarts.
* **Pictures reach the model upright and at most 1280 px on the long
  edge.** A full-size phone photo used to fill the model's whole context,
  and the reply stopped after a sentence. The shrunk copy is kept beside
  the upload (`~/.domovoi/chat_uploads/<token>-1280.jpg` or `.png`) and
  goes with the thread; the thread still shows the picture as it was sent.
* **A message with pictures gets a context window of its own:**
  `ollama_vision_num_ctx` (Settings → Models, *Vision context window*,
  default 8192), so four pictures in one message fit; at Ollama's usual
  4096 it refused them. The dashboard's chat reads it when it restarts.
  Those calls also send `think: false`, so a vision model that reasons
  first by default answers straight away instead of in silence.
* **`scripts/linux/provision.sh`** writes `OLLAMA_VISION_MODEL` with its
  tier's pin (both tiers: the 2B above) the way it writes the other model
  keys (a value the household set is kept), and pulls it: **+1.9 GB**.
  `--no-vision-model` leaves the pull out.

## 2026-10-11 — Ubuntu's jobs, switched off, no longer leave the box "degraded"

A server update; nothing to do.

* **`systemctl --failed` stays empty after `os-jobs.sh` masks Ubuntu's
  background jobs.** Under **No**, timers that were running when they were
  masked (`apt-daily`, `apt-daily-upgrade`, `motd-news`) showed as failed
  ("Unit to trigger vanished") and the box reported "degraded" until the
  next start. Each one is now stopped before systemd reloads, and every
  unit the helper masks, disables or unmasks has its failed mark cleared,
  marks an earlier version left included, at the next **Restart
  Domovoi**. `unattended-upgrades.service` keeps running until the next
  boot, as before, so an upgrade already under way is never cut short
  (docs/LINUX_HOST.md, "Internet or not").

## 2026-10-11 — Chat: pictures say what they need, and failures read in plain words

New Android build and a server update; nothing else to do, unless you want
chat to read pictures.

* **Pictures need the vision model, and chat now says so.** A message with
  pictures is answered by the vision model (Settings → Models, the Vision
  row: `qwen2.5vl:7b` unless you picked another), which is not installed
  by default. A picture used to come back with Ollama's raw error in red
  ("Client error '404 Not Found' for url 'http://localhost:11434/api/chat'
  …"). Now, on the dashboard and in the app, the paperclip explains instead
  of opening the picker, and a picture sent anyway is answered at once with
  "Pictures need the vision model qwen2.5vl:7b, which isn't installed on
  this Domovoi. An admin can install it in Settings → Models, or pick
  another vision model there." Ollama is not asked. A signed-in admin on
  the dashboard gets a link to that tab; the app opens the dashboard's
  Settings page. **To read pictures, install the vision model there.**
* **Every chat failure reads as a plain sentence**: model not installed,
  vision model that can't read pictures, not enough memory, Ollama
  unreachable or too slow, anything else it reported, an empty answer. The
  exception, with Ollama's address, goes to the web backend log only.
  Replies saved before this update are read out the same way.
* **Text questions after a picture work.** The thread's earlier pictures
  went to the chat model with every later question, and Ollama refuses a
  picture handed to a text-only model, so one picture broke the rest of the
  chat. Pictures now go only to the vision model.
* **For clients:** the send stream's `error` event is `{detail, code,
  model}`; a reply row keeps the code as `stats.error_code`; `GET
  /api/chat/models` adds `vision_installed` (`false`: a picture would be
  refused; `null`: Ollama couldn't be asked). The codes are in
  API_REFERENCE.md §3.18.

## 2026-10-11 — Long lists load as you scroll

New Android build; on the server, the usual update. Update the server
first: the Android app already installed keeps working against it (it gets
the answers it was built for), and the new app still works against a
server that hasn't been updated (Music still pages there, by offset; the
other lists arrive whole, as before).

* **Music, Videos, Files, Audiobooks and Podcasts load a page at a time**,
  on the dashboard and in the app. The first 50 rows show at once; the
  next 50 load as you near the bottom, with "showing N of M" and a
  **retry** when a page fails. Music's library loses its page arrows for
  the same scrolling (web and app), and a page grew from 12 rows to 50.
* **The app's video feed keeps going past the first page**: as you near
  the end of what is loaded, the next page joins the feed.
* **The phone's own lists page too** ("your device" and the offline app):
  music, videos and photo albums are read a page at a time off the phone's
  media index, and a picked folder builds its rows a page at a time.
* **Every search, filter and sort runs on the server** and covers the
  whole list. New: a search box on Audiobooks, Podcasts (shows, and the
  episodes of one show) and in the Files list (this folder), on the web and
  in the app, and sort choices on the web's Videos, Files and Audiobooks.
* **Select all** on Music and Files selects the loaded rows; its tooltip
  says how many.
* **API:** every list behind those pages takes `limit` (default 50, max
  200) and a `cursor` from the previous answer's `next_cursor`, and
  answers with `total`, `has_more` and `next_cursor` (API_REFERENCE §1.7).
  A client that sends no paging parameter gets the answer it always got:
  the whole list for Videos, a Files folder, Audiobooks and Podcasts (the
  bare array for those three), and Music's 50-row page with `offset`.
* **Videos and Files listings are cached between pages** and re-read when
  a folder changes, a write through the server lands, on **Rescan**, or
  after five minutes. A cold read of a 10,000-entry folder fell from ~6 s
  to ~0.13 s, and of 10,000 videos from ~2 s to ~0.1 s (Windows dev box);
  later pages take ~25 ms.
* **Videos reads every library whole.** The 2,000-videos and 2,000-folders
  per library limits are gone. 50,000 videos show their first page in
  ~1–1.5 s and every later page in ~50 ms. The one limit left is time: a
  drive still being read after two minutes is listed as far as it got, and
  the Videos page says so. An app from before this update still gets at
  most 2,000 videos per library (the list it was built to hold).

## 2026-10-11 — Android: "your device" on the Files, Music and Videos pages

New Android build; nothing to do on the server.

* **Files, Music and Videos have a switch at the top: the server, or
  "your device".** "Your device" shows the phone's own folders and
  photos, music and videos exactly as the app does with no server
  connected; playing something there plays it on the phone. Switching back
  shows the server's page where you left it, and each page remembers its
  side until the app is closed.
* **There, each row's Download is an Upload** that sends that file to the
  server: a track to the Music library (indexed at once), a video to the
  Videos library (Documents on a server without one), anything else
  through the send-to-home sheet, where you pick the library and folder.
  The row shows the progress; a toast says where it landed. The file
  streams, so a long phone video uploads without running the app out of
  memory.
* **A device an admin blocked from changing files** (Settings → Devices)
  sees why above the list and its Upload buttons refuse with the server's
  reason; so does a read-only Music library. Uploads use the same doors
  as the dashboard's (`/api/music/library/upload`, `/api/files/upload`), so
  nothing changes in who may write where.
* **A spell in the background no longer drops the app to local media.**
  Coming back after ~30 s away (the system folder picker is enough) used
  to land on the phone's media next to a healthy server, and the page you
  were on lost its place. Only time the app spends on screen trying the
  server now counts toward the 10 s fallback, with a fresh check on the
  way back; out of the house the fallback works as before.

## 2026-10-11 — Satellites moves into Settings, a phone bar you pick, and stopped rooms read quiet

Dashboard and Android build; nothing to do on the server beyond the usual
update.

* **Satellites is a Settings tab now** (Settings → Satellites, next to
  Devices; on Android, Settings → Satellites). It left the sidebar, the
  phone's bottom bar, the Android rail and drawer and Home's "everything"
  grid. Everything on it works as before, with the same sign-ins: the room
  drawer, approvals, adoption, announcements, preparing satellite media.
  Old links and bookmarks to `#satellites` open the tab, and so do Home's
  room cards and problem rows. The satellites count badge is gone with
  the sidebar row (Settings has none); the tab's first line counts the
  rooms.
* **The phone bar is home · music · videos · chat**, and each phone can
  pick its own: Settings → **Phone bar** on the dashboard and in the app,
  or "edit the bar" on Home's
  "everything" grid. Home stays first; two to five tabs; plugin pages can
  go there too. It is kept on that phone only (the browser's storage, the
  app's settings): another phone, or a private window, keeps the default.
  Calendar moved to the "everything" grid. A shared screen still leaves
  people, chat, files and news off, whatever its bar says.
* **A stopped room no longer looks like it is playing on Home.** A room's
  player keeps the last song loaded after a stop, and Home showed that
  song at 0:00 as if it were on; Music and Satellites already said
  nothing. Home now shows a stopped room as quiet, on the dashboard and in
  the app.

## 2026-10-11 — Android: the video feed stops when you leave, and phone videos go to Videos

New Android build; nothing to do on the server beyond the usual update.

* **The video feed pauses when the app leaves the screen** (the home
  button, another app, the screen turning off) and picks up where it was
  when you come back, if it was playing then; a video you paused stays
  paused. It used to keep playing out of sight. This is the swipe feed
  for both the server's Videos tab and the phone's own videos.
* **The screen stays on while a feed video plays** (buffering included);
  paused or stopped, it times out as usual.
* **Send to home puts a video in the server's Videos library** by default
  (the new `core:videos`, see "A Videos library" below). It used to
  default to Documents, and an `.mp4` to Music; a phone's `.mp4` now counts
  as a video. A server without that library gets videos in Documents; the
  sheet still lets you pick any library.

## 2026-10-11 — The update unit: "No internet" quiets Ubuntu too, and the lock works on Ubuntu 26.04

Two changes to `domovoi-update.service` (`scripts/linux/apply-update.sh`)
on Linux servers.

* **Ubuntu's own background jobs follow the internet answer.** After every
  healthy restart or update the unit runs, the new
  `scripts/linux/os-jobs.sh` sets them to the answer in Settings →
  Internet. Under **No** it masks `apt-daily.timer`,
  `apt-daily-upgrade.timer`, `unattended-upgrades.service`,
  `motd-news.timer`, `ua-timer.timer`, `apport-autoreport.path` /
  `.timer` and `snapd.snap-repair.timer` (only those installed and not
  masked already; it writes down what it masked in
  `/var/lib/domovoi-os-jobs/masked`). Under **Yes, always** or
  **Sometimes** it unmasks exactly those again. Not answered: nothing
  changes. A box answered **No** stops checking for Ubuntu updates at its
  next **Restart Domovoi**: update it by hand (`sudo apt update && sudo
  apt upgrade`) on a day it is online, or switch the answer. To keep
  managing those timers yourself, set `DOMOVOI_MANAGE_OS_JOBS=0` in
  `/etc/default/domovoi-update` (provision.sh honours it too). In the
  Windows installer's WSL distro it also turns `wsl-pro` and snapd off;
  on any other Linux box they are left alone. What it changed is in the
  journal (`journalctl -u domovoi-update`), and the last update lists an
  `os-jobs` step; when it could not set them (a unit it could not mask),
  that step is a warning and Settings → Version shows why under the last
  update (docs/LINUX_HOST.md, "Internet or not").
* **Taking an update on a box answered No** (one that would download) now
  needs one more restart than before: switch to **Sometimes**, restart,
  switch back to **No**, and **restart once more**, or Ubuntu's updaters
  stay unmasked from the Sometimes restart until the next one. The
  refusal message says so.
* **The hash-pinned lock now works on Ubuntu 26.04.** Opted in
  (`DOMOVOI_USE_LOCK=1`), the re-sync installed the build tools with
  `pip install --require-hashes -c <lock> setuptools wheel`, which the pip
  in Ubuntu 26.04's venv (25.1) refuses, so every update that changed the
  dependencies rolled back. It now installs them the way the manual
  install does. Boxes set up by `scripts/linux/provision.sh` are opted in
  (`DOMOVOI_USE_LOCK=1` in `/etc/default/domovoi-update`); every other box
  keeps the default, off.

No action needed unless you answered **No** and want Ubuntu's updates to
keep running (set `DOMOVOI_MANAGE_OS_JOBS=0`).

## 2026-10-11 — The setup code is no longer written to the journal

The first-run setup code used to be printed in a banner on every boot
until the admin was claimed, and under systemd that banner went into the
journal and into any log bundle exported from it. Now the core prints the
code only when its stdout is a terminal.

* Run as a service, the core logs one line instead, at WARNING (so it
  shows at every `LOG_LEVEL`): where the code file is and that you enter
  it on the dashboard (Settings → Configuration → Admin → "set up
  admin"). The code itself is not in `journalctl -u domovoi-core` any
  more.
* **A code already in the journal stays valid** for the rest of its 24 h
  window (a restart reuses the file's code while it is inside it), and
  the old journal lines stay as long as the journal keeps them. If that
  matters, claim the admin account now (the code is then deleted), or
  delete `~/.domovoi/setup-code.txt` and restart the core for a fresh one;
  `journalctl --vacuum-time=...` drops the old lines.
* Read it from the file, as the user the core runs as:
  `cat ~/.domovoi/setup-code.txt`. On a box set up by
  `scripts/linux/provision.sh` that user is `domovoi`:
  `sudo cat /home/domovoi/.domovoi/setup-code.txt`.
* Started by hand in a terminal, the core still prints the banner with the
  code, as before.
* `python -m domovoi.main --reset-admin` follows the same rule: it prints
  the new code in a terminal, and only where the file is when its output
  is redirected.
* The dashboard's setup prompt and the `403` from `POST /api/auth/setup`
  now point at the file first.

No action needed. If you read the code from the journal, read the file
instead.

## 2026-10-11 — A Videos library

There is now a core Videos library, `core:videos`, read from the new
setting `VIDEOS_DIR` (default `~/Videos`).

* Like the other media folders, it is created at the first boot after the
  update when it is inside the home of the user the dashboard runs as. A
  folder elsewhere (an external drive) is never created for you; make it
  yourself.
* It shows in Files as **Videos**, and the Videos tab lists what is in it.
  The Videos tab still finds videos in every other library too.
* Set it in Settings → Configuration → Advanced → Paths → **Videos
  directory** (signed in as admin), or as `VIDEOS_DIR` in `domovoi/.env`.
  A change takes effect after a restart.

Nothing to do unless you keep your videos somewhere other than `~/Videos`.

## 2026-10-11 — The container guard: loud alerts for failed containers, a freeze on tampering

The core now knows which Docker containers should be running — its
database and the rest of the compose stack, each room's music player,
every plugin's declared containers — and checks them every 30 seconds
(docs/CONTAINER_GUARD.md). One Flyway step (V024).

### Upgrading

1. **Update the server** as usual; the update unit applies **V024**
   (`container_incidents`, `container_guard_baselines`). Without it the
   guard still checks and shows its findings on the Containers page, but
   records no incidents and freezes nothing.
2. **Nothing alerts in the first 3 minutes** after the core starts, and a
   failure must last 2 minutes before it does. If a banner appears after
   the update, read it: it is about a container that really is down or
   different. An amber "running an older … image than the one Domovoi
   pins: recreate it" is a container that hasn't followed a pin bump (an
   old `domovoi-searxng` from before the pins, for example) —
   `docker compose up -d <service>` from `domovoi/` puts it on the pin.
3. **Plugins:** Jellyfin, RomM and Kiwix 1.1.0 declare their containers
   (SDK 1.5) and are watched; older versions keep working and are marked
   "containers not watched". Upgrade them from the Plugins page (they
   still load on an older core).
4. **Phones:** the new APK adds the notifications (a "Container problems"
   channel) and the banner.

### What changes

* **A banner on every dashboard page** while something is open: red for
  tampering and for a required container's failure, amber for an optional
  one's (SearXNG, Letta, Flyway). Guests and shared screens see only
  "Something needs an admin's attention." Home's problems list gets a row;
  the new **Containers** page (`#containers`) has the details, and the
  admin's acknowledge and clear.
* **A failure** (missing, stopped, crash-looping, unhealthy, an older
  image of its own repository than the pin, Docker down) alerts and
  resolves by itself; one that keeps coming back within 15 minutes stays
  one incident. **Tampering** (an image from another repository than the
  pin or without registry provenance, a music player image the core
  didn't build, an unknown container under Domovoi's compose project or
  music player names, privileged or host settings)
  alerts and **freezes plugin installs, upgrades and enables, satellite
  code and payload pushes, satellite media preparation, and updates**
  (`423`) until an admin clears it after review. Everything else keeps
  working.
* **Android**: one notification per incident on paired phones (taken
  back when it resolves or is cleared), the same banner, and a row in the
  app's Home problems; tapping opens the details. At once while the app is
  on screen; otherwise with its background sync, about every 15 minutes
  and only on Wi-Fi or Ethernet.
* **When Docker is down on a compose install**, the database container is
  down with it, so no incident can be recorded: Home's "the database isn't
  answering" row is the alert (docs/CONTAINER_GUARD.md).
* Resolved incidents are kept 90 days.
* **SDK 1.5** for plugin authors: `[[containers]]` in the manifest and
  `ctx.add_container(id, resolve)` (docs/PLUGIN_DEVELOPMENT.md). The
  install trust screen lists a plugin's containers.
* Settings: `CONTAINER_GUARD_ENABLED`, `..._INTERVAL_SEC`,
  `..._BOOT_GRACE_SEC`, `..._FAILURE_GRACE_SEC`, `..._DAEMON_GRACE_SEC`,
  `..._TAMPER_CONFIRM_SEC`, `..._COMPOSE` (docs/CONTAINER_GUARD.md).

## 2026-10-10 — Guests can change the music in a room that is playing

A friend on a phone browser tried to change the queue of a room that was
playing and could not even find the music: the tab strip sat below the
screen, the Room queue tab asked them to pair, and reordering needed a
mouse. The owner's decision: **guests should be able to edit queues that
rooms are actively playing**, and use those rooms' playback controls,
until an admin bans them.

**This deliberately loosens the October security round's device tier**,
for the room queue and the transport controls only, by owner decision. A
*guest* is a request carrying no credential at all (no household token, no
sign-in) on a box past first-run setup. For a room that is playing (MPD
`play` or `pause` with a current song), a guest may read its queue, add,
remove and move entries, and pause, resume, skip and go back. No clear
and no stop route, never the song playing now, nothing on a room that
isn't playing, and nothing it does starts a room's player (a guest can
still empty a queue one song at a time; the limits below bound how fast). The core decides
"playing" at the moment of each request; a paired device or an admin gets
exactly the answers it got before, on every room.

### What changes

* **New setting `GUEST_QUEUE_EDITING`, on by default** (Settings →
  Configuration → Security: "Guests can change the music in a playing
  room"). Off restores the old behaviour exactly: a guest's queue or
  transport call is `401` at both hops. Applies at once.
* **Guest bans.** Settings → Devices → **Guests** lists the guests that
  changed the music this week (browser id, address, browser, last action,
  room), each with **ban** / **unban**; the Room queue tab gives an admin
  a ban button on any entry a guest added. A ban covers every room, the
  queue and transport alike, and matches the guest's browser id or its
  address. It never affects a paired device or an admin. Household policy,
  not a security boundary: a determined guest comes back as a new one.
* **Limits.** A guest's queue edits and transport presses share 30 a
  minute per address, its queue reads 120 (`429` beyond); an add takes at
  most 50 tracks and never takes a queue past 500 entries; a remove takes
  one song. A guest request must come from the dashboard (a custom header),
  so a page on another site can't press skip through a LAN browser. The
  per-address limits and address bans assume one address per guest
  (SECURITY_PRIVACY.md says when that fails). Recorded guests are kept 30
  days at most, 10 per address.
* **Dashboard on a phone.** Each Room queue row has **play next**, **up**
  and **down** buttons (44 px; drag still works with a mouse). Every
  now-playing card with something on it has a **queue** button that opens
  that room's queue. A track's details gain **add to *room*'s queue**
  (append, not replace). Opening the Room queue tab no longer pops the pair
  prompt; a guest on a room that isn't playing sees "isn't playing" and a
  **pair this phone** button. A guest never sees Clear, Stop or favorite,
  and is never met with the pairing prompt for something it can't do:
  starting a quiet room says it takes a paired phone (with a **pair**
  button), and a refusal (the room stopped meanwhile, a ban) is said in
  words. With no live updates (those take a household credential), a
  guest's Music page re-reads the rooms every 15 s.
  Home shows a guest the rooms that are playing (with pause / resume and a
  queue link) instead of "couldn't load rooms". The shell fits the screen
  a phone browser really shows, so the tab strip is visible.
* **API.** `GET/POST /api/music/queue/{room}…` (except `/clear`) and the
  transport row answer a guest per the rule; the read gains `guest`,
  `admin`, and (for an admin) `guest_device_id` / `guest_source` on a
  guest's entries. New admin routes: `GET /api/music/guests`,
  `GET`/`POST /api/music/guest-bans`, `DELETE /api/music/guest-bans/{id}`.
  The dashboard sends `X-Domovoi-Device-Id` (its self-asserted browser id)
  on every request; the web hop forwards it and the caller's user agent.
  [API_REFERENCE.md](API_REFERENCE.md) §3.5 / §3.5a,
  [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#guests-on-a-playing-room).

### Upgrading

1. **Update the server** as usual; the update unit applies **V023**
   (`guests_seen`, `guest_bans`, and two nullable columns on
   `room_queue_items`). Without it, a guest's actions fail closed (the ban
   check can't be read: `503`) while everything a paired device does keeps
   working.
2. Nothing to set: guests are allowed from the first start. To keep the old
   behaviour, set `GUEST_QUEUE_EDITING=false` (or switch it off in
   Settings) before you open the door.

## 2026-10-09 — One restart at a time

Pressing **Restart to apply changes** and then reloading the dashboard
used to bring the button back while the update was still running, ready
to start another one on top of it. The server now keeps that state, not
the page.

* `GET /api/config/version` (core: `/v1/admin/version`) carries
  `restart_in_progress`: true while `domovoi-update.service` runs, however
  it was started, and for the moment between a restart being accepted and
  systemd taking it. The core reads systemd's state of the unit without
  sudo; nothing to set up.
* While it is true, `POST …/version/restart` schedules nothing and answers
  `ok: false, in_progress: true`, and `POST …/version/pull` answers
  `pulled: false` without running git.
* Settings → Version and the Plugins page's restart card show a greyed
  **Updating…** (or **Restarting…**) with the underway note, follow the run
  to its end and report how it went, after a reload too.

No action needed beyond the usual update.

## 2026-10-09 — Satellites explain their own deaths: health telemetry, self-checks, retained logs

Two satellites died while idle for days and only a power cycle brought
them back, with nothing left to read. Now each one watches itself,
recovers what it can, and leaves a record the dashboard shows. One
Flyway step (V022); the satellite code has to be pushed to each room.

### Upgrading

1. **Update the server** as usual; the update unit applies **V022**
   (`satellite_health`, `satellite_outages`). Without it the core keeps
   the latest sample and the last outage in memory only and logs one
   warning per start saying so.
2. **Push the satellite code to every room** from the dashboard
   (Satellites → room → Overview → **Upgrade satellite**). A room on the
   old code keeps working exactly as before — it sends nothing new until
   the core's `ready` lists the new frames, and this core still accepts
   its old hello — but shows *no health sample yet* until it is upgraded.
3. **Existing units, optional:** the reboot helper and the hardware
   watchdog — `satellite/PROVISIONING.md` §8.3. Cards prepared from today
   on have both; the code push installs neither (both need root). On a
   card prepared before today (every portal unit so far) the service
   account has no root by design, so this means re-preparing the card or
   editing it in a Linux machine; §8.3 gives the commands. Without the
   helper the client logs that a reboot would be needed instead of
   rebooting.

### What changes

* **Health card** on each room's Overview tab: memory (with the 24 h
  peak), CPU, temperature with the throttle flags, uptime and
  reconnects, Wi-Fi and NetworkManager state, mic frames per second and
  the wake loop's heartbeat, whether the gateway and the core answer,
  file descriptors — one sample a minute, kept when the room goes
  offline, 24 h in the database. **Last outage** under it: when the
  previous life of the satellite's process ended, why (`self-check: …`,
  `shutdown: requested`, or unknown for a power cut / OOM / wedge), the
  self-check actions it took, and a table of the failed connects it saw
  while it was away (refused, timed out, DNS, refused session; gateway
  and core reachable or not). `docs/TROUBLESHOOTING.md` says how to read
  both.
* **Logs tab split** into *retained on the server* (the lines the
  satellite pushed — including the `[offline]` ones it spooled while it
  could not reach the core, which used to be lost with its RAM ring —
  1 MB per room on disk, surviving the Pi dying) and the live ring.
* **Self-checks on the satellite** (`[health]` in its config): a capture
  stream that delivers no frames is reopened, then the process exits so
  systemd restarts it; a dead mic thread, a stalled wake loop, memory
  over 300 MB while idle, or more than 512 open files exit the same way;
  five minutes without a server with the link down runs the Wi-Fi
  recovery (once per five minutes, one cooldown shared with the Wi-Fi
  watcher); fifteen minutes without a server AND a silent gateway (no
  TCP answer on 53/80/443 nor ping, or no default route) reboots the
  unit through the new helper. Never while the gateway or the core's own
  machine answers: a core that is down is the core's problem. Every
  check acts only after seeing its condition twice in a row, and a
  **restart budget** — 3 self-restarts a rolling hour, at most 1 of them
  a reboot, kept across restarts — means a check that fires wrongly can
  never loop a unit: past it the check only logs, and `last-health.json`
  says so.
* **A refused session no longer redials every second.** A core that
  closed the socket before `ready` (pairing refused, parked for approval)
  used to reset the reconnect backoff; it now backs off like a failed
  connect (1 s doubling to 60 s), which is what pinned a Zero 2 W at
  100 % CPU after a core restart (SAT-CRASH D1).
* **API:** `GET /v1/admin/satellite/{room}/health` and
  `GET /api/satellites/{room}/health` (device read); the logs routes add
  `live`, `retained` and `last_outage` beside their old fields and answer
  an offline room with what is retained instead of `404`; `ready.features`
  gains `health` and `log_push` (for a token-authenticated session only;
  the core ignores both, and `prev_health`, from any other); the `hello`
  may carry `prev_health`; `/v1/admin/snapshot` (Open) gains a
  `satellite_health` digest — when each room's sample and outage report
  arrived, never their content — which `satellites.health` on the state
  socket diffs; the `satellite_logs_dir`
  setting (`~/.domovoi/satellite-logs`). Retained logs and health samples
  are household data — `docs/SECURITY_PRIVACY.md`, *Data at rest*.

## 2026-10-08 — Android: phone Files tab, video feed, sheet saves that keep formatting

Same-day as Security round 3 and merged on top of it. New Android build;
one server-side change in the Documents API. No Flyway step.

### Upgrading

1. **Install the new Android build.** It carries the round-3 security
   changes plus this feature set; the phone re-pairs silently.
2. **Nothing to do on the server** beyond the round-3 update. `PUT
   /api/documents/sheet/<name>.xlsx` now edits the existing workbook cell
   by changed cell instead of rewriting it, so a save from the dashboard or
   the app keeps formatting, column widths, merges, other sheets and
   anything past the editor's window. A workbook with charts, pictures or
   pivot tables is refused with `409` and left untouched rather than
   silently losing them; an `.xlsx` that would unpack past the size bound
   is refused with `413` before it is opened.

### What changes

* **Video feed.** Tapping a video opens a vertical swipe feed of the list
  it was tapped in (swipe, tap to pause, double-tap an edge to skip, long
  press for queue, details, save, and share for phone-local videos only).
  Server streams go through the app's one HTTP client, so the household
  token, the identity proof, the redirect rule and the cleartext rule apply.
* **One connection pop-up.** The server pill opens a dialog with a
  "this phone" / "your Domovoi" switch and the server list. The server side
  is greyed with the reason when it cannot be used, including a new "core
  not answering" state. Trusting a server still goes through the trust
  dialog, and the server in use can be forgotten from the list.
* **Files tab on the phone.** The local shell gains a Files tab: the same
  browser, text editor and sheet editor as the server's Files, over the
  phone's own folders chosen through the system folder picker (per-folder
  grants) and its photo albums. It cannot reach the app's own private
  storage or other apps' data. "Send to home" uploads through the Files
  door with the device id, so the household's write blocks and the
  secret-name filter apply. New permission: `READ_MEDIA_IMAGES` (API 33+),
  asked for when the photos library is first opened.
* **.xlsx editing on the phone keeps formatting**: only changed cells are
  patched into the first worksheet, every other byte is copied through.
  Sheet files are read under a 64 MiB bound and unpacked under a 200 MiB
  bound; a malformed character reference is left as text.

## 2026-10-08 — Security round 3

The fixes from the October security review, in one update. Several
household reads now need a paired device, satellites pair strictly by
default, updates can be bound to signed commits, satellites check that the
server they reach is the one they were prepared for, and the Android app
sends its token only to a server that has proved who it is. No Flyway
step. In this order:

### Upgrading

1. **Expect the first update to take longer, and under "No internet" to
   be refused.** Pull the update and press **Restart to apply changes** as
   usual. It counts as a dependency change (`pyproject.toml` and the locks
   moved), so the update re-syncs the venv the way it always has, through
   pip's resolver, which brings `urllib3` 2.8.0. The `dev` extra is no
   longer installed on a server; pytest and friends already in the venv
   stay until you remove them. The same update moves the database image
   from `postgres:16` to a pinned `postgres:16.15-trixie` digest, so
   `domovoi-db` recreates the Postgres container on the same data volume
   (nothing is lost; a few seconds' pause), and it rebuilds the music
   image, whose base is now pinned too, so music stops for a moment. Under
   **No** the update is refused as a dependency and image change: switch
   Settings → Internet to **Sometimes**, update, then switch back.
2. **Expect an amber "unsigned" badge** beside the version in Settings →
   Configuration → Version, and one warning per pull in the core's log.
   That is how an install without signed updates looks now; nothing is
   refused until you set them up (step 10).
3. **Approve any satellite that never paired.** Strict satellite pairing
   is now the default in the code, not only in a freshly written `.env`. An
   install whose `domovoi/.env` has no `SATELLITE_PAIRING_STRICT` line
   becomes strict at this restart. Satellites the dashboard shows as
   paired are not affected. One that has a pairing token but never paired,
   or whose pairing is reset later, waits on the Satellites page under
   "waiting for approval": type the six-digit code it shows and says (a
   satellite set up before 2026-09-22 says four digits, and those still
   work). A satellite with no pairing token at all (very old satellite
   code) is refused until it runs current code. The live install already
   runs strict, so nothing changes there. To keep the old behaviour, set
   `SATELLITE_PAIRING_STRICT=false`; the core then warns at every boot, and
   a room it lets in without a token cannot drop in on, or announce into,
   another room.
4. **Upgrade every satellite** (Satellites → the satellite → Overview →
   Upgrade satellite), after the core is restarted. Satellites now ask the
   server to prove its identity for the exact address they dialed, and
   check the signed file lists they download, including sounds and wake
   models. The core keeps answering satellites on the old code, so the
   order does not break anything; while any room is still on it, the core
   logs one line per start saying so. **If a satellite reaches the core by
   a name, or through NAT, a port-forward or a VM (the Windows installer's
   WSL2 design), list that address in `TRUSTED_HOSTS` in `domovoi/.env`
   before upgrading that satellite**, or it will refuse to connect once it
   has the new code; the core logs each refusal with this advice. The
   Beelink's rooms dial it by IP and need nothing. The satellite's root
   helpers (`domovoi-apply-payload`, `domovoi-sync-time`) are installed
   when a card is prepared and are not replaced by a code upgrade: a unit
   gets the new ones only when its card is re-prepared and re-flashed.
5. **Put the household token in each video satellite's kiosk URL.** The
   kiosk page reads its room's row, which is now a household read. In the
   satellite's `config.toml`:
   `[display] kiosk_url = "http://<server>:6369/display.html?room=<room_id>&device_token=<household token>"`
   (Settings → Devices → Household token; percent-encode it if it holds a
   space or `&`). The page keeps the token in the kiosk browser and removes
   it from the address bar. Without it the kiosk shows what is playing,
   with no live updates and buttons that do nothing. Rotating the token
   means updating this line on every kiosk.
6. **Send the household token from any script or automation that reads
   the house.** These now answer `401` to a caller with no credential:
   the people roster and a person's row and sessions, every room's row and
   the pending-adoption list (`/api/satellites...`), the calendar, a room's
   music queue (`/v1/admin/music/queue/<room>`), the version read
   (`/v1/admin/version`, `/api/config/version`) and podcast directory
   search. Send `X-Device-Token: <token>` (the core mirrors it in
   `~/.domovoi/device-token.txt`). The dashboard and the Android app
   already send it. An unpaired browser's Home shows no rooms and no week.
7. **Upgrade the sibling plugins you have installed** (Plugins → upgrade,
   with the zip from each repository's `dist/`): Sleep Sounds **1.2.3**,
   the media-download plugin **1.2.2**, Kiwix **1.0.2**, RomM **1.0.2**,
   Jellyfin **1.0.3**. Their everyday actions (sleep play and stop, the
   media-download plugin's search and play, the Kiwix search box and
   Library tab, the status refreshes) then take the household token, so a
   paired phone or browser can use them without the admin password, and a
   caller with no credential gets `401`. The media-download plugin's zip
   also carries `urllib3` 2.8.0. Until a plugin is upgraded its routes keep
   their old tiers. The bundled radio plugin updates with the core.
8. **Install the new Android app**, then open it once at home. Its
   household token is moved into a store sealed by the Android Keystore on
   the first start, silently: the phone stays paired. On that first
   contact at home it records the server's identity, and from then on, on
   every network, the server must prove that identity before the phone
   sends it the token. Away from home, whatever answers at the saved
   address gets a token-less check and nothing else. If the core is ever
   reinstalled or its identity rotated, forget the server under Settings →
   Connection → Trusted servers and pair again. If the phone's Keystore
   key is gone (a reset that restored files but not keys), the app asks
   for the token once.
9. **Press Refresh caches once, while online**, in satellite media
   preparation. Wake-word models now download from pinned addresses and
   are checked by SHA-256; anything in the cache the pins don't vouch for
   is removed before a card is built.
10. **Recommended: set up signed updates.** Until
    `/etc/domovoi/allowed_signers` exists on the box, every pull and update
    only warns. Once it does, **Pull the latest** verifies the fetched
    commit's SSH signature before the checkout moves, and the update unit
    verifies HEAD again as root before it runs anything; a commit that
    doesn't verify is refused and nothing changes. To set it up
    ([LINUX_HOST.md, Signed updates](LINUX_HOST.md#signed-updates)): make
    an SSH signing key on the machine you commit from and sign every
    commit, merges included; put the allowed-signers file on the box,
    owned by root and writable by root alone; turn on GitHub's **Require
    signed commits** on `main`; then run
    `sudo bash <checkout>/scripts/linux/install-update-unit.sh --dry-run`,
    which says `signed updates enforced` or stops if every run would be
    refused. The commits in this release are not signed: pull a signed
    commit before the file goes in, or the next update is refused. Merges
    made by tools must be signed too, or every box refuses them.
11. **Optional: opt into the hash-pinned lock.** `requirements-linux-py314.lock`
    pins every package a Linux server runs, each with its SHA-256s. The
    update unit uses it only when `/etc/default/domovoi-update` sets
    `DOMOVOI_USE_LOCK=1`; without that, updates re-sync exactly as before
    and the step says the lock is there. Re-seed the lock from the box's
    own `pip freeze` first, so the first locked re-sync moves only what the
    floors require ([LINUX_HOST.md, The hash-pinned lock](LINUX_HOST.md#the-hash-pinned-lock));
    the installer's `--dry-run` reports which way a re-sync goes. Fresh
    installs by hand now use the three install commands in
    [LINUX_HOST.md, Install](LINUX_HOST.md#install).
12. **Optional: give Letta and SearXNG their own secrets.** A fresh
    install now writes per-install `LETTA_TOKEN` and `SEARXNG_SECRET`; an
    existing `domovoi/.env` keeps the old shared values until you add them
    (the one-liner is in [LINUX_HOST.md](LINUX_HOST.md), "Helper-container
    secrets"), then recreate the two containers and restart the core.
13. **Optional: remove rooms that were never real.** Before this release a
    queue read for any room name could create that room. Those stray rooms
    are not removed by the update: delete each from the Satellites page,
    or with `DELETE /v1/admin/satellites/<room>?purge=true` (admin).
14. **Optional: adopt the sandboxed systemd units** in
    `scripts/linux/units/`. Nothing is installed for you. They match the
    layout in [LINUX_HOST.md](LINUX_HOST.md) (`/opt/domovoi`, user
    `domovoi`); a box with another user or checkout path (a checkout in a
    home directory, say) must change `User=`, the paths and the
    `ReadWritePaths=`/`BindPaths=` lines first. Afterwards check that
    Settings → Version still offers Restart Domovoi.

**For the owner's own machine** (not part of any install): remove the
`Bash(python -c ' *)` and `Bash(git add *)` entries from
`domovoi-project/.claude/settings.local.json` and allow the specific
scripts you use instead (A8-16); delete `COLLABORA_JWT_SECRET` and
`ONLYOFFICE_JWT_SECRET` from the dev box's `domovoi/.env`, whose engines
are retired (A8-18).

### What changes

* **Music routes never create a room.** Every route that names a room
  answers `404` for one the house has no satellite, pairing or music row
  for. A room appears when its satellite is accepted or adopted.
* **No live microphone before setup.** Before first-run admin setup, and
  again after `--reset-admin`, starting a drop-in answers `501` and the
  phone drop-in socket closes with `setup_required`, until an admin signs
  in. Nothing else loses its pre-setup grace.
* **Satellite approvals.** A request whose code is not six digits (or the
  four of an older satellite) is refused and nothing is parked. The first
  device to ask for a room name holds it for three minutes; after that a
  different device's request replaces it.
* **Add by URL** refuses a URL that points into the house or at this box,
  admin or not (`400`), and answers `409` under "No internet". A media
  server on your own network goes in `OUTBOUND_ALLOW_HOSTS`.
* **Server-side fetches connect to the address the safety check
  approved**, not to a second lookup of the same name. Under "No
  internet", a local-looking name that resolves to an internet address is
  now refused: "No" means the address, not the spelling. Running the core
  behind an HTTP proxy is not supported.
* **The radio relay serves only audio**, and a stream opened directly in a
  tab downloads instead of rendering. Song recognition no longer samples
  playlist-only (HLS) stations. Directory stations that point into the
  house are skipped unless their host is in `OUTBOUND_ALLOW_HOSTS`. The
  radio plugin's own lock is gone (it runs in the core's environment).
* **Plugin installer:** a zip whose source files exceed 2 MiB each or
  32 MiB together is refused before the trust screen; staging no longer
  freezes the core; uninstalling a `domovoi plugin dev` registration leaves
  the developer's files alone.
* **Dashboard files:** Documents "Open raw" and Images raw open in the tab
  only for pictures, audio, video, PDF and plain text; everything else
  (HTML, SVG, XML, Markdown, JSON, CSV ...) downloads. Secret-shaped files
  in `~/Documents` (`.env`, keys, setup codes) are no longer listed or
  served. A Files device block also stops that device's music upload, and
  a room-queue block stops a blocked device replacing the queue. Music
  uploads with names Windows can't store are skipped with a reason.
  Podcast `keep_n` is limited to 1–50. Library reads show a track's path
  relative to the music folder.
* **Piper voices** must be named like the catalogue's (`en_US-lessac-medium`)
  and are checked against pinned or published digests; a download that
  doesn't match is not kept.
* **Under "No", the core stops the search helper at start**, also when the
  answer was set by hand in `.env`.
* **The update unit's result file** (`last-result.json`) is now readable
  by the service user's group only, and a rollback keeps only the newest
  replaced database (`DOMOVOI_UPDATE_KEEP_FAILED_DBS`, default 1). The
  checkout can be pinned to its upstream (`DOMOVOI_UPSTREAM_URL`,
  `DOMOVOI_UPSTREAM_BRANCH`). `refused` now also means "HEAD did not
  verify" or "not the pinned upstream". The version read gains a
  `signature` object.
* **New files on the core:** `~/.domovoi/manifest-serials.json` beside the
  server identity (include it in backups; losing it is safe). Rotating the
  identity is `python -m domovoi.server_identity --rotate-identity`, which
  explains the cost first; every prepared card must then be re-prepared.
* **The `docker` group is root-equivalent**, and the docs now say so where
  they grant it.
* **domo-mass-flash:** setup network names carry six characters, the card
  no longer holds the console password in plain text, keys and passwords
  print only with `--show-secrets`, the labels file is private and its
  first line says it holds credentials, and `sanitize` removes a set-up
  master's approval record and receipts.
* **Android:** the token goes only to the server it was issued by; the
  media session admits only this app and the system; Settings → Connection
  applies the same address rule as the picker and lists trusted servers
  with a forget button; save-to-device downloads obey the plain-http rule.
* **Not fixed in this release:** one host on the network can fill the
  core's connection limit (128) and make it refuse everyone until it lets
  go; a per-source limit in the host firewall on ports 6369 and 6370 is
  the mitigation. Several upload handlers still hold a whole upload in
  memory, and a note's preview loads images from any address its author
  chose. All three are described in
  [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md).
### Added by the review pass (same update)

The security review of these fixes asked for a few more changes before the
merge. They ship in the same update; nothing above changes.

* **More reads need a paired device:** a room's play history
  (`/api/satellites/{room}/recently-played`), a room's satellite config read
  (both hops) and the speech latency summary (`/api/stats/latency`,
  `/v1/stats/latency`). `GET /api/music/now-playing` stays open, but the name
  of the device that queued the song is `null` for a caller with no household
  credential. Scripts that polled these without a credential now get `401`.
* **Documents and Files withhold more secret-shaped names** (SSH keys under
  their default names, the `.ssh`, `.gnupg`, `.aws` and `.kube` folders, every
  `.env.*`, `.netrc`, `.pgpass`, `*.kdbx`, `*.gpg`, `*.ovpn` and a few more),
  and anything inside a withheld folder. Such files are not deleted; they are
  absent from both listings and answer `404`.
* **Kiosk:** `display.html` replaces a stored household token with a different
  `?device_token=` only after the server has accepted the new one. A kiosk with
  no token shows what was playing when the page loaded and then does not
  update; put the token in `[display] kiosk_url` as described in step 4.
* **Satellite approval before first-run setup** answers `501` at both hops.
  Finish setup and sign in, then approve the satellites waiting in the card.
* **Plugin installs run one at a time.** A second click while an install is
  still running answers `409`. **Sleep Sounds 1.2.4** is the version to
  install now (its overview and `/v1/plugins/sleep/state` need a paired
  device, since they say who is asleep where).
* **Radio relay** refuses a station whose content type is not plain ASCII and
  every playlist spelling; the room's own player still plays such stations.
* **Satellite identity:** sounds-channel serials are kept per voice (a
  satellite on older code that switches voice and back may refuse the earlier
  voice's list until its next code sync; it keeps playing cached clips), and
  the identity proof is signed only for names listed exactly in
  `TRUSTED_HOSTS`, never for a wildcard entry.
* **The shipped core unit** under `scripts/linux/units/` no longer sets the
  sandbox options that make systemd imply `NoNewPrivileges` for a non-root
  service, which would have broken the Restart button and the update unit.
  If you installed that unit from an earlier build of this branch, install it
  again, `sudo systemctl daemon-reload`, restart the core and check that
  Settings → Version still offers **Restart Domovoi**. The web and database
  units keep the full sandbox.
* **Compose now requires per-install `LETTA_TOKEN` and `SEARXNG_SECRET`.**
  The update unit, `dev.sh` and `dev.ps1` run
  `python -m domovoi.env_bootstrap --repair` first, which appends generated
  values for the two keys and changes nothing else. **If you update by hand**
  (`git pull`, then restart the units yourself), run it once after the pull
  and before the restart:
  `sudo -u domovoi /opt/domovoi/.venv/bin/python -m domovoi.env_bootstrap --repair`
  (adjust the user and path to your layout); without it `domovoi-db` refuses
  to start. Chat-mode users: recreate the Letta container once
  (`docker compose --profile chat up -d letta`) so it learns the new password;
  the Version card shows a warning until then.
* **Opt-in lock:** with `DOMOVOI_USE_LOCK=1` and a lock the update cannot
  use, the step is a `warn` and Settings → Version shows
  **lock not applied: <reason>**. A package that fails its hash check still
  rolls the update back.
* **Wake-word models on a prepared card** are verified against pinned
  hashes on the server and again on the satellite; a card with a model
  missing is refused at build time instead of fetching it unverified later.
* **Android (new build):** the phone re-proves the server identity on any
  network change, including ones a VPN hides, at most every ten minutes and
  after every return from the background; redirects never carry the token
  to another host; only video saves carry a credential, and only right after
  a fresh proof; the key shown in the trust dialog is the one pinned; a core
  that is down reads as "core not answering", never as an impostor; and the
  active server can be forgotten from Settings (the app returns to the
  picker) when its key legitimately changes.

## 2026-10-06 — Lyrics: see the words, and find a song by them

### Upgrading

1. **Pull the update and restart** as usual. No new Python packages.
   **Flyway V021** adds the lyrics tables (`track_lyrics`,
   `track_lyric_lines` and the view `track_lyrics_shown`); the update's
   backup and rollback cover it. The **Android app** has a new build
   (lyrics in its players): install it to see them on the phone.
2. **Lyrics you already have are read on their own.** Within a few minutes
   of the restart Domovoi starts reading a `.lrc` file next to a song (same
   name as the song, any case) and the lyrics inside your song files (MP3,
   FLAC, Ogg, M4A, WMA tags). Nothing leaves the house for these. The first
   pass over a big library takes a while (two minutes of work every five
   minutes); after that only files that changed are read again. The Music
   page's **Jobs** tab shows how far it got.
3. **LRCLIB follows your internet answer — on a box answered "Yes,
   always" (the Beelink) or "Sometimes" it starts at once.** For songs with
   no timed lyrics of their own it asks lrclib.net, sending each such
   song's **title, artist, album and length** (nothing else), about one
   request a second, only while the server is online. The first pass for
   about 5,000 songs takes several hours — about a night — and longer
   whenever LRCLIB asks it to slow down; songs it didn't have are asked
   about again after four weeks or more. Under **No** it is off, and the
   Jobs tab says this Domovoi stays off the internet; with the question
   unanswered it is off too. To keep it off whatever the answer: Settings
   → Configuration → Library → **Synced lyrics from LRCLIB** (or
   `LYRICS_LRCLIB_ENABLED=false`); switching it off stops it before its next
   request.
4. **Domovoi may write `.lrc` files into your music folders.** With
   **Save lyrics as .lrc files** on (it is, whenever LRCLIB is on), timed
   lyrics from LRCLIB are also saved as a `.lrc` next to a song that has
   none, so other players can show them too. Such a file says
   `[re:Domovoi]` at the top. Only a song with no `.lrc` at all gets one: a
   `.lrc` you made — named like the song, or "Artist - Title.lrc" — always
   wins, is what the players show, and is never written over, changed or
   deleted; Domovoi updates only the files it wrote, and one you edit or
   delete stays yours (it is never written again). Plain lyrics without
   timing stay in the database only. The core needs write permission in the
   music folders for this (on the Beelink it runs as their owner); a folder
   it can't write shows on the Jobs tab as songs whose `.lrc` "couldn't be
   saved", with the reason. Switch it off in the same place if you'd rather
   Domovoi never wrote there.

### What changes

* **The players show the lyrics of what's playing**, line by line with the
  current line highlighted when the lyrics have timing, as plain text
  otherwise: the Music page's **Player** tab, the phone's player sheet
  (closed by default), the desktop player bar's lyrics button, and a
  room's card (its current line). It follows a room too, from what the
  room's speaker reports; if a room's speaker runs behind, nudge the
  timing (**−¼ s / +¼ s**, remembered per room). In your own browser or
  phone, tapping a line jumps there. The Android app's Player tab, player
  sheet and room cards do the same.
* **Ask for a song by its words**: "play the song that goes …", "put on
  the one with the words …", "play the song by <artist> that goes …", and
  "what's the song that goes …" (Domovoi names it and offers to play it).
  When it isn't sure it asks "Did you mean …?" the way it does for names;
  a song you don't have still goes to your streaming plugin, if you have
  one, as before. "What song goes well with a rainy day" and the like are
  not taken for words of a song. And when "play <something>" finds
  nothing by that name, the words are tried as a lyric last — that plays
  only on an exact line of six words or more, and otherwise at most asks.
  Domovoi never says or shows the words back. Settings → Configuration →
  Library → **Find songs by their words** switches all of this off.
* **Lyrics are the household's.** They are served on the household tier
  only (a paired phone or browser, an admin), like the rest of the
  household's own data — never on the kiosk display or another open page,
  never in the logs; the live updates and the server status carry counts
  only.

## 2026-10-03 — Tell Domovoi whether it has the internet

### Upgrading

1. **Pull the update and restart** as usual. No new Python packages, no
   music-image rebuild. **Flyway V020** adds `library_tracks.enrich_outcome`
   (what song recognition concluded for each track) and marks every track
   that already has a MusicBrainz id as matched; the update's backup and
   rollback cover it.
2. **Until someone answers, the internet settings don't change.** An
   existing install (the Beelink included) has no `INTERNET_ACCESS` yet.
   Unanswered, every default that follows the answer keeps its old value,
   the connectivity check dials as before, and nothing is refused. An
   admin's Home page shows one row, **"tell Domovoi whether this box has
   internet"**, until the question is answered; it opens **Settings →
   Internet**. What does change on every answer, unanswered included, is
   the set of fixes under [What changes](#what-changes): the one new piece
   of outbound traffic among them is that **the server now downloads each
   podcast show's artwork** (once per show, the first time the Podcasts
   page lists it), so phones and browsers stop fetching it from the
   publisher.
3. **Answering is one click** in Settings → Internet (admin sign-in). What
   each answer does:
   * **Yes, always:** the search helper (SearXNG, behind web answers)
     starts at once; artist names from MusicBrainz start at once; automatic
     podcast downloads start after the next restart; song recognition runs
     if there is an AcoustID key or the Shazam add-on.
   * **Sometimes:** the same, except podcast downloads stay off.
   * **No, keep everything in the house:** the server stops reaching the
     internet at once: the connectivity check stops dialing, every fetch,
     search, download and update is refused with "internet access is turned
     off for this box (Settings → Internet)", the search helper stops, a
     room playing an internet station stops and internet stations leave
     every room's queue, and transfers already running (a station in a
     browser, a model download, a podcast download) stop. After the next
     restart the news worker is off and `HF_HUB_OFFLINE=1` is set. Online
     controls are greyed with "needs internet", not hidden. Machines on
     your network (satellites, a NAS, Ollama) are still reached.

   Saving needs no restart for the answer itself; the page lists what
   follows after a restart ("restart required"). Switch back any time:
   nothing is marked broken by a refusal.
   * **Settings you set by hand stay put.** Any key already in your
     `domovoi/.env` or in the service environment wins over the answer.
     The Beelink's `.env` was copied from an older `.env.example`, so it
     likely pins `SEED_VOICE_CATALOG=true` and `LIBRARY_ENRICHER_ENABLED=true`:
     Settings → Internet shows each such key as **"set in domovoi/.env"**,
     with **"follow the answer again"**, which comments the line out of
     `.env` (never deletes it; the comment carries the date).
   * An answer in the server's environment (`INTERNET_ACCESS=` in a unit
     file) is shown read-only: put it in both units, and in
     `/etc/default/domovoi-update` (docs/LINUX_HOST.md). A fresh checkout
     can answer at bootstrap:
     `python -m domovoi.env_bootstrap --internet always|sometimes|never`
     (an existing `.env` is never touched).
4. **The search helper (SearXNG) now follows the answer.** Saving **Yes,
   always** or **Sometimes** starts the `searxng` container in the
   background (the first start downloads its image, now pinned by digest:
   `searxng/searxng:2026.5.6-a9909c497`, about 375 MB); **No** stops it; an
   unanswered box leaves it as it is. Saves are applied one after another,
   so switching Yes then No during that first download still ends with it
   stopped. Settings → Internet shows whether it is starting, running or
   couldn't start, with **start it again**. With the Linux update unit,
   every healthy update and restart also brings it in step with the
   answer, after the update is recorded as applied: the start runs in a
   unit of its own (`domovoi-searxng-start`), so a slow first download
   never holds the update open or turns it into a failure. `dev.sh` /
   `dev.ps1` start it for Yes and Sometimes. `DOMOVOI_MANAGE_SEARXNG=0`
   (in the core's environment, or in `/etc/default/domovoi-update`) leaves
   the container alone. Nothing touches it at core boot.
5. **Song recognition gets one honest second pass.** On the Beelink all
   5,232 tracks were stamped done without a MusicBrainz id — most likely
   because no working AcoustID key or Shazam add-on was ever set up (a
   personal AcoustID *user* key is refused for lookups, so it would have
   the same effect). Those tracks stay exactly as they are until a lookup
   actually answers: with an AcoustID **application** key
   (https://acoustid.org/new-application) or the Shazam add-on, the next
   song-recognition run gives them one real try. A refused key now counts
   as no key: with the Shazam add-on Shazam decides; without it the run
   stops at once, marks and requeues nothing, and the log names the key it
   needs. What the retry does to those older tracks: it adds MusicBrainz
   ids and fills tags that are empty; it doesn't rewrite a title or artist
   they already have (so a hand correction is never overwritten, and a
   name guessed from a file name stays until you edit it). A track an
   older version matched through Shazam is looked up once more too (its
   tags are kept). No SQL needed. Before installing or keeping the Shazam
   add-on on the Beelink (CPython 3.14), note that a source-built
   `shazamio` can crash at import: Domovoi now tries the import in a
   throwaway process first and skips a broken one with a log line instead
   of going down; `python -c "import shazamio"` in the venv shows which you
   have.
6. **Voices: wrong-voice clips heal by themselves.** A Microsoft voice's
   clip that an earlier version rendered with a stand-in voice (it still
   carried the Microsoft tag) is now recognised — it isn't at the 24 kHz
   Microsoft renders at — and rendered again once, the first time
   Microsoft can be reached. For a Piper voice's clips that came out in the
   robot voice, delete `~/.domovoi/sounds/voices/<voice name>` (lower case,
   e.g. `voices/ryan`) and restart the core.
7. **Whisper no longer asks huggingface.co on every start.** A model already
   downloaded loads from the local cache, so the version you have keeps
   loading — including one downloaded at a pinned revision (a cache with no
   `refs/main`, as the Windows installer will make). A missing model
   downloads once, as before, except when the answer is No: then the load
   falls back to a model that is on disk, and the Models page greys sizes
   that aren't downloaded. The `HF_HUB_OFFLINE` drop-in the internet docs
   suggested for a box without internet is no longer needed: answering No
   sets it for the core. An existing drop-in does no harm.
8. **The radio plugin is 1.3.0 and needs SDK 1.4** (it ships with the core,
   so nothing to do). Its settings still live in
   `~/.domovoi/plugins/radio.env`. **The core's own `RADIO_*` settings are
   gone:** nothing read them, and the three dashboard rows they drove
   ("Radio sampler worker", "Default sample interval", "Re-download
   cooldown") did nothing. `RADIO_*` lines left in `domovoi/.env` are
   ignored.
9. Podcast artwork is stored under `~/.domovoi/podcast_artwork`
   (`PODCAST_ARTWORK_DIR`).
10. **Updating a box answered No** (Linux update unit): an update that would
    download — new Python dependencies, or a new music player image — is
    refused before anything is touched (status `aborted`, with the reason).
    Switch to Sometimes, pull and restart, switch back to No and restart
    once more; docs/INTERNET.md → "Updating a box answered No" lists what
    the temporary Sometimes turns on.

### What changes

* **One question: "Will this Domovoi have internet?"** It is asked by
  first-run setup in the dashboard (after the admin password, with "Decide
  later"), by the Windows installer, and lives in **Settings → Internet**,
  its own tab. The answer only sets **defaults**:

  | Setting | Yes, always | Sometimes | No |
  |---|---|---|---|
  | Daily news briefing (`NEWS_ENABLED`) | on | on | off |
  | Find artists by the names people say (`MUSIC_ALIAS_FETCH_ENABLED`) | on | on | off |
  | Automatic podcast downloads (`PODCAST_FEED_POLLER_ENABLED`) | on | off | off |
  | Song recognition (`LIBRARY_ENRICHER_ENABLED`) | on with an AcoustID key or Shazam | same | off |
  | Extra voices at startup (`SEED_VOICE_CATALOG`) | off | off | off |

  Topic news (`NEWS_AUTO_FETCH`) stays a personal opt-in on every answer,
  and Microsoft voices stay your own choice: neither follows the answer.
* **"No" really keeps everything in the house.** The connectivity check no
  longer dials 1.1.1.1 at all (it reports "turned off"); every fetch that
  would leave your network is refused before a name is even looked up —
  in the server, the dashboard's server side, and plugins that use the
  SDK's HTTP client, its playback call or the shared URL check;
  `HF_HUB_OFFLINE=1` is set for the server. Machines on your network
  (satellites, the router, a NAS, Ollama) are still reached. Refusals read
  "internet access is turned off for this box (Settings → Internet)".
* **Under No, nothing of these goes out:** version check and pull (no git
  runs), plugin installs from GitHub (a zip whose Python packages are
  already installed still installs; pip runs with `--no-index`), the
  satellite media **Refresh caches** (Prepare still works from the cache),
  Ollama model installs (a registry on the house network still works, and
  the Models page lets you type one), building the music container image
  when it's missing, news feed add / re-check / **poll now** (the feed is
  never marked invalid for it), podcast search, subscribe-by-name and
  **poll now** (subscribing by RSS URL still works), registering or
  sampling a Microsoft voice, the fast-lane model download, wake-word
  training (the word is marked failed, "training needs the internet the
  first time it runs"), and the radio plugin's search, simulcast lookup,
  FCC import, internet-station play and stream. The API answers `409` with
  `X-Domovoi-Refusal: internet-off` (plugin installs keep their `422` with
  code `internet_off`). FM over an RTL-SDR keeps working, and the radio
  sampler samples only house-network streams, against your library.
* **The rooms' music players stay off the internet too.** MPD fetches a
  queued stream by itself and resumes its queue after a container restart
  (a reboot), out of sight of every check in the server. Under No no
  internet stream is handed to it (`sdk.playback.play_url`, the call the
  radio plugin and other plugins use, answers "I'm set to stay off the
  internet, so I can't play …"), and when the answer becomes No — and at
  every core start under No — a room playing one is stopped and internet
  entries are taken out of every queue. Your own music stays queued.
* **Switching to No stops transfers already running:** the radio plugin's
  browser stream, a model download (marked "cancelled: internet access was
  turned off") and a podcast episode download (back to waiting).
* **Satellites:** under No, a plugin's satellite system packages are
  installed from the satellite's local package caches only; whatever
  would need a download waits until the answer allows it.
* **Settings → Internet** also lists the enabled plugins that may reach the
  internet on their own (their manifest asks for the network; the bundled
  radio plugin follows the answer and isn't listed), and, under No, warns
  when the language model server (`OLLAMA_URL`) is outside the house or an
  Ollama cloud model is configured — both would still send your questions
  out.
* **Controls that need the internet are greyed, not hidden**, under "No",
  with "needs internet · Settings → Internet": the online settings on the
  Configuration tab (and the Microsoft choice of TTS engine), "Check for
  updates" and "Pull the latest" (restarting still works), registering,
  sampling and choosing a Microsoft voice, training a wake word, Whisper
  sizes that aren't downloaded, and the online controls on the Podcasts,
  News, Radio, Models, Plugins and Satellites pages.
* **Microsoft voices are never a silent fallback.** A Piper voice that
  failed used to make Domovoi speak through Microsoft's Edge voice, which
  sent the reply text to Microsoft. Edge now speaks only when it is the
  chosen engine (`TTS_ENGINE=edge`, or a room's voice is an Edge voice) and
  the answer isn't No. Otherwise it is Piper, then the system voice
  (espeak-ng on Linux, SAPI on Windows). On Linux, install `espeak-ng` so a
  broken Piper voice still has a voice under it.
* **Greetings stay in the right voice.** Each clip records the voice that
  actually rendered it, and is redone once the real voice works. While
  Microsoft voices can't be reached, their missing clips use the default
  Piper voice and nothing waits on Microsoft. Fewer clips go to Microsoft
  at startup: only those of Microsoft voices you actually use.
* **No Microsoft voice is added unasked.** `TTS_EDGE_VOICE` joins the voice
  list only when the extra-voices catalog is on or `TTS_ENGINE=edge`. Under
  No, no Microsoft voice is added at all. Voices already in the list stay.
* **"I checked online but couldn't find…" is only said after a search ran.**
  With the search helper stopped, web answers and "double-check that" now
  say "I can't search the web right now — my search helper isn't running"
  (and the core logs once how to start it); a SearXNG error says "I couldn't
  reach my search helper just now. Try again in a moment."; when SearXNG
  answers but every search engine behind it failed (a CAPTCHA, a timeout),
  "My search engines didn't answer just now. Try again in a moment."; a box
  answered **No** says "I'm set to stay off the internet, so I can't check
  that." The "want me to do that automatically?" offer is only made after
  a search really ran. The router's offline answer to a weather/news-type
  question, the news handler's offline lead-in, "play 97.5 FM" for a
  station that isn't loaded, and the cloud-voice refusal use the
  turned-off wording under **No** too; unanswered and plain-offline
  wording is unchanged.
* **Song recognition marks a track done only when AcoustID or Shazam
  actually answered**; a network error, a refused key or no provider leaves
  it waiting. Without a key or the add-on the startup run does nothing.
  "Enrich my library" and the Music page's button (web and phone) say why
  they can't start (internet off or down, no key, switched off, already
  running). A tag edit in the dashboard counts as a hand edit.
* **Podcasts:** "subscribe to …" promises downloads only when automatic
  downloads are on, and says when the podcast directory couldn't be
  reached instead of "I couldn't find a podcast called …"; a download cut
  off by the internet waits and retries instead of failing; one show's
  dead or slow host no longer holds up every other show's downloads (the
  round stops early only when the internet itself is gone); the poller
  skips its rounds offline.
* **Podcast artwork now comes from Domovoi.** The server downloads each
  show's artwork itself (on subscribe and at each poll; JPEG/PNG/WebP/GIF,
  at most 5 MB), and the dashboard and the Android app load it only from
  Domovoi, so phones and browsers no longer contact podcast publishers.
  Existing subscriptions get theirs the first time the Podcasts page lists
  them.
* **The video satellite's kiosk browser** starts with Chromium's background
  traffic off (`--disable-background-networking`,
  `--disable-component-update`, `--no-pings`,
  `--disable-domain-reliability`), on every answer. Video satellites pick
  it up with their next code update, at the next kiosk start.
* `.env.example` no longer pins `SEED_VOICE_CATALOG` or
  `LIBRARY_ENRICHER_ENABLED` (a fresh box that leaves the question
  unanswered gets the same values as before), documents `INTERNET_ACCESS`,
  and now asks for an AcoustID **application** key
  (https://acoustid.org/new-application) — the personal "user API key" is
  rejected by the lookup.
* **API:** `GET /v1/connectivity` adds `policy` and `reason` (`connected`,
  `offline`, `turned_off`); a turned-off transition is logged as `offline`
  with target `turned_off`. `GET /v1/admin/config` rows add
  `choice_labels`, `needs_internet`, `needs_internet_choices`,
  `internet_profile`, `follows_internet` and `set_in_environment`;
  `POST /v1/admin/config` accepts `follow_internet` and answers `followed`.
  New: `GET /v1/admin/internet` (and `GET /api/config/internet`), with
  `search_helper`, `network_plugins` and `warnings`;
  `POST /v1/admin/internet/search-helper` (and
  `POST /api/config/internet/search-helper`) runs the helper's start/stop
  again; `GET /api/config` carries `internet_access`.
  `POST /v1/admin/library/enrich` answers `409` under No and otherwise
  `{queued: false, reason: disabled | offline | no_provider | running}`
  when it can't start. New: `GET /api/podcasts/subscriptions/{id}/artwork`
  and `GET /api/podcasts/discover/artwork/{key}`; `artwork` in the podcast
  answers is now a server path or null. `GET /api/models/catalog` Whisper
  rows carry `cached`. `GET /v1/satellite-plugins/manifest` marks each
  plugin `offline` under No. The plugin SDK is **1.4.0**: `sdk.egress`,
  `connectivity.policy` / `.reason` / `.internet_allowed`; `sdk.http`
  clients refuse non-local requests under "No" with
  `egress.InternetTurnedOffConnectError` (also an `httpx.ConnectError`),
  and `sdk.playback.play_url` refuses an internet stream
  (`data.status == "internet_off"`).
* **Docs:** `docs/INTERNET.md` now explains the three answers and what each
  switches; the FAQ, troubleshooting, security, API, plugin-development,
  Linux, runbook and README pages follow it.

## 2026-10-03 — "Play" finds a name by how it sounds, asks when it isn't sure, and learns other names

### Upgrading

1. **Pull the update** (Settings → Configuration → Version → Check for
   updates → Pull the latest) and **press "Restart to apply changes"**.
2. **It installs three new Python packages**: `rapidfuzz` (MIT), `anyascii`
   (ISC) and `Metaphone` (BSD). Both the core and the dashboard load them at
   start. The update re-syncs the venv by itself because `pyproject.toml` and
   `requirements.lock` changed, and rolls back if the core does not come back
   healthy. Nothing to do by hand on the Beelink. On a box that runs Domovoi
   from a checkout by hand (a development machine), run
   `pip install -e ".[dev,real-clients,voice-profile]"` once for the
   interpreter that runs it, or the core and the dashboard stop at import.
3. **Flyway V019** adds two empty tables, `library_aliases` (the "also
   called" names) and `library_alias_lookups` (the MusicBrainz lookup's
   progress). No existing data changes. The update's database backup and
   rollback cover it as usual.
4. **Optional:** switch on the MusicBrainz lookup ([below](#the-musicbrainz-lookup-is-off-until-you-switch-it-on)).

### What changes for the people in the house

* **"Play suicide boys" plays $uicideboy$.** A spoken request is matched
  against the library by how names SOUND, before the old text search runs.
  Stylized spellings are folded (`$` reads s, `P!nk` reads Pink, `GENER8ION`
  reads generation, "3 Doors Down" and "Three Doors Down" are one name),
  then spelling and sound are compared (rapidfuzz plus Double Metaphone).
  When nothing in the library is close, the old search runs exactly as
  before, so a streaming provider still gets what you don't own.
* **"Play <artist>" plays all of that artist's songs, shuffled**, up to 500.
  "Next" and "previous" stay with the artist. **The queue ends after its
  last song**: it does not loop and is not reshuffled. An album plays in
  file order, and a song plays on its own. Each play is now recorded with
  the library song it played.
* **"Did you mean …?"** When a request is close to a name but not close
  enough to play it, Domovoi asks: "Did you mean Glass Harbor, or the album
  Moonlit Static?" You can answer "yes" (or "uh, yes", "mm-hmm", "either
  one"), "the second one", "no" (then the same request is not asked about
  again for five minutes, and the old search answers it), "no, play …" or
  just say the name. A reply about something else ("what time is it",
  "good night") drops the question and is answered as a turn of its own,
  and if nobody answers, the music that was playing comes back. The
  dashboard's play box never asks. Settings → Configuration → Library →
  "Ask 'did you mean…?'" switches it off: the middle cases then go to the
  old search.
* **"Also called": names your household gives to music.** A song, an
  artist or an album can have **any number of names** (one name → one
  thing; a band can be called "gramps", "the kindlers" and "hearth band" at
  once). Each name means ONE thing in the house: giving a name that is
  already taken asks "replace it?". Add them in the dashboard (Music → a
  song → "also called" for the song, each of its artists and its album) or
  by voice: "when I say gramps, I mean Hearth Ensemble", "what else is
  Hearth Ensemble called", "forget that name". Any paired device or room
  can add a name. Only an admin can remove or replace a name someone else
  added: a device may remove the names it added, a voice the names that
  speaker (or, for an unrecognized speaker, that room) added, and anyone
  may remove a MusicBrainz name. A name may take over another library name
  ("will now play X instead of Y"). A play through such a name then says the real target
  ("Playing Quillfeather Duo"), so the takeover is heard. The dashboard
  shows which device added a name only to paired devices and admins.
* Questions about the world that sound like the names list ("what else is
  the moon called") are answered as questions again. "When I say …, I mean
  …" about anything that is not in the library is left to the assistant.
* The old search's file-name step no longer matches inside a bracketed
  video id in a file name ("kygo" used to find a song whose file name ends
  "[FQKdHGgKygo]"). Playing a library song that the music player doesn't
  know yet now leaves what was playing alone, instead of clearing the room's
  queue.

### The MusicBrainz lookup is off until you switch it on

Settings → Configuration → Library → **"Look up other names on
MusicBrainz"** (`music_alias_fetch_enabled`). It is **off by default**, and
it applies without a restart. When it is on:

* It sends **artist names from your library** to musicbrainz.org: your
  tagged artists, and the "Artist" part of an untagged file named "Artist -
  Title". A home recording named "Grandma Edith - Happy Birthday" would send
  "Grandma Edith". It makes one request a second, through the same paced
  client as every other MusicBrainz call, and only while the server is
  online. The first run takes **about an hour and a half for ~2,000
  artists**. After that it only asks about artists added later.
* It keeps only spoken stage names that MusicBrainz lists ("Dead Mouse" for
  deadmau5, "Tec 9" for Tech N9ne), and only for an artist whose MusicBrainz
  name matches your library's spelling. Legal names, and a person's names
  that share a word with a legal name, are filtered out and never stored.
  Non-English and non-Latin names are dropped too. For a band, its other
  names are kept, and these can include members' or family names ("The
  Farriss Brothers" for INXS). A fetched name never takes a name your
  library or your household already uses.
* A fetched name shows muted in the song's "also called" list, and anyone
  can remove it. It is never fetched again.
* A MusicBrainz outage or refusal is retried later with a backoff. It is
  never recorded as "no such artist".
* Its status (no names) is on the Music → Stats card and in the core
  snapshot (`music_alias_fetch`). Turning it off stops it before its next
  request.

### Measured

On the frozen held-out set of 1,011 spoken requests, using the library as
of 2026-10-02 and the resolver alone:

* Stylized names: **60.8 %** played right without asking, **83.2 %** with a
  "did you mean", and 0.3 % played wrong.
* Ordinary names: **67.4 %** played right without asking (73.4 % of the
  requests that reach music), 86.7 % with a "did you mean".
* Requests for music that is **not in the library** play something else
  1.5 % of the time. With the old search behind it: 3.1 % on a spoken turn,
  and 3.8 % from the dashboard's play box, which never asks.
* About 8 % of requests never reach music because Whisper mishears "play"
  ("Place …", "Plate …", "PlayTech 9"). That fix is planned, not in this
  release.
* Lookups take 10 ms typically and 25 ms at the 95th percentile on a
  library of 5,232 songs (a fast desktop; expect a little more on the
  Beelink). Building the index at start takes under a second.

### Chosen for you, and not done yet

These are defaults set without asking you. Say if you want them otherwise:

* Replies say a name the way it is spoken ("Playing Suicide Boys,
  shuffled."), or the way the household's own name for it is spoken.
* Nothing is learned automatically from what you play, and only one Whisper
  hearing is used. There is no popularity ranking.
* No candidate narrowing yet. Lookup time grows with the library: about
  100 ms at the 95th percentile at 20,000 songs and 250 ms at 50,000. A
  narrowing step is planned before libraries get that big.
* "Find X in my library", "add this to my playlist" and download
  de-duplication still use the old text search. The Android app has no
  "also called" list yet.

## 2026-10-01 — Upgrading to this release, in order

Everything since the 2026-09-30 release comes in this one update: the core
stops when it is asked to, casting from the app and the dashboard works
like a remote for the room, music stays out of the microphone, the app's
player no longer freezes on a long queue, and Settings → Advanced appears
after a sign-in. There is nothing to migrate: no Flyway step and no new
dependency. The entries below say what each part does. In this order:

1. **Pick a quiet moment**: no timer or reminder due in the next five
   minutes, and nothing playing that you mind stopping for a minute.
2. **Sign in as admin** on the dashboard. Upgrading a satellite needs it.
3. **Pull the update** (Settings → Configuration → Version → Check for
   updates → Pull the latest).
4. **Press "Restart to apply changes", and expect this one stop to be
   slow.** The core that is running is still the old one, and it ignores
   the request to stop. The update waits 40 s for it, then stops it by
   force, and the update step's detail in the version panel says so
   (`still stopping after 40s: SIGKILLed by this script`). That is
   expected this once. Without the update unit, `sudo systemctl restart
   domovoi-core` waits systemd's 90 s once instead. After this, a restart
   or an update stops the core in under a second, and the satellites are
   back on the new core within about 5 s.
5. **Optional, Linux: give the core's unit the new stop settings.** The fix
   does not need them. They make systemd wait 30 s instead of 90 if a stop
   ever hangs, and let the core's helper process finish tidying up instead
   of being killed in the middle. A unit written from today's
   [LINUX_HOST.md](LINUX_HOST.md) has them already. For a unit installed
   before, run this over SSH. It takes effect at the next stop, with no
   restart needed:

   ```bash
   sudo mkdir -p /etc/systemd/system/domovoi-core.service.d
   printf '[Service]\nTimeoutStopSec=30\nKillMode=control-group\n' \
     | sudo tee /etc/systemd/system/domovoi-core.service.d/stop.conf
   sudo systemctl daemon-reload
   systemctl show domovoi-core -p TimeoutStopUSec -p KillMode
   ```

   The last line should print `TimeoutStopUSec=30s` and
   `KillMode=control-group`. `domovoi-web` can take the same
   `TimeoutStopSec=30` the same way (its own `domovoi-web.service.d`).
6. **Check it came back**: `GET /v1/admin/version` shows `running_sha` =
   `checkout_sha` = the new commit, `restart_required` false and
   `last_update.status` `ok`.
7. **Upgrade every satellite** (Satellites → the satellite → Overview →
   Upgrade satellite). Its half of keeping music out of the microphone is
   in this update ([below](#2026-10-01--music-no-longer-plays-into-the-microphone)).
8. **Install the new Android app.** The debug build
   (`android/app/build/outputs/apk/debug/app-debug.apk`) installs over the
   old app as an update. If Android refuses it (a different signature),
   uninstall the old app, install the new one, and pair the phone again.
   On its first launch, Settings → About → Problem reports also lists the
   freezes and crashes of the app it replaced, if Android kept a record of
   them.
9. **Reload every open dashboard**, the kitchen tablet included.
10. **Try it.** Play a library song on the phone, a minute in, and cast it
    to a room: the room starts on that song at that point. Press next: the
    room plays the next song you had queued, not a random one. Press
    previous: back one song. Pick "this device": the room pauses and the
    phone carries on from where the room was. Then press Restart on the
    dashboard once more: the satellites should be back within seconds.

## 2026-10-01 — Restarts and updates no longer wait 90 seconds

### What changes for the people in the house

* **A restart or an update is over in seconds.** Each one used to leave
  the house without voice control for about a minute and a half. The core
  ignored the request to stop, so Linux waited 90 s and then killed it.
  It now stops in under a second, and the satellites are back on the new
  core within about 5 s.
* **A timer or reminder that comes due as the core stops is announced once
  it is back.** One caught at the moment of the stop could be recorded as
  failed without anyone hearing it. One already cut off part-way through
  is still not repeated: nothing is announced twice.

### What changed

* Core: a stop request (systemd's SIGTERM, the dashboard's Restart, Ctrl+C)
  starts the web server's own shutdown again. The core had replaced that
  handler with its own (the cause of the 90 s), so the shutdown never ran
  in production. Every satellite now gets a clean close (1012) and
  reconnects. The shutdown has limits: `SHUTDOWN_GRACE_SEC` (5) for open
  connections, `SHUTDOWN_TEARDOWN_SEC` (10) for unloading plugins and
  stopping workers, and at `SHUTDOWN_DEADLINE_SEC` (20) the core writes
  every thread's stack to its log and exits. A stop that hangs therefore
  says where in the journal, instead of ending in a silent kill.
* Plugins: each plugin's workers are stopped together under one deadline,
  not one after another. A plugin's `on_disable` gets 5 s.
* Update script (`scripts/linux/apply-update.sh`): the stop step waits at
  most `DOMOVOI_UPDATE_STOP_TIMEOUT` (40 s) and then forces whatever is
  still stopping. Its detail says how long each unit took and how it
  ended.
* [LINUX_HOST.md](LINUX_HOST.md): both Python units get
  `TimeoutStopSec=30`, and the core gets `KillMode=control-group`, with the
  reasons and the drop-in above for units already installed.

## 2026-10-01 — Casting works like a remote for the room

### Do this once, after upgrading

**Restart the core, install the new Android app and reload every
dashboard.** Mixed versions still cast. An older app or dashboard casts
as it always did. This app against an older core gets a room that starts
the song from the top, and plays even when the phone was paused.

### What changes for the people in the house

* **A cast starts the room where you are**: on the song that is playing,
  at the point you have reached, then the rest of your queue. It used to
  start at the first song of the queue, from the top.
* **Songs saved only on the phone say they can't go to a room.** The room
  plays from the domovoi's library and can't read the phone. The app used
  to say "casting to office" while office stayed silent. A mixed queue
  sends its library songs and says how many were left out.
* **Next follows what you cast.** Next, from the app, the dashboard, the
  lock screen or by voice, plays the next song of the queue you cast. It
  used to replace that queue with one random library song. After the last
  song the room stops and says "That was the last song in the queue."
  A room playing a single song (a spoken "play …", a playlist, a song
  streamed from a search) still gets another song on next, as before.
* **Previous works while casting**: on the phone, the dashboard, the lock
  screen and the room cards on the app's Music page. It goes back one
  song; on the first song it starts that song again. It used to be greyed
  out, and on the dashboard it moved the room forward.
* **A paused phone or browser casts paused.** The room takes the queue and
  waits at that point until someone presses play ("casting to office,
  paused"). It used to start playing.
* **Changing where it plays hands over properly.** Back to "this device"
  or "this browser": the room is paused and the phone or browser carries
  on from where the room had got to. Room to room: the new room starts
  where the old one was, and the old one is paused. Playing something on
  the phone or browser while casting ends the cast and pauses the room.
  Two picks in quick succession are done one after the other, so two
  rooms never end up playing.
* **The lock screen and a headset drive the room while casting**, and the
  notification shows the room's song ("Handoff Band · in den").
* **A control that didn't work says so.** A pause the room never got used
  to count as done, so a hand-off could leave the room playing. You now
  see "couldn't pause office, it may still be playing".
* **A failed cast says which part failed.** The room's music player runs
  on the domovoi server: "its music player on the domovoi server isn't
  answering (try again in a minute; if it keeps failing, restart the
  domovoi)". Every failure used to ask whether the room's satellite was
  online, even when the satellite was fine.
* **A skip on a room whose music player is down answers in about 5 s**
  ("I couldn't reach the music player."), not 10-15.
* **Skip or previous right after a paused cast no longer kills the room's
  music player.** The player (MPD 0.23) crashed on it, came back playing an
  old song, and the skip reported an error.

### What changed

For anything else that talks to the API ([API_REFERENCE.md](API_REFERENCE.md)):

* `POST /v1/admin/music/{action}/{room_id}` (and the web's
  `/api/music/{pause|resume|stop|skip}` that pass it on) answers 200 with
  `ok: true` only when the control happened. Otherwise it answers 502 (the
  room's music player didn't answer), 409 (nothing is playing) or 503 (no
  satellite has ever connected). These used to be a 200 carrying the
  spoken apology. The 502 and 503 bodies carry `failed`: `music_player`
  or `satellite`. Dashboard pages that report a refused call (Display,
  Home, Music, Satellites) now show an error where they used to stay
  silent.
* `POST /v1/admin/music/play-tracks` (web `/api/music/play-tracks`) takes
  `start_sec` (seconds into the first song) and `start_paused`, and its
  failures carry the same `failed`. An older core ignores both fields.
* New: `POST /api/music/previous/{room_id}` (device tier).
* Skip: a room whose queue holds two songs or more uses MPD's next. The
  smart skip (another song) is kept for one-song queues. The tool model's
  "next" now goes through the same skip. "stop" then "skip" on a queue of
  several songs answers "Nothing is playing right now." (it used to start
  a random song).
* Next and previous end a person's pause. On a paused room the core now
  unpauses MPD (`pause 0`) before next or previous: MPD 0.23.12 crashed on
  a next or previous while paused on a song it had not started playing,
  which is where a paused cast leaves it.

## 2026-10-01 — Music no longer plays into the microphone

### Do this once, after upgrading

**Restart the core**, then **upgrade each satellite** (Satellites → the
satellite → Overview → **Upgrade satellite**). Either one alone already
keeps the music out of the follow-up after a question and out of a
capture a dashboard cast lands in. You need the satellite for the rest: a
music start that crosses a wake word on the way, the follow-up starting
before the question has finished, and the stop when the satellite's own
player is ever found running under a capture.

### What changes for the people in the house

* **Talking over music works again.** The satellite could start its
  music player while it was listening — after a reply that asked a
  question ("Want me to check that online?"), or when music was started
  from the dashboard or the app during a command. The microphone then
  heard the music as one long sentence: it listened until its 30 s limit
  and the lyrics were answered (dining room, 2026-09-30, "it seems to be
  listening forever").
* **The music comes back after a question nobody answers.** It used to
  stay off until the next wake word; now it returns a moment after the
  satellite stops listening for the answer.
* **The satellite listens for the answer to its question only once the
  question has finished playing.** About half the time it started about
  two seconds early, while the question was still being asked.
* Music started from the dashboard or the app while someone is talking to
  the room, or while it waits for the answer to a question, starts once
  that is over instead of at once.

### What changed

* Satellite: a `music_start` that arrives while a turn is in progress —
  the wake acknowledgement, a capture, the reply, a follow-up window, from
  the wake word until the satellite is listening for the next one — or
  during chat mode, a drop-in call or a wake-word recording, is held, not
  played, and not dropped. It plays once that is over (log: `music:
  holding <url> until the turn is over (...)`, then `music: the turn is
  over; starting <url>`). A `music_stop`, a wake word, a barge-in or the
  reply's `response_start` still drops it, and the core decides the music
  again after that reply. The 10 s wait for the speaker now counts only
  once nothing else holds the start.
* Satellite: the release a reply's `response_end` defers until its audio
  has played out (the follow-up capture, the ring) fired when the reply's
  first chunk opened the output, whenever `response_end` was handled
  first. It fires when the audio has played out. A reply whose output
  could not be opened at all (the sound card still busy) also leaves the
  speaker marked free again: it stayed marked busy, and the music after
  that turn waited out its 10 s and never came back.
* Satellite: if its own music player is ever running while a capture is
  open, it is stopped at the next frame and the log says so at ERROR
  (`this satellite's own music player is running while the microphone is
  open`), so a capture can no longer run 30 s on the satellite's own
  music. mpg123 now runs in a process group of its own and is stopped as
  a group: its `-b` output buffer runs in a forked child process.
* Core: a `music_start` is sent only to a room that can take it
  (`StreamSession.music_block`): no capture live, no turn being answered,
  no announcement on its way, not in a call, chat mode or a wake-word
  recording, and no question waiting for its answer. Otherwise it is held
  and sent once the room is free (log: `music: room=<room> is busy (...);
  its music_start waits until it is free`); a turn's own `music_start` or
  `music_stop`, a call, a stop or a closed socket supersedes it, and it is
  given up after 10 minutes. This covers the auto-resume after a reply
  that asks a question (it used to be suppressed until the next turn),
  dashboard and app casts, a drop-in's restore, and the restart after an
  announcement (it used to be dropped when a capture was open).
* A question's follow-up window closes when the answer's capture ends (the
  answer's turn decides the music), when the satellite gives up listening
  (its audio stops for 1.5 s; it sends no `utterance_end`), or 9 s after
  the question's estimated end when no capture comes.
* Older satellites and cores work with the new ones in both directions.
  An older satellite gets every core-side hold. An older core's
  auto-resume after a question still sends nothing, so with it a
  question's music comes back at the next turn, as before.

## 2026-10-01 — The app's player no longer freezes on a long queue

### Do this once, after upgrading

**Install the new Android app.** Nothing on the server.

### What changes for the people in the house

* **The player tab opens at once, whatever is queued.** With thousands of
  songs in the phone's queue it froze for seconds, taps piled up and then
  went through all at once, and with about 5,000 it crashed (the
  2026-09-30 report). Tapping a song in the phone's own music list queued
  every song on the phone, and that list shows up whenever the server
  hasn't answered for 10 s. Only the rows on screen are built now: the
  player tab, a room's queue and a playlist all work this way.
* **Playing from a long list queues up to 500 songs around the one you
  tapped** (the 50 before it, then what follows). That is also the most a
  cast can send to a room.
* **The app keeps a record when something goes wrong.** Settings → About
  → Problem reports lists the last 10 crashes, "not responding" closes and
  freezes of 3.5 s or more on that phone, each with the code that was
  running at the time. You can copy, share or clear them. They are never
  uploaded and never shown on the lock screen; they stay on the phone, out
  of its backups. A report can name the server's address.

## 2026-10-01 — Settings → Advanced shows after you sign in

### Do this once, after upgrading

**Reload every open dashboard.** Nothing else.

### What changes for the people in the house

* **Signing in shows Advanced straight away.** Settings → Configuration
  said Advanced needed an admin sign-in, and Advanced still didn't appear
  after you signed in, signed out or reloaded. The page now reads its
  settings again after every sign-in and sign-out, with no reload.
* **The page says when a reload has left you view only.** The admin
  sign-in lives only in that browser tab, by design. After a reload the
  Admin card says **signed in (view only after reload)** and offers
  **sign in again**. The note on Settings → Configuration has its own
  **sign in** button.
* **Sign out really signs out.** On a reloaded page it asks for the
  password once (the box says why), then ends the session. It used to do
  nothing there, and the next reload was signed in again. Signing out
  also takes the Advanced values and unmasked secrets off the screen at
  once.
* A save that is refused says "Save cancelled — not signed in. Your
  changes are still here." instead of a raw 403.
* The same rule covers the other pages whose content depends on who is
  signed in: the Models tab, Home's timers, and a satellite's timers.
  They read again when you sign in, sign out, pair or unpair.

## 2026-10-01 — Music library: a tidier table, and tabs that fit a phone

### Do this once, after upgrading

**Reload every open dashboard.**

### What changes for the people in the house

* **Music → Library has fewer columns.** Each song's title, artist and
  album share one cell, with the length beside the buttons. The added,
  via and source columns are gone from the table. A new info button at
  the end of each row (or a click on the row) opens the song's details,
  where they still are. On a phone the row's six buttons fold into two
  rows of three, so the title has room.
* **Tabs fit a phone.** A row of tabs wider than the screen (Music's,
  Settings') used to push the page sideways, with the last tabs off the
  edge. The tabs now scroll sideways inside their own strip, and picking
  one brings it into view. Nothing changes on a wide screen.

## 2026-09-30 — Upgrading to this release, in order

The five 2026-09-30 entries below each say what they need. Done in this
order, on an install whose dashboard **Restart** applies updates (the
Linux update unit, 2026-09-25 below), nothing is missed and the satellites
are silent for as short a time as possible.

1. **Pick a quiet moment**: no timer or reminder due in the next ten
   minutes. One that comes due during the update is announced when the
   satellites reconnect, and a satellite not yet upgraded plays it as
   silence while the server records it as spoken.
2. **Sign in as admin** on the dashboard (upgrading a satellite needs it).
3. **Turn on strict satellite pairing** if it is off (Settings →
   Configuration → Advanced → Security → Strict satellite pairing, or
   `SATELLITE_PAIRING_STRICT=true` in `.env`).
   It takes effect at the restart in step 5. With it off, any device on
   the network that brings a token under a new room name is paired on
   trust and hears every room's reminders, words included.
4. **Pull the update** (Settings → Configuration → Version → Check for
   updates → Pull the latest). `GET /v1/admin/version` then shows the new
   `checkout_sha` and the old `running_sha`.
5. **Press "Restart to apply changes".** The update run backs up the
   database, stops the web and the server, **runs Flyway (V017 for the
   chat, V018 for the timers)**, starts both and checks them; if anything
   fails it puts the old code and database back. No separate
   `docker compose run --rm flyway` is needed (running it is harmless).
   Without the update unit: run Flyway (`docker compose run --rm flyway`
   from `domovoi/`), then restart the server and the web.
6. **Check it came back**: `GET /v1/admin/version` shows `running_sha` =
   `checkout_sha` = the new commit, `restart_required` false, `bad_sha`
   null and `last_update.status` `ok`, with two more migrations than
   before. The server's journal (`journalctl -u domovoi-core --since "15
   min ago"`) shows `Whisper: short-window decoding ready`, and none of:
   `timer_fires missing — run Flyway (V018)`, `The tool router's prompt is
   N tokens`, `streams no tool calls (needs 0.9.0)`. `room <x> runs
   satellite code without the announcement fix` shows once per satellite
   until the next step.
7. **Upgrade every satellite, straight away** (Satellites → the satellite →
   Overview → Upgrade satellite). Until then a satellite plays
   announcements that reach it after a reconnect as silence, and the
   satellite halves of this release (early commit, the capture clock,
   `music_failed`, "stop the timer" said over an announcement keeping the
   reply's barge-in and follow-up, only an http(s) stream reaching
   mpg123) are missing.
8. **Install the new Android app** and allow its notifications ("Timers
   and reminders"); on Android 12L or older, also allow "Alarms &
   reminders". A debug build signed with the same key installs as an
   update; if Android refuses it (a different signature), uninstall,
   install, and pair the phone again.
9. **Reload every open dashboard**, the kitchen tablet included.
10. **Try it**: a one-minute timer in one room (that room hears "Your 1
    minute timer is done.", every other room "From the …: …"; the
    dashboard shows the alert card and the phone notifies); "stop the
    timer" in that room within 30 seconds ("Okay."); an old chat thread
    opens, a new message sends and shows its details.

## 2026-09-30 — Short commands decode faster, and answers start sooner

### Do this once, after upgrading

**Restart the core.** Nothing to migrate for this part (the timers and
chat entries below need Flyway: V017 and V018).

**Check the server's Ollama** (`ollama -v`). From 0.9.0 the tool router's
call is streamed and stopped at its first word. An older one logs `Ollama
X streams no tool calls (needs 0.9.0)` once at INFO and keeps the slow,
unstreamed route: a question still waits for the tool model's whole
throwaway answer — up to about 30-45 s on a CPU — before the Q&A model
starts, so the Q&A saving below needs 0.9.0 or later.

**If you run plugins that bring tools**, raise `ollama_tool_num_ctx` to
8192 (dashboard → **Models**): with the five sibling plugins that do, the
router's prompt no longer fits Ollama's default 4096-token window. The
core's log says so after the restart (`The tool router's prompt is N
tokens ...`) when it doesn't fit.

### How to check it worked

In this order (`curl -s http://<server>:6370/v1/stats/latency?since=<restart time>`):

1. The boot log says `Whisper: short-window decoding ready`, the answer's
   `whisper.short_window` is `true`, and after a few commands
   `stt_window.short` is above `stt_window.full`. If it says
   `unavailable (ctranslate2 X)`, the installed CTranslate2 can't — every
   capture stays on the 30 s path.
2. Before upgrading any satellite: on short commands (under ~2 s)
   `capture_timing.frame_lag_first_ms` should read about the old wake
   model's reset (the capture going out in a burst after it) — that
   confirms why their early transcripts started late. `early_commit.turns`
   will mostly stay 0 for short commands until then.
3. After upgrading the satellites (the next entry): `sat_start_backlog_ms`
   about 0-60, `decode_start_ms` about 240 — and only then
   `early_commit.turns` above 0 for short commands, with
   `early_commit.watched` counting them.

### What changes for the people in the house

* A short command is transcribed about three times faster on a CPU-only
  server (small.en, 8 cores: about 0.25 s instead of 0.9 s), so a whole
  closed command can be answered without waiting out the satellite's
  silence timeout ([early commit](CPU_HOST.md#measuring-turn-latency)) —
  once the satellite is upgraded too (step 3 above).
* A transcript that matches no command and isn't a question is heard a
  second time the slower way before the language-model router gets it:
  that catches most commands the fast decode misheard. It costs about a
  second on those turns, which were headed for several seconds in the
  router anyway; a general-knowledge question ("What is the capital of
  France?") or a request for a joke is not heard twice.
* A question, a joke or a story starts to be spoken at its first sentence
  while the rest is written, instead of after the whole answer (a first
  sentence of one to three words waits for the next, so it isn't followed
  by a gap). The tool router no longer writes an answer of its own that is
  thrown away, and a plain who/why/where question or a request for a
  joke, fact, story, poem or explanation skips it altogether.
* The first spoken command after a restart no longer pays for loading
  the voice-identification model and the Piper voice (about 0.6 s each,
  and the encoder's load used to stall every room).

### What changed

* Whisper: captures of up to 9 s decode on a 10-second window when the
  model is English-only (`.en`); anything the short decode isn't sure of
  is decoded again the old way. `whisper_short_window_enabled` (on)
  applies without a restart. The boot log says `Whisper: short-window
  decoding ready …`, or `… unavailable (ctranslate2 X)` when the installed
  CTranslate2 can't, and then everything stays on the 30 s path.
  Multilingual models always keep the 30 s window. Command accuracy: the
  same on clean TTS clips; on the same clips made far-field (synthetic
  echo and noise) the 10 s window changed about 1 command outcome in 10,
  net -11 to +5 of 252 depending on the noise. Real recordings (the
  opt-in command recordings) are not measured yet.
* The second hearing, `whisper_short_window_recheck` (on, applies without
  a restart): a short-window transcript that would go to the tool router
  is decoded again on the 30 s path and that text is routed. On the same
  far-field clips it scored at or above both windows in every condition
  (e.g. 205 of 252 against 202 for the 30 s path and 191 for the 10 s
  one), for a second decode on 16-34% of command captures. Fast-path
  commands, yes/no answers, plain questions, questions about the world
  (a question mark, four words or more, nothing about the house), requests
  for a joke or a story, and early commits are never heard twice.
* `GET /v1/stats/latency` gains `stt_window` (`{short, full, rechecked}`)
  and `whisper.short_window`; each turn records `stt_window_s` (and, heard
  twice, `stt_rechecked` and `stt_recheck_ms`, with both decodes in
  `stt_ms`), and the turn log line shows `window=10s` (`window=30s/recheck`
  when heard twice).
* `early_commit` in the same answer gains `watched`, and `cut_in` now also
  counts a person who spoke again after the capture ended, as reported by
  an upgraded satellite (the next entry). For commits from older
  satellites `cut_in` is a lower bound: it only sees the first 30-90 ms.
* The tool router: its call is capped at 256 tokens only when it is
  streamed with `think=false` (Ollama 0.9.0+); otherwise — an older
  Ollama, thinking on, or the flag rejected — at 1024, since qwen3 may
  reason before its call and 256 cut it off mid-thought (a lost command).
  Streamed, any text that isn't a `<think>` block ends the call, even
  text that looks like a tool call (a real one arrives parsed).
  `calculator` and `library` are offered to every routed turn (a
  who/why/where question about the house included): withholding them
  changed the router's tool list, and the next turn re-read ~800 tokens
  (11-15 s on a CPU). `double_check` is offered again for doubt said
  outright ("that can't be right", "check online", "google it"), and a
  call whose `claim` is only the request itself ("That can't be right.")
  checks the previous answer instead of searching for those words.
* The core's warm-up logs a warning when the router's prompt plus its
  reply cap doesn't fit the tool model's context window.
* For a spoken Q&A answer, `route_ms` and `intents_log.latency_ms` now end
  at its first sentence. Q&A figures from before and after this release
  are not comparable.
* Conversation history past `session_recent_turns_cap` is cut to its newer
  half in one go (it used to slide one exchange per turn), so a model's
  cached reading of it survives between cuts. A room already over the cap
  is cut once, on its next turn.
* `GET /v1/admin/version`: a `last_update` whose durations can't be right
  (negative, not a number, or longer than the run) now reports those
  durations as `null` instead of the whole result being unreadable.
  `apply-update.sh` times its steps with bash's own clock: on Ubuntu 26.04
  `date +%s%3N` (uutils) printed 16-19 digits and produced them. A result
  file an earlier run wrote keeps its `null` durations until the next
  update overwrites it.

## 2026-09-30 — Short commands start their transcript on time

### Do this once, after upgrading

**Upgrade each satellite** (Satellites → the satellite → Overview →
**Upgrade satellite**). The fix is in the satellite's own code.

### What changes for the people in the house

* After the wake greeting a satellite should start listening sooner. It
  used to reset its wake model between the greeting and the capture, with
  whatever you had already started saying queued behind it; a short
  command ("stop", "what time is it") then reached the server in a burst,
  and its early transcript started 0.3-0.4 s late (garage and office:
  every capture of 1.95 s or less). A long command was unaffected. The
  reset measured about 0.1 s on a desktop (26-29 wake predictions' worth
  of CPU); by that ratio it is over a second on a Pi Zero 2 W, but that is
  expected, not measured: an upgraded satellite's `sat_start_backlog_ms`
  (see "How to check it worked" above) settles it.

### What changed

* Satellite: the wake model's reset reuses the noise features the model
  computed when it loaded instead of recomputing them (41 speech-embedding
  windows, about 26-29 wake-word predictions' worth of CPU, on every reset —
  after the greeting, at the start of each reply with wake-word barge-in,
  and at the start of every wait for the wake word). The model ends up in
  the same state.
* Protocol: `utterance_start` gains `backlog_ms` and `wake_ms`;
  `speech_pause`, `speech_resume` and `utterance_end` gain `sat_ms` and
  `backlog_ms` — the satellite's own clock. New fields on existing frames:
  safe with an older core either way.
* `GET /v1/stats/latency` gains `capture_timing`: when each turn's frames,
  its pause and its early transcript happened against the pace it was
  spoken at, and whose delay it was (satellite, network or server). Numbers
  only, like the rest of the answer. Rows from before this release have
  none. See [CPU_HOST.md](CPU_HOST.md#when-the-early-transcript-starts-late).
* Satellite: `[listen] speech_pause_ms` (240 by default, unchanged) sets
  how long a pause must be before the server starts transcribing; the
  dashboard shows it as **Pause that starts transcribing** under the
  advanced Listening settings. For a satellite not yet upgraded the
  dashboard shows **needs a satellite upgrade** there instead of an input,
  and the server refuses to send it: an older Pi would have written it to
  `config.toml` and restarted, then ignored it. The same goes for any
  setting a satellite doesn't report.
* Satellite: when the core ends a capture early (`end_capture`), the
  satellite keeps reading its mic — sending nothing — until its own
  silence timeout would have ended the capture, or the reply's audio may
  be playing, and reports in `utterance_end` whether the person spoke
  again (`voiced_after_end_ms`, `listened_after_end_ms`). Its
  `utterance_end` for such a capture comes that much later; nothing waits
  for it. An older core ignores the fields.
## 2026-09-30 — Timers and reminders reach the whole house

### Do this once, after upgrading

1. **Run Flyway** for **V018** — the record of every timer and reminder
   that goes off and where it was heard, and the new per-satellite
   setting. The dashboard's **Restart** runs it on an install with the
   update unit; otherwise `docker compose run --rm flyway` from
   `domovoi/`, before the restart. The same run applies **V017** first (the chat message details, see "Chat replies
   read as formatted text" below). Without V018 the
   Domovoi server logs `timer_fires missing — run Flyway (V018); timers
   still fire but nothing is recorded` once, timers still go off and still
   reach every room, but the dashboard and the phone have no history to
   show and the new setting can't be changed.
2. **Upgrade each satellite** (Satellites → the satellite → Overview →
   **Upgrade satellite**). A satellite that hasn't been upgraded plays
   *silence* for any announcement that arrives after it reconnects — and
   after a server update every satellite reconnects, which is exactly when
   a timer that came due during the update is announced. The server can't
   hear it: it records such an announcement as spoken, and logs `room <x>
   runs satellite code without the announcement fix …` each time that
   satellite connects, until it is upgraded.
3. **Install the new Android app** and allow its notifications (the app
   asks; "Timers and reminders" is its own channel in Android's settings).
4. **Check `SATELLITE_PAIRING_STRICT`** — before the restart that applies
   this release (it takes effect at a restart). If it is off (an install
   set up before 2026-09-22 keeps whatever it had), turn it on (Settings →
   Configuration → Advanced → Security → Strict satellite pairing, or
   `.env`).
   Every satellite now speaks every room's reminders, words included; a
   satellite connected with no pairing token only ever announces its own
   room's (and what is set in its room stays there), but with strict
   pairing off any device on the network that
   brings a token under a new room name is paired on trust and hears them
   all. Strict pairing makes each new satellite wait for your approval on
   the dashboard.

Every room starts announcing every room's timers and reminders straight
away: the new setting is off for every satellite.

### What changes for the people in the house

* **Every satellite announces every timer and reminder**, not only the one
  in the room it was set in. The room it was set in says it as before
  ("Your 10 minute timer is done." / "Reminder: call mom"); every other
  room says where it came from: "From the garage: Your 10 minute timer is
  done." / "Reminder from the garage: call mom". A line spoken 90 seconds
  or more late says so ("… went off 3 minutes ago").
* **"Only reminders for this device"** — a new switch per satellite (the
  dashboard's satellite Overview, and the Android app's). On: that
  satellite announces only the timers and reminders set on it. Off (the
  default): it announces every room's. The room one was set in always
  announces it. There are no quiet hours: this switch is the one control
  (a bedroom, say). Any paired phone or dashboard can change it.
* **A room that is busy or briefly offline is waited for instead of
  skipped.** A room that is answering, listening, in a drop-in call or
  still speaking is waited for — never talked over — for up to 5 minutes.
  A satellite that is offline, or reconnecting after a server restart or
  update, still announces anything that went off in the last 2 minutes
  once it is back (after a restart, the 2 minutes start when the server is
  up again). A room still misses one when: it stays busy past 5 minutes;
  it stays offline past the 2 minutes; its connection dies in the middle
  of the announcement (not repeated, so no room hears one twice); the
  text-to-speech engines fail three times; or its Wi-Fi has just dropped —
  for up to about 15 seconds the server's side of the connection still
  looks alive, the announcement "goes out" into it, is recorded as heard,
  and is not repeated when the satellite reconnects. Each of these shows
  on the dashboard's alert and Home line ("not heard in any room …") and
  in the server's journal.
* **Reminders and timers set from the app or the dashboard chat** (no
  room) are now spoken — in every room with the switch off. They used to
  be spoken nowhere.
* **"Stop the timer" right after one goes off** now just acknowledges it
  ("Okay.") and stops it being announced in rooms still waiting their
  turn — in any room that heard it in the last 30 seconds, in the room it
  was set in (for 30 seconds after it went off), and in a room whose own
  announcement of it is still waiting (the phone may have rung first).
  Said again, or in a second room after the first one said it, it is
  still "Okay." and cancels nothing. It used to cancel the timers running
  in that room — so a kitchen "stop the timer" after the garage's
  announcement cancelled the kitchen's own timer. The room a timer was
  set in still announces its own unless "stop the timer" is said there. A
  satellite that reconnects after someone said it does not announce it.
  Only a timer counts: after a *reminder* went off, "cancel the timer"
  cancels the room's timer as before. **"Cancel that reminder"** right
  after a reminder was announced (from any room) is the same "Okay." —
  every room hears every room's reminders now, and it used to delete all
  of the room's own reminders. More than 30 seconds later both cancel as
  before.
* **"Cancel the timer for pasta" cancels it only in the room you are in.**
  Add "everywhere" ("… in every room", "… in the whole house") to cancel it
  in every room; said somewhere it isn't running, the answer names the
  room. From the app or the dashboard chat it stays house-wide. "Cancel
  the timer" never deletes a reminder any more. "How long left on the
  timer" answers the room's timer first, and when a reminder is all the
  room has, it says so ("8 minutes left on your reminder: call mom.")
  instead of calling it "the call mom timer".
* **The dashboard notifies.** Every open dashboard shows a card that
  stays until dismissed (30 minutes at most) — "Timer done · garage",
  where it was heard ("not announced in any room" when no satellite was
  going to announce it); Home's "done" lines now come from that record
  instead of guessing from a row disappearing. On a phone-sized screen
  only the newest card shows, with "+N more" and "dismiss all", and the
  cards sit under any open dialog. A tablet whose live connection is
  refused (an unpaired one) checks every 30 seconds instead.
* **The phone notifies: at once while the Domovoi app is open, within
  about 15 minutes when it isn't.** Android freezes an app that is off
  screen and cuts its network, so the app can't listen live in the
  background. What the phone does:
  - **App open (on screen):** it hears of every timer and reminder the
    moment it is set in any room, and notifies the moment one goes off.
  - **App in the background or closed:** about every 15 minutes it wakes
    for a few seconds and asks Domovoi what changed. It sets a local alarm
    for every running timer and reminder it finds, drops the alarms of
    ones cancelled since, and notifies about any that went off since it
    last asked (if under 30 minutes ago), showing the time it went off.
    No permanent notification, no background service.
  - So **a timer set by voice while the phone is in your pocket rings on
    the phone on time if it is due after the phone's next check**; one
    due sooner than that shows up at the check, late. The satellites
    announce it either way.
  - A local alarm rings at its time even with the app closed or the phone
    away from home (marked "couldn't reach Domovoi to confirm" when it
    can't ask the server; a timer cancelled while the phone couldn't hear
    of it still rings there).
  - The checks keep going while the phone sleeps (Doze). What stops
    them: the app's battery usage set to **Restricted** (neither the
    checks nor the alarms run in the background) and **Force stop** (both
    stop until the app is next opened). On Android 12 with the app's
    "Alarms & reminders" access turned off, the checks pause while the
    phone sleeps and run in its periodic wake-ups. A reboot or an app
    update starts them again within a couple of minutes.
* **The phone's lock screen shows a reminder's words** unless the phone is
  set to hide sensitive notification content (Settings → Notifications →
  notifications on lock screen; "Sensitive notifications" off on a
  Pixel). With that set it shows only "Reminder · garage". A phone marked
  as a shared screen never shows the words.
* **The words of a reminder now need a paired device to read.** Open
  timer reads (the dashboard's Home on an unpaired tablet, say) still show
  every countdown, room and whether it was heard, but a reminder reads
  "reminder" on Home and "reminder (words hidden)" in a satellite's Timers
  tab (web and Android). Plain timer labels ("pasta") stay visible. The
  timer history (7 days: where each was heard, which room said "stop the
  timer") is for paired devices and a signed-in dashboard too; without
  one, only the last 10 minutes show, with where each was heard but not
  who stopped it — enough for Home and the alert cards. The details are in
  SECURITY_PRIVACY.md. The satellites themselves are sent the words (they
  speak them, and log them in their own journal).
* **Setting reminders** understands more ways of saying it: "laundry
  reminder in 5 minutes" (the task named first), "set a 10 minute laundry
  reminder", "remind me in 10 minutes to call mom", "remind me about the
  oven in 10 minutes", and a reminder with no task at all ("set a
  reminder for 10 minutes" → "Here's your 10 minute reminder." when it
  goes off). "Better reminder for 10 minutes" (Whisper's mishearing of
  "set a reminder…") no longer stores the command as the reminder's
  words. Talking *about* a reminder or a timer ("my reminder for 10
  minutes didn't go off", "you have a reminder in 10 minutes") no longer
  sets one: it is answered by the Q&A model without asking the tool
  router. "Cancel the laundry reminder" and "cancel the ten minute
  reminder" cancel that one (they used to cancel every reminder in the
  room). Absolute times ("remind me at 6 pm") still go to the language
  model, which guesses a duration.
* Every announcement pauses and resumes music in the rooms it plays in.
  Two due together in a room with music play one after the other and the
  music comes back once, after the second. **Music someone paused stays
  paused**: an announcement, or a question to the satellite, brings the
  room's player back but no longer un-pauses the song (it used to, and
  since every room hears every timer, a kitchen timer un-paused the
  office). Resume, stop or play something new ends the pause.
* **A room in chat mode holds timers and reminders back** for the whole
  conversation: its open microphone counts as listening, which is never
  talked over. One waits up to 5 minutes, then that room is skipped
  (`busy_timeout`); the other rooms announce it as usual.
* **"The timer is going off, stop it"**, "my timer is going off", "the
  timer is done", "that reminder was for the oven, stop it", "why don't
  you set a timer for ten minutes", "I have a timer going, how long is
  left": said about a timer, these still ask for something, and go to the
  tool router like any command (a stop right after one went off
  acknowledges it). Only talk that asks for nothing ("my reminder didn't
  go off") goes straight to the Q&A model.
* **"Hey jarvis, stop the timer" said over the announcement** (upgraded
  satellites): the reply can be interrupted and a question it asks is
  listened for, as with any turn. The announcement's own end used to end
  the satellite's wait for the reply.

### What changed

* Core: `domovoi/timer_delivery.py`. Each watcher tick moves every due
  timer into `timer_fires` in the same transaction that deletes it, with
  one `timer_fire_deliveries` row per room it goes to; each room is
  claimed before a frame is sent, so no room hears one twice, across
  reconnects and restarts. Fires interrupted by a restart are picked up
  again if under 10 minutes old (a room caught mid-announcement is
  recorded `failed`, not repeated). History is kept 7 days.
* Core settings (Settings → Timers & reminders, advanced, applied
  immediately): `timer_offline_grace_sec` (120), `timer_announce_busy_wait_sec`
  (45 — how long a pending follow-up question or a just-finished reply
  holds an announcement back), `timer_announce_max_wait_sec` (300),
  `timer_fire_retention_days` (7).
* Core journal: the two `timer fired…` lines are unchanged. New, one per
  room: `timer fire <id> room=<room> origin=<room> outcome=<spoken|
  interrupted|failed|offline|busy_timeout|cancelled> detail=<reason>
  waited=<s>` — WARNING for offline, busy_timeout and failed. It never
  carries the reminder's words. The old `… fired for offline room=…;
  dropping …` and `…-broadcast-… failed; dropping` lines are gone.
* Core: `StreamSession.announce` plays one announcement at a time per
  room (intercom broadcasts, `/v1/admin/announce` and plugin
  `sdk.speech.announce` included), and a wake word or barge-in cuts an
  announcement off like a reply. A first sentence that synthesizes to no
  audio at all (every TTS engine down) is now an error for every caller,
  not a silent "announcement" reported as delivered. A timer's
  announcement also steps aside for a capture, a call or a wake-word
  recording that began after its own busy check.
* Core: a satellite socket accepted with no pairing token announces only
  its own room's timers and reminders (journal: `room <x> has no pairing
  token; it announces only its own timers and reminders …`, once), and a
  timer or reminder set in such a room is announced there only.
* Core: a music restart after an announcement waits while another is on
  its way to the room (queued on the room's lock, still synthesizing, or a
  timer delivery waiting its turn there), and a capture whose audio has
  stopped arriving no longer holds it back (a follow-up nobody answered
  sends no `utterance_end`, and every later announcement in that room used
  to stop its music for good). The music_ready handshake leaves MPD paused
  in a room whose music a person paused. A drop-in ring prompt takes the
  room's announcement lock, so a timer due meanwhile waits for it.
* Core: a satellite's `utterance_end` frame counts past an hour of audio
  are ignored, and the latency summary skips a timing no float can hold
  (one such row made `GET /v1/stats/latency` fail). `music_failed` is
  logged bounded and quoted.
* Router: `tool_gate.TIMER_ACTION_RE`. Talk about a timer or reminder
  that also stops, cancels, asks how long is left, says one is going off,
  or asks for a new one is not a statement; the timer and reminder tools'
  gates follow the same rule.
* Satellite: a `response_end` that arrives during a capture with no
  `response_start` since it began (an announcement's, or the reply the
  capture cut off) no longer ends that turn's wait for its own reply, and
  a release still waiting for earlier audio to drain is dropped when a
  capture begins. A `music_start` whose `stream_url` is not an http(s)
  URL is refused (`music_failed` with reason `bad_url` to a current core)
  and never reaches mpg123's command line.
* Protocol: the satellite's `hello` gains `announce_after_session_end:
  true`; the core warns once per connection for a satellite without it.
* Core events (catalog still v1, additive): `core.timer_fired`,
  `core.timer_fire_settled`. NOTIFY channel `timer_fires_changed`.
* Android: a background sync (`alerts/TimerSync.kt`), a self-rescheduling
  alarm about every 15 minutes, not a service. Each check is
  `GET /api/timers/fires?since_id=…` (plus `?limit=1` when that finds
  nothing new) then `GET /api/timers`, with the household token — so
  every phone with the app installed makes two or three small reads of
  the web backend every quarter hour, around the clock — **on Wi-Fi or
  Ethernet only**: the check sends the household token, usually as plain
  http, and off Wi-Fi it asks nothing (the timers already on the phone
  still ring). It is an exact
  alarm under the exact-alarm permissions the timers already hold (only
  an exact alarm gets the network while the phone sleeps), restarted
  after a reboot and after an app update. No new permission.
* Web: `GET /api/timers` gains `fires` (null without V018); new
  `GET /api/timers/fires`, `GET`/`PUT /api/satellites/{room}/timer-announcements`
  (PUT is device tier); satellite rows gain `timers_own_only`; realtime
  channel `timer_fires`. See docs/API_REFERENCE.md.
* Retiring a room (`DELETE /v1/admin/satellites/{room}`) also clears its
  "Only reminders for this device" setting; its timer history stays.

## 2026-09-30 — The first play after a room's music daemon restarts is no longer silent

### Do this once, after upgrading

**Restart the core** so it applies the core fix, then **upgrade each
satellite** (Satellites → the satellite → Overview → **Upgrade
satellite**). Either one alone already fixes the silent first play. You
need both to get the lights and the dashboard right when a stream still
can't play.

### What changes for the people in the house

* **The first cast or "play …" in a room after its music daemon restarted
  now plays.** Before, the satellite showed the music lights and stayed
  silent until the next wake word. The song had started to nobody, about
  5 s in (office, 2026-09-30).
* When a stream really can't be reached, an upgraded satellite tries for
  about 8 s, then **turns the music lights off**. With a current core,
  that room's music is also paused, so the dashboard no longer says
  "playing" over a silent room. The next turn tries again from the same
  place in the song.
* **Adding to the queue of a quiet room starts it.** It used to report
  "started" and play nothing.

### What changed

* Core: every `music_start` now goes through one helper,
  `streaming.send_music_start`. Before sending the frame, the helper checks
  that the room's stream answers `HTTP 200`. A TCP connect alone isn't
  enough, because Docker's port proxy accepts it even while MPD refuses.
  If the stream isn't serving and MPD is paused, the helper opens it with
  `pause 0` and `pause 1`, which plays nothing and leaves the song at 0:00.
  It then probes again, for up to `MUSIC_STREAM_READY_TIMEOUT_SEC` (3 s). A
  stream that's already up costs one local request. MPD opens its output
  only once it actually plays, and every start leaves MPD paused first. So
  until now, a daemon that hadn't played since it started had no stream at
  all when the satellite connected.
* Satellite: a stream that refuses the connection, or accepts it and
  closes, is retried with backoff (0.25, 0.5, 1 then 1.5 s) for up to 8 s.
  A `music_stop`, a newer `music_start`, a wake word or a response cancels
  the retries. When the satellite gives up, it logs `music: giving up on
  <url> after N attempt(s): <reason>` and sets its lights from "music" to
  idle. If the lights are showing something else by then, it leaves them.
  An exit in the first second always counts as "not up yet", even when
  `[music] prime_sec` is set lower (`music_ready` still goes out at
  `prime_sec`).
* Satellite: a `music_start` that is still waiting for the reply to
  finish playing (up to 10 s) is dropped when a `music_stop`, a wake word
  or a barge-in lands first. Before, music could start over the new
  capture. A newer `music_start` does not drop it; the newer one takes
  over.
* Core: an announcement no longer waits for its room's music to restart.
  The restart runs on its own, bounded by `MUSIC_STREAM_READY_TIMEOUT_SEC`
  plus 2 s. So a broadcast (intercom, `POST /v1/admin/announce`,
  `sdk.speech.announce`, a house-wide timer) reaches every room without
  waiting on one room's stream. The restart sends nothing if the room
  stopped, a turn or a drop-in started there, or a later announcement
  replaced it. While another announcement is on its way to that room
  (queued, still synthesizing, or a timer waiting its turn there) it
  waits, and the last one's end brings the music back — see the timers
  entry above.
* Core: a voice "play" records the room as playing before it waits on the
  stream, so a wake word during that wait no longer leaves the new song
  paused with nothing to resume. A voice "stop" forgets the room before
  it sends `music_stop`.
* Core: the provisioner's wait for a room's MPD control port now reads
  MPD's `OK MPD` greeting. A bare TCP connect passed at once through
  Docker's port proxy, even while the daemon wasn't listening yet.
* Protocol: `ready.features` adds `music_failed`, and a new client → server
  frame `music_failed {stream_url, reason, attempts}` is sent only to a core
  that lists it. On that frame the core pauses the room's MPD, unless a
  newer `music_start` is pending or the room has stopped.
* Older satellites and cores work with the new ones in both directions. An
  older satellite gets the core-side fix. An older core gets the
  satellite's retries: its music_ready fallback opens the stream after
  about 5 s, and the next retry connects. An older core never receives
  `music_failed`.
* `POST /v1/admin/music/queue/{room}/add` to a stopped, empty room now
  starts the queue paused on the first added track and runs the normal
  handshake. It used to call `pause 0`, which does nothing on a stopped
  MPD.

## 2026-09-30 — Chat replies read as formatted text, with their details

### Do this once, after upgrading

**Run Flyway** for **V017** (`chat_messages.stats` and
`chat_messages.device_id`; the timers entry above needs the same run for
V018). The dashboard's **Restart** runs it on an install with the update
unit; otherwise `docker compose run --rm flyway` from `domovoi/`, before
the restart. The dashboard's and the app's
chat read and write both columns: without V017 a thread's messages don't
load and a new message isn't sent. **Install the new Android app** for its
half.

### What changes for the people in the house

* Chat replies (dashboard and Android app) render the Markdown the models
  write: headings, lists, bold and italic, code, quotes, links and tables.
  The dashboard shows raw HTML in a reply as text, and images as their alt
  text.
* Every chat message has a time stamp, and the model beside a reply. A
  message's menu ("more" button, right-click or a long press) copies it or
  shows its details: who sent it and from which device, the model and why
  it was chosen, how long the reply took, tokens, speed, why it stopped and
  how much of the thread the model saw. Messages from before this release
  have no stats and no device.
* Home no longer has a "play" button on a quiet room (dashboard and app).
  A room that is playing or paused keeps pause, resume and stop; starting
  something in a room is done from Music, which picks the room and the
  track together.
* The Android app shows local media when its saved server hasn't answered
  for 10 seconds (away from home, say), naming the server as offline, and
  returns to the same screen the moment the server answers. An unpaired
  phone whose live connection is refused stays on the server.

### What changed

* V017: `chat_messages.stats` (JSONB, assistant rows) and
  `chat_messages.device_id` (TEXT, user rows); `IF NOT EXISTS`.
* Web: chat messages gain `stats`, `device_id` and `device_name`; a send
  takes an optional `device_id`, kept only when it is a well-formed device
  id (it is self-asserted, like the queue's). See docs/API_REFERENCE.md.
* Core: `clients/ollama.chat_stream` takes an optional `stats` dict it
  fills from Ollama's final chunk; existing callers are unchanged.
* The dashboard's HTML sanitiser no longer escapes an entity it has
  already produced: an ampersand in a document preview or a chat reply
  reads "&", not "&amp;".

## 2026-09-29 — The satellite acknowledges the wake word, then listens

### Do this once, after upgrading

**Upgrade each satellite** (Satellites → the satellite → Overview → **Upgrade satellite**). The
change is in the satellite's own code, so a satellite that hasn't been
upgraded keeps doing what it did — the greeting over the capture, with the
core's greeting-echo filter behind it as before. Its Settings tab shows the
new **Wake acknowledgement** as "(not set)", and it would ignore a value
saved there: upgrade first, then choose.

Nothing else. An upgraded satellite keeps what its `[greeting] enabled`
chose — on means the spoken greeting, off means the light only — until you
pick a mode.

### What changes for the people in the house

* **Wait for the greeting.** The spoken greeting ("Yes?", "What's up?")
  now plays to its end *before* the satellite listens, and whatever is said
  over it is not heard — by design: the satellite can no longer hear, and
  answer, its own greeting (office, 2026-09-28: "Back so soon?" routed as
  a command). It puts the clip's length in front of every command: about
  1.2 s with a Piper voice, about 2 s with an Edge voice, whose clips end
  in ~0.85 s of silence (measured on the shipped bank, 2,184 clips).
* **Three choices per satellite**, on the dashboard as **Wake
  acknowledgement** under Wake word, or `[wake] ack_mode` in the Pi's
  `config.toml`: *Spoken greeting, then listen* (the default), *Short
  chime, then listen* (about 0.5 s), *Light only, listen right away*.
* The spoken greeting now works on a **2-Mics HAT** too — it no longer
  needs echo cancellation. A HAT satellite that had it switched off keeps
  it off until you choose otherwise.
* When music was playing, the spoken greeting is still skipped; the chime
  plays.

### What changed

* Satellite: `[wake] ack_mode = "greeting" | "chime" | "none"`. The player
  (mpg123 for a greeting, `aplay` for the chime — both through
  `[music] alsa_device`, both scaled by `[playback] gain`) is waited out
  with a 4 s cap (1.5 s for the chime), then the mic frames it overlapped
  and a 210 ms tail are dropped. Each wake logs `wake ack: <clip> played N
  ms; … listening N ms after the wake word`. The chime is synthesized on
  the Pi (`satellite/chime.py`) — nothing to sync. `aplay` is in
  `alsa-utils`, which the provisioning steps already install.
* Satellite: `[greeting] reply_wait` and the endpointing that ignored
  frames under a playing greeting are gone with the overlap they existed
  for; a current satellite never sends `exit_reason:
  "no_speech_after_greeting"`. `[greeting] enabled` is read only while
  `ack_mode` is unset. The dashboard drops both fields.
* Satellite: a dashboard save the satellite could not start with (a value
  its code doesn't know) is refused before anything is written, instead of
  being written and crash-looping the service.
* Protocol: `utterance_end` and `speech_pause` gain an optional
  `ack_before_capture: true`, sent with `greeting_played` / `greeting_clip`
  when the acknowledgement finished before the capture opened. For such a
  turn the core still strips a leading greeting copy, but never drops the
  turn as greeting-only — "Yes." after the greeting "Yes?" is the person
  answering. Turns from older satellites are screened exactly as before.
  New fields on existing frames: safe with an older core either way.
* `GET /v1/admin/satellite/{room_id}/config` (and its `/api/satellites/…`
  proxy): each field gains `choice_labels` (null unless set). The
  dashboard and the Android app show the labels.

## 2026-09-25 — Restart can apply an update, and undo it

### Do this once, after upgrading (Linux, optional)

Nothing changes until you install `domovoi-update.service`: without it,
**Restart to apply changes** bounces `domovoi-core` and `domovoi-web`
exactly as before. To have it also back up the database (and
`domovoi_test`, which plugin migrations write to as well), re-sync
dependencies, rebuild the MPD image, run Flyway, health-check, check that
every plugin that loaded before still loads, and roll back on failure,
pull this release, **don't press Restart**, and run one command over SSH:

```bash
sudo bash /opt/domovoi/scripts/linux/install-update-unit.sh --apply
```

It checks the box first and stops, changing nothing, if the box can't take
the unit (`--dry-run` shows what it would do). It records the SHA the core is still
running as the rollback baseline, which is why it has to run **before**
anything restarts onto this release. Then it installs the sudoers grant
(checked with `visudo`) and the unit, and `--apply` runs the first update
and prints its result. [LINUX_HOST.md, "One-time upgrade for existing
installs"](LINUX_HOST.md#one-time-upgrade-for-existing-installs) says what
it checks and keeps the manual steps.

### What changed

* `docker-compose.yml` gives `postgres` `restart: unless-stopped`. The next
  `docker compose up -d postgres` (a `domovoi-db` restart, a reboot,
  `dev.sh`/`dev.ps1`) recreates the `domovoi-postgres` container once to
  pick it up: a few seconds without the database, data volume kept.
* `GET /v1/admin/version` gains `restart_mode`, `last_update` and
  `bad_sha`; `POST /v1/admin/version/check` gains `upstream_sha`;
  `POST /v1/admin/version/pull` gains `prev_sha` and records it in
  `~/.domovoi/update/prev_sha`. Existing fields are unchanged.
* With the unit installed, the version panel shows the last update's
  result, and stops offering a pull while upstream still points at a
  commit that was rolled back.

## 2026-09-25 — a deploy reaches the browser

### Do this once, after upgrading

**Reload the dashboard once on each browser and phone.** Plain Ctrl-R
(Cmd-R, or pull-to-refresh) is enough — not a hard refresh, and nothing to
clear. Every release after this one arrives on an ordinary open: a
bookmark, a typed address, a restored pinned tab or a reload, whichever
comes first.

Why it is needed exactly once, stated plainly rather than papered over: the
copy of the dashboard page your browser is holding right now was stored
under a regime that sent no `Cache-Control` header at all. A copy with no
explicit freshness is reused on the browser's own judgement (RFC 9111
§4.2.2, about 10% of the age since it was last modified) **with no request
to the box**, and there is nothing a server can say to a request that is
never made. The reload is what makes that browser ask once; the answer it
gets then carries the header that makes every future release arrive by
itself.

If you would rather not, nothing breaks — that browser picks the release up
when its heuristic window lapses, which on this box is hours rather than
days.

Measured on the real dashboard in a real headless Chrome, all four ways a
person arrives, with and without a service worker:
`functional-testing/plan-20260922/reach-real-20260925/`.

### What changed

* The static mount answers a conditional request for the PAGE itself,
  against the bytes it is about to send. It used to let Starlette answer
  from the file's size and mtime — and a release does not change
  `index.html`, so that was always the ETag the browser already held. The
  page came back `304`, with no versioned asset URLs in it, on the first
  reload and on the tenth.
* **The dashboard now tells you when it is behind.** A tab that was already
  open when a deploy landed has stopped asking for anything, so no header
  can reach it. The page carries a build id and compares it against
  `GET /api/bundle`; when they differ it shows "Domovoi has been updated…"
  with a reload button. This arms from this release onward — a page from
  before it has no such check in it, which is the other half of why the
  one-time reload above is needed.
* **A plugin upgrade reaches the browser too.** `/plugins/<slug>/static/*`
  was served with no `Cache-Control`, and the page fetched it with a
  default `fetch`, so an upgraded panel kept running the old script. Both
  halves now ask past the cache.
* The service worker's shell cache no longer grows an entry per changed
  file per deploy. It kept every copy of every file it ever fetched,
  because `activate` only deletes whole caches by name.

### Not changed, and worth knowing

* `http://<host>:6369` is not a secure context, so no service worker
  registers on the LAN install. If you open the dashboard on `localhost`
  or over TLS, a browser still running the pre-2026-09-25 worker needs two
  reloads on that one upgrade and one thereafter — the code deciding what
  to serve on the first reload is already in the browser, and no server
  change can reach it.
