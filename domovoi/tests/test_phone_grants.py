"""Phone grants: admin on a phone for the rest of the day (V085, 1.5.0).

A phone that has just signed in with the password may ask for a grant
(``POST /api/auth/phone-grant``): 256 random bits, bound to its device id,
ending 24 hours after issue. It swaps the grant for a fresh admin session
(``POST /api/auth/phone-grant/exchange``) when it needs a Bearer again, and
ends it on sign-out (``POST /api/auth/phone-grant/revoke``). An admin lists
and revokes grants on the dashboard (``GET`` / ``DELETE
/api/auth/phone-grants``). These pin:

* each endpoint's happy path, and that only the sha256 is stored;
* the freshness rule: the minting session proved the password in the last
  five minutes; a sign-in six minutes old, a session kept alive by admin
  actions since, and a session minted FROM a grant are all refused;
* expiry, a device id that doesn't match, one grant per device (a reissue
  replaces the old one and ends the sessions it minted);
* a password change and ``--reset-admin`` delete every grant;
* the phone's revoke answers the same whether or not the grant existed;
* the exchange shares the password login's per-source backoff, both ways;
* nothing without the household token and an admin Bearer;
* no grant, token or password in the logs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.db.session import engine
from domovoi.tests.auth_testkit import (
    HEADER,
    STRONG_PW,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db

RW = {"X-Requested-With": "domovoi-tests"}
DEVICE = "android-3f2a9c"
LABEL = "Alex's Pixel"
MIGRATION = (
    Path(__file__).resolve().parents[1] / "db" / "migrations" / "V085__admin_phone_grants.sql"
)


# ═══ DB-free ══════════════════════════════════════════════════════════════


def test_the_migration_keeps_only_a_hash_and_cascades_to_sessions() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "grant_hash   TEXT NOT NULL UNIQUE" in sql
    assert "device_id    TEXT NOT NULL UNIQUE" in sql
    assert "REFERENCES admin_phone_grants (id) ON DELETE CASCADE" in sql


# ═══ DB-backed helpers ════════════════════════════════════════════════════


def phone(token: str | None = None, device_token: str | None = None) -> dict[str, str]:
    headers = dict(RW)
    if device_token:
        headers[HEADER] = device_token
    if token:
        headers.update(bearer(token))
    return headers


async def login() -> str:
    admin_auth.LOGIN_BACKOFF.reset()
    async with web_client() as c:
        r = await c.post("/api/auth/login", json={"password": STRONG_PW, "label": "phone sign-in"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def claimed() -> tuple[str, str]:
    """A claimed house: (a fresh password session, the household token)."""
    async with web_client() as c:
        await claim_admin(c)
    return await login(), await db_device_token()


async def mint(token: str, dt: str, device_id: str = DEVICE, label: str = LABEL):
    async with web_client(headers=phone(token, dt)) as c:
        return await c.post("/api/auth/phone-grant", json={"device_id": device_id, "label": label})


async def exchange(dt: str | None, grant: str, device_id: str = DEVICE):
    async with web_client(headers=phone(None, dt)) as c:
        return await c.post(
            "/api/auth/phone-grant/exchange", json={"grant": grant, "device_id": device_id}
        )


async def revoke(grant: str):
    async with web_client(headers=RW) as c:
        return await c.post("/api/auth/phone-grant/revoke", json={"grant": grant})


async def sql(stmt: str, **params):
    async with engine.begin() as conn:
        result = await conn.execute(text(stmt), params)
        return result.all() if result.returns_rows else []


async def is_live(token: str) -> bool:
    async with web_client(headers=bearer(token)) as c:
        r = await c.get("/api/auth/status")
    return r.json()["authenticated"] is True and r.json()["idle_in_sec"] is not None


async def age_sign_in(token: str, minutes: float) -> None:
    await sql(
        "UPDATE admin_sessions SET signed_in_at = now() - make_interval(secs => :s) "
        "WHERE token_hash = :h",
        s=minutes * 60, h=admin_auth.token_sha256(token),
    )


# ═══ Happy paths ══════════════════════════════════════════════════════════


@requires_db
async def test_issue_exchange_and_the_session_it_mints(_db) -> None:
    token, dt = await claimed()
    r = await mint(token, dt)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    grant = r.json()["grant"]
    assert len(grant) == 64 and int(grant, 16) >= 0  # 256 bits, hex
    expires = datetime.fromisoformat(r.json()["expires_at"])

    rows = await sql("SELECT grant_hash, device_id, label, created_at, expires_at, "
                     "last_used_at FROM admin_phone_grants")
    assert len(rows) == 1
    stored = rows[0]
    assert stored.grant_hash == admin_auth.token_sha256(grant) != grant
    assert (stored.device_id, stored.label, stored.last_used_at) == (DEVICE, LABEL, None)
    assert stored.expires_at - stored.created_at == timedelta(hours=24)
    assert stored.expires_at == expires

    r = await exchange(dt, grant)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert datetime.fromisoformat(body["grant_expires_at"]) == expires
    assert body["idle_timeout_min"] == admin_auth.SESSION_IDLE_TIMEOUT_MIN
    assert 0 < body["idle_in_sec"] <= admin_auth.SESSION_IDLE_TIMEOUT_MIN * 60
    minted = body["token"]
    assert minted != token and await is_live(minted)

    (session,) = await sql(
        "SELECT label, signed_in_at, phone_grant_id FROM admin_sessions WHERE token_hash = :h",
        h=admin_auth.token_sha256(minted),
    )
    assert session.label == f"phone: {LABEL}"
    assert session.signed_in_at is None and session.phone_grant_id is not None
    (used,) = await sql("SELECT last_used_at FROM admin_phone_grants")
    assert used.last_used_at is not None

    # The minted session is a real admin session: it can revoke a session.
    async with web_client(headers={**RW, **bearer(minted)}) as c:
        assert (await c.get("/api/auth/sessions")).status_code == 200


@requires_db
async def test_list_and_admin_revoke(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    minted = (await exchange(dt, grant)).json()["token"]

    async with web_client(headers={**RW, **bearer(token)}) as c:
        r = await c.get("/api/auth/phone-grants")
        assert r.status_code == 200
        (entry,) = r.json()["grants"]
        assert set(entry) == {
            "id", "device_id", "label", "created_at", "expires_at", "last_used_at", "live"
        }
        assert (entry["device_id"], entry["label"], entry["live"]) == (DEVICE, LABEL, True)
        assert entry["last_used_at"] is not None
        assert grant not in r.text and admin_auth.token_sha256(grant) not in r.text

        assert (await c.delete(f"/api/auth/phone-grants/{entry['id']}")).json() == {"ok": True}
        assert (await c.delete(f"/api/auth/phone-grants/{entry['id']}")).status_code == 404
        assert (await c.get("/api/auth/phone-grants")).json() == {"grants": []}

    assert not await is_live(minted)  # the sessions it minted end with it
    assert (await exchange(dt, grant)).status_code == 401


@requires_db
async def test_phone_revoke_is_idempotent_and_ends_the_minted_session(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    minted = (await exchange(dt, grant)).json()["token"]

    first = await revoke(grant)
    again = await revoke(grant)
    never = await revoke("0" * 64)
    empty = await revoke("")
    for r in (first, again, never, empty):
        assert r.status_code == 200 and r.json() == {"ok": True}
    assert await sql("SELECT 1 FROM admin_phone_grants") == []
    assert not await is_live(minted)
    assert await is_live(token)  # the password session is not touched
    assert (await exchange(dt, grant)).status_code == 401


# ═══ Freshness ════════════════════════════════════════════════════════════


@requires_db
async def test_a_sign_in_six_minutes_old_is_refused(_db) -> None:
    token, dt = await claimed()
    await age_sign_in(token, 4)
    assert (await mint(token, dt)).status_code == 200
    await age_sign_in(token, 6)
    r = await mint(token, dt, device_id="other-phone")
    assert r.status_code == 403
    assert "password" in r.json()["detail"]
    assert len(await sql("SELECT 1 FROM admin_phone_grants")) == 1


@requires_db
async def test_a_kept_alive_session_is_still_stale(_db) -> None:
    token, dt = await claimed()
    await age_sign_in(token, 6)
    # An admin action restarts the idle hour (what the activity middleware
    # stamps on a 2xx admin write) — and must not refresh the sign-in.
    assert await admin_auth.stamp_admin_activity(token)
    async with web_client(headers={**RW, **bearer(token)}) as c:
        other = await login()
        assert (await c.delete(
            f"/api/auth/sessions/{admin_auth.token_sha256(other)}"
        )).status_code == 200
    (row,) = await sql(
        "SELECT last_used_at, signed_in_at FROM admin_sessions WHERE token_hash = :h",
        h=admin_auth.token_sha256(token),
    )
    assert row.last_used_at - row.signed_in_at > timedelta(minutes=5)
    assert await is_live(token)
    assert (await mint(token, dt)).status_code == 403


@requires_db
async def test_a_grant_minted_session_cannot_mint_a_grant(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    minted = (await exchange(dt, grant)).json()["token"]
    r = await mint(minted, dt)
    assert r.status_code == 403
    r = await mint(minted, dt, device_id="another-phone")
    assert r.status_code == 403
    assert (await exchange(dt, grant)).status_code == 200  # the original still stands


# ═══ Expiry, binding, replacement ═════════════════════════════════════════


@requires_db
async def test_an_expired_grant_is_refused_and_deleted(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    await sql("UPDATE admin_phone_grants SET expires_at = now() - interval '1 second'")
    r = await exchange(dt, grant)
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    assert await sql("SELECT 1 FROM admin_phone_grants") == []


@requires_db
async def test_a_device_id_that_does_not_match_is_refused(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    await mint(await login(), dt, device_id="second-phone")
    for device_id in ("second-phone", "never-seen", "x" * 65):
        admin_auth.LOGIN_BACKOFF.reset()
        r = await exchange(dt, grant, device_id=device_id)
        assert r.status_code == 401, device_id
        assert "expired" not in r.json()["detail"]
    admin_auth.LOGIN_BACKOFF.reset()
    assert (await exchange(dt, grant)).status_code == 200


@requires_db
async def test_a_reissue_replaces_the_old_grant(_db) -> None:
    token, dt = await claimed()
    old = (await mint(token, dt)).json()["grant"]
    old_session = (await exchange(dt, old)).json()["token"]
    new = (await mint(token, dt, label="renamed")).json()["grant"]
    assert new != old
    rows = await sql("SELECT device_id, label FROM admin_phone_grants")
    assert [(r.device_id, r.label) for r in rows] == [(DEVICE, "renamed")]
    assert not await is_live(old_session)
    admin_auth.LOGIN_BACKOFF.reset()
    assert (await exchange(dt, old)).status_code == 401
    admin_auth.LOGIN_BACKOFF.reset()
    assert (await exchange(dt, new)).status_code == 200


# ═══ Password change and --reset-admin ════════════════════════════════════


@requires_db
async def test_a_password_change_deletes_every_grant(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    minted = (await exchange(dt, grant)).json()["token"]
    await mint(token, dt, device_id="second-phone")
    async with web_client(headers={**RW, **bearer(token)}) as c:
        r = await c.post(
            "/api/auth/password",
            json={"old_password": STRONG_PW, "new_password": "another-strong-one"},
        )
    assert r.status_code == 200, r.text
    assert await sql("SELECT 1 FROM admin_phone_grants") == []
    assert not await is_live(minted)
    assert await is_live(token)
    assert (await exchange(dt, grant)).status_code == 401


@requires_db
async def test_a_password_change_from_a_grant_session_keeps_that_session(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    minted = (await exchange(dt, grant)).json()["token"]
    async with web_client(headers={**RW, **bearer(minted)}) as c:
        r = await c.post(
            "/api/auth/password",
            json={"old_password": STRONG_PW, "new_password": "another-strong-one"},
        )
    assert r.status_code == 200, r.text
    assert await sql("SELECT 1 FROM admin_phone_grants") == []
    assert await is_live(minted)
    assert not await is_live(token)


@requires_db
async def test_reset_admin_deletes_every_grant(_db) -> None:
    token, dt = await claimed()
    await mint(token, dt)
    await mint(token, dt, device_id="second-phone")
    assert len(await sql("SELECT 1 FROM admin_phone_grants")) == 2
    await admin_auth.reset_admin()
    assert await sql("SELECT 1 FROM admin_phone_grants") == []


# ═══ The login's backoff, shared ══════════════════════════════════════════


@requires_db
async def test_a_wrong_grant_throttles_the_password_login(_db) -> None:
    token, dt = await claimed()
    admin_auth.LOGIN_BACKOFF.reset()
    assert (await exchange(dt, "f" * 64)).status_code == 401
    async with web_client() as c:
        r = await c.post("/api/auth/login", json={"password": STRONG_PW})
    assert r.status_code == 429 and "Retry-After" in r.headers


@requires_db
async def test_a_wrong_password_throttles_the_exchange(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    admin_auth.LOGIN_BACKOFF.reset()
    async with web_client() as c:
        assert (await c.post("/api/auth/login", json={"password": "wrong-pass"})).status_code == 401
    r = await exchange(dt, grant)
    assert r.status_code == 429 and "Retry-After" in r.headers
    admin_auth.LOGIN_BACKOFF.reset()
    assert (await exchange(dt, grant)).status_code == 200


@requires_db
async def test_good_exchanges_leave_the_password_backoff_climbing(_db) -> None:
    """Wrong passwords interleaved with good exchanges still lengthen the
    per-address wait: an exchange hands back its own attempt only."""
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    admin_auth.LOGIN_BACKOFF.reset()
    ledger = admin_auth.LOGIN_BACKOFF._failures

    def let_the_wait_pass() -> None:
        # Keep the streak, move its last failure into the past.
        for source, (count, _last) in list(ledger.items()):
            ledger[source] = (count, time.monotonic() - admin_auth.BACKOFF_CAP_SEC)

    for expected in range(1, 5):
        async with web_client() as c:
            r = await c.post("/api/auth/login", json={"password": "wrong-pass"})
        assert r.status_code == 401
        (source,) = ledger
        assert ledger[source][0] == expected
        wait = admin_auth.LOGIN_BACKOFF.retry_after(source)
        assert wait == pytest.approx(
            admin_auth.BACKOFF_BASE_SEC * 2 ** (expected - 1), abs=0.5
        )
        let_the_wait_pass()
        assert (await exchange(dt, grant)).status_code == 200
        assert ledger[source][0] == expected
    admin_auth.LOGIN_BACKOFF.reset()


# ═══ Who may ══════════════════════════════════════════════════════════════


@requires_db
async def test_non_admin_and_unpaired_callers_are_refused(_db) -> None:
    token, dt = await claimed()
    # No Bearer, household token only.
    assert (await mint(None, dt)).status_code == 401
    # An admin Bearer without the household token, or with a wrong one.
    assert (await mint(token, None)).status_code == 401
    assert (await mint(token, "not-the-household-token")).status_code == 401
    # The cookie alone never mints.
    async with web_client(headers=phone(None, dt), cookies={admin_auth.COOKIE_NAME: "x"}) as c:
        r = await c.post("/api/auth/phone-grant", json={"device_id": DEVICE, "label": LABEL})
    assert r.status_code == 401
    assert await sql("SELECT 1 FROM admin_phone_grants") == []

    grant = (await mint(token, dt)).json()["grant"]
    # The exchange needs the household token; a Bearer is no substitute.
    assert (await exchange(None, grant)).status_code == 401
    async with web_client(headers=phone(token, None)) as c:
        r = await c.post(
            "/api/auth/phone-grant/exchange", json={"grant": grant, "device_id": DEVICE}
        )
    assert r.status_code == 401

    # The list and the admin revoke need an admin.
    async with web_client(headers={**RW, HEADER: dt}) as c:
        assert (await c.get("/api/auth/phone-grants")).status_code == 401
        assert (await c.delete("/api/auth/phone-grants/1")).status_code == 401
    assert len(await sql("SELECT 1 FROM admin_phone_grants")) == 1


# ═══ Logs ═════════════════════════════════════════════════════════════════


@requires_db
async def test_no_grant_token_or_password_reaches_the_log(_db, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    token, dt = await claimed()
    r = await mint(token, dt)
    grant = r.json()["grant"]
    minted = (await exchange(dt, grant)).json()["token"]
    admin_auth.LOGIN_BACKOFF.reset()
    wrong = "e" * 64
    await exchange(dt, wrong)
    admin_auth.LOGIN_BACKOFF.reset()
    await exchange(dt, grant, device_id="never-seen")
    await revoke(grant)
    await revoke(wrong)
    logged = caplog.text
    assert "phone grant issued" in logged and "phone grant exchange refused" in logged
    for secret in (STRONG_PW, grant, wrong, token, minted, dt,
                   admin_auth.token_sha256(grant), admin_auth.token_sha256(minted)):
        assert secret not in logged


# ═══ A grant's session ends with the grant ════════════════════════════════


@requires_db
async def test_a_grant_session_ends_at_the_grant_end_even_inside_its_hour(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    # Ten minutes of the grant left: the session's clock says so too.
    await sql("UPDATE admin_phone_grants SET expires_at = now() + interval '10 minutes'")
    r = await exchange(dt, grant)
    assert r.status_code == 200, r.text
    minted = r.json()["token"]
    assert r.json()["idle_in_sec"] <= 600
    (row,) = await sql(
        "SELECT s.hard_expires_at, g.expires_at FROM admin_sessions s "
        "JOIN admin_phone_grants g ON g.id = s.phone_grant_id WHERE s.token_hash = :h",
        h=admin_auth.token_sha256(minted),
    )
    assert row.hard_expires_at == row.expires_at
    # An admin action inside the hour does not carry it past the grant's end.
    assert await admin_auth.stamp_admin_activity(minted)
    (after,) = await sql(
        "SELECT expires_at, hard_expires_at FROM admin_sessions WHERE token_hash = :h",
        h=admin_auth.token_sha256(minted),
    )
    assert after.expires_at <= after.hard_expires_at
    # The grant's end passes: refused, though its last action was just now.
    await sql(
        "UPDATE admin_sessions SET hard_expires_at = now() - interval '1 second' "
        "WHERE token_hash = :h", h=admin_auth.token_sha256(minted),
    )
    assert not await is_live(minted)
    assert not await admin_auth.stamp_admin_activity(minted)
    async with web_client(headers={**RW, **bearer(minted)}) as c:
        assert (await c.get("/api/auth/sessions")).status_code == 401
    assert await is_live(token)  # a password sign-in has no hard end


@requires_db
async def test_expired_grants_are_cleaned_up_with_the_sessions(_db) -> None:
    token, dt = await claimed()
    grant = (await mint(token, dt)).json()["grant"]
    await mint(token, dt, device_id="second-phone")
    await sql("UPDATE admin_phone_grants SET expires_at = now() - interval '1 second' "
              "WHERE device_id = :d", d=DEVICE)
    await login()  # what prunes ended sessions prunes ended grants too
    rows = await sql("SELECT device_id FROM admin_phone_grants")
    assert [r.device_id for r in rows] == ["second-phone"]
    assert (await exchange(dt, grant)).status_code == 401
