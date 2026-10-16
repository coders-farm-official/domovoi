"""Where the guest work (V023, ``domovoi/guest_queue.py``, the phone
shell) meets the container guard (V024, ``domovoi/container_guard/``).
The two were built side by side and merged on 2026-10-11; neither may
get in the other's way:

* while a tamper incident has the freeze on, a guest on a playing room
  still reads and edits its queue and presses the transport buttons: the
  freeze is about new code reaching the house's machines, not the music;
* no operation the freeze covers wears a guest gate, so a caller with no
  credential gets that route's own 401 there, never a guest's pass and
  never the 423 that names the incident;
* on a phone, the banner lives inside the one scroller (``.main``), so the
  shell's tab strip stays on the screen the phone really shows
  (``--app-h``), and what a page scrolls to the top (Music's queue button
  bringing up the Room queue tab) stops below the sticky banner instead of
  under it (measured in a 375x812 browser: the tab row sat under the
  banner before ``--guard-banner-h``).

DB-free: the guest fixtures of ``test_guest_queue.py`` (stub players, the
auth primitives faked, bans in memory) and a freeze that reports one open
tamper incident. The route-auth matrix walks both features' routes for
their gates; this file holds the line between them.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any, Iterator

import pytest

from domovoi import guest_queue
from domovoi.container_guard import freeze
from domovoi.main import app as core_app
from domovoi.tests.route_walk import iter_route_contexts
from domovoi.tests.test_guest_queue import (  # noqa: F401 — fixtures, used by name
    ACTIVE,
    _core,
    _fresh_throttle,
    _ids,
    claimed,
    core_db,
    fake_bans,
    no_player_starts,
    rooms,
    transport,
)
from web.backend.api import music_queue as web_queue
from web.backend.main import app as web_app

OPEN_TAMPER = [
    {"id": 99, "summary": "The web search helper is running a different image.", "opened_at": None}
]

GUEST_GATES = (
    guest_queue.require_device_or_active_room_guest,
    guest_queue.require_device_read_or_active_room_guest,
    web_queue.require_device_or_guest,
)

# The operations the freeze covers (freeze.py's docstring), one route each
# at least: the walk below must find them, or it found nothing.
KNOWN_FROZEN = {
    ("core", "/v1/plugins/install"),
    ("core", "/v1/plugins/{slug}/enable"),
    ("core", "/v1/admin/satellite/upgrade"),
    ("core", "/v1/satellite-code/manifest"),
    ("core", "/v1/admin/version/pull"),
    ("core", "/v1/admin/version/channel"),
    ("core", "/v1/admin/version/restart"),
    ("web", "/api/satellites/media/prepare"),
}


@pytest.fixture
def frozen(monkeypatch):
    async def _open_tamper(session_scope=None):
        return OPEN_TAMPER

    monkeypatch.setattr(freeze, "frozen_incidents", _open_tamper)
    yield
    freeze.forget()


def _dependency_calls(dependant: Any) -> Iterator[Any]:
    if dependant is None:
        return
    for dep in getattr(dependant, "dependencies", ()):
        if dep.call is not None:
            yield dep.call
        yield from _dependency_calls(dep)


def _routes() -> Iterator[tuple[str, str, Any, list[Any]]]:
    """``(process, path, endpoint, gates)`` for every route of both apps."""
    for process, app in (("core", core_app), ("web", web_app)):
        for rc in iter_route_contexts(app.routes):
            dependant = getattr(rc, "dependant", None)
            endpoint = getattr(rc, "endpoint", None) or getattr(dependant, "call", None)
            if endpoint is None:
                continue
            yield process, rc.path, endpoint, list(_dependency_calls(dependant))


def _consults_the_freeze(endpoint: Any) -> bool:
    try:
        src = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return False
    return "require_unfrozen" in src or "is_frozen" in src


def test_no_frozen_operation_wears_a_guest_gate() -> None:
    frozen_routes = {
        (process, path): gates
        for process, path, endpoint, gates in _routes()
        if _consults_the_freeze(endpoint)
    }
    assert KNOWN_FROZEN <= set(frozen_routes), KNOWN_FROZEN - set(frozen_routes)
    guest_gated = sorted(k for k, gates in frozen_routes.items() if set(gates) & set(GUEST_GATES))
    assert guest_gated == []


def test_no_guest_route_consults_the_freeze() -> None:
    guest_routes = [
        (process, path, endpoint)
        for process, path, endpoint, gates in _routes()
        if set(gates) & set(GUEST_GATES)
    ]
    # Both hops: the core's queue edits, read and transport row; the web's
    # queue edits and previous button.
    assert {p for p, _path, _e in guest_routes} == {"core", "web"}
    assert len(guest_routes) >= 8
    assert [(p, path) for p, path, e in guest_routes if _consults_the_freeze(e)] == []


async def test_a_guest_changes_a_playing_rooms_music_while_the_freeze_is_on(
    frozen, rooms, claimed, core_db, transport, no_player_starts
) -> None:
    assert await freeze.is_frozen()
    stub = rooms[ACTIVE]
    async with _core() as c:
        r = await c.get(f"/v1/admin/music/queue/{ACTIVE}")
        assert r.status_code == 200, r.text
        assert r.json()["guest"] is True
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/add", json={"track_ids": [7]})
        assert r.status_code == 200, r.text
        last = _ids(stub)[-1]
        r = await c.post(
            f"/v1/admin/music/queue/{ACTIVE}/move", json={"song_id": last, "to_position": 1}
        )
        assert r.status_code == 200, r.text
        r = await c.post(f"/v1/admin/music/queue/{ACTIVE}/remove", json={"song_ids": [last]})
        assert r.status_code == 200, r.text
        for action in ("pause", "resume", "skip"):
            r = await c.post(f"/v1/admin/music/{action}/{ACTIVE}")
            assert r.status_code == 200, r.text
    assert [room for _t, room in transport] == [ACTIVE] * 3
    assert not [c_ for c_ in no_player_starts if c_[0] != "dispatch"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/admin/satellite/upgrade", {"room_id": ACTIVE}),
        ("/v1/admin/version/pull", None),
        ("/v1/admin/version/restart", None),
    ],
)
async def test_a_guest_on_a_frozen_operation_gets_its_401_not_the_incident(
    frozen, rooms, claimed, path, body
) -> None:
    async with _core() as c:
        r = await c.post(path, json=body) if body is not None else await c.post(path)
    assert r.status_code == 401, r.text
    assert "incident" not in r.text


STATIC = Path(__file__).resolve().parents[2] / "web" / "static"


def test_the_banner_scrolls_with_the_page_and_never_covers_what_it_scrolls_to() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    main = re.search(r'<main className="main" ref=\{mainRef\}>(.*?)</main>', html, re.S)
    assert main and "<ContainerGuardBanner/>" in main.group(1)
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert "height: var(--app-h);" in re.search(r"^\.app \{(.*?)\}", css, re.S | re.M).group(1)
    assert "position: sticky;" in re.search(r"^\.guard-banner \{(.*?)\}", css, re.S | re.M).group(1)
    assert ".main { scroll-padding-top: var(--guard-banner-h, 0px); }" in css
    jsx = (STATIC / "components.jsx").read_text(encoding="utf-8")
    banner = jsx[jsx.index("const ContainerGuardBanner = () => {"):]
    banner = banner[: banner.index("\n};\n")]
    assert "setProperty('--guard-banner-h'" in banner
    assert "removeProperty('--guard-banner-h')" in banner
    assert "ref={bannerRef}" in banner
