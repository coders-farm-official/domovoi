"""``can_add`` on a room's play history asks whether anything can fulfil it.

D55 (owner decision 2026-11-03): "no fulfiller means no button and no
error: the server computes ``can_add`` against the fulfiller allowlist, so
both phones draw it honestly."

It used to be a property of the row alone — external, not in the library,
something to fetch it by — so on a house with no provider plugin installed
every phone drew "+ add to library" on every external play, and the only
thing pressing it could produce was the acquisition queue's
graceful-absence sentence. A control that cannot do what it says is not
drawn (Kamron's no-misleading-controls rule).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from domovoi import egress
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from web.backend.api import satellites as sat_api

ROOM = "canadd_room"

# "this case did not say", so the url flag's value is used for both.
_UNSET: Any = object()


async def _clean() -> None:
    async with session_scope() as s:
        await s.execute(text("DELETE FROM media_plays WHERE room_id = :r"), {"r": ROOM})


async def _seed() -> None:
    """One external play worth re-acquiring, one library play that never is."""
    async with session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO media_plays (room_id, source, title, video_id, url) "
                "VALUES (:r, 'ytdlp', 'Ode to the Beelink', 'vid-canadd', "
                "'https://example.invalid/watch?v=vid-canadd')"
            ),
            {"r": ROOM},
        )
        await s.execute(
            text(
                "INSERT INTO media_plays (room_id, source, title) "
                "VALUES (:r, 'library', 'something already here')"
            ),
            {"r": ROOM},
        )


def _availability(
    answer: bool | None, calls: list[str], *, query: Any = _UNSET
) -> Any:
    """``_availability``'s shape (web/backend/api/acquisitions.py): ``None``
    for both flags is "the core did not answer".

    ``query`` is settable on its own so a case can tell the two flags
    apart. They used to be given the same value in every case, which meant
    a route reading the wrong one would have passed everything (review-1
    note 4).
    """

    async def fake() -> dict[str, Any]:
        calls.append("asked")
        return {
            "fulfillers": ["ytdlp"] if answer else [],
            "can_fulfill_query": answer if query is _UNSET else query,
            "can_fulfill_url": answer,
            "core_reachable": answer is not None,
        }

    return fake


async def _rows(monkeypatch, answer: bool | None, calls: list[str], *, query: Any = _UNSET):
    monkeypatch.setattr(
        sat_api, "_acquisition_availability", _availability(answer, calls, query=query)
    )
    rows = await sat_api.list_recently_played(ROOM, limit=100)
    return {r.title: r for r in rows}


@requires_db
async def test_a_fulfiller_is_installed_so_the_row_may_be_added(monkeypatch) -> None:
    await _clean()
    try:
        await _seed()
        calls: list[str] = []
        rows = await _rows(monkeypatch, True, calls)
        assert rows["Ode to the Beelink"].can_add is True
        # A library play is already here: never a candidate, whatever is installed.
        assert rows["something already here"].can_add is False
        assert calls == ["asked"]
    finally:
        await _clean()


@requires_db
async def test_no_fulfiller_means_no_button(monkeypatch) -> None:
    """The bug: this row carried ``can_add=True`` on a house with nothing
    installed to act on the queue."""
    await _clean()
    try:
        await _seed()
        rows = await _rows(monkeypatch, False, [])
        assert rows["Ode to the Beelink"].can_add is False
        assert rows["something already here"].can_add is False
    finally:
        await _clean()


@requires_db
async def test_a_core_that_did_not_answer_is_not_a_yes(monkeypatch) -> None:
    """``None`` is "can't tell right now", which is not a positive answer:
    no button, and no error for anyone to read either."""
    await _clean()
    try:
        await _seed()
        rows = await _rows(monkeypatch, None, [])
        assert rows["Ode to the Beelink"].can_add is False
    finally:
        await _clean()


@requires_db
async def test_the_core_is_not_asked_when_no_row_could_carry_the_button(monkeypatch) -> None:
    """A history of library plays only costs no round trip to the core."""
    await _clean()
    try:
        async with session_scope() as s:
            await s.execute(
                text(
                    "INSERT INTO media_plays (room_id, source, title) "
                    "VALUES (:r, 'library', 'something already here')"
                ),
                {"r": ROOM},
            )
        calls: list[str] = []
        rows = await _rows(monkeypatch, True, calls)
        assert rows["something already here"].can_add is False
        assert calls == []
    finally:
        await _clean()


@requires_db
async def test_a_url_row_asks_the_url_flag_and_not_the_query_one(monkeypatch) -> None:
    """Which flag decides is per row. This row has a stored URL, so the add
    is add-by-url and ``can_fulfill_query`` is the wrong question: a house
    whose installed fulfiller takes free-text queries and not URLs can do
    nothing with it.

    The two flags were given the same value in every case before, so a
    route reading the wrong one passed them all (review-1 note 4).
    """
    await _clean()
    try:
        await _seed()
        rows = await _rows(monkeypatch, False, [], query=True)
        assert rows["Ode to the Beelink"].can_add is False
    finally:
        await _clean()


@requires_db
async def test_a_title_only_row_asks_the_query_flag(monkeypatch) -> None:
    """And the other way round: a play with no stored URL is added by
    QUERY — the dashboard's own fork (web/static/satellites.jsx:1752-1764)
    — so a url-only fulfiller is no use to it and a query one is. Asking
    ``can_fulfill_url`` for every row would have withdrawn the dashboard's
    title-only add on a house with a query fulfiller installed.
    """
    await _clean()
    try:
        async with session_scope() as s:
            await s.execute(
                text(
                    "INSERT INTO media_plays (room_id, source, title) "
                    "VALUES (:r, 'ytdlp', 'a name and nothing else')"
                ),
                {"r": ROOM},
            )
        rows = await _rows(monkeypatch, False, [], query=True)
        assert rows["a name and nothing else"].can_add is True
        rows = await _rows(monkeypatch, True, [], query=False)
        assert rows["a name and nothing else"].can_add is False
    finally:
        await _clean()


@requires_db
async def test_the_internet_being_off_takes_the_url_button_away(monkeypatch) -> None:
    """Under the "never" answer add-by-url refuses with 409 before it ever
    reaches the queue (domovoi/main.py:5790-5791), so every press of this
    button would fail — the misleading control again, with a fulfiller
    installed and reachable. add-by-query is NOT refused there (the row
    waits and a fulfiller drains it later), so the switch only takes the
    URL row's button.
    """
    await _clean()
    try:
        await _seed()
        async with session_scope() as s:
            await s.execute(
                text(
                    "INSERT INTO media_plays (room_id, source, title) "
                    "VALUES (:r, 'ytdlp', 'a name and nothing else')"
                ),
                {"r": ROOM},
            )
        with egress.override_policy("never"):
            rows = await _rows(monkeypatch, True, [])
            assert rows["Ode to the Beelink"].can_add is False
            assert rows["a name and nothing else"].can_add is True
        # The same read with the answer back to default: the URL row returns.
        with egress.override_policy("sometimes"):
            rows = await _rows(monkeypatch, True, [])
            assert rows["Ode to the Beelink"].can_add is True
    finally:
        await _clean()


@requires_db
async def test_an_external_play_with_no_title_and_no_url_is_still_no_candidate(monkeypatch) -> None:
    """The row's own half of the condition is unchanged: there is nothing
    to look this play up by."""
    await _clean()
    try:
        async with session_scope() as s:
            await s.execute(
                text("INSERT INTO media_plays (room_id, source) VALUES (:r, 'ytdlp')"),
                {"r": ROOM},
            )
        calls: list[str] = []
        monkeypatch.setattr(sat_api, "_acquisition_availability", _availability(True, calls))
        rows = await sat_api.list_recently_played(ROOM, limit=100)
        assert len(rows) == 1
        assert rows[0].can_add is False
        assert calls == []
    finally:
        await _clean()
