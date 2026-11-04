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

Three questions, in the order the route asks them, cheapest first:

1. can this row be re-acquired at all (external, not in the library,
   something to fetch it by) — no round trip;
2. can anything fulfil its KIND — ``can_fulfill_url`` for a row with a
   stored url, ``can_fulfill_query`` for a title-only one, plus the
   internet switch for the url kind;
3. for a caller that is not an admin session, does a registered
   fulfiller's own ``url_matcher`` claim THIS url — the core's
   ``POST /v1/acquisitions/url-allowed``, which exists for this decision.

Step 3 is the allowlist D55 names, and the half a round-2 review found
missing: the registry-wide flag said yes while the gate a device-token
phone actually meets said no.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
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


def _phone() -> Request:
    """A caller with no credentials: the device-token phone's tier as far
    as the admin check goes (``check_admin_request`` answers anything but
    ``ok`` for it), which is the caller the url allowlist is asked for."""
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                    "query_string": b"", "client": ("127.0.0.1", 12345)})


def _allowlist(answer: bool | dict[str, bool], asked: list[list[str]]) -> Any:
    """``_urls_allowed_by_fulfillers``'s shape: url -> bool, and an
    EMPTY dict when the core did not answer. ``asked`` records each call's
    url list, so a case can show the page costs one round trip."""

    async def fake(urls: list[str]) -> dict[str, bool]:
        asked.append(list(urls))
        if isinstance(answer, dict):
            return answer
        return {u: answer for u in urls}

    return fake


def _as_admin(monkeypatch, result: str = "ok") -> None:
    """``check_admin_request``'s verdict for the caller. The real one needs
    a live Bearer session; ``cookie-only`` and ``pre-setup`` are the other
    answers a dashboard can get, and neither passes add-by-url's gate."""

    async def fake(request, session=None):  # noqa: ANN001
        return result

    monkeypatch.setattr(sat_api, "check_admin_request", fake)


async def _rows(
    monkeypatch, answer: bool | None, calls: list[str], *,
    query: Any = _UNSET,
    allow: bool | dict[str, bool] = True,
    asked: list[list[str]] | None = None,
):
    monkeypatch.setattr(
        sat_api, "_acquisition_availability", _availability(answer, calls, query=query)
    )
    monkeypatch.setattr(
        sat_api, "_urls_allowed_by_fulfillers",
        _allowlist(allow, asked if asked is not None else []),
    )
    rows = await sat_api.list_recently_played(ROOM, _phone(), limit=100)
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
        rows = await sat_api.list_recently_played(ROOM, _phone(), limit=100)
        assert len(rows) == 1
        assert rows[0].can_add is False
        assert calls == []
    finally:
        await _clean()


@requires_db
async def test_a_url_the_installed_provider_does_not_recognise_has_no_button(
    monkeypatch,
) -> None:
    """D55's own words: ``can_add`` is computed against the fulfiller
    ALLOWLIST, not against "something url-kind is installed".

    A phone holding a device token passes add-by-url only when a
    registered fulfiller's own ``url_matcher`` claims the url
    (``check_outbound_fetch``, domovoi/admin_auth.py:1963-1995, and a
    fulfiller registered without a matcher never claims anything). With a
    fulfiller installed and reachable, and the url unrecognised, the
    button used to be drawn and the press returned the core's 403 — the
    misleading control, one layer in.
    """
    await _clean()
    try:
        await _seed()
        asked: list[list[str]] = []
        rows = await _rows(monkeypatch, True, [], allow=False, asked=asked)
        assert rows["Ode to the Beelink"].can_add is False
        # It did ask — a False that comes from never asking is a different bug.
        assert asked == [["https://example.invalid/watch?v=vid-canadd"]]
    finally:
        await _clean()


@requires_db
async def test_a_recognised_url_still_gets_its_button(monkeypatch) -> None:
    """The other side of it: the allowlist is a gate, not an off switch."""
    await _clean()
    try:
        await _seed()
        rows = await _rows(
            monkeypatch, True, [],
            allow={"https://example.invalid/watch?v=vid-canadd": True},
        )
        assert rows["Ode to the Beelink"].can_add is True
    finally:
        await _clean()


@requires_db
async def test_a_core_that_cannot_answer_the_allowlist_is_not_a_yes(monkeypatch) -> None:
    """An empty answer is "can't tell" — the core is down, or too old to
    have the route — and that is not a positive answer, so the button is
    not drawn rather than drawn on a guess."""
    await _clean()
    try:
        await _seed()
        rows = await _rows(monkeypatch, True, [], allow={})
        assert rows["Ode to the Beelink"].can_add is False
    finally:
        await _clean()


@requires_db
async def test_the_allowlist_is_asked_once_for_the_page(monkeypatch) -> None:
    """One batched round trip for the whole page, and only for the rows
    that could carry a button: a title-only row is added by query and
    never goes near the allowlist, and the same url twice is one question.
    """
    await _clean()
    try:
        await _seed()
        async with session_scope() as s:
            await s.execute(
                text(
                    "INSERT INTO media_plays (room_id, source, title, video_id, url) "
                    "VALUES (:r, 'ytdlp', 'Ode to the Beelink (again)', 'vid-canadd-2', "
                    "'https://example.invalid/watch?v=vid-canadd')"
                ),
                {"r": ROOM},
            )
            await s.execute(
                text(
                    "INSERT INTO media_plays (room_id, source, title) "
                    "VALUES (:r, 'ytdlp', 'a name and nothing else')"
                ),
                {"r": ROOM},
            )
        asked: list[list[str]] = []
        rows = await _rows(monkeypatch, True, [], allow=True, asked=asked)
        assert asked == [["https://example.invalid/watch?v=vid-canadd"]]
        assert rows["Ode to the Beelink"].can_add is True
        assert rows["Ode to the Beelink (again)"].can_add is True
        assert rows["a name and nothing else"].can_add is True
    finally:
        await _clean()


@requires_db
async def test_an_admin_session_is_not_asked_the_allowlist(monkeypatch) -> None:
    """An admin session passes add-by-url outright, whatever the matchers
    say (``check_outbound_fetch`` returns on ``ok`` before it looks), so
    gating an admin's button on the allowlist would withdraw a control
    that works — the dashboard's add on a house whose fulfiller registered
    no ``url_matcher`` at all.
    """
    await _clean()
    try:
        await _seed()
        _as_admin(monkeypatch, "ok")
        asked: list[list[str]] = []
        rows = await _rows(monkeypatch, True, [], allow=False, asked=asked)
        assert rows["Ode to the Beelink"].can_add is True
        assert asked == []
    finally:
        await _clean()


@requires_db
async def test_a_cookie_only_dashboard_is_asked_like_any_other_caller(
    monkeypatch,
) -> None:
    """``cookie-only`` and ``pre-setup`` do not pass add-by-url's gate
    (it accepts ``ok`` alone, admin_auth.py:1976), so neither may skip the
    allowlist here. Reading `result != "no-auth"` as "admin" would have
    drawn the button for a browser whose press 403s.
    """
    await _clean()
    try:
        await _seed()
        for verdict in ("cookie-only", "pre-setup", "invalid", "no-auth"):
            _as_admin(monkeypatch, verdict)
            asked: list[list[str]] = []
            rows = await _rows(monkeypatch, True, [], allow=False, asked=asked)
            assert rows["Ode to the Beelink"].can_add is False, verdict
            assert asked != [], verdict
    finally:
        await _clean()


@requires_db
async def test_nothing_installed_never_asks_the_allowlist(monkeypatch) -> None:
    """The cheap check comes first: with no url-kind fulfiller registered
    there is nothing to ask, and with the internet off the add would 409
    before any matcher ran."""
    await _clean()
    try:
        await _seed()
        asked: list[list[str]] = []
        rows = await _rows(monkeypatch, False, [], allow=True, asked=asked)
        assert rows["Ode to the Beelink"].can_add is False
        assert asked == []
        with egress.override_policy("never"):
            asked = []
            rows = await _rows(monkeypatch, True, [], allow=True, asked=asked)
            assert rows["Ode to the Beelink"].can_add is False
            assert asked == []
    finally:
        await _clean()
