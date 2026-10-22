# Radio

## What the Radio plugin does

Radio plays internet radio stations and, with an RTL-SDR USB dongle, real
FM radio. It comes with Domovoi and is on by default. Its page on the
dashboard is **Stations**.

## How do I find and play a station?

1. Open **Stations** on the dashboard.
2. Type a station's name or genre in the search box. Your favorites that
   match show first, then the station directory's results.
3. Click a station to play it. It plays in this browser. To hear a station
   in a room, say "stream" and its name there (see below).

Clicking plays a station without making it a favorite. **Recent** lists
the last 10 stations you played. An FM station with no internet copy can't
play in the browser; tell a room to tune it by voice.

## How do I play a station by voice?

Star the station on the Stations page first: voice finds stations by name
only among your favorites. Then say "stream KEXP" or "tune to the jazz
station" in any room. "Stop the radio" stops it.

## How do I listen to FM radio?

FM needs an RTL-SDR USB dongle plugged into the Domovoi server, and FM
turned on in the plugin's settings (`RADIO_SDR_ENABLED=true` in
`~/.domovoi/plugins/radio.env`, then restart the server). On a server
installed natively on Windows the dongle needs a one-time driver swap with
Zadig; a server installed with the Windows installer can't see USB
devices, so FM needs a Linux server. Then say "play 97.5 FM".
FM works with no internet at all. **Import FCC FM** on the Stations page
loads the FM stations for your state.

## Why does a station not play?

- With the internet set to No (Settings → Internet), internet stations
  can't play; FM still can.
- Voice can't find a station that isn't a favorite: star it first.
- "Play 97.5 FM" answers with an explanation when no dongle was found at
  start-up: plug it in and restart the server.
- Some stations stop broadcasting or change their address. Search for the
  station again and star the new entry.

## What is song detection?

For your favorite stations, Radio notes the songs that are playing: from
the station's own titles, and from short clips it identifies against your
own music library first, then an online song-identification service (Shazam).
That means a short clip of a favorite station can leave your home. Set
the internet to No (Settings → Internet) and nothing is sent; to keep the
internet on but stop the clips, set `RADIO_SAMPLER_ENABLED=false` in
`~/.domovoi/plugins/radio.env` and restart the server. Radio only
records what it heard; it never downloads anything.
