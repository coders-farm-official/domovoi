# Release notes

Newest first. Only things an operator has to KNOW go here — a change that
needs an action, changes an answer a client depends on, or is invisible in
a way that would otherwise get reported as a bug.

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
