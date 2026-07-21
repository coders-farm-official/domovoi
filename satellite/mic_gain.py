"""Boot-time ALSA mic-gain auto-tune.

Why this exists: the satellite's noise-gate auto-tune sets a *software*
threshold against measured ambient. If the underlying *hardware* mic
gain is wrong (PGA Capture Volume way too high, like the garage Pi
reporting ambient at -10 dBFS), the noise-gate calibration refuses to
trust the contaminated sample and falls back to its default — leaving
VAD to chew through 100% gate-passing frames forever, blowing past
``max_record_seconds`` on every utterance.

This module fixes the upstream cause: at satellite boot, sample the
current ambient dBFS, and if it's outside the target window, walk the
ALSA PGA Capture Volume up or down until ambient lands in range. The
noise-gate calibration that runs immediately after sees clean input.

Once-per-boot, not runtime: PGA changes are persisted only via
``alsactl store``, which we don't trigger from here. Re-running at
boot keeps levels right without touching the global ALSA state file.

Tested by hand on the ReSpeaker 2-Mics Pi HAT V2 (WM8960 codec). The
WM8960's PGA range is 0–119 with roughly 0.5 dB per step, but we
don't rely on that constant — the algorithm samples at two PGA
values, computes the empirical slope, and interpolates the target.
"""

from __future__ import annotations

import logging
import re
import subprocess

log = logging.getLogger(__name__)


# Control names to try, in order. The WM8960 default ("PGA Capture
# Volume") covers ReSpeaker 2-Mics V2; the others are common
# alternatives on USB mics and other HATs.
_CANDIDATE_CONTROLS = (
    "PGA Capture Volume",
    "Capture Volume",
    "Mic Capture Volume",
)

# Maximum number of (sample → adjust) iterations before we give up and
# log whatever we've got. Each iteration costs `sample_sec` of wall
# clock at boot, so this caps boot delay.
_MAX_ITERATIONS = 4


# ─── ALSA mixer interface ─────────────────────────────────────────────────


def _amixer(card: int, *args: str) -> tuple[int, str]:
    """Run ``amixer -c <card> <args>`` and return (rc, stdout+stderr).

    Returns (-1, "") if amixer isn't installed at all — caller treats
    that as "skip the tune entirely, log a warning."
    """
    try:
        proc = subprocess.run(
            ["amixer", "-c", str(card), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return -1, ""
    except subprocess.TimeoutExpired:
        return -2, "amixer timed out"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def detect_control(card: int) -> str | None:
    """Pick the first ALSA control name that exists on this card and
    looks like a capture PGA. Returns None if nothing matches — the
    caller logs and skips."""
    for name in _CANDIDATE_CONTROLS:
        rc, _ = _amixer(card, "get", name)
        if rc == 0:
            return name
    return None


def get_pga(card: int, control: str) -> int | None:
    """Return the current PGA value (assumes both channels are equal —
    we set them in lockstep). None on read failure."""
    rc, out = _amixer(card, "get", control)
    if rc != 0:
        return None
    # `amixer get` prints something like:
    #   Simple mixer control 'PGA Capture Volume',0
    #     ...
    #     Front Left: Capture 80 [67%] [on]
    #     Front Right: Capture 80 [67%] [on]
    #
    # On non-stereo controls it's just one line. We grep for the first
    # integer after "Capture " or "Mono:".
    m = re.search(r"(?:Capture|Mono:)\s+(\d+)", out)
    if m:
        return int(m.group(1))
    return None


def set_pga(card: int, control: str, value: int) -> bool:
    """Set the PGA to ``value``. Returns True on apparent success."""
    value = max(0, value)
    rc, out = _amixer(card, "set", control, str(value))
    if rc != 0:
        log.warning("amixer set %r %d failed: %s", control, value, out.strip()[:200])
        return False
    return True


# ─── Tune algorithm (pure — easy to unit test) ────────────────────────────


def interpolate_target_pga(
    *,
    samples: list[tuple[int, float]],
    target_dbfs: float,
    pga_min: int,
    pga_max: int,
) -> int:
    """Given two-or-more (pga, observed_median_dbfs) samples, predict
    the PGA value that would land ambient at ``target_dbfs``.

    Uses the empirical slope between the two most-recent distinct
    samples (higher PGA = louder ambient, so slope should be > 0).
    Falls back to bisection when the slope is degenerate (e.g., both
    samples gave the same dBFS, or amixer rounded both to the same
    PGA value).

    Always clamps the result to [pga_min, pga_max].
    """
    distinct = [s for s in samples if s[0] is not None]
    if not distinct:
        return (pga_min + pga_max) // 2

    if len(distinct) >= 2:
        # Use the two most recent distinct PGA values for the slope.
        a, b = distinct[-2], distinct[-1]
        if a[0] != b[0]:
            slope = (b[1] - a[1]) / (b[0] - a[0])
            if abs(slope) > 0.05:  # ≥ 0.05 dB per step → trustworthy
                predicted = b[0] + (target_dbfs - b[1]) / slope
                return max(pga_min, min(pga_max, int(round(predicted))))

    # Degenerate slope OR only one sample. Bisect toward the right end.
    pga_now, dbfs_now = distinct[-1]
    if dbfs_now > target_dbfs:
        # Too loud — bisect toward 0.
        return max(pga_min, pga_now - max(1, (pga_now - pga_min) // 2))
    # Too quiet — bisect toward max.
    return min(pga_max, pga_now + max(1, (pga_max - pga_now) // 2))


def in_window(median_dbfs: float, target_dbfs: float, tolerance_db: float) -> bool:
    return abs(median_dbfs - target_dbfs) <= tolerance_db


# ─── Boot-time entry point ────────────────────────────────────────────────


def tune_to_target(
    *,
    sample_fn,
    card: int,
    control_name: str | None,
    target_dbfs: float,
    tolerance_db: float,
    pga_min: int,
    pga_max: int,
    max_iterations: int = _MAX_ITERATIONS,
) -> tuple[bool, int | None, float | None]:
    """Walk the ALSA PGA toward ``target_dbfs`` ambient median.

    ``sample_fn()`` returns the median dBFS of a fresh ambient sample
    (the caller is responsible for draining the mic queue first and
    waiting for the new gain to settle into the input stream). Pulled
    out as a callable so this function stays test-friendly — unit
    tests pass a lambda that simulates a known PGA→dBFS mapping.

    Returns ``(changed, final_pga, final_median)``:
      * ``changed`` — True if any amixer-set call fired AND succeeded.
      * ``final_pga`` — the PGA value last applied (or read), or None
        if we couldn't read at all.
      * ``final_median`` — the last sampled median, or None.

    Bails cleanly (no exception) on missing amixer / unknown control /
    unreadable PGA — logs a warning and returns ``(False, None, None)``
    so the caller's noise-gate calibration runs anyway.
    """
    if control_name is None:
        control_name = detect_control(card)
    if control_name is None:
        log.warning(
            "mic-gain auto-tune: no recognized capture control on card %d "
            "(tried %s) — skipping. Set [mic_gain] enabled = false in "
            "config.toml to silence this warning.",
            card, ", ".join(repr(n) for n in _CANDIDATE_CONTROLS),
        )
        return False, None, None

    initial = get_pga(card, control_name)
    if initial is None:
        log.warning(
            "mic-gain auto-tune: couldn't read current PGA from %r on card "
            "%d — skipping. Is alsa-utils installed?",
            control_name, card,
        )
        return False, None, None

    # First sample is at whatever the system was already set to. If it's
    # already in the target window, we're done — no amixer call.
    samples: list[tuple[int, float]] = []
    median = sample_fn()
    samples.append((initial, median))
    log.info(
        "mic-gain auto-tune: starting PGA=%d on %r → ambient median %.1f dBFS "
        "(target %.1f ± %.1f)",
        initial, control_name, median, target_dbfs, tolerance_db,
    )
    if in_window(median, target_dbfs, tolerance_db):
        log.info("mic-gain auto-tune: already in window, no change")
        return False, initial, median

    changed = False
    final_pga = initial
    final_median = median
    for i in range(max_iterations):
        next_pga = interpolate_target_pga(
            samples=samples,
            target_dbfs=target_dbfs,
            pga_min=pga_min,
            pga_max=pga_max,
        )
        if next_pga == final_pga:
            # Algorithm converged on the current value but window isn't
            # met — probably hardware can't go any further. Stop.
            log.info(
                "mic-gain auto-tune: converged at PGA=%d (median=%.1f dBFS) "
                "without reaching target window — stopping.",
                final_pga, final_median,
            )
            break
        if not set_pga(card, control_name, next_pga):
            break
        changed = True
        final_pga = next_pga
        median = sample_fn()
        samples.append((next_pga, median))
        final_median = median
        log.info(
            "mic-gain auto-tune: iter %d PGA=%d → ambient median %.1f dBFS",
            i + 1, next_pga, median,
        )
        if in_window(median, target_dbfs, tolerance_db):
            break

    if changed:
        log.info(
            "mic-gain auto-tune: done, %d → %d (ambient %.1f → %.1f dBFS)",
            initial, final_pga, samples[0][1], final_median,
        )
    return changed, final_pga, final_median
