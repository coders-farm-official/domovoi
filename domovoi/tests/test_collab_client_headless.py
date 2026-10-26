"""The browser half of co-editing, headless: the vendored Yjs bundle
(with y-websocket's client provider) plus ``web/static/collab.js``, run in Node against a real
uvicorn serving the co-editing and Documents routes. Two helpers open the
same document; one seeds it, both edit at once and converge, presence
shows both, the server asks the last editor to save the clean file through
``PUT /api/documents/text`` with the co-editing header, and ``close()``
saves the last edit on the way out.

Skipped without Node 22+ (it needs Node's own WebSocket). Postgres is the
lane's.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from domovoi.config import settings
from domovoi.tests.auth_testkit import install_fake_db
from domovoi.tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"
HARNESS = Path(__file__).with_name("collab_client_harness.js")

NODE = shutil.which("node")


def _node_has_websocket() -> bool:
    if not NODE:
        return False
    try:
        r = subprocess.run([NODE, "-e", "process.exit(typeof WebSocket === 'function' ? 0 : 1)"],
                           timeout=20)
    except Exception:
        return False
    return r.returncode == 0


pytestmark = [
    requires_db,
    pytest.mark.skipif(not _node_has_websocket(), reason="needs Node 22+ (global WebSocket)"),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(monkeypatch, tmp_path):
    import asyncio

    from sqlalchemy import text

    from domovoi.db.session import engine
    import web.backend.api.files as files_api
    from web.backend.api import collab as collab_api
    from web.backend.api import documents as documents_api
    from web.backend.collab import rooms

    async def wipe():
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE collab_snapshots, collab_updates, collab_docs CASCADE"))

    asyncio.run(wipe())
    install_fake_db(monkeypatch, admin=False)          # pre-setup: the LAN grace
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(rooms, "MANAGER", rooms.RoomManager())

    async def not_blocked(device_id):
        return None

    monkeypatch.setattr(files_api, "_block_status", not_blocked)
    (tmp_path / "plan.md").write_text("# Plan\n", encoding="utf-8")

    app = FastAPI()
    app.include_router(collab_api.router)
    app.include_router(documents_api.router)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off",
                                        log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", tmp_path
    srv.should_exit = True
    t.join(timeout=10)
    asyncio.run(wipe())


def test_two_browser_helpers_converge_save_and_leave_cleanly(server):
    base, docs = server
    r = subprocess.run([NODE, str(HARNESS), str(STATIC), base, "plan.md"],
                       capture_output=True, text=True, timeout=90)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    assert lines, (r.stdout, r.stderr[-1500:])
    out = json.loads(lines[-1])
    assert out["ok"], out.get("error")
    assert out["aSeed"] is True and out["bSeed"] is False
    assert out["bFirst"] == "# Plan\n"
    assert out["converged"].startswith("B: ") and "from A" in out["converged"]
    assert sorted(p["self"] for p in out["presence"]) == [False, True]
    assert out["status"] == "Saved"
    first = out["saves"][0]
    assert first["status"] == 200 and first["header"].count(";") == 2
    # close() saved the edit made just before leaving, and the file is it.
    assert out["savesAfterClose"] >= 2
    assert out["lastSaved"].startswith("last B: ")
    assert (docs / "plan.md").read_text(encoding="utf-8") == out["lastSaved"]
