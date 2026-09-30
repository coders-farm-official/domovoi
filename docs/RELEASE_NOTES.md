# Release notes

Newest first. Only things an operator has to KNOW go here — a change that
needs an action, changes an answer a client depends on, or is invisible in
a way that would otherwise get reported as a bug.

## 2026-09-30 — Timers and reminders reach the whole house

### Do this once, after upgrading

1. **Run Flyway** (`docker compose run --rm flyway` from `domovoi/`) for
   **V017** — the record of every timer and reminder that goes off and
   where it was heard, and the new per-satellite setting. Without it the
   Domovoi server logs `timer_fires missing — run Flyway (V017); timers
   still fire but nothing is recorded` once, timers still go off and still
   reach every room, but the dashboard and the phone have no history to
   show and the new setting can't be changed.
2. **Upgrade each satellite** (Satellites → the satellite → Overview →
   **Upgrade satellite**). A satellite that hasn't been upgraded plays
   *silence* for any announcement that arrives after it reconnects — and
   after a server update every satellite reconnects, which is exactly when
   a timer that came due during the update is announced. The server can't
   tell: it records such an announcement as spoken.
3. **Install the new Android app** and allow its notifications (the app
   asks; "Timers and reminders" is its own channel in Android's settings).

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
* **Nothing is dropped any more.** A room that is answering, listening,
  in a drop-in call or still speaking is waited for — never talked over —
  for up to 5 minutes. A satellite that is offline, or reconnecting after
  a server restart or update, still announces anything that went off in
  the last 2 minutes once it is back (after a restart, the 2 minutes start
  when the server is up again).
* **Reminders and timers set from the app or the dashboard chat** (no
  room) are now spoken — in every room with the switch off. They used to
  be spoken nowhere.
* **"Stop the timer" right after one goes off** now just acknowledges it
  ("Okay.") and stops it being announced in rooms still waiting their
  turn. It used to cancel the timers running in that room — so a kitchen
  "stop the timer" after the garage's announcement cancelled the kitchen's
  own timer. More than 30 seconds later it cancels as before.
* **"Cancel the timer for pasta" cancels it only in the room you are in.**
  Add "everywhere" ("… in every room", "… in the whole house") to cancel it
  in every room; said somewhere it isn't running, the answer names the
  room. From the app or the dashboard chat it stays house-wide. "Cancel
  the timer" never deletes a reminder any more, and "how long left on the
  timer" never describes one.
* **The dashboard and the phone notify.** The dashboard shows a card that
  stays until dismissed (30 minutes at most) — "Timer done · garage",
  where it was heard; Home's "done" lines now come from that record
  instead of guessing from a row disappearing. The Android app posts a
  notification from the live connection, and sets a local alarm so a
  timer still rings on the phone when it is off the home network (marked
  "couldn't reach Domovoi to confirm"; a timer cancelled while the phone
  was away still rings there).
* **The words of a reminder now need a paired device to read.** Open
  timer reads (the dashboard's Home on an unpaired tablet, say) still show
  every countdown, room and whether it was heard, but a reminder reads
  "reminder (words hidden)". Plain timer labels ("pasta") stay visible.
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
  announcement off like a reply.
* Core events (catalog still v1, additive): `core.timer_fired`,
  `core.timer_fire_settled`. NOTIFY channel `timer_fires_changed`.
* Web: `GET /api/timers` gains `fires` (null without V017); new
  `GET /api/timers/fires`, `GET`/`PUT /api/satellites/{room}/timer-announcements`
  (PUT is device tier); satellite rows gain `timers_own_only`; realtime
  channel `timer_fires`. See docs/API_REFERENCE.md.
* Retiring a room (`DELETE /v1/admin/satellites/{room}`) also clears its
  "Only reminders for this device" setting; its timer history stays.

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
