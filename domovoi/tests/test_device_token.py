"""The household device tier: one token per install, presented as
``X-Device-Token``, accepted by ``require_device`` alongside an admin
Bearer.

Covers, DB-free: the private-file writer (0600), the request-side helpers,
``auth_forward_headers`` carrying the token to the core, the boot hook
mirroring the row to ``~/.domovoi/device-token.txt``, and the
``require_device`` matrix through a mini app over fake primitives.

DB-backed (``requires_db``): a token row + file after the first boot of
EITHER process, the admin read / set / rotate endpoints on the core and
their web mirror against the same table, rotation at setup, and a route
wearing ``require_device`` against the real tables.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.db.session import session_scope
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    _db_sync,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_app,
    core_client,
    db_device_token,
    install_fake_db,
    make_request,
    mini_client,
    web_app,
    web_client,
)
from domovoi.tests.conftest import requires_db
from web.backend.domovoi_client import auth_forward_headers

# ═══ DB-FREE ══════════════════════════════════════════════════════════════


def test_the_mirror_file_ends_with_one_bare_LF(tmp_path, monkeypatch) -> None:
    """Not cosmetic on a Windows dev host. Text mode ends the file
    ``\r\n`` there; every Python reader ``.strip()``s that away, but the
    documented shell idiom does not — ``TOKEN=$(cat
    ~/.domovoi/device-token.txt)`` strips trailing newlines and NOT the CR,
    so the token would ride in ``X-Device-Token`` with a stray carriage
    return and never match (docs/SETUP_RUNBOOK.md,
    docs/PLUGIN_DEVELOPMENT.md)."""
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    admin_auth.write_device_token_file("Maple Street, 1984!")
    raw = admin_auth.device_token_path().read_bytes()
    assert raw == b"Maple Street, 1984!\n"
    assert b"\r" not in raw
    # ...and the value survives the shell idiom byte for byte.
    assert raw.decode("utf-8").rstrip("\n") == "Maple Street, 1984!"
    assert admin_auth.read_device_token_file() == "Maple Street, 1984!"


def test_write_private_file_asks_for_0600(tmp_path, monkeypatch) -> None:
    """The file is CREATED with mode 0600 (never world-readable for an
    instant) and an existing one is tightened before the rewrite."""
    seen: dict[str, Any] = {}
    real_open = os.open
    real_chmod = os.chmod

    def spy_open(path, flags, mode=0o777, *a, **kw):
        seen["open_mode"] = mode
        seen["flags"] = flags
        return real_open(path, flags, mode, *a, **kw)

    def spy_chmod(path, mode, *a, **kw):
        seen.setdefault("chmods", []).append(mode)
        return real_chmod(path, mode, *a, **kw)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "chmod", spy_chmod)
    target = tmp_path / "nested" / "secret.txt"
    admin_auth.write_private_file(target, "one\n")
    assert seen["open_mode"] == 0o600
    assert seen["flags"] & os.O_CREAT and seen["flags"] & os.O_TRUNC
    assert target.read_text(encoding="utf-8") == "one\n"
    # Rewrite of an existing file: chmod first, then truncate.
    admin_auth.write_private_file(target, "two\n")
    assert seen["chmods"] == [0o600]
    assert target.read_text(encoding="utf-8") == "two\n"
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_device_token_file_goes_through_the_private_writer(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    modes: list[int] = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, *a, **kw):
        modes.append(mode)
        return real_open(path, flags, mode, *a, **kw)

    monkeypatch.setattr(os, "open", spy_open)
    admin_auth.write_device_token_file("a" * 64)
    assert modes == [0o600]
    assert admin_auth.read_device_token_file() == "a" * 64
    assert admin_auth.device_token_path() == tmp_path / "device-token.txt"


def test_auth_forward_headers_carries_the_device_token() -> None:
    req = make_request({HEADER: "abc123", "Authorization": "Bearer t"})
    fwd = auth_forward_headers(req)
    assert fwd["X-Device-Token"] == "abc123"
    assert fwd["Authorization"] == "Bearer t"
    assert fwd["X-Forwarded-For"] == "192.168.1.50"
    assert "X-Device-Token" not in auth_forward_headers(make_request())


def test_device_token_from_request_strips_whitespace() -> None:
    assert admin_auth.device_token_from_request(make_request({HEADER: "  tok  "})) == "tok"
    assert admin_auth.device_token_from_request(make_request({HEADER: "   "})) is None
    assert admin_auth.device_token_from_request(make_request()) is None


@pytest.mark.asyncio
async def test_require_device_matrix_after_setup(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-token")
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: "dev-token"})).status_code == 200
        assert (await c.post("/device", headers=bearer("admin-token"))).status_code == 200
        assert (await c.post("/device")).status_code == 401
        assert (await c.post("/device", headers={HEADER: "stale-token"})).status_code == 401
        assert (await c.post("/device", headers=bearer("stale-admin"))).status_code == 401
        # A valid device token beside a broken Bearer is still a valid device.
        r = await c.post("/device", headers={HEADER: "dev-token", **bearer("stale-admin")})
        assert r.status_code == 200
    async with mini_client(cookies={COOKIE: "admin-token"}) as c:
        # The dashboard cookie alone renders nothing on this tier.
        assert (await c.post("/device")).status_code == 403


@pytest.mark.asyncio
async def test_require_device_keeps_the_pre_setup_grace(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False, device_token="dev-token")
    async with mini_client() as c:
        assert (await c.post("/device")).status_code == 200
        assert (await c.post("/device", headers={HEADER: "anything"})).status_code == 200


@pytest.mark.asyncio
async def test_require_device_fails_closed_when_the_check_raises(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, device_token="dev-token")

    async def boom(_s, _c):
        raise RuntimeError("db down")

    monkeypatch.setattr(admin_auth, "validate_device_token", boom)
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: "dev-token"})).status_code == 401


@pytest.mark.asyncio
async def test_ensure_device_token_mirrors_the_row_to_the_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    install_fake_db(monkeypatch, admin=False)

    async def fake_ensure_row(_s):
        return "b" * 64

    monkeypatch.setattr(admin_auth, "ensure_device_token_row", fake_ensure_row)
    assert await admin_auth.ensure_device_token() == "b" * 64
    assert admin_auth.read_device_token_file() == "b" * 64
    # A stale mirror is overwritten with the row's value.
    admin_auth.write_device_token_file("stale")
    assert await admin_auth.ensure_device_token() == "b" * 64
    assert admin_auth.read_device_token_file() == "b" * 64


@pytest.mark.asyncio
async def test_ensure_device_token_repairs_a_CRLF_mirror_from_an_older_build(
    tmp_path, monkeypatch
) -> None:
    """The fix above only helps a file that gets REWRITTEN.

    Every host that already runs Domovoi on Windows has a mirror file
    ending ``\r\n``, because the old writer used text mode. The boot hook
    compared ``read_device_token_file() != token`` — and that reader
    ``.strip()``s — so the comparison said "same", the file was never
    rewritten, and the CR survived every boot until the next rotation.
    The upgrade has to repair it once, by itself, or the trap the newline
    fix closes stays open on exactly the hosts that hit it."""
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    install_fake_db(monkeypatch, admin=False)
    token = "Maple Street, 1984!"

    async def fake_ensure_row(_s):
        return token

    monkeypatch.setattr(admin_auth, "ensure_device_token_row", fake_ensure_row)

    path = admin_auth.device_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((token + "\r\n").encode("utf-8"))
    # The reader cannot see the problem: this is why a stripped comparison
    # was never going to fix it.
    assert admin_auth.read_device_token_file() == token

    writes = []
    real_write = admin_auth.write_device_token_file
    monkeypatch.setattr(
        admin_auth,
        "write_device_token_file",
        lambda t: (writes.append(t), real_write(t))[1],
    )

    assert await admin_auth.ensure_device_token() == token
    assert path.read_bytes() == (token + "\n").encode("utf-8")
    assert writes == [token]

    # ...and ONE write: the next boot finds the bytes it wanted and leaves
    # the file alone.
    assert await admin_auth.ensure_device_token() == token
    assert writes == [token]


@pytest.mark.asyncio
async def test_ensure_device_token_is_never_fatal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)

    @asynccontextmanager
    async def dead_scope():
        raise ConnectionRefusedError("no database")
        yield  # pragma: no cover

    monkeypatch.setattr(admin_auth, "session_scope", dead_scope)
    assert await admin_auth.ensure_device_token() is None
    assert not admin_auth.device_token_path().exists()


# ═══ DB-BACKED ════════════════════════════════════════════════════════════


# ─── A token row + the 0600 file after the first boot of either process ──


@requires_db
@pytest.mark.asyncio
async def test_core_boot_creates_the_token_row_and_file(_db) -> None:
    assert await db_device_token() is None
    async with core_app.router.lifespan_context(core_app):
        pass
    token = await db_device_token()
    # An eight-word phrase, not hex (2026-09-23): one canonical form, so
    # the row, the 0600 mirror and every client agree byte for byte.
    assert token and admin_auth.normalize_device_token(token) == token
    assert len(token.split("-")) == admin_auth.DEVICE_TOKEN_WORDS
    assert admin_auth.read_device_token_file() == token
    if sys.platform != "win32":
        assert stat.S_IMODE(admin_auth.device_token_path().stat().st_mode) == 0o600
    # A second boot reuses the row — never a second token.
    async with core_app.router.lifespan_context(core_app):
        pass
    assert await db_device_token() == token


@requires_db
def test_web_boot_creates_the_token_row_and_file(_db_sync) -> None:
    from fastapi.testclient import TestClient

    with TestClient(web_app):
        pass
    token = asyncio.run(db_device_token())
    assert token and len(token.split("-")) == admin_auth.DEVICE_TOKEN_WORDS
    assert admin_auth.read_device_token_file() == token


@requires_db
@pytest.mark.asyncio
async def test_both_processes_share_one_row(_db) -> None:
    """Whichever hook runs first mints; the other reads the same token."""
    first = await admin_auth.ensure_device_token()
    second = await admin_auth.ensure_device_token()
    assert first == second == await db_device_token()


# ─── The admin endpoints on the core ──────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_core_device_token_read_and_rotate(_db) -> None:
    async with web_client() as web:
        admin = await claim_admin(web)
    async with core_client() as core:
        r = await core.get("/v1/admin/device-token")
        assert r.status_code == 401
        r = await core.get("/v1/admin/device-token", headers=bearer(admin))
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        assert r.json()["header"] == HEADER
        assert token == await db_device_token() == admin_auth.read_device_token_file()
        # Rotation is Bearer-only.
        r = await core.post("/v1/admin/device-token/rotate")
        assert r.status_code == 401
        r = await core.post("/v1/admin/device-token/rotate", headers=bearer(admin))
        assert r.status_code == 200, r.text
        rotated = r.json()["token"]
        assert rotated != token and r.json()["rotated"] is True
        assert await db_device_token() == rotated == admin_auth.read_device_token_file()
        r = await core.get("/v1/admin/device-token", headers=bearer(admin))
        assert r.json()["token"] == rotated
    # The old token is refused from now on.
    async with session_scope() as s:
        assert await admin_auth.validate_device_token(s, token) is False
        assert await admin_auth.validate_device_token(s, rotated) is True
    async with AsyncClient(
        transport=ASGITransport(app=core_app), base_url="http://test", cookies={COOKIE: admin}
    ) as core:
        # The cookie renders the read, never the rotation.
        assert (await core.get("/v1/admin/device-token")).status_code == 200
        assert (await core.post("/v1/admin/device-token/rotate")).status_code == 403


@requires_db
@pytest.mark.asyncio
async def test_device_token_endpoints_are_closed_before_setup(_db) -> None:
    await admin_auth.ensure_device_token()
    body = {"token": "a-perfectly-fine-token"}
    async with core_client() as core:
        assert (await core.get("/v1/admin/device-token")).status_code == 501
        assert (await core.post("/v1/admin/device-token/rotate")).status_code == 501
        assert (await core.post("/v1/admin/device-token", json=body)).status_code == 501
    async with web_client() as web:
        assert (await web.get("/api/auth/device-token")).status_code == 501
        assert (await web.post("/api/auth/device-token/rotate")).status_code == 501
        assert (await web.post("/api/auth/device-token", json=body)).status_code == 501


@requires_db
@pytest.mark.asyncio
async def test_setup_rotates_the_token_minted_before_it(_db) -> None:
    before = await admin_auth.ensure_device_token()
    async with web_client() as web:
        await claim_admin(web)
    after = await db_device_token()
    assert after is not None and after != before
    assert admin_auth.read_device_token_file() == after


# ─── A route wearing require_device, on the real tables ───────────────────


@requires_db
@pytest.mark.asyncio
async def test_require_device_route_against_the_real_tables(_db) -> None:
    await admin_auth.ensure_device_token()
    async with mini_client() as c:
        # Pre-setup: the LAN grace holds.
        assert (await c.post("/device")).status_code == 200
    async with web_client() as web:
        admin = await claim_admin(web)
    device = await db_device_token()  # rotated at setup
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: device})).status_code == 200
        assert (await c.post("/device", headers=bearer(admin))).status_code == 200
        assert (await c.post("/device")).status_code == 401
        assert (await c.post("/device", headers={HEADER: "0" * 64})).status_code == 401
    # And after a rotation the old token is a stale one.
    async with core_client() as core:
        r = await core.post("/v1/admin/device-token/rotate", headers=bearer(admin))
        new = r.json()["token"]
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: device})).status_code == 401
        assert (await c.post("/device", headers={HEADER: new})).status_code == 200


# ─── The phrase format, and the install that predates it ─────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_rotation_issues_the_phrase_format(_db) -> None:
    """Rotation is how an existing install MOVES to a phrase, so the
    rotate endpoints have to mint the new shape, not just a new value."""
    async with web_client() as web:
        admin = await claim_admin(web)
    async with core_client() as core:
        r = await core.post("/v1/admin/device-token/rotate", headers=bearer(admin))
        from_core = r.json()["token"]
    async with web_client() as web:
        r = await web.post("/api/auth/device-token/rotate", headers=bearer(admin))
        from_web = r.json()["token"]
    for token in (from_core, from_web):
        assert len(token.split("-")) == admin_auth.DEVICE_TOKEN_WORDS
        assert admin_auth.normalize_device_token(token) == token
    assert from_core != from_web
    assert admin_auth.read_device_token_file() == from_web


@requires_db
@pytest.mark.asyncio
async def test_an_install_that_still_holds_a_hex_token_keeps_working(_db) -> None:
    """The upgrade path for a live box: the row already holds 64 hex
    characters and every paired browser and phone holds the same string.
    Nothing invalidates them — only a ROTATION changes the format."""
    legacy = "9c1e" * 16
    async with session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO household_device_tokens (id, token, token_hash) "
                "VALUES (1, :t, :h)"
            ),
            {"t": legacy, "h": admin_auth.token_sha256(legacy)},
        )
    # The boot hook leaves it alone — it does not re-mint over a good row.
    assert await admin_auth.ensure_device_token() == legacy
    async with web_client() as web:
        await claim_admin(web)
    # ...but first-run setup rotates, as it always has, so THAT install
    # lands on a phrase. Re-seed to test the steady state.
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE household_device_tokens SET token = :t, token_hash = :h "
                "WHERE id = 1"
            ),
            {"t": legacy, "h": admin_auth.token_sha256(legacy)},
        )
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: legacy})).status_code == 200
        assert (await c.post("/device", headers={HEADER: legacy.upper()})).status_code == 200


@requires_db
@pytest.mark.asyncio
async def test_a_phrase_pairs_however_it_was_typed(_db) -> None:
    async with web_client() as web:
        await claim_admin(web)
    phrase = await db_device_token()
    assert phrase is not None
    async with mini_client() as c:
        for typed in (phrase, phrase.upper(), phrase.replace("-", " "),
                      phrase.replace("-", "_"), f"  {phrase}  "):
            assert (await c.post("/device", headers={HEADER: typed})).status_code == 200


# ─── An admin SETS the token (2026-09-24) ────────────────────────────────

# Spaces, a comma, digits and punctuation: illegal in the raw WebSocket
# subprotocol, silently truncated by the server's own comma split, and
# perfectly fine now that the token is base64url-encoded on that transport.
CHOSEN = "Maple Street, 1984!"


@requires_db
@pytest.mark.asyncio
async def test_core_set_stores_the_chosen_token_verbatim(_db) -> None:
    async with web_client() as web:
        admin = await claim_admin(web)
    before = await db_device_token()
    async with core_client() as core:
        # Same tier as rotate: Bearer-only.
        assert (await core.post("/v1/admin/device-token", json={"token": CHOSEN})).status_code == 401
        r = await core.post(
            "/v1/admin/device-token", json={"token": CHOSEN}, headers=bearer(admin)
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"token": CHOSEN, "header": HEADER, "rotated": True}
        # Read back through the OTHER endpoint: same row, same bytes.
        r = await core.get("/v1/admin/device-token", headers=bearer(admin))
        assert r.json()["token"] == CHOSEN
    assert await db_device_token() == CHOSEN != before
    assert admin_auth.read_device_token_file() == CHOSEN
    # Setting IS a rotation: the previous token is refused from now on.
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: CHOSEN})).status_code == 200
        assert (await c.post("/device", headers={HEADER: before})).status_code == 401
        # ...and it is matched EXACTLY — it is not canonical, so no
        # re-spelling of it opens the door.
        assert (await c.post("/device", headers={HEADER: CHOSEN.lower()})).status_code == 401
        assert (await c.post("/device", headers={HEADER: f"  {CHOSEN}  "})).status_code == 200


@requires_db
@pytest.mark.asyncio
async def test_web_set_behaves_identically_and_hits_the_same_row(_db) -> None:
    async with web_client() as web:
        admin = await claim_admin(web)
    # A FRESH client: claim_admin leaves the session cookie on its own, and
    # the cookie is a 403 on this tier rather than the 401 a caller with no
    # credential at all gets.
    async with web_client() as fresh:
        assert (await fresh.post("/api/auth/device-token", json={"token": CHOSEN})).status_code == 401
        r = await fresh.post(
            "/api/auth/device-token", json={"token": CHOSEN}, headers=bearer(admin)
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"token": CHOSEN, "header": HEADER, "rotated": True}
    # The core serves what the web set — one table, two processes.
    async with core_client() as core:
        r = await core.get("/v1/admin/device-token", headers=bearer(admin))
        assert r.json()["token"] == CHOSEN
    assert admin_auth.read_device_token_file() == CHOSEN
    # The cookie renders the read, never the write (the security tier).
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests"}, cookies={COOKIE: admin}
    ) as web:
        assert (await web.get("/api/auth/device-token")).status_code == 200
        r = await web.post("/api/auth/device-token", json={"token": "another-fine-token"})
        assert r.status_code == 403


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bad", "says"),
    [
        ("hunter2", "at least 12 characters"),
        ("   zebra   ", "at least 12 characters"),        # trimmed to 5
        ("x" * 129, "at most 128 characters"),
        ("cafe\u0301-token-abcdef", "non-ASCII"),
        ("cat-\U0001f431-token", "non-ASCII"),
        ("tab\there-token", "control characters"),
        ("line\nbreak-token", "control characters"),
        ("- - - - - - -", "spaces, hyphens and underscores"),
    ],
)
async def test_both_SET_endpoints_400_with_a_usable_message(_db, bad, says) -> None:
    """400, a message a person can act on, the row unchanged — and the
    offending value NOT echoed back (a pydantic constrained field would put
    it in the 422 body as `input`, which is why the body is a plain str)."""
    async with web_client() as web:
        admin = await claim_admin(web)
    good = await db_device_token()
    for client_factory, path in ((core_client, "/v1/admin/device-token"),
                                 (web_client, "/api/auth/device-token")):
        async with client_factory() as c:
            r = await c.post(path, json={"token": bad}, headers=bearer(admin))
            assert r.status_code == 400, (path, r.status_code, r.text)
            detail = r.json()["detail"]
            assert says in detail, (path, detail)
            assert bad not in r.text and bad.strip() not in r.text, (path, r.text)
            assert "Traceback" not in r.text
        assert await db_device_token() == good
        assert admin_auth.read_device_token_file() == good


@requires_db
@pytest.mark.asyncio
async def test_a_set_token_survives_the_file_mirror_round_trip(_db) -> None:
    """The mirror is read back with .strip() by every harness and by the
    boot hook, so a stored token with edge whitespace would make
    `ensure_device_token` rewrite the file on every boot. The SET endpoint
    trims, so the row and the file agree and the second boot is a no-op."""
    async with web_client() as web:
        admin = await claim_admin(web)
        r = await web.post(
            "/api/auth/device-token",
            json={"token": f"   {CHOSEN}   "},
            headers=bearer(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["token"] == CHOSEN
    assert await db_device_token() == CHOSEN
    assert admin_auth.read_device_token_file() == CHOSEN
    mtime = admin_auth.device_token_path().stat().st_mtime_ns
    assert await admin_auth.ensure_device_token() == CHOSEN
    assert admin_auth.device_token_path().stat().st_mtime_ns == mtime


@requires_db
@pytest.mark.asyncio
async def test_a_chosen_token_can_be_replaced_by_a_generated_one_again(_db) -> None:
    """rotate still mints a phrase, so a household that regrets its custom
    token has a way back that does not need the dialog."""
    async with web_client() as web:
        admin = await claim_admin(web)
        r = await web.post(
            "/api/auth/device-token", json={"token": CHOSEN}, headers=bearer(admin)
        )
        assert r.json()["token"] == CHOSEN
        r = await web.post("/api/auth/device-token/rotate", headers=bearer(admin))
        back = r.json()["token"]
    assert len(back.split("-")) == admin_auth.DEVICE_TOKEN_WORDS
    assert admin_auth.normalize_device_token(back) == back
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: CHOSEN})).status_code == 401
        # ...and the phrase is forgiving again.
        assert (await c.post("/device", headers={HEADER: back.upper()})).status_code == 200


# ─── The web mirror ───────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_web_device_token_mirror_behaves_like_the_core(_db) -> None:
    async with web_client() as web:
        admin = await claim_admin(web)
    async with web_client() as fresh:
        assert (await fresh.get("/api/auth/device-token")).status_code == 401
        r = await fresh.get("/api/auth/device-token", headers=bearer(admin))
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        assert token == await db_device_token()
        assert (await fresh.post("/api/auth/device-token/rotate")).status_code == 401
        r = await fresh.post("/api/auth/device-token/rotate", headers=bearer(admin))
        assert r.status_code == 200, r.text
        rotated = r.json()["token"]
        assert rotated != token
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests"}, cookies={COOKIE: admin}
    ) as web:
        assert (await web.get("/api/auth/device-token")).json()["token"] == rotated
        assert (await web.post("/api/auth/device-token/rotate")).status_code == 403
    # Same table: the core now serves the token the web rotated.
    async with core_client() as core:
        r = await core.get("/v1/admin/device-token", headers=bearer(admin))
        assert r.json()["token"] == rotated
    assert admin_auth.read_device_token_file() == rotated
