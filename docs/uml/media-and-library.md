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

    playlists ||--o{ playlist_tracks : "CASCADE"
    library_tracks ||--o{ playlist_tracks : "CASCADE"
    library_tracks ||--o{ media_plays : "SET NULL"
    media_acquisitions }o..o| playlists : "attach_to_playlist_id (soft ref)"
    media_acquisitions }o..o| library_tracks : "result.library_track_id"
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
