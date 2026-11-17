"""The admin idle timeout (2026-11-16).

What the owner saw: a dashboard tab left open all night was still admin in
the morning, and updated the server with no password prompt. The session
used to slide 30 days on every use, and the open tab used it every few
seconds (its polls carried the Bearer).

The rule now (``domovoi/admin_auth.py``): a session ends
``SESSION_IDLE_TIMEOUT_MIN`` (60) minutes after its last ADMIN ACTION — a
write that passed an admin write gate with the session's Bearer — and at
90 days whatever happens. These pin:

* idle expiry, on both processes, with nothing in between;
* polls don't extend a session (Bearer reads, the status probe, the
  cookie, the device tier's admin fallback, a WebSocket upgrade, a
  resume); admin writes do, on either process;
* the 90-day cap still ends an active session, and no action moves the
  deadline past it;
* resume within and after the window, counted from the last action;
* the reload after a server update keeps admin while the session is
  inside its hour — including the moment the web process has just
  restarted and has no live window from the core yet;
* a session from before the change (30-day ``expires_at``, polled
  ``last_used_at``) behaves sensibly: live for at most an hour more;
* sessions are per sign-in: one device's actions keep only its own alive;
* the status probe says how long is left and, for a dead Bearer, why;
* dead sessions are pruned at the next sign-in, a day after they end.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.config import ADMIN_IDLE_TIMEOUT_MIN, ADMIN_RELOAD_WINDOW_MAX_MIN
from domovoi.config import settings as core_settings
from domovoi.db.session import engine
from domovoi.tests.auth_testkit import (
    COOKIE,
    STRONG_PW,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_client,
    session_cookie,
    web_client,
)
from domovoi.tests.conftest import requires_db
from web.backend.api import auth as auth_api
from web.backend.main import app as web_app

ORIGIN = "http://test"
SAME_ORIGIN = {"X-Requested-With": "domovoi-tests", "Origin": ORIGIN}


@pytest.fixture
def live_window(monkeypatch):
    """The reload window the web reads off the core's snapshot: 60."""
    state = {"snapshot": {"admin_reload_window_min": 60}}
    monkeypatch.setattr(auth_api, "_last_live_window", None)
    monkeypatch.setattr(auth_api, "get_cached_snapshot", lambda: state["snapshot"])
    return state


async def login(label: str = "dashboard") -> tuple[str, str]:
    """A password sign-in: ``(token, cookie)``."""
    admin_auth.LOGIN_BACKOFF.reset()
    async with web_client() as c:
        r = await c.post("/api/auth/login", json={"password": STRONG_PW, "label": label})
    assert r.status_code == 200, r.text
    return r.json()["token"], session_cookie(r)


async def acted_ago(token: str, minutes: float) -> None:
    """Fake the clock: the session's last admin action was `minutes` ago."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE admin_sessions SET last_used_at = now() - make_interval(secs => :s) "
                "WHERE token_hash = :h"
            ),
            {"s": minutes * 60, "h": admin_auth.token_sha256(token)},
        )


async def clock(token: str):
    async with engine.begin() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT last_used_at, expires_at, created_at, "
                    "EXTRACT(EPOCH FROM (expires_at - now())) AS left_sec "
                    "FROM admin_sessions WHERE token_hash = :h"
                ),
                {"h": admin_auth.token_sha256(token)},
            )
        ).first()


async def core_read(token: str) -> int:
    async with core_client() as core:
        return (await core.get("/v1/admin/config", headers=bearer(token))).status_code


async def core_write(token: str) -> int:
    """An admin action on the core: a security-tier config write that
    changes nothing."""
    async with core_client() as core:
        r = await core.post("/v1/admin/config", json={"changes": {}}, headers=bearer(token))
        return r.status_code


async def web_write(token: str) -> int:
    """An admin action on the web process: a security-tier rotation."""
    async with web_client() as c:
        return (await c.post("/api/auth/device-token/rotate", headers=bearer(token))).status_code


def browser(cookie: str | None) -> AsyncClient:
    cookies = {COOKIE: cookie} if cookie else {}
    return AsyncClient(transport=ASGITransport(app=web_app), base_url=ORIGIN,
                       headers=SAME_ORIGIN, cookies=cookies)


async def status(token: str | None = None, cookie: str | None = None) -> dict:
    async with browser(cookie) as c:
        r = await c.get("/api/auth/status", headers=bearer(token) if token else {})
    assert r.status_code == 200, r.text
    return r.json()


async def resume(cookie: str):
    async with browser(cookie) as c:
        return await c.post("/api/auth/resume")


# ═══ The constant ══════════════════════════════════════════════════════════


def test_the_idle_timeout_is_one_named_hour() -> None:
    assert ADMIN_IDLE_TIMEOUT_MIN == 60
    assert admin_auth.SESSION_IDLE_TIMEOUT_MIN == ADMIN_IDLE_TIMEOUT_MIN
    # A reload can't keep a session the idle rule has ended.
    assert ADMIN_RELOAD_WINDOW_MAX_MIN == ADMIN_IDLE_TIMEOUT_MIN
    assert admin_auth.clamp_reload_window(1440) == ADMIN_IDLE_TIMEOUT_MIN
    # The 30-day slide is gone: an hour of idleness always came first.
    assert not hasattr(admin_auth, "SESSION_TTL_DAYS")


# ═══ Idle expiry ═══════════════════════════════════════════════════════════


@requires_db
async def test_a_session_ends_an_hour_after_its_last_admin_action(_db) -> None:
    async with web_client() as c:
        token = await claim_admin(c)
    row = await clock(token)
    # A sign-in is the first action: the deadline is an hour out, not 30 days.
    assert 3500 <= float(row.left_sec) <= 3600

    await acted_ago(token, 59)
    assert await core_read(token) == 200
    async with web_client() as c:
        assert (await c.get("/api/auth/sessions", headers=bearer(token))).status_code == 200

    await acted_ago(token, 61)
    assert await core_read(token) == 401
    assert await core_write(token) == 401
    async with web_client() as c:
        assert (await c.get("/api/auth/sessions", headers=bearer(token))).status_code == 401
    assert await web_write(token) == 401
    # A refused action is not an action: still over.
    assert await core_read(token) == 401


@requires_db
async def test_an_idle_session_still_needs_the_password_for_the_security_tier(_db) -> None:
    """The owner's morning: the overnight tab's Bearer can't update the
    server (the security tier) without the password again."""
    async with web_client() as c:
        token = await claim_admin(c)
    await acted_ago(token, 8 * 60)          # all night
    async with core_client() as core:
        r = await core.post("/v1/admin/config", json={"changes": {}}, headers=bearer(token))
    assert r.status_code == 401
    fresh, _ = await login()
    assert await core_write(fresh) == 200


# ═══ Polls don't extend a session; actions do ══════════════════════════════


@requires_db
async def test_polls_never_move_the_idle_clock(_db, live_window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token, cookie = await login()
    await acted_ago(token, 50)
    before = await clock(token)

    for _ in range(3):
        # Bearer reads on both processes.
        assert await core_read(token) == 200
        async with web_client() as c:
            assert (await c.get("/api/auth/sessions", headers=bearer(token))).status_code == 200
        # The status probe, with the Bearer and with the cookie.
        assert (await status(token=token))["authenticated"] is True
        assert (await status(cookie=cookie))["authenticated"] is True
        # The cookie rendering an admin read.
        async with browser(cookie) as c:
            assert (await c.get("/api/auth/sessions")).status_code == 200
        # The reload resume.
        assert (await resume(cookie)).status_code == 200

    after = await clock(token)
    assert (after.last_used_at, after.expires_at) == (before.last_used_at, before.expires_at)


@requires_db
async def test_a_device_tier_write_on_the_bearer_is_not_an_admin_action(_db) -> None:
    """A browser with no household token falls back to its admin Bearer
    for an everyday write (device registration, presence, listens...).
    The dashboard sends those by itself, so they must not count."""
    async with web_client() as c:
        await claim_admin(c)
    token, _ = await login()
    await acted_ago(token, 50)
    before = await clock(token)
    async with web_client() as c:
        r = await c.post(
            "/api/devices/register",
            json={"device_id": "b-123", "name": "Sarah's laptop", "platform": "browser"},
            headers={**bearer(token), "X-Requested-With": "domovoi-tests"},
        )
    assert r.status_code == 200, r.text
    after = await clock(token)
    assert after.last_used_at == before.last_used_at


@pytest.mark.parametrize(
    ("scope", "acts"),
    [
        ({"type": "http", "method": "POST"}, True),
        ({"type": "http", "method": "PUT"}, True),
        ({"type": "http", "method": "PATCH"}, True),
        ({"type": "http", "method": "DELETE"}, True),
        ({"type": "http", "method": "GET"}, False),
        ({"type": "http", "method": "HEAD"}, False),
        ({"type": "http", "method": "OPTIONS"}, False),
        ({"type": "websocket"}, False),
        ({}, False),
    ],
)
def test_only_a_write_can_be_an_admin_action(scope, acts) -> None:
    assert admin_auth.is_admin_activity(SimpleNamespace(scope=scope)) is acts


@requires_db
@pytest.mark.parametrize("where", ["core", "web"])
async def test_an_admin_write_restarts_the_hour(_db, where) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token, _ = await login()
    await acted_ago(token, 55)
    before = await clock(token)
    assert (await core_write(token) if where == "core" else await web_write(token)) == 200
    after = await clock(token)
    assert after.last_used_at > before.last_used_at
    assert 3500 <= float(after.left_sec) <= 3600
    # …and an hour after THAT it is over.
    await acted_ago(token, 61)
    assert await core_read(token) == 401


@requires_db
async def test_a_password_change_is_an_admin_action(_db) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token, _ = await login()
    await acted_ago(token, 55)
    async with web_client() as c:
        r = await c.post("/api/auth/password",
                         json={"old_password": STRONG_PW, "new_password": "a-brand-new-password"},
                         headers=bearer(token))
    assert r.status_code == 200, r.text
    assert 3500 <= float((await clock(token)).left_sec) <= 3600


# ═══ The absolute cap ══════════════════════════════════════════════════════


@requires_db
async def test_the_90_day_cap_ends_even_an_active_session(_db) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token, _ = await login()
    cap = admin_auth.SESSION_MAX_AGE_DAYS
    h = admin_auth.token_sha256(token)
    # Ten minutes short of the cap, acting a minute ago.
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE admin_sessions SET created_at = now() - make_interval(days => :d) "
                 "+ interval '10 minutes', last_used_at = now() - interval '1 minute' "
                 "WHERE token_hash = :h"),
            {"d": cap, "h": h},
        )
    assert await core_write(token) == 200
    row = await clock(token)
    # The action could not push the deadline past the cap.
    assert float(row.left_sec) <= 600
    assert (await status(token=token))["idle_in_sec"] <= 600
    # Past the cap: refused, however recent the last action.
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE admin_sessions SET created_at = now() - make_interval(days => :d) "
                 "- interval '1 minute', last_used_at = now() WHERE token_hash = :h"),
            {"d": cap, "h": h},
        )
    assert await core_read(token) == 401
    assert await core_write(token) == 401
    assert (await status(token=token))["ended"] == "ended"


# ═══ Resume within and after the window ════════════════════════════════════


@requires_db
async def test_a_reload_inside_the_hour_keeps_admin_after_it_signs_in_again(_db, live_window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token, cookie = await login()
    await acted_ago(token, 50)
    r = await resume(cookie)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"] == token
    assert 540 <= body["resume_in_sec"] <= 600
    assert 540 <= body["idle_in_sec"] <= 600
    assert body["idle_timeout_min"] == 60

    await acted_ago(token, 61)
    assert (await resume(cookie)).status_code == 401
    assert (await status(cookie=cookie))["authenticated"] is False


@requires_db
async def test_a_page_reloading_itself_can_not_keep_admin_alive(_db, live_window) -> None:
    """A kiosk tab, or an auto-refresh extension: reload after reload,
    each one resuming, and none of them an admin action."""
    async with web_client() as c:
        await claim_admin(c)
    token, cookie = await login()
    for minutes in (20, 40, 59):
        await acted_ago(token, minutes)
        r = await resume(cookie)
        assert r.status_code == 200
        assert await core_read(r.json()["token"]) == 200
    await acted_ago(token, 61)
    assert (await resume(cookie)).status_code == 401


# ═══ The reload after a server update ══════════════════════════════════════


@requires_db
async def test_the_reload_after_an_update_keeps_admin_inside_the_hour(_db, monkeypatch) -> None:
    """The owner presses Update (an admin action), the server restarts both
    processes, and the "Domovoi has been updated, reload" toast reloads the
    page. The web process has just started and the core may not have
    answered yet: the resume must still work, and the satellite upgrade
    after it must not ask for the password."""
    async with web_client() as c:
        await claim_admin(c)
    token, cookie = await login()
    await acted_ago(token, 30)
    assert await core_write(token) == 200        # "Update": an admin action

    # The web process restarted: no live window seen yet, no snapshot.
    monkeypatch.setattr(auth_api, "_last_live_window", None)
    monkeypatch.setattr(auth_api, "get_cached_snapshot", lambda: None)
    monkeypatch.setattr(core_settings, "admin_reload_window_min", 60)   # its .env copy
    st = await status(cookie=cookie)
    assert st["authenticated"] is True and st["resume_in_sec"] > 3500
    r = await resume(cookie)
    assert r.status_code == 200, r.text
    resumed = r.json()["token"]
    assert resumed == token
    # The next admin action — upgrading a room — goes through on it.
    assert await core_write(resumed) == 200


@requires_db
async def test_the_reload_after_an_update_past_the_hour_signs_in_again(_db, monkeypatch) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token, cookie = await login()
    await acted_ago(token, 90)
    monkeypatch.setattr(auth_api, "_last_live_window", None)
    monkeypatch.setattr(auth_api, "get_cached_snapshot", lambda: None)
    assert (await resume(cookie)).status_code == 401


# ═══ A session from before the change ══════════════════════════════════════


async def insert_old_session(*, last_used_min_ago: float | None, created_days_ago: float = 3) -> str:
    """A row as the code before the idle timeout left it: a 30-day sliding
    ``expires_at``, ``last_used_at`` stamped by the tab's last poll (or
    NULL), a cookie of its own."""
    token = "ab" * 32
    cookie = "an-old-browser-cookie"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO admin_sessions (token_hash, label, created_at, expires_at, "
                "last_used_at, signed_in_at, cookie_hash, cookie_token) VALUES "
                "(:h, 'dashboard', now() - make_interval(days => :c), now() + interval '30 days', "
                "CASE WHEN CAST(:u AS double precision) IS NULL THEN NULL "
                "ELSE now() - make_interval(secs => CAST(:u AS double precision) * 60) END, "
                "now() - make_interval(days => :c), :ch, :ct)"
            ),
            {"h": admin_auth.token_sha256(token), "c": created_days_ago, "u": last_used_min_ago,
             "ch": admin_auth.token_sha256(cookie), "ct": admin_auth.wrap_token(token, cookie)},
        )
    return token


@requires_db
async def test_an_old_session_polled_a_moment_ago_lives_at_most_an_hour_more(_db, live_window) -> None:
    """The owner's overnight tab, across the update: its last poll before
    the restart counts as its last action, so it stays admin for up to an
    hour — nobody is signed out in the middle of the update — and then
    signs out unless someone acts in it."""
    async with web_client() as c:
        await claim_admin(c)
    token = await insert_old_session(last_used_min_ago=2)
    assert await core_read(token) == 200
    st = await status(token=token)
    assert 3400 <= st["idle_in_sec"] <= 3480          # an hour from its last poll
    # Its 30-day expiry is not believed: polls alone end it.
    await acted_ago(token, 61)
    assert await core_read(token) == 401
    assert (await status(token=token))["ended"] == "idle"


@requires_db
async def test_an_old_session_never_used_counts_from_its_creation(_db) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await insert_old_session(last_used_min_ago=None, created_days_ago=2)
    assert await core_read(token) == 401


@requires_db
async def test_an_old_session_acting_now_runs_on_the_new_rule(_db) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await insert_old_session(last_used_min_ago=10)
    assert await core_write(token) == 200
    row = await clock(token)
    # The 30-day expiry is replaced by the idle deadline at its first action.
    assert 3500 <= float(row.left_sec) <= 3600


# ═══ Per sign-in: devices and tabs ═════════════════════════════════════════


@requires_db
async def test_one_devices_actions_keep_only_its_own_session_alive(_db) -> None:
    async with web_client() as c:
        await claim_admin(c)
    laptop, _ = await login("laptop")
    phone, _ = await login("phone")
    await acted_ago(laptop, 55)
    await acted_ago(phone, 55)
    assert await core_write(laptop) == 200          # Sarah changes something on the laptop
    await acted_ago(laptop, 10)
    phone_before = await clock(phone)
    assert phone_before.last_used_at is not None
    # Six minutes on, only the laptop is still signed in.
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE admin_sessions SET last_used_at = last_used_at - interval '6 minutes'"))
    assert await core_read(laptop) == 200
    assert await core_read(phone) == 401


@requires_db
async def test_tabs_of_one_browser_share_their_sign_in(_db, live_window) -> None:
    """Two tabs of the browser that signed in hold the same session (the
    second resumed it), so an action in one keeps the other signed in, and
    the idle tab's own check, when its count runs out, finds it live."""
    async with web_client() as c:
        await claim_admin(c)
    token, cookie = await login()
    r = await resume(cookie)                        # the second tab
    tab2 = r.json()["token"]
    assert tab2 == token
    await acted_ago(token, 59)
    assert await core_write(tab2) == 200            # the second tab acts
    await acted_ago(token, 30)
    st = await status(token=token)                  # the first tab asks
    assert st["authenticated"] is True and 1700 <= st["idle_in_sec"] <= 1800


# ═══ What the status probe says ════════════════════════════════════════════


@requires_db
async def test_the_status_says_why_a_bearer_stopped_working(_db) -> None:
    async with web_client() as c:
        await claim_admin(c)
    idle, _ = await login("idle")
    revoked, _ = await login("revoked")
    await acted_ago(idle, 61)
    async with web_client() as c:
        r = await c.post("/api/auth/logout", headers=bearer(revoked))
        assert r.status_code == 200
    a = await status(token=idle)
    assert a["authenticated"] is False and a["ended"] == "idle" and a["idle_in_sec"] is None
    b = await status(token=revoked)
    assert b["authenticated"] is False and b["ended"] == "ended"
    assert (await status(token="cd" * 32))["ended"] == "ended"
    live, _ = await login("live")
    c = await status(token=live)
    assert c["ended"] is None and 3500 <= c["idle_in_sec"] <= 3600
    assert c["idle_timeout_min"] == 60 and c["idle_until"]


@requires_db
async def test_login_answers_say_when_the_session_idles_out(_db) -> None:
    async with web_client() as c:
        await claim_admin(c)
    admin_auth.LOGIN_BACKOFF.reset()
    async with web_client() as c:
        r = await c.post("/api/auth/login", json={"password": STRONG_PW})
    body = r.json()
    assert 3590 <= body["idle_in_sec"] <= 3600
    assert body["idle_timeout_min"] == 60 and body["idle_until"]


# ═══ Pruning ═══════════════════════════════════════════════════════════════


@requires_db
async def test_a_sign_in_prunes_sessions_a_day_after_they_ended(_db) -> None:
    async with web_client() as c:
        await claim_admin(c)
    recent, _ = await login("ended an hour ago")
    old, _ = await login("ended two days ago")
    live, _ = await login("live")
    await acted_ago(recent, 120)
    await acted_ago(old, 3 * 24 * 60)
    await login("the next sign-in")
    async with engine.begin() as conn:
        labels = {r.label for r in (await conn.execute(text("SELECT label FROM admin_sessions"))).all()}
    assert "ended two days ago" not in labels
    assert {"ended an hour ago", "live", "the next sign-in"} <= labels
    # Still told apart: the recently ended one says why.
    assert (await status(token=recent))["ended"] == "idle"
