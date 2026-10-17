"""The dashboard's plugin list matches the registry after every change (F-054).

rc2 smoke (2026-10-16): after a dashboard upgrade of romm 1.0.2 → 1.1.0
the registry said romm 1.1.0 ok, but the Plugins page and the nav had lost
romm; after a refused load (jellyfin), the registry said load_error and the
page said ok. A manual ``pg_notify('plugins_changed', …)`` put both right
at once: the web had missed the registry's LAST change.

Why: the web reads the registry into ``HOST.rows`` on each
``plugins_changed`` NOTIFY, and every such resync asked the LISTEN task to
refresh its channel set, which it did by closing its connection and
connecting again. The core's lifecycle actions write the registry more
than once in a row (an upgrade deletes the row and inserts the new one; an
install inserts it ``ok`` and then marks it ``load_error``), so the second
NOTIFY landed while the web wasn't listening, and Postgres keeps no NOTIFY
for a listener that isn't there.

Now:

* a channel refresh changes the listeners on the live connection, and
  ``plugins_changed`` is never unlistened (no gap);
* every (re)connect resyncs once, for whatever changed while nothing
  listened (boot, a dropped connection);
* a lifecycle action proxied through the web (install, confirm, enable,
  disable, uninstall, upgrade) answers only after the web has re-read the
  registry, so the list the page fetches next is the registry's;
* a resync that finds nothing changed tells no dashboard "plugins changed"
  (that push is a user-visible "reload" toast; the catch-up above must not
  cry wolf).
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from web.backend import plugin_host, realtime
from web.backend.realtime import ListenTask


class _FakePollLoop:
    _interval_sec = 1.5

    async def emit_for_channel(self, channel: str) -> None:
        pass


async def _notify(payload: str) -> None:
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_notify('plugins_changed', :p)"), {"p": payload})


async def _until(pred, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


@pytest.fixture
def registry(monkeypatch):
    """A stand-in for the registry and the web's resync of it: the
    resync reads ``registry["now"]`` the way ``HOST.resync`` reads the
    plugins table, records what it saw, and refreshes the LISTEN channels
    the way ``HOST.on_channels_changed`` does after every resync."""
    state = {"now": "romm 1.0.2", "seen": None, "resyncs": 0, "task": None}

    async def resync() -> None:
        await asyncio.sleep(0)
        state["seen"] = state["now"]
        state["resyncs"] += 1
        state["task"].refresh_channels()

    monkeypatch.setattr(realtime, "ON_PLUGINS_CHANGED", resync)
    return state


@requires_db
@pytest.mark.asyncio
async def test_a_change_right_after_a_channel_refresh_is_not_lost(registry, monkeypatch) -> None:
    """The upgrade's two writes: the row goes (NOTIFY 1), the web resyncs
    and refreshes its channels, the new row arrives (NOTIFY 2). The
    second NOTIFY is sent at the worst moment for the old code: the
    instant the LISTEN task opens a new connection."""
    import asyncpg

    real_connect = asyncpg.connect
    connects = []

    async def connect(*args, **kwargs):
        # The LISTEN task dials a bare DSN; SQLAlchemy's pool (the NOTIFY
        # senders here) passes keywords, and is not counted.
        if not (args and isinstance(args[0], str)):
            return await real_connect(*args, **kwargs)
        connects.append(time.monotonic())
        if len(connects) > 1 and registry["now"] == "romm gone":
            registry["now"] = "romm 1.1.0"
            await _notify("romm")
        return await real_connect(*args, **kwargs)

    monkeypatch.setattr(asyncpg, "connect", connect)
    task = ListenTask(_FakePollLoop())  # type: ignore[arg-type]
    registry["task"] = task
    await task.start()
    try:
        assert await _until(lambda: len(connects) >= 1)
        await asyncio.sleep(0.5)                        # LISTEN registered
        registry["now"] = "romm gone"
        await _notify("romm")
        assert await _until(lambda: registry["seen"] == "romm gone")
        # Whatever the task does with the refresh, the new row is announced
        # now (if it did not reconnect, nothing above sent it yet).
        await asyncio.sleep(0.3)
        if registry["now"] == "romm gone":
            registry["now"] = "romm 1.1.0"
            await _notify("romm")
        ok = await _until(lambda: registry["seen"] == "romm 1.1.0")
    finally:
        await task.stop()
    assert ok, f"the web still thinks {registry['seen']!r}; the registry says romm 1.1.0"
    assert len(connects) == 1, "a channel refresh must not drop the LISTEN connection"


@requires_db
@pytest.mark.asyncio
async def test_a_change_while_not_listening_is_caught_up_on_reconnect(registry) -> None:
    """The LISTEN connection drops (a Postgres restart, a network blip);
    a registry change in the meantime was announced to nobody. The
    reconnect resyncs once, so the web catches up."""
    from domovoi.db.session import engine

    task = ListenTask(_FakePollLoop())  # type: ignore[arg-type]
    task._RECONNECT_INITIAL_SEC = 0.2
    registry["task"] = task
    await task.start()
    try:
        assert await _until(lambda: registry["seen"] == "romm 1.0.2")
        async with engine.begin() as conn:
            killed = (await conn.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                "AND query LIKE 'LISTEN %'"
            ))).all()
        assert killed, "found no LISTEN connection to drop"
        registry["now"] = "jellyfin load_error"
        await _notify("jellyfin")          # nobody is listening: lost
        ok = await _until(lambda: registry["seen"] == "jellyfin load_error", timeout=6.0)
    finally:
        await task.stop()
    assert ok, f"the web still thinks {registry['seen']!r} after the reconnect"


# ─── the lifecycle proxy answers after the web has re-read ───────────────


async def _fake_core(status: int, body: dict):
    payload = json.dumps(body).encode()
    reason = {200: "OK", 422: "Unprocessable Entity"}[status]

    async def handle(reader, writer):
        await reader.readline()
        length = 0
        while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        if length:
            await reader.readexactly(length)
        writer.write(f"HTTP/1.1 {status} {reason}\r\ncontent-type: application/json\r\n"
                     f"content-length: {len(payload)}\r\nconnection: close\r\n\r\n".encode()
                     + payload)
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/api/plugins/install/abc123/confirm", 200),   # an install that loads, or is refused
        ("/api/plugins/install/abc123/confirm", 422),
        ("/api/plugins/romm/enable", 200),
        ("/api/plugins/romm/disable", 200),
        ("/api/plugins/romm/uninstall", 200),
    ],
)
async def test_a_lifecycle_action_answers_after_the_web_resynced(monkeypatch, path, status) -> None:
    from web.backend.main import app as web_app

    order: list[str] = []

    async def resync(app=None) -> None:
        order.append("resync")

    monkeypatch.setattr(plugin_host.HOST, "resync", resync)
    server = await _fake_core(status, {"ok": status == 200})
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("DOMOVOI_URL", f"http://127.0.0.1:{port}")
    try:
        async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
            r = await c.post(path, json={"data": "keep"},
                             headers={"X-Requested-With": "domovoi-tests"})
            order.append("answered")
    finally:
        server.close()
    assert r.status_code == status, r.text
    assert order == ["resync", "answered"]


@pytest.mark.asyncio
async def test_a_core_that_is_down_costs_no_resync(monkeypatch) -> None:
    """Nothing reached the core, so nothing changed: the 503 comes back
    without a registry read."""
    from web.backend.main import app as web_app

    calls: list[int] = []

    async def resync(app=None) -> None:
        calls.append(1)

    monkeypatch.setattr(plugin_host.HOST, "resync", resync)
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
        r = await c.post("/api/plugins/romm/enable", json={},
                         headers={"X-Requested-With": "domovoi-tests"})
    assert r.status_code == 503
    assert calls == []


# ─── a resync that changed nothing tells nobody ──────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_resync_that_changed_nothing_pushes_nothing(monkeypatch) -> None:
    host = plugin_host.PluginHost()
    pushed: list[str] = []

    async def broadcast(kind: str, payload: dict) -> None:
        pushed.append(kind)

    host.broadcast = broadcast
    await host.resync()
    assert pushed == ["plugins.changed"]          # the first read is news
    await host.resync()
    await host.resync()
    assert pushed == ["plugins.changed"], "an unchanged registry pushed 'plugins changed'"

    rows = await plugin_host.fetch_plugin_rows()
    moved = [dict(r) for r in rows] + [{
        **(rows[0] if rows else {}), "slug": "zz-new", "name": "New", "version": "1.0.0",
        "enabled": True, "status": "ok", "manifest": {}, "updated_at": "now",
    }]

    async def fetch_moved():
        return moved

    monkeypatch.setattr(plugin_host, "fetch_plugin_rows", fetch_moved)
    await host.resync()
    assert pushed == ["plugins.changed", "plugins.changed"]


# ─── the real pieces together: registry writes → LISTEN → HOST ───────────

_FRESH = "freshlist"
_FRESH_MANIFEST = {"realtime": [{"notify_channel": f"plugin_{_FRESH}_things_changed",
                                 "realtime_channel": "freshlist.things"}]}


async def _insert(version: str, status: str = "ok") -> None:
    from domovoi.plugins_runtime import registry as reg

    await reg.insert_plugin(
        slug=_FRESH, name="Fresh List", version=version, publisher="tests", license="MIT",
        domovoi_api="1.0", enabled=True, bundled=False, install_source="zip", source_ref=None,
        install_dir="/tmp/freshlist", manifest=_FRESH_MANIFEST, status=status,
    )


@requires_db
@pytest.mark.asyncio
async def test_back_to_back_registry_writes_leave_the_host_right(monkeypatch) -> None:
    """The core's own writes, as an upgrade and a refused install make
    them, with the web's real resync and LISTEN task: the plugin's realtime
    channel leaves the map and comes back in between, which is what made
    the old task reconnect at the wrong moment."""
    from domovoi.plugins_runtime import registry as reg

    host = plugin_host.PluginHost()
    task = ListenTask(_FakePollLoop())  # type: ignore[arg-type]

    async def channels_changed() -> None:
        task.refresh_channels()

    host.on_channels_changed = channels_changed
    monkeypatch.setattr(realtime, "ON_PLUGINS_CHANGED", host.resync)
    await reg.delete_plugin(_FRESH)
    await _insert("1.0.2")
    await host.resync()                   # the web's boot read (main.py)
    await task.start()
    try:
        assert _FRESH in host.rows
        await asyncio.sleep(0.5)          # LISTEN registered
        results = {}
        for attempt in range(5):
            # The upgrade: the row goes, the new one arrives.
            await reg.delete_plugin(_FRESH)
            await _insert(f"1.1.{attempt}")
            ok = await _until(
                lambda: (host.rows.get(_FRESH) or {}).get("version") == f"1.1.{attempt}",
                timeout=15.0,     # a busy shared Postgres; a lost NOTIFY never arrives
            )
            # A refused load: inserted ok, then marked load_error.
            await reg.delete_plugin(_FRESH)
            await _insert("2.0.0")
            await reg.update_plugin(_FRESH, status="load_error", enabled=False,
                                    last_error="refused by contract")
            refused = await _until(
                lambda: (host.rows.get(_FRESH) or {}).get("status") == "load_error", timeout=15.0
            )
            results[attempt] = (ok, refused)
    finally:
        await task.stop()
        await reg.delete_plugin(_FRESH)
        realtime.set_plugin_notify_channels({})
    assert all(ok and refused for ok, refused in results.values()), results
