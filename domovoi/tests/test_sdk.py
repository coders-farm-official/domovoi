"""SDK facade (design §4.10): playback.play_url one-call, namespaced
session context + mediated confirmations, library ingest
(sanitize-before-insert, upsert, attach, NOTIFY, event), realtime
channel naming, core-config whitelist, stub double."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from domovoi.clients import mpd as mpd_mod
from domovoi.events import EVENTS, Event
from domovoi.now_playing import NOW_PLAYING
from domovoi.sdk import build_sdk
from domovoi.sdk.coreconfig import CoreConfigView
from domovoi.sdk.testing import make_stub_sdk
from domovoi.tests.conftest import requires_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
def sdk():
    s = build_sdk("testplug", version="0.1.0")
    yield s
    s.teardown()


@pytest.fixture(autouse=True)
def _clean_mpd_stub_cache():
    mpd_mod._clients.clear()
    yield
    mpd_mod._clients.clear()


# ─── PlaybackAPI ──────────────────────────────────────────────────────────


async def test_play_url_one_call(sdk) -> None:
    """prepare_url (paused-queue) + stamp + fully-populated Response."""
    sdk.now_playing.register_source("testplug")
    response = await sdk.playback.play_url(
        "kitchen", "http://stream.test/abc",
        title="Some Song", artist="Someone", source="testplug",
        now_playing_data={"ref": "abc123"},
        record_play=False,   # media_plays covered by the DB-tier test
    )
    assert response.music_action == "start"
    # music_stream_url is None under stubs (no MPD room provisioned) —
    # the field is populated from mpd_stream_url_for in real deployments.
    assert "Some Song" in response.text

    stamp = NOW_PLAYING.get("kitchen")
    assert stamp is not None
    assert stamp.source == "testplug"
    assert stamp.data["stream_url"] == "http://stream.test/abc"
    assert stamp.data["ref"] == "abc123"

    # The stub MPD client actually received the prepare_url — and the
    # queue was left PAUSED (the music_ready handshake resumes it;
    # dossier §7 invariant 9).
    stub = mpd_mod._clients["kitchen"]
    assert stub._song is not None
    assert stub._song["file"] == "http://stream.test/abc"
    assert stub._song["title"] == "Some Song"     # title stamping
    assert stub._state == "pause"


async def test_play_url_rejects_unregistered_source(sdk) -> None:
    with pytest.raises(ValueError, match="not a registered now-playing source"):
        await sdk.playback.play_url(
            "kitchen", "http://x", title="T", source="never_registered",
        )


async def test_playback_stop_clears_stamp(sdk) -> None:
    sdk.now_playing.register_source("testplug")
    await sdk.playback.play_url(
        "kitchen", "http://stream.test/abc", title="T", source="testplug",
        record_play=False,
    )
    response = await sdk.playback.stop("kitchen")
    assert response.music_action == "stop"
    assert NOW_PLAYING.get("kitchen") is None


# ─── Namespaced session context + confirmations (DB tier) ─────────────────


@requires_db
async def test_session_namespace_roundtrip(sdk, db_session) -> None:
    await sdk.sessions.set_key(db_session, "kitchen", "tuned_station", 42)
    assert await sdk.sessions.get_key(db_session, "kitchen", "tuned_station") == 42
    assert await sdk.sessions.get_key(db_session, "kitchen", "missing", "dflt") == "dflt"

    # Stored under the reserved plugins namespace, not top-level.
    row = (
        await db_session.execute(
            text("SELECT context FROM sessions WHERE room_id = 'kitchen' LIMIT 1")
        )
    ).first()
    ctx = dict(row[0])
    assert ctx["plugins"]["testplug"]["tuned_station"] == 42
    assert "tuned_station" not in ctx

    await sdk.sessions.clear_namespace(db_session, "kitchen")
    assert await sdk.sessions.get_key(db_session, "kitchen", "tuned_station") is None


@requires_db
async def test_clear_namespace_everywhere_bulk_teardown(db_session) -> None:
    # Exercises the real UPDATE ... #- CAST(:path AS TEXT[]) path that the
    # disable/uninstall teardown runs. Regression: the path was once bound as
    # a Postgres array-literal string, which asyncpg rejected with DataError
    # and the teardown swallowed as best-effort — leaving stale plugin keys.
    a = build_sdk("bulkplug")
    other = build_sdk("keepplug")
    try:
        await a.sessions.set_key(db_session, "kitchen", "k", "A")
        await a.sessions.set_key(db_session, "garage", "k", "A")
        await other.sessions.set_key(db_session, "kitchen", "k", "keep")

        touched = await a.sessions.clear_namespace_everywhere(db_session)
        assert touched >= 2  # both sessions this plugin wrote to

        assert await a.sessions.get_key(db_session, "kitchen", "k") is None
        assert await a.sessions.get_key(db_session, "garage", "k") is None
        # A sibling plugin's namespace is untouched.
        assert await other.sessions.get_key(db_session, "kitchen", "k") == "keep"
    finally:
        a.teardown()
        other.teardown()


@requires_db
async def test_session_namespace_isolated_between_plugins(db_session) -> None:
    a = build_sdk("pluga")
    b = build_sdk("plugb")
    try:
        await a.sessions.set_key(db_session, "kitchen", "k", "A")
        await b.sessions.set_key(db_session, "kitchen", "k", "B")
        assert await a.sessions.get_key(db_session, "kitchen", "k") == "A"
        assert await b.sessions.get_key(db_session, "kitchen", "k") == "B"
        await a.sessions.clear_namespace(db_session, "kitchen")
        assert await a.sessions.get_key(db_session, "kitchen", "k") is None
        assert await b.sessions.get_key(db_session, "kitchen", "k") == "B"
    finally:
        a.teardown()
        b.teardown()


@requires_db
async def test_request_confirmation_enforces_slug_namespace(sdk, db_session) -> None:
    with pytest.raises(ValueError, match="namespaced"):
        await sdk.sessions.request_confirmation(
            db_session, "kitchen",
            kind="core.self_intro", handler="voice_profile",
        )


@requires_db
async def test_request_confirmation_parks_payload(db_session) -> None:
    """A plugin kind declared on a (stubbed) registered handler parks the
    single-slot payload through the mediated core API."""
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.handlers.base import Handler, HandlerDisplay
    from domovoi.models import Response

    class _FakePluginHandler(Handler):
        name = "testplug"
        priority_band = 400
        display = HandlerDisplay(label="Test Plug")
        tool_schema = {"name": "testplug", "description": "x", "parameters": {}}
        fast_paths: list = []
        confirmation_kinds = ("testplug.pick_one",)
        plugin_slug = "testplug"

        async def execute(self, intent, ctx, session) -> Response:
            return Response(text="ok")

    sdk = build_sdk("testplug")
    HANDLER_BY_NAME["testplug"] = _FakePluginHandler()
    try:
        await sdk.sessions.request_confirmation(
            db_session, "kitchen",
            kind="testplug.pick_one", handler="testplug",
            data={"options": [1, 2]}, prompt="Which one?",
        )
        row = (
            await db_session.execute(
                text("SELECT context FROM sessions WHERE room_id = 'kitchen' LIMIT 1")
            )
        ).first()
        pending = dict(row[0])["pending_confirmation"]
        assert pending["handler"] == "testplug"
        assert pending["kind"] == "testplug.pick_one"
        assert pending["options"] == [1, 2]
    finally:
        HANDLER_BY_NAME.pop("testplug", None)
        sdk.teardown()


# ─── LibraryAPI (DB tier) ─────────────────────────────────────────────────


@requires_db
async def test_ingest_track_sanitizes_upserts_and_notifies(
    sdk, db_session, tmp_path, monkeypatch
) -> None:
    from domovoi.config import settings

    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    # The DOWNLOADED file has some provider-templated name; the illegal
    # characters come from the resolved TITLE/ARTIST (sanitized into the
    # canonical path before the insert).
    raw = tmp_path / "dl" / "raw_download.mp3"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"ID3fake")

    events: list[Event] = []

    async def _on_added(evt: Event) -> None:
        events.append(evt)

    sub = EVENTS.subscribe("core.library_track_added", _on_added, owner="test")
    try:
        track = await sdk.library.ingest_track(
            db_session,
            file_path=raw,
            title="Song: The Best?",
            artist="AC/DC",
            source="testplug",
            source_id="vid001",
            added_via="voice",
            metadata={"album": "Album", "duration_sec": 200},
        )
        # Windows-strict sanitize BEFORE insert: path in the row is the
        # renamed canonical location with illegal chars replaced.
        assert track.id > 0
        final = Path(track.file_path)
        assert final.exists()
        assert final.parent.name == "AC_DC"
        assert "?" not in final.name and ":" not in final.name

        # Upsert on (source, source_id): same id, updated metadata.
        again = await sdk.library.ingest_track(
            db_session,
            file_path=final,
            title="Song: The Best? (Remaster)",
            artist="AC/DC",
            source="testplug",
            source_id="vid001",
            added_via="voice",
        )
        assert again.id == track.id

        found = await sdk.library.get_by_source_id(db_session, "testplug", "vid001")
        assert found is not None and found.id == track.id

        await asyncio.sleep(0.05)
        assert events and events[0].payload["track_id"] == track.id
    finally:
        EVENTS.unsubscribe(sub)


@requires_db
async def test_library_search_and_fuzzy_wrappers(sdk, db_session) -> None:
    await db_session.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, artist) VALUES "
            "('/m/Radiohead/Creep.mp3', 'Creep', 'Radiohead'), "
            "('/m/TLC/Creep.mp3', 'Creep', 'TLC')"
        )
    )
    results = await sdk.library.search(db_session, "creep")
    assert len(results) == 2

    match = await sdk.library.find_fuzzy_match(
        db_session, "Creep (Official Video)", "Radiohead"
    )
    assert match is not None and match.artist == "Radiohead"

    assert await sdk.library.find_fuzzy_match(db_session, "Zebra Anthem") is None


async def test_parse_title_artist_wrapper(sdk) -> None:
    assert sdk.library.parse_title_artist("Radiohead - Creep (Official Video)") == (
        "Creep", "Radiohead",
    )
    # No pattern → raw title back, artist None.
    assert sdk.library.parse_title_artist("Just A Title") == ("Just A Title", None)


# ─── Realtime / config / events / logger / stub ───────────────────────────


@requires_db
async def test_realtime_notify_channel_naming(sdk, db_session) -> None:
    channel = await sdk.realtime.notify(db_session, "stations_changed", "tuned")
    assert channel == "plugin_testplug_stations_changed"
    with pytest.raises(ValueError, match="bad channel suffix"):
        await sdk.realtime.notify(db_session, "Bad-Name!", "x")


async def test_core_config_whitelist() -> None:
    view = CoreConfigView()
    assert isinstance(view.bot_name, str)
    assert isinstance(view.use_stubs, bool)
    with pytest.raises(AttributeError, match="not plugin-visible"):
        _ = view.database_url
    with pytest.raises(AttributeError, match="read-only"):
        view.bot_name = "Hacked"


async def test_events_view_force_prefixes_and_teardown(sdk) -> None:
    seen: list[Event] = []

    async def cb(evt: Event) -> None:
        seen.append(evt)

    sdk.events.subscribe("plugin.testplug.thing_happened", cb)
    sdk.events.emit("thing_happened", {"n": 1})            # force-prefixed
    sdk.events.emit("plugin.testplug.thing_happened", {"n": 2})  # already prefixed
    await asyncio.sleep(0.05)
    assert [e.payload["n"] for e in seen] == [1, 2]

    sdk.teardown()
    assert EVENTS.subscriber_count("plugin.testplug.thing_happened") == 0


async def test_get_logger_name_and_idempotency(sdk) -> None:
    import logging

    log1 = sdk.log
    assert log1.name == "plugin.testplug"
    from domovoi.sdk.observability import get_logger

    log2 = get_logger("testplug")
    assert log2 is log1
    file_handlers = [
        h for h in log1.handlers
        if getattr(h, "_domovoi_plugin_file_handler", False)
    ]
    assert len(file_handlers) <= 1   # never stacks
    assert isinstance(log1, logging.Logger)


async def test_state_store_survives_rebuild_until_teardown() -> None:
    a = build_sdk("stateful")
    a.state["x"] = 1
    b = build_sdk("stateful")            # enable→disable→enable cycle
    assert b.state["x"] == 1
    b.teardown()
    c = build_sdk("stateful")
    assert "x" not in c.state
    c.teardown()


async def test_make_stub_sdk_recording_shapes() -> None:
    stub = make_stub_sdk("radio")
    stub.now_playing.register_source("radio")
    response = await stub.playback.play_url(
        "kitchen", "http://s", title="KEXP", source="radio",
    )
    assert response.music_action == "start"
    assert stub.playback.calls and stub.playback.calls[0]["room_id"] == "kitchen"

    result = await stub.acquisition.enqueue(None, kind="query", text="a b")
    assert result.outcome == "enqueued"
    claimed = await stub.acquisition.claim_next(None)
    assert claimed is not None and claimed["status"] == "claimed"

    await stub.sessions.set_key(None, "kitchen", "k", 1)
    assert await stub.sessions.get_key(None, "kitchen", "k") == 1
    with pytest.raises(ValueError):
        await stub.sessions.request_confirmation(
            None, "kitchen", kind="core.nope", handler="radio",
        )
    assert stub.connectivity.online is True


# ─── Session-id resolution helper ─────────────────────────────────────────


@requires_db
async def test_resolve_session_id_room_vs_uuid(db_session) -> None:
    from domovoi.sdk.sessions import resolve_session_id

    sid = await resolve_session_id(db_session, "kitchen")
    assert isinstance(sid, UUID)
    # Same room resolves to the same (most recent) session.
    assert await resolve_session_id(db_session, "kitchen") == sid
    # A UUID passes through (after touch).
    assert await resolve_session_id(db_session, sid) == sid
