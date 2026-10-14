"""The engine's platform seams (satellite/engine_platform.py).

A Pi must get exactly what it always had — the same player argv, the same
amixer calls, the same sudo'd systemctl restart, no control socket — and a
spare laptop the sound server's volume and a restart that needs no
privilege. Nothing here runs a real subprocess.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from satellite import _volume, devices, engine_platform as ep


class Done:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def recorder(answers):
    """A fake subprocess.run answering by argv[0] (a callable or a Done)."""
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        a = answers.get(argv[0])
        if isinstance(a, Exception):
            raise a
        if callable(a):
            return a(argv)
        return a or Done(1, err="not here")
    return run, calls


def pi_cfg(profile="xvf3800_usb", **kw):
    dev = devices.resolve(profile)
    base = dict(device=dev, music_alsa_device=dev.music_alsa_device,
                output_mixer_card=dev.output_mixer_card,
                output_mixer_control=dev.output_mixer_control)
    base.update(kw)
    return SimpleNamespace(**base)


# ─── audio out: the Pi's argv, byte for byte ──────────────────────────────

def test_the_music_player_argv_is_the_pi_s():
    assert ep.AlsaAudioOut().stream_argv("plughw:CARD=Array,DEV=0", "http://h:8051") == [
        "mpg123", "-q", "-b", "1024", "--devbuffer", "1.0",
        "-o", "alsa", "-a", "plughw:CARD=Array,DEV=0", "http://h:8051",
    ]


def test_the_clip_players_argv_are_the_pi_s():
    out = ep.AlsaAudioOut()
    assert out.mp3_argv("default", "/x/greet.mp3", 1.0) == [
        "mpg123", "-q", "-o", "alsa", "-a", "default", "/x/greet.mp3"]
    assert out.mp3_argv("default", "/x/greet.mp3", 2.0) == [
        "mpg123", "-q", *_volume.mpg123_scale_args(2.0), "-o", "alsa", "-a", "default", "/x/greet.mp3"]
    assert out.wav_argv("plughw:0,0", "/x/chime.wav") == ["aplay", "-q", "-D", "plughw:0,0", "/x/chime.wav"]


# ─── which seams a config gets ────────────────────────────────────────────

@pytest.mark.parametrize("profile", ["respeaker_2mic_hat", "xvf3800_usb", "radxa_zero3w_video"])
def test_a_pi_gets_amixer_sudo_systemctl_and_no_control_socket(profile):
    plat = ep.for_profile(pi_cfg(profile), request_exit=lambda c: pytest.fail("a Pi never exits to restart"))
    assert isinstance(plat.volume, ep.AmixerVolume)
    assert isinstance(plat.service, ep.SudoSystemctl)
    assert plat.control_socket is False
    dev = devices.resolve(profile)
    assert (plat.volume.card, plat.volume.control) == (dev.output_mixer_card, dev.output_mixer_control)


def test_a_builder_s_namespace_without_a_device_is_a_pi():
    plat = ep.for_profile(SimpleNamespace(), request_exit=lambda c: None)
    assert isinstance(plat.service, ep.SudoSystemctl)
    assert not plat.control_socket


def test_a_spare_pc_gets_auto_volume_exit_to_restart_and_the_socket():
    cfg = SimpleNamespace(device=SimpleNamespace(name="generic_pc"), music_alsa_device="default",
                          output_mixer_card="0", output_mixer_control="Master")
    exits = []
    plat = ep.for_profile(cfg, request_exit=exits.append)
    assert isinstance(plat.volume, ep.AutoVolume)
    assert isinstance(plat.service, ep.ExitToRestart)
    assert plat.control_socket is True
    assert plat.service.restart() is True and exits == [ep.EXIT_RESTART]


def test_a_real_laptop_config_gets_the_control_socket_and_can_turn_it_off():
    """From a parsed config.toml (control_enabled is None unless set)."""
    from satellite.tests._client_import import import_client

    client = import_client()
    cfg = client.Config.from_toml({"device": {"profile": "generic_pc"}})
    assert ep.for_profile(cfg, request_exit=lambda c: None).control_socket is True
    off = client.Config.from_toml({"device": {"profile": "generic_pc"}, "control": {"enabled": False}})
    assert ep.for_profile(off, request_exit=lambda c: None).control_socket is False
    pi = client.Config.from_toml({"device": {"profile": "xvf3800_usb"}})
    assert ep.for_profile(pi, request_exit=lambda c: None).control_socket is False


def test_probing_for_a_sound_server_is_quiet_until_one_answers(caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="satellite.platform")
    run, _ = recorder({"pactl": Done(1, err="Connection refused")})
    vol = ep.AutoVolume("0", None, run=run, which=lambda name: "/usr/bin/pactl" if name == "pactl" else None)
    assert vol.get() is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_an_explicit_volume_backend_wins():
    cfg = pi_cfg(volume_backend="pactl")
    assert isinstance(ep.for_profile(cfg, request_exit=lambda c: None).volume, ep.PactlVolume)
    cfg = pi_cfg(volume_backend="none")
    assert isinstance(ep.for_profile(cfg, request_exit=lambda c: None).volume, ep.NoVolume)


# ─── service control ──────────────────────────────────────────────────────

def test_the_pi_restart_is_the_sudoers_line():
    run, calls = recorder({"sudo": Done(0)})
    assert ep.SudoSystemctl(run=run).restart() is True
    assert calls == [["sudo", "-n", "/usr/bin/systemctl", "--no-block", "restart",
                      "domovoi-satellite.service"]]


def test_a_refused_or_missing_sudo_is_a_false_not_a_crash():
    run, _ = recorder({"sudo": Done(1, err="a password is required")})
    assert ep.SudoSystemctl(run=run).restart() is False
    run, _ = recorder({"sudo": FileNotFoundError("sudo")})
    assert ep.SudoSystemctl(run=run).restart() is False
    run, _ = recorder({"sudo": subprocess.TimeoutExpired("sudo", 10)})
    assert ep.SudoSystemctl(run=run).restart() is False


def test_the_module_level_run_is_looked_up_when_called(monkeypatch):
    """A test (or anything) replacing subprocess.run is honoured."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or Done(0))
    assert ep.SudoSystemctl().restart() is True
    assert calls and calls[0][0] == "sudo"


# ─── volume backends ──────────────────────────────────────────────────────

def test_amixer_reads_and_sets_the_profile_s_control():
    run, calls = recorder({"amixer": Done(0, out="  Front Left: Playback 40 [67%] [-20.00dB] [on]")})
    vol = ep.AmixerVolume("Array", "PCM", run=run)
    assert vol.get() == 67
    assert vol.set(80) is True
    assert calls == [_volume.get_command("Array", "PCM"), _volume.set_command("Array", "PCM", 80)]


def test_amixer_without_a_control_does_nothing():
    run, calls = recorder({})
    vol = ep.AmixerVolume("0", None, run=run)
    assert not vol.available() and vol.get() is None and vol.set(50) is False and calls == []


def test_wpctl_reads_and_sets_the_default_sink():
    run, calls = recorder({"wpctl": lambda argv: Done(0, out="Volume: 0.40\n")})
    vol = ep.WpctlVolume(run=run)
    assert vol.get() == 40
    assert vol.set(65) is True
    assert calls[1:] == _volume.wpctl_set_commands(65)


def test_pactl_reads_and_sets_the_default_sink():
    out = "Volume: front-left: 26214 /  40% / -23.88 dB,   front-right: 26214 /  40% / -23.88 dB\n"
    run, calls = recorder({"pactl": lambda argv: Done(0, out=out)})
    vol = ep.PactlVolume(run=run)
    assert vol.get() == 40
    assert vol.set(55) is True
    assert calls[1:] == _volume.pactl_set_commands(55)


def test_auto_takes_the_first_that_answers_and_keeps_it():
    run, calls = recorder({
        "wpctl": Done(1, err="Could not connect to PipeWire"),
        "pactl": lambda argv: Done(0, out="Volume: front-left: 1 /  30% / x"),
    })
    vol = ep.AutoVolume("0", "Master", run=run, which=lambda name: f"/usr/bin/{name}")
    assert vol.get() == 30
    assert vol.chosen().name == "pactl"
    calls.clear()
    vol.get()
    assert [c[0] for c in calls] == ["pactl"], "settled: wpctl is not asked again"


def test_auto_asks_again_until_a_sound_server_is_up():
    state = {"up": False}
    run, _ = recorder({"wpctl": lambda argv: Done(0, out="Volume: 0.25") if state["up"] else Done(1)})
    vol = ep.AutoVolume("0", None, run=run, which=lambda name: "/usr/bin/wpctl" if name == "wpctl" else None)
    assert vol.get() is None and vol.chosen() is None
    state["up"] = True
    assert vol.get() == 25 and vol.chosen().name == "wpctl"


def test_auto_with_nothing_installed_is_unavailable():
    vol = ep.AutoVolume("0", "Master", which=lambda name: None)
    assert not vol.available() and vol.get() is None and vol.set(10) is False


@pytest.mark.parametrize("out,want", [
    ("Volume: 0.40", 40), ("Volume: 1.00 [MUTED]", 100), ("Volume: 1.53", 100),
    ("Volume: 0.004", 0), ("nothing", None),
])
def test_wpctl_output_parses(out, want):
    assert _volume.parse_wpctl_volume(out) == want


@pytest.mark.parametrize("out,want", [
    ("Volume: mono: 65536 / 100% / 0.00 dB", 100),
    ("Volume: front-left: 0 /   0% / -inf dB", 0),
    ("Failed to get sink volume", None),
])
def test_pactl_output_parses(out, want):
    assert _volume.parse_pactl_volume(out) == want


def test_the_sound_server_commands_clamp_and_unmute():
    assert _volume.wpctl_set_commands(140)[0][-1] == "1.00"
    assert _volume.wpctl_set_commands(-5)[0][-1] == "0.00"
    assert _volume.wpctl_set_commands(40)[1] == ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"]
    assert _volume.pactl_set_commands(40) == [
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "40%"],
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
    ]


# ─── the client uses the seams ────────────────────────────────────────────

def test_the_client_restarts_a_pi_through_sudo(monkeypatch):
    from satellite import client

    sat = object.__new__(client.Satellite)
    sat.cfg = pi_cfg("respeaker_2mic_hat")
    calls = []
    monkeypatch.setattr(client.subprocess, "run", lambda argv, **kw: calls.append(argv) or Done(0))
    assert sat._restart_service() is True
    assert calls[0][:3] == ["sudo", "-n", "/usr/bin/systemctl"]


def test_the_client_exit_code_is_what_a_restart_asked_for():
    import threading

    from satellite import client

    sat = object.__new__(client.Satellite)
    sat.shutdown_event = threading.Event()
    sat._request_exit(ep.EXIT_RESTART)
    assert sat.exit_code == ep.EXIT_RESTART and sat.shutdown_event.is_set()
