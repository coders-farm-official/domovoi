"""FE-3 — the video satellite's kiosk surface stays open, and the security
doc says so in as many words.

Kamron's call: the kiosk renders unattended with nobody to hold a
credential, so its page, its now-playing read and its pause / resume verbs
remain daily tier. What this pins is that the documentation and the code
agree — if either the gate or the paragraph moves, this fails and the other
one has to move with it.

DB-free (route table + a file read).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domovoi.tests.route_walk import iter_route_contexts

from domovoi import admin_auth
from domovoi.tests.auth_testkit import web_app

REPO_ROOT = Path(__file__).resolve().parents[2]
SECURITY_DOC = REPO_ROOT / "docs" / "SECURITY_PRIVACY.md"

KIOSK_READ = "/api/music/now-playing"
# All four, because all four are on the screen: display.jsx renders a
# transport row of play/pause, skip and stop.
KIOSK_VERBS = (
    ("POST", "/api/music/pause/{room_id}"),
    ("POST", "/api/music/resume/{room_id}"),
    ("POST", "/api/music/stop/{room_id}"),
    ("POST", "/api/music/skip/{room_id}"),
)

GATES = (
    admin_auth.require_admin_mutation,
    admin_auth.require_admin_security,
    admin_auth.require_device,
    admin_auth.require_admin_read,
)


def _dependency_calls(dependant):
    if dependant is None:
        return
    for dep in getattr(dependant, "dependencies", ()):
        if dep.call is not None:
            yield dep.call
        yield from _dependency_calls(dep)


def _route(method: str, path: str):
    for rc in iter_route_contexts(web_app.routes):
        if getattr(rc, "path", None) == path and method in (
            getattr(rc, "methods", None) or set()
        ):
            return rc
    raise AssertionError(f"{method} {path} is missing from the web app")


@pytest.mark.parametrize(("method", "path"), KIOSK_VERBS)
def test_the_kiosk_verbs_are_still_open(method, path) -> None:
    calls = list(_dependency_calls(_route(method, path).dependant))
    assert not [c for c in calls if c in GATES], (
        f"{method} {path} grew an auth gate — that is a product decision "
        f"(FE-3 kept it open); update docs/SECURITY_PRIVACY.md's kiosk "
        f"section and this test together"
    )


def test_the_now_playing_read_is_still_open() -> None:
    calls = list(_dependency_calls(_route("GET", KIOSK_READ).dependant))
    assert not [c for c in calls if c in GATES]


def test_the_security_doc_names_the_open_kiosk_surface() -> None:
    doc = SECURITY_DOC.read_text(encoding="utf-8")
    kiosk = doc[doc.index("The video satellite's kiosk page"):doc.index("**Device identity is self-asserted")]
    assert "`GET /display.html?room=<room_id>`" in kiosk
    assert f"`GET {KIOSK_READ}`" in kiosk
    for _method, path in KIOSK_VERBS:
        assert f"`POST {path}`" in kiosk, path
    # And that it says what they give away, not just that they exist.
    assert "every" in kiosk.lower() and "pause" in kiosk.lower()


def test_the_doc_records_what_the_kiosk_lost_instead() -> None:
    """The verbs stayed open; the live push did not (WEB-9). A reader has
    to be told which is which."""
    doc = SECURITY_DOC.read_text(encoding="utf-8")
    kiosk = doc[doc.index("The video satellite's kiosk page"):doc.index("**Device identity is self-asserted")]
    assert "/ws/state" in kiosk
    assert "X-Requested-With" in kiosk
