"""Unit tests for the satellite's mic-gain auto-tune algorithm.

The tune algorithm is pure (input: PGA→dBFS samples; output: next PGA
to try) so we can exercise it without ALSA hardware or even a Pi —
``sample_fn`` becomes a lambda that simulates a known PGA-to-dBFS
mapping and the test asserts the algorithm converges into the target
window in a reasonable number of iterations.

Lives under domovoi/tests/ so the existing pytest config picks
it up; doesn't touch any domovoi code.
"""

from __future__ import annotations

from satellite.mic_gain import (
    in_window,
    interpolate_target_pga,
    tune_to_target,
)


def test_in_window() -> None:
    assert in_window(-55.0, -55.0, 5.0)
    assert in_window(-50.1, -55.0, 5.0)
    assert in_window(-59.9, -55.0, 5.0)
    assert not in_window(-49.9, -55.0, 5.0)
    assert not in_window(-60.1, -55.0, 5.0)


def test_interpolate_target_pga_uses_slope() -> None:
    """Two samples bracketing the target → linear interpolation lands
    near the predicted PGA."""
    samples = [(80, -15.0), (40, -35.0)]  # 0.5 dB per step
    pga = interpolate_target_pga(
        samples=samples, target_dbfs=-55.0, pga_min=0, pga_max=119
    )
    # Need 20 more dB drop from PGA=40 → 40 more steps → PGA=0.
    assert pga == 0


def test_interpolate_target_pga_clamps_to_max() -> None:
    """Predicted PGA above max gets clamped — no NaN, no negative values."""
    samples = [(0, -80.0), (40, -65.0)]
    pga = interpolate_target_pga(
        samples=samples, target_dbfs=-30.0, pga_min=0, pga_max=119
    )
    assert pga == 119


def test_interpolate_target_pga_falls_back_to_bisect_on_degenerate_slope() -> None:
    """Both samples produced the same dBFS (impossible-but-not-crashing
    edge case) → the algorithm bisects toward the right end."""
    samples = [(80, -20.0), (40, -20.0)]
    pga = interpolate_target_pga(
        samples=samples, target_dbfs=-55.0, pga_min=0, pga_max=119
    )
    # Too loud → bisect toward 0 from the most-recent (40).
    assert 0 <= pga < 40


def test_tune_to_target_converges_to_window() -> None:
    """End-to-end: simulate a 0.5 dB/step PGA mapping starting from the
    garage's actual symptoms (PGA=80 → -15 dBFS), drive the tune
    algorithm with a fake ALSA layer, and assert it lands in the
    target window inside the iteration budget."""
    state = {"pga": 80}
    set_calls: list[int] = []

    def fake_sample() -> float:
        # ambient_dbfs ≈ -55 + 0.5 * (pga - 0). Centered so PGA=110→0,
        # PGA=80→-15, PGA=0→-55 — matches what the garage logs showed.
        return -55.0 + 0.5 * state["pga"]

    # Monkey-patch the ALSA helpers used by tune_to_target to talk to
    # our `state` dict instead of a real `amixer` subprocess.
    import satellite.mic_gain as mg

    orig_get = mg.get_pga
    orig_set = mg.set_pga
    orig_detect = mg.detect_control
    try:
        mg.detect_control = lambda card: "PGA Capture Volume"
        mg.get_pga = lambda card, control: state["pga"]

        def fake_set(card, control, value):
            state["pga"] = value
            set_calls.append(value)
            return True

        mg.set_pga = fake_set

        changed, final_pga, final_median = tune_to_target(
            sample_fn=fake_sample,
            card=0,
            control_name=None,  # forces detect_control (now stubbed)
            target_dbfs=-55.0,
            tolerance_db=5.0,
            pga_min=0,
            pga_max=119,
            max_iterations=4,
        )
    finally:
        mg.get_pga = orig_get
        mg.set_pga = orig_set
        mg.detect_control = orig_detect

    assert changed is True
    assert final_pga is not None and final_median is not None
    # Target window is -50 to -60 dBFS; our linear sim should drop us in.
    assert -60.0 <= final_median <= -50.0
    # Should have converged in well under the iteration budget.
    assert len(set_calls) <= 3


def test_tune_to_target_no_op_when_already_in_window() -> None:
    """Starting PGA already produces in-window ambient → no amixer
    set call is fired, returns changed=False."""
    state = {"pga": 10}
    set_calls: list[int] = []

    def fake_sample() -> float:
        return -55.0 + 0.5 * state["pga"]  # PGA=10 → -50 dBFS, in window

    import satellite.mic_gain as mg

    orig_get, orig_set, orig_detect = mg.get_pga, mg.set_pga, mg.detect_control
    try:
        mg.detect_control = lambda card: "PGA Capture Volume"
        mg.get_pga = lambda card, control: state["pga"]

        def fake_set(card, control, value):
            state["pga"] = value
            set_calls.append(value)
            return True

        mg.set_pga = fake_set

        changed, final_pga, _ = tune_to_target(
            sample_fn=fake_sample,
            card=0,
            control_name=None,
            target_dbfs=-55.0,
            tolerance_db=5.0,
            pga_min=0,
            pga_max=119,
        )
    finally:
        mg.get_pga, mg.set_pga, mg.detect_control = orig_get, orig_set, orig_detect

    assert changed is False
    assert final_pga == 10
    assert set_calls == []


def test_tune_to_target_skips_when_no_control_detected() -> None:
    """No matching ALSA control → returns early, never samples."""
    sample_calls = {"n": 0}

    def fake_sample() -> float:
        sample_calls["n"] += 1
        return -55.0

    import satellite.mic_gain as mg

    orig_detect = mg.detect_control
    try:
        mg.detect_control = lambda card: None  # nothing on this card
        changed, final_pga, final_median = tune_to_target(
            sample_fn=fake_sample,
            card=0,
            control_name=None,
            target_dbfs=-55.0,
            tolerance_db=5.0,
            pga_min=0,
            pga_max=119,
        )
    finally:
        mg.detect_control = orig_detect

    assert changed is False
    assert final_pga is None
    assert final_median is None
    assert sample_calls["n"] == 0
