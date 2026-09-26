"""Household speech and personal content are for PAIRED DEVICES only
(owner decision, 2026-09-26).

What the household said to Domovoi — a room's or a person's conversation
turns, voice notes, the chat threads typed on the dashboard and the images
sent into them, the wake-word clips recorded of somebody's voice — and what
the house keeps about a person (memories, pending ones included; favorites;
preferences) used to answer any host on the LAN with no credential at all,
while ``/ws/state`` (device tier) and the satellite log pull (admin read),
which carry the same kind of content, were already gated.

These reads now take the READ half of the device tier,
``admin_auth.require_device_read`` — the same gate the Documents, Files,
Images and Videos reads take:

* no credential, a stale household token, or a wrong ``?device_token=`` is
  **401**, and the handler is never reached;
* the household token (``X-Device-Token``), an admin Bearer, the dashboard
  cookie, or ``?device_token=`` gets through — the cookie and the query
  are the read half's precedent (a reloaded browser holds only the cookie;
  an ``<img>`` / ``<audio>`` cannot set a header);
* before first-run setup the whole surface keeps its LAN grace.

Two halves, like :mod:`test_web_daily_tier`:

* DB-FREE: every route driven through the real web app with the auth
  primitives faked (``auth_testkit.install_fake_db``). "Gets through" is
  proved by REACHING the handler — its first side effect is replaced with
  a marker that raises — so a refusal is also proof nothing was read.
* DB-BACKED: the real credentials against a real database — admin claimed
  through the real setup endpoint, the household token read back out of
  ``household_device_tokens``, a person with a spoken turn and a memory
  seeded — so "a paired phone reads it and nothing else does" is shown on
  the rows themselves.

Which routes are on this list, and which reads were deliberately LEFT
open, is pinned structurally in :mod:`test_route_auth_matrix`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    install_fake_db,
    web_app,
    web_client,
)
from domovoi.tests.conftest import requires_db

ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"


class Reached(BaseException):
    """Raised by the markers below when a request gets past the gate.

    A ``BaseException`` so no handler's ``except Exception`` can swallow
    the proof."""


# name -> (concrete GET path, web.backend.api module, attribute the handler
# touches FIRST once the gate lets it through). The concrete paths are the
# routes in test_route_auth_matrix.SPEECH_READS with sample ids filled in.
SPEECH_READS: dict[str, tuple[str, str, str]] = {
    "room-conversations": ("/api/satellites/kitchen/conversations",
                           "satellites", "session_scope"),
    "room-notes": ("/api/satellites/kitchen/notes", "satellites", "session_scope"),
    "person-conversations": ("/api/people/1/conversations", "people", "session_scope"),
    "person-notes": ("/api/people/1/notes", "people", "session_scope"),
    "person-memories": ("/api/people/1/memories", "people", "session_scope"),
    "person-favorites": ("/api/people/1/favorites", "people", "session_scope"),
    "person-preferences": ("/api/people/1/preferences", "people", "session_scope"),
    "chat-threads": ("/api/chat/threads", "chat", "session_scope"),
    "chat-messages": ("/api/chat/threads/1/messages", "chat", "session_scope"),
    "chat-upload": ("/api/chat/uploads/" + "ab" * 16, "chat", "_upload_path"),
    "wake-clip-audio": ("/api/wake-words/1/clips/clip_001.wav/audio",
                        "wake_words", "session_scope"),
}
_IDS = sorted(SPEECH_READS)


@pytest.fixture
def mark_reached(monkeypatch):
    """Replace each route's first side effect with a marker and return the
    recorder: a call that gets past the gate raises :class:`Reached`; one
    that does not never touches the marker."""
    from web.backend import api as web_api

    seen: list[str] = []

    @asynccontextmanager
    async def marked_session():
        seen.append("session")
        raise Reached("reached the database")
        yield  # pragma: no cover

    def marked_call(*a, **kw):
        seen.append("call")
        raise Reached("reached the handler")

    for _path, module_name, attr in SPEECH_READS.values():
        module = getattr(web_api, module_name)
        replacement = marked_session if attr == "session_scope" else marked_call
        monkeypatch.setattr(module, attr, replacement)
    return seen


@pytest.fixture
def claimed(monkeypatch):
    """An install that HAS an admin password (so the pre-setup grace no
    longer waves everything through), one admin session, one household
    token."""
    return install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )


def _client(headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    """The web app with no lifespan (no poll loop, no LISTEN task) — nothing
    here needs one, and entering it would open a database connection."""
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers=headers or {}, **kw,
    )


def _with_query(path: str, token: str) -> str:
    return f"{path}{'&' if '?' in path else '?'}device_token={token}"


# ─── Refused: nothing is read ─────────────────────────────────────────────


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_no_credential_is_refused(name, claimed, mark_reached) -> None:
    """Any host on the LAN, signed out and unpaired: 401, and the handler
    never ran — not a row, not a file."""
    path = SPEECH_READS[name][0]
    async with _client() as c:
        r = await c.get(path)
    assert r.status_code == 401, r.text
    # The detail names the header, which is what makes the dashboard open
    # its "pair this browser" prompt rather than the admin login.
    assert HEADER in r.json()["detail"]
    assert mark_reached == []


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_a_stale_household_token_is_refused(name, claimed, mark_reached) -> None:
    """A token this household never minted (or rotated away) is not a
    credential, in the header or in the query."""
    path = SPEECH_READS[name][0]
    async with _client({HEADER: "stale-token"}) as c:
        assert (await c.get(path)).status_code == 401
    async with _client() as c:
        assert (await c.get(_with_query(path, "stale-token"))).status_code == 401
    assert mark_reached == []


# ─── Allowed: a paired device, an admin, and the read half's precedent ────


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_the_household_token_reads_it(name, claimed, mark_reached) -> None:
    """The point of the tier: a paired phone or browser reads the house's
    history without the admin password."""
    path = SPEECH_READS[name][0]
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        with pytest.raises(Reached):
            await c.get(path)


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_an_admin_bearer_reads_it(name, claimed, mark_reached) -> None:
    path = SPEECH_READS[name][0]
    async with _client(bearer(ADMIN_TOKEN)) as c:
        with pytest.raises(Reached):
            await c.get(path)


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_the_dashboard_cookie_renders_it(name, claimed, mark_reached) -> None:
    """Same as every other device-tier read (Documents, Files, Videos): a
    GET that only renders carries no CSRF risk, and a browser that has just
    reloaded holds the admin cookie and nothing else until it signs in."""
    path = SPEECH_READS[name][0]
    async with _client(cookies={COOKIE: ADMIN_TOKEN}) as c:
        with pytest.raises(Reached):
            await c.get(path)


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_the_query_token_passes_as_on_every_device_read(
    name, claimed, mark_reached
) -> None:
    """``?device_token=`` is what the read half of the tier accepts for a
    browser that cannot set a header (a chat image, a clip's ``<audio>``);
    these reads take the same gate, so the same rule."""
    path = SPEECH_READS[name][0]
    async with _client() as c:
        with pytest.raises(Reached):
            await c.get(_with_query(path, DEVICE_TOKEN))


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace(name, monkeypatch, mark_reached) -> None:
    """Before anyone has claimed the admin password the surface is open on
    the LAN, exactly as every other tier is — that is what makes a first
    boot, and a throwaway test instance, usable."""
    install_fake_db(monkeypatch, admin=False)
    path = SPEECH_READS[name][0]
    async with _client() as c:
        with pytest.raises(Reached):
            await c.get(path)


@pytest.mark.asyncio
async def test_the_query_token_still_never_authorizes_a_write(claimed) -> None:
    """Gating these reads on the read half did not widen the write half:
    the chat, memory and favorite writes beside them still refuse a
    household token that arrives only in the query."""
    writes = [
        ("POST", "/api/chat/threads", {"title": "x"}),
        ("POST", "/api/people/1/memories", {"body": "milk"}),
        ("POST", "/api/people/1/favorites", {"kind": "song", "value": "x"}),
    ]
    async with _client({"X-Requested-With": "XMLHttpRequest"}) as c:
        for method, path, body in writes:
            r = await c.request(method, _with_query(path, DEVICE_TOKEN), json=body)
            assert r.status_code == 401, (method, path, r.text)


# ─── The same, against a real database ────────────────────────────────────


async def _seed_person_with_a_turn_and_a_memory() -> int:
    """One person, one spoken turn, one voice note naming them, one memory.
    Returns the person id."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        pid = (
            await conn.execute(
                text("INSERT INTO people (name) VALUES ('Mira') RETURNING id")
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO sessions (id, room_id) "
                "VALUES ('6b1f0b7e-8d0e-4a57-9d52-0c9a1d5a1a11', 'kitchen')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO conversation_log "
                "(session_id, room_id, person_id, user_text, assistant_text) "
                "VALUES ('6b1f0b7e-8d0e-4a57-9d52-0c9a1d5a1a11', 'kitchen', :pid, "
                "'remind me about the dentist', 'okay')"
            ),
            {"pid": pid},
        )
        await conn.execute(
            text("INSERT INTO voice_notes (room_id, text) VALUES ('kitchen', 'Mira: buy oats')")
        )
        await conn.execute(
            text(
                "INSERT INTO memories (person_id, body, source, status) "
                "VALUES (:pid, 'allergic to peanuts', 'manual', 'active')"
            ),
            {"pid": pid},
        )
    return int(pid)


@requires_db
@pytest.mark.asyncio
async def test_the_real_household_token_reads_the_rows_and_nothing_else_does(
    _db, db_session
) -> None:
    # db_session truncates the people / conversation / notes / memory tables
    # first; _db gives fresh auth state and a dead core hop.
    async with web_client() as setup:
        admin = await claim_admin(setup)
    token = await db_device_token()
    assert token, "setup should have minted a household token"
    pid = await _seed_person_with_a_turn_and_a_memory()

    reads = {
        f"/api/people/{pid}/conversations": "remind me about the dentist",
        f"/api/people/{pid}/memories": "allergic to peanuts",
        f"/api/people/{pid}/notes": "buy oats",
        "/api/satellites/kitchen/conversations": "remind me about the dentist",
        "/api/satellites/kitchen/notes": "buy oats",
    }
    for path, said in reads.items():
        async with _client() as anon:
            r = await anon.get(path)
            assert r.status_code == 401, (path, r.text)
            assert said not in r.text
        async with _client({HEADER: "0" * 64}) as stale:
            assert (await stale.get(path)).status_code == 401, path
        for label, client in (
            ("household token", _client({HEADER: token})),
            ("admin bearer", _client(bearer(admin))),
            ("dashboard cookie", _client(cookies={COOKIE: admin})),
            ("query token", _client()),
        ):
            async with client as c:
                url = _with_query(path, token) if label == "query token" else path
                r = await c.get(url)
                assert r.status_code == 200, (path, label, r.text)
                assert said in r.text, (path, label)


@requires_db
@pytest.mark.asyncio
async def test_a_chat_thread_is_read_back_by_the_paired_device_only(_db) -> None:
    """Round trip on the chat surface: a paired browser creates a thread,
    reads its list and its (empty) transcript back; a LAN host with no
    credential learns nothing, not even that the thread exists."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE chat_threads RESTART IDENTITY CASCADE"))
    async with web_client() as setup:
        await claim_admin(setup)
    token = await db_device_token()

    async with _client({"X-Requested-With": "domovoi-tests", HEADER: token}) as paired:
        made = await paired.post("/api/chat/threads", json={"title": "shopping for Mira"})
        assert made.status_code == 200, made.text
        tid = made.json()["id"]
        listed = await paired.get("/api/chat/threads")
        assert listed.status_code == 200
        assert [t["title"] for t in listed.json()["threads"]] == ["shopping for Mira"]
        msgs = await paired.get(f"/api/chat/threads/{tid}/messages")
        assert msgs.status_code == 200 and msgs.json() == {"messages": []}

    async with _client() as anon:
        for path in ("/api/chat/threads", f"/api/chat/threads/{tid}/messages"):
            r = await anon.get(path)
            assert r.status_code == 401, (path, r.text)
            assert "shopping for Mira" not in r.text

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE chat_threads RESTART IDENTITY CASCADE"))
