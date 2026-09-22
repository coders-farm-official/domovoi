"""Every web→core hop carries a credential (CORE-4 / ADD-1).

The dashboard's routes are proxies: the web process holds the page, the
core holds the room. Now that the core routes behind them are gated, a
hop that forwards nothing turns an authenticated click into a 401 the
user can do nothing about — and, before the gates, was how the core
stayed open in the first place ("the web hop is admin-gated" is only true
of the hop).

Two guards, both DB-free:

* a STATIC one — every call to ``post_admin`` / ``get_admin`` /
  ``delete_admin`` / ``post_admin_bytes`` / ``fetch_admin_snapshot``
  anywhere in the web package passes ``headers``. It reads the source, so
  it covers routes no test drives yet and fails the moment a new proxy
  forgets;
* a BEHAVIOURAL one — a representative route from each module really does
  put the caller's Authorization (and device token) on the wire, and the
  web process's own timer-driven hop presents the household token instead.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth
from domovoi.tests.auth_testkit import install_fake_db, make_request
from web.backend.main import app as web_app

CORE_HOP_FUNCS = {
    "post_admin",
    "get_admin",
    "delete_admin",
    "post_admin_bytes",
    "fetch_admin_snapshot",
}
WEB_ROOT = pathlib.Path(__file__).resolve().parents[2] / "web"
ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"


# ─── Static: no hop leaves without headers ────────────────────────────────


def _hops_without_headers(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in CORE_HOP_FUNCS
            and "headers" not in {kw.arg for kw in node.keywords}
        ):
            out.append((node.lineno, node.func.id))
    return out


def test_every_web_to_core_call_forwards_a_credential() -> None:
    offenders: list[str] = []
    for path in sorted(WEB_ROOT.rglob("*.py")):
        if "node_modules" in path.parts:
            continue
        # The client module itself DEFINES these; it takes headers as an
        # argument rather than passing them.
        if path.name == "domovoi_client.py":
            continue
        for lineno, func in _hops_without_headers(path):
            offenders.append(f"{path.relative_to(WEB_ROOT)}:{lineno} {func}()")
    assert not offenders, (
        "these web→core calls pass no credentials — the core route behind "
        "them will refuse an authenticated caller: " + ", ".join(offenders)
    )


def test_the_guard_would_notice_a_hop_that_forgot(tmp_path) -> None:
    """The static check is only worth having if it fails on the shape it
    is meant to catch."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "async def go(request):\n"
        "    await post_admin('/v1/admin/announce', {})\n"
        "    await get_admin('/v1/admin/config', headers={'a': 'b'})\n",
        encoding="utf-8",
    )
    assert _hops_without_headers(sample) == [(2, "post_admin")]


# ─── Behavioural: what actually goes on the wire ──────────────────────────


class _Recorder:
    """Stands in for the hop helpers and remembers the headers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def post(self, status: int = 200, payload: Any = None):
        async def _fn(path, body=None, timeout=30.0, headers=None):
            self.calls.append((path, dict(headers or {})))
            return status, payload if payload is not None else {"ok": True}

        return _fn

    def get(self, status: int = 200, payload: Any = None):
        async def _fn(path, timeout=10.0, headers=None):
            self.calls.append((path, dict(headers or {})))
            return status, payload if payload is not None else {"ok": True}

        return _fn


def _web_client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _caller_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        admin_auth.DEVICE_TOKEN_HEADER: DEVICE_TOKEN,
        # WEB-6: a write under /api/ without this is refused 403 in front
        # of the router, so a caller that omits it never reaches the hop
        # this module is about. Every real client sends one (the dashboard
        # `XMLHttpRequest`, the app `DomovoiApp`).
        "X-Requested-With": "domovoi-tests",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "attr", "method", "url", "body"),
    [
        (
            "web.backend.api.satellites",
            "post_admin",
            "POST",
            "/api/satellites/kitchen/restart",
            None,
        ),
        (
            "web.backend.api.satellites",
            "post_admin",
            "POST",
            "/api/satellites/kitchen/announce",
            {"message": "dinner"},
        ),
        (
            "web.backend.api.music",
            "post_admin",
            "POST",
            "/api/music/pause/kitchen",
            None,
        ),
        (
            "web.backend.api.music",
            "post_admin",
            "POST",
            "/api/music/play",
            {"room_id": "kitchen", "query": "jazz"},
        ),
        (
            "web.backend.api.wake_words",
            "post_admin",
            "POST",
            "/api/wake-words/1/push",
            {"room_id": "kitchen"},
        ),
    ],
    ids=[
        "satellites restart",
        "satellites announce",
        "music transport",
        "music play",
        "wake push",
    ],
)
async def test_a_proxied_action_puts_the_callers_credentials_on_the_wire(
    module, attr, method, url, body, monkeypatch
) -> None:
    import importlib

    install_fake_db(monkeypatch, admin=False)  # pre-setup: the web gate lets us in
    mod = importlib.import_module(module)
    rec = _Recorder()
    monkeypatch.setattr(mod, attr, rec.post())
    async with _web_client() as c:
        await c.request(method, url, json=body, headers=_caller_headers())
    assert rec.calls, f"{url} never reached the core"
    _path, headers = rec.calls[-1]
    assert headers.get("Authorization") == f"Bearer {ADMIN_TOKEN}"
    assert headers.get(admin_auth.DEVICE_TOKEN_HEADER) == DEVICE_TOKEN


@pytest.mark.asyncio
async def test_the_satellite_log_pull_forwards_the_session(monkeypatch) -> None:
    """ADD-1: the core route is admin-gated now, so a signed-in operator
    reading the Logs tab must still get their logs."""
    import web.backend.api.satellites as satellites

    install_fake_db(monkeypatch, admin=False)
    rec = _Recorder()
    monkeypatch.setattr(satellites, "get_admin", rec.get(200, {"text": "hi"}))
    async with _web_client() as c:
        r = await c.get("/api/satellites/kitchen/logs", headers=_caller_headers())
    assert r.status_code == 200
    path, headers = rec.calls[-1]
    assert path.startswith("/v1/admin/satellite/kitchen/logs")
    assert headers.get("Authorization") == f"Bearer {ADMIN_TOKEN}"


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["greetings", "voices"])
async def test_a_clip_rerender_carries_the_editing_admins_session(
    module, monkeypatch
) -> None:
    """Voices and Greetings ask the core to re-render clips as a side
    effect of an edit. That is an admin-tier action on an admin-gated
    route, so the re-render carries the session of the admin whose edit
    triggered it rather than going out anonymous.

    Driven at the helper, not through the route: the edit itself writes a
    row, and the point here is the hop, not Postgres."""
    import importlib

    mod = importlib.import_module(f"web.backend.api.{module}")
    rec = _Recorder()
    monkeypatch.setattr(mod, "post_admin", rec.post())
    await mod._trigger_rerender(make_request(_caller_headers()))
    assert rec.calls == [
        (
            "/v1/admin/sounds/regenerate",
            {
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                admin_auth.DEVICE_TOKEN_HEADER: DEVICE_TOKEN,
                "X-Forwarded-For": "192.168.1.50",
            },
        )
    ]


# ─── The web process's own credential ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_poll_loop_presents_the_household_token(tmp_path, monkeypatch) -> None:
    """The state poll runs on a timer with no caller behind it, so it
    presents the household device token the web lifespan mirrored to
    disk at boot."""
    from web.backend import domovoi_client

    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(domovoi_client, "_service_token", None, raising=False)
    admin_auth.write_device_token_file("household-token")
    headers = await domovoi_client.service_auth_headers()
    assert headers == {admin_auth.DEVICE_TOKEN_HEADER: "household-token"}


@pytest.mark.asyncio
async def test_a_missing_household_token_is_not_fatal(tmp_path, monkeypatch) -> None:
    """Before the migration lands (or with the DB down) the poll must
    still run — the core's own gate is what decides, not this helper."""
    from web.backend import domovoi_client

    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(domovoi_client, "_service_token", None, raising=False)

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("web.backend.db.session_scope", boom)
    assert await domovoi_client.service_auth_headers() == {}


@pytest.mark.asyncio
async def test_the_snapshot_fetch_sends_the_headers_it_is_given(monkeypatch) -> None:
    from web.backend import domovoi_client

    seen: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"active_rooms": []}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen["headers"] = dict(headers or {})
            return _Resp()

    monkeypatch.setattr(domovoi_client.httpx, "AsyncClient", _Client)
    await domovoi_client.fetch_admin_snapshot(headers={"X-Device-Token": "abc"})
    assert seen["headers"] == {"X-Device-Token": "abc"}
