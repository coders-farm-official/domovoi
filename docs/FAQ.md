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

1. **Edge TTS sends response *text* out — but only if you choose it.** Text-to-speech defaults to `piper`, fully local neural TTS, so out of the box Domovoi's replies are spoken on your own hardware. Edge is never used as a stand-in: if Piper can't speak a reply, the robotic system voice does, and an Edge voice is only registered when you choose Edge or the extra voices. Microsoft's Edge neural voices are available on Settings → Voices if you prefer them, and they sound better — but switching means the *text Domovoi speaks back to you* (not your voice, not your audio) goes to a Microsoft service. Worth choosing on purpose rather than inheriting. On a server answered **No** to the internet question, Edge isn't used at all.
2. **Song identification sends short audio clips out — but not from your microphone.** The bundled radio plugin can sample short clips of *radio streams* you've favorited, and the library enricher can fingerprint *your music files*, sending those to online identification services (Shazam via `shazamio`, optionally AcoustID/MusicBrainz). Radio-stream audio and library files, never mic audio. Both are switchable off (see the table below).

## What touches the internet, and how do I turn each thing off?

Every optional outbound touchpoint, sourced from the code. One answer covers all of them: **No, keep everything in the house** in Settings → Internet (`INTERNET_ACCESS=never`) refuses every one, and the online controls say "needs internet" ([INTERNET.md](INTERNET.md)). The last column is how to turn one off on its own on a server that does use the internet.

| Feature | What goes out | Where | How to disable |
|---|---|---|---|
| Edge TTS — **opt-in, not the default** | Response text | Microsoft Edge TTS service | Nothing to disable: the default engine is `piper` (fully local), and Edge is never a fallback for it. This row applies only if you switch to `edge` yourself (Settings → Voices, or `TTS_ENGINE=edge`) |
| Clips for a registered Edge voice | Fixed text, once per clip: the "trouble reaching the network" notice, the voice sample and the wake greetings (including ones you wrote in Settings → Greetings) | Microsoft Edge TTS, at startup when a clip is missing or its text changed, and only while the internet is up | Only when an Edge voice is registered: you chose `TTS_ENGINE=edge`, or the extra voices (`SEED_VOICE_CATALOG=true`, 19 Edge voices). While the internet is down, Piper renders a stand-in that is replaced later |
| Connectivity probe | A TCP connection, no payload | `1.1.1.1:443` every 30 s | Answer **No** in Settings → Internet: the probe stops dialing and the dashboard says "Turned off for this box". Otherwise **leave `CONNECTIVITY_PROBE_TARGET` on an internet address**: the failing dial is how Domovoi knows the line is down. Pointed at a LAN host (your router), it reports online when the internet isn't there, and online features fail slowly instead of saying they can't. If your network blocks `1.1.1.1`, use another public `host:443` (e.g. `9.9.9.9:443`) |
| Model downloads | Model files, once | Hugging Face (Whisper models, Piper voices), Ollama model pulls, the fast lane's model (GitHub, only once `fastlane_mode` is `shadow`) | One-time each. Whisper loads its model from the server's own cache first and asks Hugging Face only for a model that isn't downloaded yet; under **No** it never asks (and `HF_HUB_OFFLINE=1` is set for the core), so download every model you'll use before you disconnect ([how](INTERNET.md#before-you-disconnect)) |
| News briefings | RSS feed fetches; topic feed discovery via your local SearXNG | The feeds you configure | `NEWS_ENABLED=false` kills all background fetching (it follows the internet answer: off for **No**); `NEWS_AUTO_FETCH` (topic feeds) is already off by default |
| "Double-check that" / web answers (weather, scores, "check that online") | Search queries; and, every time the container starts, its own fetches of the ClearURLs rule lists, a Wikidata query and the radio-browser server list | Your own SearXNG container (localhost-only, port 6888), which queries public search engines | It follows the internet answer: saving **Yes** or **Sometimes** starts it, **No** stops it (no search is sent even if it runs, but its startup fetches would be, which is why it is stopped). Not answered: it runs only if you started it (`docker compose up -d searxng`). When it isn't running Domovoi says so — "I can't search the web right now — my search helper isn't running" — instead of claiming it searched |
| Radio plugin: station directory | Station-name searches; an FM favorite's call sign, to find its internet stream (when you favorite it, and at startup for FM favorites that still have none) | radio-browser.info | Disable or uninstall the radio plugin from the dashboard's Plugins page |
| Radio plugin: FCC station import | One bulk query on demand | transition.fcc.gov | Off unless you click **Import FCC FM** (or set `RADIO_FCC_IMPORT_ON_BOOT=true`) |
| Radio plugin: song detection | Short clips of favorited radio *streams*; ICY metadata polls | Shazam; the stations themselves | `RADIO_SAMPLER_ENABLED=false`, `RADIO_ICY_POLLER_ENABLED=false` in the plugin's own file, `~/.domovoi/plugins/radio.env` (restart the core) |
| Library enricher | Audio fingerprints of your library files | AcoustID (only with an `ACOUSTID_API_KEY`, an application key) and Shazam (only with the `shazam` extra installed); metadata lookups to MusicBrainz | `LIBRARY_ENRICHER_ENABLED=false`. With neither a key nor Shazam it sends nothing and marks nothing |
| MusicBrainz alias lookup — **off unless your internet answer is Yes or Sometimes** | Artist names from your library — tagged artists, and the "Artist" of an untagged file whose name reads "Artist - Title" (so a home recording named "Grandma Edith - Happy Birthday" would send "Grandma Edith"): one search per artist, once, then only for artists added later (about an hour and a half the first time for ~2,000 artists); the spoken stage names that come back are kept, legal names and a person's names sharing a word with one are filtered out and never stored. A band's other names (members, "The Farriss Brothers" for INXS) can be kept | musicbrainz.org, one request a second, only while the server is online | On when the internet answer is **Yes** or **Sometimes**, off when it is **No** or not answered. Settings → Configuration → Library → "Look up other names on MusicBrainz" (`music_alias_fetch_enabled` / `MUSIC_ALIAS_FETCH_ENABLED`) overrides the answer; switching it off stops it before its next request |
| Synced lyrics (LRCLIB) — **off unless your internet answer is Yes or Sometimes** | A song's title, artist, album and length, for songs with no timed lyrics of their own (no `.lrc` beside them, none in their tags): one lookup per song, then again after a few weeks for songs LRCLIB didn't have (several hours the first time for 5,000 songs, about a night, longer when LRCLIB asks it to slow down). The lyrics that come back stay on the server, and with "Save lyrics as .lrc files" timed ones are also saved as a `.lrc` next to the song (never over a `.lrc` you made) | lrclib.net, about one request a second, only while the server is online | On when the internet answer is **Yes** or **Sometimes**, off when it is **No** or not answered. Settings → Configuration → Library → "Synced lyrics from LRCLIB" (`lyrics_lrclib_enabled` / `LYRICS_LRCLIB_ENABLED`) overrides the answer; switching it off stops it before its next request. Lyrics from `.lrc` files and the songs' own tags never touch the internet |
| Podcasts | Feed polls + episode downloads; a show's name when you subscribe by voice or search in Discover; each show's artwork, once | Feeds you subscribe to; Apple's iTunes search | Automatic downloads follow the internet answer: on for **Yes**, off for **Sometimes**, **No** and not answered (`PODCAST_FEED_POLLER_ENABLED`). Search runs only when you ask. Show artwork is downloaded **by the server** and served to your browser and phone, which never contact the publisher |
| Extra voices | Downloads of the catalog's Piper voices (about 0.7 GB), and the Edge clips above for 19 Edge voices | Hugging Face; Microsoft | Off once the internet question is answered (any answer), unless `SEED_VOICE_CATALOG` is set in your `domovoi/.env` — which a server installed before October 2026 has (its old template wrote `SEED_VOICE_CATALOG=true`; Settings → Internet → **follow the answer again** hands it back). Not answered, it's on unless `SEED_VOICE_CATALOG=false` |
| Things an admin starts | Downloads, only when an admin clicks: **Pull the latest** (and, with the update unit, Python packages and the music image's Debian packages, and the search helper's image when it starts it), plugin installs, model installs, **Refresh caches** for satellite media | GitHub; PyPI and the PyTorch CPU index; Debian's mirrors; Docker Hub; the Ollama registry; Hugging Face (a new Whisper size) | Nothing runs on its own; don't click them. Under **No** they are greyed and the server refuses them |

The install-time trust screen for any *third-party* plugin lists that plugin's own network behavior — see [What are plugins, and are they safe?](#what-are-plugins-and-are-they-safe)

Setting up a whole server one way or the other? [Will your Domovoi have internet?](INTERNET.md) explains the three answers, what each one switches, and what to download before a server goes without.

## What hardware do I need?

**The server** — one machine that stays on:

- **Linux or Windows** — both supported; see [Linux or Windows for the server?](#linux-or-windows-for-the-server). Either way Docker runs Postgres and the per-room music daemons (Docker Engine on Linux, Docker Desktop on Windows).
- An NVIDIA GPU is recommended but **not required**: the default STT is Whisper `large-v3` on CUDA, and the local Ollama models (a 3B conversational model and a 14B tool-routing model by default) want VRAM too. On a CPU-only or non-NVIDIA box, four settings get you a system that's instant for everyday commands and slower only for open-ended questions — see [CPU_HOST.md](CPU_HOST.md).
- **~20 GB of free disk** for the software and all default models (the two Ollama models alone are ~11 GB), **plus** room for your own media library on top. The full breakdown and ways to shrink it are in the README's [Disk footprint](../README.md#disk-footprint) section.

**Satellites** — one small box per room you want to talk to:

- Raspberry Pi Zero 2 W (or a Pi 4) with either:
  - **ReSpeaker 2-Mics Pi HAT** (V1 or V2.0 — they use different codecs, check yours), or
  - **ReSpeaker XVF3800 USB 4-Mic Array** — the nicer option: on-chip echo cancellation, gain control, and beamforming. Required for open-mic chat mode, and it makes talk-over barge-in reliable. (The spoken wake greeting works on either board: it plays before the satellite listens.)
- Powered speaker on the 3.5 mm jack, microSD card, decent power supply.

Full parts list and step-by-step setup: [Satellite Hardware](SATELLITE_HARDWARE.md) and the provisioning checklist in `satellite/PROVISIONING.md`.

## Can it run without a GPU?

Yes, with tradeoffs. Whisper's device is a setting (`WHISPER_DEVICE`, choices `cuda` or `cpu` — dashboard settings gear, advanced section). On CPU you'll want a smaller Whisper model than the default `large-v3` (e.g. `small` or `medium`), and Ollama will run its models on CPU too, so responses get noticeably slower. The system works; it just stops feeling instant. If you're GPU-less, also prefer the fast-path voice commands (timers, music, announcements) — those never touch the LLM at all.

## Does it work offline?

Yes — local-first is a tested contract, not a slogan. Every voice handler declares how much network it needs (`no`, `degraded`, or `yes`), and any handler that isn't fully local **must** implement an offline fallback — the test suite enforces this. A background connectivity probe tells the router whether the internet is reachable, and the router picks the right path per turn.

What that looks like in practice with the internet down:

- **Fully works:** wake word, STT, intent routing, timers, reminders, clocks, calculator, music from your local library, intercom announcements, room-to-room drop-in calls, voice profiles, memory. All local.
- **Degrades:** the default Piper voice is local, so Domovoi keeps talking in the same voice. If you chose the online Edge voices, it falls back to Piper (and to the robotic system voice if Piper is missing), so it keeps talking, just in a different voice. Radio needs the stream, but its handler degrades per-command, and FM over an RTL-SDR keeps working.
- **Says so honestly:** web-backed answers ("double-check that", news fetches) reply that they can't check right now instead of guessing. On a server answered **No** they say "I'm set to stay off the internet" instead.
- **Satellite-side:** if a satellite loses the *server* for 30+ seconds, the next wake word plays a locally cached "having trouble reaching the network" clip instead of silence.

Running with no internet at all, for good? Answer **No, keep everything in the house** in Settings → Internet: the server then contacts nothing outside your network. A few things can only be downloaded online, and the first time Domovoi needs one is usually the day the line is down, so do [Before you disconnect](INTERNET.md#before-you-disconnect) first.

### Can Domovoi run with no internet at all?

Yes. After setup (which downloads the software and models), answer **No, keep everything in the house** — at the end of the first-run setup, in Settings → Internet, or `INTERNET_ACCESS=never` in `domovoi/.env`. The server then makes no connection outside your network: the internet check stops, and every feed, search, voice, download and plugin request is refused with "internet access is turned off for this box (Settings → Internet)". Music, timers, intercom, voice, the dashboard and the app keep working. What it can't switch off is outside the server: the satellites' own operating system keeps its clock with public time servers, and your browsers and phones are your browsers and phones. [INTERNET.md](INTERNET.md#internet-in-the-house-but-domovoi-shouldnt-use-it) has the details.

## How do I add rooms?

A room *is* a satellite. To add one:

1. Provision a new Pi (flash, HAT/array setup, install the satellite client — see `satellite/PROVISIONING.md`).
2. In its `~/.domovoi/config.toml`, set a stable `room_id` (e.g. `kitchen`) and point `domovoi_url` at your server: `ws://your-server.local:6370`.
3. Start it. That's it — on first connection, the server automatically provisions that room's own music daemon (an MPD container with its own queue and volume) and the room appears on the dashboard's Satellites page.

Room names matter: intercom ("announce in the kitchen…") and drop-in ("drop in on the kitchen") address rooms by that `room_id`.

## Can another device on my network pretend to be one of my rooms?

Not once the real one has connected. Each satellite generates a random **pairing token** on first boot (stored in `~/.domovoi/pairing_token`) and sends it when it connects. The server remembers only a hash of it and binds the room to that token the first time it sees one — *trust-on-first-use*. After that, a device connecting as `kitchen` must present the matching token or the server refuses it and closes the connection. So a random device that later tries to impersonate a paired room is turned away (it can't issue commands as that room, and can't receive that room's audio or drop-ins).

Strict pairing (`SATELLITE_PAIRING_STRICT=true`) is the default, and it closes the one gap left: a tokenless connection is refused, and a room's first pairing waits for you to type in the six-digit code the satellite is showing and saying. The honest caveat applies only if you turn strict pairing off: then the *first* token wins, so there's a one-time window when a room has never paired — whoever connects first claims it — and a device with no token at all can connect under any room name nobody has paired. Such a tokenless room can use itself (timers, music, voice commands) but can never drop in on another room or announce into one. An install that upgrades into this release and never set the line becomes strict at its next restart: paired rooms are unaffected, and a satellite that hasn't paired yet asks for approval once.

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

## Who added that song, and can I stop someone adding more?

Every room has one queue — MPD's, the thing its speaker actually plays from —
and anyone on the network can add to it, reorder it, or drop a track from the
dashboard's **Music → Room queue** tab or the app's **room queue** tab. Each
entry carries a quiet "added by *<device>*" note, so "who put this on?" has an
answer. Entries Domovoi has no record of — a voice request, or a cast from
before this existed — simply show nothing rather than a guess.

Devices name themselves. A browser or phone introduces itself the first time
it loads, with a name guessed from the platform ("Chrome on Windows", "Pixel
8"); you can rename it in **Settings → Devices** (dashboard) or **Settings →
Connection** (app). Renaming relabels that device's existing queue entries.

An admin can take queue editing away from a named device — one room, or every
room — in **Settings → Devices → Queue access**. A blocked device can still
*see* the queue and still plays its own music; it just can't change what the
room plays. Be aware of what this is: a device tells the server who it is, and
nothing on a trusted LAN forces it to be honest, so this reliably settles a
household argument but is not a defence against someone determined. Changing
blocks needs the admin password, which is why it can't be undone from the
device it was applied to.

## Can I move files around without the command line?

Yes — the dashboard's **Files** tab moves things by drag and drop. Pick up a
row (or select several first) and drop it on a folder, on a breadcrumb crumb to
move it up a level, or on another library's chip to move it into that library.
Dragging files in from your desktop uploads them into the folder you're looking
at. On the phone, each row has a **move** button that opens a destination
picker, since dragging inside a scrolling list isn't a real gesture.

A few things it refuses on purpose: moving a folder into itself, moving
anything onto a name that already exists (nothing is ever silently
overwritten), and moving *out of* a read-only library — a removable drive or a
read-only plugin library can only be a destination. Copying *off* a removable
drive is still the separate **import** action. If one item in a multi-item drag
fails, the rest still move and the toast says what went wrong.

None of this needs the admin password — browsing, downloading, uploading,
moving and importing are open to every phone and browser on your network, the
same as playing music. **Deleting** is the one Files action that does need
admin, because it's the one that destroys something; the phone app can't sign
in as admin, so delete from the dashboard. If one device keeps filling the
libraries with things it shouldn't, an admin can take uploading, moving and
importing away from it in **Settings → Devices → Files access** — it can still
browse and download, and the Files page tells it why it can't change anything.
The same honesty note as queue access applies: a device tells the server who it
is, so this settles a household argument rather than stopping someone
determined.

## What music sources exist out of the box?

- **Your local library** — files in your music folder (`MUSIC_DIR`, default `~/Music`), indexed and playable by voice per room, with playlists, album art in the browser player, and optional automatic metadata cleanup.
- **Browser uploads** — drag files into the dashboard; they land in `MUSIC_DIR/uploads/` and join the library.
- **Internet radio + FM** — the bundled radio plugin: station search, live tuning (FM needs an optional RTL-SDR dongle), a Recent strip of the last ten stations you played, and passive song detection on stations you favorite. Clicking a station plays it; favoriting is a separate act, because a favorite is also what puts a station on the detectors' poll schedule.
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
| Pictures (Images tab) | `PICTURES_DIR` (default `~/Pictures`) |
| Podcasts, audiobooks | `PODCASTS_DIR` / `AUDIOBOOKS_DIR` (default `~/.domovoi/podcasts`, `~/.domovoi/audiobooks`) |
| Satellite config & caches | `~/.domovoi/` on each Pi |

**These directories are created for you on first boot.** Every media library
above is created (`mkdir -p`, ordinary permissions) when the dashboard starts,
so a fresh headless server gets a working Documents and Pictures library even
though a server account has no desktop `~/Documents` or `~/Pictures` folder.
Nothing is overwritten: a directory that already exists is left exactly as it
is, contents and permissions untouched.

Domovoi **refuses** to create a configured path in four cases, and logs a
`WARNING` naming the library, the setting and the path instead:

- the path is **outside your home directory** — it might be the mountpoint of a
  drive that isn't mounted yet, and creating an empty folder there would mask
  the drive when it does come up (your files would appear to vanish). Point
  `MUSIC_DIR` at an external disk and you create and mount it yourself;
- the path is a whole **filesystem or drive root** (`/`, `D:\`);
- the path is inside the private config area `~/.domovoi/` but isn't one of the
  media folders that legitimately live there (podcasts, audiobooks);
- the path already exists as a **file**, not a directory.

The server still starts in every one of these cases — the library simply won't
appear on the Files page until you fix the setting or create the directory. If
a library is missing from Files, the dashboard log says which one and why.

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
