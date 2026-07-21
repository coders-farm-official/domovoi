from __future__ import annotations

from uuid import uuid4

import pytest

from domovoi.clients.mpd import MPDStubClient
from domovoi.handlers.music import (
    MusicHandler,
    _NEXT_RE,
    _NOW_PLAYING_RE,
    _PAUSE_RE,
    _PLAY_ANY_RE,
    _PLAY_ARTIST_RE,
    _PLAY_RANDOM_RE,
    _PREV_RE,
    _RESUME_RE,
    _STOP_RE,
    _VOLUME_DOWN_RE,
    _VOLUME_SET_RE,
    _VOLUME_UP_RE,
    level_to_percent,
    percent_to_level,
)
from domovoi.capabilities import (
    CAPABILITIES,
    STREAMING_SEARCH_PROVIDER,
    MediaCandidate,
)
from domovoi.models import Context, Response
from domovoi.tests.conftest import requires_db


# ─── Streaming-search capability seam fakes (design §10.2) ──────────────────

import contextlib
import re as _re


def _norm_title(t: str) -> str:
    t = _re.sub(r"[\(\[].*?[\)\]]", "", t or "")
    t = _re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return " ".join(t.split())


class _FakeStreamingProvider:
    """Behavior-shaped StreamingSearchProvider stub: records calls,
    serves canned candidates, streams by returning a Response the way a
    real provider would after sdk.playback.play_url."""

    slug = "extstream"

    def __init__(self, results: list[MediaCandidate] | None = None,
                 stream_result: Response | str | None = "auto") -> None:
        self.results = results or []
        self.search_calls: list[str] = []
        self.stream_calls: list[tuple] = []
        self.stream_result = stream_result

    async def search(self, query: str, *, limit: int = 5) -> list[MediaCandidate]:
        self.search_calls.append(query)
        return self.results[:limit]

    async def stream(self, room_id, candidate=None, *, query=None):
        self.stream_calls.append((room_id, candidate, query))
        if self.stream_result is None:
            return None
        if isinstance(self.stream_result, Response):
            return self.stream_result
        title = candidate.title if candidate is not None else (query or "")
        return Response(
            text=f"Streaming {title}.",
            data={"title": title},
            matched_handler=self.slug,
        )

    def likely_same(self, title_a: str, title_b: str) -> bool:
        a, b = _norm_title(title_a), _norm_title(title_b)
        return bool(a and b) and (a in b or b in a)


class _ExplodingProvider(_FakeStreamingProvider):
    async def search(self, query, *, limit=5):
        raise AssertionError("provider must not be consulted on a local hit")

    async def stream(self, room_id, candidate=None, *, query=None):
        raise AssertionError("provider must not be consulted on a local hit")


@contextlib.contextmanager
def _registered(provider):
    CAPABILITIES.register(STREAMING_SEARCH_PROVIDER, provider, slug=provider.slug)
    try:
        yield provider
    finally:
        CAPABILITIES.unregister(STREAMING_SEARCH_PROVIDER, slug=provider.slug)


# ─── Pure regex tests (no DB / MPD) ─────────────────────────────────────────

def test_play_artist_regex() -> None:
    m = _PLAY_ARTIST_RE.match("play paranoid android by radiohead")
    assert m and m.group(1) == "paranoid android" and m.group(2) == "radiohead"


def test_play_any_regex() -> None:
    m = _PLAY_ANY_RE.match("play lofi")
    assert m and m.group(1) == "lofi"


def test_pause_regex() -> None:
    assert _PAUSE_RE.match("pause")
    assert _PAUSE_RE.match("pause the music")


def test_resume_regex() -> None:
    assert _RESUME_RE.match("resume")
    assert _RESUME_RE.match("continue the music")


def test_stop_regex() -> None:
    assert _STOP_RE.match("stop the music")
    assert _STOP_RE.match("stop")


def test_next_prev_regex() -> None:
    assert _NEXT_RE.match("next")
    assert _NEXT_RE.match("skip")
    assert _NEXT_RE.match("next song")
    assert _PREV_RE.match("previous")
    assert _PREV_RE.match("go back")


def test_now_playing_regex() -> None:
    assert _NOW_PLAYING_RE.match("what's playing")
    assert _NOW_PLAYING_RE.match("what song is this")
    assert _NOW_PLAYING_RE.match("who sang that")


def test_volume_regex() -> None:
    m = _VOLUME_SET_RE.match("volume 75")
    assert m and m.group(1) == "75"
    m = _VOLUME_SET_RE.match("set the volume to 30")
    assert m and m.group(1) == "30"
    assert _VOLUME_UP_RE.match("volume up")
    assert _VOLUME_UP_RE.match("louder")
    assert _VOLUME_DOWN_RE.match("turn it down")


# ─── Behavior tests against the stub MPD client ────────────────────────────

@pytest.mark.asyncio
async def test_pause_action() -> None:
    stub = MPDStubClient()
    stub._state = "play"
    # Swap the module-level stub so MusicHandler picks it up.
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _PAUSE_RE.match("pause")
    assert m
    response = await handler._pause_from_match(m, ctx, None)
    assert "paused" in response.text.lower()
    assert stub._state == "pause"


@pytest.mark.asyncio
async def test_play_any_action_returns_fake_song() -> None:
    stub = MPDStubClient()
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _PLAY_ANY_RE.match("play lofi")
    assert m
    response = await handler._play_any_from_match(m, ctx, None)
    assert "playing lofi" in response.text.lower()
    # Handler calls `prepare_search` — MPD is queued paused, the streaming
    # layer flips it to "play" once the Pi sends `music_ready`. See
    # `schedule_music_resume_fallback` in domovoi/streaming.py.
    assert stub._state == "pause"


def _spy_play_handler():
    """MusicHandler with _play / _play_random replaced by call recorders."""
    handler = MusicHandler()
    calls: dict = {}

    async def fake_play(query, ctx, session):
        calls["play"] = query
        return None

    async def fake_random(ctx, session):
        calls["random"] = True
        return None

    handler._play = fake_play          # type: ignore[assignment]
    handler._play_random = fake_random  # type: ignore[assignment]
    return handler, calls


@pytest.mark.asyncio
async def test_play_artist_only_searches_by_artist_not_random() -> None:
    """A tool call with an artist but no title ("play the latest TI song"
    → artist='TI') must search by artist, NOT fall back to a random track."""
    handler, calls = _spy_play_handler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    await handler.execute_from_tool({"action": "play", "artist": "TI"}, ctx, None)
    assert calls.get("play") == {"artist": "TI"}
    assert "random" not in calls


@pytest.mark.asyncio
async def test_play_title_and_artist_combined() -> None:
    handler, calls = _spy_play_handler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    await handler.execute_from_tool(
        {"action": "play", "title": "Whatever You Like", "artist": "TI"}, ctx, None
    )
    assert calls.get("play") == {"title": "Whatever You Like", "artist": "TI"}
    assert "random" not in calls


@pytest.mark.asyncio
async def test_play_no_title_no_artist_still_random() -> None:
    """A bare play with nothing usable still shuffles the library."""
    handler, calls = _spy_play_handler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    await handler.execute_from_tool({"action": "play"}, ctx, None)
    assert calls.get("random") is True
    assert "play" not in calls


@pytest.mark.asyncio
async def test_play_explicit_random_alias_shuffles() -> None:
    """'play something' (generic title) still routes to the random path."""
    handler, calls = _spy_play_handler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    await handler.execute_from_tool(
        {"action": "play", "title": "something"}, ctx, None
    )
    assert calls.get("random") is True
    assert "play" not in calls


def test_podcast_show_candidate() -> None:
    from domovoi.handlers.music import _podcast_show_candidate

    # "from/by X" → the artist part is the entity.
    assert _podcast_show_candidate({"title": "the latest", "artist": "wait what"}) == "wait what"
    # Free text with a leading recency word stripped.
    assert _podcast_show_candidate({"any": "the latest wait what"}) == "wait what"
    assert _podcast_show_candidate({"any": "wait what"}) == "wait what"
    assert _podcast_show_candidate({}) == ""


class _MissMPD:
    """MPD stub whose searches always miss — drives _play's fallback cascade."""

    async def prepare_search(self, query: dict) -> None:
        return None

    async def prepare_filename(self, *substrings: str) -> None:
        return None


def _use_miss_mpd() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": _MissMPD()}


@pytest.mark.asyncio
async def test_play_cascade_tries_podcast_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a local-library miss, _play tries a subscribed podcast BEFORE
    the streaming-search capability (music-first cascade) — 'play the
    latest from Wait What' reaches the Wait What podcast instead of a
    random external result."""
    from domovoi.handlers.spoken_audio import SpokenAudioHandler

    _use_miss_mpd()
    calls: list = []
    pod_resp = Response(text="Playing latest Wait What.", session_id=None, matched_handler="spoken_audio")

    async def fake_pod(self, ctx, session, show):
        calls.append(("podcast", show))
        return pod_resp

    monkeypatch.setattr(SpokenAudioHandler, "try_play_latest", fake_pod)

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    with _registered(_ExplodingProvider()):
        resp = await handler._play({"title": "the latest", "artist": "wait what"}, ctx, None)
    assert resp is pod_resp
    assert calls == [("podcast", "wait what")]  # provider never reached


@pytest.mark.asyncio
async def test_play_cascade_falls_to_provider_then_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    """No matching podcast → the registered streaming provider; and if
    the provider also misses (stream returns None), a GENERIC 'couldn't
    find anything' — never the podcast-specific error."""
    from domovoi.handlers.spoken_audio import SpokenAudioHandler

    _use_miss_mpd()

    async def no_pod(self, ctx, session, show):
        return None

    monkeypatch.setattr(SpokenAudioHandler, "try_play_latest", no_pod)

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)

    # Provider hits → its response is returned, retagged to music.
    with _registered(_FakeStreamingProvider()) as provider:
        resp = await handler._play({"any": "the latest ti song"}, ctx, None)
        assert "streaming" in resp.text.lower()
        assert resp.matched_handler == "music"
        assert provider.stream_calls[0][2] == "the latest ti song"

    # Provider misses too → generic, not podcast-specific.
    with _registered(_FakeStreamingProvider(stream_result=None)):
        resp2 = await handler._play({"any": "nonexistent thing"}, ctx, None)
    assert "couldn't find anything called" in resp2.text.lower()
    assert "podcast" not in resp2.text.lower()


@pytest.mark.asyncio
async def test_play_cascade_graceful_absence_when_no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """The design §10.2 absence copy: local miss + no podcast + NO
    streaming-search provider registered → "no streaming provider is
    installed", not a crash and not a bare couldn't-find."""
    from domovoi.handlers.spoken_audio import SpokenAudioHandler

    _use_miss_mpd()

    async def no_pod(self, ctx, session, show):
        return None

    monkeypatch.setattr(SpokenAudioHandler, "try_play_latest", no_pod)

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    assert CAPABILITIES.absent(STREAMING_SEARCH_PROVIDER)
    resp = await handler._play({"any": "some unknown song"}, ctx, None)
    assert "no streaming provider is installed" in resp.text.lower()
    assert "some unknown song" in resp.text.lower()


@pytest.mark.asyncio
async def test_now_playing_when_stopped() -> None:
    stub = MPDStubClient()  # default state = "stop"
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _NOW_PLAYING_RE.match("what's playing")
    assert m
    response = await handler._now_playing_from_match(m, ctx, None)
    assert "nothing" in response.text.lower()


def test_volume_level_to_percent_table() -> None:
    # The 1-10 knob → hardware %, even steps from 50% (1) to 100% (10).
    assert [level_to_percent(n) for n in range(1, 11)] == [
        50, 56, 61, 67, 72, 78, 83, 89, 94, 100,
    ]
    # Out-of-range numbers clamp: below 1 → floor, above 10 → max.
    assert level_to_percent(0) == 50
    assert level_to_percent(-3) == 50
    assert level_to_percent(50) == 100


def test_volume_percent_to_level_roundtrip() -> None:
    # Every level round-trips through its percent back to itself.
    for n in range(1, 11):
        assert percent_to_level(level_to_percent(n)) == n
    # Off-grid percentages snap to the nearest notch (amixer quantization,
    # manual mixer changes), and clamp into 1-10.
    assert percent_to_level(100) == 10
    assert percent_to_level(73) == 5   # nearest to 72 (level 5)
    assert percent_to_level(5) == 1    # below floor → level 1
    assert percent_to_level(0) == 1


@pytest.mark.asyncio
async def test_volume_set() -> None:
    stub = MPDStubClient()
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _VOLUME_SET_RE.match("set the volume to 5")
    assert m
    response = await handler._volume_set_from_match(m, ctx, None)
    # Confirmation echoes the 1-10 number; satellite gets the mapped %.
    assert response.text == "Volume set to 5."
    assert response.satellite_volume == 72
    # MPD pinned to 100% so music isn't attenuated twice (hardware × MPD).
    assert stub._volume == 100


@pytest.mark.asyncio
async def test_volume_set_above_ten_caps_at_max() -> None:
    stub = MPDStubClient()
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _VOLUME_SET_RE.match("volume 80")
    assert m
    response = await handler._volume_set_from_match(m, ctx, None)
    assert response.text == "Volume set to 10."
    assert response.satellite_volume == 100


@pytest.mark.asyncio
async def test_volume_set_below_one_floors() -> None:
    stub = MPDStubClient()
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _VOLUME_SET_RE.match("volume 0")
    assert m
    response = await handler._volume_set_from_match(m, ctx, None)
    assert response.text == "Volume set to 1."
    assert response.satellite_volume == 50  # the floor


@pytest.mark.asyncio
async def test_volume_up_one_notch() -> None:
    # Satellite at 72% (level 5) → "turn it up" steps to level 6 (78%).
    stub = MPDStubClient()
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, satellite_volume=72)
    m = _VOLUME_UP_RE.match("louder")
    assert m
    response = await handler._volume_up_from_match(m, ctx, None)
    assert response.text == "Volume up to 6."
    assert response.satellite_volume == 78
    assert stub._volume == 100  # pinned


@pytest.mark.asyncio
async def test_volume_down_floors_at_one() -> None:
    # Already at the floor (level 1 / 50%) → "turn it down" stays at 1.
    stub = MPDStubClient()
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, satellite_volume=50)
    m = _VOLUME_DOWN_RE.match("quieter")
    assert m
    response = await handler._volume_down_from_match(m, ctx, None)
    assert response.text == "Volume down to 1."
    assert response.satellite_volume == 50  # never below the floor


@pytest.mark.asyncio
async def test_volume_bump_defaults_to_max_when_unknown() -> None:
    # No satellite report yet → bump treats current as level 10 (boot
    # default), so "turn it down" goes to level 9 (94%).
    stub = MPDStubClient()
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": stub}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)  # satellite_volume=None
    m = _VOLUME_DOWN_RE.match("turn it down")
    assert m
    response = await handler._volume_down_from_match(m, ctx, None)
    assert response.text == "Volume down to 9."
    assert response.satellite_volume == 94


# ─── Routed end-to-end (with DB) ────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_routed_play_command_via_intent(db_session) -> None:
    from domovoi.clients import mpd as mpd_module
    from domovoi.models import Intent
    from domovoi.router import route

    mpd_module._clients = {"kitchen": MPDStubClient()}

    intent = Intent(transcript="play tangerine by led zeppelin", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()

    assert response.matched_handler == "music"
    assert response.matched_path == "fast"
    assert "tangerine" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_play_local_hit_stamps_last_play_source_local(db_session) -> None:
    """A local-library hit must tag the session's `last_play_source` so a
    follow-up "add it to my library" doesn't try to enqueue the previous
    external stream result."""
    from domovoi.clients import mpd as mpd_module
    from domovoi.db.repositories import SessionRepository

    mpd_module._clients = {"kitchen": MPDStubClient()}

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    handler = MusicHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    m = _PLAY_ANY_RE.match("play creep")
    assert m
    response = await handler._play_any_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "playing" in response.text.lower()
    ctx_data = await SessionRepository(db_session).get_context(sid)
    assert ctx_data.get("last_play_source") == "local"
    assert ctx_data.get("last_played_track") is not None


# ─── Local-miss → external streaming fallback (capability seam) ─────────────

class _NoMatchMPDStub(MPDStubClient):
    """Pretends the local library has nothing, so MusicHandler must fall back.

    Both code paths the handler tries before giving up have to miss —
    `prepare_search` (tag-based) and `prepare_filename` (substring
    fallback). Otherwise the parent stub synthesizes a fake hit and the
    provider / offline branches never run.
    """

    async def prepare_search(self, query):
        return None

    async def prepare_filename(self, *substrings):
        return None


@pytest.mark.asyncio
async def test_play_local_miss_falls_back_to_provider_when_online() -> None:
    """When MPD has no local match and ctx.online, MusicHandler delegates
    to the registered streaming-search provider and returns its streaming
    response, retagged so logs reflect the route taken."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": _NoMatchMPDStub()}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _PLAY_ARTIST_RE.match("play creep by radiohead")
    assert m
    with _registered(_FakeStreamingProvider()) as provider:
        response = await handler._play_artist_from_match(m, ctx, None)
    assert "streaming" in response.text.lower()
    # The handler tag is rewritten to music so logs reflect the route taken.
    assert response.matched_handler == "music"
    assert provider.stream_calls == [("kitchen", None, "creep by radiohead")]


@pytest.mark.asyncio
async def test_play_local_miss_no_provider_call_when_offline() -> None:
    """Offline + no local match (+ no podcast) → graceful generic
    'couldn't find anything' message; the provider is never consulted."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": _NoMatchMPDStub()}

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=False)
    m = _PLAY_ARTIST_RE.match("play creep by radiohead")
    assert m
    with _registered(_ExplodingProvider()):
        response = await handler._play_artist_from_match(m, ctx, None)
    assert "couldn't find anything called" in response.text.lower()


@pytest.mark.asyncio
async def test_play_local_hit_does_not_invoke_provider() -> None:
    """Sanity check: when MPD finds a track locally, the streaming
    capability is never touched."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": MPDStubClient()}  # default stub matches everything

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _PLAY_ANY_RE.match("play lofi")
    assert m
    with _registered(_ExplodingProvider()):
        response = await handler._play_any_from_match(m, ctx, None)
    assert "playing" in response.text.lower()


# ─── Random play ───────────────────────────────────────────────────────────

def test_play_random_regex_canonical_phrasings() -> None:
    for s in (
        "play a song",
        "play me a song",
        "play something",
        "play anything",
        "play some music",
        "play random",
        "play random song",
        "play random track",
        "play a random song",
        "shuffle",
        "shuffle my library",
        "shuffle my music",
        "surprise me",
        # Phrasings added when the dashboard's "play something" button
        # (sends "play something random") kept slipping through to
        # an external search. Each one must hit the random/library-only path.
        "play something random",
        "play anything random",
        "play random music",
        "play a random thing",
        "play any song",
        "play any track",
        "play me anything",
        "play me a random song",
        "play some random music",
        "pick something",
        "pick me something",
        "pick a song",
        "pick something for me",
        "pick me something for me",
    ):
        assert _PLAY_RANDOM_RE.match(s), f"expected match: {s!r}"


def test_play_random_regex_does_not_swallow_specific_play() -> None:
    """Specific play commands ('play X by Y', 'play creep') must not
    fall into the random bucket — they have their own dispatch."""
    for s in (
        "play creep",
        "play creep by radiohead",
        "play a song by abba",  # has 'by' → goes to PLAY_ARTIST_RE
        "play lofi",
    ):
        assert not _PLAY_RANDOM_RE.match(s), f"unexpected random match: {s!r}"


@requires_db
@pytest.mark.asyncio
async def test_play_random_picks_from_library(db_session) -> None:
    """Random play picks any track from library_tracks via SQL ORDER BY
    RANDOM() and plays it via MPD's tag search."""
    from sqlalchemy import text as sql_text

    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": MPDStubClient()}

    # Load a few tracks so the random pick has options.
    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (file_path, title, artist) VALUES "
            "(:fp1, :t1, :a1), (:fp2, :t2, :a2), (:fp3, :t3, :a3)"
        ),
        {
            "fp1": "/music/Radiohead/Creep.mp3", "t1": "Creep", "a1": "Radiohead",
            "fp2": "/music/Daft Punk/One More Time.mp3", "t2": "One More Time", "a2": "Daft Punk",
            "fp3": "/music/Akon/Sunny Day.mp3", "t3": "Sunny Day", "a3": "Akon",
        },
    )
    await db_session.commit()

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _PLAY_RANDOM_RE.match("play a song")
    assert m
    response = await handler._play_random_from_match(m, ctx, db_session)
    await db_session.commit()

    assert "playing" in response.text.lower()
    assert response.music_action == "start"
    assert response.matched_handler == "music"


@requires_db
@pytest.mark.asyncio
async def test_play_random_empty_library_responds_politely(db_session) -> None:
    """No tracks in library → friendly 'add something first' message
    rather than a confusing failure."""
    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _PLAY_RANDOM_RE.match("surprise me")
    assert m
    response = await handler._play_random_from_match(m, ctx, db_session)

    assert "empty" in response.text.lower()
    assert "add" in response.text.lower()
    assert response.music_action is None  # no music_start when nothing's playing


@requires_db
@pytest.mark.asyncio
async def test_play_random_clears_stale_stream_state(db_session) -> None:
    """A random pick is a brand-new context — any old external-stream
    seam state from earlier in the session must be cleared so 'next'
    afterwards goes to MPD's next, not into a stale provider search."""
    from sqlalchemy import text as sql_text

    from domovoi.clients import mpd as mpd_module
    from domovoi.db.repositories import SessionRepository

    mpd_module._clients = {"kitchen": MPDStubClient()}

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    repo = SessionRepository(db_session)
    await repo.set_context_key(sid, "last_stream_query", "stale query")
    await repo.set_context_key(sid, "last_stream_title", "Old Song")
    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (file_path, title, artist) "
            "VALUES (:fp, :t, :a)"
        ),
        {"fp": "/music/X/Y.mp3", "t": "Y", "a": "X"},
    )
    await db_session.commit()

    handler = MusicHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    m = _PLAY_RANDOM_RE.match("shuffle")
    assert m
    await handler._play_random_from_match(m, ctx, db_session)
    await db_session.commit()

    ctx_data = await SessionRepository(db_session).get_context(sid)
    assert ctx_data.get("last_stream_query") is None
    assert ctx_data.get("last_stream_title") is None
    assert ctx_data.get("last_play_source") == "local"


# ─── Smart skip ────────────────────────────────────────────────────────────

def test_skip_regex_covers_voice_natural_phrasings() -> None:
    for s in (
        "next", "skip", "next song", "next track",
        "skip this", "skip it", "skip this one",
        "skip this song", "skip this track",
    ):
        assert _NEXT_RE.match(s), f"expected match: {s!r}"


@requires_db
@pytest.mark.asyncio
async def test_smart_skip_advances_past_same_title_results(db_session) -> None:
    """The flagship case: the provider's results for 'lady gaga' are
    mostly Poker Face uploads. 'Skip this' should jump past every entry
    the provider judges likely_same and stream the first different-titled
    result (Bad Romance) — all through the capability seam."""
    from domovoi.clients import mpd as mpd_module
    from domovoi.db.repositories import SessionRepository

    mpd_module._clients = {"kitchen": MPDStubClient()}

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    provider = _FakeStreamingProvider(results=[
        MediaCandidate(id="pf01", title="Poker Face (Official Music Video)"),
        MediaCandidate(id="pf02", title="Lady Gaga - Poker Face (Live)"),
        MediaCandidate(id="pf03", title="Poker Face [Lyrics]"),
        MediaCandidate(id="br01", title="Bad Romance (Official Music Video)"),
    ])

    repo = SessionRepository(db_session)
    await repo.set_context_key(sid, "last_play_source", provider.slug)
    await repo.set_context_key(sid, "last_stream_query", "lady gaga")
    await repo.set_context_key(
        sid, "last_stream_title", "Poker Face (Official Music Video)"
    )
    await db_session.commit()

    handler = MusicHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    m = _NEXT_RE.match("skip this")
    assert m
    with _registered(provider):
        response = await handler._next_from_match(m, ctx, db_session)
    await db_session.commit()

    # Skipped past pf01-pf03 (all "Poker Face" per likely_same) and
    # landed on br01 (Bad Romance — different cleaned title).
    assert "streaming" in response.text.lower()
    assert "bad romance" in response.text.lower()
    assert response.matched_handler == "music"
    assert provider.search_calls == ["lady gaga"]
    (room, candidate, _q), = provider.stream_calls
    assert room == "kitchen" and candidate.id == "br01"
    # The stream seam state is updated to the new track.
    ctx_data = await repo.get_context(sid)
    assert "bad romance" in ctx_data["last_stream_title"].lower()


@requires_db
@pytest.mark.asyncio
async def test_smart_skip_results_exhausted_responds_politely(db_session) -> None:
    """When every provider result is likely_same as the current title
    (an obscure query returning identical uploads), exhausting the list
    should produce a polite 'I've run out' message rather than crashing
    or playing the same song again."""
    from domovoi.clients import mpd as mpd_module
    from domovoi.db.repositories import SessionRepository

    mpd_module._clients = {"kitchen": MPDStubClient()}
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    provider = _FakeStreamingProvider(results=[
        MediaCandidate(id="a", title="Same Song"),
        MediaCandidate(id="b", title="Same Song (Live)"),
        MediaCandidate(id="c", title="Same Song [HD]"),
    ])
    repo = SessionRepository(db_session)
    await repo.set_context_key(sid, "last_play_source", provider.slug)
    await repo.set_context_key(sid, "last_stream_query", "obscure")
    await repo.set_context_key(sid, "last_stream_title", "Same Song")
    await db_session.commit()

    handler = MusicHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    with _registered(provider):
        response = await handler._smart_skip(ctx, db_session)
    assert "run out" in response.text.lower() or "no more" in response.text.lower()
    assert "obscure" in response.text.lower()
    assert provider.stream_calls == []  # nothing fresh to stream


@requires_db
@pytest.mark.asyncio
async def test_smart_skip_local_play_picks_another_random_track(db_session) -> None:
    """When the current playback is a local library track, "next"
    should play *another* random track from the library rather than
    falling off the end of MPD's single-song queue (which would just
    stop playback). Stale external-stream context must be ignored when
    last_play_source is 'local'."""
    from sqlalchemy import text as sql_text

    from domovoi.clients import mpd as mpd_module
    from domovoi.db.repositories import SessionRepository

    mpd = MPDStubClient()
    mpd_module._clients = {"kitchen": mpd}

    # Seed enough tracks so play_random has options to pick from.
    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (file_path, title, artist) VALUES "
            "(:fp1, :t1, :a1), (:fp2, :t2, :a2)"
        ),
        {
            "fp1": "/music/A/One.mp3", "t1": "One", "a1": "A",
            "fp2": "/music/B/Two.mp3", "t2": "Two", "a2": "B",
        },
    )
    await db_session.commit()

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    repo = SessionRepository(db_session)
    # Stale external-stream state from earlier — must be ignored because
    # last_play_source is now 'local' (the provider skip only fires when
    # the source matches the registered provider's slug).
    await repo.set_context_key(sid, "last_stream_query", "stale")
    await repo.set_context_key(sid, "last_stream_title", "x")
    await repo.set_context_key(sid, "last_play_source", "local")
    await db_session.commit()

    handler = MusicHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await handler._smart_skip(ctx, db_session)
    # _play_random returned: music_action="start", text starts with "Playing".
    assert response.music_action == "start"
    assert response.text.lower().startswith("playing ")


@requires_db
@pytest.mark.asyncio
async def test_smart_skip_no_session_inspects_mpd_currentsong(db_session) -> None:
    """The admin / web "skip" path runs without a session_id. With no
    session context to read last_play_source from, smart-skip must
    fall back to inspecting MPD's currentsong — a non-HTTP file
    means a local library track is playing, so "next" should pick
    another random track instead of dead-ending on MPD's
    single-song queue."""
    from sqlalchemy import text as sql_text

    from domovoi.clients import mpd as mpd_module

    mpd = MPDStubClient()
    # Stub MPD into a "playing a local track" state.
    await mpd.play_filename("Artist/One.mp3")
    mpd_module._clients = {"kitchen": mpd}

    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (file_path, title, artist) VALUES "
            "(:fp, :t, :a)"
        ),
        {"fp": "/music/Artist/One.mp3", "t": "One", "a": "Artist"},
    )
    await db_session.commit()

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._smart_skip(ctx, db_session)
    assert response.music_action == "start"
    assert response.text.lower().startswith("playing ")


@pytest.mark.asyncio
async def test_smart_skip_no_session_no_currentsong_falls_through() -> None:
    """No session context AND MPD reports no current song (or an HTTP
    stream we can't reason about as a library track) — there's
    nothing local to skip in, so fall through to ``_simple_ack`` and
    let MPD's ``next`` do whatever it does."""
    from domovoi.clients import mpd as mpd_module

    # Default stub state: state="stop", song=None.
    mpd_module._clients = {"kitchen": MPDStubClient()}
    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._smart_skip(ctx, None)
    assert response.text == "Next track."


@requires_db
@pytest.mark.asyncio
async def test_smart_skip_in_playlist_advances_position(db_session) -> None:
    """When app.state.current_playlist[room] is set and the
    freshness check passes (MPD currentsong.file matches
    last_file_path), `next` should advance to the next position
    within the playlist instead of falling into the random branch.
    """
    from sqlalchemy import text as sql_text

    from domovoi.clients import mpd as mpd_module

    # Seed three tracks in a playlist at positions 0, 1, 2.
    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (id, file_path, title, artist) VALUES "
            "(1001, '/music/X.mp3', 'X', 'X'), "
            "(1002, '/music/Y.mp3', 'Y', 'Y'), "
            "(1003, '/music/Z.mp3', 'Z', 'Z')"
        )
    )
    await db_session.execute(
        sql_text("INSERT INTO playlists (id, name) VALUES (90, 'lineup')")
    )
    await db_session.execute(
        sql_text(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES "
            "(90, 1001, 0), (90, 1002, 1), (90, 1003, 2)"
        )
    )
    await db_session.commit()

    # MPD stub is "playing" track X (position 0). play_search will be
    # called by smart_skip to advance to Y (position 1).
    mpd = MPDStubClient()
    mpd._song = {"file": "X.mp3", "title": "X", "artist": "X"}
    mpd._state = "play"
    mpd_module._clients = {"kitchen": mpd}

    class _FakeApp:
        class state:
            current_playlist = {
                "kitchen": {
                    "playlist_id": 90,
                    "name": "lineup",
                    "mode": "ordered",
                    "last_track_id": 1001,
                    "last_position": 0,
                    "last_file_path": "X.mp3",  # matches MPD's reported file
                }
            }

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, app=_FakeApp)
    response = await handler._smart_skip(ctx, db_session)
    assert response.music_action == "start"
    assert "lineup" in response.text.lower()
    new_entry = _FakeApp.state.current_playlist["kitchen"]
    # Advanced to position 1 (track id 1002, "Y").
    assert new_entry["last_track_id"] == 1002
    assert new_entry["last_position"] == 1


@requires_db
@pytest.mark.asyncio
async def test_smart_skip_playlist_freshness_drop_on_mismatch(db_session) -> None:
    """If MPD's currentsong.file no longer matches the stored
    last_file_path, the playlist entry is stale (something else
    took over the room). _smart_skip should drop the entry and
    fall through to the existing local-source branch."""
    from sqlalchemy import text as sql_text

    from domovoi.clients import mpd as mpd_module

    # Seed a library track for the fallback random branch.
    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (file_path, title, artist) VALUES "
            "('/music/Other.mp3', 'Other', 'O')"
        )
    )
    await db_session.commit()

    mpd = MPDStubClient()
    # MPD reports playing a DIFFERENT local file than what the
    # stored playlist entry remembers.
    mpd._song = {"file": "Other.mp3", "title": "Other", "artist": "O"}
    mpd._state = "play"
    mpd_module._clients = {"kitchen": mpd}

    class _FakeApp:
        class state:
            current_playlist = {
                "kitchen": {
                    "playlist_id": 999,
                    "name": "stale",
                    "mode": "ordered",
                    "last_track_id": 12345,
                    "last_position": 0,
                    "last_file_path": "Stale.mp3",  # doesn't match MPD
                }
            }

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, app=_FakeApp)
    response = await handler._smart_skip(ctx, db_session)
    # Stale entry was dropped; fell through to local-source random.
    assert "kitchen" not in _FakeApp.state.current_playlist
    assert response.music_action == "start"
    assert response.text.lower().startswith("playing ")


@requires_db
@pytest.mark.asyncio
async def test_smart_skip_in_playlist_loops_at_end(db_session) -> None:
    """At the end of an ordered playlist, next loops back to the
    lowest-position track. User-confirmed end-of-playlist behavior."""
    from sqlalchemy import text as sql_text

    from domovoi.clients import mpd as mpd_module

    await db_session.execute(
        sql_text(
            "INSERT INTO library_tracks (id, file_path, title, artist) VALUES "
            "(2001, '/music/L1.mp3', 'L1', 'L'), "
            "(2002, '/music/L2.mp3', 'L2', 'L')"
        )
    )
    await db_session.execute(
        sql_text("INSERT INTO playlists (id, name) VALUES (95, 'looper')")
    )
    await db_session.execute(
        sql_text(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES "
            "(95, 2001, 0), (95, 2002, 1)"
        )
    )
    await db_session.commit()

    mpd = MPDStubClient()
    mpd._song = {"file": "L2.mp3", "title": "L2", "artist": "L"}
    mpd._state = "play"
    mpd_module._clients = {"kitchen": mpd}

    class _FakeApp:
        class state:
            current_playlist = {
                "kitchen": {
                    "playlist_id": 95,
                    "name": "looper",
                    "mode": "ordered",
                    "last_track_id": 2002,
                    "last_position": 1,  # currently on the LAST track
                    "last_file_path": "L2.mp3",
                }
            }

    handler = MusicHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True, app=_FakeApp)
    response = await handler._smart_skip(ctx, db_session)
    assert response.music_action == "start"
    new_entry = _FakeApp.state.current_playlist["kitchen"]
    # Looped back to track 1 (position 0).
    assert new_entry["last_track_id"] == 2001
    assert new_entry["last_position"] == 0
