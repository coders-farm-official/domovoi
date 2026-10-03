# Will your Domovoi have internet?

Installing Domovoi needs the internet: Python packages, Docker images, the
language models and the speech models all download during setup. After
that it is your choice. Speech recognition, the language models and the
default voice all run on the server, so **your voice never leaves the
house, with or without internet**. The internet only adds extras.

Decide which of these fits the server, then do that section:

| Will the server have internet after setup? | Do this |
|---|---|
| **Yes, always.** It's on your home internet. | [Turn on the online extras](#if-your-domovoi-will-have-internet-turn-these-on) |
| **Sometimes.** The line comes and goes, or it's slow or metered. | [Download ahead](#before-you-disconnect), then turn on [the small extras](#sometimes-connected-boxes) |
| **No.** There's no internet there, or you don't want Domovoi to use it. | [Download ahead, then switch off what can't work](#running-domovoi-with-no-internet). If the house does have internet, also read [Internet in the house, but Domovoi shouldn't use it](#internet-in-the-house-but-domovoi-shouldnt-use-it) |

How to change a setting:

- **`domovoi/.env`** is Domovoi's settings file. The first run creates it
  from `.env.example` (`dev.sh` / `dev.ps1` do that for you). Edit it on
  the server, then restart the core.
- **Settings → Configuration** in the dashboard needs the admin sign-in.
  It saves to the same `.env`. Some settings apply at once; the page says
  when one needs a restart.
- **The radio plugin** reads its own file, `~/.domovoi/plugins/radio.env`,
  in the home of the user the core runs as. Restart the core after editing
  it.

"Restart the core" means `sudo systemctl restart domovoi-core` if you
installed the [Linux units](LINUX_HOST.md#make-it-an-appliance), or
stopping `dev.sh` / `dev.ps1` and starting it again.

Nothing here is permanent. Every item is a setting you can flip later.

---

## If your Domovoi will have internet, turn these on

In rough order of payoff.

1. **Start the search helper (SearXNG).** From `domovoi/`, once:

   ```bash
   docker compose up -d searxng
   ```

   Docker brings it back after every reboot (`restart: unless-stopped`).
   It listens on `127.0.0.1:6888`, so the LAN can't reach it.

   *Why:* the weather, sports scores, prices and current events ("Want me
   to check that online?"), "double-check that", and news feed discovery
   all search through it. Nothing starts it for you: not `dev.sh` /
   `dev.ps1`, not the Linux units. Without it the searches come back
   empty, and Domovoi answers "I checked online but couldn't find a clear
   answer to that" (for "double-check that": "I couldn't find anything
   about that to confirm or deny it").

2. **Set your town for local news:** Settings → Configuration → News →
   *Local news location* (`NEWS_LOCATION`). Applies at once.

   *Why:* the morning briefing gets a local block. Empty skips it.

3. **Leave the news worker on.** `NEWS_ENABLED` is on by default
   (Settings → Configuration → Workers → *News worker*).

   *Why:* the general briefing is fetched every morning at 05:00
   (`NEWS_FETCH_HOUR`), so "what's the news" answers at once.

4. **Optional: topic news every morning:** Settings → Configuration → News
   → *Auto-fetch topic news* (`NEWS_AUTO_FETCH`, off by default). Applies at
   once.

   *Why:* each person's topics of interest are fetched with the morning
   job, instead of Domovoi asking first. It is off by default on purpose:
   the household opts in.

5. **Podcast downloads:** in `domovoi/.env`,

   ```
   PODCAST_FEED_POLLER_ENABLED=true
   ```

   then restart the core.

   *Why:* new episodes of the shows you subscribe to download by
   themselves: every subscription is checked every 30 minutes and the
   newest 5 per show are kept. It is off by default,
   and until you turn it on a voice "subscribe to …" subscribes but
   downloads nothing, although the reply says "I'll download new episodes
   as they come out".

6. **Song recognition for badly tagged music:** get a free API key from
   [acoustid.org](https://acoustid.org/) (sign in and register an
   application; the application's key is the one that works for lookups),
   then in `domovoi/.env`:

   ```
   ACOUSTID_API_KEY=<your key>
   LIBRARY_ENRICHER_ENABLED=true
   ```

   (A fresh `.env` already has the second line.) Restart the core.

   *Why:* tracks with missing or wrong tags get their real artist and
   title, so voice requests find them.

   **Do this before the first start with your music in place and the
   internet up.** The enricher looks at each track once. Without a key it
   has nothing to ask (the optional second stage, Shazam, is the separate
   `shazam` extra and is not installed by default), and it still marks
   every track as looked up. A key added later is then never used on those
   tracks. To make it try them again:

   ```bash
   docker exec -i domovoi-postgres psql -U domovoi domovoi -c "UPDATE library_tracks SET enriched_at = NULL WHERE musicbrainz_recording_id IS NULL;"
   ```

   then say "enrich my library", or restart the core.

7. **FM stations by frequency (radio plugin):** in
   `~/.domovoi/plugins/radio.env`,

   ```
   RADIO_MARKET_STATE=CO
   RADIO_MARKET_CITY=Denver
   RADIO_FCC_IMPORT_ON_BOOT=true
   ```

   with your own two-letter state and city, then restart the core. The
   last line downloads your state's FM list again at every start. To do it
   just once instead, leave that line out and press **Import FCC FM** on
   the radio plugin's Stations page (admin sign-in) after the restart.

   *Why:* "play 97.5 FM" finds the station in your market rather than any
   97.5 in the country.

8. **Fill the satellite media cache.** First install `openwakeword` into
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

9. **One-click updates (Linux):** install the update unit,
   [LINUX_HOST.md → Updates from the dashboard](LINUX_HOST.md#updates-from-the-dashboard).

   *Why:* **Pull the latest** and **Restart to apply changes** (Settings →
   Configuration → Version) then also re-sync dependencies, run
   migrations, rebuild the music image, and roll back if the result isn't
   healthy.

10. **Worth knowing, but not turned on here:**

    - **Microsoft Edge voices** (`TTS_ENGINE=edge`, Settings →
      Configuration → Voice & speech → *TTS engine*): nicer voices, but the
      text of every reply goes to Microsoft.
    - **Extra voices.** A fresh `.env` has `SEED_VOICE_CATALOG=true`. On
      its first start with internet the core registers a catalog of 9
      Piper voices (about 0.7 GB, downloaded to render their clips) and 19
      Microsoft Edge voices, whose clips are rendered by Microsoft (see
      [What goes out](#what-goes-out)). Set `SEED_VOICE_CATALOG=false`
      before that first start if you'd rather it didn't.
    - **A custom wake word.** The trainer downloads about 8 GB of training
      data once. See [`scripts/wake_word/README.md`](../scripts/wake_word/README.md).

Coming: other names for artists from MusicBrainz, so an artist is found
by the name people say even when it's written differently, and synced
lyrics from LRCLIB. Both will be off until you turn them on, and this list will name
their settings when they ship.

---

## What goes out

None of the extras send your voice or recordings. They send short text:

- a question or claim you asked to have checked online, through your own
  SearXNG, which passes it on to public search engines;
- the addresses of news feeds and of the podcasts you subscribe to, and a
  show's name when you subscribe by voice or use Discover (Apple's iTunes
  search);
- audio fingerprints of your music files (AcoustID), never the files;
- station searches and the FCC import (radio plugin).

Three that are easy to miss, all only while the server has internet:

- **Clips for the Edge voice.** The voice registry always holds one
  Microsoft Edge voice (`TTS_EDGE_VOICE`, Aria by default; the extra
  voices above add 18 more). At startup the core renders each registered
  voice's clips that are missing: the "trouble reaching the network"
  notice, the voice sample ("Hi, I'm Domovoi…") and the wake greetings,
  including any you wrote under Settings → Greetings. For an Edge voice
  Microsoft renders them, so that fixed text goes to Microsoft once per
  clip. Never anything you said, and there's no setting for it yet.
- **The voice fallback.** With the default `piper` engine, a reply that
  Piper fails to speak goes to the Edge voice next and only then to the
  system voice, so on a server with internet that one reply's text reaches
  Microsoft. Keep the Piper voice working: the core's log says
  `speech warm-up: Piper voice … ready` at every start.
- **The Whisper check.** Each time the core starts, it asks huggingface.co
  whether its Whisper model changed, and downloads it again if it did. To
  stop that once the model is downloaded, set `HF_HUB_OFFLINE=1` in the
  core's environment ([below](#stop-the-whisper-check)).

The full list, with every off switch: [FAQ → What touches the internet](FAQ.md#what-touches-the-internet-and-how-do-i-turn-each-thing-off)
and [Security & privacy → What leaves your network](SECURITY_PRIVACY.md#what-leaves-your-network--and-how-to-turn-each-thing-off).

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
- [ ] **The fast lane**, if you use it: `python -m domovoi.fast_lane fetch`.
- [ ] **Install the plugins you want** (the Plugins page). Installing one
      from GitHub, and the Python packages it needs, takes the internet.
- [ ] **Fill the satellite media cache** (item 8 above, `openwakeword`
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

### Then switch off what can't work

In `domovoi/.env`, then restart the core:

```
LIBRARY_ENRICHER_ENABLED=false
SEED_VOICE_CATALOG=false
PODCAST_FEED_POLLER_ENABLED=false
NEWS_ENABLED=false
```

- `LIBRARY_ENRICHER_ENABLED=false`, so "enrich my library" can't mark
  your tracks as looked up when it can't look anything up.
- `SEED_VOICE_CATALOG=false`, so the core doesn't add the extra voices,
  which it would try to download and render without the internet. (Voices
  already added stay.)
- `PODCAST_FEED_POLLER_ENABLED=false` is the default; it's written here so
  it stays that way. The poller doesn't wait for the internet: switched on,
  it would try every feed every 30 minutes.
- `NEWS_ENABLED=false` is optional. The news worker already skips its
  fetch while the server is offline; off, it doesn't run at all.

In `~/.domovoi/plugins/radio.env` (radio plugin), unless you listen to FM
through an RTL-SDR dongle and have favorited FM stations:

```
RADIO_SAMPLER_ENABLED=false
```

The song-detection sampler doesn't check for the internet: it tries to
grab audio from every favorited internet station (up to 20 seconds each)
and fails. The ICY "now playing" poller waits for the internet by itself;
leave it.

**Leave `CONNECTIVITY_PROBE_TARGET` on an internet address** (the default
is `1.1.1.1:443`). With no internet the probe's connection simply fails,
and that is how Domovoi knows to answer offline. Pointing it at your
router makes Domovoi believe it is online: it offers to "check that
online", and online features fail slowly instead of saying they can't.

#### Stop the Whisper check

Optional, but it saves a failed lookup at every start: set `HF_HUB_OFFLINE=1`
in the core's **process environment**. A line in `domovoi/.env` won't do,
because Domovoi reads `.env` itself and doesn't hand it on to the
libraries.

- **Linux units:** a drop-in, as in
  [LINUX_HOST.md → Make it an appliance](LINUX_HOST.md#make-it-an-appliance):

  ```bash
  sudo mkdir -p /etc/systemd/system/domovoi-core.service.d
  printf '[Service]\nEnvironment=HF_HUB_OFFLINE=1\n' \
    | sudo tee /etc/systemd/system/domovoi-core.service.d/offline.conf
  sudo systemctl daemon-reload && sudo systemctl restart domovoi-core
  ```

- **Windows:** `setx HF_HUB_OFFLINE 1` as the account that runs the core,
  then start the core from a new terminal.

With it set, a Whisper model that isn't downloaded yet can't be fetched
either: to change `WHISPER_MODEL` later, remove the line, restart while
online, then put it back.

### What works with no internet

- The wake word, speech recognition, the language models and the Piper
  voice.
- Music and your media library, playlists, and the podcasts and
  audiobooks already on the server.
- Timers, reminders, the clock and the calculator.
- Intercom announcements and drop-in between rooms.
- Voice profiles, memories and voice notes; the calendar and documents.
- The dashboard and the Android app on your network.
- FM radio through an RTL-SDR dongle (radio plugin, `RADIO_SDR_ENABLED`).
- General questions, answered from what the local language model already
  knows.

### What doesn't

- Web answers: the weather, scores, prices, current events, "double-check
  that". Domovoi says so: "I can't check that right now — I don't have
  internet."
- Fresh news. The briefing reads whatever was fetched last.
- Internet radio and station search.
- Podcast search, new subscriptions and new episodes.
- Song recognition: the library enricher and the radio plugin's Shazam
  step.
- Downloading models or voices, and installing plugins from GitHub.
- Updates: **Pull the latest** needs GitHub, and the update unit needs
  PyPI when dependencies changed. Bring the server online for an update,
  and pull **and** restart in the same sitting.

### Internet in the house, but Domovoi shouldn't use it

Do everything above, with two changes: `NEWS_ENABLED=false` is not
optional for you, and don't start the search helper (SearXNG). Even then,
while the server can reach the internet, a few things still go out on
their own:

- the internet check: a connection that carries no data, to `1.1.1.1`
  every 30 seconds;
- the Edge voice's clips, rendered by Microsoft whenever one is missing or
  its text changed (see [What goes out](#what-goes-out));
- the Whisper check at each start, unless you [stop it](#stop-the-whisper-check);
- the radio plugin, if you have favorite stations: it checks the internet
  stations' "now playing" every 30 seconds, and at each start looks up an
  internet stream for FM favorites that don't have one.

Things you ask for also go out when you ask: a podcast or station search,
a model or plugin install, an update.

There is no single off switch for this yet. The sure way is to block the
server's internet at your router once [Before you disconnect](#before-you-disconnect)
is done. Domovoi then sees no internet and works as described above. The
satellites use the house Wi-Fi too: if nothing in the system should reach
out, block them as well once they are set up (they take their clock from
the server).

---

## Sometimes-connected boxes

- Do the whole [Before you disconnect](#before-you-disconnect) list.
- Turn on items 1-4 of [the online list](#if-your-domovoi-will-have-internet-turn-these-on):
  the search helper, local news, the news worker, and topic news if you
  want it. They wait for the connection by themselves.
- Leave podcast downloads off on a slow or metered line.
- Leave the library enricher off (`LIBRARY_ENRICHER_ENABLED=false`) unless
  you have an AcoustID key and the line is reliable while it runs. A
  lookup that fails because the line dropped still marks the track as
  looked up.
- Leave `CONNECTIVITY_PROBE_TARGET` alone, as above.
- Update only while connected: pull **and** restart in the same sitting.

## Changing your answer later

- **Going online:** do [the online list](#if-your-domovoi-will-have-internet-turn-these-on).
  Downloads happen as features need them.
- **Going offline:** do [Before you disconnect](#before-you-disconnect)
  first, while the line is still there, then switch things off.
