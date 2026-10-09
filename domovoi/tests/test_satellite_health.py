"""Satellite health telemetry on the core: the `health` and `log_push`
frames, `prev_health` in the hello, the retained per-room log, the
admin routes, and the V022 repositories.

Everything up to the repositories is DB-free (a FakeWS and a
SimpleNamespace app, `session_scope` replaced), so it cannot hide behind
`requires_db`. The repository section runs on a lane with V022 applied.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from domovoi import satellite_health as sh
from domovoi import streaming
from domovoi.streaming import CORE_FEATURES, StreamSession
from domovoi.tests.conftest import requires_db


# ─── validation ───────────────────────────────────────────────────────────


def test_health_and_log_push_are_advertised():
    assert "health" in CORE_FEATURES and "log_push" in CORE_FEATURES


def test_validate_sample_keeps_known_keys_and_coerces():
    frame = {
        "type": "health", "seq": 7, "ts": 1.5, "rss_kb": "48112", "fds": 23.0,
        "cpu_pct": 3.25, "soc_temp_c": 52.3, "throttled": 0x50005,
        "throttled_flags": ["under_voltage", 42, "x" * 100],
        "wifi": {"rx_mbits": 28.9, "tx_mbits": None, "ssid": "home" * 40, "extra": 1},
        "nm_state": {"code": 100, "state": "connected"},
        "gateway_ok": True, "core_ok": "yes", "ws": "up", "mic_fps": 33.3,
        "log_ring": {"bytes": 1000, "lines": 9, "dropped_lines": 0, "junk": 5},
        "unknown_key": {"anything": 1}, "boot_id": "b" * 100,
    }
    s = sh.validate_sample(frame)
    assert s["seq"] == 7 and s["rss_kb"] is None and s["fds"] == 23
    assert s["throttled_flags"] == ["under_voltage", "x" * 40]
    assert s["wifi"] == {"rx_mbits": 28.9, "tx_mbits": None, "ssid": ("home" * 40)[:64]}
    assert s["core_ok"] is None and s["gateway_ok"] is True
    assert s["log_ring"] == {"bytes": 1000, "lines": 9, "dropped_lines": 0}
    assert "unknown_key" not in s and "type" not in s
    assert len(s["boot_id"]) == 64


def test_validate_sample_refuses_oversize_or_empty():
    assert sh.validate_sample({"type": "health", "ws": "x" * 5000}) is None
    assert sh.validate_sample({"type": "health"}) is None
    assert sh.validate_sample({"type": "health", "seq": 1}) is None
    assert sh.validate_sample("nope") is None
    assert sh.validate_sample({"type": "health", "rss_kb": float("nan"), "fds": 3})["rss_kb"] is None


def test_validate_outage_bounds_the_report():
    raw = {
        "last_exit_reason": "r" * 2000, "prev_boot_id": "b",
        "offline_diag": [{"failure": "tcp_refused", "deep": {"a": {"b": {"c": {"d": {"e": 1}}}}}}] * 80,
        "sample": {"rss_kb": 1},
    }
    doc = sh.validate_outage(raw)
    assert len(doc["last_exit_reason"]) == 500
    assert len(doc["offline_diag"]) == 60
    assert len(json.dumps(doc)) <= sh.OUTAGE_MAX_BYTES
    assert sh.validate_outage(["not", "a", "dict"]) is None


def test_validate_outage_slims_an_oversize_report_keeping_the_newest_diag():
    raw = {
        "last_exit_reason": "RSS", "sample": {"pad": "x" * 400},
        "offline_diag": [{"n": i, "pad": "y" * 400} for i in range(60)],
    }
    doc = sh.validate_outage(raw)
    assert doc["truncated"] is True and "sample" not in doc
    assert doc["offline_diag"][-1]["n"] == 59
    assert len(json.dumps(doc)) <= sh.OUTAGE_MAX_BYTES


def test_journal_lines_and_quiet_exits():
    line = sh.format_health_line("den", {"rss_kb": 49152, "cpu_pct": 3.1, "throttled": 0x50005,
                                         "wifi": {"rx_mbits": 28.9}, "mic_fps": 33.3})
    assert "room=den rss=48MB" in line and "throttled=0x50005" in line and "mic_fps=33.3" in line
    summary = sh.outage_summary({
        "last_exit_reason": "RSS 310 MB", "prev_boot_id": "b1",
        "offline_diag": [{"failure": "tcp_refused", "gateway_ok": True, "core_ok": False}] * 5,
        "actions": [{"action": "wifi_recover", "reason": "no server 5 min"}],
    })
    assert "exit='RSS 310 MB'" in summary and "offline_diag=5" in summary
    assert summary.count("tcp_refused") == 3 and "last action wifi_recover" in summary
    assert sh.is_quiet_exit("shutdown: requested") and not sh.is_quiet_exit("RSS 310 MB")


# ─── the retained log ─────────────────────────────────────────────────────


def test_retained_log_stamps_marks_and_rotates(tmp_path):
    rl = sh.RetainedLog(tmp_path, max_bytes=400)
    assert rl.append("den", "first\nsecond\n", offline=True, now=1_700_000_000) == 2
    assert rl.append("den", "third", offline=False, now=1_700_000_001) == 1
    text = (tmp_path / "den.log").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("2023-11-14T22:13:20Z [offline] first")
    assert lines[2] == "2023-11-14T22:13:21Z third"
    # Past the cap: the file rotates to .log.1 (one kept) and starts over.
    rl.append("den", "x" * 300, offline=False)
    rl.append("den", "after rotation", offline=False)
    assert (tmp_path / "den.log.1").exists()
    assert "after rotation" in (tmp_path / "den.log").read_text()
    assert "first" in (tmp_path / "den.log.1").read_text()
    info = rl.info("den")
    assert info["rotated_bytes"] > 0 and info["bytes"] < 400 and info["updated_at"]
    assert rl.append("den", "", offline=False) == 0


def test_retained_log_tail_is_whole_lines_and_room_names_are_file_safe(tmp_path):
    rl = sh.RetainedLog(tmp_path)
    for i in range(20):
        rl.append("../../etc/passwd room", f"line {i:02d}", offline=False, now=1_700_000_000)
    assert sorted(p.name for p in tmp_path.iterdir()) == [".._.._etc_passwd_room.log"]
    tail = rl.tail("../../etc/passwd room", 80)
    assert tail.startswith("2023-") and tail.endswith("line 19\n")
    assert len(tail.encode()) <= 80
    assert rl.tail("nobody", 100) == "" and rl.info("nobody")["bytes"] == 0


def test_log_push_limiter_allows_a_burst_then_the_trickle():
    lim = sh.LogPushLimiter(capacity=100, refill_per_sec=10)
    assert lim.allow("den", 60, now=0.0)
    assert lim.allow("den", 40, now=0.0)
    assert not lim.allow("den", 1, now=0.0), "the bucket is empty"
    assert lim.allow("den", 20, now=2.0), "refilled 20 bytes in 2 s"
    assert lim.allow("other", 100, now=2.0), "per room"


def test_summarize_history():
    t0 = datetime(2026, 10, 9, tzinfo=timezone.utc)
    rows = [
        (t0, {"rss_kb": 50_000, "cpu_pct": 2.0, "throttled": 0, "reconnects": 1}),
        (t0 + timedelta(minutes=1), {"rss_kb": 52_000, "cpu_pct": 9.5, "throttled": 0x4, "reconnects": 2}),
        (t0 + timedelta(minutes=2), {"rss_kb": 51_000, "cpu_pct": 3.0, "soc_temp_c": 61.0}),
    ]
    s = sh.summarize_history(rows)
    assert s["samples"] == 3 and s["from"].startswith("2026-10-09T00:00")
    assert s["rss_kb"] == {"min": 50_000, "max": 52_000, "last": 51_000}
    assert s["cpu_pct"]["max"] == 9.5 and s["soc_temp_c"]["last"] == 61.0
    assert s["throttled_any"] == 0x4 and s["reconnects"] == 2
    assert sh.summarize_history([]) == {"samples": 0}


# ─── the stream handlers ──────────────────────────────────────────────────


class FakeWS:
    def __init__(self, state=None):
        self.app = SimpleNamespace(state=state or SimpleNamespace(active_sessions={}))
        self.sent_text: list[str] = []

    async def send_text(self, t):
        self.sent_text.append(t)


@asynccontextmanager
async def no_v022():
    raise RuntimeError('relation "satellite_health" does not exist')
    yield  # pragma: no cover


@pytest.fixture
def no_db(monkeypatch):
    monkeypatch.setattr(streaming, "session_scope", no_v022)
    StreamSession._v022_warned = False


@pytest.mark.asyncio
async def test_a_health_frame_is_kept_per_room_and_logged_every_tenth(no_db, caplog):
    ws = FakeWS()
    session = StreamSession(ws, "den")
    caplog.set_level(logging.INFO)
    for i in range(12):
        await session._on_control({"type": "health", "seq": i, "rss_kb": 1000 + i, "mic_fps": 33.0})
    kept = ws.app.state.satellite_health["den"]
    assert kept["sample"]["rss_kb"] == 1011 and kept["received_at"]
    assert ws.sent_text == [], "never answered with error"
    infos = [r for r in caplog.records if "satellite health room=den" in r.getMessage()]
    assert len(infos) == 2, "samples 1 and 11"
    warned = [r for r in caplog.records if "V022" in r.getMessage()]
    assert len(warned) == 1, "the missing-table hint is said once"


@pytest.mark.asyncio
async def test_a_malformed_health_frame_is_dropped_quietly(no_db):
    ws = FakeWS()
    session = StreamSession(ws, "den")
    await session._on_control({"type": "health", "ws": "x" * 9000})
    await session._on_control({"type": "health"})
    assert "den" not in getattr(ws.app.state, "satellite_health", {})
    assert ws.sent_text == []


def test_health_is_not_dropped_when_the_room_disconnects():
    """The last sample before an outage is the point; wifi_status and the
    rest are popped, this is not."""
    src = inspect.getsource(StreamSession.run)
    assert "wifi_status.pop(" in src
    assert "satellite_health" not in src and "satellite_last_outage" not in src


@pytest.mark.asyncio
async def test_prev_health_becomes_the_rooms_last_outage(no_db, caplog):
    ws = FakeWS()
    session = StreamSession(ws, "den")
    caplog.set_level(logging.INFO)
    await session._accept_outage_report({
        "prev_boot_id": "boot-1", "last_exit_reason": "RSS 310 MB is above the limit",
        "offline_diag": [{"failure": "tcp_timeout", "gateway_ok": False, "core_ok": False}],
    })
    outage = ws.app.state.satellite_last_outage["den"]
    assert outage["prev_boot_id"] == "boot-1"
    assert outage["report"]["last_exit_reason"].startswith("RSS 310")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "previous life" in r.getMessage()]
    assert len(warnings) == 1 and "tcp_timeout" in warnings[0].getMessage()
    # A requested shutdown is said quietly.
    await session._accept_outage_report({"last_exit_reason": "shutdown: requested"})
    quiet = [r for r in caplog.records if r.levelno == logging.INFO and "on request" in r.getMessage()]
    assert len(quiet) == 1
    await session._accept_outage_report("garbage")      # dropped, no raise


@pytest.mark.asyncio
async def test_log_push_lands_in_the_retained_log(no_db, tmp_path):
    state = SimpleNamespace(
        active_sessions={}, satellite_retained_log=sh.RetainedLog(tmp_path),
        log_push_limiter=sh.LogPushLimiter(capacity=200, refill_per_sec=0),
    )
    ws = FakeWS(state)
    session = StreamSession(ws, "den")
    await session._on_control({"type": "log_push", "lines": "spooled one\nspooled two\n", "offline": True})
    await session._on_control({"type": "log_push", "lines": "live line", "offline": False, "dropped": 3})
    text = (tmp_path / "den.log").read_text()
    assert "[offline] spooled one" in text and "[offline] spooled two" in text
    assert "Z live line" in text and "[offline] live line" not in text
    assert "dropped 3 line(s)" in text
    # Over budget: dropped, not written, and never an error frame.
    await session._on_control({"type": "log_push", "lines": "x" * 180, "offline": False})
    assert "x" * 180 not in (tmp_path / "den.log").read_text()
    await session._on_control({"type": "log_push", "lines": ["not", "a", "string"]})
    await session._on_control({"type": "log_push", "lines": "y" * (sh.LOG_PUSH_MAX_BYTES + 1)})
    assert ws.sent_text == []


# ─── the routes ───────────────────────────────────────────────────────────


@asynccontextmanager
async def no_db_scope():
    raise RuntimeError("no database in this test")
    yield  # pragma: no cover


@pytest.fixture
def core_client(monkeypatch, tmp_path):
    from httpx import ASGITransport, AsyncClient

    from domovoi import main as core_main
    from domovoi.tests.auth_testkit import install_fake_db

    install_fake_db(monkeypatch, admin=True, device_token="house-token", sessions={"bearer-1"})
    monkeypatch.setattr(core_main, "session_scope", no_db_scope)
    app = core_main.app
    monkeypatch.setattr(app.state, "active_sessions", {}, raising=False)
    monkeypatch.setattr(app.state, "satellite_health", {}, raising=False)
    monkeypatch.setattr(app.state, "satellite_last_outage", {}, raising=False)
    monkeypatch.setattr(app.state, "satellite_retained_log", sh.RetainedLog(tmp_path), raising=False)
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_health_route_answers_a_paired_device_and_refuses_nobody_else(core_client):
    app, client = core_client
    app.state.satellite_health["den"] = {"received_at": "2026-10-09T10:00:00+00:00",
                                         "sample": {"rss_kb": 50_000}}
    app.state.satellite_last_outage["den"] = {"reported_at": "x", "prev_boot_id": "b",
                                              "report": {"last_exit_reason": "RSS"}}
    async with client:
        r = await client.get("/v1/admin/satellite/den/health")
        assert r.status_code == 401
        r = await client.get("/v1/admin/satellite/den/health", headers={"X-Device-Token": "house-token"})
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False
        assert body["latest"]["sample"]["rss_kb"] == 50_000
        assert body["last_outage"]["report"]["last_exit_reason"] == "RSS"
        assert body["history"] is None, "no V022 in this test"
        r = await client.get("/v1/admin/satellite/nowhere/health",
                             headers={"Authorization": "Bearer bearer-1"})
        assert r.status_code == 200 and r.json()["latest"] is None


@pytest.mark.asyncio
async def test_logs_route_serves_the_retained_log_for_an_offline_room(core_client):
    app, client = core_client
    app.state.satellite_retained_log.append("den", "kept while it was away", offline=True)
    async with client:
        r = await client.get("/v1/admin/satellite/den/logs?max_bytes=4096",
                             headers={"Authorization": "Bearer bearer-1"})
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False and body["live"] is None
        assert body["text"] == "" and body["bytes"] == 0, "the old fields keep their shape"
        assert "[offline] kept while it was away" in body["retained"]["text"]
        assert body["retained"]["file_bytes"] > 0
        # Nothing retained, nothing live: still 404, as before.
        r = await client.get("/v1/admin/satellite/attic/logs?max_bytes=4096",
                             headers={"Authorization": "Bearer bearer-1"})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_logs_route_for_a_connected_room_carries_live_and_retained(core_client):
    app, client = core_client
    app.state.satellite_retained_log.append("den", "older line", offline=False)

    class Live:
        async def request_logs(self, *, max_bytes, timeout=60.0):
            return {"text": "ring line\n", "stats": {"bytes": 10, "max_bytes": 100}, "chunks": 1}

    app.state.active_sessions["den"] = Live()
    async with client:
        r = await client.get("/v1/admin/satellite/den/logs?max_bytes=4096",
                             headers={"Authorization": "Bearer bearer-1"})
    body = r.json()
    assert body["connected"] is True
    assert body["text"] == "ring line\n" and body["live"]["text"] == "ring line\n"
    assert body["live"]["buffered_bytes"] == 10
    assert "older line" in body["retained"]["text"]


# ─── the repositories (V022) ──────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_health_rows_are_kept_pruned_and_summarised():
    from sqlalchemy import text

    from domovoi.db.repositories import SatelliteHealthRepository
    from domovoi.db.session import session_scope

    room = "health-test-room"
    async with session_scope() as s:
        await s.execute(text("DELETE FROM satellite_health WHERE room_id = :r"), {"r": room})
        repo = SatelliteHealthRepository(s)
        await repo.insert(room, {"seq": 1, "rss_kb": 50_000, "cpu_pct": 2.0})
        await repo.insert(room, {"seq": 2, "rss_kb": 52_000, "cpu_pct": 4.0})
        # An old row, as if from yesterday.
        await s.execute(
            text("INSERT INTO satellite_health (room_id, received_at, sample) "
                 "VALUES (:r, now() - interval '25 hours', CAST(:s AS jsonb))"),
            {"r": room, "s": json.dumps({"seq": 0, "rss_kb": 1})},
        )
    async with session_scope() as s:
        repo = SatelliteHealthRepository(s)
        latest = await repo.latest(room)
        assert latest["sample"]["seq"] == 2 and latest["received_at"]
        rows = await repo.history(room, 24)
        assert [r[1]["seq"] for r in rows] == [1, 2], "the 25 h old row is outside the window"
        assert sh.summarize_history(rows)["rss_kb"] == {"min": 50_000, "max": 52_000, "last": 52_000}
        assert await repo.prune(room, 24) == 1
        await s.execute(text("DELETE FROM satellite_health WHERE room_id = :r"), {"r": room})


@requires_db
@pytest.mark.asyncio
async def test_outages_keep_the_last_n_per_room():
    from sqlalchemy import text

    from domovoi.db.repositories import SatelliteOutageRepository
    from domovoi.db.session import session_scope

    room = "outage-test-room"
    async with session_scope() as s:
        await s.execute(text("DELETE FROM satellite_outages WHERE room_id = :r"), {"r": room})
        repo = SatelliteOutageRepository(s)
        for i in range(7):
            await repo.insert(room, f"boot-{i}", {"last_exit_reason": f"reason {i}"}, keep=5)
    async with session_scope() as s:
        repo = SatelliteOutageRepository(s)
        rows = await repo.list(room, 20)
        assert len(rows) == 5
        assert rows[0]["prev_boot_id"] == "boot-6" and rows[0]["report"]["last_exit_reason"] == "reason 6"
        assert [r["prev_boot_id"] for r in rows][-1] == "boot-2"
        latest = await repo.latest(room)
        assert latest["prev_boot_id"] == "boot-6"
        assert await repo.latest("never-reported") is None
        await s.execute(text("DELETE FROM satellite_outages WHERE room_id = :r"), {"r": room})
