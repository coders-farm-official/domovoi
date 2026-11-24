"""Friend Houses on the dashboard (F2: friending; F3: messages; F4: shared lists): Settings → Household →
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
SPACE = "cd" * 16
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
    assert "const FH_DESCRIPTION = 'Message and share lists with friends’ houses, house to house, with no company server in between';" in src
    # Said once: the section subtitle, not again in the page's head.
    assert "{FH_DESCRIPTION}.</span>" not in src
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
    "notice": ("DELETE", "/api/friend-houses/notice", None),
    "invites": ("GET", "/api/friend-houses/invites", None),
    "make": ("POST", "/api/friend-houses/invites", {"days": 7, "label": "for the Smiths"}),
    "invite": ("GET", "/api/friend-houses/invites/0123abcd", None),
    "cancel": ("DELETE", "/api/friend-houses/invites/0123abcd", None),
    "preview": ("POST", "/api/friend-houses/preview", {"file": SECRET_FILE}),
    "ask": ("POST", "/api/friend-houses/requests", {"file": SECRET_FILE, "name": "Smiths"}),
    "requests": ("GET", "/api/friend-houses/requests", None),
    "accept": ("POST", "/api/friend-houses/requests/rq0123abcd/accept", {"name": "Smiths"}),
    "decline": ("POST", "/api/friend-houses/requests/rq0123abcd/decline", None),
    "withdraw": ("DELETE", "/api/friend-houses/requests/rq0123abcd", None),
    "friends": ("GET", "/api/friend-houses/friends", None),
    "verified": ("POST", "/api/friend-houses/friends/fr0123abcd/verified", None),
    "rename": ("PATCH", "/api/friend-houses/friends/fr0123abcd", {"name": "Smiths"}),
    "unfriend": ("DELETE", "/api/friend-houses/friends/fr0123abcd", None),
    # F3: messages
    "alert": ("PUT", "/api/friend-houses/friends/fr0123abcd/alert", {"alert": "sender"}),
    "conversations": ("GET", "/api/friend-houses/conversations", None),
    "unread": ("GET", "/api/friend-houses/unread", None),
    "conversation": ("GET", f"/api/friend-houses/conversations/{HOUSE}", None),
    "mark-read": ("POST", f"/api/friend-houses/conversations/{HOUSE}/read", None),
    "export": ("GET", f"/api/friend-houses/conversations/{HOUSE}/export", None),
    "delete-conversation": ("DELETE", f"/api/friend-houses/conversations/{HOUSE}", None),
    "send": ("POST", "/api/friend-houses/messages", {"friend_id": "fr0123abcd", "text": MESSAGE}),
    # F4: shared lists
    "shared-lists": ("GET", "/api/friend-houses/shared-lists", None),
    "share-list": ("POST", "/api/friend-houses/shared-lists", {"list_id": 7, "friend_id": "fr0123abcd"}),
    "accept-list": ("POST", f"/api/friend-houses/shared-lists/{SPACE}/accept", {"name": "Groceries (Smiths)"}),
    "decline-list": ("POST", f"/api/friend-houses/shared-lists/{SPACE}/decline", None),
    "stop-sharing": ("DELETE", f"/api/friend-houses/shared-lists/{SPACE}", None),
    # Blocking (admin only)
    "block": ("POST", "/api/friend-houses/friends/fr0123abcd/block",
              {"keep_messages": False, "new_address": True}),
    "blocked": ("GET", "/api/friend-houses/blocked", None),
    "unblock": ("DELETE", f"/api/friend-houses/blocked/{HOUSE}", None),
    # Apps with friends (SDK 1.18): admin only, at both hops
    "apps": ("GET", "/api/friend-houses/friends/fr0123abcd/apps", None),
    "set-apps": ("PUT", "/api/friend-houses/friends/fr0123abcd/apps", {"on": True, "apps": ["games"]}),
    "set-app": ("PUT", "/api/friend-houses/friends/fr0123abcd/apps/games", {"allowed": False}),
    "resume-app": ("POST", "/api/friend-houses/friends/fr0123abcd/apps/games/resume", None),
}
_IDS = sorted(ROUTES)


# Owner decision 17 (widened 2026-11-23): what everyone in the household
# may do with existing friends wears the device tier; everything else, the
# page's "Friends and connections" zone and saving or deleting a
# conversation, the security tier.
HOUSEHOLD_ROUTES = {
    ("GET", "/api/friend-houses"), ("GET", "/api/friend-houses/friends"),
    ("GET", "/api/friend-houses/conversations"), ("GET", "/api/friend-houses/conversations/{house}"),
    ("GET", "/api/friend-houses/unread"),
    ("POST", "/api/friend-houses/conversations/{house}/read"), ("POST", "/api/friend-houses/messages"),
    ("GET", "/api/friend-houses/shared-lists"),
    # Moved from the security tier on 2026-11-23 (starting a share stays admin).
    ("POST", "/api/friend-houses/shared-lists/{space_id}/accept"),
    ("POST", "/api/friend-houses/shared-lists/{space_id}/decline"),
    ("DELETE", "/api/friend-houses/shared-lists/{space_id}"),
}
#: The dashboard mirrors the plugin mail service added (design note §6.11).
FRIEND_APPS_ROUTES = {
    ("GET", "/api/friend-houses/friends/{friend_id}/apps"),
    ("PUT", "/api/friend-houses/friends/{friend_id}/apps"),
    ("PUT", "/api/friend-houses/friends/{friend_id}/apps/{slug}"),
    ("POST", "/api/friend-houses/friends/{friend_id}/apps/{slug}/resume"),
}
# What stays admin, named so a move is a decision, not a slip.
ADMIN_ROUTES = {
    ("DELETE", "/api/friend-houses/notice"), ("PUT", "/api/friend-houses/settings"),
    ("GET", "/api/friend-houses/invites"), ("POST", "/api/friend-houses/invites"),
    ("GET", "/api/friend-houses/invites/{invite_id}"), ("DELETE", "/api/friend-houses/invites/{invite_id}"),
    ("POST", "/api/friend-houses/preview"), ("POST", "/api/friend-houses/requests"),
    ("GET", "/api/friend-houses/requests"), ("POST", "/api/friend-houses/requests/{request_id}/accept"),
    ("POST", "/api/friend-houses/requests/{request_id}/decline"),
    ("DELETE", "/api/friend-houses/requests/{request_id}"),
    ("POST", "/api/friend-houses/friends/{friend_id}/verified"), ("PATCH", "/api/friend-houses/friends/{friend_id}"),
    ("DELETE", "/api/friend-houses/friends/{friend_id}"), ("PUT", "/api/friend-houses/friends/{friend_id}/alert"),
    ("GET", "/api/friend-houses/conversations/{house}/export"), ("DELETE", "/api/friend-houses/conversations/{house}"),
    ("POST", "/api/friend-houses/shared-lists"),
    ("POST", "/api/friend-houses/friends/{friend_id}/block"), ("GET", "/api/friend-houses/blocked"),
    ("DELETE", "/api/friend-houses/blocked/{house}"),
    *FRIEND_APPS_ROUTES,
}


def test_every_route_wears_its_tier() -> None:
    seen, household = 0, set()
    for r in iter_route_contexts(web_app.routes):
        if not getattr(r, "path", "").startswith("/api/friend-houses"):
            continue
        seen += 1
        deps = {d.call for d in r.dependant.dependencies}
        method = next(iter(r.methods))
        if (method, r.path) in HOUSEHOLD_ROUTES:
            household.add((method, r.path))
            assert admin_auth.require_device_after_setup in deps, r.path
            assert admin_auth.require_admin_security not in deps, r.path
        elif r.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            assert admin_auth.require_admin_security in deps, r.path
        else:
            assert admin_auth.require_admin_security_read in deps, r.path
    assert seen == len(ROUTES)
    assert household == HOUSEHOLD_ROUTES
    every = {(next(iter(r.methods)), r.path) for r in iter_route_contexts(web_app.routes)
             if getattr(r, "path", "").startswith("/api/friend-houses")}
    assert every == HOUSEHOLD_ROUTES | ADMIN_ROUTES


@pytest.fixture
def forwarded(monkeypatch):
    """Fake the core hop: record (method, path, body) and answer 200."""
    from web.backend.api import friend_houses as fh

    calls: list[tuple[str, str, object]] = []

    def fake(method):
        async def call(path, body=None, timeout=10.0, headers=None):
            # The caller's own credential goes on: the admin Bearer, or a
            # household phone's token on the household routes.
            assert headers and (headers.get("Authorization") == f"Bearer {ADMIN_TOKEN}"
                                or headers.get(admin_auth.DEVICE_TOKEN_HEADER) == HOUSEHOLD_TOKEN
                                or headers.get("Authorization") is None)
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
    # The household routes have no pre-setup grace either: after
    # --reset-admin the friends and messages are still there.
    assert r.status_code == 501, r.text
    assert forwarded == []


def _is_household(name: str) -> bool:
    method, path, _ = ROUTES[name]
    pattern = path.replace(HOUSE, "{house}").replace(SPACE, "{space_id}")
    return (method, pattern) in HOUSEHOLD_ROUTES


HOUSEHOLD_TOKEN = "household-token-for-tests"


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_the_household_token_reaches_only_the_household_routes(name, monkeypatch, forwarded) -> None:
    # Owner decision 17: a household phone reads the inbox, sends, sees the
    # friends and shared lists; trust and settings stay with the admin.
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=HOUSEHOLD_TOKEN)
    async with _client({admin_auth.DEVICE_TOKEN_HEADER: HOUSEHOLD_TOKEN}) as c:
        r = await _send(c, name)
    if _is_household(name):
        assert r.status_code == 200, r.text
        assert len(forwarded) == 1
    else:
        assert r.status_code in (401, 403), (name, r.status_code)
        assert forwarded == []


@pytest.mark.asyncio
async def test_the_open_conversations_own_mark_read_is_no_admin_activity(monkeypatch, forwarded) -> None:
    # Audit R6 WEB-24: the open conversation marks itself read from its
    # 10-second poll when a friend's message arrives. With the admin
    # Bearer on an admin write gate, each one restarted the idle hour, so
    # a chatty friend house could keep an admin signed in for 90 days. The
    # route is on the device tier, whose admin fallback is read-only.
    state = install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        r = await _send(c, "mark-read")
        assert r.status_code == 200, r.text
        assert state.acted == []
        r = await _send(c, "rename")  # a person's admin action still counts
        assert r.status_code == 200 and state.acted == [ADMIN_TOKEN]


def test_the_page_sends_no_admin_write_by_itself() -> None:
    # The page's own timers may only reach GETs or household routes: the
    # one write it sends unasked is mark-read, which is on the device tier.
    src = (STATIC / "friend_houses.jsx").read_text(encoding="utf-8")
    body = src[src.index("const markRead"):]
    body = body[:body.index("};") + 2]
    assert "apiPost(`${FH_API}/conversations/${house}/read`" in body
    assert ("POST", "/api/friend-houses/conversations/{house}/read") in HOUSEHOLD_ROUTES
    for line in src.splitlines():
        if "useFhEvery(" in line and "=>" in line:
            assert not any(w in line for w in ("apiPost", "apiPut", "apiPatch", "apiDel")), line


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_an_admin_bearer_reaches_the_cores_route(name, monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    method, path, body = ROUTES[name]
    async with _client(bearer(ADMIN_TOKEN)) as c:
        r = await _send(c, name)
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"  # R6 FE-11: friend files, messages
    assert len(forwarded) == 1
    f_method, f_path, f_body = forwarded[0]
    assert f_method == method
    assert f_path.split("?")[0] == path.replace("/api/friend-houses", "/v1/admin/friend-houses")
    if method in ("PUT", "PATCH") or name in ("make", "preview", "ask", "accept", "send",
                                                   "share-list", "accept-list", "block"):
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


def _run_js(source: str, fn: str, cases: list) -> list:
    """``fn(...case)`` for each case, with ``source`` (a slice of a page's
    plain-JS helpers) loaded first, in node."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    assert node, "node is required to run the page's helpers"
    script = ("const window = {};\n" + source +
              f"\nconsole.log(JSON.stringify(JSON.parse(process.argv[1]).map((c) => {fn}(...c))));")
    out = subprocess.run([node, "-e", script, json.dumps(cases)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, check=True)
    return json.loads(out.stdout)


def _fh_helpers() -> str:
    src = _page()
    return src[src.index("const fhClockOf = "):src.index("const FhAlertChoice = ")]


def test_the_quiet_hours_note_shows_only_when_the_friend_speaks_and_its_quiet_now() -> None:
    all_ = {"rooms": "all", "until": "07:00", "read_later": True}
    some = {"rooms": "some", "until": "07:00", "read_later": True}
    assert _run_js(_fh_helpers(), "fhQuietNote", [
        [all_, "read"], [all_, "sender"], [all_, "inbox"], [None, "read"],
        [some, "read"], [some, "sender"], [{**all_, "read_later": False}, "read"]]) == [
        "Quiet hours until 7:00am: new messages will be read out at 7:00am.",
        "Quiet hours until 7:00am: new messages will be announced at 7:00am.",
        None,
        None,
        "Quiet hours in some rooms until 7:00am: new messages will be read out there at 7:00am.",
        "Quiet hours in some rooms until 7:00am: new messages will be announced there at 7:00am.",
        "Quiet hours until 7:00am: new messages wait in the inbox and won’t be read out later.",
    ]
    src = _page()
    # From the friends payload, next to the choices.
    assert "quiet={friends && friends.quiet_hours}" in src
    assert "{fhQuietNote(quiet, current) && (" in src


def test_a_held_message_says_so_and_when_it_was_read_out() -> None:
    at = "2026-11-23T07:02:00"  # no zone: the browser's own clock, as fmtClock reads it
    assert _run_js(_fh_helpers(), "fhHeldText", [
        [{"direction": "in", "held": "quiet_hours", "read_out_at": None}],
        [{"direction": "in", "held": "quiet_hours", "read_out_at": at}],
        [{"direction": "in", "held": None, "read_out_at": None}],
        [{"direction": "out", "held": "quiet_hours", "read_out_at": None}]]) == [
        "held for quiet hours", "held for quiet hours · read out at 7:02am", None, None]
    assert '{fhHeldText(m) && <span className="fh-held">· {fhHeldText(m)}</span>}' in _page()


def test_the_sections_run_messages_then_shared_lists_then_the_admin_zone() -> None:
    src = _page()
    body = src[src.index("const FriendHousesPanel = "):]
    body = body[body.rindex("{head}"):]
    order = ["<FhInboxCard", "<FhSharedListsCard", "<FhAdminZone", "<FhRequestsCard", "<FhFriendsCard",
             "<FhInvitesCard", "<FhAddCard", "<FhHouseCard", "<FhBlockedCard", "</FhAdminZone>"]
    at = [body.index(tag) for tag in order]
    assert at == sorted(at)
    # Home's unread row lands on the Messages section.
    assert "const FriendHousesPanel = ({ marks = [] }) => {" in src
    assert "if (mark !== 'messages')" in src
    # No "Admins only" page any more: the zone asks instead.
    assert "Admins only." not in src


# ─── the page, driven: what a household member and an admin get ──────────

FH_HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FH_FILES = ["web/static/components.jsx", "web/static/friend_houses.jsx"]
# Auth, scripted: `__signedIn` is this tab's admin sign-in; ensureLoggedIn
# (the prompt) answers `__answer` and counts the asks.
FH_AUTH = """
globalThis.__signedIn = %s; globalThis.__answer = %s; globalThis.__asked = 0;
globalThis.Auth = { status: {}, subscribe: () => () => {}, headers: () => ({}), modalOpen: false,
  isLoggedIn: () => globalThis.__signedIn, canResume: () => false,
  ensureLoggedIn: () => { globalThis.__asked += 1;
    if (globalThis.__signedIn) return Promise.resolve(true);
    if (globalThis.__answer) globalThis.__signedIn = true;
    return Promise.resolve(!!globalThis.__answer); } };
"""
FH_API_TABLE = {
    "GET /api/friend-houses": {"status": "on", "unread": 1, "fingerprint": "abcd"},
    "GET /api/friend-houses/conversations": {"conversations": [
        {"house": HOUSE, "name": "The Smiths", "friend_id": "fr0123abcd", "unread": 1, "last_id": 3,
         "not_delivered": 0}], "unread": 1},
    "GET /api/friend-houses/friends": {"friends": [
        {"id": "fr0123abcd", "name": "The Smiths", "house": HOUSE, "alert": "inbox", "verified": True}],
        "quiet_hours": None},
    "GET /api/friend-houses/shared-lists": {"shared_lists": []},
    "GET /api/lists": [{"id": 7, "name": "Groceries"}],
    "GET /api/friend-houses/requests": {"requests": []},
    "GET /api/friend-houses/invites": {"invites": []},
    "GET /api/friend-houses/blocked": {"blocked": [{"house": "cd" * 32, "name": "Pests"}]},
    "GET /api/config": {"internet_access": "allowed"},
}
FH_ADMIN_PATHS = ("/api/friend-houses/requests", "/api/friend-houses/invites", "/api/friend-houses/blocked",
                  "/api/config/editable")
FH_SCRIPT = """
const zone = () => h.find((e) => e.type === 'button' && String((e.props || {}).className || '') === 'fh-zone-toggle');
const snap = () => ({ texts: h.text(), expanded: !!zone().props['aria-expanded'],
                      share: h.findAll((e) => e.type === 'select' || (e.type === 'button' && /^Share/.test(e.text))).length,
                      asked: h.global('__asked'), fetched: h.hookCalls.slice() });
h.render(); await h.settle(); h.rerender();
const before = snap();
await h.click((e) => e === zone());
const after = snap();
return { before, after };
"""


def _drive_panel(signed_in: bool, answer: bool) -> dict:
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    assert node, "node is required to drive the page"
    sc = {"files": FH_FILES, "component": "FriendHousesPanel", "props": {"marks": []},
          "api": FH_API_TABLE, "setup": FH_AUTH % (json.dumps(signed_in), json.dumps(answer)),
          "script": FH_SCRIPT}
    out = subprocess.run([node, str(FH_HARNESS), str(REPO_ROOT), json.dumps({"s": sc})], capture_output=True,
                         text=True, encoding="utf-8", timeout=120, check=True)
    got = json.loads(out.stdout)["s"]
    assert "__harness_error" not in got, got
    return got


ZONE_ORDER = ["Requests", "Friends", "Make a friend file", "Add a friend from their file", "This house",
              "Blocked houses"]


def _no_admin_content(snap: dict) -> None:
    for title in ZONE_ORDER:
        assert title not in snap["texts"], title
    assert not [p for p in snap["fetched"] if p.startswith(FH_ADMIN_PATHS)], snap["fetched"]
    assert not any("Admins only" in t for t in snap["texts"])


def test_a_household_member_gets_messages_and_shared_lists_and_a_closed_zone() -> None:
    got = _drive_panel(signed_in=False, answer=False)
    before = got["before"]
    assert "Messages" in before["texts"] and "Shared lists" in before["texts"]
    assert "Friends and connections" in before["texts"]
    assert before["expanded"] is False and before["asked"] == 0
    _no_admin_content(before)
    # Saving a copy, deleting a conversation and starting a share stay
    # admin: not offered, and nothing in their place.
    assert not any("Save a copy" in t for t in before["texts"])
    assert not before["share"]
    assert not any("sign in" in t.lower() for t in before["texts"])


def test_an_admin_gets_the_share_action_in_shared_lists() -> None:
    assert _drive_panel(signed_in=True, answer=False)["before"]["share"]


def test_the_zone_stays_closed_when_the_sign_in_is_cancelled_or_refused() -> None:
    # A wrong password keeps the prompt open; closing it answers false,
    # exactly like a cancel.
    after = _drive_panel(signed_in=False, answer=False)["after"]
    assert after["asked"] == 1 and after["expanded"] is False
    _no_admin_content(after)


def test_the_zone_opens_after_a_sign_in_in_the_owners_order() -> None:
    after = _drive_panel(signed_in=False, answer=True)["after"]
    assert after["asked"] == 1 and after["expanded"] is True
    at = [after["texts"].index(t) for t in ZONE_ORDER]
    assert at == sorted(at)
    assert after["texts"].index("Shared lists") < at[0]
    assert {"/api/friend-houses/requests", "/api/friend-houses/invites", "/api/friend-houses/blocked"} <= set(
        after["fetched"])


def test_an_admin_opens_the_zone_at_once_and_it_starts_closed() -> None:
    got = _drive_panel(signed_in=True, answer=False)
    assert got["before"]["expanded"] is False  # collapsed on every visit
    assert got["after"]["expanded"] is True and got["after"]["asked"] == 1


def _home() -> str:
    return (STATIC / "home.jsx").read_text(encoding="utf-8")


def test_home_lists_unread_friend_houses_messages() -> None:
    src = _home()
    helpers = (src[src.index("const HomePlural = "):src.index("// Answered once")]
               + src[src.index("const HOME_FH_INBOX = "):src.index("/* ---- status line")])
    row = {"key": "fh-unread", "tone": "brand", "scope": "household",
           "text": "2 unread Friend Houses messages", "href": "#settings/friend-houses/messages"}
    assert _run_js(helpers, "HomeFriendHousesRow", [
        [{"unread": 2}], [{"unread": 1}], [{"unread": 0}], [None]]) == [
        row, {**row, "text": "1 unread Friend Houses message"}, None, None]
    problem = {"key": "db", "text": "database"}
    assert _run_js(helpers, "HomeWithRow", [
        [{"mode": "rows", "rows": [problem]}, row], [{"mode": "none"}, row],
        [{"mode": "summary", "count": 1}, row], [{"mode": "none"}, None]]) == [
        {"mode": "rows", "rows": [problem, row]}, {"mode": "rows", "rows": [row]},
        {"mode": "summary", "count": 1, "extra": [row]}, {"mode": "none"}]
    # The inbox's own tier: asked only by a paired browser or an admin, and
    # re-read with the health tick and on focus.
    assert "useApiObject(viewer.paired || admin ? HOME_FH_INBOX : null," in src
    assert "fhInbox.refresh(); }, HOME_HEALTH_MS);" in src
    assert "HomeFriendHousesRow(fhInbox.data)" in src


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


def test_the_page_warns_when_the_installer_firewall_is_missing() -> None:
    # Threat-model finding 22: without the installer's rules the Tor
    # container could reach the rest of the home network, so the page says
    # so plainly, both before and after Friend Houses is turned on.
    page = _page()
    assert "status.firewall === 'missing'" in page
    assert "Update Domovoi (or run its installer again) before using Friend Houses." in page
    assert page.count("<FhFirewallNote status={status}/>") == 2


# ─── F4: shared lists ────────────────────────────────────────────────────


def test_shared_lists_are_plain_text_and_addressed_by_id_only() -> None:
    src = _code(_page())
    start = src.index("/* ---- shared lists (F4)") if "/* ---- shared lists (F4)" in src else src.index("const FH_SHARE_EXPLAIN")
    part = src[start:src.index("const FriendHousesPanel")]
    for banned in ("dangerouslySetInnerHTML", "innerHTML", "localStorage", "console."):
        assert banned not in part, banned
    # Every shared-list URL interpolates only the 32-hex id.
    urls = re.findall(r"`\$\{FH_API\}/shared-lists[^`]*`", part)
    assert urls
    for url in urls:
        assert re.findall(r"\$\{([^}]*)\}", url)[1:] in ([], ["share.id"]), url
    assert "const fhSpaceOk = (id) => /^[0-9a-f]{32}$/.test(String(id || ''));" in part
    # The list and friend are chosen in the body, never in the address.
    assert "apiPost(`${FH_API}/shared-lists`, { list_id: Number(listId), friend_id: friendId })" in part


def test_the_page_says_how_shared_lists_work() -> None:
    src = _page()
    for words in ("Changes either house makes show up in both, usually within a minute. "
                  "Each house keeps its own copy and its own name for it.",
                  "shared a list:", " — shared with ", " — from ",
                  "Stop sharing ${listName} with ${who}? You both keep your own copy.",
                  "waiting to send", "Shared lists", "Share a list"):
        assert words in src, words


@pytest.mark.asyncio
async def test_a_shared_list_id_that_isnt_one_never_reaches_the_core(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        for bad in ("cd" * 15, "CD" * 16, "zz" * 16, "cd" * 17, "ab" * 32):
            for method, path in (("POST", f"/api/friend-houses/shared-lists/{bad}/accept"),
                                 ("POST", f"/api/friend-houses/shared-lists/{bad}/decline"),
                                 ("DELETE", f"/api/friend-houses/shared-lists/{bad}")):
                r = await c.request(method, path, json={} if method == "POST" else None)
                assert r.status_code in (404, 405), (method, bad, r.status_code)
    assert forwarded == []


@pytest.mark.asyncio
async def test_sharing_a_list_checks_the_list_and_the_friend(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        for body in ({"list_id": 0, "friend_id": "fr0123abcd"},
                     {"list_id": -3, "friend_id": "fr0123abcd"},
                     {"list_id": "7", "friend_id": "fr0123abcd"},
                     {"list_id": True, "friend_id": "fr0123abcd"},
                     {"list_id": 1.5, "friend_id": "fr0123abcd"},
                     {"list_id": 7, "friend_id": "bad.id"},
                     {"list_id": 7},
                     {"friend_id": "fr0123abcd"}):
            r = await c.post("/api/friend-houses/shared-lists", json=body)
            assert r.status_code in (400, 404), (body, r.text)
        r = await c.post(f"/api/friend-houses/shared-lists/{SPACE}/accept", json={"name": ["Groceries"]})
        assert r.status_code == 400 and "Groceries" not in r.text
        assert (await c.post(f"/api/friend-houses/shared-lists/{SPACE}/accept", json={})).status_code == 200
    assert forwarded == [("POST", f"/v1/admin/friend-houses/shared-lists/{SPACE}/accept", {})]


# ─── presence and blocking ───────────────────────────────────────────────


def test_presence_says_only_whether_the_house_is_online() -> None:
    src = _code(_page())
    presence = re.search(r"const FhPresence = .*?\n\};", src, re.S).group(0)
    assert "<Pill tone=\"ok\">Online</Pill>" in presence
    assert "`Last seen ${ago}`" in presence and "'Not reached yet'" in presence
    assert "<FhPresence friend={friend}/>" in src


def test_the_page_says_what_blocking_does() -> None:
    src = _page()
    for words in ("Block ${name}? They’re removed and can’t ask to be friends again unless you unblock them.",
                  "Keep our messages with them", "Save a copy first",
                  "Move this house to a new private address (recommended): the blocked house can’t even reach the door.",
                  "Your other friends are told the new address automatically; messages may take a few minutes to get",
                  "Blocked houses",
                  "Unblock ${name}? They aren’t friends again; they can ask with a new friend file.",
                  "You blocked this house. Unblock it first if you want to be friends again.",
                  "They won’t be able to reach this house any more. (Block also stops them asking again.)"):
        assert words in src, words
    code = _code(src)
    # Both choices default to ticked; the block is sent as a body, by id.
    assert "const [keep, setKeep] = React.useState(true);" in code
    assert "const [move, setMove] = React.useState(true);" in code
    assert ("apiPost(`${FH_API}/friends/${encodeURIComponent(friend.id)}/block`,\n"
            "                              { keep_messages: keep, new_address: move })") in code
    # A blocked preview hides Send request, like the other refusals.
    assert "preview.already_asked || preview.blocked);" in code
    # The blocked list: plain text, hidden when empty, addressed by house key.
    card = re.search(r"const FhBlockedCard = .*?\n\};", code, re.S).group(0)
    assert "if (!list.length) return null;" in card
    assert "apiDelete(`${FH_API}/blocked/${house.house}`)" in code
    for banned in ("dangerouslySetInnerHTML", "innerHTML"):
        assert banned not in code


@pytest.mark.asyncio
async def test_blocking_checks_the_friend_and_the_body(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        assert (await c.post("/api/friend-houses/friends/bad.id/block", json={})).status_code == 404
        for body in ({"keep_messages": "no"}, {"new_address": 0}, {"keep_messages": None}, [True]):
            r = await c.post("/api/friend-houses/friends/fr0123abcd/block", json=body)
            assert r.status_code == 400, (body, r.text)
        # Leaving both out keeps the defaults: keep the messages, move.
        assert (await c.post("/api/friend-houses/friends/fr0123abcd/block", json={})).status_code == 200
    assert forwarded == [("POST", "/v1/admin/friend-houses/friends/fr0123abcd/block",
                          {"keep_messages": True, "new_address": True})]


@pytest.mark.asyncio
async def test_an_unblock_needs_a_house_key(monkeypatch, forwarded) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client(bearer(ADMIN_TOKEN)) as c:
        for bad in ("ab" * 31, "AB" * 32, "zz" * 32, "ab" * 33, "fr0123abcd"):
            r = await c.delete(f"/api/friend-houses/blocked/{bad}")
            assert r.status_code == 404, (bad, r.status_code)
    assert forwarded == []


# ─── apps with friends (SDK 1.18): admin only, once the routes exist ─────

FRIEND_APPS_PLANNED = sorted(FRIEND_APPS_ROUTES)


def test_the_apps_routes_exist_on_the_security_tier() -> None:
    assert set(_apps_routes()) == FRIEND_APPS_ROUTES


def test_no_apps_route_is_on_the_household_tier() -> None:
    assert not [r for r in HOUSEHOLD_ROUTES if "/apps" in r[1]]
    assert not set(FRIEND_APPS_PLANNED) & HOUSEHOLD_ROUTES


def _apps_routes() -> list[tuple[str, str]]:
    return sorted({(m, r.path) for r in iter_route_contexts(web_app.routes)
                   if getattr(r, "path", "").startswith("/api/friend-houses") and "/apps" in r.path
                   for m in r.methods})


@pytest.mark.asyncio
async def test_the_household_token_is_refused_on_every_apps_route(monkeypatch, forwarded) -> None:
    # None exist before the plugin mail service; each one it adds is tried
    # here with a household phone's token, and must be refused unforwarded.
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=HOUSEHOLD_TOKEN)
    async with _client({admin_auth.DEVICE_TOKEN_HEADER: HOUSEHOLD_TOKEN}) as c:
        for method, path in _apps_routes():
            url = path.replace("{friend_id}", "f1").replace("{slug}", "games")
            body = {} if method in ("PUT", "POST") else None
            r = await (c.request(method, url, json=body) if body is not None else c.request(method, url))
            assert r.status_code in (401, 403), (method, path, r.status_code)
    assert forwarded == []
