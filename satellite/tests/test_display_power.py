"""satellite/display_power.py — power-mechanism fallback order + backlight
sysfs paths, all off-hardware via injected run/backlight dirs."""

from __future__ import annotations

import subprocess

from satellite import display_power


def _run_recorder(ok_cmds: set[str]):
    """A fake subprocess.run that succeeds only for argv[0] in ok_cmds."""
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        rc = 0 if cmd[0] in ok_cmds else 1
        return subprocess.CompletedProcess(cmd, rc)

    run.calls = calls
    return run


def _make_backlight(tmp_path, cur=128, mx=255):
    d = tmp_path / "backlight0"
    d.mkdir()
    (d / "brightness").write_text(str(cur))
    (d / "max_brightness").write_text(str(mx))
    (d / "bl_power").write_text("0")
    return d


def test_set_power_none_is_noop() -> None:
    assert display_power.set_power(True, method="none") is None


def test_pinned_method_only_tries_that_method(monkeypatch) -> None:
    run = _run_recorder({"xset"})
    monkeypatch.setattr(display_power, "_wayland_env", lambda: None)
    assert display_power.set_power(False, method="xset", run=run) == "xset"
    assert all(c[0] == "xset" for c in run.calls)


def test_auto_falls_back_to_backlight(monkeypatch, tmp_path) -> None:
    """No Wayland socket, xset fails → the backlight sysfs write wins."""
    bl = _make_backlight(tmp_path)
    run = _run_recorder(set())  # every subprocess fails
    monkeypatch.setattr(display_power, "_wayland_env", lambda: None)
    monkeypatch.setattr(display_power, "_backlight_dir", lambda: bl)
    assert display_power.set_power(False, method="auto", run=run) == "backlight"
    assert (bl / "bl_power").read_text() == display_power._BL_POWER_OFF
    assert display_power.set_power(True, method="auto", run=run) == "backlight"
    assert (bl / "bl_power").read_text() == display_power._BL_POWER_ON


def test_auto_returns_none_when_nothing_works(monkeypatch) -> None:
    run = _run_recorder(set())
    monkeypatch.setattr(display_power, "_wayland_env", lambda: None)
    monkeypatch.setattr(display_power, "_backlight_dir", lambda: None)
    assert display_power.set_power(True, method="auto", run=run) is None


def test_wlopm_used_when_socket_present(monkeypatch) -> None:
    run = _run_recorder({"wlopm"})
    monkeypatch.setattr(
        display_power, "_wayland_env",
        lambda: {"XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-0"},
    )
    assert display_power.set_power(True, method="auto", run=run) == "wlopm"
    assert run.calls[0][0] == "wlopm"


def test_get_brightness_percent(tmp_path) -> None:
    bl = _make_backlight(tmp_path, cur=128, mx=255)
    assert display_power.get_brightness(bl_dir=bl) == 50


def test_get_brightness_none_without_backlight() -> None:
    from pathlib import Path

    assert display_power.get_brightness(bl_dir=Path("/nonexistent-bl")) is None


def test_set_brightness_clamps_and_writes(tmp_path) -> None:
    bl = _make_backlight(tmp_path, cur=10, mx=200)
    assert display_power.set_brightness(150, bl_dir=bl) is True   # clamps to 100%
    assert (bl / "brightness").read_text() == "200"
    assert display_power.set_brightness(0, bl_dir=bl) is True     # floor 1%, not off
    assert int((bl / "brightness").read_text()) >= 1
