# Radio — internet radio + FM for Domovoi

Published by **Coders Farm** · MIT · bundled with Domovoi and enabled by
default. This plugin is also the **reference example for Domovoi plugin
development** — see the note for developers at the bottom.

## What it does

* **Internet radio.** Search a community station directory
  (radio-browser.info) from the dashboard's Stations page, favorite
  stations, and play them by voice ("stream KEXP", "tune to the news
  station") in any room, or in the browser via the page's player.
* **FM via RTL-SDR (optional hardware).** With a USB RTL-SDR dongle,
  "play 97.5 FM" tunes real over-the-air FM and streams it to the room —
  this works **fully offline**. The FCC FM catalog for your state loads
  with one click (Import FCC FM), and favorited FM stations get their
  internet simulcast URL resolved automatically when one exists.
* **Passive song detection.** Two background detectors watch your
  favorited stations and record what's playing:
  * an **ICY metadata poller** reads now-playing titles straight from
    the stream (cheap; covers ~90 % of internet stations);
  * an **audio sampler** grabs short clips with ffmpeg and runs a
    two-tier identify chain — your **local library fingerprints** first
    (free, offline, catches songs you already own), then an **online
    song-identification service (Shazam)**.
* **Automatic library acquisition.** A detected song you don't own yet
  is queued into Domovoi's media-acquisition queue. The radio plugin
  does not download anything itself and doesn't know what will: any
  installed media-provider plugin fulfills the queue. With no provider
  installed, detections still log and the queue holds the requests —
  installing a provider later drains the backlog.

## Voice commands

| Say | Does |
|---|---|
| "stream KEXP" / "tune to the jazz station" | play a favorited station by name |
| "play 97.5 FM" | tune a frequency (FM/SDR, or a favorited simulcast) |
| "stop the radio" / "stop streaming" | stop playback in the room |

Station-name commands work on **favorited** stations — favorite them on
the Stations page first.

## Hardware notes (FM / RTL-SDR)

FM tuning is optional and off by default (`RADIO_SDR_ENABLED=false`).
You need:

* an RTL2832U-based USB dongle (any cheap RTL-SDR stick);
* the rtl-sdr tools on PATH (`rtl_fm`, `rtl_test`);
* **Windows**: a one-time [Zadig](https://zadig.akeo.ie/) run to swap
  the dongle's USB interface to WinUSB — without it the OS claims it as
  a DVB-T receiver and `rtl_fm` can't open it;
* `ffmpeg` on PATH (also required for the audio sampler).

The demodulated FM audio is served over HTTP on
`RADIO_SDR_STREAM_BASE:RADIO_SDR_HTTP_PORT`. The room's MPD runs in a
container, so **its localhost is not your machine's localhost** — set
`RADIO_SDR_STREAM_BASE` to a LAN hostname/IP that resolves from inside
the MPD container.

At startup the plugin probes for the dongle (`rtl_test`); if the probe
fails, FM commands answer with a friendly explainer instead of erroring.

## Configuration

All settings live under the plugin's `RADIO_` prefix (dashboard →
Settings → Radio, persisted to `~/.domovoi/plugins/radio.env`). The
interesting ones:

| Setting | Default | Meaning |
|---|---|---|
| `RADIO_DETECTIONS_RETENTION_DAYS` | `90` | prune detection history older than this (0 = keep forever) |
| `RADIO_MARKET_STATE` / `RADIO_MARKET_CITY` | empty | FCC import scope + "play 97.5 fm" disambiguation |
| `RADIO_FCC_IMPORT_ON_BOOT` | `false` | refresh the FCC catalog at startup (when online) |
| `RADIO_SAMPLER_ENABLED` | `true` | the ffmpeg audio sampler |
| `RADIO_ICY_POLLER_ENABLED` | `true` | the stream-metadata poller |
| `RADIO_SDR_ENABLED` | `false` | the RTL-SDR FM tuner |
| `RADIO_SDR_STREAM_BASE` | `http://127.0.0.1` | host MPD dials for FM audio (see hardware notes) |
| `RADIO_FINGERPRINTER_ENABLED` | `true` | fingerprint library tracks for offline matching |

## Privacy & permissions (what the manifest warns about)

* The sampler can send short audio clips of whatever a favorited
  station is playing to an online song-identification service (Shazam).
  Disable with `RADIO_SAMPLER_ENABLED=false` — ICY polling and local
  fingerprint matching keep working.
* The plugin queries external directories (radio-browser.info, the FCC)
  over the network and runs `ffmpeg` (and `rtl_fm` with SDR hardware
  enabled) on your machine.
* Detections are broadcast on the plugin event bus
  (`plugin.radio.detection_recorded`) so other installed plugins can see
  what a station played; the radio plugin itself only observes — it
  never downloads anything.

## For plugin developers

This plugin is the worked example the Domovoi plugin-dev guide walks
through — it exercises every extension plane:

* **voice**: a handler at priority band 280 with anchored fast paths,
  per-path `offline_ok`, a reachable `fallback_offline`, and a
  namespaced confirmation kind (`radio.station_choice`);
* **background work**: four poll workers, two connectivity-gated
  startup hooks, and an in-process hardware manager shared via
  `sdk.state`;
* **data**: its own Postgres schema (`plugin_radio`) with soft
  references into core tables, cleaned up by event-bus subscriptions
  *paired with* a periodic reconciliation sweep (the bus is
  fire-and-forget — the sweep is truth);
* **UI**: a web router at `/api/plugins/radio`, a zero-build JSX page
  registered through `window.DomovoiPlugins.radio`, manifest-declared
  realtime channels, and a sidebar badge;
* **couplings**: acquisitions via `sdk.acquisition.enqueue` (no
  provider vocabulary anywhere), now-playing stamping via
  `sdk.playback.play_url(source="radio")`, and a favorites matcher via
  `sdk.now_playing.register_matcher`.

Layout, manifest spec, and test-harness docs: `docs/plugin-dev/` in the
main repository. The plugin's own suite lives in `tests/` and runs with
the repo-wide `pytest`.
