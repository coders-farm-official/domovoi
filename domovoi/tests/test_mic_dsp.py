"""XVF3800 microphone DSP presets on the core (domovoi/mic_dsp.py, 1.4.1).

DB-free: the hello, the frames and the admin endpoints run against the
same doubles as test_satellite_supports.py; the gates themselves are held
by test_core_route_tiers.py and, for the dashboard hop, below.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from domovoi import admin_auth, mic_dsp, satellite_abilities as sa
from domovoi import main as core_main
from domovoi.streaming import StreamSession
from domovoi.tests.test_satellite_supports import (  # noqa: F401 — fixtures
    DEVICES,
    FakeWS,
    inventory,
    paired,
)

def test_the_core_names_the_satellite_s_presets():
    from satellite import mic_dsp as sat_mic_dsp

    assert mic_dsp.PRESETS == tuple(sat_mic_dsp.PRESETS)
    assert set(mic_dsp.PRESET_LABELS) == set(mic_dsp.PRESETS)
    assert mic_dsp.MARK_MAX == sat_mic_dsp.MARK_MAX
    for text in ("block 2, 1m", "a\x1b[2Jb\n", "x" * 400, "\u2028\t", ""):
        assert mic_dsp.clean_mark(text) == sat_mic_dsp.clean_mark(text)


def test_the_ability_is_declared_only():
    assert "mic_dsp" not in sa.ASSUMED_WITHOUT_SUPPORTS
    assert sa.from_hello({"type": "hello"}).allows("mic_dsp") is False
    assert sa.from_hello({"type": "hello", "mic_dsp": True}).allows("mic_dsp") is True
    # A spare computer that lists the name (and nothing else) gets it.
    assert sa.from_hello({"type": "hello", "supports": ["mic_dsp"]}).allows("mic_dsp") is True


def test_the_marker_is_sanitised():
    assert mic_dsp.clean_mark("  block 2, 1m  ") == "block 2, 1m"
    assert mic_dsp.clean_mark("a\x1b[31mb\r\nc\x00") == r"a\x1b[31mb\x0d\x0ac\x00"
    assert mic_dsp.clean_mark("a\u2028b") == r"a\u2028b"
    assert len(mic_dsp.clean_mark("é" * 500)) == mic_dsp.MARK_MAX
    assert mic_dsp.clean_mark(7) == ""


def test_a_reported_state_is_bounded():
    state = mic_dsp.clean_state({
        "preset": "C", "ok": True, "at": "2026-11-22T10:00:00+00:00",
        "applied": {"PP_MIN_NS": ["0.05"], "bad key": [1], "PP_X": ["rm -rf /"]},
        "readback": {f"P{i}": [float(i)] for i in range(40)} | {"PP_AGCGAIN": [float("nan")]},
        "error": "x\x1b" * 400,
    })
    assert state["preset"] == "C" and state["ok"] is True
    assert state["applied"] == {"PP_MIN_NS": ["0.05"], "PP_X": None}
    assert len(state["readback"]) <= 16
    assert len(state["error"]) <= 200 and "\x1b" not in state["error"]
    odd = mic_dsp.clean_state({"preset": "rm", "ok": "yes", "at": "<script>"})
    assert odd["preset"] is None and odd["ok"] is False and odd["at"] is None


# ─── the session ──────────────────────────────────────────────────────────


async def _hello(device: str):
    hello = DEVICES[device]["hello"]
    ws = FakeWS()
    session = StreamSession(ws, hello["room_id"])  # type: ignore[arg-type]
    await session._on_control(dict(hello))
    ws.sent.clear()
    return session, ws


async def test_frames_go_only_to_a_satellite_that_declared_it(paired, inventory):
    session, ws = await _hello("pi_xvf3800")
    await session.send_mic_dsp("B")
    await session.send_mic_dsp_mark("block 2, 1m")
    assert ws.sent == [{"type": "set_mic_dsp", "preset": "B"},
                       {"type": "mic_dsp_mark", "text": "block 2, 1m"}]
    assert "mic_dsp" in session._ready_features()
    for device in ("pi_2mic_hat", "linux_pc", "windows_pc", "android_room"):
        session, ws = await _hello(device)
        with pytest.raises(sa.NotDeclared):
            await session.send_mic_dsp("B")
        with pytest.raises(sa.NotDeclared):
            await session.send_mic_dsp_mark("x")
        assert ws.sent == [], device
        assert "mic_dsp" not in session._ready_features()


async def test_the_reported_state_is_cached_only_from_a_declared_satellite(paired, inventory):
    report = {"type": "mic_dsp_state", "preset": "B", "ok": True,
              "readback": {"PP_AGCMAXGAIN": [31.6]}, "at": "2026-11-22T10:00:00+00:00"}
    session, ws = await _hello("pi_xvf3800")
    await session._on_control(dict(report))
    cached = ws.app.state.satellite_mic_dsp[session.room_id]
    assert cached["preset"] == "B" and cached["readback"] == {"PP_AGCMAXGAIN": [31.6]}
    session, ws = await _hello("pi_2mic_hat")
    await session._on_control(dict(report))
    assert session.room_id not in getattr(ws.app.state, "satellite_mic_dsp", {})


# ─── the household boot default (1.5.0, owner answer Q8: a seam only) ────


def test_no_boot_default_is_chosen(monkeypatch):
    from domovoi.config import settings

    assert type(settings).model_fields["mic_dsp_default_preset"].default == ""
    assert mic_dsp.household_default() is None
    assert mic_dsp.default_to_apply({"preset": "factory", "error": None}) is None


def test_a_set_default_applies_only_to_a_satellite_at_factory(monkeypatch):
    from domovoi.config import settings

    monkeypatch.setattr(settings, "mic_dsp_default_preset", "C")
    assert mic_dsp.default_to_apply({"preset": "factory", "error": None}) == "C"
    assert mic_dsp.default_to_apply({"preset": None, "error": None}) == "C"
    # An admin's choice stands; a failed write is not retried on every report.
    assert mic_dsp.default_to_apply({"preset": "B", "error": None}) is None
    assert mic_dsp.default_to_apply({"preset": "factory", "error": "timeout"}) is None
    for bad in ("factory", "Z", "c "):
        monkeypatch.setattr(settings, "mic_dsp_default_preset", bad)
        assert mic_dsp.default_to_apply({"preset": "factory"}) is None


async def test_a_set_default_is_sent_when_the_satellite_reports_factory(paired, inventory, monkeypatch):
    from domovoi.config import settings

    report = {"type": "mic_dsp_state", "preset": "factory", "ok": True, "readback": {}}
    session, ws = await _hello("pi_xvf3800")
    ws.sent.clear()
    await session._on_control(dict(report))
    assert "set_mic_dsp" not in ws.types(), "unset: nothing is sent"
    monkeypatch.setattr(settings, "mic_dsp_default_preset", "B")
    await session._on_control(dict(report))
    assert {"type": "set_mic_dsp", "preset": "B"} in ws.sent


# ─── the admin endpoints ──────────────────────────────────────────────────


class _Target:
    def __init__(self, declared: bool):
        self.room_id = "office"
        self.board = None
        self.abilities = sa.from_hello({"type": "hello", "mic_dsp": declared})
        self.sent: list[tuple[str, str]] = []

    async def send_mic_dsp(self, preset):
        self.sent.append(("preset", preset))

    async def send_mic_dsp_mark(self, text):
        self.sent.append(("mark", text))


@pytest.fixture
def rooms(monkeypatch):
    state = SimpleNamespace(active_sessions={}, satellite_mic_dsp={})
    monkeypatch.setattr(core_main.app, "state", state)
    return state


async def test_set_sends_only_a_known_name(rooms):
    target = rooms.active_sessions["office"] = _Target(True)
    out = await core_main.admin_set_satellite_mic_dsp("office", core_main._AdminMicDspBody(preset="D"))
    assert out == {"room_id": "office", "preset": "D", "sent": True}
    with pytest.raises(HTTPException) as e:
        await core_main.admin_set_satellite_mic_dsp("office", core_main._AdminMicDspBody(preset="PP_AGCONOFF 0"))
    assert e.value.status_code == 422
    assert target.sent == [("preset", "D")]


async def test_no_control_for_a_satellite_without_the_ability(rooms):
    target = rooms.active_sessions["office"] = _Target(False)
    for call in (
        core_main.admin_get_satellite_mic_dsp("office"),
        core_main.admin_set_satellite_mic_dsp("office", core_main._AdminMicDspBody(preset="B")),
        core_main.admin_mark_satellite_mic_dsp("office", core_main._AdminMicDspMarkBody(text="x")),
    ):
        with pytest.raises(HTTPException) as e:
            await call
        assert e.value.status_code == 409
    assert target.sent == []
    with pytest.raises(HTTPException) as e:
        await core_main.admin_get_satellite_mic_dsp("nowhere")
    assert e.value.status_code == 404


async def test_get_returns_the_presets_and_the_readback(rooms):
    rooms.active_sessions["office"] = _Target(True)
    out = await core_main.admin_get_satellite_mic_dsp("office")
    assert [p["name"] for p in out["presets"]] == ["factory", "B", "C", "D"] and out["state"] is None
    rooms.satellite_mic_dsp["office"] = {"preset": "B", "readback": {"PP_AGCMAXGAIN": [31.6]}}
    out = await core_main.admin_get_satellite_mic_dsp("office")
    assert out["state"]["readback"] == {"PP_AGCMAXGAIN": [31.6]}


async def test_the_marker_is_sanitised_and_logged_on_the_server(rooms, caplog):
    target = rooms.active_sessions["office"] = _Target(True)
    with caplog.at_level(logging.INFO):
        out = await core_main.admin_mark_satellite_mic_dsp(
            "office", core_main._AdminMicDspMarkBody(text="block 2\n1m\x1b" + "y" * 300))
    assert out["text"].startswith(r"block 2\x0a1m\x1b") and len(out["text"]) == mic_dsp.MARK_MAX
    assert out["at"]
    assert target.sent == [("mark", out["text"])]
    line = next(r.getMessage() for r in caplog.records if "mic_dsp mark room=office" in r.getMessage())
    assert "\n" not in line and "\x1b" not in line and f"at={out['at']}" in line
    with pytest.raises(HTTPException) as e:
        await core_main.admin_mark_satellite_mic_dsp("office", core_main._AdminMicDspMarkBody(text="   "))
    assert e.value.status_code == 422


# ─── the gates, both hops ─────────────────────────────────────────────────


def _gates(app, method: str, path: str) -> list:
    from domovoi.tests.route_walk import iter_route_contexts
    from domovoi.tests.test_core_route_tiers import _dependency_calls

    for rc in iter_route_contexts(app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return list(_dependency_calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a route")


@pytest.mark.parametrize(("method", "path", "gate"), [
    ("GET", "/v1/admin/satellite/{room_id}/mic_dsp", admin_auth.require_admin_read),
    ("POST", "/v1/admin/satellite/{room_id}/mic_dsp", admin_auth.require_admin_mutation),
    ("POST", "/v1/admin/satellite/{room_id}/mic_dsp/mark", admin_auth.require_admin_mutation),
    ("GET", "/api/satellites/{room_id}/mic_dsp", admin_auth.require_admin_read),
    ("POST", "/api/satellites/{room_id}/mic_dsp", admin_auth.require_admin_mutation),
    ("POST", "/api/satellites/{room_id}/mic_dsp/mark", admin_auth.require_admin_mutation),
])
def test_every_hop_is_admin_only(method, path, gate):
    from web.backend.main import app as web_app

    app = web_app if path.startswith("/api/") else core_main.app
    gates = _gates(app, method, path)
    assert gate in gates
    assert admin_auth.require_device not in gates
    assert admin_auth.require_device_read not in gates
