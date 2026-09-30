# Release notes

Newest first. Only things an operator has to KNOW go here — a change that
needs an action, changes an answer a client depends on, or is invisible in
a way that would otherwise get reported as a bug.

## 2026-09-30 — Short commands decode faster, and answers start sooner

### Do this once, after upgrading

**Restart the core.** Nothing to migrate.

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

1. **Run Flyway** (`docker compose run --rm flyway` from `domovoi/`) for
   **V018** — the record of every timer and reminder that goes off and
   where it was heard, and the new per-satellite setting. Without it the
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
4. **Check `SATELLITE_PAIRING_STRICT`.** If it is off (an install set up before
   2026-09-22 keeps whatever it had), turn it on (Settings → Security →
   Strict satellite pairing, or `.env`).
   Every satellite now speaks every room's reminders, words included; a
   satellite connected with no pairing token only ever announces its own
   room's, but with strict pairing off any device on the network that
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
  sets one. "Cancel the laundry reminder" and "cancel the ten minute
  reminder" cancel that one (they used to cancel every reminder in the
  room). Absolute times ("remind me at 6 pm") still go to the language
  model, which guesses a duration.
* Every announcement pauses and resumes music in the rooms it plays in.

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
  token; it announces only its own timers and reminders …`, once).
* Protocol: the satellite's `hello` gains `announce_after_session_end:
  true`; the core warns once per connection for a satellite without it.
* Core events (catalog still v1, additive): `core.timer_fired`,
  `core.timer_fire_settled`. NOTIFY channel `timer_fires_changed`.
* Android: a background sync (`alerts/TimerSync.kt`), a self-rescheduling
  alarm about every 15 minutes, not a service. Each check is
  `GET /api/timers/fires?since_id=…` (plus `?limit=1` when that finds
  nothing new) then `GET /api/timers`, with the household token — so
  every phone with the app installed makes two or three small reads of
  the web backend every quarter hour, around the clock. It is an exact
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
  stopped, a turn or a drop-in started there, another announcement is
  playing there (two timers due at once: that one restarts the music when
  it ends), or a later announcement replaced it.
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
