# Media: library, playlists, MPD, and the acquisition queue

How Domovoi stores music, plays it in a room, and obtains new media. Sources
of truth: `domovoi/db/migrations/V001__baseline.sql` (tables),
`domovoi/mpd_provisioner.py`, `domovoi/acquisitions.py`,
`domovoi/streaming.py` (the music handshake), `domovoi/now_playing.py`.

## Data model

```mermaid
erDiagram
    library_tracks {
        serial id PK
        text file_path UK
        text title
        text artist
        text album
        int duration_sec
        text source "open enum: manual|indexed|upload|plugin slug"
        text source_id
        text added_via "voice|manual"
        text musicbrainz_recording_id "enrichment"
        bool favorited
        timestamptz added_at
    }
    playlists {
        serial id PK
        text name UK "case-insensitive unique"
        text description
        text cover_color
        text cover_emoji
        int resume_position "ordered-mode resume"
    }
    playlist_tracks {
        serial id PK
        int playlist_id FK
        int track_id FK
        int position "gaps allowed on purpose"
    }
    media_plays {
        bigserial id PK
        text room_id
        text source "open enum: library|playlist|spoken_audio|plugin slug"
        text title
        text artist
        text video_id "opaque external ref (provider plugins)"
        text url
        text stream_url
        int library_track_id FK "SET NULL — history survives deletes"
        timestamptz started_at
    }
    media_acquisitions {
        bigserial id PK
        text kind "query | url"
        text text "search text or URL — never a provider wire format"
        jsonb metadata "producer hints: artist, title, ..."
        text requested_by "voice:handler | web | chat | plugin:slug"
        text origin_ref "soft ref: plugin_slug:table:id"
        bigint attach_to_playlist_id "soft ref, NO FK"
        text dedup_key "partial-unique while pending/claimed"
        text status "pending|claimed|done|failed|unfulfillable|cancelled"
        text claimed_by "fulfiller plugin slug"
        int attempts
        timestamptz next_attempt_at
        jsonb result "library_track_id + file_path"
    }
    mpd_rooms {
        text room_id PK
        int control_port UK "6650+N"
        int http_port UK "8050+N"
        text container_name UK "domovoi-mpd-room"
        timestamptz last_connected_at
    }
    devices {
        text device_id PK "client-minted: browser-xxxx | android-xxxx"
        text name "human label; seeded from the platform, then editable"
        text platform "advisory, no CHECK"
        text user_agent
        timestamptz last_seen_at
    }
    room_queue_items {
        text room_id PK "with song_id"
        int song_id PK "MPD songid — stable across reorders"
        text device_id "soft ref to devices, NO FK"
        text device_name "snapshot at add time"
        timestamptz added_at
    }
    queue_device_blocks {
        bigint id PK
        text device_id "nullable — survives a rename"
        text device_name "nullable — survives a reinstall"
        text room_id "NULL = every room"
        text note
    }

    playlists ||--o{ playlist_tracks : "CASCADE"
    library_tracks ||--o{ playlist_tracks : "CASCADE"
    library_tracks ||--o{ media_plays : "SET NULL"
    media_acquisitions }o..o| playlists : "attach_to_playlist_id (soft ref)"
    media_acquisitions }o..o| library_tracks : "result.library_track_id"
    room_queue_items }o..o| devices : "device_id (soft ref)"
    queue_device_blocks }o..o| devices : "device_id OR device_name (soft)"
```

Things the shapes encode deliberately:

* **Open enums.** `library_tracks.source` and `media_plays.source` carry no
  CHECK — provider plugins register their own slug in the in-process
  `registered_values` registry and stamp it without a core migration.
* **Soft refs across the acquisition boundary.** A queued acquisition may
  outlive the playlist it was meant to feed; `attach_to_playlist_id` has no
  FK, and the completion path re-checks the playlist and skips the attach
  with a log if it vanished.
* **History survives deletion.** `media_plays.library_track_id` is
  `ON DELETE SET NULL`, so "what played in the kitchen last night" keeps
  answering after a track is removed.
* **`room_queue_items` annotates MPD, it doesn't duplicate it.** There is no
  DB copy of a room's queue — see "Editing a room's queue" below for why, and
  why the key is a songid rather than a position. `device_id` has no FK:
  forgetting a device must not delete queue history, and voice-added entries
  have no device at all.
* **A queue block is two keys, either of which matches.** `device_id` and
  `device_name` are both nullable with a CHECK that at least one is set, so a
  block can follow a rename (id) or a reinstall (name). Paired partial unique
  indexes keep one block per target per scope — a plain UNIQUE would let
  duplicate all-rooms blocks pile up, because Postgres treats NULL `room_id`
  values as distinct.
* **`mpd_rooms` is the provisioner's source of truth** — which room owns
  which host-port pair and container, surviving restarts. Port allocation is
  `max + 1` from the bases (control 6650, http-stream 8050), serialized by a
  Postgres advisory lock.

## Playing a song in a room

Every satellite room gets its own MPD daemon (docker container
`domovoi-mpd-<room>`, lazily created on the room's first WebSocket connect),
so queues, current track, and volume are independent per room. The Pi plays
music by pulling the room's MPD http stream with `mpg123`.

```mermaid
sequenceDiagram
    autonumber
    participant Pi as Satellite Pi
    participant S as Core (StreamSession)
    participant M as MusicHandler
    participant MPD as Room MPD container
    participant NP as Now-playing registry

    Pi->>S: "play <song>" (utterance frames)
    S->>M: route() → fast path (band 300, greedy ^play)
    Note over M: local library match first; a local miss<br/>cascades to a streaming-search-provider<br/>capability if one is installed
    M->>MPD: queue track, leave PAUSED against the<br/>always-on silence stream
    M->>NP: stamp(room, source, {stream_url, title})
    M-->>S: Response {music_action:"start",<br/>music_stream_url}
    S-->>Pi: response_start + TTS ("Playing …") + response_end
    S-->>Pi: music_start {stream_url}
    Note over S: arms the music_ready fallback timer<br/>(music_prepare_fallback_sec)
    Pi->>Pi: spawn mpg123, prime buffer against<br/>MPD's silence stream
    Pi->>S: music_ready
    S->>MPD: resume()
    Note over Pi: song frames land in an already-primed<br/>buffer — no first-second stutter.<br/>Satellites that never send music_ready<br/>still get music via the fallback resume.
    S->>S: record media_plays row (source, title, room)
```

Around that happy path:

* **Wake capture kills the Pi's player**, so after a non-music turn ("what
  time is it?" mid-song) the server auto-resends `music_start` from its
  `resumable_music` memory — unless the response carries `expect_followup`,
  in which case resume is suppressed for one turn so the player can't
  saturate the mic while the Pi listens for the reply.
* **"Stop"** sends `music_stop`, clears the room's resumable entry and its
  now-playing stamp.
* **The playback-state sweeper** (a core poll worker) clears now-playing
  stamps whose room's MPD no longer plays the stamped stream, so a stale
  card can't outlive reality.

## Editing a room's queue

A room's queue is **MPD's**, and deliberately stays that way: it is what the
satellite's stream actually plays from, it survives a core restart, and voice
commands mutate it directly. A second DB-backed queue would be a rival source
of truth that drifts the first time anything touches MPD without going through
us. So the queue stays in MPD, and Domovoi *annotates* it.

That annotation is `room_queue_items` (core migration V010), keyed by
`(room_id, MPD songid)` — **not** position. A songid is stable for the life of
a queue entry: moving an entry, or inserting ahead of it, changes every `Pos`
but no `Id`. Two consequences fall out of that choice:

* a reorder needs no database write at all, and
* "remove entry 3" can never become "remove the wrong song" because someone
  else reordered first.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (dashboard / app)
    participant W as Web backend (:6369)
    participant DB as Postgres
    participant K as Core (:6370)
    participant MPD as Room MPD container

    Note over C: registers itself once per boot:<br/>POST /api/devices/register {device_id, name}
    C->>W: POST /api/music/queue/kitchen/add<br/>{track_ids, device_id}
    W->>DB: look up the device's name,<br/>then queue_device_blocks (id OR name, room OR all)
    alt blocked
        W-->>C: 403 "Kids iPad isn't allowed to edit the queue in kitchen"
    else allowed
        W->>K: POST /v1/admin/music/queue/kitchen/add
        K->>MPD: addid each resolved file (NO clear)
        MPD-->>K: new songids
        K-->>W: {queued:[{song_id, file}], started}
        W->>DB: INSERT room_queue_items (room, song_id, device_id, device_name)
        W-->>C: 200
    end

    C->>W: GET /api/music/queue/kitchen?device_id=…
    W->>K: GET /v1/admin/music/queue/kitchen
    K->>MPD: playlistinfo + currentsong
    W->>DB: join room_queue_items LEFT JOIN devices,<br/>reap rows whose songid has left the queue
    W-->>C: items[] with added_by + playing, editable, blocked_reason
```

Around that path:

* **The read is never blocked.** A blocked device still sees what's on; it
  gets `editable:false` and a `blocked_reason` so the UI can disable its own
  controls and explain, rather than discovering a 403 on first click. The
  server re-checks every edit regardless — the flag is a courtesy, not the
  gate.
* **`added_by` prefers the live device name** (`COALESCE(devices.name,
  room_queue_items.device_name)`), so renaming a device relabels its queue
  entries; a device that has since been forgotten keeps the name it used.
  Entries with no record at all — voice commands, casts from before devices
  had names — render nothing rather than a guess.
* **Provenance is reaped on read**, bounded by the room's own queue, so a
  long-lived room can't accumulate rows for songs that played months ago.
* **Blocks match id OR name.** The id survives a rename (the obvious way to
  slip a block); the name survives a reinstall. Neither is a security
  boundary — a device id is self-asserted on a trusted LAN. See
  [../SECURITY_PRIVACY.md](../SECURITY_PRIVACY.md).

## Acquiring media

"Get this into my library" is a durable queue, not an RPC — the full design
rationale is in
[../ARCHITECTURE.md](../ARCHITECTURE.md#media-acquisition-queue-domovoiacquisitionspy).

```mermaid
sequenceDiagram
    autonumber
    participant P as Producer<br/>(voice / web / chat / plugin)
    participant A as AcquisitionService
    participant PG as media_acquisitions
    participant F as Fulfiller plugin<br/>(its own poll worker)
    participant W as Web dashboard

    P->>A: enqueue(kind, text, metadata, dedup_key, …)
    A->>PG: layer 1 — library fuzzy match (pg_trgm)
    alt already in library
        A-->>P: "«title» is already in your library."
    else duplicate in live queue
        A->>PG: INSERT … ON CONFLICT (dedup_key) DO NOTHING
        A-->>P: "That's already queued for download."
    else enqueued
        A->>PG: row status=pending + pg_notify('acquisitions_changed')
        A-->>P: "Queued — I'll fetch it shortly."<br/>or the graceful-absence line when<br/>no fulfiller is installed
    end
    PG--)W: NOTIFY → realtime channel "acquisitions"

    loop fulfiller tick
        F->>A: claim_next(session)
        A->>PG: SELECT … FOR UPDATE SKIP LOCKED<br/>status=pending AND kind matches<br/>(+ url_matcher filter for url rows)
        A-->>F: claimed row (attempts += 1)
        F->>F: resolve + download + tag<br/>(provider-specific)
        alt success
            F->>A: complete(id, library_track_id, file_path)
            A->>PG: status=done (+ playlist attach, soft-ref checked)
        else transient failure
            F->>A: fail(id, error, retry_in=…)
            A->>PG: back to pending, next_attempt_at pushed out<br/>(terminal failed after max attempts)
        else can't ever fulfill
            F->>A: fail(id, error, unfulfillable=true)
            A->>PG: status=unfulfillable
        end
        A->>A: emit core.acquisition_completed / _failed
    end
```

The bus events are the fast path; a plugin correlating completions back to
its own rows pairs the subscription with a periodic
`completed_for_origin(origin_ref_prefix=…)` reconciliation sweep — the bus is
latency, the sweep is truth.

The bundled [radio plugin](../../plugins/radio/) exercises the whole chain:
passive song detection on a tuned station enqueues `query`-kind acquisitions
with rich metadata, and its dashboard page rides the `acquisitions` realtime
channel.
