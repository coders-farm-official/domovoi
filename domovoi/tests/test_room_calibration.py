"""Speaker calibration (1.5.0, slice 7): domovoi/room_calibration.py and
the playout's per-device latency (docs/ROOM_AUDIO_PROTOCOL.md §11, §5.5).

* the solve on synthetic rooms with known output latencies, input
  latencies and distances;
* a run over fake speakers that answer like a real room (a simulated
  acoustic path per pair), including chirps nobody hears;
* when it runs: the admin action, the refusals, the automatic run (off by
  default) and the move that clears the value;
* the stored values and their clearing on move (DB);
* the room-audio session: ``latency`` after ``ready`` and the results routed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import admin_auth, room_calibration
from domovoi.config import settings
from domovoi.main import app as core_app
from domovoi.room_calibration import (
    CalibrationRefused,
    RoomCalibration,
    get_calibration,
    solve,
)
from domovoi.room_playout import protocol as P
from domovoi.room_playout.ref_client import TimedPlayer as RefPlayer
from domovoi.room_playout.service import InMemoryRoomRegistry, RoomPlayout
from domovoi.room_playout.session import RoomAudioSession
from domovoi.tests.auth_testkit import _db  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db
from web.backend.main import app as web_app

# ─── a simulated room ─────────────────────────────────────────────────────


def measure(out_lat: dict[str, float], in_lat: dict[str, float],
            dist: dict[frozenset, float], *, self_loop: bool = True,
            missing: set[tuple[str, str]] = frozenset(), noise_us: float = 0.0,
            seed: int = 0) -> dict[tuple[str, str], float]:
    """``m(i, j) = L(i) + d(i, j) + M(j)``, as a room would give them."""
    rng = np.random.default_rng(seed)
    m: dict[tuple[str, str], float] = {}
    for i in out_lat:
        for j in out_lat:
            if (i == j and not self_loop) or (i, j) in missing:
                continue
            d = 0.0 if i == j else dist[frozenset((i, j))]
            m[(i, j)] = out_lat[i] + d + in_lat[j] + rng.normal(0, noise_us)
    return m


def rel(lat: dict[str, float]) -> dict[str, float]:
    lo = min(lat.values())
    return {k: v - lo for k, v in lat.items()}


# ─── the solve ────────────────────────────────────────────────────────────


def test_two_devices_both_ways_cancel_distance_and_mic_latency() -> None:
    L = {"a": 12_000.0, "b": 31_500.0}
    M = {"a": 4_000.0, "b": 9_000.0}              # different mics: the self-recordings cancel them
    d = {frozenset("ab"): 8_800.0}                # 3 m apart
    sol = solve(["a", "b"], measure(L, M, d))
    assert sol.unsolved == [] and sol.consistent
    for k, v in rel(L).items():
        assert abs(sol.latency_us[k] - v) <= 1
    assert abs(sol.distance_us[("a", "b")] - 8_800.0) < 5


def test_without_self_recordings_equal_mics_are_assumed() -> None:
    """An echo-cancelling array cannot hear itself: with alike mics the
    two-way difference still gives the output latencies."""
    L = {"a": 5_000.0, "b": 2_000.0, "c": 40_000.0}
    M = {"a": 3_000.0, "b": 3_000.0, "c": 3_000.0}
    d = {frozenset("ab"): 3_000.0, frozenset("ac"): 9_000.0, frozenset("bc"): 7_500.0}
    sol = solve(["a", "b", "c"], measure(L, M, d, self_loop=False))
    for k, v in rel(L).items():
        assert abs(sol.latency_us[k] - v) <= 1
    assert sol.latency_us["b"] == 0


def test_three_devices_with_noise_land_within_a_fraction_of_a_millisecond() -> None:
    L = {"pi": 1_500.0, "pc": 18_000.0, "usb": 7_200.0}
    M = {"pi": 2_000.0, "pc": 6_500.0, "usb": 4_000.0}
    d = {frozenset(("pi", "pc")): 11_000.0, frozenset(("pi", "usb")): 4_000.0,
         frozenset(("pc", "usb")): 8_000.0}
    for seed in range(5):
        sol = solve(list(L), measure(L, M, d, noise_us=60.0, seed=seed))
        assert sol.consistent
        for k, v in rel(L).items():
            assert abs(sol.latency_us[k] - v) < 300, (seed, k, sol.latency_us)


def test_a_device_not_heard_both_ways_is_left_unsolved() -> None:
    L = {"a": 1_000.0, "b": 2_000.0, "c": 3_000.0}
    M = dict.fromkeys(L, 1_000.0)
    d = {frozenset("ab"): 1_000.0, frozenset("ac"): 1_000.0, frozenset("bc"): 1_000.0}
    # c's chirp reached nobody: c can hear the others, but not be measured.
    m = measure(L, M, d, missing={("c", "a"), ("c", "b")})
    sol = solve(["a", "b", "c"], m)
    assert sol.unsolved == ["c"] and set(sol.latency_us) == {"a", "b"}
    assert sol.latency_us == {"a": 0, "b": 1_000}


def test_nothing_heard_solves_nothing_and_wild_values_are_dropped() -> None:
    assert solve(["a", "b"], {}).latency_us == {}
    m = {("a", "b"): 5_000.0, ("b", "a"): 9_000_000.0}       # not a chirp's delay
    assert solve(["a", "b"], m).unsolved == ["a", "b"]


def test_measurements_that_disagree_are_flagged() -> None:
    L = {"a": 1_000.0, "b": 2_000.0, "c": 3_000.0}
    M = dict.fromkeys(L, 1_000.0)
    d = {frozenset("ab"): 1_000.0, frozenset("ac"): 1_000.0, frozenset("bc"): 1_000.0}
    m = measure(L, M, d)
    m[("a", "c")] += 30_000.0                                   # an echo taken for the chirp
    assert not solve(list(L), m).consistent


# ─── a run over fake speakers ─────────────────────────────────────────────


class Room:
    """The acoustic truth of a test room, and speakers that answer like it."""

    def __init__(self, L: dict[str, float], M: dict[str, float], d: dict[frozenset, float],
                 *, deaf: set[str] = frozenset(), mute: set[str] = frozenset(),
                 silent: set[str] = frozenset()) -> None:
        self.L, self.M, self.d = L, M, d
        self.deaf, self.mute, self.silent = deaf, mute, silent
        self.cal: RoomCalibration | None = None
        self.steps: dict[tuple[str, int], dict[str, Any]] = {}

    def sink(self, sid: str) -> "Speaker":
        return Speaker(self, sid)


class Speaker:
    def __init__(self, room: Room, sid: str) -> None:
        self.room, self.sid = room, sid
        self.controls: list[dict[str, Any]] = []

    def send_audio(self, frame: bytes) -> None:
        pass

    def send_control(self, msg: dict[str, Any]) -> None:
        self.controls.append(msg)
        if msg["type"] != "calibrate" or self.sid in self.room.silent:
            return
        key = (msg["run_id"], msg["step"])
        if msg["play"]:
            self.room.steps[key] = {"player": self.sid, "at": msg["at_us"]}
        asyncio.get_running_loop().call_soon(self._answer, msg)

    def _answer(self, msg: dict[str, Any]) -> None:
        r = self.room
        step = r.steps.get((msg["run_id"], msg["step"]))
        out: dict[str, Any] = {"type": "calibrate_result", "run_id": msg["run_id"],
                               "step": msg["step"], "played": bool(msg["play"])}
        player = step and step["player"]
        if player is None or player in r.mute or self.sid in r.deaf or (
                player != self.sid and frozenset((player, self.sid)) not in r.d):
            out.update(heard=False, reason="not_heard")
        else:
            d = 0.0 if player == self.sid else r.d[frozenset((player, self.sid))]
            out.update(heard=True, arrival_us=step["at"] + r.L[player] + d + r.M[self.sid])
        r.cal.on_result(self.sid, out)


def _playout(room: Room, sids: list[str], *, room_id: str = "kitchen",
             supports: bool = True) -> tuple[RoomPlayout, dict[str, Speaker]]:
    reg = InMemoryRoomRegistry({s: room_id for s in sids})
    pl = RoomPlayout(reg, run_tasks=False, clock=lambda: 10_000_000)
    sinks = {}
    for s in sids:
        sink = room.sink(s)
        if supports:
            pl.declare(s, ["calibrate"])
        pl.attach(s, sink)
        sinks[s] = sink
    return pl, sinks


def _cal(pl: RoomPlayout, room: Room, stored: dict[str, int], **kw: Any) -> RoomCalibration:
    async def store(values: dict[str, int]) -> None:
        stored.update(values)

    async def not_quiet(_room: str) -> bool:
        return False

    kw.setdefault("quiet", not_quiet)
    kw.setdefault("auto_retries", 0)
    cal = RoomCalibration(pl, store=store, lead_us=0, answer_grace_s=0.5,
                          auto=kw.pop("auto", lambda: False), **kw)
    room.cal = cal
    return cal


def _latency_frames(sink: Speaker) -> list[int]:
    return [c["output_latency_us"] for c in sink.controls if c["type"] == "latency"]


async def test_a_run_measures_stores_and_sends_each_latency() -> None:
    L = {"sat_a": 3_000.0, "sat_b": 14_000.0, "sat_c": 9_000.0}
    M = {"sat_a": 1_000.0, "sat_b": 5_000.0, "sat_c": 2_000.0}
    d = {frozenset(("sat_a", "sat_b")): 6_000.0, frozenset(("sat_a", "sat_c")): 3_000.0,
         frozenset(("sat_b", "sat_c")): 4_000.0}
    room = Room(L, M, d)
    pl, sinks = _playout(room, list(L))
    stored: dict[str, int] = {}
    out = await _cal(pl, room, stored).calibrate("kitchen")
    assert out["status"] == "done", out
    assert stored == {"sat_a": 0, "sat_b": 11_000, "sat_c": 6_000}
    for sid, us in stored.items():
        # Sent when it changed (every device starts a connection at 0).
        assert _latency_frames(sinks[sid]) == ([us] if us else [])
        assert pl.latency[sid] == us
    # Every device got every step; one player per step, the chirp as specified.
    steps = [c for c in sinks["sat_a"].controls if c["type"] == "calibrate"]
    assert [c["play"] for c in steps] == [True, False, False]
    assert steps[0]["chirp"] == room_calibration.CHIRP and steps[0]["listen_ms"] == 400


async def test_a_chirp_nobody_hears_keeps_that_speakers_value_and_says_so() -> None:
    L = {"sat_a": 3_000.0, "sat_b": 5_000.0, "sat_c": 9_000.0}
    M = dict.fromkeys(L, 1_000.0)
    d = {frozenset(("sat_a", "sat_b")): 2_000.0, frozenset(("sat_a", "sat_c")): 2_000.0,
         frozenset(("sat_b", "sat_c")): 2_000.0}
    room = Room(L, M, d, mute={"sat_c"})            # c's speaker too quiet to reach anyone
    pl, sinks = _playout(room, list(L))
    stored: dict[str, int] = {}
    out = await _cal(pl, room, stored).calibrate("kitchen", names={"sat_c": "Shelf speaker"})
    assert out["status"] == "partial"
    assert set(stored) == {"sat_a", "sat_b"}
    assert "Shelf speaker keeps its previous setting" in out["message"]
    row = next(r for r in out["satellites"] if r["satellite_id"] == "sat_c")
    assert row["tuned"] is False and "No other satellite heard its chirp" in row["note"]
    assert _latency_frames(sinks["sat_c"]) == []       # untouched


async def test_nothing_heard_changes_nothing_and_says_so_plainly() -> None:
    room = Room({"sat_a": 0.0, "sat_b": 0.0}, {"sat_a": 0.0, "sat_b": 0.0}, {})   # too far apart
    pl, sinks = _playout(room, ["sat_a", "sat_b"])
    pl.set_latency("sat_a", 4_000)                     # the previous value
    stored: dict[str, int] = {}
    cal = _cal(pl, room, stored)
    out = await cal.calibrate("kitchen")
    assert out["status"] == "failed" and stored == {}
    assert "could not hear each other's chirps" in out["message"]
    assert "keeps its previous setting" in out["message"]
    assert pl.latency["sat_a"] == 4_000
    assert cal.last["kitchen"] is out                  # what the drawer shows


async def test_a_device_that_never_answers_counts_as_not_heard() -> None:
    room = Room({"sat_a": 0.0, "sat_b": 2_000.0}, {"sat_a": 0.0, "sat_b": 0.0},
                {frozenset(("sat_a", "sat_b")): 1_000.0}, silent={"sat_b"})
    pl, _ = _playout(room, ["sat_a", "sat_b"])
    stored: dict[str, int] = {}
    out = await _cal(pl, room, stored).calibrate("kitchen")
    assert out["status"] == "failed" and stored == {}


async def test_refusals_one_speaker_old_software_playing_and_twice() -> None:
    room = Room({"sat_a": 0.0}, {"sat_a": 0.0}, {})
    pl, _ = _playout(room, ["sat_a"])
    cal = _cal(pl, room, {})
    with pytest.raises(CalibrationRefused, match="this room has one"):
        await cal.calibrate("kitchen")
    # Two speakers, but one runs software from before calibration.
    room2 = Room({"sat_a": 0.0, "sat_b": 0.0}, {"sat_a": 0.0, "sat_b": 0.0}, {})
    pl2, _ = _playout(room2, ["sat_a"])
    pl2.registry.assign("sat_b", "kitchen")
    pl2.attach("sat_b", room2.sink("sat_b"))            # no `supports`
    with pytest.raises(CalibrationRefused, match="newer software"):
        await _cal(pl2, room2, {}).calibrate("kitchen")
    # Playing: refused, with a sentence for the drawer.
    room3 = Room({"sat_a": 0.0, "sat_b": 0.0}, {"sat_a": 0.0, "sat_b": 0.0}, {})
    pl3, _ = _playout(room3, ["sat_a", "sat_b"])
    pl3.say("kitchen", b"\0\0" * 4800, 48_000)
    cal3 = _cal(pl3, room3, {})
    with pytest.raises(CalibrationRefused, match="Something is playing") as e:
        await cal3.calibrate("kitchen")
    assert e.value.status == 409
    cal3._running.add("kitchen")
    with pytest.raises(CalibrationRefused, match="being tuned already"):
        await cal3.calibrate("kitchen")


# ─── when it runs on its own ──────────────────────────────────────────────


def test_the_automatic_run_is_on_by_default() -> None:
    # Owner decision 2026-11-22: on by default, an admin can turn it off.
    assert type(settings).model_fields["room_calibration_auto"].default is True
    from domovoi.config_schema import EDITABLE_FIELDS as FIELDS

    assert any(f.name == "room_calibration_auto" for f in FIELDS)


async def test_a_join_starts_an_automatic_run_only_when_on_and_uncalibrated() -> None:
    L = {"sat_a": 0.0, "sat_b": 7_000.0}
    room = Room(L, dict.fromkeys(L, 0.0), {frozenset(("sat_a", "sat_b")): 1_000.0})
    pl, _ = _playout(room, ["sat_a"])
    on = {"v": False}
    stored: dict[str, int] = {}
    cal = _cal(pl, room, stored, auto=lambda: on["v"], auto_delay_s=0)
    pl.registry.assign("sat_b", "kitchen")
    pl.declare("sat_b", ["calibrate"])
    pl.attach("sat_b", room.sink("sat_b"))
    pl.joined("sat_b", "kitchen", calibrated=False)
    assert cal._auto_tasks == {}                          # off: nothing plays
    on["v"] = True
    pl.joined("sat_b", "kitchen", calibrated=True)
    assert cal._auto_tasks == {}                          # already tuned here
    pl.joined("sat_b", "kitchen", calibrated=False)
    await cal._auto_tasks["kitchen"]
    assert stored == {"sat_a": 0, "sat_b": 7_000}
    assert cal.last["kitchen"]["reason"] == "joined"


async def test_an_automatic_run_skips_a_playing_room() -> None:
    room = Room({"sat_a": 0.0, "sat_b": 0.0}, {"sat_a": 0.0, "sat_b": 0.0},
                {frozenset(("sat_a", "sat_b")): 1_000.0})
    pl, _ = _playout(room, ["sat_a", "sat_b"])
    stored: dict[str, int] = {}
    cal = _cal(pl, room, stored, auto=lambda: True, auto_delay_s=0)
    pl.say("kitchen", b"\0\0" * 4800, 48_000)
    pl.joined("sat_b", "kitchen", calibrated=False)
    await cal._auto_tasks["kitchen"]
    assert stored == {} and "kitchen" not in cal.last


def _two(**kw: Any) -> tuple[RoomPlayout, Room, dict[str, int], RoomCalibration, list]:
    L = {"sat_a": 0.0, "sat_b": 4_000.0}
    room = Room(L, dict.fromkeys(L, 0.0), {frozenset(("sat_a", "sat_b")): 1_000.0})
    pl, _ = _playout(room, ["sat_a", "sat_b"])
    stored: dict[str, int] = {}
    kw.setdefault("auto", lambda: True)
    kw.setdefault("auto_delay_s", 0)
    cal = _cal(pl, room, stored, **kw)
    runs: list[str] = []
    real = cal.calibrate

    async def counted(room_id: str, **k: Any) -> dict[str, Any]:
        runs.append(room_id)
        return await real(room_id, **k)

    cal.calibrate = counted  # type: ignore[method-assign]
    return pl, room, stored, cal, runs


async def test_with_the_default_setting_a_join_runs_once(monkeypatch) -> None:
    monkeypatch.setattr(settings, "room_calibration_auto",
                        type(settings).model_fields["room_calibration_auto"].default)
    L = {"sat_a": 0.0, "sat_b": 3_000.0}
    room = Room(L, dict.fromkeys(L, 0.0), {frozenset(("sat_a", "sat_b")): 1_000.0})
    pl, _ = _playout(room, ["sat_a", "sat_b"])
    stored: dict[str, int] = {}
    cal = _cal(pl, room, stored, auto=None, auto_delay_s=0)   # the real setting
    pl.joined("sat_b", "kitchen", calibrated=False)
    await cal._auto_tasks["kitchen"]
    assert stored == {"sat_a": 0, "sat_b": 3_000}
    # Its value is stored now: a reconnect is no join to tune for.
    pl.joined("sat_b", "kitchen", calibrated=True)
    assert cal._auto_tasks["kitchen"].done()


async def test_turned_off_a_join_plays_nothing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "room_calibration_auto", False)
    room = Room({"sat_a": 0.0, "sat_b": 0.0}, {"sat_a": 0.0, "sat_b": 0.0},
                {frozenset(("sat_a", "sat_b")): 1_000.0})
    pl, _ = _playout(room, ["sat_a", "sat_b"])
    cal = _cal(pl, room, {}, auto=None, auto_delay_s=0)
    pl.joined("sat_b", "kitchen", calibrated=False)
    assert cal._auto_tasks == {}


async def test_a_join_runs_once_when_the_room_is_idle() -> None:
    pl, _, stored, cal, runs = _two()
    pl.joined("sat_b", "kitchen", calibrated=False)
    await cal._auto_tasks["kitchen"]
    assert runs == ["kitchen"] and stored == {"sat_a": 0, "sat_b": 4_000}


async def test_a_move_into_the_room_starts_a_run() -> None:
    pl, _, stored, cal, runs = _two()
    pl.registry.assign("sat_b", "den")
    pl.rebind("sat_b")
    pl.registry.assign("sat_b", "kitchen")
    assert pl.rebind("sat_b") == "kitchen"               # moved back in: a join
    await cal._auto_tasks["kitchen"]
    assert runs == ["kitchen"]


async def test_joins_close_together_share_one_run() -> None:
    pl, _, _, cal, runs = _two(auto_delay_s=0.05)
    pl.joined("sat_a", "kitchen", calibrated=False)
    first = cal._auto_tasks["kitchen"]
    pl.joined("sat_b", "kitchen", calibrated=False)
    await asyncio.sleep(0)
    assert first.cancelled()
    await cal._auto_tasks["kitchen"]
    assert runs == ["kitchen"]


async def test_a_turn_in_the_room_skips_the_run() -> None:
    pl, _, stored, cal, runs = _two(turn_active=lambda r: True)
    pl.joined("sat_b", "kitchen", calibrated=False)
    await cal._auto_tasks["kitchen"]
    assert runs == [] and stored == {}


async def test_quiet_hours_defer_the_run_until_they_end() -> None:
    checks = {"n": 0}

    async def quiet(_room: str) -> bool:
        checks["n"] += 1
        return checks["n"] <= 3                 # quiet for three looks, then not

    pl, _, stored, cal, runs = _two(quiet=quiet, auto_retry_s=0)   # no busy retries
    pl.joined("sat_b", "kitchen", calibrated=False)
    await cal._auto_tasks["kitchen"]
    assert runs == ["kitchen"] and checks["n"] == 4 and stored


async def test_a_busy_room_is_retried_and_runs_once_idle() -> None:
    busy = {"n": 2}

    def turn(_room: str) -> bool:
        busy["n"] -= 1
        return busy["n"] >= 0

    pl, _, _, cal, runs = _two(turn_active=turn, auto_retries=5, auto_retry_s=0)
    pl.joined("sat_b", "kitchen", calibrated=False)
    await cal._auto_tasks["kitchen"]
    assert runs == ["kitchen"]


def test_the_turn_check_reads_the_live_sessions() -> None:
    room = Room({}, {}, {})
    pl, _ = _playout(room, [])
    sessions = [SimpleNamespace(awaiting_reply=lambda: False),
                SimpleNamespace(awaiting_reply=lambda: True)]
    pl.registry.live_in = lambda r: sessions if r == "kitchen" else []  # type: ignore[attr-defined]
    cal = _cal(pl, room, {})
    assert cal._turn_active("kitchen") is True
    assert cal._turn_active("den") is False


def test_a_move_clears_the_sent_latency_and_counts_as_a_join() -> None:
    reg = InMemoryRoomRegistry({"sat_a": "kitchen"})
    pl = RoomPlayout(reg, run_tasks=False)
    joined: list[tuple] = []
    pl.calibration = SimpleNamespace(on_joined=lambda *a: joined.append(a))
    sink = Speaker(Room({}, {}, {}), "sat_a")
    pl.attach("sat_a", sink)
    pl.set_latency("sat_a", 9_000)
    reg.assign("sat_a", "den")
    assert pl.rebind("sat_a") == "den"
    assert _latency_frames(sink) == [9_000, 0] and pl.latency["sat_a"] == 0
    assert joined == [("sat_a", "den", False)]


# ─── the playout uses each device's latency ───────────────────────────────


def _converged(p: RefPlayer) -> None:
    for k in range(16):
        t = 1_000.0 * k
        p.on_text(json.dumps({"type": "pong", "t0": t, "t1": t + 500, "t2": t + 510}), t + 1010)


def test_a_device_with_a_longer_path_plays_its_samples_earlier() -> None:
    """Two players, the same timeline; one is told its path is 10 ms longer.
    At the same DAC time it renders the samples due 10 ms later, so both
    reach the air together."""
    ramp = np.arange(48_000, dtype=np.int64)
    pcm = np.stack([ramp % 30_000, ramp % 30_000], axis=1).astype("<i2")
    players = []
    for latency in (0, 10_000):
        p = RefPlayer()
        _converged(p)
        p.on_text(json.dumps(P.timeline_msg(1, 0)), 0)
        p.on_text(json.dumps({"type": "latency", "output_latency_us": latency}), 0)
        for k in range(50):
            p.on_binary(P.encode_audio(P.AudioFrame(1, 960 * k, 0, pcm[960 * k:960 * (k + 1)].tobytes())), 0)
        players.append(p)
    plain, slow = (p.render(100_000, 480)[:, 0] for p in players)
    assert plain[0] == 4_800 and slow[0] == 4_800 + 480     # 10 ms = 480 samples ahead


# ─── the room-audio session ───────────────────────────────────────────────


class _WS:
    def __init__(self, hello: dict[str, Any], then: list[dict[str, Any]]) -> None:
        self.inbox = [json.dumps(hello)] + [json.dumps(m) for m in then]
        self.sent: list[Any] = []

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        return self.inbox.pop(0)

    async def receive(self) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        if self.inbox:
            return {"type": "websocket.receive", "text": self.inbox.pop(0)}
        return {"type": "websocket.disconnect"}

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        pass

    async def close(self, code: int = 1000) -> None:
        pass


async def test_the_session_sends_the_stored_latency_and_routes_results() -> None:
    pl = RoomPlayout(InMemoryRoomRegistry({"sat_a": "kitchen"}), run_tasks=False)
    got: list[tuple] = []
    joined: list[tuple] = []
    pl.calibration = SimpleNamespace(on_result=lambda sid, m: got.append((sid, m["step"])),
                                     on_joined=lambda *a: joined.append(a))
    result = {"type": "calibrate_result", "run_id": "r", "step": 2, "heard": False}
    ws = _WS({"type": "hello", "proto": 1, "pairing_token": "t", "supports": ["calibrate"]},
             [result])

    async def ok(_sid: str, _tok: str | None) -> bool:
        return True

    async def stored(_sid: str) -> int | None:
        return 2_500

    # A socket from before (the device reconnects): replaced, its state forgotten.
    pl.attach("sat_a", Speaker(Room({}, {}, {}), "sat_a"))
    pl.set_latency("sat_a", 9_000)
    seen: list[bool] = []
    pl.calibration.on_joined = lambda *a: seen.append(pl.can_calibrate("sat_a")) or joined.append(a)
    sess = RoomAudioSession(ws, "sat_a", pl, auth=ok, latency_of=stored)  # type: ignore[arg-type]
    await sess.run()
    assert seen == [True]               # it takes part, as its hello said
    await asyncio.sleep(0)
    kinds = [m["type"] for m in ws.sent]
    assert kinds[:3] == ["ready", "timeline", "latency"]
    assert ws.sent[2]["output_latency_us"] == 2_500
    assert got == [("sat_a", 2)]
    assert joined == [("sat_a", "kitchen", True)]


# ─── the admin action's tier ──────────────────────────────────────────────


def test_the_calibrate_routes_are_admin_mutations() -> None:
    from domovoi.tests.test_rooms_registry import _gates

    assert admin_auth.require_admin_mutation in _gates(
        core_app, "POST", "/v1/admin/rooms/{room_id}/calibrate")
    assert admin_auth.require_admin_mutation in _gates(
        web_app, "POST", "/api/rooms/{room_id}/calibrate")


async def test_the_core_route_refuses_without_an_admin(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.tests.auth_testkit import HEADER, install_fake_db

    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="device-token")
    async with AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                           base_url="http://test") as c:
        nobody = await c.post("/v1/admin/rooms/kitchen/calibrate")
        household = await c.post("/v1/admin/rooms/kitchen/calibrate",
                                 headers={HEADER: "device-token"})
    assert nobody.status_code == 401
    assert household.status_code in (401, 403)


# ─── with the database ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def two_in_a_room():
    """kitchen: sat_a + sat_b (both paired); den: empty."""
    from domovoi.admin_auth import token_sha256
    from domovoi.db.repositories import (
        RoomsRepository,
        SatellitePairingRepository,
        SatellitesRepository,
    )
    from domovoi.db.session import session_scope

    tag = uuid.uuid4().hex[:6]
    h = {"kitchen": f"rc_kitchen_{tag}", "den": f"rc_den_{tag}",
         "a": f"rcsat_a_{tag}", "b": f"rcsat_b_{tag}"}
    async with session_scope() as s:
        rooms, sats = RoomsRepository(s), SatellitesRepository(s)
        await rooms.create(h["kitchen"], "Kitchen")
        await rooms.create(h["den"], "Den")
        for sid in (h["a"], h["b"]):
            await SatellitePairingRepository(s).pair(sid, token_sha256("t" * 64 + sid))
            assert await sats.move(sid, h["kitchen"])
            assert await rooms.delete(sid) == "deleted"
    yield h
    async with session_scope() as s:
        ids = [h["a"], h["b"]]
        await s.execute(text("DELETE FROM satellite_pairings WHERE satellite_id = ANY(:i)"), {"i": ids})
        await s.execute(text("DELETE FROM satellites WHERE satellite_id = ANY(:i)"), {"i": ids})
        await s.execute(text("DELETE FROM rooms WHERE room_id LIKE :p"), {"p": f"%{tag}%"})


@requires_db
async def test_values_are_stored_read_back_and_cleared_on_move(two_in_a_room) -> None:
    from domovoi.db.repositories import SatellitesRepository
    from domovoi.db.session import session_scope
    from domovoi.room_playout.session import stored_latency

    h = two_in_a_room
    assert await stored_latency(h["a"]) is None              # never calibrated: None
    await room_calibration.store_latencies({h["a"]: 0, h["b"]: 11_250})
    async with session_scope() as s:
        b = await SatellitesRepository(s).get(h["b"])
    assert b["output_latency_us"] == 11_250 and b["calibrated_at"] is not None
    assert await stored_latency(h["a"]) == 0
    async with session_scope() as s:
        assert await SatellitesRepository(s).move(h["b"], h["den"])
        b = await SatellitesRepository(s).get(h["b"])
    assert (b["output_latency_us"], b["calibrated_at"]) == (None, None)
    assert await stored_latency(h["b"]) is None


@requires_db
async def test_the_rooms_list_and_the_admin_action(two_in_a_room, monkeypatch, _db) -> None:  # noqa: F811
    """The drawer's read carries each satellite's value and the room's last
    run; the action answers 409 with a sentence when there's one speaker."""
    from httpx import ASGITransport, AsyncClient

    from domovoi import room_devices
    from domovoi.tests.auth_testkit import bearer, claim_admin, web_client

    h = two_in_a_room
    monkeypatch.setattr(core_app.state, "room_devices", None, raising=False)
    core_app.state.active_sessions = {}
    core_app.state.active_satellites = {}
    reg = room_devices.registry(core_app)
    await reg.refresh()
    pl = RoomPlayout(reg, run_tasks=False)
    monkeypatch.setattr(core_app.state, "room_playout", pl, raising=False)
    await room_calibration.store_latencies({h["b"]: 3_000})
    async with web_client() as w:
        token = await claim_admin(w)
    try:
        async with AsyncClient(transport=ASGITransport(app=core_app),
                               base_url="http://test") as c:
            doc = (await c.get("/v1/admin/rooms", headers=bearer(token))).json()
            kitchen = next(r for r in doc["rooms"] if r["room_id"] == h["kitchen"])
            vals = {s["satellite_id"]: s["output_latency_us"] for s in kitchen["satellites"]}
            assert vals == {h["a"]: None, h["b"]: 3_000}
            assert kitchen["tunable_speakers"] == 0 and kitchen["calibration"] is None
            r = await c.post(f"/v1/admin/rooms/{h['kitchen']}/calibrate", headers=bearer(token))
            assert r.status_code == 409 and "two speakers" in r.json()["detail"]
            r = await c.post("/v1/admin/rooms/rc_nowhere/calibrate", headers=bearer(token))
            assert r.status_code == 404
    finally:
        core_app.state.active_sessions = {}
        core_app.state.active_satellites = {}
    assert get_calibration(core_app).playout is pl
