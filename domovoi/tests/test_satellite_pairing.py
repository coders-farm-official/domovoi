"""Satellite pairing tokens — WS trust-on-first-use auth (V002).

Covers:
  * the ``SatellitePairingRepository`` CRUD (get/pair/touch/reset);
  * all FIVE hello-frame validation cases in
    ``StreamSession._validate_pairing`` + the strict-mode variant of case 5;
  * the admin-gated reset endpoint (unauth refused once a credential exists,
    authed reset deletes the row, reset of an unpaired room is a no-op).

The validation cases (see domovoi/streaming.py):
  1. token, no row        -> PAIR (claim), accept
  2. token, row match     -> accept, bump last_seen_at
  3. token, row mismatch  -> REFUSE (impostor / wrong token)
  4. no token, row exists -> REFUSE (paired room requires its token)
  5. no token, no row     -> accept (older) UNLESS strict, then REFUSE
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.admin_auth import token_sha256
from domovoi.config import settings
from domovoi.db.repositories import SatellitePairingRepository
from domovoi.db.session import SessionLocal, engine, session_scope
from domovoi.main import app as core_app
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import TABLES_TO_TRUNCATE, requires_db

pytestmark = requires_db

TOKEN = "a" * 64            # a plausible secrets.token_hex(32) value
OTHER_TOKEN = "b" * 64


@pytest_asyncio.fixture(autouse=True)
async def _clean_pairings(tmp_path, monkeypatch):
    """Truncate the pairing (+ admin) tables before and after each test, and
    isolate the admin config dir (setup-code file) into a tmp dir."""
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    admin_auth.LOGIN_BACKOFF.reset()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellite_pairings, admin_auth, admin_sessions CASCADE"))
    yield
    admin_auth.LOGIN_BACKOFF.reset()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellite_pairings, admin_auth, admin_sessions CASCADE"))


# ─── Test doubles ──────────────────────────────────────────────────────────


class _FakeWS:
    """Minimal WebSocket stand-in for ``_validate_pairing`` — it only ever
    reaches ``send_text`` (on a reject). Records the frames it's handed."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


def _session(room_id: str = "kitchen") -> StreamSession:
    return StreamSession(_FakeWS(), room_id)  # type: ignore[arg-type]


async def _seed_pairing(room_id: str, token: str) -> None:
    """Commit a pairing row so ``_validate_pairing``'s own session sees it."""
    async with session_scope() as s:
        await SatellitePairingRepository(s).pair(room_id, token_sha256(token))


# ─── Missing-table graceful detection ───────────────────────────────────────


def test_missing_pairing_table_detection() -> None:
    """A not-yet-applied migration (satellite_pairings absent) is detected so it
    logs a one-time actionable hint, while unrelated errors are not."""
    from domovoi.streaming import _is_missing_pairing_table

    assert _is_missing_pairing_table(
        Exception('relation "satellite_pairings" does not exist')
    ) is True

    class _UndefinedTableError(Exception):
        pass

    _UndefinedTableError.__name__ = "UndefinedTableError"
    wrapped = Exception("wrapped")
    wrapped.orig = _UndefinedTableError('relation "satellite_pairings" does not exist')
    assert _is_missing_pairing_table(wrapped) is True

    assert _is_missing_pairing_table(Exception("connection refused")) is False
    assert _is_missing_pairing_table(
        Exception('relation "other_table" does not exist')
    ) is False


# ─── Repository ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repo_get_returns_none_then_row_after_pair() -> None:
    async with SessionLocal() as s:
        repo = SatellitePairingRepository(s)
        assert await repo.get_pairing("kitchen") is None
        await repo.pair("kitchen", token_sha256(TOKEN))
        await s.commit()
        row = await repo.get_pairing("kitchen")
    assert row is not None
    token_hash, paired_at, last_seen_at = row
    assert token_hash == token_sha256(TOKEN)
    assert paired_at is not None
    assert last_seen_at is not None  # pair() stamps last_seen too


@pytest.mark.asyncio
async def test_repo_touch_last_seen_advances() -> None:
    async with SessionLocal() as s:
        repo = SatellitePairingRepository(s)
        await repo.pair("kitchen", token_sha256(TOKEN))
        await s.commit()
        before = (await repo.get_pairing("kitchen"))[2]
        await repo.touch_last_seen("kitchen")
        await s.commit()
        after = (await repo.get_pairing("kitchen"))[2]
    assert after >= before


@pytest.mark.asyncio
async def test_repo_reset_deletes_and_reports() -> None:
    async with SessionLocal() as s:
        repo = SatellitePairingRepository(s)
        await repo.pair("kitchen", token_sha256(TOKEN))
        await s.commit()
        assert await repo.reset_pairing("kitchen") is True
        await s.commit()
        assert await repo.get_pairing("kitchen") is None
        # Second reset is a no-op → False.
        assert await repo.reset_pairing("kitchen") is False


# ─── Validation: the five hello cases ───────────────────────────────────────


@pytest.mark.asyncio
async def test_case1_token_no_row_pairs_and_accepts() -> None:
    sess = _session("kitchen")
    accepted = await sess._validate_pairing({"pairing_token": TOKEN})
    assert accepted is True
    # A row was claimed with the token's sha256.
    async with SessionLocal() as s:
        row = await SatellitePairingRepository(s).get_pairing("kitchen")
    assert row is not None and row[0] == token_sha256(TOKEN)


@pytest.mark.asyncio
async def test_case2_token_matches_accepts_and_touches() -> None:
    await _seed_pairing("kitchen", TOKEN)
    sess = _session("kitchen")
    accepted = await sess._validate_pairing({"pairing_token": TOKEN})
    assert accepted is True
    assert sess.ws.sent == []  # no error frame on accept


@pytest.mark.asyncio
async def test_case3_token_mismatch_refuses() -> None:
    await _seed_pairing("kitchen", TOKEN)
    sess = _session("kitchen")
    accepted = await sess._validate_pairing({"pairing_token": OTHER_TOKEN})
    assert accepted is False
    assert sess.ws.sent, "a pairing_rejected error frame should be sent"
    assert "pairing_rejected" in sess.ws.sent[-1]
    # The stored hash is UNTOUCHED — the impostor did not overwrite it.
    async with SessionLocal() as s:
        row = await SatellitePairingRepository(s).get_pairing("kitchen")
    assert row[0] == token_sha256(TOKEN)


@pytest.mark.asyncio
async def test_case4_no_token_but_paired_refuses() -> None:
    await _seed_pairing("kitchen", TOKEN)
    sess = _session("kitchen")
    accepted = await sess._validate_pairing({})  # no pairing_token
    assert accepted is False
    assert "pairing_rejected" in sess.ws.sent[-1]


@pytest.mark.asyncio
async def test_case5_no_token_no_row_accepts_when_lenient() -> None:
    sess = _session("garage")
    accepted = await sess._validate_pairing({})
    assert accepted is True
    assert sess.ws.sent == []
    # Nothing was written — an unpaired room stays unpaired.
    async with SessionLocal() as s:
        assert await SatellitePairingRepository(s).get_pairing("garage") is None


@pytest.mark.asyncio
async def test_case5_no_token_no_row_refuses_when_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "satellite_pairing_strict", True)
    sess = _session("garage")
    accepted = await sess._validate_pairing({})
    assert accepted is False
    assert "pairing_rejected" in sess.ws.sent[-1]


@pytest.mark.asyncio
async def test_strict_still_pairs_a_token_bearing_first_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict mode only refuses TOKENLESS unpaired rooms — a satellite that
    presents a token still pairs (case 1), so an all-paired fleet bootstraps."""
    monkeypatch.setattr(settings, "satellite_pairing_strict", True)
    sess = _session("den")
    accepted = await sess._validate_pairing({"pairing_token": TOKEN})
    assert accepted is True
    async with SessionLocal() as s:
        assert await SatellitePairingRepository(s).get_pairing("den") is not None


@pytest.mark.asyncio
async def test_blank_token_treated_as_absent() -> None:
    """An empty-string pairing_token is treated as no token — a paired room
    is still protected (case 4 refuse), not case-1 re-paired."""
    await _seed_pairing("kitchen", TOKEN)
    sess = _session("kitchen")
    accepted = await sess._validate_pairing({"pairing_token": "   "})
    assert accepted is False


# ─── Admin-gated reset endpoint ─────────────────────────────────────────────


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test")


async def _claim_admin() -> str:
    """Set an admin credential (so the pre-setup LAN-trust grace no longer
    applies) and mint a bearer token — directly via admin_auth primitives,
    since the setup/login HTTP flow lives on the WEB app, not the core."""
    async with session_scope() as s:
        await admin_auth.set_password(s, "correct-horse-battery")
        token = await admin_auth.create_session(s, "test")
    return token


@pytest.mark.asyncio
async def test_reset_endpoint_refuses_unauth_once_admin_exists() -> None:
    await _seed_pairing("kitchen", TOKEN)
    await _claim_admin()  # a credential now exists → no pre-setup grace
    async with _core() as client:
        r = await client.delete("/v1/admin/satellites/kitchen/pairing")
    assert r.status_code == 401
    # Row survives an unauthorized attempt.
    async with SessionLocal() as s:
        assert await SatellitePairingRepository(s).get_pairing("kitchen") is not None


@pytest.mark.asyncio
async def test_reset_endpoint_deletes_row_when_authed() -> None:
    await _seed_pairing("kitchen", TOKEN)
    token = await _claim_admin()
    async with _core() as client:
        r = await client.delete(
            "/v1/admin/satellites/kitchen/pairing",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    assert r.json() == {"room_id": "kitchen", "reset": True}
    async with SessionLocal() as s:
        assert await SatellitePairingRepository(s).get_pairing("kitchen") is None


@pytest.mark.asyncio
async def test_reset_endpoint_noop_for_unpaired_room() -> None:
    token = await _claim_admin()
    async with _core() as client:
        r = await client.delete(
            "/v1/admin/satellites/nowhere/pairing",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    assert r.json() == {"room_id": "nowhere", "reset": False}
