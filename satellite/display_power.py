"""Screen power + brightness for video satellites.

Three mechanisms, tried in order under the default ``auto`` method (pin one
with ``[display] power_method`` when auto guesses wrong; ``none`` disables
control entirely — the dashboard toggle then degrades to a no-op):

  wlopm      — wlroots output-power-management client. Works under the cage
               kiosk compositor. Needs the compositor's Wayland socket; we
               locate the first ``/run/user/*/wayland-*`` socket and export
               XDG_RUNTIME_DIR/WAYLAND_DISPLAY for the call.
  xset       — X11 DPMS force on/off (kiosks running under X). Uses
               DISPLAY=:0.
  backlight  — ``/sys/class/backlight/<dev>/bl_power`` (0 = on, 4 = off)
               plus ``brightness``. The only mechanism that also supports a
               brightness percentage, and the most reliable one on DSI
               panels. Requires write access (VIDEO_SATELLITE.md documents
               the udev rule).

Every subprocess/sysfs access is injectable so the state machine is fully
unit-testable off-hardware. All calls are best-effort: a failure returns
False/None rather than raising — screen control degrading must never take
the satellite down.
"""

from __future__ import annotations

import glob
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_BL_POWER_ON = "0"
_BL_POWER_OFF = "4"  # FB_BLANK_POWERDOWN

METHODS = ("auto", "wlopm", "xset", "backlight", "none")


def _wayland_env() -> dict[str, str] | None:
    """Locate a compositor socket (/run/user/<uid>/wayland-<n>) and build the
    env vars wlopm needs. None when no socket exists (no compositor up)."""
    for sock in sorted(glob.glob("/run/user/*/wayland-*")):
        p = Path(sock)
        if p.name.endswith(".lock"):
            continue
        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = str(p.parent)
        env["WAYLAND_DISPLAY"] = p.name
        return env
    return None


def _backlight_dir() -> Path | None:
    hits = sorted(glob.glob("/sys/class/backlight/*"))
    return Path(hits[0]) if hits else None


def _try_wlopm(on: bool, run=subprocess.run) -> bool:
    env = _wayland_env()
    if env is None:
        return False
    try:
        r = run(
            ["wlopm", "--on" if on else "--off", "*"],
            env=env,
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _try_xset(on: bool, run=subprocess.run) -> bool:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    try:
        r = run(
            ["xset", "dpms", "force", "on" if on else "off"],
            env=env,
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _try_backlight(on: bool, bl_dir: Path | None = None) -> bool:
    d = bl_dir if bl_dir is not None else _backlight_dir()
    if d is None:
        return False
    try:
        (d / "bl_power").write_text(_BL_POWER_ON if on else _BL_POWER_OFF)
        return True
    except OSError:
        return False


def set_power(on: bool, method: str = "auto", run=subprocess.run) -> str | None:
    """Switch the panel on/off. Returns the mechanism that actually worked
    ("wlopm" | "xset" | "backlight"), or None when nothing did (including
    method="none"). Never raises."""
    if method == "none":
        return None
    order: tuple[str, ...]
    if method == "auto":
        order = ("wlopm", "xset", "backlight")
    elif method in METHODS:
        order = (method,)
    else:
        log.warning("unknown display power_method %r — treating as auto", method)
        order = ("wlopm", "xset", "backlight")
    for m in order:
        ok = (
            _try_wlopm(on, run=run)
            if m == "wlopm"
            else _try_xset(on, run=run)
            if m == "xset"
            else _try_backlight(on)
        )
        if ok:
            log.info("display power %s via %s", "on" if on else "off", m)
            return m
    log.warning(
        "display power %s: no mechanism worked (tried %s)",
        "on" if on else "off", ", ".join(order),
    )
    return None


def get_brightness(bl_dir: Path | None = None) -> int | None:
    """Current backlight brightness as a 0-100 percent, or None when the
    hardware exposes no backlight (HDMI monitors typically don't)."""
    d = bl_dir if bl_dir is not None else _backlight_dir()
    if d is None:
        return None
    try:
        cur = int((d / "brightness").read_text().strip())
        mx = int((d / "max_brightness").read_text().strip())
        if mx <= 0:
            return None
        return max(0, min(100, round(cur * 100 / mx)))
    except (OSError, ValueError):
        return None


def set_brightness(pct: int, bl_dir: Path | None = None) -> bool:
    """Set backlight brightness by percent. False when there's no backlight
    or the write fails. Clamps to 1% minimum so 'dim' never means 'off'
    (power is bl_power's job)."""
    d = bl_dir if bl_dir is not None else _backlight_dir()
    if d is None:
        return False
    try:
        mx = int((d / "max_brightness").read_text().strip())
        raw = max(1, round(max(1, min(100, int(pct))) * mx / 100))
        (d / "brightness").write_text(str(raw))
        return True
    except (OSError, ValueError):
        return False
