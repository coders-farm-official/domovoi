# Domovoi core service

The core voice service package: STT → intent routing → handlers → TTS,
WebSocket streaming for satellites, persistence, background workers, and
the plugin runtime. This README is the package-internals reference —
run-it and use-it documentation lives in the repo-root `README.md` and
`docs/`; repo-wide conventions live in `CLAUDE.md`.

## Run locally (Windows, PowerShell)

The Domovoi server's `pyproject.toml` lives at the **repo root** (one level above this directory), so `pip install -e` and `python -m domovoi.main` must be run from there. Docker commands run from this directory where the compose file lives.

```powershell
# From the repo root (one level up):
cd ..

# 1. Install Python deps (Python 3.11+)
pip install -e ".[dev]"

# 2. Bring up Postgres + run migrations (switch to domovoi/ for compose)
cd domovoi
docker compose up -d postgres
docker compose run --rm flyway

# 3. Start the app natively from the repo root (needs CUDA access; not containerized)
cd ..
python -m domovoi.main
```

Or in one shot (scripts handle the cd dance for you):

```powershell
./domovoi/scripts/dev.ps1
```

## Run locally (bash / git-bash)

```bash
./domovoi/scripts/dev.sh
```

## Smoke test

```bash
# Set a timer
curl -X POST http://localhost:6370/v1/intent \
  -H "Content-Type: application/json" \
  -d '{"transcript":"set a timer for 5 minutes","room_id":"kitchen"}'

# Clock — time / date / day-of-week / year / month / tomorrow / yesterday
curl -X POST http://localhost:6370/v1/intent \
  -H "Content-Type: application/json" \
  -d '{"transcript":"what time is it","room_id":"kitchen"}'
curl -X POST http://localhost:6370/v1/intent \
  -H "Content-Type: application/json" \
  -d '{"transcript":"what is the date today","room_id":"kitchen"}'

# List registered handlers
curl http://localhost:6370/v1/handlers

# Connectivity state
curl http://localhost:6370/v1/connectivity

# Health
curl http://localhost:6370/v1/health
```

### CUDA runtime (NVIDIA hosts)

The cuBLAS/cuDNN wheels Whisper needs on CUDA live in their own `cuda`
extra, so a CPU-only or non-NVIDIA host doesn't pull ~2-3 GB it will never
load:

```bash
pip install -e ".[real-clients,cuda]"
```

Without an NVIDIA GPU, skip it and set `whisper_device=cpu` +
`whisper_compute_type=int8` — see
[docs/CPU_HOST.md](../docs/CPU_HOST.md). A `cuda` device on a machine that
can't do CUDA now fails at startup with a message naming the fix.

### Voice-profile install (Windows quirk)

Resemblyzer pins `webrtcvad>=2.0.10`, which has no Windows binary
wheels — pip would try to compile it from source and fail without MSVC
build tools installed. Our code path doesn't use webrtcvad (we pass a
numpy array directly to `embed_utterance`, which skips Resemblyzer's
preprocess pipeline), so the workaround is to install the rest of the
voice-profile deps via the extra and Resemblyzer with `--no-deps`:

```bash
pip install -e ".[real-clients,voice-profile]"
pip install --no-deps resemblyzer
```

`librosa` and `torch` are Resemblyzer's other deps and ship cp312
Windows wheels, so they install cleanly through the extra.

**On Linux this workaround is unnecessary** — `webrtcvad` compiles from
source given `python3-dev` and `build-essential`, so a plain
`pip install resemblyzer` works. See
[docs/LINUX_HOST.md](../docs/LINUX_HOST.md).

Verify it's all there:

```bash
python -c "from resemblyzer import VoiceEncoder; VoiceEncoder()"
# Should print: "Loaded the voice encoder model on cpu in <N> seconds."
```

### Voice-profile command vocabulary

Local speaker identification + enrollment. The pre-router hook in
`streaming.py` runs a Resemblyzer embedding (256-dim float32) on every
utterance, matches it against `voice_profiles` rows, and stamps
`person_id` + `presence_tier` onto the Context (and into `intents_log`
for the audit trail). The handler covers the user-facing parts.

| Ask | Behavior |
|---|---|
| "I'm Sarah" / "my name is Sarah" / "call me Sarah" / "this is Sarah" / "I am Sarah" | Asks "Nice to meet you, Sarah. Did I get that right?" — parks `pending_confirmation` in session context AND flags `expect_followup` on the response, so the user can answer "yes" / "no" without saying "Hey Jarvis" again. The next clear "yes" enrolls Sarah's embedding into `voice_profiles`. "No" cancels with no DB write. |
| "Domovoi, this is my friend Alex" / "this is Alex" / "meet Alex" | v1 acknowledges only — doesn't enroll, since the *introducer's* voice is on the wire, not the introduced person's. Full third-party enrollment lands with the LLM identity classifier follow-up. |
| "who am I" / "do you know me" / "do you know who I am" | Reads back the current speaker from `ctx.person_id`, or "I don't recognize your voice yet." |
| "forget me" / "forget my voice" / "don't save my voice" / "delete my voice profile" | Cascade-deletes the speaker's `people` row + `voice_profiles` rows; historical `intents_log.person_id` lapses to NULL via the FK's `ON DELETE SET NULL`. |

Enrollment requires:
* a session_id (programmatic `/v1/intent` calls without a WebSocket
  context can't park `pending_confirmation`, so they get a friendly
  "couldn't quite save your voice" response and skip enrollment);
* an embedding (clips below `VOICE_PROFILE_MIN_UTTERANCE_SEC` produce
  no vector — Resemblyzer needs ~1 s of voiced audio).

The handler intentionally does *not* fast-path on `i'm hungry` /
`i'm tired` and similar "I'm <adjective>" false positives — the
captured "name" is checked against a small adjective blocklist (in
`_clean_name`) and rejected before it can reach DB.

Audit query for the "stranger during empty house" case:
`SELECT at, room_id, transcript FROM intents_log WHERE
presence_tier = 'high' AND person_id IS NULL ORDER BY at DESC`.

### Intercom command vocabulary

Fan-out announcements to one or more rooms' Pi satellites. The
core service synthesizes the announcement once and injects it into each
target room's WebSocket as a normal `response_start` / PCM /
`response_end` cycle, so no satellite-side protocol changes were needed.
Music in target rooms is auto-resumed after the announcement plays
(same mechanism as the wake-word music-resume).

| Ask | Behavior |
|---|---|
| "announce: dinner is ready" | Broadcast to every connected satellite |
| "announce dinner is ready" | Same — colon optional for the bare-broadcast form |
| "announce to the house: pizza arrived" | Broadcast (recipient phrase resolves to all rooms) |
| "announce to the kitchen: someone's at the door" | Targets one room; recipient form **requires** a colon or comma |
| "announce in the garage: power is back" | Same shape, "in" instead of "to" |
| "broadcast: ..." | Alias for "announce" |
| "tell everyone the package is here" | Broadcast — bare-form alias |
| "tell the house pizza arrived" | Broadcast |

The TELL pattern is intentionally restricted to clear broadcast phrases
("the house" / "everyone" / "all rooms") — single-room "tell the kitchen
X" goes through "announce in the kitchen: X" instead, so we don't
accidentally hijack QA phrasings like "tell me a joke" or "tell the time."

LLM tool routing handles the natural-language variants the regex doesn't
catch via `{room: <id|"all">, message: <body>}`.

### Reminder command vocabulary

Reminders are timers with a non-null `message`. The TimerWatcher fires
expired reminders by calling `StreamSession.announce` on the originating
room — the message is spoken aloud, not just logged.

| Ask | Example response |
|---|---|
| "remind me to call mom in 10 minutes" | "I'll remind you to call mom in 10 minutes." |
| "remind me to take the trash out in 1 hour" | "I'll remind you to take the trash out in 1 hour." |
| "remind me to check the oven in 90 seconds" | "I'll remind you to check the oven in 90 seconds." |
| "what reminders do I have" / "list my reminders" | Reads back the next reminder + count |
| "cancel my reminder to call mom" | Substring-match cancel (Whisper rarely matches the original label exactly) |
| "cancel my reminders" | Cancels every reminder in the current room |

Absolute-time reminders ("remind me at 5pm") are deferred — they need a
real natural-language datetime parser. Use a relative duration for now.

### Voice notes command vocabulary

Quick text capture against the `voice_notes` table. Notes are stamped
with the room they were captured from and the wall-clock time.

| Ask | Behavior |
|---|---|
| "jot down: replace the air filter" | Inserts a note (colon optional) |
| "write down call the plumber" | Same — alternate verb |
| "save a note: pizza is here" | Same |
| "note that the printer is out of toner" | Same |
| "what did I jot down today" | Read back recent notes — windows: today, yesterday, this week, recently |
| "what was my last note" / "read my last note" | Most-recent single note |

### Homelab command vocabulary

Spoken status of Domovoi itself. Reads `nvidia-smi` (degrades to "no GPUs
detected" without it), Ollama's `/api/ps` for currently-loaded models,
and `clients.mpd._room_ports` for connected satellites.

| Ask | Example response |
|---|---|
| "what's domovoi doing" / "what's my domovoi doing" / "what's domovoi up to" | "GPU 12% load, 4.0 of 16.0 gigabytes VRAM used, hottest at 65 degrees. Llama3.2:3b and qwen2.5:14b loaded. 2 satellites connected." |
| "what's the server doing" / "what's my server doing" | Same |
| "how's domovoi" / "how's my domovoi" / "how is domovoi doing" | Same |
| "is domovoi busy" / "is my domovoi busy" | Same |
| "system status" / "domovoi status" / "my domovoi status" / "homelab status" / "gpu status" | Same |

Detail (per-GPU breakdown, full model list, room list) is logged at INFO
level so the user can scroll the Domovoi server terminal — the spoken
response stays compact since long status reads are tedious through TTS.

### Clock command vocabulary

The `clock` handler answers a small set of time-of-day / calendar
questions against the Domovoi server's local wall clock. Fully local
(`requires_network="no"`); no Pi-side or external state needed.

| Ask | Example response |
|---|---|
| "what time is it" / "what's the time" / "tell me the time" / "current time" / "do you have the time" / "do you know what time it is" | "It's 3:47 PM." |
| "what's the date" / "what's today's date" / "what's the date today" / "what's today" / "today's date" / "what date is it" | "It's Monday, May 4th, 2026." |
| "what day is it" / "what day is today" / "what day of the week is it" | "It's Monday." |
| "what year is it" / "what's the year" / "current year" | "It's 2026." |
| "what month is it" / "what's the month" / "current month" | "It's May." |
| "what's tomorrow" / "what's tomorrow's date" / "tomorrow's date" / "what day is tomorrow" | "Tomorrow is Tuesday, May 5th." |
| "what was yesterday" / "yesterday's date" / "what day was yesterday" | "Yesterday was Sunday, May 3rd." |

Phrasings the regexes don't catch route through the LLM tool-call path
via `{kind: time|date|day_of_week|year|month|tomorrow|yesterday}`.

### Timer command vocabulary

Plain countdown timers (no spoken message — the watcher just logs / chimes
when they fire). Reminders are a sibling of this: `timers.message IS NOT NULL`
becomes a reminder, otherwise it's a bare timer. Both share the same DB
table and watcher worker.

| Ask | Behavior |
|---|---|
| "set a timer for 5 minutes" / "timer for 30 seconds" | Creates a timer; "Timer set for 5 minutes." |
| "set a timer for 1 hour for the laundry" / "timer for 10 minutes called pasta" / "timer for 2 hours named oven" | Labeled timer; the label round-trips into cancel + status. |
| "cancel the timer" / "stop the timer" | Cancels the next-firing timer in the room. |
| "cancel the timer for pasta" / "cancel the pasta timer" / "stop the timer named oven" | Substring-match cancel by label. |
| "how much time left on the timer" / "how long left on the timer" | Reads remaining duration: "3 minutes left on the pasta timer." |

Units accepted: `second(s)`, `minute(s)`, `hour(s)`. For longer or
absolute-time-of-day forms, fall back to a reminder ("remind me to X at
5pm" — currently deferred, see ReminderHandler notes).

### Calculator command vocabulary

Deterministic local computation — arithmetic, percentages, unit
conversion, date/time math, tip/split. `requires_network="no"`: every
answer is computed locally, never delegated to Ollama (the LLM is
unreliable above two-digit arithmetic). Sits between `ReminderHandler`
and `MusicHandler` in the dispatch chain so math fast paths intercept
math questions before they can fall through to the QA path.

**Arithmetic** — `simpleeval` under the hood (never builtin `eval`),
restricted to `+`, `-`, `*`, `/`, `**`, parens, and `sqrt()`. Spoken
math vocabulary normalizes to operators:

| Ask | Behavior |
|---|---|
| "what's 47 times 89" / "calculate 5 plus 3" / "compute sqrt(144)" | Voice-friendly answer: *"That's 4,183."* with thousands separators. |
| "100 divided by 7" / "47 plus 89 minus 12" | Bare form (must start with a digit); floats round to 4 significant figures. |
| "5 squared" / "5 cubed" / "5 to the power of 3" | Powers via `**`. |
| "square root of 144" | `sqrt()`. |
| "10 divided by 0" | *"I can't divide by zero."* |
| Operand > 1e15 | Rejected before evaluation so misheard "googolplex" can't hang the math. |

**Percentages** — three shapes:

| Ask | Behavior |
|---|---|
| "47% of 89" / "what's 20% of 50" / "47 percent of 89" | `X/100 * Y` — money formatting (2 dp) when the input had `$`; otherwise 1 dp. |
| "what percent of 80 is 20" | `part/total * 100`. |
| "8% tax on $47" / "20% off $89" | Final amount AND the delta: *"That's $48.60 — $3.60 in tax."* |

**Unit conversions** — small hand-rolled table in
`clients/units.py` covering weight (oz/lb/g/kg), distance
(in/ft/yd/mi/mm/cm/m/km), volume (tsp/tbsp/cup/pint/qt/gal/mL/L, US
customary), temperature (F/C/K — affine), and time (sec/min/hr/day/
week). Plurals and abbreviations are accepted ("ounces", "feet",
"inches"). Unknown units respond *"I don't know that unit."*

| Ask | Behavior |
|---|---|
| "how many oz in 100 grams" / "how many ounces are in 100 grams" | *"That's 3.53 oz."* |
| "convert 100 grams to oz" / "convert 5 feet to cm" | Same. |
| "100 grams in oz" | Short form — must use names from the unit table, otherwise no match. |

**Date / time math** — Domovoi's local clock; a per-person timezone
preference is a future hook (no schema for it yet, so v1 is local-time
only). Holidays come from `clients/holidays.py` (Christmas, New Year's,
July 4th, Halloween, Valentine's Day; Thanksgiving is computed as the
4th Thursday of November rather than hard-coded). `next_occurrence`
always returns the next future date — December 26th asks for next
year's Christmas.

| Ask | Behavior |
|---|---|
| "90 days from today" / "in 90 days" / "5 days ago" | Forward / back date arithmetic. |
| "4 hours from now" / "in 4 hours" / "12 hours ago" | Datetime arithmetic; response includes the wall-clock time. |
| "days until christmas" / "how many days until halloween" | Days to next occurrence + the actual date. |
| "next friday" / "next monday" | Next future Monday/Tuesday/.../Sunday — "next Monday" said ON a Monday rolls forward 7 days. |

**Tip / split** — restaurant arithmetic:

| Ask | Behavior |
|---|---|
| "20% tip on $50" | *"$10.00 tip on $50.00, total $60.00."* |
| "split $200 4 ways" | *"Each person owes $50.00."* (rejects 0 people gracefully) |
| "split $200 4 ways with 20% tip" | Combined: total after tip, divided per person. |

**Anti-poach note**: the bare arithmetic regex anchors on a leading
digit, so `play 47 times by some artist` cannot match (starts with
"play") — MusicHandler later in the chain still wins for `play`-
prefixed transcripts. The `how many X in Y Z` unit regex anchors on
known unit names, so `how many songs in my library` does not poach
either; that one continues falling through to LibraryHandler.

**LLM tool routing**: the tool schema exposes a single `calculator`
tool with an `action` discriminator (`arithmetic` / `percentage` /
`unit_convert` / `date_math` / `tip_split`), mirroring the
MemoryHandler / ReminderHandler shape. `execute_from_tool` validates
and dispatches.

**Deferred** (future follow-up, NOT shipped here): stats over a list
("mean of 10, 20, 30"), geometry formulas ("area of a circle radius
5"), ratios / proportions, number-base conversions ("47 in binary").

### Music command vocabulary

Local music playback via the per-room MPD daemon. `requires_network="no"` —
all data is on the Domovoi server's filesystem, served back to the Pi over
HTTP from MPD's httpd output. Volume is per-room: each MPD instance
maintains independent state.

| Ask | Behavior |
|---|---|
| "play creep by radiohead" / "play sunny day from akon" | Search-and-play with explicit artist; falls through to an installed streaming-provider plugin if not in the local library. |
| "play creep" | Free-text play; tag search → filename search → streaming-provider search-and-stream (when a provider plugin is installed). |
| "play a song" / "play me a song" / "play something" / "play anything" / "play some music" / "play random" / "shuffle" / "shuffle my library" / "shuffle my music" / "surprise me" | **Random play** from your local library. Picks one track at random from `library_tracks` via `ORDER BY RANDOM() LIMIT 1` and plays it via MPD tag search. No external round-trip — empty library responds with a nudge to add something first. |
| "pause" / "pause the music" / "pause music" | Pause MPD (state retained, resume with "resume"). |
| "resume" / "continue" / "continue the music" | Resume from where pause left off. |
| "stop" / "stop the music" | Stop playback (loses position; "resume" will not restart). |
| "next" / "skip" / "next song" / "next track" / "skip this" / "skip it" / "skip this one" / "skip this song" / "skip this track" | **Smart skip.** When a streaming-provider search just played its top result, re-runs the stored query through the provider seam and streams the next result whose cleaned title differs from the currently-playing one. Catches the "5 different Poker Face uploads in a row" case. Falls through to playlist advance / random library pick / plain `mpd.next()` otherwise. |
| "previous" / "back" / "previous track" / "go back" | Step back in the queue. |
| "what's playing" / "what is playing" / "what song is this" / "who sang that" / "who sings this" | Reads the current title + artist. |
| "set the volume to 5" / "volume 8" | Set volume on a **1–10 knob** (1 ≈ 50%, the usable floor; 10 = max; numbers above 10 cap at 10, below 1 floor at 1). Drives the satellite's hardware output gain, so it controls Domovoi's voice **and** music together; reply echoes the number ("Volume set to 5."). |
| "volume up" / "louder" / "turn it up" | Steps up one notch on the 1–10 scale. |
| "volume down" / "quieter" / "turn it down" | Steps down one notch; never below 1 (50%). |
| "rescan my library" / "update the music library" / "scan for new music" | Triggers an MPD `update` AND re-runs the library indexer. Synchronous (~1 second on a typical library); response is past-tense with actual counts: *"Rescan complete — found 764 tracks, 12 new since last scan."* |

**How smart-skip works.** Whenever a registered streaming-search
provider plays a result (the music-fallthrough path), the query and the
playing title are stamped into session context (`last_stream_query` /
`last_stream_title` + `last_play_source`). On a skip, the handler
re-runs the query through the provider's `search`, skipping any
candidate the provider's `likely_same` heuristic says matches the
currently-playing title. First genuinely-different track plays. When
the results are exhausted, the bot says it's run out and suggests a
different search.

**Random play caveats.** The library random pick selects from
`library_tracks`, which is the core's metadata view. If a track
you expected isn't picked, run "rescan my library" to make sure MPD's
own DB knows about it. Random play also clears the stale
stream-skip keys so a subsequent "skip" doesn't accidentally jump back
into an old external search.

### Library indexer

`library_tracks` is the core's metadata view of what's on disk
in `MUSIC_DIR`. Two paths populate it:

* **Media-provider plugins** (via `sdk.library.ingest_track`) write a
  row for every track downloaded via "add to my library"
  (`added_via='voice'`).
* **`library_indexer`** (`domovoi/workers/library_indexer.py`)
  walks `MUSIC_DIR` and inserts a row for any audio file no provider
  wrote — drag-and-dropped MP3s, rsynced collections,
  pre-existing libraries (`added_via='manual'`).

Without the indexer, manually-placed files would only appear in MPD's
own tag DB; `library_tracks` would stay empty and break random play,
"how many songs", and add-to-library dedup against
hand-placed files.

**Metadata extraction**: the indexer reads ID3 / Vorbis / MP4 tags via
`mutagen` first. If title or artist is missing or empty, it falls back
to filename parsing (the same `Artist - Title.mp3` pattern every
ingest path uses — `domovoi/library_naming.py`). Worst case, a file with no tags and no separator in its
name gets indexed with `title=<bare stem>, artist=NULL` — still
findable by title-substring search.

**When it runs**:

* **Once at domovoi startup** as a fire-and-forget background
  task. First run on a populated library can take a few seconds for
  hundreds of files, microseconds for already-indexed re-runs.
* **On the voice "rescan my library" / "update the music library"
  command** — alongside the per-room MPD `update`. The voice response
  reports the count of new tracks added: *"Rescanning your library —
  added 12 new tracks to the index."*

**Idempotency**: `INSERT ... ON CONFLICT (file_path) DO NOTHING`, so
re-runs are cheap. The indexer doesn't currently detect deletions —
removed files leave stale rows. A future cleanup pass could delete
rows whose `file_path` no longer exists; not a priority since deleted
files just won't be selectable by random play (MPD search misses).

**Supported audio extensions**: `.mp3`, `.m4a`, `.mp4`, `.flac`,
`.ogg`, `.oga`, `.opus`, `.wav`, `.wma`, `.aac`, `.alac`. Anything
else in `MUSIC_DIR` (cover art, lyrics, scripts, junk) is skipped.

### Library enrichment (acoustic fingerprinting)

After the indexer populates `library_tracks` with mutagen + filename
metadata, the **enricher** (`domovoi/workers/library_enricher.py`)
identifies each track acoustically and writes back canonical metadata:

```
   library_tracks row (NULL artist / sloppy title / no MB-ID)
              ↓
   ┌──────────────────────┐
   │  AcoustID layer      │  Chromaprint fingerprint → AcoustID API
   │  (open standard)     │  → MusicBrainz recording ID + title + artist
   └──────────┬───────────┘  Score < 0.7 → discarded
              ↓ miss / threshold / no API key / no fpcalc
   ┌──────────────────────┐
   │  Shazam layer        │  Send raw audio via shazamio → Shazam's API
   │  (catalog fallback)  │  → title + artist + album
   └──────────┬───────────┘
              ↓ either match
   UPDATE library_tracks SET title = COALESCE(:new, title), ...
                              enriched_at = NOW()
```

`COALESCE` on UPDATE means the indexer's existing data is preserved
when the API returns NULL for a field. Both success and "no match"
stamp `enriched_at` so the same tracks don't get re-hammered on every
restart. To force re-attempt of no-match tracks (e.g. after AcoustID's
catalog grows):

```sql
UPDATE library_tracks SET enriched_at = NULL
WHERE musicbrainz_recording_id IS NULL;
```

**When it runs**:

* **Once at domovoi startup** as a background task chained after
  the indexer. Skipped when the connectivity probe says we're offline
  — no point burning the polite rate limit window hitting failing
  endpoints.
* **On the voice "enrich my library" / "fingerprint my music" / "tag
  my library" command** — same worker, kicked from a fast path that
  returns immediately with a queued-count + ETA. When the worker
  finishes, a result-summary announcement is fanned to the originating
  room's Pi via the same `StreamSession.announce()` plumbing reminders
  use. The user hears two utterances: the immediate "fingerprinting
  now, I'll let you know when done" reply and the deferred "library
  enrichment done — identified X of Y tracks" callback.
* **Idempotent** via `enriched_at` filter — re-runs only process
  tracks where `enriched_at IS NULL`.

**Setup (one-time)**:

1. **Get a free AcoustID API key.** Register at
   [acoustid.org/api-key](https://acoustid.org/api-key) (~30 seconds,
   no payment / approval). Set `ACOUSTID_API_KEY=<your-key>` in
   `domovoi/.env`. Without a key the enricher still works via
   shazamio alone, but you skip the open / MusicBrainz-IDs path.
2. **Install Chromaprint's `fpcalc` binary.**
   - **Windows**: `choco install chromaprint`, OR download from
     [acoustid.org/chromaprint](https://acoustid.org/chromaprint),
     extract `fpcalc.exe`, add its directory to PATH.
   - **Linux**: `apt install libchromaprint-tools` or equivalent.
   - **Verify**: `fpcalc -version` should print a version string.
   Without `fpcalc`, the enricher logs one warning and falls back to
   shazamio for that run.
3. **Already-installed Python deps**: `pyacoustid` and `shazamio` are
   in the `real-clients` extra. Reinstall with
   `pip install -e ".[real-clients]"` if you set this up before
   2026-05-07.

**Rate limiting**: The enricher waits `LIBRARY_ENRICHER_DELAY_SEC`
(default 1.0) between API calls. A first run on a 764-track library
takes ~13 minutes and runs detached — voice / music handlers stay
responsive throughout.

**What it doesn't do (deferred)**:
- MusicBrainz album lookup from the recording ID it gets back.
  Provider download pipelines have a similar partial-MB-enrichment
  path; both could be unified into a single resolver helper later.
- Cleanup of stale `library_tracks` rows pointing at deleted files —
  the indexer doesn't remove them, the enricher just stamps
  `enriched_at` and moves on. A future cleanup worker could delete
  rows whose `file_path` no longer exists.

Music auto-resumes after non-music turns: if music is playing in the
kitchen and you ask "what time is it," the wake-word capture briefly
kills mpg123 on the Pi; after the response, the core emits a
`music_start` so playback respawns automatically.

### Library command vocabulary

Metadata queries against the local music library — what's in it, when
something was added, total counts. Does NOT play music — that's
MusicHandler's job. `requires_network="no"`.

| Ask | Example response |
|---|---|
| "find creep in my library" / "search for creep in my library" / "search creep in my library" | "Found Creep by Radiohead and one other in your library." |
| "do I have creep" / "is creep in my library" / "have I got creep in my library" | "Yes — Creep by Radiohead." or "I don't have anything matching 'creep' in your library." |
| "what did I add today" / "what did I add yesterday" / "what did I add this week" / "what did I add recently" | Counts + names a few of the most-recent additions. |
| "how many songs" / "how many tracks" / "how many albums" / "how many songs do I have" / "library count" | "You have 412 tracks across 38 albums in your library." |
| "enrich my library" / "fingerprint my music" / "tag my library" / "clean up my library" / "fix my music tags" | Kicks the AcoustID + Shazam enrichment worker. Replies immediately with queued count + ETA: *"Got it — fingerprinting 764 tracks now. This'll take about 13 minutes; I'll let you know when it's done."* When the worker finishes (~13 min later for a fresh library), an announcement is fanned to the originating room's Pi: *"Library enrichment done — identified 743 of 764 tracks, 21 couldn't be matched."* See "Library enrichment" below for setup. |

Bare "find X" (no "in my library") is deliberately left for a
media-provider plugin's greedy band-900 catch-all, since "find lofi"
is more often a search request than a library check.

### External media search & download

Online search-and-stream and download-to-library are **plugin
features**: a media-provider plugin registers the
`streaming-search-provider` capability (consumed by the music
cascade/smart-skip above) and/or an acquisition fulfiller that drains
the core `media_acquisitions` queue. Core stays provider-agnostic —
"add X to my library" style requests enqueue a generic acquisition;
with no provider installed the bot answers with the graceful-absence
copy and the request waits in the queue until one is.

Downloads land in `${MUSIC_DIR}/<artist>/<title>.mp3` (Windows-strict
sanitized via `sdk.library.ingest_track`) and trigger an MPD update on
every room so the track is immediately playable without a manual
rescan.

**Add-to-library dedup** runs three checks so you don't end up with
duplicates:

1. **Exact** — the provider's `(source, source_id)` is already in
   `library_tracks` (you previously downloaded *this exact upload*).
2. **In flight** — a live acquisition with the same provider-namespaced
   `dedup_key` is already `pending`/`claimed` (unique partial index).
3. **Fuzzy title** — a track with a similar title exists in the library
   from a different upload (live vs. studio, different cover, etc.);
   the provider's handler asks "should I add it too?" through the
   standard `pending_confirmation` mechanism.

### Yes/no confirmations across handlers

Several handlers ask follow-up yes/no questions (voice profile
enrollment, a provider plugin's duplicate prompt, and more). The flow:

1. The handler stamps `pending_confirmation = {"handler": ..., "kind":
   ..., ...}` into `sessions.context` and sets `Response.expect_followup
   = True`.
2. The streaming layer adds `expect_followup: true` to the
   `response_end` frame; the satellite's mic thread skips the wake-word
   gate on the next capture.
3. The router's pending-confirmation pre-empt routes the user's reply
   directly to the handler's `handle_confirmation` method, bypassing
   the normal fast-path / LLM dispatch.
4. The pending payload is cleared after one routed turn (yes or no).

**Robustness:** step 3 fires regardless of whether step 2's WS hop
worked — even if the satellite missed the followup signal (because the
user's speech echoed off the speaker and triggered a false barge-in
mid-question, for example), the user can say "Hey Jarvis, yes" and the
parked confirmation still resolves.

### When followup capture doesn't seem to work

If the bot asks a question but the satellite doesn't auto-listen
afterwards, three diagnostic log filters distinguish the most common
causes:

```bash
# On the Pi:
journalctl -u domovoi-satellite --since "10 min ago" | grep -E "(expect_followup|barge-in|follow-up:)"
```

| What you see | What it means | Fix |
|---|---|---|
| **"expect_followup armed"** + **"capturing reply without wake-word gate"** in sequence | The mechanism is working. If the user *still* didn't get a reply captured, look for **"follow-up: no reply within timeout"** further down — they hesitated too long. | Bump `[listen] followup_pre_speech_timeout` from 8.0 to 12.0+ in `~/.domovoi/config.toml`. |
| **"expect_followup armed"** but **no** "capturing reply" line, plus **"barge-in detected"** in between | Speaker echo of the bot's own question triggered a false barge-in mid-question; the core interrupted the response and dropped the followup flag. Pi log will also show `"barge-in fired while expect_followup was armed; treating user's audio as a barge..."` | Raise `[barge_in] min_speech_ms` to 500–700 ms, or set `[barge_in] require_wake_word = true` to disable VAD-based barge entirely. Headphones eliminate the echo path entirely. |
| Neither line appears | The handler that responded didn't set `Response.expect_followup = True`. Either the handler doesn't support followups (most don't yet — voice profile and provider duplicate-confirm prompts today) or the response was the LLM Q&A fallthrough (no followup support). | Expected for handlers that don't ask questions. If you think the handler *should* support followup, file a follow-up. |

The pending-confirmation router pre-empt means yes/no confirmations
still work even when the Pi-side followup capture misfires — just say
the wake word again and the parked payload resolves. The `expect_followup`
mechanism is the ergonomic optimization, not the correctness guarantee.

### WiFi command vocabulary

Voice triggers for the satellite-side WiFi self-heal that landed after
the 2026-05-06 TTS-chop incident (`docs/INCIDENT_2026-05-06_TTS_CHOP.md`).
The autonomous watcher polls `iw dev wlan0 link` every 60 s and
reassociates when rx bitrate dips below 5 Mbit/s; these voice commands
let the user kick that flow without waiting for the next poll, or read
the current rate. `requires_network="no"` — the action is fully Pi-local;
the core only owns the routing.

| Ask | Example response |
|---|---|
| "fix the wifi" / "fix wifi" / "reset the wifi" / "reconnect the wifi" / "refresh the wifi" / "restart the wifi" / "cycle the wifi" / "bounce the wifi" / "reassociate" / "fix the network" / "fix the connection" / "fix your wifi" / "please fix the wifi" / "can you fix the wifi" | "OK, reconnecting now." — sets `pi_action="reassociate_wifi"` on the response_end frame. The Pi waits for playback to drain, then runs `sudo wpa_cli -i wlan0 reassociate`. The WS briefly drops; the satellite's reconnect logic handles it. Bypasses the watcher's 5-minute cooldown — user intent overrides safety throttle. |
| "how's the wifi" / "how's your wifi" / "how is your wifi" / "how's the network" / "how's the connection" / "what's the wifi rate" / "what's your wifi speed" / "what's the wifi signal" / "are you having wifi issues" / "are you connected to wifi" | "WiFi is connected at 43 megabits per second to Kamber Wifi 2.0." Reads from the core's per-room cache, refreshed every poll by the satellite. If no reading yet (Pi just connected): "I don't have a recent WiFi reading from this room yet. Give me about a minute and ask again." |

When the Pi has been disconnected from the Domovoi server for >30 s OR a
reassociate has failed to recover, the next wake word plays a
pre-rendered local MP3 ("Sorry, I'm having trouble reaching the network.
Try moving me closer to the WiFi router, or have someone check on me
later.") instead of streaming. The MP3 is regenerated by the core
at startup whenever `tts_edge_voice` changes
(`domovoi/canned_sounds.py`), so it always sounds like the bot's
configured voice. Provisioning step 6.7 in `satellite/PROVISIONING.md`
covers the one-line sudoers entry the satellite needs to run wpa_cli
without a password prompt.

### Double-check command vocabulary

Voice-triggered fact verification. Pulls the assistant's previous
response from session context, extracts one verifiable claim from it
via Ollama, queries SearxNG, then re-prompts Ollama with the claim +
search results to render a verdict. `requires_network="yes"` — needs
both SearxNG (locally hosted) and the public web behind it.

| Ask | Behavior |
|---|---|
| "double check that" / "double-check that" / "can you double check that" | Verifies the claim from the bot's previous response. |
| "verify that" / "verify this" / "verify it" / "fact check that" / "fact-check that" | Same. |
| "are you sure" / "are you sure about that" | Same — accepts informal verification triggers. |
| "is that right" / "is that correct" / "is that true" / "is that accurate" | Same. |
| "really?" | Same — shortest form. |

**Verdicts**: three possible outcomes, each spoken differently:
- *"Yes, that checks out. <reason>."* — CONFIRMED
- *"Actually no, that doesn't check out. <reason>."* — REFUTED
- *"I'm not sure either way. <reason>."* — AMBIGUOUS (search results were inconclusive or sources conflicted)

URLs aren't spoken — they belong in `Response.data` for the audit
trail. `intents_log` records the matched_path so a later "what did
the bot just verify?" query is straightforward.

**Graceful failures** (each with its own response, none speak a
verdict):
- No session context yet → "I don't have anything recent to double-check."
- Last response had no factual claim (e.g., "Got it, playing music") → "There's nothing in that response to fact-check."
- SearxNG returns no results → "I couldn't find anything about that."
- Offline (`requires_network="yes"` triggers `fallback_offline`) → "I can't check that right now — I don't have internet."

**Risk callout**: the verifier prompt explicitly demands "AMBIGUOUS"
for weak evidence to defend against the model rubber-stamping its
own previous claim. Parse failures also default to AMBIGUOUS rather
than picking a verdict by accident.

### Proactive web-search offer

Beyond the user-triggered "double check that" phrasing, the QA
fallthrough proactively offers a web search when it suspects its
own answer is stale. Two signals drive the offer (either one fires):

1. **Heuristic categorizer** (`domovoi/uncertainty.py`) — pattern-
   matches the question against four time-sensitive categories:
   `current_events`, `prices_finance`, `sports_scores`, `general_recent`.
2. **LLM self-doubt flag** — the QA call requests a JSON object
   (`{answer, needs_verification, candidate_claim}`) so the model can
   nominate its own answers for verification.

When either signal fires, the bot speaks its answer and tacks on
"Want me to check that online?". A yes routes via the existing
`pending_confirmation` flow to `DoubleCheckHandler.handle_confirmation`
which runs SearxNG on the **question itself** (not just a claim) and
re-prompts Ollama with the results to synthesize a cited answer.

**Per-speaker auto-search prefs** (`web_search_prefs` table):
after `AUTO_SEARCH_OFFER_THRESHOLD` (default 3) yeses in the same
category from the same known speaker (`person_id IS NOT NULL`), the
bot tacks on a meta-offer: *"want me to do that automatically from
now on?"*. A yes flips `auto_search=TRUE` and future questions in
that category skip the offer entirely — they route straight to the
SearxNG-synth path with `matched_path="auto_search"`. Anonymous
speakers never accumulate prefs and get the offer every time.

**Offline behavior**: the offer is suppressed when `ctx.online=False`
(no point offering what we can't deliver). Plain QA still runs.

### SearxNG setup

The DoubleCheckHandler depends on a locally-hosted SearxNG
instance. Brought up via the `searxng` service in
`domovoi/docker-compose.yml`:

```bash
cd domovoi
docker compose up -d searxng
```

The container is bound to `127.0.0.1:6888` (NOT `0.0.0.0`) so the LAN
can't hit it — only the core running on the same host. The
JSON API is enabled via the mounted `searxng/settings.yml`. Verify
with:

```bash
curl -s "http://localhost:6888/search?q=eiffel+tower&format=json" | head -c 200
```

(Should return a JSON object with a `results` array.) The web UI is
also reachable at `http://localhost:6888` if you want to inspect
which engines responded.

`SEARXNG_URL` is configurable via env (`.env`); default is
`http://localhost:6888`.

### Conversational chat mode (#8, opt-in)

A wake-word-triggered **open-mic conversation** backed by a self-hosted
[Letta](https://docs.letta.com) agent running on the **local** Ollama —
distinct from the default command mode, which stays on the fast-path
router. Only conversational turns hit Letta; command-mode latency is
completely untouched (the per-turn `conversational_mode` check is a cheap
session-context read). The whole feature is gated off by default
(`chat_mode_enabled=False`); flip it only after the bring-up below.

> ⚠️ **SPIKE — validate before relying on it.** The live path (real
> Letta client + tool-calling on the local Ollama) is an unproven
> integration: the Letta + Ollama + pgvector bring-up and local-model
> tool-calling reliability have NOT been validated end-to-end on Domovoi.
> Treat this as experimental. Tests exercise the STUB client only
> (`LettaStubClient`) — Letta is not running in CI. Smoke-test the
> plumbing (agent creation actually hits Ollama; tool-calls dispatch back
> to the real handlers) before enabling it in a daily-driver setup.

**1. Pull the required Ollama models** (on the Domovoi host, where Ollama
runs natively — NOT inside the Letta container):

```bash
ollama pull qwen2.5:14b        # the conversational LLM (letta_model)
ollama pull nomic-embed-text   # embeddings — REQUIRED on self-hosted Letta
```

Self-hosted Letta on Docker **requires** an embedding model for its
archival-memory search; `nomic-embed-text` is served through the same
local Ollama. Use tagged models and avoid heavy quantization (Letta's
guidance: don't go below Q5).

**2. Bring up the Letta server** (opt-in — it is deliberately NOT part of
the default `docker compose up`, and nothing in the core depends
on it):

```bash
cd domovoi
docker compose up -d letta
```

The `letta` service (in `domovoi/docker-compose.yml`) runs
`letta/letta:latest`, published on host port `6283`. It **bundles its own
Postgres+pgvector**
in the `letta-pgdata` volume and self-manages its own schema — it is NOT
pointed at the core's Flyway-owned Postgres (that would collide
with the migration-only invariant, since Letta auto-migrates its tables).
It reaches the host-native Ollama via
`OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`.

> **Windows note (host.docker.internal):** Domovoi runs natively on
> Windows, so the Letta container reaches the host's Ollama via the magic
> DNS name `host.docker.internal` (Docker Desktop provides it). The `/v1`
> suffix is required — it's Ollama's OpenAI-compatible endpoint. On Linux
> you'd instead use `--network host` + `localhost:11434/v1`.

**3. Install the SDK and flip the flag.** The Letta client dep lives in an
opt-in extra so non-chat deployments don't carry it:

```bash
pip install -e ".[chat]"   # or ".[dev,real-clients,chat]"
```

Set `CHAT_MODE_ENABLED=true` in `domovoi/.env` (or
`settings.chat_mode_enabled`). With the flag off — or `USE_STUBS=true` —
the core uses `LettaStubClient` and never touches the Letta
server, so the suite and a stock deployment run untouched.

**How it works at runtime.** Saying an enter phrase ("let's have a chat",
"can we chat", "let's talk") flips a per-session `conversational_mode`
flag (stored in `sessions.context` JSONB alongside `pending_confirmation`
— no migration) and sends the satellite a `chat_start` frame so it opens
its mic (AEC board required — see below). From then on, the streaming
layer routes each utterance to Letta (`ensure_agent` + `chat_stream`)
instead of the command router, piping the assistant's text deltas through
the same per-sentence TTS path command mode uses. An exit phrase ("that's
all", "thanks goodbye", "stop", "never mind") clears the flag, sends a
`chat_end` frame, and returns to command mode. Conversational turns log
`matched_path="chat"` in `intents_log` / `conversation_log` (the matched_path CHECKs
allow it).

**Tool bridge.** Inside a conversation, Letta can call back into the
core's **already-existing** handlers (radio, homelab, timer,
music, library, …) plus an in-network **SearxNG** lookup
(`domovoi/clients/searxng.py`, NOT Letta's built-in Exa web search).
The bridge (`domovoi/letta_tools.py`) is generic — it derives tool
defs from each handler's `tool_schema`, so future handlers are exposed
automatically. Smart-home control is out of scope. The live tool-call
execution is part of the spike — verify it dispatches correctly before
relying on it.

**Pi requirement.** Open-mic chat needs an **AEC / full-duplex** mic board
(`DeviceProfile.supports_full_duplex`) so the satellite can hear you over
its own speaker — i.e. the ReSpeaker XVF3800 USB array, not the bare HAT.
On a non-AEC board the server still enters chat mode but the satellite
declines the open mic and emits a `chat_end`. See `[chat]` in
`satellite/config.toml.example`.

### LLM fallthrough (general Q&A)

When no handler's fast-path regex matches and no tool-call dispatch
fires either, the router hands the transcript to the configured Ollama
model with the chat-style system prompt. This is the catch-all "ask me
anything" path — "tell me a joke," "what's the capital of Bolivia,"
"explain entropy in one sentence." Conversation history threads through
`sessions.context.recent_turns` so multi-turn follow-ups (within the
prompt budget) carry context.

`requires_network="no"` because Ollama runs on Domovoi itself; the
core's `online` flag only gates handlers whose work actually
needs the public internet (external search / provider plugins).

## Tests

### Test database setup (one-time)

Pytest runs against a **separate** database (`domovoi_test`) so it
never touches real data. The test conftest TRUNCATEs `library_tracks`,
`voice_profiles`, `voice_notes`, `download_jobs`, etc. before each
test — running pytest against the prod DB would wipe everything.

The conftest auto-derives the test URL from `DATABASE_URL` by appending
`_test` to the dbname and refuses to run if the resolved URL doesn't
end in `_test`. You don't have to set `TEST_DATABASE_URL` unless your
test DB lives somewhere unusual.

**Fresh installs** get the test DB created automatically by the
`db/init/01-create-test-db.sql` script that fires on Postgres' first
boot. **Existing installs** (you have a populated `pgdata` volume from
before this change) need a one-time bootstrap:

```bash
cd domovoi

# 1. Bring up Postgres + run prod migrations as usual
docker compose up -d postgres
docker compose run --rm flyway

# 2. Create the test DB on the same Postgres instance (one-time)
docker exec domovoi-postgres psql -U domovoi -d postgres \
    -c "CREATE DATABASE domovoi_test;"

# 3. Migrate the test DB to the same schema as prod
docker compose run --rm flyway-test
```

After this, every new V###__*.sql migration needs to be applied to
**both** databases:

```bash
docker compose run --rm flyway       # prod
docker compose run --rm flyway-test  # test
```

### Running the suite

```bash
# pytest runs from the repo root (where pyproject.toml lives):
pytest
```

The conftest's safety belt will refuse to run if the resolved test DB
name doesn't end in `_test` — the assertion fires before any test
touches the DB. If you ever see `Refusing to run tests against
database 'domovoi'`, your `TEST_DATABASE_URL` is misconfigured
(or absent) and `DATABASE_URL` doesn't have a dbname the auto-deriver
recognized; either unset `TEST_DATABASE_URL` and use the standard
`domovoi_test` convention, or set it explicitly to a test database.

Test breakdown:

- `tests/test_registry.py` — pure-Python contract: every handler with `requires_network != "no"` must override `fallback_offline`. No DB.
- `tests/test_timer_handler.py` — regex parsing (no DB) + DB-backed CRUD.
- `tests/test_clock_handler.py` — pure-Python: ordinal/format helpers, regex coverage for every supported phrasing, frozen-clock behavior tests for each `kind`. No DB.
- `tests/test_intercom_handler.py` — regex coverage for announce/broadcast/tell, room-phrase resolution against `_room_ports`, fan-out target population. Behavior tests use module rebinds for `_room_ports`. No DB needed.
- `tests/test_reminder_handler.py` — regex coverage + DB-backed insert/list/cancel via TimerRepository (reminders are timers with non-null `message`).
- `tests/test_voice_notes_handler.py` — regex + DB-backed CRUD against the `voice_notes` table.
- `tests/test_homelab_handler.py` — pure-Python: format helpers + behavior with mocked nvidia-smi / Ollama-API responses. No DB.
- `tests/test_voice_embedder.py` — stub-embedder determinism + unit-norm output + empty-input handling. No DB.
- `tests/test_voice_identifier.py` — pure-Python helpers (cosine, presence tier) + DB-backed match / no-match / closest-of-N / last_seen-touch behavior under the stub embedder.
- `tests/test_voice_profile_handler.py` — regex coverage (incl. adjective false-positive defense), DB-backed self-intro / confirmation / forget flows, edge case for embedding-less acknowledgment.

## Auditing conversation history

`conversation_log` is a flat row-per-turn audit table written by
the router on every routed exchange — separate from `intents_log`
(routing decisions only) and from `sessions.context.recent_turns`
(prompt-budget-bounded history threaded into Ollama). Every turn lands
exactly one row regardless of whether the path was fast / fast_offline
/ llm / llm_offline / qa / confirmation. Useful for:

* **Multi-turn debugging** — survives `sessions` table churn, so even
  if a session expires or a row vanishes mid-conversation, the audit
  trail stays intact. `SELECT user_text, assistant_text FROM
  conversation_log WHERE room_id = 'garage' AND at > NOW() - INTERVAL
  '1 hour' ORDER BY at` reconstructs the exact transcript.
* **Per-room / per-person review** — joinable to `people` via
  `person_id` (`ON DELETE SET NULL` so anonymized rows stay), and to
  `intents_log` if you want both routing + content.

Capped only by retention policy (none currently — add a vacuum worker
when the table gets unwieldy).
- `tests/test_router.py` — fast-path dispatch, offline gate, `intents_log` writes.
- `tests/test_connectivity.py` — state transitions logged once per change.
- `tests/test_integration.py` — end-to-end `POST /v1/intent` against real Postgres, timer watcher firing.

## Environment variables

All config is env-driven via `.env` (see `.env.example`):

| Var | Default | Purpose |
|-----|---------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://domovoi:domovoi@localhost:6432/domovoi` | DB connection |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server |
| `SEARXNG_URL` | `http://localhost:6888` | SearxNG (DoubleCheckHandler) |
| `CONNECTIVITY_PROBE_TARGET` | `1.1.1.1:443` | `host:port` probed every 30 s |
| `CONNECTIVITY_PROBE_INTERVAL_SEC` | `30` | Poll interval |
| `CONNECTIVITY_PROBE_TIMEOUT_SEC` | `2` | Per-probe timeout |
| `BOT_NAME` | `Domovoi` | Bot identity (used in responses) |
| `LOG_LEVEL` | `INFO` | Standard library logging level |
| `CHAT_MODE_ENABLED` | `false` | Master gate for conversational chat mode. Off → `LettaStubClient`, Letta never contacted |
| `CHAT_SILENCE_TIMEOUT_SEC` | `30.0` | Auto-exit an open-mic conversation after this much silence |
| `LETTA_BASE_URL` | `http://localhost:6283` | Self-hosted Letta server (the `letta` compose service) |
| `LETTA_TOKEN` | `domovoi-local` | SDK token = the server's `LETTA_SERVER_PASSWORD` |
| `LETTA_MODEL` | `ollama/qwen2.5:14b` | Letta LLM handle (local Ollama) |
| `LETTA_EMBEDDING_MODEL` | `ollama/nomic-embed-text` | Letta embedding handle — REQUIRED on self-hosted Letta |

## Adding a new handler

1. **Create** `domovoi/handlers/<your_handler>.py`:

   ```python
   from domovoi.handlers.base import Handler
   from domovoi.models import Context, Intent, Response

   class YourHandler(Handler):
       name = "your_handler"
       requires_network = "no"   # or "degraded" | "yes"

       tool_schema = {
           "name": "your_handler",
           "description": "What this handler does.",
           "parameters": {"type": "object", "properties": {...}, "required": [...]},
       }

       def __init__(self) -> None:
           self.fast_paths = [
               # (re.Pattern, method-on-Handler)
           ]

       async def execute(self, intent, ctx, session) -> Response:
           ...

       async def execute_from_tool(self, args, ctx, session) -> Response:
           ...

       # REQUIRED if requires_network != "no":
       async def fallback_offline(self, intent, ctx, session) -> Response:
           return Response(text="I can't do that right now — no internet.")
   ```

2. **Register** it in `domovoi/handlers/__init__.py`:

   ```python
   from domovoi.handlers.your_handler import YourHandler
   HANDLERS = [TimerHandler(), YourHandler()]
   ```

3. **Tests:**
   - `test_registry.py` automatically checks that your handler implements `fallback_offline` if needed.
   - Add handler-specific tests in `tests/test_<your_handler>.py`.

4. **Schema:** if you need new tables or columns, write a **new** migration file (`V002__<desc>.sql`, `V003__<desc>.sql`, …). Never alter V001.

## Layout

See the repo-root `CLAUDE.md` for the tree and the permanent conventions.
