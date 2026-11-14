# Will your Domovoi have internet?

Installing Domovoi needs the internet: Python packages, Docker images, the
speech models and, unless you leave AI out of setup, the language models
all download during setup. After that it is your choice, and Domovoi asks
you once:

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
- [Lookups in Chat](#lookups-in-chat)
- [Changing it later](#changing-it-later)
- [What goes out](#what-goes-out)
- [Egress inventory](#egress-inventory): every host, and which answer allows it
- [Check it yourself](#check-it-yourself)

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
  then restart the core (under `never` the core stops the search helper
  as it starts; it never starts it). `yes` / `no` / `online` /
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

- **Topic news every morning** (`NEWS_AUTO_FETCH`, Settings → Advanced
  → News) stays each household's own opt-in, off by default.
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

1. **Set your town for local news:** Settings → Advanced → News →
   *Local news location* (`NEWS_LOCATION`). Applies at once.

   *Why:* the morning briefing gets a local block. Empty skips it.

2. **Song recognition for badly tagged music:** create a free AcoustID
   **application** at [acoustid.org/new-application](https://acoustid.org/new-application)
   and paste its API key into the dashboard: Settings → Advanced →
   Library → **AcoustID key** (signed in as admin). The Music page's
   "song recognition needs a free AcoustID key" and the song-recognition
   row of Settings → Internet open that field. **test the key** asks
   AcoustID once whether it takes the key (it isn't offered when the
   answer is **No**). It must be an application key: the key on your
   AcoustID user page is refused for lookups, and both the test and the
   core's log say so. Once saved, the key is never shown again, only
   whether one is set; **clear** removes it. (Or install the `shazam`
   extra; either one is enough.)

   The key applies at once. Where song recognition was off only because
   there was no key, it turns on after a restart: the save says so, and
   Settings → Internet shows "→ on after a restart". A key never turns it
   on under **No**, or when you switched it off yourself.

   Without the dashboard, put the key in `domovoi/.env` instead and
   restart the core:

   ```
   ACOUSTID_API_KEY=<the application's key>
   ```

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

4. **Fill the satellite media cache.** Settings → Rooms → *prepare a room's
   SD card* → **Refresh caches** (admin sign-in; the wheel fetch can take
   minutes). The wake-word models come from pinned URLs and are checked
   against pinned digests; the server no longer needs the `openwakeword`
   package for that. An offline card is prepared only when the cache holds
   every pinned model, and the satellite checks them again on first boot.

   *Why:* the SD cards you prepare from the dashboard carry the satellite's
   Python wheels, system packages and wake-word models, so a new satellite
   sets itself up from the card instead of from the internet.

5. **One-click updates (Linux, and the Windows install):** on a Linux
   server, install the update unit,
   [LINUX_HOST.md → Updates from the dashboard](LINUX_HOST.md#updates-from-the-dashboard);
   the Windows install runs the same update unit inside its own distro.

   *Why:* **Update Domovoi** and **Restart to apply changes** (Settings →
   About & updates) then also re-sync dependencies, run
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
  `docker compose up -d searxng`. Chat's lookups use it too, after asking
  (see [Lookups in Chat](#lookups-in-chat)).
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
- **Calendars added by link** (Settings → Household → Calendars: a Google,
  Outlook or iCloud calendar's private ICS address) are read every 30
  minutes under **Yes** (and not answered), every 2 hours under
  **Sometimes**, and never under **No**, where each one keeps its last copy
  and says how old it is. A failure waits longer each time (15 minutes,
  doubling, up to 12 hours). An admin's **refresh now** reads one at once,
  except under **No**.

**Worth knowing, but not turned on by any answer:**

- **Extra voices** (`SEED_VOICE_CATALOG=true`): the other two of Domovoi's
  three Piper voices (about 130 MB, downloaded on first use unless the
  provisioning fetched them).
- **Topic news every morning** (`NEWS_AUTO_FETCH`).
- **A custom wake word.** Turning training on (Settings → Voice → Wake words)
  downloads about 22 GB once: the trainer's image and its training data.
  Under No it can't be turned on, and answering No while it downloads stops
  the download. See [`scripts/wake_word/README.md`](../scripts/wake_word/README.md).

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
- [ ] **A custom wake word**, if you want one: turn training on in
      Settings → Voice → Wake words and train it now (about 22 GB, once). Under No
      training is refused, even with the data already downloaded.
- [ ] **Install the plugins you want** (the Plugins page). Installing one
      from GitHub, and the Python packages it needs, takes the internet.
- [ ] **Fill the satellite media cache**
      ([item 4 above](#if-your-domovoi-will-have-internet)). Prepare the
      satellites' cards after that. A card built from
      complete caches carries what the satellite needs; if a cache was
      incomplete, the build warns and the satellite fetches the rest over
      its own connection. The manual path in
      [`satellite/PROVISIONING.md`](../satellite/PROVISIONING.md) installs
      over the Pi's own connection, so build those satellites while the
      house is online.
- [ ] **Set the time zone (Linux server):** `sudo timedatectl set-timezone <Area/City>`.
      On the Windows install, check the PC's own clock and time zone in
      Windows. Without internet the server can't reach public time servers. If your
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
- The calendars household phones share from the Domovoi app: the phone
  sends them over your own network, so they keep updating. A calendar
  added by link keeps showing its last copy, and the Calendar page and
  Settings say it can't refresh and how old that copy is.
- The dashboard and the Android app on your network.
- FM radio through an RTL-SDR dongle (radio plugin, `RADIO_SDR_ENABLED`),
  including song detection against your own library.
- Saving a podcast feed (by its address) or a radio station for later:
  nothing is fetched until the answer changes.
- General questions, answered from what the local language model already
  knows.
- Asking Domovoi how Domovoi works (the help desk answers from the manual
  that comes with it).

### What doesn't

- Web answers: the weather, scores, prices, current events, "double-check
  that". Domovoi says why: "I'm set to stay off the internet, so I can't
  check that." Chat's lookups on Wikipedia and the web are refused the same
  way; its manual and offline library still answer.
- Fresh news. The briefing reads whatever was fetched last.
- Internet radio and station search.
- Podcast search, and new episodes.
- New events from calendars added by link (Google, Outlook, iCloud).
- Song recognition: the library enricher and the radio plugin's Shazam
  step.
- Synced lyrics from LRCLIB. Lyrics in `.lrc` files and in the songs' own
  tags still show, and so do the ones LRCLIB sent before.
- Downloading models, voices and wake-word training data; installing
  plugins from GitHub (a plugin zip whose Python packages are already
  installed still installs).
- Updates: **Check for updates** and **Update Domovoi** are greyed, and
  on Linux the update unit refuses (touching nothing) an update that
  would download: new Python dependencies, a new music player image, or a
  moved container image pin (Postgres, Flyway, Letta, the search helper).
  On a Windows install no Windows part is offered either (the release's
  Setup comes from GitHub), and the Version card says why.

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
- song popularity from ListenBrainz follows at once, so your songs'
  MusicBrainz ids start going to api.listenbrainz.org (similar artists
  stay off under Sometimes);
- the restart boots the news worker.

Switching back to **No** stops the search helper, the artist names and the
lyrics lookups at once; the second restart turns the news worker off again
and sets `HF_HUB_OFFLINE` back, and on a Linux server with the update unit
it masks Ubuntu's own updaters again (the first restart, under Sometimes,
unmasked them: [LINUX_HOST.md → Internet or not](LINUX_HOST.md#internet-or-not)). To keep that window quiet, set
`MUSIC_ALIAS_FETCH_ENABLED=false` and `LYRICS_LRCLIB_ENABLED=false`
(Settings → Advanced) before you switch; they then stay off whatever
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
  wherever it points. The Android app's chat on the phone can also look
  things up by itself, only after you allow it on a card
  ([Lookups in Chat](#lookups-in-chat)).
- **The satellites' own system** (Raspberry Pi OS): it keeps its clock with
  public time servers, and a satellite being set up installs packages
  over its own connection on its second boot. The Domovoi part of the
  satellite talks only to the server (under **No**, a plugin's system
  packages come from the satellite's local package cache only), and the
  video satellite's kiosk browser is started with its background traffic
  switched off.
- **Docker and Ollama themselves** (Docker Desktop and Ollama for Windows
  too, if you run your own) check for their own updates; so does the
  server's operating system, and on the Windows install, Windows and WSL.
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

## Lookups in Chat

Chat can look something up when your own sources don't have the answer,
and it always asks first. Before anything leaves the house, the chat shows
a card with the exact words (or the page address) and where they would go:
**Allow once**, **Allow in this chat** or **Not now**. A chat that was
allowed has a chip in its header that turns the permission off again, and
the history list marks a chat that went online with a globe.

- **The order:** Domovoi's own manual and an offline library (Kiwix, if
  installed) come first. They never leave the house and work under **No**.
  Then Wikipedia and Wikidata, through the search helper (straight to
  Wikipedia if the helper doesn't answer). Then the open web, or the
  search you chose.
- **Settings → Chat & AI → Search** (admin): **Always allow internet**
  (off by default; while it is off every chat asks first), the order of
  the sources, and **Use your own search**: this server's helper, your own
  SearXNG address, or Brave Search with your own key (the key is never
  shown again and goes only to Brave).
- **A page you name** is fetched only if its address starts with
  `https://`, with no cookies and no scripts. Its main text is read, up to
  2 MB.
- **Under No**, only the manual and the offline library answer; the rest
  is refused. **Sometimes** and **Yes** allow the rest, with the card.
- **What stays:** the server's log names the source and the outcome, never
  the words. The chat keeps its answer, with the titles and links of what
  it read, until you delete the chat.
- **On a phone:** a chat on the Android app does the same at home, through
  your server, which follows your answer; a server set to **No** is not
  gone around. Away from home, or when your server doesn't answer, the
  phone itself asks Wikipedia and, if you set one in the app's model
  settings, your own search too, after the same card. Those requests don't
  pass through your server. Your own SearXNG address must start with
  `https://`, unless it is on your home network (a private address such as
  192.168.x.x, or a `.local` name), where `http://` is accepted.

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
- says when a restart is needed, the same way Settings → Advanced
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
- the words of a Chat lookup you allowed: to Wikipedia and Wikidata
  (through the search helper, or straight to Wikipedia if it doesn't
  answer), then to the search engines the helper asks, or to your own
  SearXNG, or to Brave Search with your own key; and the address of a page
  you asked Chat to read, to that page's site ([Lookups in
  Chat](#lookups-in-chat)). A phone chat away from home sends its words to
  Wikipedia, and to your own search if you set one, itself;
- your library's artist names, to musicbrainz.org (the artist-names
  extra);
- a song's title, artist, album and length, to lrclib.net (synced
  lyrics), for songs with no timed lyrics of their own;
- the MusicBrainz ids of your library's artists and songs, to
  listenbrainz.org (similar artists and song popularity for Up next) —
  ids only, never names or what you played;
- the addresses of news feeds and of the podcasts you subscribe to, and a
  show's name when you subscribe by voice or search Discover (Apple's
  iTunes search); the server downloads each show's artwork once, so your
  browser and phone don't contact the publisher;
- audio fingerprints of your music files (AcoustID; with the Shazam
  add-on, Shazam's own audio signature when AcoustID has no answer),
  never the files;
- the private address of each calendar an admin adds by link, to that
  calendar's own server (Google, Microsoft or Apple), as a plain request
  for the calendar file: nothing about the house goes with it, and nothing
  is ever written back;
- station searches, an FM station's call sign when you favorite it, and
  the FCC import (radio plugin); short clips of a favorited internet
  station's audio to Shazam to name the song.

And only if you choose them: downloads an admin starts (models, plugins,
updates, the satellite cache). One of them stays current by itself: once an
admin has put the Android app on the server for phones, a box answered
**Yes, always** fetches the newer app an update names (from the project's
GitHub release, once per start with retries); under **Sometimes** it waits
for the admin's **Download the app to this server**, as big downloads do. On a Windows install that includes the
Windows part of **Update Domovoi**: the tray on the PC downloads the
release's `DomovoiSetup.exe`, its manifest and their signatures from
github.com, after an admin asked for it in the dashboard or chose it in the
tray's menu, never by itself
([WINDOWS_HOST.md](WINDOWS_HOST.md#update-domovoi-both-halves)). No voice sends anything: they are all local
(the Microsoft Edge online voices were dropped in October 2026).

### Every touchpoint, and how to turn each off

Every optional outbound touchpoint, sourced from the code. One answer covers all of them: **No, keep everything in the house** in Settings → Privacy → Internet (`INTERNET_ACCESS=never`) refuses every one, and the online controls say "needs internet". The last column is how to turn one off on its own on a server that does use the internet.

| Feature | What goes out | Where | How to disable |
|---|---|---|---|
| Connectivity probe | A TCP connection, no payload | `1.1.1.1:443` every 30 s | Answer **No** in Settings → Privacy → Internet: the probe stops dialing and the dashboard says "Turned off for this box". Otherwise **leave `CONNECTIVITY_PROBE_TARGET` on an internet address**: the failing dial is how Domovoi knows the line is down. Pointed at a LAN host (your router), it reports online when the internet isn't there, and online features fail slowly instead of saying they can't. If your network blocks `1.1.1.1`, use another public `host:443` (e.g. `9.9.9.9:443`) |
| Model downloads | Model files, once | Hugging Face (Whisper models, Piper voices), Ollama model pulls, the fast lane's model (GitHub, only once `fastlane_mode` is `shadow`) | One-time each. Whisper loads its model from the server's own cache first and asks Hugging Face only for a model that isn't downloaded yet; under **No** it never asks (and `HF_HUB_OFFLINE=1` is set for the core), so download every model you'll use before you disconnect ([how](#before-you-disconnect)) |
| News briefings | RSS feed fetches; topic feed discovery via your local SearXNG | The feeds you configure | `NEWS_ENABLED=false` kills all background fetching (it follows the internet answer: off for **No**); `NEWS_AUTO_FETCH` (topic feeds) is already off by default |
| "Double-check that" / web answers (weather, scores, "check that online") | Search queries; and, every time the container starts, its own fetches of the ClearURLs rule lists, a Wikidata query and the radio-browser server list | Your own SearXNG container (localhost-only, port 6888), which queries public search engines | It follows the internet answer: saving **Yes** or **Sometimes** starts it, **No** stops it (no search is sent even if it runs, but its startup fetches would be, which is why it is stopped). Not answered: it runs only if you started it (`docker compose up -d searxng`). When it isn't running Domovoi says so — "I can't search the web right now — my search helper isn't running" — instead of claiming it searched |
| Chat lookups — **only after you allow each one** (or "Always allow internet", off by default) | A chat's search words, or the address of a page you name | Your local SearXNG (Wikipedia and Wikidata first), Wikipedia, your own SearXNG or Brave Search (with your key); the page itself when you name one (the server reads it: HTTPS only, no cookies, 2 MB); a phone away from home asks Wikipedia, and reads a page you name, itself | Answer **Not now** on the card, leave Settings → Chat & AI → Search → **Always allow internet** off, or answer **No** in Settings → Privacy → Internet (then only the manual and an offline library are searched) |
| Radio plugin: station directory | Station-name searches; an FM favorite's call sign, to find its internet stream (when you favorite it, and at startup for FM favorites that still have none) | radio-browser.info | Disable or uninstall the radio plugin from the dashboard's Plugins page |
| Radio plugin: FCC station import | One bulk query on demand | transition.fcc.gov | Off unless you click **Import FCC FM** (or set `RADIO_FCC_IMPORT_ON_BOOT=true`) |
| Radio plugin: song detection | Short clips of favorited radio *streams*; ICY metadata polls | Shazam; the stations themselves | `RADIO_SAMPLER_ENABLED=false`, `RADIO_ICY_POLLER_ENABLED=false` in the plugin's own file, `~/.domovoi/plugins/radio.env` (restart the core) |
| Library enricher | Audio fingerprints of your library files (and, to AcoustID, the AcoustID key) | AcoustID (only with an AcoustID application key: Settings → System → Advanced → Library → **AcoustID key**, or `ACOUSTID_API_KEY`; over HTTPS) and Shazam (only with the `shazam` extra installed); metadata lookups to MusicBrainz. **test the key** beside the key sends one lookup of a made-up fingerprint, never one of your files, only when an admin presses it | `LIBRARY_ENRICHER_ENABLED=false`. With neither a key nor Shazam it sends nothing and marks nothing. Under **No** neither the enricher nor the test sends anything |
| MusicBrainz alias lookup — **off unless your internet answer is Yes or Sometimes** | Artist names from your library — tagged artists, and the "Artist" of an untagged file whose name reads "Artist - Title" (so a home recording named "Grandma Edith - Happy Birthday" would send "Grandma Edith"): one search per artist, once, then only for artists added later (about an hour and a half the first time for ~2,000 artists); the spoken stage names that come back are kept, legal names and a person's names sharing a word with one are filtered out and never stored. A band's other names (members, "The Farriss Brothers" for INXS) can be kept | musicbrainz.org, one request a second, only while the server is online | On when the internet answer is **Yes** or **Sometimes**, off when it is **No** or not answered. Settings → System → Advanced → Library → "Look up other names on MusicBrainz" (`music_alias_fetch_enabled` / `MUSIC_ALIAS_FETCH_ENABLED`) overrides the answer; switching it off stops it before its next request |
| Synced lyrics (LRCLIB) — **off unless your internet answer is Yes or Sometimes** | A song's title, artist, album and length, for songs with no timed lyrics of their own (no `.lrc` beside them, none in their tags): one lookup per song, then again after a few weeks for songs LRCLIB didn't have (several hours the first time for 5,000 songs, about a night, longer when LRCLIB asks it to slow down). The lyrics that come back stay on the server, and with "Save lyrics as .lrc files" timed ones are also saved as a `.lrc` next to the song (never over a `.lrc` you made) | lrclib.net, about one request a second, only while the server is online | On when the internet answer is **Yes** or **Sometimes**, off when it is **No** or not answered. Settings → System → Advanced → Library → "Synced lyrics from LRCLIB" (`lyrics_lrclib_enabled` / `LYRICS_LRCLIB_ENABLED`) overrides the answer; switching it off stops it before its next request. Lyrics from `.lrc` files and the songs' own tags never touch the internet |
| Similar artists for Up next (ListenBrainz) — **off unless your internet answer is Yes** | The MusicBrainz ids of your library's artists (public catalogue numbers, no names, nothing you played): one request per artist that has an id, then each again after 90 days (about 600 requests, roughly 10–15 minutes, the first time for a 6,000-song library). The similar artists that come back are kept on the server; Up next reads them from there and never asks the internet while choosing songs | labs.api.listenbrainz.org, at most one request a second, only while the server is online, from a background job only | On when the internet answer is **Yes**, off for **Sometimes**, **No** or not answered. Settings → Household → Music mix → "Similar artists from the internet" (`music_similar_artists_enabled` / `MUSIC_SIMILAR_ARTISTS_ENABLED`) overrides the answer; switching it off stops it before its next request and deletes what it fetched |
| Song popularity for Up next (ListenBrainz) — **off unless your internet answer is Yes or Sometimes** | The MusicBrainz ids of your library's songs that have one (no titles, nothing you played): 100 songs a request (about 40 requests the first time for 6,000 songs), each song again after 30 days | api.listenbrainz.org, at most one request a second, only while the server is online, from a background job only | On when the internet answer is **Yes** or **Sometimes**, off when it is **No** or not answered. Settings → Household → Music mix → "Song popularity from the internet" (`music_popularity_enabled` / `MUSIC_POPULARITY_ENABLED`) overrides the answer; switching it off stops it before its next request and deletes what it fetched |
| Podcasts | Feed polls + episode downloads; a show's name when you subscribe by voice or search in Discover; each show's artwork, once | Feeds you subscribe to; Apple's iTunes search | Automatic downloads follow the internet answer: on for **Yes**, off for **Sometimes**, **No** and not answered (`PODCAST_FEED_POLLER_ENABLED`). Search runs only when you ask. Show artwork is downloaded **by the server** and served to your browser and phone, which never contact the publisher |
| Calendars by link — **only the ones an admin adds** | A request for each calendar's ICS file at its private address (with the last answer's ETag / date, so an unchanged calendar costs almost nothing), at most 10 MB | The calendar's own server: Google (`calendar.google.com`), Microsoft (`outlook.office365.com`, `outlook.live.com`), Apple (`p*-caldav.icloud.com`), or wherever the link points | Remove the calendar (Settings → Household → Calendars). Every 30 minutes under **Yes**, every 2 hours under **Sometimes**, never under **No**. Phones' shared calendars never use the internet: the phone sends them to the server over your network |
| Extra voices | Downloads of the other two Piper voices (about 130 MB) | Hugging Face | Off once the internet question is answered (any answer), unless `SEED_VOICE_CATALOG` is set in your `domovoi/.env` — which a server installed before October 2026 has (its old template wrote `SEED_VOICE_CATALOG=true`; Settings → Privacy → Internet → **follow the answer again** hands it back). Not answered, it's on unless `SEED_VOICE_CATALOG=false` |
| Wake-word training — **off until an admin turns it on** | The trainer image's build and its training data, about 22 GB, once; a rebuild when an update changes the trainer. On a box with an NVIDIA GPU, the install and the Linux update unit also fetch the NVIDIA Container Toolkit | Docker Hub, GitHub, PyPI and Ubuntu's archive (the build); Hugging Face and Backblaze B2 (the data); NVIDIA's apt repository (the toolkit) | Don't turn it on (Settings → Voice → Wake words). Under **No** it can't be turned on, no training starts, and the update unit downloads none of it ([SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#wake-word-training-the-gpu-runtime-the-trainer-image-and-what-it-downloads)) |
| A USB stick for an old computer — **only when an admin prepares one** | Debian's installer image (793 MB), once, kept for the next stick; on a server installed before October 2026, the `xorriso` package at the next update or restart | cdimage.debian.org (held to a pinned SHA-256); Ubuntu's archive | Don't press **Prepare the stick**. Under **No** it is refused unless the image is already on the server, and the update unit installs nothing. The old computer itself downloads Debian's packages and the satellite's libraries while it sets up ([SPARE_DEVICE_SATELLITES.md](SPARE_DEVICE_SATELLITES.md#old-computers-usb-stick)) |
| Things an admin starts | Downloads, only when an admin clicks: **Update Domovoi** (and, with the update unit, Python packages and the music image's Debian packages, and the search helper's image when it starts it), plugin installs, model installs, **Refresh caches** for satellite media, the font catalogue and a font added for Documents | GitHub (the fonts: GitHub's copy of Google Fonts); PyPI and the PyTorch CPU index; Debian's mirrors; Docker Hub; the Ollama registry; Hugging Face (a new Whisper size) | Nothing runs on its own; don't click them. Under **No** they are greyed and the server refuses them |

The install-time trust screen for any *third-party* plugin lists that plugin's own network behavior — see [What are plugins, and are they safe?](FAQ.md#what-are-plugins-and-are-they-safe)

Every host behind these rows is in the [egress inventory](#egress-inventory).

More on the security side: [Security & privacy → What leaves your network](SECURITY_PRIVACY.md#what-leaves-your-network--and-how-to-turn-each-thing-off).

---

## Egress inventory

Every host on the internet that Domovoi's own code can contact, who
contacts it, what for, and which internet answer allows it. The table
above says what each feature sends; this one lists the destinations, so a
household (or its router) can check traffic against it. A test,
`domovoi/tests/test_egress_inventory.py`, reads these tables and fails
when the code gains an internet address that isn't listed here
([keeping it complete](#keeping-the-inventory-complete)).

What the answer columns mean:

- **on**: may go out by itself, as the features in
  [What each answer changes](#what-each-answer-changes) are set;
- **asked**: only when someone asks for it (a voice request, a button, a
  setting an admin turns on);
- **refused**: the server refuses it, whatever anyone asks
  (`domovoi/egress.py` is the one choke point: the HTTP clients, the
  outbound-URL check in `domovoi/net_safety.py`, and plugins' requests
  through the SDK all go through it);
- **not the server**: a phone, a satellite or an installer making its own
  connection; the server's answer doesn't reach it (see
  [what the answer can't reach](#internet-in-the-house-but-domovoi-shouldnt-use-it)).

Hosts that redirect (GitHub release downloads, Hugging Face files) hand
the download on to their own storage hosts, listed in the same row; under
**No** a redirect is refused like the first request.

### The server

| Host | Contacted by | What for | Yes | Sometimes | No |
|---|---|---|---|---|---|
| `1.1.1.1` (TCP 443, no data) | core | The internet check, every 30 s (`CONNECTIVITY_PROBE_TARGET`) | on | on | off |
| `huggingface.co` · `cdn-lfs.huggingface.co` · `cas-bridge.xethub.hf.co` | core | Whisper models (a size not on disk yet), Piper voices (a voice not on disk yet; the extra voices) | asked | asked | refused |
| `registry.ollama.ai` · `ollama.com` | Ollama, at the core's request | Model pulls (Settings → Models). An Ollama `-cloud` model sends every question to `ollama.com` | asked | asked | pulls refused; a `-cloud` model is Ollama's own connection, which Settings → Internet warns about |
| `github.com` · `objects.githubusercontent.com` · `release-assets.githubusercontent.com` | core, the update unit | **Update Domovoi** (`git fetch` of the project); the Android app for phones; the Windows satellite Setup; the Linux satellite's pinned Python (python-build-standalone); the wake-word models (openWakeWord's release, Domovoi's wake-word models repository); the fast lane's model (sherpa-onnx); the XVF3800 host tool for satellite cards | asked; the newer Android app an update names is fetched by itself | asked | refused |
| `api.github.com` · `codeload.github.com` | core | Installing a plugin from GitHub | asked | asked | refused |
| `api.github.com` · `raw.githubusercontent.com` | dashboard | Fonts: the font catalogue, and the files of a font you add (Documents) | asked | asked | refused |
| `pypi.org` · `files.pythonhosted.org` · `download.pytorch.org` | core, the update unit | A plugin's Python packages; the update's dependency sync; the satellites' wheels for **Refresh caches** | asked | asked | refused |
| `www.python.org` | core | The Windows satellite's embeddable Python, for **Refresh caches** | asked | asked | refused |
| `registry-1.docker.io` · `auth.docker.io` · `production.cloudflare.docker.com` · `deb.debian.org` | Docker on the server, at Domovoi's request | Container images (Postgres, Flyway, the search helper, Letta, the music player's Debian base), and Debian packages for the music image and the satellite cards | asked (an update, a first start, **Refresh caches**) | asked | refused: the update unit refuses an update that would download |
| `musicbrainz.org` | core | Other names for artists; song recognition's metadata and genres | on | on | off |
| `lrclib.net` | core | Synced lyrics | on | on | off |
| `labs.api.listenbrainz.org` | core | Similar artists for Up next (artist ids only) | on | off | off |
| `api.listenbrainz.org` | core | Song popularity for Up next (song ids only) | on | on | off |
| `api.acoustid.org` | core | Song recognition (fingerprints); **test the key** | on, with a key | on, with a key | off |
| `amp.shazam.com` | core (the `shazam` add-on), radio plugin | Song recognition when AcoustID has no answer; naming the song on a favorited station | on, with the add-on | on, with the add-on | off |
| `itunes.apple.com` | core, dashboard | Podcast search (by voice, and Discover) | asked | asked | refused |
| `feeds.bbci.co.uk` · `feeds.npr.org` · `feeds.arstechnica.com` · `www.theverge.com` · `www.sciencedaily.com` · `www.espn.com`, and the feeds you add | core | The news briefing's default feeds, and your own | on | on | off |
| The host of each podcast feed you subscribe to, and wherever its episodes and artwork live | core | Feed checks, episode downloads, each show's artwork once | on | asked (downloads off) | refused |
| The host of each calendar an admin adds by link (Google's `calendar.google.com`, Microsoft's `outlook.office365.com` · `outlook.live.com`, Apple's iCloud calendar hosts) | core (every 30 min), dashboard (adding one, **refresh now**) | Reading the calendar's ICS file, read-only | on | on (every 2 h) | refused: the last copy stays |
| `en.wikipedia.org` · `api.search.brave.com`, your own SearXNG, a page you name | core | Chat lookups, each after you allow it on its card | asked | asked | refused |
| The public search engines in SearXNG's default settings, `en.wikipedia.org`, `query.wikidata.org`, `clearurls.xyz`, `raw.githubusercontent.com`, the radio-browser server list | the search helper (SearXNG, a local container) | Web answers, "double-check that", news feed discovery; its own rule lists at every start | on | on | the container is stopped |
| `cdimage.debian.org` | core | The Debian installer image for a USB stick for an old computer | asked | asked | refused unless the image is already on the server |
| `f002.backblazeb2.com` · `huggingface.co` · `github.com` · `download.pytorch.org` · `pypi.org` · `archive.ubuntu.com` · `nvidia.github.io` | the wake-word trainer's build, the update unit | Wake-word training: the trainer image, its training data, and on an NVIDIA box the NVIDIA Container Toolkit | asked (an admin turns training on) | asked | refused |
| `de1.api.radio-browser.info` | radio plugin | Station search; an FM favorite's call sign | on | on | refused |
| `transition.fcc.gov` | radio plugin | **Import FCC FM** | asked | asked | refused |
| Each internet station's own stream host | core (the rooms' music players), radio plugin | Playing a station; its song titles (ICY); short clips for song detection | on | on | refused, and stations leave the queues |

### Installing, and the other devices

| Host | Contacted by | What for | Yes | Sometimes | No |
|---|---|---|---|---|---|
| `download.docker.com` · `github.com` · `nvidia.github.io`, Ubuntu's archive | the Linux install script (`provision.sh`) | Docker, Ollama's release, the NVIDIA toolkit, the system's packages: installing needs the internet | not the server | not the server | not the server |
| `releases.ubuntu.com` · `github.com` · `raw.githubusercontent.com` | the Windows Setup and the tray icon | Ubuntu's WSL image, WSL itself, the install script; the Windows part of **Update Domovoi** (when an admin asks) | not the server | not the server | the tray offers no Windows update |
| `github.com` · `pypi.org` · `files.pythonhosted.org` · `www.python.org` · `deb.debian.org`, Raspberry Pi OS's archive and time servers | a satellite being set up, and its operating system | Packages, Python and wake-word models the server's card or cache doesn't carry; the system clock | not the server | not the server | not the server; the Domovoi part of a satellite talks only to the server |
| `huggingface.co` · `en.wikipedia.org` · `api.search.brave.com`, your own SearXNG, a page you name | the Android app | On-device models and voices you choose to download; a phone chat's lookups away from home, after its card | not the server | not the server | not the server; at home the phone's lookups go through the server and follow its answer |

Plugins installed from elsewhere make their own connections: each one's
install screen lists them, and Settings → Internet lists every enabled
plugin that "may reach the internet on its own". They are outside this
inventory. Links in the dashboard and the app (to this repository, to
AcoustID's sign-up page, to the Raspberry Pi imager) open in your browser
only when you click them.

### Voice requests that use the internet

Every voice handler declares whether it needs the internet
(`requires_network`, [ARCHITECTURE.md](ARCHITECTURE.md)). These are the
ones that do, and where they go; the test holds this list equal to the
handlers' own declarations. Every other handler declares `no` and holds no
internet address of its own.

| Handler | Declares | Uses |
|---|---|---|
| `double_check` | yes | The search helper (SearXNG) |
| `news` | degraded | The news feeds; the search helper for topic feeds |
| `spoken_audio` | degraded | `itunes.apple.com`; the podcast feeds' own hosts |
| `radio` (radio plugin) | degraded | `de1.api.radio-browser.info`; the stations' own hosts |

Two handlers that declare `no` can start a download when someone asks for
one: `voice` ("download Linda's voice", from `huggingface.co`) and
`library` (identifying songs, through song recognition). Under **No** both
say they're set to stay off the internet, and download nothing.

### Keeping the inventory complete

`domovoi/tests/test_egress_inventory.py` scans the code that runs on the
server, the satellites, the Windows host and the Android app for
`http://` and `https://` addresses. Each host must be one of:

- in a backticked host of the inventory's tables above;
- local, by `domovoi/egress.py`'s own rule (loopback and private
  addresses, `localhost`, `.local`, `.lan`, `.home.arpa`, single-label
  names), or a documentation name (`example.com`, `.example`, `.test`,
  `.invalid`);
- on the test's short list of addresses the code never contacts: XML
  namespaces, licence and attribution links, help links shown to people.

Adding a host? Add it to the right row here (or a new row, with the answer
that allows it), and make the request go through `domovoi/egress.py`. A
link that is only shown to people goes on the test's never-contacted list,
with a comment saying where it is shown. A host the code reaches without
writing an address (a library's own endpoint, a `docker pull`) can't be
found by the scan: list it here by hand, as the Ollama, Docker Hub and
Shazam rows are.

---

## Check it yourself

You don't have to take the inventory's word for it. Answer **No**, restart
the core once (Settings → Internet asks for it), then watch the server's
traffic.

**What you should still see under No,** all of it inside your network:
the satellites and the phones talking to the server (ports 6369, 6370 and
the rooms' music streams, 8050 and up), Ollama on the same box (11434),
multicast name lookups (mDNS, `224.0.0.251` port 5353) so `domovoi.local`
works, and DNS questions to your router. What the operating system does
for itself is not Domovoi: its clock (NTP) and its own update checks (on a
Linux server with the update unit, answering No masks Ubuntu's own
updaters: [LINUX_HOST.md → Internet or not](LINUX_HOST.md#internet-or-not)).

### At the router

The surest check, because it sees everything that leaves the house, the
containers and the operating system included. Give the server a fixed
address (a DHCP reservation), then look at the router's connection log or
traffic monitor for that address, or add a rule that blocks and logs its
traffic to the internet. With No answered, the log should stay empty apart
from the operating system's own clock and update checks.

### On a Linux server

Connections open right now, with the program that holds each, leaving out
the ones inside your network:

```bash
sudo ss -tunp | grep -vE '127\.0\.0\.|192\.168\.|[^0-9]10\.|172\.(1[6-9]|2[0-9]|3[01])\.|\[::1\]|\[f[cde][0-9a-f]*:'
```

`ss` sees the core, the dashboard and Ollama, but not the containers (the
search helper, the rooms' music players, Postgres), which have their own
network. `tcpdump` sees both: every packet the box sends to an address
outside your network, for as long as you leave it running:

```bash
sudo tcpdump -ni any '(ip and not dst net 10.0.0.0/8 and not dst net 172.16.0.0/12
  and not dst net 192.168.0.0/16 and not dst net 127.0.0.0/8
  and not dst net 169.254.0.0/16 and not dst net 224.0.0.0/4
  and not dst host 255.255.255.255)
  or (ip6 and not dst net fe80::/10 and not dst net fc00::/7
  and not dst net ff00::/8 and not dst host ::1)'
```

(Install it with `sudo apt install tcpdump` while the answer is still Yes.)
If your network uses another range (a VPN's `100.64.0.0/10`, say), leave it
out the same way. To catch a timer or a worker, leave it running for a
day, or add `-w egress.pcap` and open the file in Wireshark later.

### On the Windows install

The server runs inside its own WSL2 virtual machine, which belongs to a
hidden account and has no shell for you to open, so its connections don't
show as a Windows program in Resource Monitor or `netstat -b`. Watch the
PC's network adapter instead, with Windows' own packet monitor, from an
administrator PowerShell:

```powershell
pktmon start --capture --comp nics --file-name "$env:USERPROFILE\domovoi-egress.etl"
# ...leave it running for a while, then:
pktmon stop
pktmon etl2pcap "$env:USERPROFILE\domovoi-egress.etl" --out "$env:USERPROFILE\domovoi-egress.pcapng"
```

Open the `.pcapng` in Wireshark with the display filter
`ip && !(ip.dst==10.0.0.0/8 || ip.dst==172.16.0.0/12 || ip.dst==192.168.0.0/16 || ip.dst==224.0.0.0/4 || ip.dst==255.255.255.255)`.
The capture holds the whole PC: Windows, your browser and every other
program talk to the internet too, so the router check above is the clean
one. With WSL's mirrored networking the server uses the PC's own address,
so at the router its traffic and the PC's share one row.

### What Domovoi says about itself

Under **No**, every refusal is written to the core's log, once per kind of
request, as `internet off: refused <what>` (on Linux,
`journalctl -u domovoi-core | grep 'internet off'`; on Windows, `core.log`
from the tray icon's **Save logs for support...**). And you can ask the
gate itself whether it would let an address out (on a Linux server
installed by the script; in a checkout of your own, from its folder with
its Python):

```bash
cd /opt/domovoi
.venv/bin/python -m domovoi.egress --print-policy                   # never
.venv/bin/python -m domovoi.egress --check https://musicbrainz.org  # the refusal
.venv/bin/python -m domovoi.egress --check http://192.168.1.20      # allowed: on your network
```
