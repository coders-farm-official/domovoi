"""satellite/adaptive_gate.py: the per-capture gate for noisy rooms (pure)."""

from __future__ import annotations

import math

from satellite.adaptive_gate import CaptureGate, GateSettings, mic_score, percentile, pre_wake_floor

S = GateSettings()


def _pre_wake(room: float, wake: float = -20.0) -> list[float]:
    """3 s of room at ``room`` dBFS, then the wake phrase at ``wake``."""
    return [room] * 100 + [wake] * S.wake_skip_frames


def test_percentile_ignores_silence_and_empty():
    assert percentile([-math.inf, -50.0, -40.0, -30.0], 50) == -40.0
    assert percentile([-math.inf], 50) is None
    assert percentile([], 90) is None


def test_floor_leaves_out_the_wake_phrase():
    assert pre_wake_floor(_pre_wake(-42.0)) == -42.0


def test_floor_is_the_median_so_one_laugh_does_not_lift_it():
    pre = [-45.0] * 50 + [-15.0] * 10 + [-45.0] * 6 + [-20.0] * S.wake_skip_frames
    assert pre_wake_floor(pre) == -45.0


def test_no_floor_without_enough_room_audio():
    assert pre_wake_floor([-40.0] * (S.wake_skip_frames + 5)) is None
    assert pre_wake_floor([]) is None
    g = CaptureGate.from_pre_wake([], base_gate_dbfs=-55.0)
    assert g.threshold() == -55.0


def test_a_gate_set_above_the_room_stays():
    # a hand-set or recalibrated gate already above floor + margin wins
    g = CaptureGate.from_pre_wake(_pre_wake(-62.0), base_gate_dbfs=-45.0)
    assert g.threshold() == -45.0


def test_party_lifts_the_gate_above_the_chatter():
    g = CaptureGate.from_pre_wake(_pre_wake(-38.0), base_gate_dbfs=-57.0)
    assert g.threshold() == -38.0 + S.floor_margin_db


def test_speaker_level_takes_over_after_enough_speech():
    g = CaptureGate.from_pre_wake(_pre_wake(-50.0), base_gate_dbfs=-57.0)
    floor_gate = g.threshold()
    for _ in range(S.min_voiced_frames - 1):
        g.observe(-12.0, True)
    assert g.threshold() == floor_gate and g.speech_dbfs is None
    g.observe(-12.0, True)
    assert g.speech_dbfs == -12.0
    assert g.threshold() == -12.0 - S.drop_db


def test_unvoiced_and_silent_frames_do_not_count_as_speech_level():
    g = CaptureGate(base_gate_dbfs=-57.0)
    for _ in range(50):
        g.observe(-10.0, False)
        g.observe(-math.inf, True)
    assert g.speech_dbfs is None
    assert g.threshold() == -57.0


def test_a_soft_speaker_never_lowers_the_floor_gate():
    g = CaptureGate.from_pre_wake(_pre_wake(-40.0), base_gate_dbfs=-57.0)
    for _ in range(20):
        g.observe(-30.0, True)
    assert g.threshold() == max(-40.0 + S.floor_margin_db, -30.0 - S.drop_db)


def test_report_numbers():
    g = CaptureGate.from_pre_wake(_pre_wake(-40.04), base_gate_dbfs=-57.0)
    assert g.report() == {"floor_dbfs": -40.0, "speech_dbfs": None, "gate_dbfs": round(-40.04 + S.floor_margin_db, 1)}


def test_mic_score_is_relative_to_each_devices_own_floor():
    near = mic_score(_pre_wake(-40.0), [-20.0] * 50)
    # the same room through a device with 10 dB more gain (AGC): same score
    louder = mic_score(_pre_wake(-30.0), [-10.0] * 50)
    far = mic_score(_pre_wake(-40.0), [-28.0] * 50)
    assert near == louder == 20.0
    assert far is not None and far < near


def test_mic_score_unknown_without_floor_or_speech():
    assert mic_score([], [-20.0] * 10) is None
    assert mic_score(_pre_wake(-40.0), []) is None
