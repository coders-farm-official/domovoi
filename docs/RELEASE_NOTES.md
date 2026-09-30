# Release notes

Newest first. Only things an operator has to KNOW go here — a change that
needs an action, changes an answer a client depends on, or is invisible in
a way that would otherwise get reported as a bug.

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
  An exit in the first 0.5 s always counts as "not up yet", even when
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
  replaced it.
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
