"""Pydantic response models for the web backend.

These are the API contract — they end up in the auto-generated
OpenAPI spec (``python -m web.scripts.dump_openapi``), which client
work builds against. Listed here in one place so the contract stays
discoverable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domovoi.models import MAX_CONFIG_CHANGES


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
    """Create takes the same presentation fields PATCH edits, so the
    dashboard's new-playlist form posts once instead of create-then-edit
    (F-021)."""
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    cover_color: str | None = Field(default=None, max_length=64)
    cover_emoji: str | None = Field(default=None, max_length=16)


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
    """Partial update for a ``library_tracks`` row: the ``favorited``
    flag, and the title / artist / album a person corrects from the
    track drawer (F-024). Only fields actually sent are written; a
    metadata edit also stamps ``enriched_at`` so the enricher's next
    sweep (which touches unenriched rows only) can't overwrite a hand
    correction."""
    favorited: bool | None = None
    title: str | None = Field(default=None, max_length=300)
    artist: str | None = Field(default=None, max_length=300)
    album: str | None = Field(default=None, max_length=300)


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


# ─── Music: "also called" names (V019 library_aliases) ───────────────────


class LibraryAlias(BaseModel):
    """One other name for a library artist, album or song. ``can_remove``
    is for THE CALLER (an admin anything; a device the names it added;
    anyone a MusicBrainz name). ``added_by`` is "admin", the adding
    device's name, "voice in <room>" or "MusicBrainz" — never another
    device's raw id."""
    id: int
    alias: str
    alias_key: str
    target_type: Literal["artist", "album", "track"]
    target_key: str | None = None
    target_name: str
    target_artist_key: str | None = None
    target_track_id: int | None = None
    source: Literal["manual", "voice", "musicbrainz"]
    created_by_kind: Literal["admin", "device", "voice", "system"]
    added_by: str
    created_at: datetime | None = None
    suppressed: bool = False
    can_remove: bool = False


class LibraryAliasPage(BaseModel):
    items: list[LibraryAlias]
    total: int


class LibraryAliasCreate(BaseModel):
    """``POST /api/music/aliases``. Name the target by ``target_name``
    (artist / album — an album may add ``target_artist_name`` to pin it to
    that artist's album) or ``target_track_id`` (a song). ``replace=true``
    answers "yes" to the 409's "replace it?"."""
    alias: str = Field(min_length=1, max_length=500)
    target_type: Literal["artist", "album", "track"]
    target_name: str | None = Field(default=None, max_length=500)
    target_artist_name: str | None = Field(default=None, max_length=500)
    target_track_id: int | None = None
    replace: bool = False


class LibraryAliasShadow(BaseModel):
    type: Literal["artist", "title", "album"]
    name: str


class LibraryAliasAddResult(BaseModel):
    """201 (``added`` / ``replaced``) or 200 (``exists``). ``shadows``
    names the OTHER library names this alias now plays instead of."""
    status: Literal["added", "exists", "replaced"]
    alias: LibraryAlias
    shadows: list[LibraryAliasShadow] = []


class TrackAliasSong(BaseModel):
    id: int
    title: str
    aliases: list[LibraryAlias]


class TrackAliasArtist(BaseModel):
    name: str
    key: str
    # True for the whole multi-artist credit ("Earth, Wind & Fire"), shown
    # before its parts so a band can be named as one.
    credit: bool = False
    aliases: list[LibraryAlias]


class TrackAliasAlbum(BaseModel):
    name: str
    key: str
    artist_key: str | None = None
    artist_name: str | None = None
    aliases: list[LibraryAlias]


class TrackAliases(BaseModel):
    """The track drawer's "also called" lists in one call."""
    track: TrackAliasSong
    artists: list[TrackAliasArtist]
    album: TrackAliasAlbum | None = None


class AliasFetchStatus(BaseModel):
    enabled: bool | None = None
    state: str = "unknown"
    artists_total: int = 0
    checked: int = 0
    matched: int = 0
    no_match: int = 0
    ambiguous: int = 0
    errors: int = 0
    aliases_added: int = 0
    last_lookup_at: datetime | None = None
    last_error: str | None = None


class AliasStatus(BaseModel):
    """``GET /api/music/aliases/status`` — the Stats tab's card."""
    household: int
    musicbrainz: int
    suppressed: int
    fetch: AliasFetchStatus


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
    # The matching library_tracks id when the playing file resolves to a
    # library row (same match as ``favorited``), else null. Lets clients
    # — the kiosk display page especially — build the
    # ``/api/music/library/{track_id}/cover`` art URL. Streams have none.
    track_id: int | None = None
    # Generic now-playing SOURCE STAMP (design §4.7). A provider plugin
    # stamps a room when it starts playback; the dashboard renders a
    # provider-agnostic "open source ↗" pill from ``source_url`` when
    # the stamp supplies one. Freshness-checked against MPD's
    # currentsong so a stale stamp from a prior provider play doesn't
    # leak through after a library track takes over the room.
    source: str | None = None
    source_url: str | None = None
    source_ref: str | None = None
    # MPD songid of the playing queue entry — the key the room-queue
    # provenance table is on, so the card and the queue agree on which
    # entry they're talking about. Null when nothing is playing.
    song_id: int | None = None
    # Name of the device that added this entry to the room's queue, when we
    # have a record of it. Null for voice-added entries, casts that predate
    # the feature, and anything that reached MPD from outside Domovoi — the
    # UI renders nothing rather than guessing.
    added_by: str | None = None


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
    # What opened the mic: "wake_word" | "barge_in" | "followup" |
    # "push_to_talk". None for rows written before V011 and for turns that
    # never came from a satellite mic. Surfaced so a turn that looks wrong
    # can be traced to its origin without inferring it from timestamps.
    utterance_trigger: str | None = None


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


class SatelliteDisplay(BaseModel):
    """Screen/kiosk state a video satellite reports via display_status:
    panel power, kiosk-browser liveness, backlight percent (null when the
    hardware exposes none — HDMI monitors typically don't), and the
    configured idle behavior. Null field-wise until the device reports."""

    on: bool | None = None
    kiosk_alive: bool | None = None
    brightness: int | None = None
    idle_mode: str | None = None


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
    # "waiting" = adopted (satellites row exists) but never connected — no
    # mpd_rooms row yet, so mpd_ports/now_playing are null.
    status: Literal["online", "offline", "waiting"]
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
    # Satellite kind — "voice" (default) or "video" (screen-bearing kiosk
    # build). Kept a plain str (registered_values open enum, not a Literal)
    # so future types don't break deserialization. Resolved DB-row-first
    # (explicit adoption/hello writes) with the live snapshot as fallback.
    sat_type: str = "voice"
    # Whether the satellite's voice-input stack is running (hello frame);
    # false on mic-less builds — gates mic-dependent UI (drop-in, wake
    # recording). Defaults true for offline rooms (unknown until connect).
    mic_enabled: bool = True
    # Optional physical-room grouping label ("Living Room") from the
    # satellites inventory table; null = ungrouped. Server-side metadata
    # only — never sent to the device.
    room_label: str | None = None
    # Hardware description from adoption ("Raspberry Pi Zero 2 W Rev 1.0");
    # null for satellites that predate the inventory table.
    hardware: str | None = None
    # When the USB-adoption flow claimed this device; null = self-registered.
    adopted_at: datetime | None = None
    # Screen/kiosk state (video satellites, from the live snapshot) — null
    # for voice satellites and for offline rooms.
    display: SatelliteDisplay | None = None
    # Whether this room is keeping its command recordings for tuning (V016:
    # an admin opted it in, and an admin credential exists — what the core
    # acts on), and since when. Open like the rest of this row on
    # purpose: anyone in the house can see that a room is recording, which
    # is what the dashboard's "recording commands for tuning" marker shows.
    # The recordings themselves are admin-only (/api/captures).
    capture_commands: bool = False
    capture_since: datetime | None = None
    # The per-satellite setting "Only reminders for this device" (V018
    # timer_own_only_rooms): true = this room announces only the timers
    # and reminders set on it; false (the default) = every room's, named
    # by the room they came from. Open like the rest of the row: it says
    # how a room behaves, not what anybody said. False when V018 is missing.
    timers_own_only: bool = False


# ─── Notes / Timers ───────────────────────────────────────────────────────


class VoiceNote(BaseModel):
    id: int
    room_id: str | None = None
    body: str
    captured_at: datetime


class Timer(BaseModel):
    id: int
    expires_at: datetime
    # When it was set: with expires_at, the full length of the timer, so a
    # page can draw how much of it has run. Every row has one (the column
    # is NOT NULL); optional here only for the shape's sake.
    created_at: datetime | None = None
    label: str | None = None
    message: str | None = None  # non-null = reminder
    room_id: str | None = None  # null = set somewhere with no room
    is_reminder: bool = False
    # True when this reminder's words (``message`` and ``label``) were held
    # back because the caller holds no household credential (rule M1,
    # docs/SECURITY_PRIVACY.md). Never true for a plain timer.
    masked: bool = False


class TimerFireDelivery(BaseModel):
    """One room's announcement of a fire. ``detail`` is a reason code
    (``offline``, ``capturing``, ``forced_over:followup``, ``tts_failed``,
    ``acknowledged:kitchen`` ...), never speech."""

    room_id: str
    is_origin: bool = False
    outcome: str  # pending | sending | spoken | interrupted | failed | offline | busy_timeout | cancelled
    detail: str | None = None
    finished_at: datetime | None = None


class TimerFire(BaseModel):
    """A timer or reminder that went off (V018 ``timer_fires``), and where
    it was announced. ``room_id`` is the room it was SET in (null = set
    with no room). ``deliveries`` lists the origin room first, then the
    other rooms A→Z; ``heard_in`` is the rooms that heard it, same order.
    ``summary`` is computed by the server ("heard in garage, kitchen ·
    still announcing") and shown verbatim. The words the core spoke are
    never part of this shape.

    To a caller with no household credential (rule F1) a fire comes cut
    down: ``deliveries`` ``[]``, ``acked_at`` / ``acked_by`` /
    ``settled_at`` null, no `` · stopped in …`` in ``summary``, and a
    reminder's words masked (rule M1)."""

    id: int
    timer_id: int
    kind: Literal["timer", "reminder"]
    is_reminder: bool = False
    label: str | None = None
    message: str | None = None
    masked: bool = False
    room_id: str | None = None
    created_at: datetime | None = None
    due_at: datetime
    fired_at: datetime
    settled_at: datetime | None = None
    acked_at: datetime | None = None
    acked_by: str | None = None
    heard_in: list[str] = Field(default_factory=list)
    summary: str = ""
    deliveries: list[TimerFireDelivery] = Field(default_factory=list)


class TimerList(BaseModel):
    """Every timer and reminder in the house, soonest first.

    ``server_now`` is the database clock at the moment of the read — the
    clock that decides when a timer fires — so a page counts down against
    the server rather than a phone whose clock is minutes off.

    ``fires`` is what went off in the last 10 minutes (newest first, at
    most 20) — the source of Home's "done · kitchen" lines. ``null`` means
    the server keeps no fire history (V018 missing): a client falls back to
    its own behaviour. ``[]`` means nothing fired lately. Without a
    household credential each fire comes cut down as on
    ``GET /api/timers/fires`` (rule F1)."""

    server_now: datetime
    timers: list[Timer]
    fires: list[TimerFire] | None = None


class TimerFireList(BaseModel):
    """``GET /api/timers/fires``: fire history plus the database clock.

    ``window_sec`` says how far back this answer could reach: ``null`` —
    the whole kept history (the caller holds a household credential);
    a number — only fires from that many seconds back (no credential:
    600, and each fire cut to what Home draws, rule F1 in
    docs/SECURITY_PRIVACY.md). It depends only on who asked, never on the
    data, so an empty windowed answer says nothing about older fires —
    and a client must not read it as "the history is empty"."""

    server_now: datetime
    fires: list[TimerFire]
    window_sec: int | None = None


class TimerAnnouncements(BaseModel):
    """A room's "Only reminders for this device" setting. ``since`` is
    when it was turned on (null while off)."""

    room_id: str
    own_only: bool = False
    since: datetime | None = None


class TimerAnnouncementsUpdate(BaseModel):
    """``PUT /api/satellites/{room_id}/timer-announcements`` body."""

    model_config = ConfigDict(extra="forbid")

    own_only: bool = Field(..., strict=True)


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


class DisplayRequest(BaseModel):
    """Drive a video satellite's screen from the drawer's Display block."""
    action: Literal["on", "off", "restart_kiosk"]


class PendingSatellite(BaseModel):
    """An unprovisioned satellite presenting a USB adoption volume on the
    server. ``pending_id`` is the device's per-session nonce (drive letters
    never leave the backend). A ``parse_error`` entry is a volume that looks
    like a setup drive but carries an unreadable device-info — surfaced so
    the user sees WHY nothing is adoptable."""

    pending_id: str
    mac: str | None = None
    board: str | None = None
    model: str | None = None
    sat_type: str = "voice"
    status: str = "awaiting_provision"   # awaiting_provision | wifi_failed | bootstrapping
    step: str | None = None
    error: str | None = None
    client_version: str | None = None
    profiles_supported: list[str] = []
    # Set when this device's MAC matches an existing satellites row — the
    # UI then offers re-provision instead of a fresh adopt.
    already_adopted_as: str | None = None
    parse_error: str | None = None


class SatelliteApproveRequest(BaseModel):
    """Approve a parked satellite by the code it is showing and saying.

    The dashboard never renders the code, so this is the operator copying
    it off the device in the room — which is what makes the approval a
    statement about a device rather than about a room name. The core
    compares it in constant time and counts the attempt."""

    code: str = Field(..., min_length=1, max_length=16, pattern=r"^[0-9]+$")


class AdoptRequest(BaseModel):
    """Adopt a pending satellite: name it, hand it Wi-Fi, pick its mic
    profile. The PSK transits to the device on the gadget volume exactly
    once and is never logged (docs/SECURITY_PRIVACY.md)."""

    room_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,31}$")
    room_label: str | None = Field(default=None, max_length=80)
    wifi_ssid: str = Field(..., min_length=1, max_length=32)
    wifi_psk: str = Field(..., min_length=8, max_length=63)   # WPA2-PSK bounds
    wifi_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    wifi_hidden: bool = False
    device_profile: str = "respeaker_2mic_hat"
    initial_volume: int | None = Field(default=None, ge=0, le=100)
    # Re-provision an already-adopted device (wifi_failed retry / moving
    # it). Only honored when the device's MAC matches the existing row.
    force: bool = False

    @field_validator("wifi_ssid")
    @classmethod
    def _ssid_the_device_can_carry(cls, v: str) -> str:
        # The same rule the device applies before writing the name into
        # its network configuration; refusing it here means a 422 with
        # the reason instead of a silent stall on the device.
        from satellite import provisioning_protocol as proto

        try:
            return proto.validate_wifi_ssid(v)
        except proto.ProvisionInvalid as e:
            raise ValueError(str(e)) from None


class RoomLabelRequest(BaseModel):
    """Set (or clear, with null) a satellite's display room label."""
    room_label: str | None = Field(default=None, max_length=80)


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
    Domovoi server's ``/v1/admin/music/play-tracks``.

    The room starts on the first id: a client sends its CURRENT track first
    (not the top of its queue), with ``start_sec`` = how far into it the
    listener is, so the hand-off carries on where the listener was.
    ``start_paused``: the listener had it paused, so the room waits there,
    paused, until someone presses play."""
    room_id: str
    track_ids: list[int] = Field(..., min_length=1, max_length=500)
    start_sec: float = Field(0.0, ge=0, le=86400)
    start_paused: bool = False


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
    # The core's speech-recognition state from its /v1/health: "ok",
    # "fallback" (the cpu fallback model loaded instead of the configured
    # one), "unavailable" (nothing loaded), "stub" (tests) or "not_loaded".
    # None when the core did not answer. Never makes `status` degraded.
    stt: str | None = None


class ConfigResponse(BaseModel):
    bot_name: str
    tts_voice: str
    rooms: list[str]
    web_version: str
    # Min positive clips before a wake word can be trained (Feature 5) — the
    # Wake Words tab reads this to gate/label "N / min clips" off the real
    # server config instead of a hardcoded guess.
    wake_word_min_clips: int
    # Who sees Home's "needs attention" rows: "everyone" (household members
    # see the rows that explain what they notice, an admin sees all),
    # "summary" (household members see one neutral line) or "admins"
    # (only an admin sees any). HOME_PROBLEMS_VISIBILITY, admin-set.
    home_problems_visibility: Literal["everyone", "summary", "admins"] = "everyone"
    # The household's internet answer (INTERNET_ACCESS, Settings → Internet):
    # "" = not answered yet (today's behaviour; an admin's Home row asks),
    # "always", "sometimes" or "never". Open like the rest of this
    # response: the Home page and every "needs internet" greying read it.
    internet_access: Literal["", "always", "sometimes", "never"] = ""


class ConfigUpdateRequest(BaseModel):
    """A batch of domovoi config edits from the settings gear, keyed by
    Settings field name. Validated/coerced server-side by the Domovoi core."""
    # CORE-7: a config push is a handful of keys, not a payload. The
    # bound is on the NUMBER of keys (pydantic's max_length for a dict);
    # the transport cap on the whole body is in domovoi/transport_guard.py.
    changes: dict[str, Any] = Field(..., max_length=MAX_CONFIG_CHANGES)
    # Settings → Internet: hand-set settings that should follow the
    # household's internet answer again (their .env line is commented out).
    follow_internet: list[str] = Field(default_factory=list, max_length=16)


# ─── Music: lyrics (V021 track_lyrics; web/backend/api/lyrics.py) ────────
#
# Household tier only (require_device_read) — never on an open page, the
# kiosk display, the realtime socket or a log. Lyrics are the household's
# copy of copyrighted text for its own songs.


class LyricLine(BaseModel):
    """One timed line: ``t`` = milliseconds from the start of the song (the
    file's ``[offset:]`` already applied), ``text`` "" = an instrumental gap."""
    t: int
    text: str


class LyricsDoc(BaseModel):
    """``GET /api/music/library/{track_id}/lyrics`` — what a player shows.

    ``status``: ``synced`` (timed lines in ``lines``, the same words as plain
    text in ``text``), ``plain`` (``text`` only), ``instrumental`` (LRCLIB
    says the song has no words) or ``none``. ``checking`` is true while
    Domovoi is still looking (the local scan has not read the file yet, or
    LRCLIB is on and has not been asked about this song). ``source`` is
    where the shown lyrics came from — ``sidecar`` (a .lrc beside the song),
    ``embedded`` (the song file's own tags) or ``lrclib`` — and
    ``source_label`` says so in words."""
    track_id: int
    status: Literal["synced", "plain", "instrumental", "none"]
    checking: bool = False
    source: str | None = None
    source_label: str | None = None
    lines: list[LyricLine] | None = None
    text: str | None = None
    updated_at: datetime | None = None


class RoomLyrics(BaseModel):
    """``GET /api/music/now-playing/{room_id}/lyrics`` — the room's song,
    where it is, and its lyrics in one read. ``read_at`` is the server's
    clock right after the MPD read (informational: a client anchors its own
    interpolation on when IT received the answer). ``line_index`` is the
    last timed line at ``elapsed_sec`` (-1 before the first), only for
    ``synced`` lyrics. ``track_id`` / ``lyrics`` are null when nothing plays
    or the song is not a library track."""
    room_id: str
    state: Literal["play", "pause", "stop"]
    track_id: int | None = None
    elapsed_sec: float | None = None
    duration_sec: int | None = None
    read_at: datetime
    line_index: int | None = None
    lyrics: LyricsDoc | None = None


class LyricsSourceCounts(BaseModel):
    sidecar: int = 0
    embedded: int = 0
    lrclib: int = 0


class LyricsLrclibStatus(BaseModel):
    """Counts from the database; ``enabled`` / ``state`` / ``due`` / the
    timers / ``last_error`` from the core's snapshot (``lyrics.fetch``):
    ``enabled: null, state: "unknown"`` until the core reports one."""
    enabled: bool | None = None
    state: str = "unknown"
    asked: int = 0
    found: int = 0
    not_found: int = 0
    instrumental: int = 0
    skipped: int = 0
    errors: int = 0
    due: int | None = None
    next_retry_at: datetime | None = None
    rate_limited_until: str | None = None
    paused_until: str | None = None
    last_error: str | None = None


class LyricsLrcFiles(BaseModel):
    """The .lrc files Domovoi writes for LRCLIB's timed lyrics. ``enabled``
    = LRCLIB on AND "Save lyrics as .lrc files" on (null until the core
    reports); ``last_error`` = the most frequent failure code."""
    enabled: bool | None = None
    written: int = 0
    exists: int = 0
    edited: int = 0
    deleted: int = 0
    failed: int = 0
    last_error: str | None = None


class LyricsScanStatus(BaseModel):
    state: str = "unknown"
    unscanned: int = 0
    last_pass_at: str | None = None


class LyricsIndexStatus(BaseModel):
    state: str = "unknown"
    pending: int = 0
    indexed: int = 0


class LyricsStatus(BaseModel):
    """``GET /api/music/lyrics/status`` — the Music page's Jobs card. Counts
    and states only, never a lyric or a title."""
    tracks: int
    scanned: int
    with_lyrics: int
    synced: int
    plain: int
    instrumental: int
    by_source: LyricsSourceCounts
    lrclib: LyricsLrclibStatus
    lrc_files: LyricsLrcFiles
    scan: LyricsScanStatus
    index: LyricsIndexStatus
    search_enabled: bool | None = None
