"""WEB-5 — the web routes that destroy something answer to an admin
session: a library track (and its file), a person, a voice profile, a
denylist entry, a recorded wake-word clip.

DB-free. The refusal is proved twice over: the status code, and the fact
that the handler never opened a database session or touched the disk — so
the row and the file are still there afterwards.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.tests.auth_testkit import COOKIE, install_fake_db, web_app
from web.backend.api import denylist as denylist_api
from web.backend.api import music as music_api
from web.backend.api import people as people_api
from web.backend.api import wake_words as wake_words_api

BROWSER = {"X-Requested-With": "XMLHttpRequest"}

# Every destructive web route this item covers, with the query the
# dashboard actually sends for the dangerous variant.
DESTRUCTIVE = (
    ("/api/music/library/7?also_file=true", "a library track and its file"),
    ("/api/music/library/7", "a library track"),
    ("/api/people/3", "a person and their voice profiles"),
    ("/api/people/3/profiles/5", "one voice profile"),
    ("/api/denylist/2", "a denylist entry"),
    ("/api/wake-words/1/clips/take-1.wav", "a recorded wake-word clip"),
)

TOUCHING_MODULES = (music_api, people_api, denylist_api, wake_words_api)


@pytest.fixture
def no_side_effects(monkeypatch):
    """Fail loudly if a refused delete still reaches the database or the
    filesystem. Returns nothing — its value is that it never fires."""

    @asynccontextmanager
    async def forbidden_session():
        raise AssertionError("a refused delete opened a database session")
        yield  # pragma: no cover

    for module in TOUCHING_MODULES:
        if hasattr(module, "session_scope"):
            monkeypatch.setattr(module, "session_scope", forbidden_session)

    def forbidden_unlink(self, *a, **kw):
        raise AssertionError(f"a refused delete unlinked {self}")

    monkeypatch.setattr(Path, "unlink", forbidden_unlink)
    return None


def _client(**kw) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test", headers=BROWSER, **kw
    )


@pytest.mark.parametrize(("path", "what"), DESTRUCTIVE, ids=[p for p, _ in DESTRUCTIVE])
@pytest.mark.asyncio
async def test_deleting_needs_an_admin_session(path, what, monkeypatch, no_side_effects) -> None:
    """Without an admin session the delete is refused 401 and nothing is
    removed: {what} is still there."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    async with _client() as c:
        assert (await c.delete(path)).status_code == 401


@pytest.mark.parametrize(("path", "what"), DESTRUCTIVE, ids=[p for p, _ in DESTRUCTIVE])
@pytest.mark.asyncio
async def test_the_dashboard_cookie_alone_cannot_delete(
    path, what, monkeypatch, no_side_effects
) -> None:
    """A cookie renders GET state and nothing more: the delete is 403."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    async with _client(cookies={COOKIE: "admin-token"}) as c:
        assert (await c.delete(path)).status_code == 403


@pytest.mark.parametrize(("path", "what"), DESTRUCTIVE, ids=[p for p, _ in DESTRUCTIVE])
@pytest.mark.asyncio
async def test_a_fresh_install_keeps_its_grace(path, what, monkeypatch) -> None:
    """Before anyone has claimed the admin password these still work off
    the LAN grace (the same rule the rest of the gated surface follows) —
    proved by the request reaching the handler's database call."""
    install_fake_db(monkeypatch, admin=False)
    reached: list[str] = []

    @asynccontextmanager
    async def marker_session():
        reached.append(path)
        raise RuntimeError("stop here — the gate let it through")
        yield  # pragma: no cover

    for module in TOUCHING_MODULES:
        if hasattr(module, "session_scope"):
            monkeypatch.setattr(module, "session_scope", marker_session)

    async with _client() as c:
        with pytest.raises(RuntimeError):
            await c.delete(path)
    assert reached == [path]
