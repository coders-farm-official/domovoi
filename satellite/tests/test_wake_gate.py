"""Harder to wake by accident (false wakes, 2026-11-09; satellite/wake_gate.py).

Rooms woke whenever a TV or a phone said "Jarvis": one 80 ms chunk over the
threshold was a wake, and the room listened again the instant a turn ended.
Now, by default and with no setup:

* **patience**: the score must be over the threshold on 2 chunks in a row
  (+80 ms on a real wake); a one-chunk blip is not a wake, and says so in
  the log;
* **cooldown**: 2 s after every turn no wake counts, and a swallowed one is
  logged with its score;
* the wake's **peak score** is in the log ring and goes to the core in
  `utterance_start` (`wake_score`);
* both settings load from `[wake]`, are on the dashboard's list, and a bad
  value falls back to the default;
* the **threshold in effect** is in the health sample beside the setting,
  and a dashboard edit of the sensitivity removes a pushed word's leftover
  threshold file.
"""

from __future__ import annotations

import json
import logging

import pytest

from satellite import remote_config, wake_gate
from satellite.tests.test_wake_ack import _sat, client, settle
from satellite.wake_gate import WakeGate


# ─── The gate ───────────────────────────────────────────────────────────


def test_one_chunk_over_the_threshold_is_not_a_wake() -> None:
    gate = WakeGate(0.5, patience=2, cooldown_sec=0)
    assert gate.feed(0.99, 1.0) is False
    assert gate.feed(0.1, 1.08) is False
    assert gate.take_short_run() == (0.99, 1)
    assert gate.take_short_run() is None          # once


def test_two_in_a_row_wake_with_the_peak() -> None:
    gate = WakeGate(0.5, patience=2, cooldown_sec=0)
    assert gate.feed(0.7, 1.0) is False
    assert gate.feed(0.93, 1.08) is True
    assert (gate.last_peak, gate.last_run) == (0.93, 2)
    # The run starts again after a wake.
    assert gate.feed(0.9, 1.16) is False


def test_patience_one_wakes_on_the_first_chunk_as_before() -> None:
    gate = WakeGate(0.5, patience=1, cooldown_sec=0)
    assert gate.feed(0.51, 1.0) is True
    assert gate.feed(0.5, 1.1) is False            # at the threshold is not over it


def test_a_far_field_wake_at_a_raised_threshold_still_wakes() -> None:
    # S6: threshold 0.8 (what the house set). The run counts against the
    # floor max(0.3, 0.8 x 0.6) = 0.48; the peak must clear 0.8.
    gate = WakeGate(0.8, patience=2, cooldown_sec=0)
    assert gate.floor == pytest.approx(0.48)
    assert [gate.feed(s, i * 0.08) for i, s in enumerate([0.1, 0.6, 0.85, 0.6])] == [
        False, False, True, False]
    assert (gate.last_peak, gate.last_run) == (0.85, 2)
    # Peak first, then a neighbour over the floor: wakes on the neighbour.
    gate = WakeGate(0.8, patience=2, cooldown_sec=0)
    assert [gate.feed(s, i * 0.08) for i, s in enumerate([0.85, 0.6])] == [False, True]


def test_a_lone_blip_at_a_raised_threshold_does_not_wake() -> None:
    gate = WakeGate(0.8, patience=2, cooldown_sec=0)
    assert not any(gate.feed(s, i * 0.08) for i, s in enumerate([0.1, 0.85, 0.15, 0.1]))
    assert gate.take_short_run() == (0.85, 1)


def test_a_run_over_the_floor_that_never_reaches_the_threshold_does_not_wake() -> None:
    gate = WakeGate(0.8, patience=2, cooldown_sec=0)
    assert not any(gate.feed(0.7, i * 0.08) for i in range(10))
    assert gate.take_short_run() is None          # nothing reached the threshold


def test_the_floor_never_sits_above_the_threshold() -> None:
    assert wake_gate.floor_for(0.5) == 0.3
    assert wake_gate.floor_for(0.2) == 0.2
    assert wake_gate.floor_for(1.0) == pytest.approx(0.6)


def test_a_reset_breaks_the_run() -> None:
    gate = WakeGate(0.5, patience=2, cooldown_sec=0)
    gate.feed(0.9, 1.0)
    gate.reset()                                   # a failed prediction between
    assert gate.feed(0.9, 1.16) is False
    assert gate.feed(0.9, 1.24) is True


def test_a_zero_cooldown_quiets_nothing() -> None:
    gate = WakeGate(0.5, patience=1, cooldown_sec=2.0)
    gate.quiet_for(10.0, 0.0)                      # after an ordinary answered turn
    assert gate.feed(0.9, 10.0) is True


def test_nothing_wakes_in_the_cooldown_and_the_swallowed_score_is_kept() -> None:
    gate = WakeGate(0.5, patience=2, cooldown_sec=2.0)
    gate.quiet_for(10.0)
    assert gate.in_cooldown(11.9)
    for t in (10.1, 10.2, 10.3, 11.9):
        assert gate.feed(0.98, t) is False
    assert not gate.in_cooldown(12.0)
    assert gate.take_cooldown_peak() == 0.98
    # A run straddling the end of the cooldown counts only its outside part.
    assert gate.feed(0.9, 12.0) is False
    assert gate.feed(0.9, 12.08) is True


def test_the_settings_fall_back_to_the_defaults() -> None:
    # 0.7 and 2 since 2026-11-14 (were 0.5 and 1): the owner's tuned rooms.
    assert wake_gate.THRESHOLD_DEFAULT == 0.7
    assert wake_gate.PATIENCE_DEFAULT == 2 and wake_gate.COOLDOWN_SEC_DEFAULT == 2.0
    assert wake_gate.patience_setting(None) == 2
    assert wake_gate.patience_setting(3) == 3
    assert wake_gate.patience_setting(0) == 1 and wake_gate.patience_setting(9) == 5
    for bad in ("x", True, 2.5):
        assert wake_gate.patience_setting(bad) == 2
    assert wake_gate.cooldown_setting(None) == 2.0
    assert wake_gate.cooldown_setting(0) == 0.0
    assert wake_gate.cooldown_setting(99) == 10.0 and wake_gate.cooldown_setting(-1) == 0.0
    for bad in ("x", True, float("nan")):
        assert wake_gate.cooldown_setting(bad) == 2.0


# ─── The config ─────────────────────────────────────────────────────────


def test_the_config_reads_them_and_reports_them() -> None:
    cfg = client.Config.from_toml({})
    assert (cfg.wake_threshold, cfg.wake_patience_frames, cfg.wake_cooldown_sec) == (0.7, 2, 2.0)
    cfg = client.Config.from_toml({"wake": {"patience_frames": 3, "cooldown_sec": 2.5}})
    assert (cfg.wake_patience_frames, cfg.wake_cooldown_sec) == (3, 2.5)
    assert {"wake.patience_frames", "wake.cooldown_sec"} <= remote_config.SETTABLE


# ─── The wake loop ──────────────────────────────────────────────────────


class _ScriptedOww:
    """A wake model that scores chunks from a script, then 0."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)

    def reset(self) -> None:
        pass

    def predict(self, chunk):
        return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}


def _start_frame(sat, loop) -> dict:
    sat._begin_utterance("wake_word")
    loop.run_until_complete(settle())
    while not sat.send_q.empty():
        kind, data = sat.send_q.get_nowait()
        if kind == "text":
            msg = json.loads(data)
            if msg.get("type") == "utterance_start":
                return msg
    raise AssertionError("no utterance_start")


def test_the_wake_loop_needs_two_in_a_row_and_sends_the_peak(monkeypatch, caplog) -> None:
    sat, loop = _sat(monkeypatch, "none", [])
    sat.cfg.wake_patience_frames = 2
    sat.cfg.wake_cooldown_sec = 0.0
    caplog.set_level(logging.INFO, logger="satellite")
    try:
        assert sat._wait_for_wake(_ScriptedOww([0.97, 0.0, 0.0, 0.81, 0.92])) is True
        start = _start_frame(sat, loop)
    finally:
        loop.close()
    lines = [r.getMessage() for r in caplog.records]
    assert any("wake score 0.970 for 1 chunk(s), 2 needed in a row: not a wake" in m for m in lines)
    assert any(m.startswith("wake word detected (peak score 0.920 over 2 chunk(s)") for m in lines)
    assert start["wake_score"] == 0.92
    assert "wake_ms" in start


def test_the_wake_loop_ignores_the_word_right_after_a_turn(monkeypatch, caplog) -> None:
    sat, loop = _sat(monkeypatch, "none", [])
    sat.cfg.wake_patience_frames = 1
    sat.cfg.wake_cooldown_sec = 2.0
    clock = {"now": 100.0}

    def _monotonic() -> float:
        return clock["now"]

    class _Oww(_ScriptedOww):
        def predict(self, chunk):
            clock["now"] += 0.08          # one chunk of time per prediction
            return super().predict(chunk)

    import time as real_time

    class _Time:
        # Only the client's own clock: everything else stays real.
        def __getattr__(self, name):
            return getattr(real_time, name)

        monotonic = staticmethod(_monotonic)

    monkeypatch.setattr(client, "time", _Time())
    caplog.set_level(logging.INFO, logger="satellite")
    # The last turn was ignored as background talk (or cut off at the cap,
    # or failed): the media says the word again at once (0.99 on the first
    # chunks), then a person says it 3 s later.
    sat._quiet_next_wake = True
    scores = [0.99, 0.99, 0.99] + [0.0] * 35 + [0.9]
    try:
        assert sat._wait_for_wake(_Oww(scores)) is True
        start = _start_frame(sat, loop)
    finally:
        loop.close()
    assert start["wake_score"] == 0.9
    assert sat._quiet_next_wake is False             # read once
    lines = [r.getMessage() for r in caplog.records]
    assert any("wake word ignored in the 2.0s after the last turn (peak score 0.990" in m
               for m in lines)


def test_after_an_ordinary_turn_the_wake_word_works_at_once(monkeypatch) -> None:
    # S5: "hey Jarvis" right after a reply still wakes.
    sat, loop = _sat(monkeypatch, "none", [])
    sat.cfg.wake_patience_frames = 1
    sat.cfg.wake_cooldown_sec = 2.0
    sat._quiet_next_wake = False
    try:
        assert sat._wait_for_wake(_ScriptedOww([0.99])) is True
        start = _start_frame(sat, loop)
    finally:
        loop.close()
    assert start["wake_score"] == 0.99


def test_a_failed_prediction_breaks_the_run(monkeypatch) -> None:
    sat, loop = _sat(monkeypatch, "none", [])
    sat.cfg.wake_patience_frames = 2
    sat.cfg.wake_cooldown_sec = 0.0

    class _Flaky(_ScriptedOww):
        def predict(self, chunk):
            if self.scores and self.scores[0] is None:
                self.scores.pop(0)
                raise RuntimeError("onnx hiccup")
            return super().predict(chunk)

    try:
        # 0.9, a failed chunk, 0.9 is not two in a row; the later pair is.
        assert sat._wait_for_wake(_Flaky([0.9, None, 0.9, 0.0, 0.9, 0.95])) is True
        start = _start_frame(sat, loop)
    finally:
        loop.close()
    assert start["wake_score"] == 0.95


def test_a_capture_cut_off_at_the_cap_asks_for_the_cooldown(monkeypatch) -> None:
    from satellite.tests.test_capture_endpointing import FRAME, FakeVad, capture, make_sat
    import asyncio
    import types

    # The real webrtcvad is a satellite-only dependency; a dev box may not
    # have it (test_capture_endpointing fakes it the same way).
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))

    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop, max_record_seconds=0.3)        # 10 frames
        sat._quiet_next_wake = False
        capture(sat, loop, [FRAME] * 20)
    finally:
        loop.close()
    assert sat._quiet_next_wake is True


def test_no_score_no_field(monkeypatch) -> None:
    sat, loop = _sat(monkeypatch, "none", [])
    try:
        sat._woke_at = None
        sat._wake_score = None
        start = _start_frame(sat, loop)
    finally:
        loop.close()
    assert "wake_score" not in start and "wake_ms" not in start


# ─── The threshold in effect ────────────────────────────────────────────


def test_health_reports_the_threshold_in_effect_beside_the_setting() -> None:
    sat = object.__new__(client.Satellite)
    sat.cfg = client.Config.from_toml({"wake": {"threshold": 0.8}})
    sat._wake_threshold = 0.45                   # a pushed word's leftover file
    assert sat._health_wake_facts() == {"wake_threshold": 0.45, "wake_threshold_setting": 0.8}
    sat._wake_threshold = 0.8
    assert sat._health_wake_facts() == {"wake_threshold": 0.8, "wake_threshold_setting": 0.8}


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[wake]\nthreshold = 0.5\n', encoding="utf-8")
    side = tmp_path / "wake_threshold"
    monkeypatch.setattr(client, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(client, "WAKE_THRESHOLD_SIDECAR", side)
    return cfg_path, side


def _sat_for_config(monkeypatch):
    sat = object.__new__(client.Satellite)
    sat.cfg = client.Config.from_toml({})
    restarts: list[int] = []
    monkeypatch.setattr(sat, "_restart_service", lambda: restarts.append(1), raising=False)
    return sat, restarts


def test_setting_the_sensitivity_removes_a_leftover_threshold(monkeypatch, config_home) -> None:
    cfg_path, side = config_home
    side.write_text("0.45\n", encoding="utf-8")
    sat, restarts = _sat_for_config(monkeypatch)
    sat._apply_config_changes({"wake.threshold": 0.8})
    assert restarts == [1]
    assert not side.exists()
    assert client._effective_wake_threshold(client.Config.from_toml(
        {"wake": {"threshold": 0.8}})) == 0.8


def test_another_setting_leaves_the_threshold_file_alone(monkeypatch, config_home) -> None:
    _cfg_path, side = config_home
    side.write_text("0.45\n", encoding="utf-8")
    sat, restarts = _sat_for_config(monkeypatch)
    sat._apply_config_changes({"wake.patience_frames": 3})
    assert restarts == [1]
    assert side.read_text(encoding="utf-8") == "0.45\n"


# ─── a pushed wake word's threshold is the [wake] setting (2026-11-11) ──
# A push used to park its threshold in a separate file that overrode the
# setting: every room woke at 0.5 while its Settings tab showed 0.8.


def _sat_for_push(monkeypatch, tmp_path, cfg: dict | None = None):
    sat, restarts = _sat_for_config(monkeypatch)
    if cfg is not None:
        sat.cfg = client.Config.from_toml(cfg)
    synced: list[int] = []
    monkeypatch.setattr(sat, "_sync_wake_models", lambda: synced.append(1), raising=False)
    monkeypatch.setattr(client, "WAKE_SIDECAR", tmp_path / "wake")
    return sat, restarts, synced


def _threshold_in(cfg_path) -> float:
    import tomllib
    return tomllib.loads(cfg_path.read_text(encoding="utf-8"))["wake"]["threshold"]


def test_a_push_writes_its_threshold_into_the_wake_setting(monkeypatch, config_home, tmp_path) -> None:
    cfg_path, side = config_home
    cfg_path.write_text('[wake]\n# how easily it wakes\nthreshold = 0.8\n', encoding="utf-8")
    sat, restarts, synced = _sat_for_push(monkeypatch, tmp_path, {"wake": {"threshold": 0.8}})
    sat._apply_wake_switch("home_word", threshold=0.5)
    assert _threshold_in(cfg_path) == 0.5
    assert "# how easily it wakes" in cfg_path.read_text(encoding="utf-8")   # merged, not rewritten
    assert cfg_path.with_name("config.toml.bak").read_text(encoding="utf-8").endswith("threshold = 0.8\n")
    assert not side.exists()                     # one number: no override file
    assert (tmp_path / "wake").read_text(encoding="utf-8") == "home_word\n"
    assert synced == [1] and restarts == [1]
    # What the rebooted device uses is what the Settings tab shows.
    loaded = client.Config.from_toml({"wake": {"threshold": _threshold_in(cfg_path)}})
    assert client._effective_wake_threshold(loaded) == 0.5


def test_a_push_also_removes_a_threshold_an_older_push_left(monkeypatch, config_home, tmp_path) -> None:
    cfg_path, side = config_home
    side.write_text("0.3\n", encoding="utf-8")
    sat, _restarts, _synced = _sat_for_push(monkeypatch, tmp_path)
    sat._apply_wake_switch("home_word", threshold=0.6)
    assert _threshold_in(cfg_path) == 0.6
    assert not side.exists()


def test_a_push_without_a_threshold_leaves_the_setting(monkeypatch, config_home, tmp_path) -> None:
    cfg_path, side = config_home
    cfg_path.write_text('[wake]\nthreshold = 0.8\n', encoding="utf-8")
    side.write_text("0.45\n", encoding="utf-8")   # an earlier word's leftover
    sat, restarts, synced = _sat_for_push(monkeypatch, tmp_path, {"wake": {"threshold": 0.8}})
    sat._apply_wake_switch("home_word", threshold=None)
    assert cfg_path.read_text(encoding="utf-8") == '[wake]\nthreshold = 0.8\n'
    assert not cfg_path.with_name("config.toml.bak").exists()
    assert not side.exists()
    assert synced == [1] and restarts == [1]


@pytest.mark.parametrize("bad", [1.7, -0.1, float("nan"), "loud"])
def test_a_push_with_a_threshold_out_of_range_leaves_the_setting(monkeypatch, config_home, tmp_path, bad) -> None:
    cfg_path, side = config_home
    sat, restarts, _synced = _sat_for_push(monkeypatch, tmp_path)
    sat._apply_wake_switch("home_word", threshold=bad)
    assert cfg_path.read_text(encoding="utf-8") == '[wake]\nthreshold = 0.5\n'
    assert not side.exists()
    assert restarts == [1]


def test_a_push_whose_config_cant_be_written_keeps_the_value_in_effect(monkeypatch, config_home, tmp_path) -> None:
    cfg_path, side = config_home
    cfg_path.unlink()                            # nothing to merge into
    sat, restarts, _synced = _sat_for_push(monkeypatch, tmp_path)
    sat._apply_wake_switch("home_word", threshold=0.55)
    assert side.read_text(encoding="utf-8") == "0.55\n"
    # ...and the health report shows it beside the setting (the dashboard's pill).
    assert client._effective_wake_threshold(client.Config.from_toml({})) == 0.55
    assert restarts == [1]


# ─── a leftover threshold file moves into [wake] threshold at start ──


def test_start_moves_a_leftover_threshold_into_the_setting(config_home, caplog) -> None:
    cfg_path, side = config_home
    cfg_path.write_text('[wake]\nthreshold = 0.8\n', encoding="utf-8")
    side.write_text("0.5\n", encoding="utf-8")
    cfg = client.Config.from_toml({"wake": {"threshold": 0.8}})
    with caplog.at_level(logging.INFO, logger=client.log.name):
        assert client._migrate_wake_threshold_sidecar(cfg, cfg_path) == 0.5
    assert _threshold_in(cfg_path) == 0.5         # what the device was using
    assert not side.exists()
    lines = [r.getMessage() for r in caplog.records if r.name == client.log.name]
    assert len(lines) == 1, lines                 # one line says what happened
    assert "wake sensitivity set to 0.5" in lines[0] and "removed" in lines[0]
    # Nothing left to do on the next start.
    assert client._migrate_wake_threshold_sidecar(
        client.Config.from_toml({"wake": {"threshold": 0.5}}), cfg_path) is None


def test_start_removes_a_leftover_that_matches_the_setting(config_home) -> None:
    cfg_path, side = config_home
    side.write_text("0.7\n", encoding="utf-8")  # the default setting
    assert client._migrate_wake_threshold_sidecar(client.Config.from_toml({}), cfg_path) is None
    assert not side.exists()
    assert not cfg_path.with_name("config.toml.bak").exists()   # nothing to write


@pytest.mark.parametrize("garbage", [b"", b"   \n", b"loud\n", b"7\n", b"nan\n", b"\xff\xfe\x00"])
def test_start_removes_a_garbage_leftover_and_keeps_the_setting(config_home, garbage) -> None:
    cfg_path, side = config_home
    cfg_path.write_text('[wake]\nthreshold = 0.8\n', encoding="utf-8")
    side.write_bytes(garbage)
    cfg = client.Config.from_toml({"wake": {"threshold": 0.8}})
    assert client._migrate_wake_threshold_sidecar(cfg, cfg_path) is None
    assert not side.exists()
    assert cfg_path.read_text(encoding="utf-8") == '[wake]\nthreshold = 0.8\n'


def test_start_leaves_an_unreadable_leftover_in_place(monkeypatch, config_home) -> None:
    cfg_path, side = config_home
    side.write_text("0.65\n", encoding="utf-8")
    real_read = type(side).read_text

    def denied(self, *a, **k):
        if self == side:
            raise PermissionError("denied")
        return real_read(self, *a, **k)

    monkeypatch.setattr(type(side), "read_text", denied)
    assert client._migrate_wake_threshold_sidecar(client.Config.from_toml({}), cfg_path) is None
    monkeypatch.undo()
    assert side.read_text(encoding="utf-8") == "0.65\n"
    assert cfg_path.read_text(encoding="utf-8") == '[wake]\nthreshold = 0.5\n'


def test_start_keeps_the_leftover_when_the_config_cant_take_it(config_home) -> None:
    cfg_path, side = config_home
    cfg_path.unlink()
    side.write_text("0.65\n", encoding="utf-8")
    cfg = client.Config.from_toml({})
    assert client._migrate_wake_threshold_sidecar(cfg, cfg_path) is None
    assert side.read_text(encoding="utf-8") == "0.65\n"     # still in effect, nothing changes
    assert client._effective_wake_threshold(cfg) == 0.65


def test_start_wires_the_migration_before_connecting(monkeypatch, config_home) -> None:
    """main() moves the leftover and hands the moved value to the Satellite."""
    cfg_path, side = config_home
    side.write_text("0.35\n", encoding="utf-8")
    seen: list[float] = []

    def stop(cfg, _path):
        seen.append(cfg.wake_threshold)
        return False                             # exit 3 before any network

    # A stand-in, never the real module: importing that here would fix its
    # paths to this test's home for every later test.
    import sys
    import types
    import satellite
    fake = types.SimpleNamespace(provisioning_active=lambda: False)
    monkeypatch.setitem(sys.modules, "satellite.provisioning_mode", fake)
    monkeypatch.setattr(satellite, "provisioning_mode", fake, raising=False)
    monkeypatch.setattr(client, "_resolve_server_url", stop)
    monkeypatch.setattr(client, "_ensure_config", lambda _p: True)
    monkeypatch.setattr(client.engine_platform, "is_windows_pc", lambda _c: client.sys.platform == "win32")
    monkeypatch.setattr(client.engine_platform, "windows_runtime_problem", lambda *_a, **_k: None)
    monkeypatch.setattr(client.pc_engine, "is_pc", lambda _c: False)
    monkeypatch.setattr(client.logging, "basicConfig", lambda **_k: None)
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))   # main() adds the ring + spool
    assert client.main(["--config", str(cfg_path)]) == 3
    assert seen == [0.35]
    assert not side.exists()
    assert _threshold_in(cfg_path) == 0.35


# ─── the tuning log: scores around a wake, and near misses (2026-11-11) ──


def test_scores_format_oldest_first_two_decimals():
    assert wake_gate.format_scores([0.04, 0.312, 0.9, 0.2]) == "0.04 0.31 0.90 0.20"
    assert wake_gate.format_scores([]) == ""


def test_a_run_that_never_reaches_the_threshold_is_a_near_miss_once():
    nm = wake_gate.NearMiss(0.8)
    seq = [0.1, 0.55, 0.72, 0.6, 0.1]
    out = [nm.feed(s, float(i)) for i, s in enumerate(seq)]
    assert out == [None, None, None, None, 0.72]


def test_a_run_that_reaches_the_threshold_is_not_a_near_miss():
    nm = wake_gate.NearMiss(0.8)
    out = [nm.feed(s, float(i)) for i, s in enumerate([0.6, 0.85, 0.6, 0.1])]
    assert out == [None, None, None, None]


def test_near_misses_are_rate_limited():
    nm = wake_gate.NearMiss(0.8)
    assert [nm.feed(s, t) for s, t in [(0.6, 0.0), (0.1, 0.1)]] == [None, 0.6]
    assert [nm.feed(s, t) for s, t in [(0.7, 2.0), (0.1, 2.1)]] == [None, None]
    assert [nm.feed(s, t) for s, t in [(0.7, 20.0), (0.1, 20.1)]] == [None, 0.7]


def test_a_low_threshold_never_has_a_floor_above_it():
    nm = wake_gate.NearMiss(0.4)
    assert nm.floor == 0.4
    assert [nm.feed(s, float(i)) for i, s in enumerate([0.39, 0.1])] == [None, None]
