"""A device an admin blocked in Settings cannot write through the
documents door either — without any client being asked to change.

The state this replaces: all five ``/api/documents`` saves took
``device_id`` as an OPTIONAL body field, no client sent it, and so a
tablet an admin had blocked kept saving by doing nothing at all. The
Files page on that same tablet showed the banner saying it could not.
``/api/files`` refused the same device, because there the field is
required — so the block was one JSON field away from meaningless, and
the weaker of two doors into one folder was setting the policy for both.

Requiring the field instead would have 422'd every Save in the dashboard
and in the Android app, which is why the previous pass left it optional
and said so. The answer taken here is the third one: the server knows
more about the caller than the body does.
:func:`web.backend.api.files.caller_device_id` reads the body, then
``X-Device-Id``, then ``?device_id=``, then the ``domovoi-device-id``
COOKIE — which the server itself sets on ``POST /api/devices/register``,
the call every browser makes on every dashboard load, carrying the very
id the blocklist is keyed on. No client change, no new field, nothing to
remember.

What is NOT closed is stated here as a test rather than as a paragraph
(:func:`test_a_caller_that_names_itself_nowhere_is_still_unidentified`):
a caller that offers no identity at all — curl, or the Android app, whose
OkHttp client keeps no cookie jar — is anonymous, and an id is
self-asserted within the household in any case. What changed is the
DEFAULT: a normal client now carries its name without being asked instead
of omitting it without being noticed.

DB-free: the one database read these handlers make (the files
device-block) is stubbed per test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import web.backend.api.files as files_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import HEADER, install_fake_db
from web.backend.api.devices import DEVICE_ID_COOKIE
from web.backend.main import app

DEVICE_TOKEN = "device-token"
BLOCKED_ID = "browser-jrzp6f361b"
BLOCK_MESSAGE = "Chrome on Windows isn't allowed to change files"


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, device_token=DEVICE_TOKEN)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "shopping.txt").write_text("milk\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def one_device_is_blocked(monkeypatch):
    """The real shape of ``_block_status``: it answers for the device it
    is asked about, and ``None`` when nobody is named."""

    async def _status(device_id):
        return BLOCK_MESSAGE if device_id == BLOCKED_ID else None

    monkeypatch.setattr(files_api, "_block_status", _status)


def _client(cookies=None, headers=None) -> TestClient:
    c = TestClient(
        app,
        headers={
            "X-Requested-With": "domovoi-tests",
            HEADER: DEVICE_TOKEN,
            **(headers or {}),
        },
    )
    for k, v in (cookies or {}).items():
        c.cookies.set(k, v)
    return c


# Every save on the documents door, each with the body an existing client
# actually sends — which is to say, one that never mentions a device.
SAVES = [
    ("put", "/api/documents/text/blocked.md", {"json": {"text": "x"}}),
    ("put", "/api/documents/sheet/blocked.csv", {"json": {"rows": [[{"v": "1"}]]}}),
    ("post", "/api/documents/create", {"json": {"name": "blocked2.md", "kind": "text"}}),
    (
        "post",
        "/api/documents/drawings/write",
        {"json": {"rel_path": "blocked.excalidraw", "content": "{}"}},
    ),
    (
        "post",
        "/api/documents/upload",
        {"files": {"files": ("blocked.txt", b"x", "text/plain")}},
    ),
]


@pytest.mark.parametrize("method,path,kwargs", SAVES)
def test_the_cookie_the_server_set_is_enough_to_shut_the_door(
    claimed, docs_dir, one_device_is_blocked, method, path, kwargs
):
    """The repro from the findings, closed: a blocked browser, a body with
    no ``device_id`` in it, and a 403 on all five saves. The identity comes
    from the cookie the server handed this browser at registration."""
    c = _client(cookies={DEVICE_ID_COOKIE: BLOCKED_ID})
    before = sorted(p.name for p in docs_dir.iterdir())
    r = getattr(c, method)(path, **kwargs)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == BLOCK_MESSAGE
    assert sorted(p.name for p in docs_dir.iterdir()) == before


@pytest.mark.parametrize(
    "where,client_kwargs",
    [
        ("cookie", {"cookies": {DEVICE_ID_COOKIE: BLOCKED_ID}}),
        ("header", {"headers": {"X-Device-Id": BLOCKED_ID}}),
    ],
)
def test_any_place_the_caller_names_itself_counts(
    claimed, docs_dir, one_device_is_blocked, where, client_kwargs
):
    """The cookie is what the browser has today; ``X-Device-Id`` is what
    makes the Android fix one line in its HTTP layer rather than an edit
    per screen. Both are consulted, so neither is load-bearing alone."""
    r = _client(**client_kwargs).put(
        "/api/documents/text/blocked.md", json={"text": "x"}
    )
    assert r.status_code == 403, f"{where}: {r.text}"


def test_the_query_string_the_files_page_already_sends_counts_too(
    claimed, docs_dir, one_device_is_blocked
):
    """``/api/files/browse`` carries ``?device_id=`` on every call from
    every client. A caller that identifies itself while reading is
    identified when it writes from the same URL shape."""
    r = _client().put(
        f"/api/documents/text/blocked.md?device_id={BLOCKED_ID}", json={"text": "x"}
    )
    assert r.status_code == 403


def test_the_body_still_wins_when_it_names_someone(
    claimed, docs_dir, one_device_is_blocked
):
    """An explicit field is the most explicit thing a caller can do and is
    taken first — the behaviour ``/api/files`` has always had."""
    r = _client(cookies={DEVICE_ID_COOKIE: "browser-someone-else"}).put(
        "/api/documents/text/blocked.md",
        json={"text": "x", "device_id": BLOCKED_ID},
    )
    assert r.status_code == 403


def test_an_unblocked_device_is_not_inconvenienced(claimed, docs_dir, one_device_is_blocked):
    """The other half of the requirement. Identifying a caller must not
    cost a caller that is allowed to write anything at all."""
    c = _client(cookies={DEVICE_ID_COOKIE: "browser-not-blocked"})
    r = c.put("/api/documents/text/shopping.txt", json={"text": "milk, bread"})
    assert r.status_code == 200, r.text
    assert (docs_dir / "shopping.txt").read_text(encoding="utf-8") == "milk, bread"


def test_a_cookie_that_is_not_a_device_id_is_ignored_rather_than_refused(
    claimed, docs_dir, one_device_is_blocked
):
    """A stale or malformed cookie from some older build means "this
    caller did not identify itself", never 400. Turning a legitimate Save
    into an error would be a worse bug than the one being fixed."""
    r = _client(cookies={DEVICE_ID_COOKIE: "!!"}).put(
        "/api/documents/text/shopping.txt", json={"text": "still fine"}
    )
    assert r.status_code == 200, r.text


def test_a_caller_that_names_itself_nowhere_is_still_unidentified(
    claimed, docs_dir, one_device_is_blocked
):
    """THE REMAINING GAP, on purpose and in the open.

    curl, and the Android app (its OkHttp client is built with no cookie
    jar), send no id anywhere, so the server has nothing to match and the
    save goes through. Closing this needs the client to say who it is —
    one header in ``android/.../net/DeviceAuth.kt``'s interceptor, which
    is why ``X-Device-Id`` is already read above.

    FLIP THIS TEST, do not delete it, when that lands: the assertion then
    becomes 403 and the behaviour becomes "a caller that will not name
    itself does not get to write", which is what ``/api/files`` already
    says in its own docstring.
    """
    r = _client().put("/api/documents/text/anonymous.md", json={"text": "x"})
    assert r.status_code == 200, r.text
    assert (docs_dir / "anonymous.md").exists()


# ═══ The library rules on the save that CREATES the library ══════════

def test_the_admin_write_hook_covers_the_very_first_save(
    claimed, monkeypatch, tmp_path
):
    """``core_library`` answers ``None`` while ``documents_dir`` does not
    exist, and the save that creates the folder is exactly the save that
    hit that window — so on a fresh install the first save skipped
    ``_assert_admin_for`` and ``lib.editable`` altogether.

    Nothing was open in practice (the hook is empty and core:documents is
    editable). It mattered because this function is what the branch offers
    as proof that the documents door FOLLOWS the hook if the policy ever
    changes, and the existing proof could not see the hole: it runs with
    the folder already present.
    """

    async def _not_blocked(device_id):
        return None

    monkeypatch.setattr(files_api, "_block_status", _not_blocked)
    missing = tmp_path / "no-documents-here"
    monkeypatch.setattr(settings, "documents_dir", str(missing))
    monkeypatch.setattr(
        files_api, "ADMIN_WRITE_LIBRARY_IDS", frozenset({"core:documents"})
    )

    r = _client().put("/api/documents/text/first.md", json={"text": "x"})
    assert r.status_code == 401, r.text
    assert not missing.exists(), "the refused save created the library anyway"


def test_and_the_first_save_still_works_when_nothing_is_pinned(
    claimed, monkeypatch, tmp_path
):
    """The reason the ``None`` branch returns rather than denying: a
    headless install has no ``~/Documents`` until something writes one,
    and refusing that save would leave the library permanently
    unreachable. That has to keep being true."""

    async def _not_blocked(device_id):
        return None

    monkeypatch.setattr(files_api, "_block_status", _not_blocked)
    missing = tmp_path / "no-documents-here"
    monkeypatch.setattr(settings, "documents_dir", str(missing))

    r = _client().put("/api/documents/text/first.md", json={"text": "x"})
    assert r.status_code == 200, r.text
    assert (missing / "first.md").read_text(encoding="utf-8") == "x"


# ═══ Where the cookie comes from ═════════════════════════════════════════

def test_registering_a_device_hands_the_browser_its_own_id_back():
    """The whole mechanism in one assertion: ``POST /api/devices/register``
    — which ``web/static/index.html`` calls in ``bootstrap()`` on every
    dashboard load — replies with a cookie carrying the id the client just
    minted, and the browser then repeats it on every request it makes,
    including the five documents saves whose body has no field for it.

    ``httponly`` because no page script needs to read it (the id is already
    in localStorage, where the client minted it). ``samesite=lax`` rather
    than the admin session cookie's ``strict``: this value can only ever
    NARROW what a caller may do, so the failure to avoid is the cookie NOT
    arriving. No ``secure``, for the same reason the session cookie has
    none — v1 runs over plain LAN HTTP and a Secure cookie would simply
    never be sent.
    """
    from starlette.responses import Response

    from web.backend.api.devices import _remember_device

    r = Response()
    _remember_device(r, "browser-jrzp6f361b")
    header = r.headers["set-cookie"]
    assert header.startswith(f"{DEVICE_ID_COOKIE}=browser-jrzp6f361b")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert "Secure" not in header
