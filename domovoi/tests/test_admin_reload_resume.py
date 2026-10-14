"""A reloaded dashboard tab stays signed in for the reload window
(owner decision 2026-10-14).

"If someone logs in with admin, from that device, that exact session, they
should be good for within 60 mins of signing in. Refreshing should not lose
admin on the same machine, same session."

The shape: ``admin_sessions.signed_in_at`` (V028) records when a session
last proved the password, and ``POST /api/auth/resume`` hands the HttpOnly
cookie's own token back to the page that holds it while
``now - signed_in_at < ADMIN_RELOAD_WINDOW_MIN`` — same origin and
``X-Requested-With`` only. These pin:

* inside the window the resume answers the SAME session's token (nothing is
  minted) and that token does everything a Bearer does, the security tier
  included; outside it, or with the window at 0, it is refused and the
  cookie keeps rendering (view only, as before);
* the window is ABSOLUTE: using the session never moves it, only the
  password does (a password change restarts the caller's own);
* sign-out, a revoked session, a password change from another session, an
  expired session and a session from before V028 never resume;
* only this browser's cookie resumes, and only its own session: another
  device's fresh sign-in lends this one nothing;
* no ``X-Requested-With``, another origin (another port, another scheme,
  ``null``, ``Sec-Fetch-Site`` other than same-origin, neither header) or
  no cookie is refused, and a refusal touches nothing;
* ``GET /api/auth/status`` says how long is left, read-only;
* the setting: default 60, 0..1440, a bad ``.env`` value reads as 0, the
  web reads the live value from the core's snapshot (clamped), and the
  core's snapshot carries it;
* the migration: nullable columns with NO default, so the sessions that
  existed before it never resume;
* review round 1 (2026-10-14): the browser cookie is a secret of its own,
  never the Bearer (the row keeps its sha256 and the Bearer wrapped under
  it; a pre-V028 cookie renders nothing), a sign-in stamped in the future
  does not resume, the window never outlives the 90-day cap, and a missing
  core snapshot never widens the window (0 until one has been seen).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.config import ADMIN_RELOAD_WINDOW_MAX_MIN, Settings
from domovoi.config import settings as core_settings
from domovoi.config_schema import FIELD_BY_NAME
from domovoi.db.session import engine
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import (
    COOKIE,
    STRONG_PW,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_client,
    make_request,
    session_cookie,
    web_client,
)
from domovoi.tests.conftest import requires_db
from web.backend.api import auth as auth_api
from web.backend.main import app as web_app

ORIGIN = "http://test"           # the test client's own origin (base_url)
REQUESTED_WITH = {"X-Requested-With": "domovoi-tests"}
SAME_ORIGIN = {**REQUESTED_WITH, "Origin": ORIGIN}
MIGRATION = (
    Path(__file__).resolve().parents[1] / "db" / "migrations"
    / "V028__admin_session_reload_cookie.sql"
)


@pytest.fixture
def window(monkeypatch):
    """The live window the web reads (the core's snapshot), set per test.
    Defaults to the shipped 60 minutes."""
    state = {"snapshot": {"admin_reload_window_min": 60}}
    monkeypatch.setattr(auth_api, "_last_live_window", None)
    monkeypatch.setattr(auth_api, "get_cached_snapshot", lambda: state["snapshot"])

    def set_window(value, *, snapshot: bool = True):
        state["snapshot"] = {"admin_reload_window_min": value} if snapshot else None

    return set_window


@dataclass(frozen=True)
class Signin:
    """One password sign-in, as a browser holds it: the Bearer the answer
    carried and the cookie it set (its own secret since V028)."""

    token: str
    cookie: str


def _cookie_of(who) -> str | None:
    return who.cookie if isinstance(who, Signin) else who


def _token_of(who) -> str:
    return who.token if isinstance(who, Signin) else who


def browser(who, **headers) -> AsyncClient:
    """A browser tab after a reload: the session cookie, no Bearer. `who`
    is a Signin, a raw cookie value, or None."""
    cookie = _cookie_of(who)
    cookies = {COOKIE: cookie} if cookie else {}
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url=ORIGIN,
        headers={**SAME_ORIGIN, **headers}, cookies=cookies,
    )


async def login(label: str = "dashboard") -> Signin:
    """A fresh password sign-in (a new browser, its own cookie)."""
    admin_auth.LOGIN_BACKOFF.reset()
    async with web_client() as c:
        r = await c.post("/api/auth/login", json={"password": STRONG_PW, "label": label})
    assert r.status_code == 200, r.text
    return Signin(token=r.json()["token"], cookie=session_cookie(r))


async def row(who):
    async with engine.begin() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT signed_in_at, last_used_at, expires_at, created_at, "
                    "cookie_hash, cookie_token "
                    "FROM admin_sessions WHERE token_hash = :h"
                ),
                {"h": admin_auth.token_sha256(_token_of(who))},
            )
        ).first()


async def signed_in_ago(who, minutes: float) -> None:
    """Fake the clock: the sign-in happened `minutes` ago."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE admin_sessions SET signed_in_at = now() - make_interval(secs => :s) "
                "WHERE token_hash = :h"
            ),
            {"s": minutes * 60, "h": admin_auth.token_sha256(_token_of(who))},
        )


async def session_count() -> int:
    async with engine.begin() as conn:
        return (await conn.execute(text("SELECT count(*) FROM admin_sessions"))).scalar_one()


async def resume(who, **headers):
    async with browser(who, **headers) as c:
        return await c.post("/api/auth/resume")


async def status(who) -> dict:
    async with browser(who) as c:
        r = await c.get("/api/auth/status")
    assert r.status_code == 200, r.text
    return r.json()


# ═══ Inside the window ═════════════════════════════════════════════════════


@requires_db
async def test_a_reload_inside_the_window_gets_the_same_session_back(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    sessions = await session_count()
    before = await row(token)
    assert before.signed_in_at is not None

    st = await status(token)
    assert st["authenticated"] is True
    assert st["reload_window_min"] == 60
    assert 3590 <= st["resume_in_sec"] <= 3600
    assert st["resume_until"]

    r = await resume(token)
    assert r.status_code == 200, r.text
    body = r.json()
    # The cookie's own token — "that exact session" — and nothing minted.
    assert body["token"] == token.token
    assert await session_count() == sessions
    assert 3590 <= body["resume_in_sec"] <= 3600
    assert body["reload_window_min"] == 60
    assert r.headers["cache-control"] == "no-store"
    # A use like any other: it slides the expiry, never the sign-in time.
    after = await row(token)
    assert after.signed_in_at == before.signed_in_at
    assert after.last_used_at is not None


@requires_db
async def test_the_login_and_setup_answers_say_when_the_window_ends(_db, window) -> None:
    code = admin_auth.generate_setup_code()
    admin_auth.write_setup_code(code)
    async with web_client() as c:
        r = await c.post("/api/auth/setup", json={"setup_code": code, "password": STRONG_PW})
    assert r.status_code == 200, r.text
    assert 3590 <= r.json()["resume_in_sec"] <= 3600
    admin_auth.LOGIN_BACKOFF.reset()
    async with web_client() as c:
        r = await c.post("/api/auth/login", json={"password": STRONG_PW})
    assert r.status_code == 200, r.text
    assert 3590 <= r.json()["resume_in_sec"] <= 3600
    assert r.json()["reload_window_min"] == 60


@requires_db
async def test_security_tier_routes_work_after_a_resume(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    r = await resume(token)
    assert r.status_code == 200, r.text
    resumed = r.json()["token"]

    # Web: the household-token rotation is security tier, Bearer-only.
    async with web_client() as c:
        r = await c.post("/api/auth/device-token/rotate", headers=bearer(resumed))
    assert r.status_code == 200, r.text
    # Core: the config write is security tier too, on the other process.
    async with core_client() as core:
        r = await core.post("/v1/admin/config", json={"changes": {}}, headers=bearer(resumed))
    assert r.status_code == 200, r.text
    # The cookie alone still authorizes nothing: only the page that asked
    # for the token back (and holds it) can.
    async with browser(token) as c:
        r = await c.post("/api/auth/device-token/rotate")
    assert r.status_code == 403
    async with AsyncClient(
        transport=ASGITransport(app=core_app), base_url=ORIGIN, cookies={COOKIE: token.cookie},
    ) as core:
        r = await core.post("/v1/admin/config", json={"changes": {}})
    assert r.status_code == 403


# ═══ The window is absolute ════════════════════════════════════════════════


@requires_db
async def test_past_the_window_a_reload_is_view_only_again(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    await signed_in_ago(token, 61)
    before = await row(token)

    r = await resume(token)
    assert r.status_code == 401
    assert "sign in again" in r.json()["detail"]
    # Refused: nothing about the session moved.
    after = await row(token)
    assert (after.last_used_at, after.expires_at) == (before.last_used_at, before.expires_at)
    # The cookie still renders (view only, today's behaviour), and says so.
    st = await status(token)
    assert st["authenticated"] is True
    assert st["resume_in_sec"] is None and st["resume_until"] is None


@requires_db
async def test_using_the_session_never_moves_the_window(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    await signed_in_ago(token, 59)
    # Busy all along: Bearer and cookie reads, a resume.
    async with web_client() as c:
        for _ in range(3):
            assert (await c.get("/api/auth/sessions", headers=bearer(token.token))).status_code == 200
    r = await resume(token)
    assert r.status_code == 200, r.text
    assert 50 <= r.json()["resume_in_sec"] <= 60
    # A minute later by the sign-in's clock, the window is shut however
    # recently the session was used.
    await signed_in_ago(token, 60.5)
    assert (await resume(token)).status_code == 401


@requires_db
async def test_a_password_change_restarts_the_callers_own_window(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    await signed_in_ago(token, 50)
    async with web_client() as c:
        r = await c.post(
            "/api/auth/password",
            json={"old_password": STRONG_PW, "new_password": "another-good-password"},
            headers=bearer(token.token),
        )
    assert r.status_code == 200, r.text
    st = await status(token)
    assert 3590 <= st["resume_in_sec"] <= 3600


# ═══ What ends it ══════════════════════════════════════════════════════════


@requires_db
async def test_signing_out_ends_the_resume(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    async with browser(token) as c:
        r = await c.post("/api/auth/logout", headers=bearer(token.token))
        assert r.status_code == 200
        # The browser is told to drop the cookie…
        assert COOKIE in r.headers.get("set-cookie", "")
    # …and a cookie kept anyway (replayed) resumes nothing: the session is gone.
    r = await resume(token)
    assert r.status_code == 401
    assert (await status(token))["authenticated"] is False


@requires_db
async def test_a_revoked_session_does_not_resume(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    laptop = await login("laptop")
    phone = await login("phone")
    async with web_client() as c:
        r = await c.request(
            "DELETE", f"/api/auth/sessions/{admin_auth.token_sha256(laptop.token)}",
            headers=bearer(phone.token),
        )
    assert r.status_code == 200, r.text
    assert (await resume(laptop)).status_code == 401
    assert (await resume(phone)).status_code == 200


@requires_db
async def test_a_password_change_elsewhere_ends_every_other_resume(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    laptop = await login("laptop")
    phone = await login("phone")
    async with web_client() as c:
        r = await c.post(
            "/api/auth/password",
            json={"old_password": STRONG_PW, "new_password": "another-good-password"},
            headers=bearer(phone.token),
        )
    assert r.status_code == 200, r.text
    assert (await resume(laptop)).status_code == 401
    assert (await resume(phone)).status_code == 200


@requires_db
async def test_an_expired_session_does_not_resume(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE admin_sessions SET expires_at = now() - interval '1 second' "
                 "WHERE token_hash = :h"),
            {"h": admin_auth.token_sha256(token.token)},
        )
    assert (await resume(token)).status_code == 401


@requires_db
async def test_a_session_from_before_the_migration_never_resumes(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    old = "a" * 64
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO admin_sessions (token_hash, label, expires_at) "
                 "VALUES (:h, 'old', now() + interval '30 days')"),
            {"h": admin_auth.token_sha256(old)},
        )
    # The browser of such a session holds the OLD cookie: the raw Bearer.
    # After the upgrade it renders nothing and resumes nothing — the owner
    # signs in once more (review finding 4: force a fresh sign-in)…
    st = await status(old)
    assert st["authenticated"] is False
    assert st["resume_in_sec"] is None
    assert (await resume(old)).status_code == 401
    async with AsyncClient(
        transport=ASGITransport(app=core_app), base_url=ORIGIN, cookies={COOKIE: old},
    ) as core:
        assert (await core.get("/v1/admin/config")).status_code == 401
    # …while a tab still holding that Bearer in memory keeps working.
    async with web_client() as c:
        assert (await c.get("/api/auth/sessions", headers=bearer(old))).status_code == 200


# ═══ The cookie is its own secret, never the Bearer (review finding 4) ═══════


@requires_db
async def test_the_cookie_is_not_the_bearer_and_the_db_holds_neither(_db, window) -> None:
    code = admin_auth.generate_setup_code()
    admin_auth.write_setup_code(code)
    async with web_client() as c:
        r = await c.post("/api/auth/setup", json={"setup_code": code, "password": STRONG_PW})
    assert r.status_code == 200
    setup = Signin(token=r.json()["token"], cookie=session_cookie(r))
    signin = await login()
    for s in (setup, signin):
        assert s.cookie and s.cookie != s.token
        assert s.token not in s.cookie and s.cookie not in s.token
        stored = await row(s)
        # Only hashes, and the Bearer wrapped: none of the three raw values.
        assert stored.cookie_hash == admin_auth.token_sha256(s.cookie)
        assert stored.cookie_token and stored.cookie_token != s.token
        assert s.cookie not in (stored.cookie_hash, stored.cookie_token)
        assert admin_auth.unwrap_token(stored.cookie_token, s.cookie) == s.token
        assert admin_auth.unwrap_token(stored.cookie_token, s.cookie + "x") != s.token
    assert setup.cookie != signin.cookie


@requires_db
async def test_the_cookie_renders_and_resumes_but_is_no_bearer(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    s = await login()
    # It renders admin GET state, on both processes…
    assert (await status(s))["authenticated"] is True
    async with AsyncClient(
        transport=ASGITransport(app=core_app), base_url=ORIGIN, cookies={COOKIE: s.cookie},
    ) as core:
        assert (await core.get("/v1/admin/config")).status_code == 200
        # …but presented AS a Bearer it is nothing.
        r = await core.get("/v1/admin/config", headers=bearer(s.cookie))
        assert r.status_code == 401
    # The session list marks the cookie's own session current.
    async with browser(s) as c:
        sessions = (await c.get("/api/auth/sessions")).json()["sessions"]
    current = [x for x in sessions if x["current"]]
    assert [x["token_hash"] for x in current] == [admin_auth.token_sha256(s.token)]
    assert all("cookie" not in key for x in sessions for key in x)


@requires_db
@pytest.mark.parametrize(
    "ended",
    [
        "expires_at = now() - interval '1 second'",                     # slid past its expiry
        f"created_at = now() - interval '{admin_auth.SESSION_MAX_AGE_DAYS} days 1 minute'",  # the cap
    ],
    ids=["expired", "past-90-day-cap"],
)
async def test_an_ended_sessions_cookie_renders_nothing(_db, window, ended) -> None:
    """Review round 2 (N10): validate_cookie holds the session's expiry and
    its 90-day cap, exactly as validate_token does — such a cookie is not
    signed in, on either process, and slides nothing."""
    async with web_client() as c:
        await claim_admin(c)
    s = await login()
    async with engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE admin_sessions SET {ended} WHERE token_hash = :h"),
            {"h": admin_auth.token_sha256(s.token)},
        )
    before = await row(s)
    st = await status(s)
    assert st["authenticated"] is False and st["resume_in_sec"] is None
    async with AsyncClient(
        transport=ASGITransport(app=core_app), base_url=ORIGIN, cookies={COOKIE: s.cookie},
    ) as core:
        assert (await core.get("/v1/admin/config")).status_code == 401
    async with browser(s) as c:
        assert (await c.get("/api/auth/sessions")).status_code == 401
    after = await row(s)
    assert (after.last_used_at, after.expires_at) == (before.last_used_at, before.expires_at)


@requires_db
async def test_the_security_tier_reads_answer_the_bearer_only(_db, window) -> None:
    """Review round 2: the household token (both processes) and the
    recordings are Bearer-only reads; the cookie alone is 403 even inside
    the reload window, where the page takes its Bearer back instead."""
    async with web_client() as c:
        await claim_admin(c)
    s = await login()
    async with browser(s) as c:
        assert (await c.get("/api/auth/device-token")).status_code == 403
        assert (await c.get("/api/captures")).status_code == 403
        token = (await c.post("/api/auth/resume")).json()["token"]
        assert (await c.get("/api/auth/device-token", headers=bearer(token))).status_code == 200
    async with AsyncClient(
        transport=ASGITransport(app=core_app), base_url=ORIGIN, cookies={COOKIE: s.cookie},
    ) as core:
        assert (await core.get("/v1/admin/device-token")).status_code == 403
        assert (await core.get("/v1/admin/device-token", headers=bearer(s.token))).status_code == 200


@requires_db
async def test_a_cookie_whose_wrapped_token_does_not_match_never_resumes(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    s = await login()
    other = await login("other")
    # The row's wrapped token swapped for another session's: the cookie
    # still finds the row, but what it unwraps is not that row's token.
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE admin_sessions SET cookie_token = :ct WHERE token_hash = :h"),
            {"ct": admin_auth.wrap_token(other.token, s.cookie),
             "h": admin_auth.token_sha256(s.token)},
        )
    r = await resume(s)
    assert r.status_code == 401
    assert "token" not in r.json()


# ═══ Clock edges (review finding 3) ════════════════════════════════════════


@requires_db
async def test_a_sign_in_stamped_in_the_future_does_not_resume(_db, window) -> None:
    """The database clock stepped back after the sign-in: the stamp is in
    the future, and it must not stretch the window by the step."""
    async with web_client() as c:
        await claim_admin(c)
    s = await login()
    await signed_in_ago(s, -300)            # five hours ahead of now()
    assert (await resume(s)).status_code == 401
    assert (await status(s))["resume_in_sec"] is None


@requires_db
async def test_the_window_never_outlives_the_90_day_cap(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    s = await login()
    cap = admin_auth.SESSION_MAX_AGE_DAYS
    async with engine.begin() as conn:
        # Created just under the cap ago, signed in again a minute ago.
        await conn.execute(
            text("UPDATE admin_sessions SET created_at = now() - make_interval(days => :d) "
                 "+ interval '10 minutes', signed_in_at = now() - interval '1 minute' "
                 "WHERE token_hash = :h"),
            {"d": cap, "h": admin_auth.token_sha256(s.token)},
        )
    r = await resume(s)
    assert r.status_code == 200, r.text
    assert r.json()["resume_in_sec"] <= 600          # the cap, not the hour
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE admin_sessions SET created_at = now() - make_interval(days => :d) "
                 "- interval '1 minute' WHERE token_hash = :h"),
            {"d": cap, "h": admin_auth.token_sha256(s.token)},
        )
    assert (await resume(s)).status_code == 401


# ═══ This browser, this session ════════════════════════════════════════════


@requires_db
async def test_another_devices_fresh_sign_in_lends_this_browser_nothing(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    laptop = await login("laptop")
    await signed_in_ago(laptop, 120)
    phone = await login("phone")          # a sign-in a minute ago, elsewhere

    assert (await resume(laptop)).status_code == 401
    assert (await status(laptop))["resume_in_sec"] is None
    # The phone's cookie resumes the phone's session, and only that.
    r = await resume(phone)
    assert r.status_code == 200
    assert r.json()["token"] == phone.token


@requires_db
async def test_no_cookie_no_resume(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    await login()
    r = await resume(None)
    assert r.status_code == 401
    assert "no admin sign-in in this browser" in r.json()["detail"]
    st = await status(None)
    assert st["authenticated"] is False and st["resume_in_sec"] is None


@requires_db
async def test_a_bearer_alone_resumes_nothing(_db, window) -> None:
    """The resume reads the cookie and nothing else."""
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    r = await resume(None, Authorization=f"Bearer {token.token}")
    assert r.status_code == 401


# ═══ Same origin, X-Requested-With ═════════════════════════════════════════


@requires_db
async def test_the_resume_needs_x_requested_with(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url=ORIGIN,
        headers={"Origin": ORIGIN}, cookies={COOKIE: token.cookie},
    ) as c:
        r = await c.post("/api/auth/resume")
    assert r.status_code == 403
    assert "X-Requested-With" in r.json()["detail"]


def test_the_route_wears_the_header_check_itself() -> None:
    """Not only the process-wide backstop: the route's own dependency, so
    moving the middleware can't open it."""
    from domovoi.tests.route_walk import iter_route_contexts
    from web.backend.api.csrf_guard import require_requested_with

    (rc,) = [r for r in iter_route_contexts(web_app.routes)
             if getattr(r, "path", None) == "/api/auth/resume"]
    calls = [d.call for d in rc.dependant.dependencies]
    assert require_requested_with in calls


@requires_db
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "http://test:6390"},                 # same host, another port
        {"Origin": "https://test"},                     # another scheme
        {"Origin": "null"},                             # a sandboxed frame
        {"Origin": "http://test", "Sec-Fetch-Site": "same-site"},
        {"Origin": "", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "", "Sec-Fetch-Site": "same-site"},
        {"Origin": ""},                                 # neither header
        {"Origin": "http://test/path"},
        {"Origin": "http://user@test"},
    ],
    ids=["foreign", "other-port", "other-scheme", "null", "fetch-site-same-site",
         "no-origin-cross-site", "no-origin-same-site", "neither", "path", "userinfo"],
)
async def test_another_origin_is_refused_and_touches_nothing(_db, window, headers) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    before = await row(token)
    r = await resume(token, **headers)
    assert r.status_code == 403, r.text
    assert "another origin" in r.json()["detail"]
    assert "token" not in r.json()
    after = await row(token)
    assert (after.last_used_at, after.expires_at) == (before.last_used_at, before.expires_at)


@requires_db
async def test_a_same_origin_fetch_without_an_origin_header_resumes(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    r = await resume(token, Origin="", **{"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, r.text
    r = await resume(token, **{"Sec-Fetch-Site": "same-origin"})   # both, agreeing
    assert r.status_code == 200, r.text


def _req(origin: str | None, host: str, scheme: str = "http", fetch_site: str | None = None):
    headers = {"host": host}
    if origin is not None:
        headers["origin"] = origin
    if fetch_site is not None:
        headers["sec-fetch-site"] = fetch_site
    req = make_request(headers=headers)
    req.scope["scheme"] = scheme
    req.scope["server"] = None
    req.scope["path"] = "/api/auth/resume"
    req.scope["query_string"] = b""
    return req


@pytest.mark.parametrize(
    ("origin", "host", "scheme", "ok"),
    [
        ("http://192.168.1.10:6369", "192.168.1.10:6369", "http", True),
        ("HTTP://Domovoi.LAN:6369", "domovoi.lan:6369", "http", True),
        ("http://domovoi.local", "domovoi.local:80", "http", True),     # default port
        ("http://domovoi.local:80", "domovoi.local", "http", True),
        ("https://box.lan", "box.lan", "https", True),
        ("http://[::1]:6369", "[::1]:6369", "http", True),
        ("http://192.168.1.10:6369", "192.168.1.10:6390", "http", False),
        ("http://192.168.1.11:6369", "192.168.1.10:6369", "http", False),
        ("https://box.lan", "box.lan", "http", False),
        ("https://box.lan:6369", "box.lan:6369", "http", False),   # only the scheme differs
        ("http://box.lan:abc", "box.lan", "http", False),
        ("ftp://box.lan", "box.lan", "http", False),
        ("http://box.lan", "", "http", False),
    ],
)
def test_same_origin_compares_scheme_host_and_port(origin, host, scheme, ok) -> None:
    assert auth_api.same_origin(_req(origin, host, scheme)) is ok


# ═══ The setting ═══════════════════════════════════════════════════════════


@requires_db
async def test_a_window_of_zero_is_todays_behaviour(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    window(0)
    token = await login()
    st = await status(token)
    assert st["reload_window_min"] == 0
    assert st["authenticated"] is True and st["resume_in_sec"] is None
    assert (await resume(token)).status_code == 401


@requires_db
async def test_the_window_is_read_live_and_shrinking_it_applies_at_once(_db, window) -> None:
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    await signed_in_ago(token, 10)
    assert (await resume(token)).status_code == 200
    window(5)                                    # an admin saves 5 minutes
    assert (await resume(token)).status_code == 401
    window(15)
    r = await resume(token)
    assert r.status_code == 200
    assert 290 <= r.json()["resume_in_sec"] <= 300


@pytest.mark.parametrize(
    ("snapshot", "read"),
    [
        ({"admin_reload_window_min": 15}, 15),             # the live value wins
        ({"admin_reload_window_min": 0}, 0),
        ({"admin_reload_window_min": 99999}, ADMIN_RELOAD_WINDOW_MAX_MIN),
        ({"admin_reload_window_min": -3}, 0),
        # Not a live value, and none seen yet since this process started: 0.
        ({"admin_reload_window_min": True}, 0),
        ({"admin_reload_window_min": "45"}, 0),
        ({}, 0),                                           # an older core
        (None, 0),                                         # the core is down
    ],
)
def test_the_web_reads_the_window_from_the_cores_snapshot(monkeypatch, snapshot, read) -> None:
    monkeypatch.setattr(auth_api, "_last_live_window", None)
    monkeypatch.setattr(auth_api, "get_cached_snapshot", lambda: snapshot)
    monkeypatch.setattr(core_settings, "admin_reload_window_min", 30)   # this process's copy
    assert auth_api.reload_window_min() == read


@pytest.mark.parametrize(
    ("last_live", "own", "read"),
    [
        (0, 60, 0),       # an admin turned it off: a missing snapshot keeps it off
        (15, 60, 15),     # an admin shortened it: still short
        (120, 60, 60),    # an admin lengthened it: no longer than this process booted with
        (60, 0, 0),
    ],
)
def test_a_missing_snapshot_never_widens_the_window(monkeypatch, last_live, own, read) -> None:
    """Review finding 2: the core down or restarting must not hand the
    web's boot-time .env copy back the last word."""
    snapshot = {"value": {"admin_reload_window_min": last_live}}
    monkeypatch.setattr(auth_api, "_last_live_window", None)
    monkeypatch.setattr(auth_api, "get_cached_snapshot", lambda: snapshot["value"])
    monkeypatch.setattr(core_settings, "admin_reload_window_min", own)
    assert auth_api.reload_window_min() == admin_auth.clamp_reload_window(last_live)
    snapshot["value"] = None                     # a poll missed
    assert auth_api.reload_window_min() == read
    snapshot["value"] = {}                       # or an answer without the field
    assert auth_api.reload_window_min() == read


@requires_db
async def test_a_live_zero_holds_while_the_core_snapshot_is_missing(_db, window, monkeypatch) -> None:
    """The reviewer's R1, end to end: 0 saved live, then the core's snapshot
    goes missing; the web's boot copy (60) does not reopen the resume."""
    async with web_client() as c:
        await claim_admin(c)
    monkeypatch.setattr(core_settings, "admin_reload_window_min", 60)
    s = await login()
    window(0)
    assert (await resume(s)).status_code == 401
    window(None, snapshot=False)
    assert (await resume(s)).status_code == 401
    assert (await status(s))["reload_window_min"] == 0


def test_clamp_reload_window() -> None:
    assert admin_auth.clamp_reload_window(60) == 60
    assert admin_auth.clamp_reload_window(0) == 0
    assert admin_auth.clamp_reload_window(-1) == 0
    assert admin_auth.clamp_reload_window(10**6) == ADMIN_RELOAD_WINDOW_MAX_MIN
    assert admin_auth.clamp_reload_window(True) == 0
    assert admin_auth.clamp_reload_window("60") == 0
    assert admin_auth.clamp_reload_window(None) == 0


@pytest.mark.parametrize(
    ("raw", "read"),
    [(None, 60), ("", 60), ("  ", 60), ("30", 30), (" 45 ", 45), (0, 0), ("0", 0),
     ("-5", 0), ("ninety", 0), ("1.5", 0), (5000, ADMIN_RELOAD_WINDOW_MAX_MIN), (True, 0)],
)
def test_the_env_value_is_never_fatal_and_never_wider(monkeypatch, raw, read) -> None:
    monkeypatch.delenv("ADMIN_RELOAD_WINDOW_MIN", raising=False)
    kwargs = {} if raw is None else {"admin_reload_window_min": raw}
    assert Settings(_env_file=None, **kwargs).admin_reload_window_min == read


def test_the_default_is_the_owners_sixty_minutes(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_RELOAD_WINDOW_MIN", raising=False)
    assert Settings(_env_file=None).admin_reload_window_min == 60


def test_the_setting_is_in_admin_configuration() -> None:
    spec = FIELD_BY_NAME["admin_reload_window_min"]
    assert spec.type == "int"
    assert (spec.min, spec.max) == (0, ADMIN_RELOAD_WINDOW_MAX_MIN)
    assert spec.tier == "hot"
    assert spec.section == "common"
    assert spec.group == "Security"


def test_the_core_snapshot_carries_the_live_window(monkeypatch) -> None:
    """A config save setattr()s the CORE's singleton; the snapshot the web
    polls reads that singleton, not .env."""
    from domovoi import main as core_main

    for name in ("active_sessions", "resumable_music", "wifi_status", "current_playlist",
                 "active_dropins", "satellite_full_duplex", "satellite_sat_type",
                 "satellite_mic_enabled", "satellite_display", "satellite_voice",
                 "satellite_volume", "satellite_synced_sha"):
        monkeypatch.setattr(core_main.app.state, name, {}, raising=False)
    monkeypatch.setattr(core_settings, "admin_reload_window_min", 25)
    snap = asyncio.run(core_main.admin_snapshot())
    assert snap["admin_reload_window_min"] == 25


# ═══ The migration ═════════════════════════════════════════════════════════


def _migration_sql() -> str:
    """V028 without its comment lines."""
    return "\n".join(
        line for line in MIGRATION.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )


def test_the_migration_adds_nullable_columns_with_no_default() -> None:
    statements = [" ".join(x.split()) for x in _migration_sql().split(";") if x.strip()]
    assert statements == [
        "ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS signed_in_at TIMESTAMPTZ",
        "ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS cookie_hash TEXT",
        "ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS cookie_token TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_sessions_cookie_hash "
        "ON admin_sessions (cookie_hash) WHERE cookie_hash IS NOT NULL",
    ]


@requires_db
async def test_the_migrated_columns_are_there_and_reapplying_is_a_no_op(_db) -> None:
    async with engine.begin() as conn:
        cols = {
            r.column_name: r
            for r in (
                await conn.execute(
                    text(
                        "SELECT column_name, data_type, is_nullable, column_default "
                        "FROM information_schema.columns WHERE table_name = 'admin_sessions' "
                        "AND column_name IN ('signed_in_at', 'cookie_hash', 'cookie_token')"
                    )
                )
            ).all()
        }
        assert cols["signed_in_at"].data_type == "timestamp with time zone"
        assert cols["cookie_hash"].data_type == "text"
        assert cols["cookie_token"].data_type == "text"
        for col in cols.values():
            assert col.is_nullable == "YES"
            assert col.column_default is None
        # A row written the way every pre-V028 build wrote one keeps NULLs.
        await conn.execute(
            text("INSERT INTO admin_sessions (token_hash, expires_at) "
                 "VALUES ('pre-v028', now() + interval '1 day')")
        )
        for statement in _migration_sql().split(";"):
            if statement.strip():
                await conn.execute(text(statement))
        values = (
            await conn.execute(
                text("SELECT signed_in_at, cookie_hash, cookie_token FROM admin_sessions "
                     "WHERE token_hash = 'pre-v028'")
            )
        ).one()
        assert tuple(values) == (None, None, None)
