"""Friend Houses on the dashboard (F2: friending; F3: messages): Settings → Household →
Friend Houses (web/static/friend_houses.jsx) and its proxy to the core
(web/backend/api/friend_houses.py).

DB-free. Three halves:

* the page source: strings from another house are plain text (threat-model
  finding 1: no HTML, no markdown, no innerHTML), a friend file never goes
  to the console, browser storage or an address, and the section is
  registered where Settings looks for it;
* the proxy's gate: every route wears the security tier (reads included),
  401 with no credential, 501 before setup;
* the proxy's forwarding: an admin Bearer reaches the core's
  /v1/admin/friend-houses path with the body as sent, an id that isn't one
  never reaches the core, and a malformed body is refused without echoing
  what was sent (it may be a friend file).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth
from domovoi.tests.auth_testkit import bearer, install_fake_db, web_app
from domovoi.tests.route_walk import iter_route_contexts

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
ADMIN_TOKEN = "admin-session-token"
XRW = {"X-Requested-With": "XMLHttpRequest"}
SECRET_FILE = "DOMOVOI-FRIEND-FILE v1 not-a-real-one-0123456789abcdef"
HOUSE = "ab" * 32
MESSAGE = "Dinner on Sunday?\nBring the cat."


def _page() -> str:
    return (STATIC / "friend_houses.jsx").read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Source without comments, so the header's explanation can't trip a check."""
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"(^|[^:])//[^\n]*", r"\1", text)


# ─── the page source ─────────────────────────────────────────────────────


def test_strings_from_another_house_are_plain_text() -> None:
    src = _code(_page())
    for banned in ("dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML",
                   "marked.", "marked(", "chatMarkdown", "renderMarkdown", "sanitizeHtml", "document.write",
                   "linkify", "<iframe"):
        assert banned not in src, banned


def test_a_friend_file_stays_in_memory_and_post_bodies() -> None:
    src = _code(_page())
    for banned in ("localStorage", "sessionStorage", "indexedDB", "console.", "location.hash",
                   "location.search", "URLSearchParams", "history.pushState", "history.replaceState"):
        assert banned not in src, banned
    # The file text only ever travels to preview / send-request, as a body.
    assert "apiPost(`${FH_API}/preview`, { file: text })" in src
    assert "apiPost(`${FH_API}/requests`, { file: text, name: name.trim() })" in src
    # Download is built in the browser from the text, as plain text.
    assert "new Blob([String(file.text)], { type: 'text/plain' })" in src


def test_the_page_says_what_friend_houses_is() -> None:
    src = _page()
    assert "const FH_DESCRIPTION = 'Serverless messaging and friend system between households';" in src
    assert "gaming" not in src.lower()
    assert "Anyone who gets this file can ask to be your friend. Nothing happens unless you accept." in src
    assert "They won’t be able to reach this house any more." in src
    assert "Optional: call them and check they see the same code." in src


def test_the_section_is_registered_under_household() -> None:
    settings = (STATIC / "settings.jsx").read_text(encoding="utf-8")
    household = re.search(r"\{ id: 'household'.*?sections: \[([^\]]*)\]", settings, re.S).group(1)
    assert "'friend-houses'" in household
    assert "{ id: 'friend-houses', label: 'Friend Houses', optional: true," in settings
    assert "window.DomovoiSettingsSections['friend-houses'] = { Panel: FriendHousesPanel };" in _page()
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert index.index('src="friend_houses.jsx"') < index.index('src="settings.jsx"')
    assert "'/friend_houses.jsx'" in (STATIC / "sw.js").read_text(encoding="utf-8")


# ─── the proxy's gate ────────────────────────────────────────────────────

ROUTES: dict[str, tuple[str, str, dict | None]] = {
    "status": ("GET", "/api/friend-houses", None),
    "settings": ("PUT", "/api/friend-houses/settings", {"house_name": "The Alex house"}),
    "invites": ("GET", "/api/friend-houses/invites", None),
    "make": ("POST", "/api/friend-houses/invites", {"days": 7, "label": "for the Smiths"}),
    "invite": ("GET", "/api/friend-houses/invites/0123abcd", None),
    "cancel": ("DELETE", "/api/friend-houses/invites/0123abcd", None),
    "preview": ("POST", "/api/friend-houses/preview", {"file": SECRET_FILE}),
    "ask": ("POST", "/api/friend-houses/requests", {"file": SECRET_FILE, "name": "Smiths"}),
    "requests": ("GET", "/api/friend-houses/requests", None),
    "accept": ("POST", "/api/friend-houses/requests/rq0123abcd/accept", {"name": "Smiths"}),
    "decline": ("POST", "/api/friend-houses/requests/rq0123abcd/decline", None),
    "friends": ("GET", "/api/friend-houses/friends", None),
    "verified": ("POST", "/api/friend-houses/friends/fr0123abcd/verified", None),
    "rename": ("PATCH", "/api/friend-houses/friends/fr0123abcd", {"name": "Smiths"}),
    "unfriend": ("DELETE", "/api/friend-houses/friends/fr0123abcd", None),
    # F3: messages
    "alert": ("PUT", "/api/friend-houses/friends/fr0123abcd/alert", {"alert": "sender"}),
    "conversations": ("GET", "/api/friend-houses/conversations", None),
    "conversation": ("GET", f"/api/friend-houses/conversations/{HOUSE}", None),
    "mark-read": ("POST", f"/api/friend-houses/conversations/{HOUSE}/read", None),
    "export": ("GET", f"/api/friend-houses/conversations/{HOUSE}/export", None),
    "delete-conversation": ("DELETE", f"/api/friend-houses/conversations/{HOUSE}", None),
    "send": ("POST", "/api/friend-houses/messages", {"friend_id": "fr0123abcd", "text": MESSAGE}),
}
_IDS = sorted(ROUTES)


def test_every_route_wears_the_security_tier() -> None:
    seen = 0
    for r in iter_route_contexts(web_app.routes):
        if not getattr(r, "path", "").startswith("/api/friend-houses"):
            continue
        seen += 1
        deps = {d.call for d in r.dependant.dependencies}
        if r.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            assert admin_auth.require_admin_security in deps, r.path
        else:
            assert admin_auth.require_admin_security_read in deps, r.path
    assert seen == len(ROUTES)


@pytest.fixture
def forwarded(monkeypatch):
    """Fake the core hop: record (method, path, body) and answer 200."""
    from web.backend.api import friend_houses as fh

    calls: list[tuple[str, str, object]] = []

    def fake(method):
        async def call(path, body=None, timeout=10.0, headers=None):
            assert headers and headers.get("Authorization") == f"Bearer {ADMIN_TOKEN}"
            calls.append((method, path, body))
            return 200, {"ok": True}
        return call

    async def get(path, timeout=10.0, headers=None):
        calls.append(("GET", path, None))
        return 200, {"ok": True}

    async def delete(path, timeout=10.0, headers=None):
        calls.append(("DELETE", path, None))
        return 200, {"ok": True}

    monkeypatch.setattr(fh, "get_admin", get)
    monkeypatch.setattr(fh, "delete_admin", delete)
    monkeypatch.setattr(fh, "post_admin", fake("POST"))
    monkeypatch.setattr(fh, "put_admin", fake("PUT"))
    monkeypatch.setattr(fh, "patch_admin", fake("PATCH"))
    return calls


def _client(headers: dict[str, str] | None = None) -> AsyncClient:
    h = dict(XRW)
    h.update(headers or {})
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test", headers=h)


async def _send(c: AsyncClient, name: str):
    method, path, body = ROUTES[name]
    return await c.request(method, path, json=body) if body is not None else await c.request(method, path)


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_no_credential_is_401_and_nothing_is_forwarded(name, monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client() as c:
        r = await _send(c, name)
    assert r.status_code == 401, r.text
    assert forwarded == []


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_before_setup_everything_is_501(name, monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=False)
    async with _client() as c:
        r = await _send(c, name)
    assert r.status_code == 501, r.text
    assert forwarded == []


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_an_admin_bearer_reaches_the_cores_route(name, monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    method, path, body = ROUTES[name]
    async with _client(bearer(ADMIN_TOKEN)) as c:
        r = await _send(c, name)
    assert r.status_code == 200, r.text
    assert len(forwarded) == 1
    f_method, f_path, f_body = forwarded[0]
    assert f_method == method
    assert f_path.split("?")[0] == path.replace("/api/friend-houses", "/v1/admin/friend-houses")
    if method in ("PUT", "PATCH") or name in ("make", "preview", "ask", "accept", "send"):
        assert f_body == body


@pytest.mark.asyncio
async def test_an_id_that_isnt_one_never_reaches_the_core(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        r = await c.get("/api/friend-houses/invites/" + "a" * 65)
        assert r.status_code == 404
        r = await c.post("/api/friend-houses/friends/bad.id/verified")
        assert r.status_code == 404
    assert forwarded == []


@pytest.mark.asyncio
async def test_a_bad_body_is_refused_without_echoing_the_file(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        r = await c.post("/api/friend-houses/preview", json={"file": [SECRET_FILE]})
        assert r.status_code == 400
        assert SECRET_FILE not in r.text
        r = await c.post("/api/friend-houses/requests", json={"file": "x" * 20000, "name": "a"})
        assert r.status_code == 400
        assert "x" * 100 not in r.text
    assert forwarded == []


# ─── F3: messages ────────────────────────────────────────────────────────


def test_message_text_never_goes_in_an_address() -> None:
    src = _code(_page())
    # Sent only as a POST body, to the one route that takes it.
    assert "apiPost(`${FH_API}/messages`, { friend_id: conv.friend_id, text: body })" in src
    # No URL the page builds carries a message's text or body: every
    # template that names the API interpolates only ids, the house key and
    # numbers.
    for url in re.findall(r"`\$\{FH_API\}[^`]*`", src):
        for part in re.findall(r"\$\{([^}]*)\}", url):
            assert not re.search(r"\b(text|body|m\.body|last_error|name)\b", part), (url, part)
    # Conversations are addressed by the house key, checked as 64 hex.
    assert "const fhHouseOk = (h) => /^[0-9a-f]{64}$/.test(String(h || ''));" in src


def test_message_bodies_are_plain_text_with_their_line_breaks() -> None:
    src = _code(_page())
    body = re.search(r"const FhMessage = .*?\n\};", src, re.S).group(0)
    assert "whiteSpace: 'pre-wrap'" in body
    assert "{String(m.body == null ? '' : m.body)}" in body
    for banned in ("dangerouslySetInnerHTML", "innerHTML", "href=", "<a ", "fetch(", "new URL", "<img"):
        assert banned not in body, banned


def test_the_page_says_the_message_states_and_choices_plainly() -> None:
    src = _page()
    for words in ("Waiting to send", "Still trying: ", "Delivered", "Not delivered",
                  "(their clock)", "(no longer a friend)", "Load earlier",
                  "Save a copy (with proof of what was sent)",
                  "Delete every message with ${name}? This can’t be undone. Save a copy first if you might need it.",
                  "Only in the inbox", "Say who it’s from in the rooms",
                  "Read new messages aloud in the rooms",
                  "Wake words are taken out, and long messages are cut short.",
                  "quiet hours"):
        assert words in src, words
    # Least intrusive first, and the default when the server says nothing.
    order = [src.index(f"id: '{a}'") for a in ("inbox", "sender", "read")]
    assert order == sorted(order)
    assert "? friend.alert : 'inbox'" in src
    # The export is saved through a Blob, with a safe file name.
    assert "new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })" in src
    assert "`Friend Houses - ${fhSafe(name) || 'friend'} - " in src


@pytest.mark.asyncio
async def test_a_house_key_that_isnt_one_never_reaches_the_core(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        for bad in ("ab" * 31, "AB" * 32, "zz" * 32, "ab" * 33, "..%2F" + "ab" * 30):
            # 404 (not a house key), or 405 when a slash in it lands on another route.
            for method, path in (("GET", f"/api/friend-houses/conversations/{bad}"),
                                 ("POST", f"/api/friend-houses/conversations/{bad}/read"),
                                 ("GET", f"/api/friend-houses/conversations/{bad}/export"),
                                 ("DELETE", f"/api/friend-houses/conversations/{bad}")):
                assert (await c.request(method, path)).status_code in (404, 405), (method, bad)
    assert forwarded == []


@pytest.mark.asyncio
async def test_load_earlier_passes_before_and_limit_as_numbers(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        r = await c.get(f"/api/friend-houses/conversations/{HOUSE}?before=120&limit=50")
        assert r.status_code == 200
        assert (await c.get(f"/api/friend-houses/conversations/{HOUSE}?before=x")).status_code == 422
        assert (await c.get(f"/api/friend-houses/conversations/{HOUSE}?limit=500")).status_code == 422
    assert forwarded == [("GET", f"/v1/admin/friend-houses/conversations/{HOUSE}?limit=50&before=120", None)]


@pytest.mark.asyncio
async def test_a_message_is_checked_before_it_is_forwarded(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        for body in ({"friend_id": "fr0123abcd", "text": "   "},
                     {"friend_id": "fr0123abcd", "text": "x" * 2001},
                     {"friend_id": "bad.id", "text": "hi"},
                     {"friend_id": "fr0123abcd", "text": [MESSAGE]},
                     {"text": MESSAGE}):
            r = await c.post("/api/friend-houses/messages", json=body)
            assert r.status_code in (400, 404), (body, r.text)
            assert MESSAGE not in r.text and "Bring the cat" not in r.text
        r = await c.put("/api/friend-houses/friends/fr0123abcd/alert", json={"alert": "shout"})
        assert r.status_code == 400
        assert (await c.post("/api/friend-houses/messages",
                             json={"friend_id": "fr0123abcd", "text": "x" * 2000})).status_code == 200
    assert forwarded == [("POST", "/v1/admin/friend-houses/messages", {"friend_id": "fr0123abcd", "text": "x" * 2000})]
