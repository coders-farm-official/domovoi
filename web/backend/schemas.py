"""Pydantic response models for the web backend.

These are the API contract — they end up in the auto-generated
OpenAPI spec (``python -m web.scripts.dump_openapi``), which client
work builds against. Listed here in one place so the contract stays
discoverable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ─── Music ────────────────────────────────────────────────────────────────


class Track(BaseModel):
    id: int
    file_path: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_sec: int | None = None
    # Open enum (design §6.4): provider plugins register their own
    # source values ('upload', a plugin slug, ...); NULL = manual.
    source: str | None = None
    source_id: str | None = None
    musicbrainz_recording_id: str | None = None
    added_at: datetime
    added_via: Literal["voice", "manual"] | None = None
    enriched_at: datetime | None = None
    favorited: bool = False


class Playlist(BaseModel):
    """One row in the Playlists tab. ``is_virtual=True`` is the
    pinned-at-top "Favorites" entry — derived from
    ``library_tracks.favorited`` rather than a real ``playlists``
    row, so it can't be renamed or deleted from the dashboard."""
    id: int
    name: str
    track_count: int
    created_at: datetime | None = None
    is_virtual: bool = False
    description: str | None = None
    cover_color: str | None = None
    cover_emoji: str | None = None


class PlaylistCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    cover_color: str | None = Field(default=None, max_length=64)
    cover_emoji: str | None = Field(default=None, max_length=16)


class PlaylistReorder(BaseModel):
    """Full-order rewrite: the playlist's track_ids in the new order."""
    track_ids: list[int]


class PlaylistTrackAdd(BaseModel):
    track_id: int = Field(..., ge=1)


class TrackPatch(BaseModel):
    """Partial update for a ``library_tracks`` row. Today only the
    ``favorited`` flag is editable from the dashboard — exposed as a
    PATCH so future per-field tweaks (manual title/artist edits etc.)
    plug into the same surface without a new endpoint."""
    favorited: bool | None = None


class LibraryPage(BaseModel):
    """One page of ``library_tracks`` plus the unbounded count that
    matches the active filters. ``total`` reflects ``q``/``source``;
    it's what pagination math and the "X results" caption read."""
    total: int
    items: list[Track]


class LibraryStats(BaseModel):
    """Aggregate snapshot of the whole library — computed server-side
    so the Stats tab never depends on what's loaded in memory."""
    total_tracks: int
    total_duration_sec: int
    by_added_via: dict[str, int]
    # Open-enum source buckets ('manual' bucket = NULL source). Feeds
    # the Library tab's data-driven source filter.
    by_source: dict[str, int] = {}
    enriched_count: int


class NowPlayingSong(BaseModel):
    file: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_sec: int | None = None


class NowPlaying(BaseModel):
    room_id: str
    state: Literal["play", "pause", "stop"]
    song: NowPlayingSong | None = None
    elapsed_sec: float | None = None
    stream_url: str | None = None
    # Whether the currently-playing item is favorited. For local files
    # this is the matching library_tracks row's ``favorited`` flag.
    # Always ``False`` when nothing is playing, when the song doesn't
    # match a known row, or for external streams (provider plugins own
    # richer favorite state on their own pages).
    favorited: bool = False
    # Generic now-playing SOURCE STAMP (design §4.7). A provider plugin
    # stamps a room when it starts playback; the dashboard renders a
    # provider-agnostic "open source ↗" pill from ``source_url`` when
    # the stamp supplies one. Freshness-checked against MPD's
    # currentsong so a stale stamp from a prior provider play doesn't
    # leak through after a library track takes over the room.
    source: str | None = None
    source_url: str | None = None
    source_ref: str | None = None


class FavoriteNowPlayingResult(BaseModel):
    """Result of favoriting whatever is currently playing in a room.

    ``kind`` tells the dashboard what happened so it can render the
    appropriate toast: ``library`` = an existing library row's flag was
    flipped (returns ``track_id``); ``acquisition`` = an external
    stream was queued into the generic media-acquisition queue
    (returns ``acquisition_id`` + the core's user-facing ``message``,
    which carries the graceful-absence copy when no provider plugin is
    installed)."""
    kind: Literal["library", "acquisition"]
    title: str | None = None
    artist: str | None = None
    track_id: int | None = None
    acquisition_id: int | None = None
    already_favorited: bool = False
    message: str | None = None


# ─── People ───────────────────────────────────────────────────────────────


class Person(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_seen_at: datetime | None = None
    notes: str | None = None
    voice_profile_count: int = 0
    presence_tier: Literal["low", "medium", "high"] = "high"


class VoiceProfile(BaseModel):
    id: int
    person_id: int
    model: str
    enrolled_at: datetime
    room_id: str | None = None
    sample_seconds: float | None = None


# ─── Profile personalization ─────────────────────────────────────

class Memory(BaseModel):
    id: int
    person_id: int
    body: str
    topic: str | None = None
    source: Literal["explicit", "implicit", "manual"]
    status: Literal["active", "pending", "rejected"]
    created_at: datetime


class MemoryCreate(BaseModel):
    body: str
    topic: str | None = None


class MemoryPatch(BaseModel):
    status: Literal["active", "pending", "rejected"] | None = None
    body: str | None = None
    topic: str | None = None


class Favorite(BaseModel):
    id: int
    person_id: int
    kind: str
    value: str
    rank: int = 0


class FavoriteCreate(BaseModel):
    kind: str
    value: str
    rank: int = 0


class PreferencesPatch(BaseModel):
    # JSONB merge — each key in `set` is written, each key in
    # `unset` is removed. Lets the web UI submit add/remove in one
    # round-trip without read-modify-write on the client.
    set: dict[str, object] = {}
    unset: list[str] = []


class DenylistEntry(BaseModel):
    id: int
    denylisted_at: datetime
    notes: str | None = None


# ─── Sessions / Conversations ─────────────────────────────────────────────


class Session(BaseModel):
    id: str
    room_id: str | None = None
    started_at: datetime
    last_activity: datetime
    person_id: int | None = None
    intent_count: int = 0


class ConversationTurn(BaseModel):
    id: int
    session_id: str | None = None
    at: datetime
    room_id: str | None = None
    user_text: str | None = None
    assistant_text: str | None = None
    matched_handler: str | None = None
    matched_path: str | None = None


class RecentlyPlayed(BaseModel):
    """One row of a room's play history (media_plays). ``source`` is an
    open enum — library, playlist, plus whatever provider plugins
    record. ``in_library`` is true when an external play already has a
    matching library_tracks row (by ``source``/``source_id``); the
    drawer offers "+ add" only when ``can_add`` (an external play with
    enough metadata to queue a generic acquisition, and not already in
    the library)."""
    id: int
    room_id: str | None = None
    source: str
    title: str | None = None
    artist: str | None = None
    channel: str | None = None
    external_id: str | None = None
    url: str | None = None
    started_at: datetime
    in_library: bool = False
    can_add: bool = False


# ─── Satellites ───────────────────────────────────────────────────────────


class WifiStatus(BaseModel):
    rx_mbits: float | None = None
    tx_mbits: float | None = None
    ssid: str | None = None


class MpdPorts(BaseModel):
    control: int
    http: int


class SatellitePairing(BaseModel):
    """WS pairing status for a room (V002). ``paired`` is whether a
    ``satellite_pairings`` row exists (the room has claimed its token,
    trust-on-first-use); ``paired_at`` / ``last_seen_at`` are that row's
    timestamps. An unpaired room reports ``paired=False`` with null times —
    it still accepts a tokenless satellite unless strict pairing is on."""

    paired: bool = False
    paired_at: datetime | None = None
    last_seen_at: datetime | None = None


class Satellite(BaseModel):
    room_id: str
    status: Literal["online", "offline"]
    last_connected_at: datetime | None = None
    wifi: WifiStatus | None = None
    now_playing: NowPlaying | None = None
    active_session_id: str | None = None
    mpd_ports: MpdPorts | None = None
    version: str | None = None
    # Two-way drop-in (Feature 4). ``full_duplex`` is whether this room's
    # board has on-chip AEC (only XVF3800 rooms can do drop-in without an
    # echo howl) — the UI offers drop-in only between full-duplex rooms.
    # ``in_call_with`` is the peer room_id when this satellite is currently
    # in a live drop-in, else None (drives the Hang-up affordance).
    full_duplex: bool = False
    in_call_with: str | None = None
    # Active TTS voice the satellite reports speaking in (voice_status); None =
    # registry default. The room's config.toml/sidecar drives this, so it
    # surfaces here (the Settings tab can't show a sidecar-overridden voice).
    voice: str | None = None
    # Master output volume (0-100) the satellite reports via volume_status, or
    # None when it hasn't reported one yet (or its board has no output mixer
    # control). Drives the overview tab's volume slider.
    volume: int | None = None
    # WS pairing status (V002): whether this room has claimed a pairing token
    # and, if so, when. Read from the shared Postgres directly (the web
    # process reads the same DB), so it's fresh even for an offline room.
    pairing: SatellitePairing = SatellitePairing()


# ─── Notes / Timers ───────────────────────────────────────────────────────


class VoiceNote(BaseModel):
    id: int
    room_id: str | None = None
    body: str
    captured_at: datetime


class Timer(BaseModel):
    id: int
    expires_at: datetime
    label: str | None = None
    message: str | None = None  # non-null = reminder
    room_id: str | None = None
    is_reminder: bool = False


# ─── Calendar ─────────────────────────────────────────────────────────────


class CalendarEvent(BaseModel):
    id: int
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = None
    description: str | None = None
    source: str | None = None
    external_id: str | None = None
    last_synced_at: datetime | None = None


class CalendarEventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = None
    description: str | None = None


class CalendarEventPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = None
    description: str | None = None


# ─── Action requests ──────────────────────────────────────────────────────


class AnnounceRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)


class VolumeRequest(BaseModel):
    """Set a satellite's master output volume (0-100) from the overview tab."""
    level: int = Field(..., ge=0, le=100)


class DropInStartRequest(BaseModel):
    """Open a drop-in FROM the path's ``{room_id}`` (initiator) TO
    ``target_room``."""
    target_room: str = Field(..., min_length=1, max_length=120)


class PlayRequest(BaseModel):
    room_id: str
    query: str


class PlayTrackRequest(BaseModel):
    """Direct play of a specific library_tracks row by id. Used by
    the Music page's "Play in {room}" button on a library row, where
    the dashboard already knows the exact track and there's no need
    to round-trip through fuzzy text-matching in the router."""
    room_id: str
    track_id: int = Field(..., ge=1)


class CastTracksRequest(BaseModel):
    """Cast an arbitrary ordered queue of library tracks into a room.

    The browser music player owns the queue order (it's whatever the user
    built locally), so unlike ``PlayPlaylistRequest`` this ships the exact
    ``track_id`` list rather than a playlist id. Proxied to the
    Domovoi server's ``/v1/admin/music/play-tracks``."""
    room_id: str
    track_ids: list[int] = Field(..., min_length=1, max_length=500)


class PlayPlaylistRequest(BaseModel):
    """Direct play of a playlist by id. ``playlist_id == 0`` is the
    virtual Favorites view. ``shuffle=True`` randomizes the pick;
    otherwise playback starts at the first track and ``next``
    advances by position."""
    room_id: str
    playlist_id: int = Field(..., ge=0)
    shuffle: bool = False


class AddByQueryRequest(BaseModel):
    """Queue a generic media acquisition by free-text query (design
    §4.8). An open daily action — with no fulfiller plugin installed
    the row waits ``pending`` and the response carries the graceful-
    absence copy."""
    room_id: str
    query: str = Field(..., min_length=1, max_length=500)
    artist: str | None = None
    attach_to_playlist_id: int | None = Field(default=None, ge=1)


class AddByUrlRequest(BaseModel):
    """Queue a media acquisition for an EXACT external URL. Used by the
    Recently-played drawer's "+ add" button when a play has a stored
    URL (unlike the now-playing heart, which re-searches by title).
    ``dedup_key`` is an optional provider-namespaced identity so repeat
    clicks don't queue duplicates. Gated by the core's outbound-fetch
    tier (§7.3)."""
    room_id: str
    url: str = Field(..., min_length=1, max_length=2000)
    title: str | None = None
    dedup_key: str | None = None
    attach_to_playlist_id: int | None = Field(default=None, ge=1)


# ─── Misc ─────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db_reachable: bool
    domovoi_reachable: bool


class ConfigResponse(BaseModel):
    bot_name: str
    tts_voice: str
    rooms: list[str]
    web_version: str
    # Min positive clips before a wake word can be trained (Feature 5) — the
    # Wake Words tab reads this to gate/label "N / min clips" off the real
    # server config instead of a hardcoded guess.
    wake_word_min_clips: int


class ConfigUpdateRequest(BaseModel):
    """A batch of domovoi config edits from the settings gear, keyed by
    Settings field name. Validated/coerced server-side by the Domovoi core."""
    changes: dict[str, Any]
