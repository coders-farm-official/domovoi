# Will your Domovoi have internet?

Installing Domovoi needs the internet: Python packages, Docker images, the
language models and the speech models all download during setup. After
that it is your choice, and Domovoi asks you once:

> **Will this Domovoi have internet?**
> **Yes, always** · **Sometimes** · **No, keep everything in the house**

Speech recognition, the language models and the default voice all run on
the server, so **your voice and recordings never leave the house, whatever
you answer**. The internet only adds extras, and the answer sets their
defaults.

- [Which answer fits you](#which-answer-fits-you)
- [Where you answer](#where-you-answer)
- [What each answer changes](#what-each-answer-changes)
- [If your Domovoi will have internet](#if-your-domovoi-will-have-internet)
- [Sometimes](#sometimes)
- [Running Domovoi with no internet](#running-domovoi-with-no-internet)
- [Internet in the house, but Domovoi shouldn't use it](#internet-in-the-house-but-domovoi-shouldnt-use-it)
- [Changing it later](#changing-it-later)
- [What goes out](#what-goes-out)

---

## Which answer fits you

| Answer | Pick it when | In one line |
|---|---|---|
| **Yes, always** | It's on your home internet. | The online extras are on. They pause by themselves when the internet drops and pick up again when it's back. |
| **Sometimes** | The connection comes and goes, or it's slow or metered. | Small lookups run whenever the internet is up. Big automatic downloads (podcast episodes) stay off until you turn them on. |
| **No, keep everything in the house** | There's no internet there, or you don't want Domovoi to use it. | Domovoi doesn't contact the internet at all. Online extras are off and say "needs internet"; music, timers, intercom, voice and everything else on your network work as normal. |
| *not answered yet* | Every server installed before the question existed, and anyone who skips it. | Every default stays as it always was. An admin sees one Home row, **tell Domovoi whether this box has internet**, until someone answers. |

"No" means no **internet**, not no network: the satellites, the phones and
browsers in the house, Ollama, a music share or a feed server on your LAN
all keep working.

## Where you answer

Any one of these; they all set the same thing, `INTERNET_ACCESS`:

- **The dashboard.** Right after you claim the admin account, the setup
  dialog asks (or choose **Decide later**). Afterwards: **Settings →
  Internet** (admin sign-in), or click the Home row.
- **A fresh install from the command line.** Create the first `.env` with
  the answer in it:

  ```bash
  python -m domovoi.env_bootstrap --internet always    # or sometimes, never
  ```

  That only works on a fresh checkout: an existing `.env` is never touched
  (`dev.sh` / `dev.ps1` run `env_bootstrap` without the flag, so run this
  first). The Windows installer asks the same question and does this for
  you.
- **By hand.** `INTERNET_ACCESS=always` (or `sometimes`, `never`) in
  `domovoi/.env` (where the dashboard and `env_bootstrap` write it too),
  then restart the core. `yes` / `no` / `online` /
  `offline` work too. Anything else counts as not answered, with one
  warning in the core's log.
- **The server's environment** (a systemd `Environment=` line, say). It
  wins over `.env`, and the dashboard then shows the answer as **set in
  the server's environment** and won't change it. The core and the
  dashboard are separate processes, so the line goes into **both** units
  (and, on Linux, into `/etc/default/domovoi-update` for the update
  unit): a line in the core's unit alone leaves the dashboard asking the
  question and skips the dashboard's own refusals.
  [LINUX_HOST.md → Internet or not](LINUX_HOST.md#internet-or-not) has the
  drop-ins. (One related switch works only from the environment, never
  from `domovoi/.env`: `DOMOVOI_MANAGE_SEARXNG=0`, which tells Domovoi to
  leave the search helper's container alone.)

The answer only sets **defaults**. A setting you set yourself, in
`domovoi/.env`, in the server's environment, or by saving it in the
dashboard, stays the way you set it. Settings → Internet shows which is
which and can hand a setting back to the answer
([Changing it later](#changing-it-later)).

## What each answer changes

**Settings that follow the answer** (unless you set them yourself):

| Feature | Setting | Not answered | Yes, always | Sometimes | No | Applies |
|---|---|---|---|---|---|---|
| Daily news briefing | `NEWS_ENABLED` | on | on | on | off | after a restart |
| Find artists by the names people say (MusicBrainz) | `MUSIC_ALIAS_FETCH_ENABLED` | off | on | on | off | at once |
| Automatic podcast downloads | `PODCAST_FEED_POLLER_ENABLED` | off | **on** | off | off | after a restart |
| Song recognition (AcoustID / Shazam) | `LIBRARY_ENRICHER_ENABLED` | on (does nothing without a key) | on **if** there is an AcoustID key or the Shazam add-on, else off | same as Yes | off | after a restart |
| Synced lyrics (LRCLIB) | `LYRICS_LRCLIB_ENABLED` | off | on | on | off | at once |
| Save lyrics as .lrc files | `LYRICS_WRITE_LRC` | on (does something only while LRCLIB is on) | on | on | off | at once |
| Extra voices at startup | `SEED_VOICE_CATALOG` | on | off | off | off | after a restart |

**What the answer switches by itself** (no setting of its own):

| | Not answered | Yes, always | Sometimes | No |
|---|---|---|---|---|
| The search helper (SearXNG) behind web answers | as you left it | started | started | stopped |
| The internet check (a connection to `1.1.1.1:443` every 30 s) | dials | dials | dials | **doesn't dial**; the dashboard says "Turned off for this box" |
| Anything that would reach the internet: feeds, streams, downloads, searches, plugins' requests through the SDK | allowed | allowed | allowed | **refused**: "internet access is turned off for this box (Settings → Internet)" |
| Online controls in the dashboard | as today | as today | as today | greyed, marked **needs internet · Settings → Internet** |
| `HF_HUB_OFFLINE` (the Hugging Face libraries' offline switch) | as you set it | as you set it | as you set it | set to `1` at the core's start, unless you set it yourself |

**What no answer changes:**

- **Topic news every morning** (`NEWS_AUTO_FETCH`, Settings → Configuration
  → News) stays each household's own opt-in, off by default.
- **Microsoft Edge voices** stay your choice (`TTS_ENGINE=edge`, Settings →
  Voices). They are never used as a stand-in for the local voice, and
  under **No** they aren't used at all.
- **Your voice never leaves the box.** Whisper always loads its model from
  the server's own disk first; a model downloads only when it isn't there
  yet, and never under **No**.

Restart-tier settings keep their current value until the core restarts,
like any other restart-tier change: Settings → Internet lists them under
"restart required". Switching to or from **No** also asks for a restart,
so the Hugging Face libraries pick up `HF_HUB_OFFLINE`. Everything else in
the second table applies the moment you save.

---

## If your Domovoi will have internet

Answer **Yes, always**. That already starts the search helper, turns on
artist names from MusicBrainz, synced lyrics from LRCLIB and podcast
downloads, and turns song recognition on as soon as it has a key. What
stays yours to do:

1. **Set your town for local news:** Settings → Configuration → News →
   *Local news location* (`NEWS_LOCATION`). Applies at once.

   *Why:* the morning briefing gets a local block. Empty skips it.

2. **Song recognition for badly tagged music:** create a free AcoustID
   **application** at [acoustid.org/new-application](https://acoustid.org/new-application)
   and put its API key in `domovoi/.env`:

   ```
   ACOUSTID_API_KEY=<the application's key>
   ```

   then restart the core. It must be an application key: the key on your
   AcoustID user page is refused for lookups, and the core's log then
   says so. (Or install the `shazam` extra; either one is enough.)

   *Why:* tracks with missing or wrong tags get their real artist and
   title, so voice requests find them. Tracks the server looked at before
   it had a key are tried again by themselves, once a lookup has actually
   answered; a track is only ever marked "no match" after a lookup really
   ran, never because the internet was down, the key was refused or there
   was no key. For those older tracks the retry adds MusicBrainz ids and
   fills tags that are empty; it doesn't rewrite a title or artist they
   already have, so names you corrected by hand are kept.

3. **FM stations by frequency (radio plugin):** in
   `~/.domovoi/plugins/radio.env` (the home of the user the core runs
   as),

   ```
   RADIO_MARKET_STATE=CO
   RADIO_MARKET_CITY=Denver
   ```

   with your own two-letter state and city, restart the core, then press
   **Import FCC FM** once on the Stations page (admin sign-in).
   `RADIO_FCC_IMPORT_ON_BOOT=true` does the same at every start instead;
   the button is enough, the list rarely changes.

   *Why:* "play 97.5 FM" finds the station in your market rather than any
   97.5 in the country.

4. **Fill the satellite media cache.** First install `openwakeword` into
   the server's Python environment, so the wake-word models can be cached
   too:

   ```bash
   pip install --no-deps openwakeword
   ```

   Then: Satellites → *prepare satellite media* → **Refresh caches**
   (admin sign-in; the wheel fetch can take minutes).

   *Why:* the SD cards you prepare from the dashboard carry the satellite's
   Python wheels, system packages and wake-word models, so a new satellite
   sets itself up from the card instead of from the internet.

5. **One-click updates (Linux):** install the update unit,
   [LINUX_HOST.md → Updates from the dashboard](LINUX_HOST.md#updates-from-the-dashboard).

   *Why:* **Pull the latest** and **Restart to apply changes** (Settings →
   Configuration → Version) then also re-sync dependencies, run
   migrations, rebuild the music image, keep the search helper in step
   with your answer, and roll back if the result isn't healthy.

**Already on if you answered Yes:**

- **The search helper (SearXNG)**, behind the weather, sports scores,
  prices and current events ("Want me to check that online?"),
  "double-check that", and news feed discovery. Saving **Yes** or
  **Sometimes** starts it (the first start downloads the pinned image,
  a few hundred MB, in the background); `dev.sh` / `dev.ps1` and the
  Linux update unit start it too, and Docker brings it back after every
  reboot. It listens on `127.0.0.1:6888`, so the LAN can't reach it.
  Settings → Internet shows whether it is starting, running or couldn't
  start (and why), with a **start it again** button for a first start that
  failed or timed out. If it isn't running, Domovoi says so ("I can't
  search the web right now — my search helper isn't running") instead of
  pretending it searched; by hand, from `domovoi/`:
  `docker compose up -d searxng`.
- **Other names for artists** from MusicBrainz, so "play suicide boys"
  finds `$uicideboy$`. It sends your library's artist names to
  musicbrainz.org, one a second (about an hour and a half the first time
  for a couple of thousand artists, then only new ones), and pauses while
  the internet is down.
- **Podcast downloads:** new episodes of the shows you subscribe to
  download by themselves (every subscription is checked every 30
  minutes). Saving the answer lists it under "restart required"; it starts
  after the next restart. Subscribing by voice says whether it will
  download.
- **The news worker** fetches the general briefing every morning at 05:00
  (`NEWS_FETCH_HOUR`), so "what's the news" answers at once.

**Worth knowing, but not turned on by any answer:**

- **Microsoft Edge voices** (Settings → Voices, or `TTS_ENGINE=edge`):
  nicer voices, but the text of every reply goes to Microsoft.
- **Extra voices** (`SEED_VOICE_CATALOG=true`): 9 more Piper voices (about
  0.7 GB) and 19 Microsoft Edge voices registered at startup.
- **Topic news every morning** (`NEWS_AUTO_FETCH`).
- **A custom wake word.** The trainer downloads about 8 GB of training
  data the first time. See [`scripts/wake_word/README.md`](../scripts/wake_word/README.md).

---

## Sometimes

Answer **Sometimes**. It is **Yes** minus podcast downloads, which stay off
until you turn them on yourself (`PODCAST_FEED_POLLER_ENABLED=true` in
`domovoi/.env`, then restart), because a slow or metered line is the wrong
place for episodes to download on their own.

- Do the whole [Before you disconnect](#before-you-disconnect) list too:
  the first time Domovoi needs something it can only download is usually
  the day the line is down.
- Everything that is on waits for the internet by itself and picks up
  again when it's back: the search helper's answers, artist names, the news
  worker, song recognition. A lookup that fails because the line dropped is
  simply tried again later.
- Update only while connected: pull **and** restart in the same sitting.

---

## Running Domovoi with no internet

### Before you disconnect

Do these once while the server is still online. The first time Domovoi
needs something it can only download is usually the day the line is down.

- [ ] **Start the core once and let it finish booting.** That builds the
      music container image (check: `docker image inspect domovoi-mpd:latest`),
      downloads the Whisper model you configured, and downloads the default
      Piper voice (the log says `speech warm-up: Piper voice … ready`). The
      first `docker compose up -d postgres` and `docker compose run --rm
      flyway` pulled the database images.
- [ ] **Download the Whisper fallback model too.** The core falls back to
      `WHISPER_CPU_FALLBACK_MODEL` (default `small.en`) when the configured
      model can't load, and it only downloads it then. Skip this if
      `WHISPER_MODEL` is already `small.en`. Run it as the user the core
      runs as, so it lands in that user's cache:

      ```bash
      python -c "from faster_whisper import download_model; download_model('small.en')"
      ```

      With the Linux units that is
      `sudo -u domovoi -H /opt/domovoi/.venv/bin/python -c "…"`.
- [ ] **Pull every Ollama model you'll want** (`ollama pull <model>`, or
      Settings → Models, which shows the model each role uses).
- [ ] **The extra voices**, if you want them: `SEED_VOICE_CATALOG=true`
      in `domovoi/.env` and one restart while online downloads them.
      Voices you upload on Settings → Voices are already on the server.
- [ ] **The fast lane**, if you use it: `python -m domovoi.fast_lane fetch`.
- [ ] **A custom wake word**, if you want one: train it now (the trainer
      downloads its training data the first time).
- [ ] **Install the plugins you want** (the Plugins page). Installing one
      from GitHub, and the Python packages it needs, takes the internet.
- [ ] **Fill the satellite media cache**
      ([item 4 above](#if-your-domovoi-will-have-internet), `openwakeword`
      first). Prepare the satellites' cards after that. A card built from
      complete caches carries what the satellite needs; if a cache was
      incomplete, the build warns and the satellite fetches the rest over
      its own connection. The manual path in
      [`satellite/PROVISIONING.md`](../satellite/PROVISIONING.md) installs
      over the Pi's own connection, so build those satellites while the
      house is online.
- [ ] **Set the time zone:** `sudo timedatectl set-timezone <Area/City>`.
      Without internet the server can't reach public time servers. If your
      router or another box on the LAN serves NTP, point the server at it
      (`NTP=<its address>` in `/etc/systemd/timesyncd.conf`, then
      `sudo systemctl restart systemd-timesyncd`). The satellites set their
      clocks from the server, so the house stays consistent even if the
      server drifts.

### Then answer No

Settings → Internet → **No, keep everything in the house** (or
`INTERNET_ACCESS=never`). That is all: there is nothing else to switch
off.

- The internet check stops dialing.
- Every way out is refused, with "internet access is turned off for this
  box (Settings → Internet)": feeds, streams, searches, model and plugin
  downloads, updates, and plugins' requests through the SDK.
- The news worker, artist names, podcast downloads, song recognition and
  the extra voices are off, unless you set one on yourself.
- The search helper container is stopped.
- The online controls are greyed with "needs internet", not hidden, so
  you can find them again.
- `HF_HUB_OFFLINE=1` is set for the core at its next start, so the Hugging
  Face libraries don't try either.
- A room that is playing an internet station stops, and internet stations
  are taken out of every room's music queue, so neither a reboot (the
  music player resumes what it had queued) nor "resume" can bring one
  back. Your own music stays queued.
- Transfers that were already running stop: a station playing in a
  browser, a model download (marked "cancelled: internet access was turned
  off"), a podcast episode download (back to waiting). Song recognition
  stops by itself.
- A satellite installs a plugin's system packages from its local package
  cache only; anything that would need a download waits until the answer
  changes.
- Nothing is marked as broken because of it: no feed is flagged dead, no
  episode failed, no track "no match", no radio station unreachable. Turn
  the answer back to Yes and everything picks up where it was.

Leave `CONNECTIVITY_PROBE_TARGET` alone. It only matters while the
internet is allowed, and it must point at an internet address: pointed at
your router, it would tell Domovoi the internet is up when it isn't.

### What works with no internet

- The wake word, speech recognition, the language models and the Piper
  voice.
- Music and your media library, playlists, and the podcasts and
  audiobooks already on the server.
- Timers, reminders, the clock and the calculator.
- Intercom announcements and drop-in between rooms.
- Voice profiles, memories and voice notes; the calendar and documents.
- The dashboard and the Android app on your network.
- FM radio through an RTL-SDR dongle (radio plugin, `RADIO_SDR_ENABLED`),
  including song detection against your own library.
- Saving a podcast feed (by its address) or a radio station for later:
  nothing is fetched until the answer changes.
- General questions, answered from what the local language model already
  knows.

### What doesn't

- Web answers: the weather, scores, prices, current events, "double-check
  that". Domovoi says why: "I'm set to stay off the internet, so I can't
  check that."
- Fresh news. The briefing reads whatever was fetched last.
- Internet radio and station search.
- Podcast search, and new episodes.
- Song recognition: the library enricher and the radio plugin's Shazam
  step.
- Synced lyrics from LRCLIB. Lyrics in `.lrc` files and in the songs' own
  tags still show, and so do the ones LRCLIB sent before.
- Microsoft Edge voices: Piper speaks instead.
- Downloading models, voices and wake-word training data; installing
  plugins from GitHub (a plugin zip whose Python packages are already
  installed still installs).
- Updates: **Check for updates** and **Pull the latest** are greyed, and
  on Linux the update unit refuses (touching nothing) an update that
  would download: new Python dependencies, a new music player image, or a
  moved container image pin (Postgres, Flyway, Letta, the search helper).

#### Updating a box answered No

Switch the answer to **Sometimes**, pull **and** restart in the same
sitting, then switch it back to **No** and restart once more. While the
answer is Sometimes, Sometimes is real:

- saving it starts the search helper (the first time that downloads its
  image, a few hundred MB; every start also fetches its own rule lists,
  see [What goes out](#what-goes-out));
- artist names from MusicBrainz follow at once, so the artist-names sweep
  starts sending your artists' names;
- synced lyrics from LRCLIB follow at once too, so songs' titles, artists,
  albums and lengths start going to lrclib.net;
- the restart boots the news worker.

Switching back to **No** stops the search helper, the artist names and the
lyrics lookups at once; the second restart turns the news worker off again
and sets `HF_HUB_OFFLINE` back. To keep that window quiet, set
`MUSIC_ALIAS_FETCH_ENABLED=false` and `LYRICS_LRCLIB_ENABLED=false`
(Settings → Configuration) before you switch; they then stay off whatever
the answer says until you press **follow the answer again**.

---

## Internet in the house, but Domovoi shouldn't use it

Answer **No**: it is real. Domovoi's own code then contacts nothing
outside your network: no internet check, no feeds, no searches, no voices,
no model or plugin downloads, no updates, no internet stations in the
rooms' music players, and plugins' requests through the SDK are refused
too.

What the answer can't reach, because it isn't the server:

- **The browsers and phones** in the house. They talk to Domovoi on your
  network; podcast artwork comes from the server too. But a link you open,
  or a picture someone pasted into a document from a website, loads from
  wherever it points.
- **The satellites' own system** (Raspberry Pi OS): it keeps its clock with
  public time servers, and a satellite being set up installs packages
  over its own connection on its second boot. The Domovoi part of the
  satellite talks only to the server (under **No**, a plugin's system
  packages come from the satellite's local package cache only), and the
  video satellite's kiosk browser is started with its background traffic
  switched off.
- **Docker Desktop and Ollama themselves** check for their own updates; so
  does the server's operating system.
- **A plugin that makes its own connections** without the SDK (raw
  `httpx`, its own downloads, a `docker pull`) is its author's
  responsibility; its install screen lists what it does, and Settings →
  Internet lists every enabled plugin that asks for the network as one
  that "may reach the internet on its own". Today that includes Coders
  Farm's own optional plugins: the media-provider plugin's dashboard
  search and stream, Image generation's engine, package and model
  downloads, and the container images Kiwix, Jellyfin and RomM pull when
  they set themselves up. Disable one to keep it in the house. (The radio
  plugin that ships with Domovoi follows the answer.)
- **A language model outside the house.** Ollama counts as local, but an
  `OLLAMA_URL` that points at a machine on the internet, or an Ollama
  "cloud" model (a name ending in `-cloud`), sends every question there.
  Settings → Internet warns about both under **No**.

If nothing in the house should reach out at all, block the server, and the
satellites once they are set up, at your router as well.

---

## Changing it later

**Settings → Internet** (admin sign-in):

- shows whether the server is connected right now, not connected, or
  turned off for this box;
- changes the answer. The search helper starts or stops at once, and so
  does everything in the [second table](#what-each-answer-changes). Saves
  are applied one after another: switch to Yes and then to No while the
  helper's first download runs, and it is stopped as soon as the download
  ends;
- shows the search helper's state, with **start it again**;
- lists every setting that follows the answer, with its value and a tag:
  - **follows the answer**;
  - **set in domovoi/.env · follow the answer again**: set by hand (or by
    an older `.env` template, below). The button hands it back to the
    answer: it comments the line out of `domovoi/.env` (the line stays in
    the file, marked, so you can see what it was) and applies the
    answer's value;
  - **set in the server's environment**: change it there;
- lists the plugins that may reach the internet on their own, and, under
  **No**, warns about a language model outside the house;
- says when a restart is needed, the same way Settings → Configuration
  does.

**Installed before October 2026?** Your `domovoi/.env` was copied from an
older `.env.example`, which set `SEED_VOICE_CATALOG=true` and
`LIBRARY_ENRICHER_ENABLED=true` explicitly. Those two show as **set in
domovoi/.env**, so the answer leaves them alone until you press **follow
the answer again** next to them.

**Going online:** switch to **Yes** or **Sometimes**, then do the
[items that stay yours](#if-your-domovoi-will-have-internet).

**Going offline:** do [Before you disconnect](#before-you-disconnect)
first, while the line is still there, then switch to **No**.

---

## What goes out

None of the extras send your voice or recordings. With the internet
allowed, they send short text:

- a question or claim you asked to have checked online, through your own
  SearXNG, which passes it on to public search engines. SearXNG also
  fetches a few things of its own every time its container starts, before
  anyone searches: the ClearURLs rule lists (clearurls.xyz,
  raw.githubusercontent.com), a Wikidata query, and the radio-browser
  server list. That is why **No** stops the container instead of just not
  searching;
- your library's artist names, to musicbrainz.org (the artist-names
  extra);
- a song's title, artist, album and length, to lrclib.net (synced
  lyrics), for songs with no timed lyrics of their own;
- the addresses of news feeds and of the podcasts you subscribe to, and a
  show's name when you subscribe by voice or search Discover (Apple's
  iTunes search); the server downloads each show's artwork once, so your
  browser and phone don't contact the publisher;
- audio fingerprints of your music files (AcoustID), never the files;
- station searches, an FM station's call sign when you favorite it, and
  the FCC import (radio plugin); short clips of a favorited internet
  station's audio to Shazam to name the song.

And only if you choose them: the text of every reply to Microsoft for an
Edge voice (and, for a registered Edge voice, its fixed greeting clips,
rendered once while the internet is up), and downloads an admin starts
(models, plugins, updates, the satellite cache).

The full list, with every off switch: [FAQ → What touches the internet](FAQ.md#what-touches-the-internet-and-how-do-i-turn-each-thing-off)
and [Security & privacy → What leaves your network](SECURITY_PRIVACY.md#what-leaves-your-network--and-how-to-turn-each-thing-off).
