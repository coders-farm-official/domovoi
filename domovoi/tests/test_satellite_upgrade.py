"""Tests for Feature #10 — satellite code-channel upgrades.

Covers the four domovoi-side surfaces the in-field upgrade flow leans on:

* the satellite-code file channel (`/v1/satellite-code/manifest` +
  `/v1/satellite-code/{path}`) — the allowlist / denylist / traversal guards
  that keep caches, backups, and `.env` secrets from ever leaving the host;
* the `/v1/admin/satellite/upgrade` write endpoint — 503/404 error shapes and
  the `intents_log` audit row, mirroring `admin_satellite_restart`;
* the hello-frame `synced_sha` caching in `StreamSession._on_control`;
* `git_version` — the never-raises contract (missing git → "unknown",
  dirty tree → "-dirty" suffix, failed pull → structured error);
* `admin_snapshot` exposing `satellite_synced_sha` + `domovoi_version`.

Integrity here is the per-file MANIFEST sha256, never the git SHA — these
tests pin the file-channel guards, not the SHA, as the security boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import PurePosixPath
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import git_version
from domovoi.db.session import engine
from domovoi.main import _SAT_CODE_EXT_ALLOW, app
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import TABLES_TO_TRUNCATE, requires_db


@pytest_asyncio.fixture(autouse=True)
async def _truncate_between_tests():
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )
    yield


# ─── Satellite-code manifest endpoint ────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_satellite_code_manifest_lists_only_allowlisted_files() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/satellite-code/manifest")
    assert r.status_code == 200, r.text
    manifest = r.json()
    assert isinstance(manifest, dict)
    # The Pi's own entrypoint must be carried so it can self-upgrade.
    assert "client.py" in manifest
    # Every value is a sha256 hex digest (64 chars), never the git SHA.
    for rel, sha in manifest.items():
        assert isinstance(sha, str) and len(sha) == 64
        # Denylist: no caches, backups, bytecode, or bundled audio.
        assert "__pycache__" not in rel
        assert not rel.endswith((".mp3", ".bak", ".pyc"))
        # Allowlist: every key's extension is in the allowlist.
        assert (
            PurePosixPath(rel).suffix in _SAT_CODE_EXT_ALLOW
        ), f"{rel!r} has a non-allowlisted suffix"


# ─── Satellite-code file endpoint ────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_satellite_code_file_serves_allowlisted_file() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/satellite-code/client.py")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream"
    # The body is the real source — sanity-check it looks like the file.
    assert b"def " in r.content or b"import " in r.content


@requires_db
@pytest.mark.asyncio
async def test_satellite_code_file_rejects_traversal() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # A .py target that climbs out of satellite/ into the repo root.
            # The dots are percent-encoded so httpx/Starlette don't collapse
            # the `..` before routing — the encoded segment reaches the
            # `{path:path}` param decoded as `..`, exercising the handler's
            # `relative_to` traversal guard (not the extension check; .py is
            # allowlisted).
            r = await client.get(
                "/v1/satellite-code/%2e%2e/domovoi/main.py"
            )
    assert r.status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_satellite_code_file_rejects_non_allowlisted_suffix() -> None:
    """A real file that exists under satellite/ but has a non-allowlisted
    suffix (the bundled satellite/sounds/network_issues.mp3) must 404 — the
    .mp3 bundle is the sounds channel's job, not the code channel's."""
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/satellite-code/sounds/network_issues.mp3")
    assert r.status_code == 404


@requires_db
@pytest.mark.asyncio
async def test_satellite_code_file_404_for_missing_allowlisted_file() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/satellite-code/does_not_exist.py")
    assert r.status_code == 404


# ─── admin/satellite/upgrade ─────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_admin_satellite_upgrade_503_with_no_sessions() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        app.state.active_sessions = {}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/satellite/upgrade", json={"room_id": "kitchen"}
            )
    assert r.status_code == 503
    assert "no satellites" in r.json()["detail"].lower()


@requires_db
@pytest.mark.asyncio
async def test_admin_satellite_upgrade_404_for_unknown_room() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        fake = type(
            "FakeSession", (), {"room_id": "kitchen", "request_upgrade": AsyncMock()}
        )()
        app.state.active_sessions = {"kitchen": fake}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/satellite/upgrade", json={"room_id": "garage"}
            )
    assert r.status_code == 404
    assert "garage" in r.json()["detail"]


@requires_db
@pytest.mark.asyncio
async def test_admin_satellite_upgrade_requests_and_logs() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        upgrade_mock = AsyncMock()
        kitchen = type(
            "FakeSession", (), {"room_id": "kitchen", "request_upgrade": upgrade_mock}
        )()
        app.state.active_sessions = {"kitchen": kitchen}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/satellite/upgrade", json={"room_id": "kitchen"}
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["requested"] is True
        assert body["room_id"] == "kitchen"
        assert "expected_sha" in body
        # The endpoint forwarded the upgrade request to the live session.
        upgrade_mock.assert_awaited_once()

    # And it wrote the synthetic-turn audit row, like admin_satellite_restart.
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT room_id, transcript, matched_handler FROM intents_log "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).all()
    assert rows
    assert rows[0][0] == "kitchen"
    assert rows[0][1] == "[ui] upgrade satellite"
    assert rows[0][2] == "satellite"


# ─── hello-frame synced_sha caching ──────────────────────────────────────


class _FakeAppState:
    def __init__(self) -> None:
        self.satellite_full_duplex: dict[str, bool] = {}
        self.satellite_synced_sha: dict[str, str | None] = {}
        self.satellite_sat_type: dict[str, str] = {}
        self.satellite_mic_enabled: dict[str, bool] = {}


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeAppState()


class _FakeWS:
    """Minimal WebSocket stand-in — `_on_control`'s hello branch only reaches
    `self.ws.app.state`, never the socket itself."""

    def __init__(self) -> None:
        self.app = _FakeApp()


@pytest.mark.asyncio
async def test_hello_caches_synced_sha() -> None:
    ws = _FakeWS()
    session = StreamSession(ws, "kitchen")
    await session._on_control(
        {"type": "hello", "synced_sha": "abc123", "supports_full_duplex": True}
    )
    assert ws.app.state.satellite_synced_sha["kitchen"] == "abc123"
    assert ws.app.state.satellite_full_duplex["kitchen"] is True


@pytest.mark.asyncio
async def test_hello_caches_none_when_never_synced() -> None:
    ws = _FakeWS()
    session = StreamSession(ws, "garage")
    await session._on_control({"type": "hello", "supports_full_duplex": False})
    assert ws.app.state.satellite_synced_sha["garage"] is None


# ─── git_version (never-raises contract) ─────────────────────────────────


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


@pytest.mark.asyncio
async def test_current_sha_unknown_when_git_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args, **kwargs):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(git_version.subprocess, "run", _boom)
    sha = await git_version.current_sha()
    assert sha == "unknown"


@pytest.mark.asyncio
async def test_current_sha_appends_dirty_when_porcelain_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(cmd, **kwargs):
        # cmd is ["git", subcommand, ...]
        sub = cmd[1]
        if sub == "rev-parse":
            return _completed(cmd, 0, stdout="deadbee\n")
        if sub == "status":
            # Non-empty porcelain → dirty tree.
            return _completed(cmd, 0, stdout=" M domovoi/main.py\n")
        return _completed(cmd, 0)

    monkeypatch.setattr(git_version.subprocess, "run", _fake_run)
    sha = await git_version.current_sha()
    assert sha == "deadbee-dirty"


@pytest.mark.asyncio
async def test_current_sha_clean_tree_no_dirty_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(cmd, **kwargs):
        sub = cmd[1]
        if sub == "rev-parse":
            return _completed(cmd, 0, stdout="cafef00\n")
        if sub == "status":
            return _completed(cmd, 0, stdout="")
        return _completed(cmd, 0)

    monkeypatch.setattr(git_version.subprocess, "run", _fake_run)
    sha = await git_version.current_sha()
    assert sha == "cafef00"


@pytest.mark.asyncio
async def test_pull_reports_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(cmd, **kwargs):
        # Simulate `git pull --ff-only` refusing on a dirty/diverged tree.
        return _completed(
            cmd, 1, stderr="error: Your local changes would be overwritten\n"
        )

    monkeypatch.setattr(git_version.subprocess, "run", _fake_run)
    result = await git_version.pull()
    assert result["pulled"] is False
    assert result["new_sha"] is None
    assert "local changes" in result["error"]


# ─── admin_snapshot surfaces the new keys ────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_admin_snapshot_includes_version_keys() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        app.state.satellite_synced_sha = {"kitchen": "abc123"}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/admin/snapshot")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "satellite_synced_sha" in body
    assert body["satellite_synced_sha"]["kitchen"] == "abc123"
    assert "domovoi_version" in body
    assert isinstance(body["domovoi_version"], str)
