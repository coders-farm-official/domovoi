# Frequently Asked Questions

Honest answers to the questions people actually ask. For a term you don't recognize, see the [Glossary](GLOSSARY.md). When something's broken rather than confusing, head to [Troubleshooting](TROUBLESHOOTING.md).

- [Is my voice data leaving my network?](#is-my-voice-data-leaving-my-network)
- [What touches the internet, and how do I turn each thing off?](#what-touches-the-internet-and-how-do-i-turn-each-thing-off)
- [What hardware do I need?](#what-hardware-do-i-need)
- [Can it run without a GPU?](#can-it-run-without-a-gpu)
- [Does it work offline?](#does-it-work-offline)
- [How do I add rooms?](#how-do-i-add-rooms)
- [Can another device on my network pretend to be one of my rooms?](#can-another-device-on-my-network-pretend-to-be-one-of-my-rooms)
- [Can I rename the assistant or change the wake word?](#can-i-rename-the-assistant-or-change-the-wake-word)
- [What are plugins, and are they safe?](#what-are-plugins-and-are-they-safe)
- [Can I use it with Home Assistant?](#can-i-use-it-with-home-assistant)
- [Does it support multiple people?](#does-it-support-multiple-people)
- [What music sources exist out of the box?](#what-music-sources-exist-out-of-the-box)
- [Can I save music, podcasts, or audiobooks to my phone or computer?](#can-i-save-music-podcasts-or-audiobooks-to-my-phone-or-computer)
- [Where is my data?](#where-is-my-data)
- [How do I back up and restore?](#how-do-i-back-up-and-restore)
- [Linux or Windows for the server?](#linux-or-windows-for-the-server)

---

## Is my voice data leaving my network?

**No.** Everything in the voice path is local:

- Your satellite streams microphone audio over your LAN to the Domovoi server (WebSocket on port 6370). It never goes further.
- Speech-to-text is **Whisper running on your own machine** (`faster-whisper`, CUDA by default). No cloud STT.
- The language models are **local Ollama models** on the same machine.
- Voice-profile identification ("who's speaking") computes voice embeddings locally and stores them in your own Postgres.

Two honest nuances, so you can decide for yourself:

1. **Edge TTS sends response *text* out — but it's off by default.** Text-to-speech defaults to `piper`, fully local neural TTS, so out of the box nothing Domovoi says leaves the house. Microsoft's Edge neural voices are available in the settings gear if you prefer them, and they sound better — but switching means the *text Domovoi speaks back to you* (not your voice, not your audio) goes to a Microsoft service. Worth choosing on purpose rather than inheriting.
2. **Song identification sends short audio clips out — but not from your microphone.** The bundled radio plugin can sample short clips of *radio streams* you've favorited, and the library enricher can fingerprint *your music files*, sending those to online identification services (Shazam via `shazamio`, optionally AcoustID/MusicBrainz). Radio-stream audio and library files, never mic audio. Both are switchable off (see the table below).

## What touches the internet, and how do I turn each thing off?

Every optional outbound touchpoint, sourced from the code:

| Feature | What goes out | Where | How to disable |
|---|---|---|---|
| Edge TTS — **opt-in, not the default** | Response text | Microsoft Edge TTS service | Nothing to disable: the default engine is `piper` (fully local). This row applies only if you switch to `edge` yourself (Settings gear → TTS engine, or `TTS_ENGINE=edge`) |
| Connectivity probe | A TCP dial, no payload | `1.1.1.1:443` every 30 s | Change `CONNECTIVITY_PROBE_TARGET` to a LAN host (the probe is how Domovoi knows it's offline — don't remove it, repoint it) |
| Model downloads | One-time fetches | Whisper models, Piper voices (Hugging Face), Ollama model pulls | Nothing recurring — happens at setup / first use of a new model or voice |
| News briefings | RSS feed fetches; topic feed discovery via your local SearXNG | The feeds you configure | `NEWS_ENABLED=false` kills all background fetching; `NEWS_AUTO_FETCH` (topic feeds) is already off by default |
| "Double-check that" / web answers | Search queries | Your own SearXNG container (localhost-only, port 6888), which queries public search engines | Don't start the `searxng` container — the handler degrades gracefully and says it can't check |
| Radio plugin: station directory | Station-name searches | radio-browser.info | Disable or uninstall the radio plugin from the dashboard's Plugins page |
| Radio plugin: FCC station import | One bulk query on demand | transition.fcc.gov | Off unless you click "Import FCC" (or set `RADIO_FCC_IMPORT_ON_BOOT=true`) |
| Radio plugin: song detection | Short clips of favorited radio *streams*; ICY metadata polls | Shazam; the stations themselves | `RADIO_SAMPLER_ENABLED=false`, `RADIO_ICY_POLLER_ENABLED=false` |
| Library enricher | Audio fingerprints of your library files | AcoustID (only if you set `ACOUSTID_API_KEY`) and Shazam; metadata lookups to MusicBrainz | `LIBRARY_ENRICHER_ENABLED=false` |
| Podcasts | Feed polls + episode downloads | Feeds you subscribe to | Already off by default (`PODCAST_FEED_POLLER_ENABLED=false`) |

The install-time trust screen for any *third-party* plugin lists that plugin's own network behavior — see [What are plugins, and are they safe?](#what-are-plugins-and-are-they-safe)

## What hardware do I need?

**The server** — one machine that stays on:

- **Linux or Windows** — both supported; see [Linux or Windows for the server?](#linux-or-windows-for-the-server). Either way Docker runs Postgres and the per-room music daemons (Docker Engine on Linux, Docker Desktop on Windows).
- An NVIDIA GPU is recommended but **not required**: the default STT is Whisper `large-v3` on CUDA, and the local Ollama models (a 3B conversational model and a 14B tool-routing model by default) want VRAM too. On a CPU-only or non-NVIDIA box, four settings get you a system that's instant for everyday commands and slower only for open-ended questions — see [CPU_HOST.md](CPU_HOST.md).
- **~20 GB of free disk** for the software and all default models (the two Ollama models alone are ~11 GB), **plus** room for your own media library on top. The full breakdown and ways to shrink it are in the README's [Disk footprint](../README.md#disk-footprint) section.

**Satellites** — one small box per room you want to talk to:

- Raspberry Pi Zero 2 W (or a Pi 4) with either:
  - **ReSpeaker 2-Mics Pi HAT** (V1 or V2.0 — they use different codecs, check yours), or
  - **ReSpeaker XVF3800 USB 4-Mic Array** — the nicer option: on-chip echo cancellation, gain control, and beamforming. Required for the talk-over-the-greeting flow and open-mic chat mode.
- Powered speaker on the 3.5 mm jack, microSD card, decent power supply.

Full parts list and step-by-step setup: [Satellite Hardware](SATELLITE_HARDWARE.md) and the provisioning checklist in `satellite/PROVISIONING.md`.

## Can it run without a GPU?

Yes, with tradeoffs. Whisper's device is a setting (`WHISPER_DEVICE`, choices `cuda` or `cpu` — dashboard settings gear, advanced section). On CPU you'll want a smaller Whisper model than the default `large-v3` (e.g. `small` or `medium`), and Ollama will run its models on CPU too, so responses get noticeably slower. The system works; it just stops feeling instant. If you're GPU-less, also prefer the fast-path voice commands (timers, music, announcements) — those never touch the LLM at all.

## Does it work offline?

Yes — local-first is a tested contract, not a slogan. Every voice handler declares how much network it needs (`no`, `degraded`, or `yes`), and any handler that isn't fully local **must** implement an offline fallback — the test suite enforces this. A background connectivity probe tells the router whether the internet is reachable, and the router picks the right path per turn.

What that looks like in practice with the internet down:

- **Fully works:** wake word, STT, intent routing, timers, reminders, clocks, calculator, music from your local library, intercom announcements, room-to-room drop-in calls, voice profiles, memory. All local.
- **Degrades:** TTS falls back down its chain (Edge online → Piper local → system voice), so Domovoi keeps talking, just in a different voice. Radio needs the stream, but its handler degrades per-command.
- **Says so honestly:** web-backed answers ("double-check that", news fetches) reply that they can't check right now instead of guessing.
- **Satellite-side:** if a satellite loses the *server* for 30+ seconds, the next wake word plays a locally cached "having trouble reaching the network" clip instead of silence.

## How do I add rooms?

A room *is* a satellite. To add one:

1. Provision a new Pi (flash, HAT/array setup, install the satellite client — see `satellite/PROVISIONING.md`).
2. In its `~/.domovoi/config.toml`, set a stable `room_id` (e.g. `kitchen`) and point `domovoi_url` at your server: `ws://your-server.local:6370`.
3. Start it. That's it — on first connection, the server automatically provisions that room's own music daemon (an MPD container with its own queue and volume) and the room appears on the dashboard's Satellites page.

Room names matter: intercom ("announce in the kitchen…") and drop-in ("drop in on the kitchen") address rooms by that `room_id`.

## Can another device on my network pretend to be one of my rooms?

Not once the real one has connected. Each satellite generates a random **pairing token** on first boot (stored in `~/.domovoi/pairing_token`) and sends it when it connects. The server remembers only a hash of it and binds the room to that token the first time it sees one — *trust-on-first-use*. After that, a device connecting as `kitchen` must present the matching token or the server refuses it and closes the connection. So a random device that later tries to impersonate a paired room is turned away (it can't issue commands as that room, and can't receive that room's audio or drop-ins).

The honest caveat: because the *first* token wins, there's a one-time window when a room has never paired — whoever connects first claims it. On a home Wi-Fi you control, that's normally just you provisioning the Pi. If you want to eliminate even that window, set `SATELLITE_PAIRING_STRICT=true` to require a token for *every* room (only do this once all your satellites have paired, or new/re-flashed Pis can't connect). Older tokenless satellites keep working by default — pairing protects any room that has paired without breaking ones that haven't.

**Re-flashed a Pi or moved a room to new hardware?** The new device has a new token that won't match, so it'll be refused. Clear the old pairing from the dashboard: Satellites → open the room → **Overview → Reset pairing** (admin login required). The next connection re-pairs. Full details: [Security & Privacy](SECURITY_PRIVACY.md#satellite-pairing-ws-auth).

One thing pairing does *not* do: encrypt the audio. LAN traffic is still unencrypted in v1 (TLS is on the hardening backlog), so keep satellites and the server on a network segment you control. See [Is my voice data leaving my network?](#is-my-voice-data-leaving-my-network).

## Can I drop in on a room from my phone?

Yes. In the Android app, open a satellite's page and tap **drop in from this phone** — your phone joins that room's live two-way audio bridge directly (it needs microphone permission and the room's satellite must support full-duplex/AEC audio). It works like a room-to-room drop-in, except your phone is one end instead of another Pi. Note it's one-directional in the sense that *you* call into a room; a room can't call your phone, because your phone isn't a registered room.

## Can I rename the assistant or change the wake word?

Those are two separate things:

- **The name** — what Domovoi calls itself in speech and on screen — is the `BOT_NAME` setting (dashboard settings gear → Identity, takes effect on restart). Greeting and sample clips re-render with the new name.
- **The wake word** — the phrase that opens the mic — defaults to `hey_jarvis`, one of openWakeWord's prebuilt models. You can switch among the other built-ins in the satellite config, or **train a custom one** ("Hey Domovoi" is the natural choice) entirely from the product: dashboard → Settings → Wake Words → record positive clips *on the actual satellite* (through its real mic), train, then push the model to a room. Training itself needs a Linux toolchain (WSL2 or Docker on your Windows server — see `scripts/wake_word/README.md` and `scripts/wake_word/DOCKER_TRAINER.md`), configured once via `WAKE_WORD_TRAINER_ENABLED` and `WAKE_WORD_TRAIN_COMMAND`.

## What are plugins, and are they safe?

Plugins add features — new voice commands, dashboard pages, background workers, media providers. The bundled radio plugin (`plugins/radio`, published by Coders Farm) is the reference example; it's how Domovoi does internet radio and FM.

The trust model, in plain words:

- **A plugin is code running on your server, with the access your server has.** There is no sandbox pretending otherwise. The install screen says exactly that, and it can't be skipped.
- **You see what you're agreeing to before anything runs.** Install is two-phase: the zip is staged and inspected first, and you get a preview — publisher, permissions (network? subprocesses? hardware?), the plugin's own plain-English warnings, its dependency list — before you confirm. The radio plugin's warnings, for example, disclose that it can send stream clips to Shazam and query external station directories.
- **No plugin code executes before you confirm.** Dependencies are declared in a hash-pinned lockfile and checked in a throwaway subprocess with `pip --dry-run --require-hashes --only-binary` — so not even a package build script runs pre-confirmation. The staged files are hash-verified again at confirm time.
- **Installing, upgrading, and removing plugins requires the admin password.** So does every other risky operation ([Security & Privacy](SECURITY_PRIVACY.md) has the full model).
- **Plugins keep their data in their own database schema** and write their own log file (`~/.domovoi/logs/plugin_<slug>.log`), so you can see what one is doing and remove it cleanly (uninstall offers keep-data or purge).

Bottom line: treat a plugin like any software you install on a home server — only install from publishers you trust. Want to build one? [Plugin Development](PLUGIN_DEVELOPMENT.md).

## Can I use it with Home Assistant?

They coexist happily — Domovoi doesn't replace Home Assistant (it doesn't do lights, locks, or thermostats out of the box), and Home Assistant doesn't do what Domovoi does (local voice, music, intercom, per-room audio). The practical bridge is Domovoi's HTTP API on port 6370: Home Assistant automations can, for instance, POST to the announce endpoint to speak a message in any room ("the wash is done") through your satellites. See [Home Assistant](HOME_ASSISTANT.md) for recipes and [API Reference](API_REFERENCE.md) for the endpoints. Deeper integration (device control by voice) is natural plugin territory.

## Does it support multiple people?

Yes — voice profiles. Say "I'm Sarah" and confirm, and Domovoi enrolls your voice locally (a voice embedding, stored in your Postgres — no cloud). After that it recognizes who's speaking and personalizes: per-person memories ("remember that I…"), per-person news topics, and a "who am I?" you can ask any time. You can introduce someone else ("this is my friend Alex"), and "forget me" deletes a person's profile and voice data outright. Matching thresholds are tunable per household if siblings' voices collide.

## What music sources exist out of the box?

- **Your local library** — files in your music folder (`MUSIC_DIR`, default `~/Music`), indexed and playable by voice per room, with playlists, album art in the browser player, and optional automatic metadata cleanup.
- **Browser uploads** — drag files into the dashboard; they land in `MUSIC_DIR/uploads/` and join the library.
- **Internet radio + FM** — the bundled radio plugin: station search, favorites, live tuning (FM needs an optional RTL-SDR dongle), and passive song detection on stations you favorite.
- **Podcasts and audiobooks** — RSS podcast subscriptions and local audiobook files, streamed per room like music.

Anything beyond that — pulling music from external sources — is the job of **provider plugins**: separately installed plugins that act as acquisition fulfillers and streaming search providers. Core deliberately speaks only a generic "media acquisition" vocabulary; a request like "add this song" is queued as a structured request, and whatever provider plugin you've installed fulfills it. No fulfiller installed? Requests wait in the queue (visibly, on the dashboard) and Domovoi tells you a provider is needed — installing one later drains the backlog.

## Can I save music, podcasts, or audiobooks to my phone or computer?

**Yes.** Any track, downloaded podcast episode, or audiobook has a **save** button (a download icon) next to its play control. It pulls the actual audio file off your Domovoi server onto the device you're using:

- **Web dashboard** — the file goes to your browser's normal downloads folder, named after the track/episode/book.
- **Android app** — the file is handed to the system DownloadManager and lands in **`Downloads/Domovoi/`**, with a progress notification; it shows up in your Files app afterwards.

Single-file audiobooks (a `.m4b`, say) save as that one file. **Folder audiobooks** (a book split into per-chapter files) come down as a single **zip** of all the chapters, so on Android you'll see a brief "zipping chapters" note while the server builds it. This is a plain file copy from your own server — nothing goes to the internet.

## Where is my data?

All on machines you own:

| What | Where |
|---|---|
| Conversations, intents, people/voice profiles, memories, playlists, timers, plugin data | Postgres — the `domovoi` database, in the `domovoi-pgdata` Docker volume, published on host port 6432 |
| Server config & runtime artifacts | `~/.domovoi/` on the server — setup code, rendered voice clips (`sounds/`), wake-word models and training clips, uploaded Piper voices, album-art cache, per-plugin logs (`logs/`), podcasts, audiobooks |
| Service settings | `domovoi/.env` next to the code |
| Music | `MUSIC_DIR` (default `~/Music`) |
| Documents (office pages) | `DOCUMENTS_DIR` (default `~/Documents`) |
| Satellite config & caches | `~/.domovoi/` on each Pi |

## How do I back up and restore?

There's no one-button backup tool yet — but it's three pieces, all standard:

1. **The database** (the important one):
   ```powershell
   docker exec domovoi-postgres pg_dump -U domovoi domovoi > domovoi-backup.sql
   ```
2. **`~/.domovoi/`** on the server — copy the folder. Trained wake-word models and recorded training clips live here and are genuinely hard to recreate; the rest (rendered sounds, caches) regenerates itself.
3. **Your media** — `MUSIC_DIR` and friends, which you're presumably backing up anyway.

Restore on a new machine: install Domovoi, start Postgres, restore the dump (`docker exec -i domovoi-postgres psql -U domovoi domovoi < domovoi-backup.sql`), copy `~/.domovoi/` and your media back, copy your `.env`, start the core. Returning users note: admin credentials live in the database, so a restored database keeps your password; a *fresh* database means first-run setup again (new setup code in `~/.domovoi/setup-code.txt`). Satellites reconnect on their own — their config never left the Pi.

## Linux or Windows for the server?

**Either. Pick by what the box is.**

**Linux** is the better host for a dedicated always-on machine, and it's where the project is heading. A headless install leaves several GB more RAM for models, wake-word training runs natively instead of bridging through WSL2, `ffmpeg`/`fpcalc`/`mpg123` are one `apt install`, and autostart is a systemd unit instead of a scheduled-task workaround. Setup: [LINUX_HOST.md](LINUX_HOST.md).

**Windows** remains fully supported and is the better choice when the server is also the household's gaming PC — for many homes that machine already has the best GPU in the house, and Domovoi is built to run well on it rather than demanding a separate Linux box. It carries genuine Windows-specific engineering: CUDA DLL preloading for Whisper (`domovoi/bootstrap.py`), Docker Desktop networking, console-encoding-safe setup codes, and a SAPI voice as the last TTS fallback.

Postgres, MPD, and SearXNG run in containers either way, and the application code is plain Python. The differences are real but small, and each one is documented rather than assumed.

---

*Still curious? The [Architecture](ARCHITECTURE.md) doc explains how the pieces fit; [Troubleshooting](TROUBLESHOOTING.md) is for when they don't.*
