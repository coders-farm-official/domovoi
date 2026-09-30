# Release notes

Newest first. Only things an operator has to KNOW go here — a change that
needs an action, changes an answer a client depends on, or is invisible in
a way that would otherwise get reported as a bug.

## 2026-09-30 — Short commands decode faster, and answers start sooner

### Do this once, after upgrading

**Restart the core.** Nothing to migrate, and nothing else to change.
Check the server's Ollama (`ollama -v`): from 0.9.0 the tool router's
call is streamed and stopped early; an older one logs `Ollama X streams no
tool calls (needs 0.9.0)` once at INFO and routes as before.

### What changes for the people in the house

* A short command is transcribed about three times faster on a CPU-only
  server (small.en, 8 cores: about 0.25 s instead of 0.9 s), so a whole
  closed command can now be answered without waiting out the satellite's
  silence timeout ([early commit](CPU_HOST.md#measuring-turn-latency)).
* A question, a joke or a story starts to be spoken at its first sentence
  while the rest is written, instead of after the whole answer. The tool
  router no longer writes an answer of its own that is thrown away, and a
  plain who/why/where question or a request for a joke, fact, story, poem
  or explanation skips it altogether.
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
  Multilingual models always keep the 30 s window.
* `GET /v1/stats/latency` gains `stt_window` (`{short, full}`) and
  `whisper.short_window`; each turn records `stt_window_s`, and the turn
  log line shows `window=10s`.
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

* After the wake greeting a satellite starts listening about 1-1.5 s
  sooner on a Pi Zero 2 W. It used to spend that long resetting its wake
  model between the greeting and the capture, with whatever you had
  already started saying queued behind it; a short command ("stop", "what
  time is it") then reached the server in a burst, and its early
  transcript started 0.3-0.4 s late (garage and office: every capture of
  1.95 s or less). A long command was unaffected.

### What changed

* Satellite: the wake model's reset reuses the noise features the model
  computed when it loaded instead of recomputing them (41 speech-embedding
  windows, about 24 wake-word predictions' worth of CPU, on every reset —
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
  advanced Listening settings.

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
