"""WEB-4 — the voice registry answers to an admin session, and a Piper
upload is metered to disk instead of being read into memory.

DB-free throughout: the auth gates run over ``install_fake_db``'s fake
primitives, and the upload paths are refused before any handler reaches
Postgres.
"""

from __future__ import annotations

import io

import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient

from domovoi.tests.auth_testkit import bearer, install_fake_db, web_app
from web.backend import middleware as mw
from web.backend.api import voices as voices_api

# Every call the dashboard makes carries it (WEB-6's backstop refuses a
# mutation without it), so the tests speak the same way a browser does.
BROWSER = {"X-Requested-With": "XMLHttpRequest"}

VOICE_MUTATIONS = (
    ("POST", "/api/voices/edge"),
    ("POST", "/api/voices/piper"),
    ("PATCH", "/api/voices/1"),
    ("DELETE", "/api/voices/1"),
)


def _web() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test", headers=BROWSER
    )


def _upload(data: bytes, name: str = "v.onnx") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


# ─── The gates ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path"), VOICE_MUTATIONS)
@pytest.mark.asyncio
async def test_registering_or_removing_a_voice_needs_an_admin_session(
    method, path, monkeypatch
) -> None:
    """Registering an Edge voice, uploading a Piper model, renaming a voice
    or deleting one all answer 401 without an admin session."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    async with _web() as c:
        assert (await c.request(method, path, json={})).status_code == 401


@pytest.mark.asyncio
async def test_the_dashboard_cookie_alone_cannot_change_the_voice_registry(
    monkeypatch,
) -> None:
    """A cookie renders GET state; changing the registry needs the Bearer."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    async with AsyncClient(
        transport=ASGITransport(app=web_app),
        base_url="http://test",
        headers=BROWSER,
        cookies={"domovoi_admin": "admin-token"},
    ) as c:
        assert (await c.delete("/api/voices/1")).status_code == 403


@pytest.mark.asyncio
async def test_listing_voices_stays_open(monkeypatch) -> None:
    """Only the mutations moved: the list is still a read anyone on the
    daily surface can render (it feeds the Settings page's voice picker)."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    from fastapi.routing import iter_route_contexts

    listed = [
        rc for rc in iter_route_contexts(web_app.routes)
        if getattr(rc, "path", None) == "/api/voices"
        and "GET" in (getattr(rc, "methods", None) or set())
    ]
    assert listed, "GET /api/voices is missing"
    names = [d.call.__name__ for d in listed[0].dependant.dependencies if d.call]
    assert "require_admin_mutation" not in names


# ─── The byte budgets ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_model_over_the_budget_is_refused_before_it_is_all_read(
    tmp_path, monkeypatch
) -> None:
    """The refusal lands while the upload is still arriving: the source is
    only read as far as the budget, and nothing is left on disk."""
    monkeypatch.setattr(voices_api, "_voices_dir", lambda: tmp_path)
    monkeypatch.setattr(voices_api, "_MAX_ONNX_BYTES", 100)
    monkeypatch.setattr(voices_api, "_UPLOAD_CHUNK_BYTES", 16)
    source = io.BytesIO(b"x" * 10_000)
    upload = UploadFile(file=source, filename="v.onnx")

    with pytest.raises(Exception) as excinfo:
        await voices_api._save_under_budget(
            upload, tmp_path / "v.onnx.part", 100, "model file"
        )
    assert getattr(excinfo.value, "status_code", None) == 413
    assert source.tell() < 1_000, "the whole upload was read before refusing"
    assert list(tmp_path.iterdir()) == [], "a refused upload left a file behind"


@pytest.mark.asyncio
async def test_a_model_inside_the_budget_is_written_whole(tmp_path) -> None:
    dest = tmp_path / "v.onnx.part"
    written = await voices_api._save_under_budget(
        _upload(b"y" * 5_000), dest, 10_000, "model file"
    )
    assert written == 5_000
    assert dest.read_bytes() == b"y" * 5_000


@pytest.mark.asyncio
async def test_a_disk_error_says_which_one(tmp_path) -> None:
    """Disk full, permission denied, path gone — the operator gets the
    reason, not a bare 500, and the partial file is gone."""
    # A voices dir that is not there is the cheapest real OSError to raise
    # from the same line a full disk would.
    dest = tmp_path / "gone" / "v.onnx.part"
    with pytest.raises(Exception) as excinfo:
        await voices_api._save_under_budget(
            _upload(b"z" * 100), dest, 10_000, "model file"
        )
    assert getattr(excinfo.value, "status_code", None) == 500
    assert "could not save model file" in excinfo.value.detail
    assert not dest.exists()


@pytest.mark.asyncio
async def test_an_undeclared_oversize_never_reaches_the_handler(
    tmp_path, monkeypatch
) -> None:
    """A body that does not declare its length is cut off mid-read by the
    middleware. FastAPI's form parser reports the interruption as its own
    400 rather than the 413 — either way the endpoint never runs and the
    voices dir stays empty."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    monkeypatch.setattr(voices_api, "_voices_dir", lambda: tmp_path)
    monkeypatch.setattr(mw, "BODY_LIMITS", (("/api/voices/piper", 4096),))

    async def chunks():
        for _ in range(20):
            yield b"x" * 4096

    async with _web() as c:
        r = await c.post(
            "/api/voices/piper",
            headers={**bearer("admin-token"), "Content-Type": "multipart/form-data; boundary=zz"},
            content=chunks(),
        )
    assert r.status_code in (400, 413), r.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_an_oversized_config_is_refused_and_the_model_is_not_kept(
    tmp_path, monkeypatch
) -> None:
    """The config JSON has its own, much smaller cap — and refusing it also
    clears the model staged just before it."""
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    monkeypatch.setattr(voices_api, "_voices_dir", lambda: tmp_path)
    monkeypatch.setattr(voices_api, "_MAX_CONFIG_BYTES", 64)
    async with _web() as c:
        r = await c.post(
            "/api/voices/piper",
            headers=bearer("admin-token"),
            data={"name": "Test Voice", "set_default": "false"},
            files={
                "onnx": ("v.onnx", b"z" * 2_000, "application/octet-stream"),
                "config": ("v.onnx.json", b"{" + b" " * 500 + b"}", "application/json"),
            },
        )
    assert r.status_code == 413
    assert "config file" in r.json()["detail"]
    assert list(tmp_path.iterdir()) == [], "a refused upload left files behind"


@pytest.mark.asyncio
async def test_an_oversized_model_upload_answers_413(tmp_path, monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    monkeypatch.setattr(voices_api, "_voices_dir", lambda: tmp_path)
    monkeypatch.setattr(voices_api, "_MAX_ONNX_BYTES", 64)
    async with _web() as c:
        r = await c.post(
            "/api/voices/piper",
            headers=bearer("admin-token"),
            data={"name": "Test Voice", "set_default": "false"},
            files={
                "onnx": ("v.onnx", b"z" * 4_000, "application/octet-stream"),
                "config": ("v.onnx.json", b"{}", "application/json"),
            },
        )
    assert r.status_code == 413
    assert "model file" in r.json()["detail"]
    assert list(tmp_path.iterdir()) == []


# ─── The middleware in front of it ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_declared_oversize_is_refused_without_running_the_app() -> None:
    """A body whose Content-Length is over budget never reaches the app —
    the point of metering at the ASGI edge rather than in the handler."""
    ran = False

    async def app(scope, receive, send):
        nonlocal ran
        ran = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    monkeypatched = mw.BodyLimitMiddleware(app)
    monkeypatched_limits = (("/capped", 100),)
    original = mw.BODY_LIMITS
    mw.BODY_LIMITS = monkeypatched_limits
    try:
        async with AsyncClient(
            transport=ASGITransport(app=monkeypatched), base_url="http://test"
        ) as c:
            r = await c.post("/capped", content=b"x" * 500)
            assert r.status_code == 413
            assert ran is False
            # A route with no entry in the table is untouched.
            assert (await c.post("/other", content=b"x" * 500)).status_code == 200
            assert ran is True
    finally:
        mw.BODY_LIMITS = original


@pytest.mark.asyncio
async def test_an_undeclared_oversize_is_cut_off_mid_body() -> None:
    """Without a Content-Length the body is metered as it arrives and
    refused the moment it passes the budget."""
    read = 0

    async def app(scope, receive, send):
        nonlocal read
        while True:
            message = await receive()
            read += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def chunks():
        for _ in range(20):
            yield b"x" * 100

    original = mw.BODY_LIMITS
    mw.BODY_LIMITS = (("/capped", 250),)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=mw.BodyLimitMiddleware(app)),
            base_url="http://test",
        ) as c:
            r = await c.post("/capped", content=chunks())
            assert r.status_code == 413
            assert read < 2_000, "the app was fed the whole body anyway"
    finally:
        mw.BODY_LIMITS = original


def test_the_piper_upload_route_has_a_budget() -> None:
    assert mw.body_limit_for("/api/voices/piper") == 210 * mw.MB
    assert mw.body_limit_for("/api/voices/edge") is None


def test_the_web_app_runs_the_body_limit_middleware() -> None:
    assert any(m.cls is mw.BodyLimitMiddleware for m in web_app.user_middleware)
