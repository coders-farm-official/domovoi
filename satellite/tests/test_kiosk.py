"""satellite/kiosk.py — URL derivation + service liveness/restart helpers."""

from __future__ import annotations

import subprocess

from satellite import kiosk


def test_build_kiosk_url_derives_web_host() -> None:
    assert (
        kiosk.build_kiosk_url("ws://domovoi.local:6370", "kitchen")
        == "http://domovoi.local:6369/display.html?room=kitchen"
    )


def test_build_kiosk_url_ip_host() -> None:
    assert (
        kiosk.build_kiosk_url("ws://192.168.1.50:6370", "livingroom_tv")
        == "http://192.168.1.50:6369/display.html?room=livingroom_tv"
    )


def test_build_kiosk_url_wss_becomes_https() -> None:
    assert kiosk.build_kiosk_url("wss://domovoi.example:6370", "den").startswith(
        "https://domovoi.example:6369/"
    )


def test_build_kiosk_url_override_wins_verbatim() -> None:
    assert (
        kiosk.build_kiosk_url(
            "ws://domovoi.local:6370", "den", override="http://elsewhere/x"
        )
        == "http://elsewhere/x"
    )


def test_url_from_config(tmp_path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[satellite]\nroom_id = "tv_room"\n'
        'domovoi_url = "ws://10.0.0.2:6370"\n'
        "[display]\n",
        encoding="utf-8",
    )
    assert (
        kiosk.url_from_config(cfg)
        == "http://10.0.0.2:6369/display.html?room=tv_room"
    )


def _fake_run(returncode: int):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode)

    run.calls = calls
    return run


def test_kiosk_alive_true_on_zero_exit() -> None:
    run = _fake_run(0)
    assert kiosk.kiosk_alive(run=run) is True
    assert run.calls[0][:2] == ["systemctl", "is-active"]


def test_kiosk_alive_false_on_nonzero_or_error() -> None:
    assert kiosk.kiosk_alive(run=_fake_run(3)) is False

    def boom(cmd, **kwargs):
        raise FileNotFoundError("no systemctl")

    assert kiosk.kiosk_alive(run=boom) is False


def test_restart_kiosk_uses_sudo_noninteractive() -> None:
    run = _fake_run(0)
    assert kiosk.restart_kiosk(run=run) is True
    assert run.calls[0][:2] == ["sudo", "-n"]
    assert kiosk.KIOSK_UNIT in run.calls[0]
