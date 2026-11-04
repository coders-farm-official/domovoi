"""The core route behind an honest "+ add to library", and the web hop
that reads it: ``POST /v1/acquisitions/url-allowed``.

D55 (owner decision 2026-11-03) needs a client to know, before anyone
presses anything, whether a registered fulfiller's own ``url_matcher``
claims a row's url — the gate a caller that is not an admin session meets
at add-by-url (``check_outbound_fetch``). That answer is live state inside
the core process's capability registry, so the web process asks for it over
this route and gates ``can_add`` on the reply
(``web/backend/api/satellites.py``).

This file is the route's own test, and — the half a review found missing —
the test of the CONTRACT between the two halves. ``test_recently_played_can_add``
stubs ``_urls_allowed_by_fulfillers`` out in every case, so nothing there
compares the request path, the request key ``urls`` or the response key
``allowed`` against the route that serves them: renaming the key left 599
server cases green while every device-token phone lost its button on every
row. The cases below drive the real web helper against the real core route
over a real httpx client (ASGI transport instead of a socket), so a rename
on either side fails here.

DB-free but for the last case: the handler touches no table and the device
gate runs against the faked auth primitives (``install_fake_db``), the way
``test_core_route_tiers``' structural half does. The last case is the whole
feature — rows out of Postgres, the page's ``can_add`` decided by a real
matcher through the real route — so it takes ``requires_db``.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.acquisitions import ACQUISITIONS
from domovoi.db.session import session_scope
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, make_request
from domovoi.tests.conftest import requires_db
from web.backend import domovoi_client
from web.backend.api import satellites as sat_api

PATH = "/v1/acquisitions/url-allowed"
TOKEN = "device-token"
MINE = "https://provider.invalid/watch?v=abc"
THEIRS = "https://somewhere-else.invalid/watch?v=abc"
ROOM = "canadd_seam_room"


@pytest.fixture
def fulfiller():
    """A registered url-kind fulfiller whose matcher claims ONE host — the
    allowlist shape a provider plugin registers. The factory lets a case
    pick the matcher (none, raising, thread-recording)."""
    installed: list[str] = []

    def register(matcher: Any = None, *, kinds: set[str] | None = None, slug: str = "tests-provider") -> None:
        ACQUISITIONS.register_fulfiller(
            slug, kinds=kinds or {"url"}, url_matcher=matcher
        )
        installed.append(slug)

    yield register
    for slug in installed:
        ACQUISITIONS.unregister_fulfiller(slug)


def _claims_provider_invalid(url: str) -> bool:
    return "provider.invalid" in url


def core_client(**kw) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=core_app), base_url="http://test", **kw
    )


def _device(monkeypatch) -> None:
    """A household caller: setup is done (so the pre-setup LAN grace is
    not what lets anything through) and ``TOKEN`` is the live token."""
    install_fake_db(monkeypatch, admin=True, device_token=TOKEN)


# ─── The route's own answer ───────────────────────────────────────────────


async def test_each_url_gets_the_matchers_verdict(monkeypatch, fulfiller) -> None:
    """The shape the web helper reads: ``{"allowed": {url: bool}}``, one
    entry per url asked about, the matcher's own verdict in each."""
    _device(monkeypatch)
    fulfiller(_claims_provider_invalid)
    async with core_client() as c:
        r = await c.post(PATH, json={"urls": [MINE, THEIRS]}, headers={HEADER: TOKEN})
    assert r.status_code == 200, r.text
    assert r.json() == {"allowed": {MINE: True, THEIRS: False}}


async def test_with_nothing_installed_no_url_is_allowed(monkeypatch) -> None:
    _device(monkeypatch)
    async with core_client() as c:
        r = await c.post(PATH, json={"urls": [MINE]}, headers={HEADER: TOKEN})
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] == {MINE: False}


async def test_a_fulfiller_with_no_matcher_never_allowlists(monkeypatch, fulfiller) -> None:
    """``url_allowed_by_fulfillers``' conservative half: a provider that
    registered no matcher claims nothing, which is why ``can_fulfill_url``
    (something url-kind is installed) is the wrong question for a
    device-token phone."""
    _device(monkeypatch)
    fulfiller(None)
    async with core_client() as c:
        r = await c.post(PATH, json={"urls": [MINE]}, headers={HEADER: TOKEN})
    assert r.json()["allowed"] == {MINE: False}


async def test_a_matcher_that_raises_is_a_no_and_not_a_500(monkeypatch, fulfiller) -> None:
    """Plugin code, so the route cannot assume it works. The caller still
    gets an answer: ``false``, the same as a url nothing recognizes."""
    _device(monkeypatch)

    def boom(url: str) -> bool:
        raise RuntimeError("matcher bug")

    fulfiller(boom)
    async with core_client() as c:
        r = await c.post(PATH, json={"urls": [MINE]}, headers={HEADER: TOKEN})
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] == {MINE: False}


async def test_repeats_collapse_and_an_empty_page_asks_nothing(monkeypatch, fulfiller) -> None:
    """Keyed by url, so the same play twice on a page is one answer; and an
    empty list is a legal (if pointless) question, not a 422."""
    _device(monkeypatch)
    seen: list[str] = []

    def counting(url: str) -> bool:
        seen.append(url)
        return _claims_provider_invalid(url)

    fulfiller(counting)
    async with core_client() as c:
        r = await c.post(PATH, json={"urls": [MINE, MINE]}, headers={HEADER: TOKEN})
        empty = await c.post(PATH, json={"urls": []}, headers={HEADER: TOKEN})
    assert r.json()["allowed"] == {MINE: True}
    assert empty.status_code == 200
    assert empty.json() == {"allowed": {}}
    # The page's own cap is what bounds the work; a repeated url is not
    # free at the matcher, only in the answer. Worth knowing if it ever is.
    assert seen == [MINE, MINE]


async def test_the_cap_is_the_recently_played_pages_own_limit(monkeypatch, fulfiller) -> None:
    """500 is that route's ``limit`` ceiling, so the one caller never has
    to ask twice — and 501 is a 422 rather than an unbounded run of plugin
    code."""
    _device(monkeypatch)
    fulfiller(_claims_provider_invalid)
    async with core_client(timeout=30.0) as c:
        ok = await c.post(
            PATH, json={"urls": [f"{THEIRS}{i}" for i in range(500)]},
            headers={HEADER: TOKEN},
        )
        too_many = await c.post(
            PATH, json={"urls": [f"{THEIRS}{i}" for i in range(501)]},
            headers={HEADER: TOKEN},
        )
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["allowed"]) == 500
    assert too_many.status_code == 422


async def test_the_matchers_run_off_the_event_loop(monkeypatch, fulfiller) -> None:
    """A url_matcher is plugin code the core cannot time out, so the batch
    runs in a worker thread: a backtracking regex over a page of urls
    costs one thread and not the loop (voice, satellites, timers).

    Pinned by the thread the matcher sees, not by a stopwatch."""
    _device(monkeypatch)
    ran_on: list[int] = []

    def recording(url: str) -> bool:
        ran_on.append(threading.get_ident())
        return False

    fulfiller(recording)
    async with core_client() as c:
        r = await c.post(PATH, json={"urls": [MINE, THEIRS]}, headers={HEADER: TOKEN})
    assert r.status_code == 200, r.text
    assert len(ran_on) == 2
    loop_thread = threading.get_ident()
    assert all(t != loop_thread for t in ran_on), "a matcher ran on the event loop"


# ─── The gate ─────────────────────────────────────────────────────────────


async def test_the_route_is_on_the_device_tier(monkeypatch, fulfiller) -> None:
    """It runs third-party code, so it is not open to the LAN: the
    household token or an admin Bearer, like the other ordinary household
    actions. ``admin=True`` in the fake means setup is done, so the
    pre-setup grace is not what answers here."""
    _device(monkeypatch)
    fulfiller(_claims_provider_invalid)
    body = {"urls": [MINE]}
    async with core_client() as c:
        nothing = await c.post(PATH, json=body)
        stale = await c.post(PATH, json=body, headers={HEADER: "not-the-token"})
        household = await c.post(PATH, json=body, headers={HEADER: TOKEN})
    assert nothing.status_code == 401, nothing.text
    assert stale.status_code == 401, stale.text
    assert household.status_code == 200, household.text


async def test_an_admin_bearer_passes_too(monkeypatch, fulfiller) -> None:
    """The dashboard's own session: ``require_device`` takes either."""
    install_fake_db(monkeypatch, admin=True, sessions={"sess"}, device_token=TOKEN)
    fulfiller(_claims_provider_invalid)
    async with core_client() as c:
        r = await c.post(PATH, json={"urls": [MINE]}, headers=bearer("sess"))
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] == {MINE: True}


# ─── The contract between the web helper and the route ───────────────────


def _route_httpx_to_core(monkeypatch) -> None:
    """Every ``httpx.AsyncClient`` the web helper opens speaks to the real
    core app instead of a socket. The URL it builds is used as it builds
    it — the ASGI transport keeps the path — so a renamed route 404s here
    exactly as it would in the house."""
    import httpx

    real = httpx.AsyncClient

    def factory(*a: Any, **kw: Any) -> AsyncClient:
        kw.pop("transport", None)
        return real(*a, transport=ASGITransport(app=core_app), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _web_holds_the_household_token(monkeypatch) -> None:
    """The web process's own credential for a hop it makes on its own
    behalf. Patching the file reader keeps ``service_auth_headers`` itself
    — the thing under test is that the helper presents it."""
    monkeypatch.setattr(admin_auth, "read_device_token_file", lambda: TOKEN)
    monkeypatch.setattr(domovoi_client, "_service_token", None)


async def test_the_web_helper_and_the_route_agree_on_the_path_and_the_keys(
    monkeypatch, fulfiller
) -> None:
    """The contract, end to end over the real client: the path the helper
    POSTs to, the request key ``urls`` it sends, and the response key
    ``allowed`` it reads.

    This is the case that was missing. Renaming the route's answer key
    (``domovoi/main.py``) or its path, or the key the helper sends, leaves
    every other server case green and silently takes the "+ add" button
    away from every non-admin caller on every row.
    """
    _device(monkeypatch)
    _web_holds_the_household_token(monkeypatch)
    fulfiller(_claims_provider_invalid)
    _route_httpx_to_core(monkeypatch)

    answer = await sat_api._urls_allowed_by_fulfillers([MINE, THEIRS])

    assert answer == {MINE: True, THEIRS: False}


async def test_the_helper_presents_the_household_credential(
    monkeypatch, fulfiller
) -> None:
    """The route is device tier, so a hop with no credential is refused —
    and the refusal reads as "can't tell", not as a yes.

    The two halves: with the token the answer comes back; with the web
    process holding none (fresh install, DB hiccup — ``service_auth_headers``
    returns ``{}``) the core answers 401 and the helper's best-effort
    ``except`` leaves the dict empty, so no button is drawn.
    """
    _device(monkeypatch)
    fulfiller(_claims_provider_invalid)
    _route_httpx_to_core(monkeypatch)

    _web_holds_the_household_token(monkeypatch)
    assert await sat_api._urls_allowed_by_fulfillers([MINE]) == {MINE: True}

    async def no_credential() -> dict[str, str]:
        return {}

    monkeypatch.setattr(domovoi_client, "service_auth_headers", no_credential)
    assert await sat_api._urls_allowed_by_fulfillers([MINE]) == {}


async def test_a_core_too_old_for_the_route_is_not_a_yes(
    monkeypatch, fulfiller
) -> None:
    """The 404 a house gets between upgrading the web and restarting the
    core: an empty dict, which the recently-played route reads as "no
    button", never as an allowed url."""
    _device(monkeypatch)
    _web_holds_the_household_token(monkeypatch)
    fulfiller(_claims_provider_invalid)
    _route_httpx_to_core(monkeypatch)
    # The route one path-segment away from where it is: what a core
    # without it answers.
    monkeypatch.setattr(
        domovoi_client, "domovoi_url", lambda: "http://localhost:6370/v0"
    )
    assert await sat_api._urls_allowed_by_fulfillers([MINE]) == {}


# ─── The whole feature, with nothing between the halves stubbed ──────────


@requires_db
async def test_the_pages_button_is_decided_by_the_real_matcher(
    monkeypatch, fulfiller
) -> None:
    """Rows out of Postgres, ``can_fulfill_url`` out of the core's own
    ``GET /v1/acquisitions``, and ``can_add`` out of a real
    ``url_matcher`` — asked over the real route by the real helper. The
    provider claims one of the two plays; only that row gets a button.

    Every case in ``test_recently_played_can_add`` replaces the helper
    with a stub, which is the right shape for testing the ROUTE's own
    logic and the wrong one for testing that the two halves fit. This one
    fits them together.
    """
    async def clean() -> None:
        async with session_scope() as s:
            await s.execute(
                text("DELETE FROM media_plays WHERE room_id = :r"), {"r": ROOM}
            )

    await clean()
    try:
        async with session_scope() as s:
            await s.execute(
                text(
                    "INSERT INTO media_plays (room_id, source, title, video_id, url) "
                    "VALUES (:r, 'ytdlp', 'the provider knows this one', 'vid-mine', :u)"
                ),
                {"r": ROOM, "u": MINE},
            )
            await s.execute(
                text(
                    "INSERT INTO media_plays (room_id, source, title, video_id, url) "
                    "VALUES (:r, 'ytdlp', 'nothing installed knows this one', "
                    "'vid-theirs', :u)"
                ),
                {"r": ROOM, "u": THEIRS},
            )
        _device(monkeypatch)
        _web_holds_the_household_token(monkeypatch)
        fulfiller(_claims_provider_invalid)
        _route_httpx_to_core(monkeypatch)

        rows = {
            r.title: r
            for r in await sat_api.list_recently_played(
                ROOM, make_request(), limit=100
            )
        }

        assert rows["the provider knows this one"].can_add is True
        assert rows["nothing installed knows this one"].can_add is False
    finally:
        await clean()
