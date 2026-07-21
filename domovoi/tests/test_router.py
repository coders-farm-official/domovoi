from __future__ import annotations

import re

import pytest

from domovoi.handlers import (
    HANDLER_BY_NAME,
    HANDLERS,
    register_handler,
    unregister_handler,
)
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response
from domovoi.router import _LEADING_FILLER_RE, route
from domovoi.tests.conftest import requires_db


def _dry_run_winner(transcript: str) -> str | None:
    """Replicate the router's normalization + band-ordered fast-path
    matching WITHOUT dispatching — the §13.2 collision-corpus dry run."""
    t = transcript.lower().strip().rstrip(".,!?")
    t = _LEADING_FILLER_RE.sub("", t)
    for handler in HANDLERS:
        for fp in handler.fast_paths:
            if fp.pattern.match(t):
                return handler.name
    return None


@requires_db
@pytest.mark.asyncio
async def test_fast_path_dispatches_to_timer_handler(db_session) -> None:
    intent = Intent(transcript="set a timer for 5 minutes", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_handler == "timer"
    assert response.matched_path == "fast"
    assert response.session_id is not None
    assert "5 minutes" in response.text


@requires_db
@pytest.mark.asyncio
async def test_unmatched_falls_through_to_qa(db_session) -> None:
    intent = Intent(transcript="tell me a story about a dragon", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_path == "qa"
    assert "stub qa" in response.text


@requires_db
@pytest.mark.asyncio
async def test_intents_log_row_written(db_session) -> None:
    from sqlalchemy import text

    intent = Intent(transcript="set a timer for 5 minutes", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    await route(intent, ctx, db_session)
    await db_session.commit()

    rows = (
        await db_session.execute(text("SELECT matched_handler, matched_path, online FROM intents_log"))
    ).all()
    assert len(rows) == 1
    assert rows[0][0] == "timer"
    assert rows[0][1] == "fast"
    assert rows[0][2] is True


@requires_db
@pytest.mark.asyncio
async def test_conversation_log_captures_user_and_assistant(db_session) -> None:
    """conversation_log audit table — the user-side transcript AND the assistant's
    response both land on the same row, with routing metadata that
    matches the intents_log row written in the same transaction."""
    from sqlalchemy import text

    intent = Intent(transcript="set a timer for 5 minutes", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    await route(intent, ctx, db_session)
    await db_session.commit()

    rows = (
        await db_session.execute(
            text(
                """
                SELECT user_text, assistant_text, matched_handler,
                       matched_path, online, room_id, session_id
                FROM conversation_log
                """
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0][0] == "set a timer for 5 minutes"
    assert "5 minutes" in rows[0][1].lower()  # the assistant confirmation
    assert rows[0][2] == "timer"
    assert rows[0][3] == "fast"
    assert rows[0][4] is True
    assert rows[0][5] == "kitchen"
    assert rows[0][6] is not None  # session_id was set


@requires_db
@pytest.mark.asyncio
async def test_conversation_log_accumulates_across_turns(db_session) -> None:
    """Three turns on the same session should produce three
    conversation_log rows — independent of whether each turn was
    fast-path, QA, or anything else. Regression test for the issue
    where mid-session sessions disappear and history thinks each turn
    is the first."""
    from sqlalchemy import text

    ctx = Context(room_id="kitchen", online=True)

    # Turn 1: clock fast-path.
    r1 = await route(
        Intent(transcript="what time is it", room_id="kitchen"),
        ctx,
        db_session,
    )
    await db_session.commit()
    assert r1.session_id is not None

    # Turn 2: same session — feed the established session_id forward
    # the same way streaming.py does between utterances.
    r2 = await route(
        Intent(transcript="what's the date", room_id="kitchen", session_id=r1.session_id),
        ctx.model_copy(update={"session_id": r1.session_id}),
        db_session,
    )
    await db_session.commit()

    # Turn 3.
    r3 = await route(
        Intent(transcript="what day is it", room_id="kitchen", session_id=r2.session_id),
        ctx.model_copy(update={"session_id": r2.session_id}),
        db_session,
    )
    await db_session.commit()

    # All three turns share one session.
    assert r1.session_id == r2.session_id == r3.session_id

    rows = (
        await db_session.execute(
            text(
                """
                SELECT user_text, session_id
                FROM conversation_log
                ORDER BY id
                """
            )
        )
    ).all()
    assert len(rows) == 3
    assert [r[0] for r in rows] == [
        "what time is it",
        "what's the date",
        "what day is it",
    ]
    # All three rows reference the same session_id.
    assert rows[0][1] == rows[1][1] == rows[2][1]


@requires_db
@pytest.mark.asyncio
async def test_conversation_log_carries_person_and_presence(db_session) -> None:
    """When the pre-router populates ctx.person_id / presence_tier (via
    voice_identifier), those fields propagate into conversation_log
    too — same way they already do into intents_log."""
    from sqlalchemy import text

    from domovoi.db.repositories import PeopleRepository

    sarah_id = await PeopleRepository(db_session).create(name="Sarah")
    await db_session.commit()

    ctx = Context(
        room_id="kitchen",
        online=True,
        person_id=sarah_id,
        presence_tier="low",
    )
    await route(
        Intent(transcript="set a timer for 1 minute", room_id="kitchen"),
        ctx,
        db_session,
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            text("SELECT person_id, presence_tier FROM conversation_log")
        )
    ).first()
    assert row is not None
    assert row[0] == sarah_id
    assert row[1] == "low"


# ─── Offline gate: fake a networked handler and verify fallback_offline fires. ──

class _FakeNetworkedHandler(Handler):
    name = "fake_network"
    priority_band = 400  # general plugin space
    display = HandlerDisplay(label="Fake Network", tone="info")
    requires_network = "yes"
    tool_schema = {
        "name": "fake_network",
        "description": "test-only",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    def __init__(self) -> None:
        # Deliberately a bare tuple — declaration sugar the router must
        # normalize on the fly (design §4.3 "tuples accepted as sugar").
        self.fast_paths = [(re.compile(r"^check the web$"), _FakeNetworkedHandler._online_match)]
        self.online_called = False
        self.offline_called = False

    async def _online_match(self, m, ctx, session) -> Response:
        self.online_called = True
        return Response(text="online response")

    async def execute(self, intent, ctx, session) -> Response:
        return Response(text="online response")

    async def fallback_offline(self, intent, ctx, session) -> Response:
        self.offline_called = True
        return Response(text="offline: I can't reach the web")


@requires_db
@pytest.mark.asyncio
async def test_offline_gate_invokes_fallback(db_session) -> None:
    fake = _FakeNetworkedHandler()
    HANDLERS.append(fake)
    try:
        intent = Intent(transcript="check the web", room_id="kitchen")
        ctx = Context(room_id="kitchen", online=False)
        response = await route(intent, ctx, db_session)
        await db_session.commit()
        assert fake.offline_called is True
        assert fake.online_called is False
        assert response.matched_path == "fast_offline"
        assert "offline" in response.text.lower()
    finally:
        HANDLERS.remove(fake)


@requires_db
@pytest.mark.asyncio
async def test_pending_confirmation_routes_yes_to_handler(db_session) -> None:
    """If the previous turn parked a pending_confirmation, a clear
    "yes" must route directly to that handler's `handle_confirmation`
    method instead of falling through to QA."""
    import base64

    import numpy as np

    from domovoi.clients.voice_embedder import EMBEDDING_DIM, StubVoiceEmbedder
    from domovoi.db.repositories import SessionRepository
    from sqlalchemy import text as sql_text

    pcm = (np.full(16_000, 1234, dtype=np.int16)).tobytes()
    embedding = await StubVoiceEmbedder().embed(pcm)
    assert embedding is not None
    embedding_b64 = base64.b64encode(
        embedding.astype(np.float32).tobytes()
    ).decode("ascii")

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "voice_profile",
            "kind": "core.self_intro",
            "name": "Sarah",
            "embedding_b64": embedding_b64,
        },
    )
    await db_session.commit()

    intent = Intent(transcript="Yes", room_id="kitchen", session_id=sid)
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()

    assert response.matched_handler == "voice_profile"
    assert response.matched_path == "confirmation"
    # Enrollment happened: people row inserted.
    rows = (
        await db_session.execute(sql_text("SELECT name FROM people"))
    ).all()
    assert len(rows) == 1 and rows[0][0] == "Sarah"

    # pending_confirmation cleared so a stray "yes" later doesn't
    # re-enroll the same person.
    ctx_data = await SessionRepository(db_session).get_context(sid)
    assert ctx_data.get("pending_confirmation") is None


@requires_db
@pytest.mark.asyncio
async def test_pending_confirmation_falls_through_when_not_yes_no(db_session) -> None:
    """An ambiguous answer (anything other than yes/no) leaves the
    pending_confirmation in place and routes the utterance normally,
    so a follow-up "yes" can still complete the original flow."""
    from domovoi.db.repositories import SessionRepository

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {"handler": "voice_profile", "kind": "core.self_intro", "name": "Sarah", "embedding_b64": ""},
    )
    await db_session.commit()

    intent = Intent(
        transcript="set a timer for 5 minutes", room_id="kitchen", session_id=sid
    )
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()

    assert response.matched_handler == "timer"
    assert response.matched_path == "fast"
    # Pending stays put for the next turn.
    ctx_data = await SessionRepository(db_session).get_context(sid)
    assert ctx_data.get("pending_confirmation") is not None


# ─── Polite/filler prefix stripping (Bug 2 — 2026-05-08 incident) ─────────


@requires_db
@pytest.mark.asyncio
async def test_polite_prefix_routes_to_fast_path(db_session) -> None:
    """`please set a timer for 5 minutes` must reach the timer fast path.
    Without the leading-filler strip in the router, the start-anchored
    timer regex misses, the LLM tool-call fallback returns nothing in
    the stub, and the request lands in QA — exactly the failure mode
    that produced the 2026-05-08 'qa hallucinated that it added the
    song' incident with `please add that to my library`."""
    intent = Intent(
        transcript="please set a timer for 5 minutes", room_id="kitchen"
    )
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_handler == "timer"
    assert response.matched_path == "fast"


@requires_db
@pytest.mark.asyncio
async def test_request_prefix_routes_to_fast_path(db_session) -> None:
    """`can you set a timer for 5 minutes` and `could you please set ...`
    are common conversational phrasings and must also reach the fast
    path. The filler regex collapses stacked prefixes in one substitution."""
    for transcript in (
        "can you set a timer for 5 minutes",
        "could you please set a timer for 5 minutes",
        "would you please set a timer for 5 minutes",
    ):
        response = await route(
            Intent(transcript=transcript, room_id="kitchen"),
            Context(room_id="kitchen", online=True),
            db_session,
        )
        await db_session.commit()
        assert response.matched_handler == "timer", transcript
        assert response.matched_path == "fast", transcript


@requires_db
@pytest.mark.asyncio
async def test_yes_prefix_with_no_pending_routes_to_fast_path(
    db_session,
) -> None:
    """`yes, set a timer for 5 minutes` with no pending_confirmation must
    fall through the pre-empt and then get filler-stripped down to the
    timer fast path. The yes/no pre-empt only fires when there's an
    actual pending payload to claim — without one, the leading "yes,"
    is just filler that would otherwise wreck the start-anchored fast
    path."""
    intent = Intent(
        transcript="yes, set a timer for 5 minutes", room_id="kitchen"
    )
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_handler == "timer"
    assert response.matched_path == "fast"


@requires_db
@pytest.mark.asyncio
async def test_bare_yes_with_pending_still_routes_to_confirmation(
    db_session,
) -> None:
    """Filler-strip must NOT short-circuit the pending-confirmation
    pre-empt: a bare "yes" with a parked pending_confirmation must
    still route to the parking handler's confirmation method, even
    though "yes" is in the filler regex's vocabulary. The pre-empt
    runs FIRST and returns before filler-strip ever touches the
    transcript."""
    from domovoi.db.repositories import SessionRepository

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {
            "handler": "voice_profile",
            "kind": "core.self_intro",
            "name": "Sarah",
            "embedding_b64": "",
        },
    )
    await db_session.commit()

    response = await route(
        Intent(transcript="yes", room_id="kitchen", session_id=sid),
        Context(session_id=sid, room_id="kitchen", online=True),
        db_session,
    )
    await db_session.commit()
    assert response.matched_handler == "voice_profile"
    assert response.matched_path == "confirmation"


@requires_db
@pytest.mark.asyncio
async def test_online_path_taken_when_online(db_session) -> None:
    fake = _FakeNetworkedHandler()
    HANDLERS.append(fake)
    try:
        intent = Intent(transcript="check the web", room_id="kitchen")
        ctx = Context(room_id="kitchen", online=True)
        response = await route(intent, ctx, db_session)
        await db_session.commit()
        assert fake.online_called is True
        assert fake.offline_called is False
        assert response.matched_path == "fast"
    finally:
        HANDLERS.remove(fake)


# ─── Band ordering + the §4.2 ordering-regression corpus ──────────────────


# One utterance per ordering guarantee (design §13.2: the core-owned
# collision corpus). Every entry must keep resolving to its owning handler
# no matter how registration order or band assignments evolve.
ORDERING_CORPUS: list[tuple[str, str]] = [
    # dismiss FIRST — ahead of voice_profile ("I'm good" is not a name)
    ("i'm good", "dismiss"),
    ("i'm fine", "dismiss"),
    ("never mind", "dismiss"),
    ("false alarm", "dismiss"),
    # voice_profile before greedy captures
    ("i'm sarah", "voice_profile"),
    ("forget me", "voice_profile"),
    ("who am i", "voice_profile"),
    # device / utility cluster
    ("fix the wifi", "wifi"),
    ("what voices do you have", "voice"),
    ("switch to ryan", "voice"),
    # reminder before timer ("remind" collision)
    ("remind me to feed the cat in 10 minutes", "reminder"),
    ("set a timer for 5 minutes", "timer"),
    ("what's 5 plus 3", "calculator"),
    ("what time is it", "clock"),
    ("say that again", "repeat"),
    ("double check that", "double_check"),
    ("are you sure", "double_check"),
    # dropin before intercom; intercom before voice_notes
    ("drop in on the kitchen", "dropin"),
    ("hang up", "dropin"),
    ("tell everyone dinner is ready", "intercom"),
    ("let's have a chat", "chat_mode"),
    ("remember that i like jazz", "memory"),
    ("my favorite team is the mariners", "memory"),
    ("homelab status", "homelab"),
    # news before greedy media
    ("what's the news", "news"),
    ("any news about spacex", "news"),
    # spoken_audio's anchored media before playlist/music
    ("play the latest episode of the daily", "spoken_audio"),
    ("resume my book", "spoken_audio"),
    ("next chapter", "spoken_audio"),
    # playlist before music's ^play catch-all
    ("play my favorites", "playlist"),
    ("play the chill playlist", "playlist"),
    ("shuffle the chill playlist", "playlist"),
    ("make a new playlist called chill", "playlist"),
    # music owns the greedy catch-all + bare transport
    ("play the beatles", "music"),
    ("play creep by radiohead", "music"),
    ("pause", "music"),
    ("stop the music", "music"),
    ("skip this", "music"),
    # library's anchored "find X in my library"
    ("find creep in my library", "library"),
    ("how many songs do i have", "library"),
    ("what did i add today", "library"),
]


def test_ordering_regression_corpus() -> None:
    for transcript, expected in ORDERING_CORPUS:
        winner = _dry_run_winner(transcript)
        assert winner == expected, (
            f"{transcript!r} routed to {winner!r}, expected {expected!r}"
        )


class _FakeRadioHandler(Handler):
    """Stand-in for the bundled radio plugin: band 280, between
    spoken_audio (270) and playlist (290), so "play 97.5 fm" must NOT
    be poached by music's greedy catch-all at 300."""

    name = "fake_radio"
    priority_band = 280
    plugin_slug = "fake_radio"
    display = HandlerDisplay(label="Fake Radio", tone="media")
    requires_network = "degraded"
    tool_schema = {
        "name": "fake_radio",
        "description": "test-only",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(
                re.compile(r"^play \d{2,3}(?:\.\d)? fm$"),
                _FakeRadioHandler._tune,
            ),
        ]

    async def _tune(self, m, ctx, session) -> Response:
        return Response(text="tuned")

    async def execute(self, intent, ctx, session) -> Response:
        return Response(text="tuned")

    async def fallback_offline(self, intent, ctx, session) -> Response:
        return Response(text="offline radio")


def test_band_280_wins_play_fm_over_music_catchall() -> None:
    """The §4.2 keystone: a media handler in band 280 beats music (300)
    for "play 97.5 fm"; without it, music's ^play catch-all takes the
    utterance. Registration order must not matter."""
    assert _dry_run_winner("play 97.5 fm") == "music"  # no radio installed
    fake = _FakeRadioHandler()
    register_handler(fake)
    try:
        assert _dry_run_winner("play 97.5 fm") == "fake_radio"
        # ... and the rest of the corpus is untouched by the newcomer.
        for transcript, expected in ORDERING_CORPUS:
            assert _dry_run_winner(transcript) == expected, transcript
        # Registry stays band-sorted with the plugin slotted in.
        bands = [h.priority_band for h in HANDLERS]
        assert bands == sorted(bands)
        assert HANDLER_BY_NAME["fake_radio"] is fake
    finally:
        unregister_handler(fake)
    assert _dry_run_winner("play 97.5 fm") == "music"
    assert "fake_radio" not in HANDLER_BY_NAME


# ─── Degraded offline_ok asymmetry (design §4.3 / dossier §2.1 M4) ─────────


class _FakeDegradedHandler(Handler):
    name = "fake_degraded"
    priority_band = 410
    display = HandlerDisplay(label="Fake Degraded", tone="info")
    requires_network = "degraded"
    tool_schema = {
        "name": "fake_degraded",
        "description": "test-only",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    def __init__(self) -> None:
        self.fast_paths = [
            # Needs the network (e.g. an internet stream) — the router
            # must auto-fallback this one while offline.
            FastPath(
                re.compile(r"^degraded online thing$"),
                _FakeDegradedHandler._networked,
                offline_ok=False,
            ),
            # Works offline (e.g. local hardware) — offline_ok defaults
            # True for degraded handlers; the router must dispatch it
            # normally even while offline.
            FastPath(
                re.compile(r"^degraded local thing$"),
                _FakeDegradedHandler._local,
            ),
        ]
        self.calls: list[str] = []

    async def _networked(self, m, ctx, session) -> Response:
        self.calls.append("networked")
        return Response(text="did the online thing")

    async def _local(self, m, ctx, session) -> Response:
        self.calls.append("local")
        return Response(text="did the local thing")

    async def execute(self, intent, ctx, session) -> Response:
        return Response(text="degraded execute")

    async def fallback_offline(self, intent, ctx, session) -> Response:
        self.calls.append("fallback")
        return Response(text="offline degraded fallback")


@requires_db
@pytest.mark.asyncio
async def test_degraded_offline_ok_false_path_falls_back(db_session) -> None:
    fake = _FakeDegradedHandler()
    register_handler(fake)
    try:
        ctx = Context(room_id="kitchen", online=False)
        r = await route(
            Intent(transcript="degraded online thing", room_id="kitchen"),
            ctx, db_session,
        )
        await db_session.commit()
        assert fake.calls == ["fallback"]
        assert r.matched_path == "fast_offline"
        assert r.matched_handler == "fake_degraded"
    finally:
        unregister_handler(fake)


@requires_db
@pytest.mark.asyncio
async def test_degraded_default_path_still_runs_offline(db_session) -> None:
    """A degraded handler is NOT auto-fallen-back wholesale — its
    offline-capable paths (offline_ok unset ⇒ True) dispatch normally
    while offline. This is what makes fallback_offline genuinely
    reachable instead of dead code."""
    fake = _FakeDegradedHandler()
    register_handler(fake)
    try:
        ctx = Context(room_id="kitchen", online=False)
        r = await route(
            Intent(transcript="degraded local thing", room_id="kitchen"),
            ctx, db_session,
        )
        await db_session.commit()
        assert fake.calls == ["local"]
        assert r.matched_path == "fast"
    finally:
        unregister_handler(fake)


@requires_db
@pytest.mark.asyncio
async def test_degraded_paths_run_normally_online(db_session) -> None:
    fake = _FakeDegradedHandler()
    register_handler(fake)
    try:
        ctx = Context(room_id="kitchen", online=True)
        for t in ("degraded online thing", "degraded local thing"):
            await route(Intent(transcript=t, room_id="kitchen"), ctx, db_session)
            await db_session.commit()
        assert fake.calls == ["networked", "local"]
    finally:
        unregister_handler(fake)


# ─── Confirmation namespacing through the ABC (design §4.7) ────────────────


@requires_db
@pytest.mark.asyncio
async def test_legacy_unnamespaced_kind_still_dispatches(db_session) -> None:
    """A payload parked before the namespacing cutover carries a bare
    kind ("self_intro"); the router normalizes it to "core.self_intro"
    instead of wedging the session."""
    from domovoi.db.repositories import SessionRepository

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {"handler": "voice_profile", "kind": "self_intro", "name": "Sarah",
         "embedding_b64": ""},
    )
    await db_session.commit()

    response = await route(
        Intent(transcript="no", room_id="kitchen", session_id=sid),
        Context(session_id=sid, room_id="kitchen", online=True),
        db_session,
    )
    await db_session.commit()
    assert response.matched_handler == "voice_profile"
    assert response.matched_path == "confirmation"


@requires_db
@pytest.mark.asyncio
async def test_undeclared_kind_falls_through_to_normal_routing(db_session) -> None:
    """A pending payload whose kind the handler does NOT declare is
    undispatchable: the router logs and routes the turn normally (the
    "yes" here has nothing valid to claim, so it falls to QA)."""
    from domovoi.db.repositories import SessionRepository

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await SessionRepository(db_session).set_context_key(
        sid,
        "pending_confirmation",
        {"handler": "timer", "kind": "core.bogus_kind"},
    )
    await db_session.commit()

    response = await route(
        Intent(transcript="yes", room_id="kitchen", session_id=sid),
        Context(session_id=sid, room_id="kitchen", online=True),
        db_session,
    )
    await db_session.commit()
    assert response.matched_path != "confirmation"


@pytest.mark.asyncio
async def test_request_confirmation_rejects_undeclared_kind() -> None:
    """The mediated pending API validates kind ∈ confirmation_kinds at
    SET time (design §4.7) — colliding payload shapes can't be parked."""
    from domovoi.confirmations import request_confirmation

    with pytest.raises(ValueError):
        await request_confirmation(
            None, None, kind="core.not_a_thing", handler="playlist", data={},
        )
    with pytest.raises(ValueError):
        await request_confirmation(
            None, None, kind="core.playlist_choice", handler="nope", data={},
        )


# ─── matched_path is app-validated (registered_values, design §6.4) ────────


def test_matched_path_validation_replaces_the_db_check() -> None:
    from domovoi import registered_values

    for good in ("fast", "fast_offline", "qa", "confirmation", "chat"):
        assert registered_values.require("matched_path", good) == good
    with pytest.raises(ValueError):
        registered_values.require("matched_path", "not_a_path")
    # Plugins can open the vocabulary at registration time.
    registered_values.register("matched_path", "test_path", owner="test_plugin")
    try:
        assert registered_values.require("matched_path", "test_path") == "test_path"
    finally:
        registered_values.unregister_owner("test_plugin")
    with pytest.raises(ValueError):
        registered_values.require("matched_path", "test_path")
