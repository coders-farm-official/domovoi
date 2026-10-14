"""The Windows satellite's platform pieces (spare devices, 2026-10-13).

What a ``windows_pc`` engine gets behind the engine's seams — its profile and
its honest ``supports`` list, the player and Core Audio volume, the battery,
the gates that keep POSIX root paths (plantable files at the root of the
system drive on Windows) out of reach, the device-name check, the test-only
audio hooks and the player itself — and that the Pi and Linux profiles get
exactly what they had. Pure parts run on any OS; the ones that call Windows
run on Windows only (read-only: no volume is changed, no microphone opened,
nothing played through the speakers).
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from satellite import debug_audio, devices, engine_platform as ep, pc_engine, win32, win_player
from satellite.tests._client_import import import_client

client = import_client()

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((ROOT / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="calls Windows itself")
# The player's MP3 decoder is a Windows-only requirement (requirements-windows.txt).
needs_miniaudio = pytest.mark.skipif(__import__("importlib.util").util.find_spec("miniaudio") is None,
                                     reason="miniaudio: the Windows satellite's MP3 decoder")


def win_cfg(**sections) -> "client.Config":
    doc = {
        "device": {"profile": "windows_pc"},
        "satellite": {"room_id": "den", "room_name": "Den", "domovoi_url": "ws://127.0.0.1:6370",
                      "server_fingerprint": FP},
    }
    for name, values in sections.items():
        doc.setdefault(name, {}).update(values)
    return client.Config.from_toml(doc)


# ─── the profile, the hello ───────────────────────────────────────────────

def test_the_windows_profile_is_a_plain_pc_with_board_windows_pc():
    p = devices.resolve("windows_pc")
    assert (p.capture_dtype, p.capture_channels, p.capture_select_channel) == ("int16", 1, None)
    assert p.supports_full_duplex is False and p.mic_gain_enabled is False
    assert p.leds_enabled_default is False and p.wifi_enabled_default is False
    assert p.music_alsa_device == "default" and p.output_mixer_control is None
    assert p.board == "windows_pc"
    cfg = win_cfg()
    assert ep.is_pc(cfg) and ep.is_windows_pc(cfg)
    assert not cfg.leds_enabled and not cfg.wifi_enabled and not cfg.mic_gain_enabled


def test_the_windows_hello_is_the_fixtures_windows_pc_hello():
    want = FIXTURES["devices"]["windows_pc"]["hello"]
    got = client.Satellite(win_cfg())._hello_frame()
    assert set(got) == set(want)
    for key in ("type", "sat_type", "mic_enabled", "speech_pause", "capture_control",
                "announce_after_session_end", "board", "supports_full_duplex", "supports"):
        assert got[key] == want[key], key
    assert not set(got["supports"]) & set(FIXTURES["devices"]["windows_pc"]["expect"]["not_allowed"])
    assert not set(got["supports"]) & {"plugin_payloads", "wifi", "leds", "mic_gain", "time_sync", "display"}


def test_the_linux_laptop_hello_is_unchanged():
    linux = client.Config.from_toml({"device": {"profile": "generic_pc"},
                                     "satellite": {"server_fingerprint": FP}})
    assert pc_engine.hello_fields(linux)["board"] == "linux_pc"


# ─── platform seams ───────────────────────────────────────────────────────

def test_a_windows_engine_gets_the_windows_player_core_audio_and_exit_to_restart():
    exits = []
    plat = ep.for_profile(win_cfg(), request_exit=exits.append)
    assert isinstance(plat.audio_out, ep.WindowsAudioOut)
    assert isinstance(plat.volume, ep.WindowsVolume)
    assert isinstance(plat.service, ep.ExitToRestart) and plat.control_socket is True
    plat.service.restart()
    assert exits == [ep.EXIT_RESTART]
    none = ep.for_profile(win_cfg(audio={"volume_backend": "none"}), request_exit=exits.append)
    assert isinstance(none.volume, ep.NoVolume)


@pytest.mark.parametrize("profile", ["respeaker_2mic_hat", "xvf3800_usb", "radxa_zero3w_video", "generic_pc"])
def test_every_other_profile_keeps_alsa_and_its_own_restart(profile):
    cfg = client.Config.from_toml({"device": {"profile": profile}})
    plat = ep.for_profile(cfg, request_exit=lambda c: None)
    assert isinstance(plat.audio_out, ep.AlsaAudioOut)
    assert not isinstance(plat.volume, ep.WindowsVolume)


def test_the_windows_player_argv():
    out = ep.WindowsAudioOut(python=r"C:\sat\python\python.exe")
    head = [r"C:\sat\python\python.exe", "-m", "satellite.win_player"]
    assert out.stream_argv("default", "http://h:8051") == head + ["stream", "http://h:8051", "--device", "default"]
    assert out.mp3_argv("default", r"C:\x\g.mp3", 1.0) == head + ["mp3", r"C:\x\g.mp3", "--device", "default"]
    assert out.mp3_argv("3", "g.mp3", 1.5)[-2:] == ["--gain", "1.5"]
    assert out.wav_argv("Speakers (Realtek)", "c.wav") == head + ["wav", "c.wav", "--device", "Speakers (Realtek)"]


@pytest.mark.parametrize("bad", ["x & calc.exe", "-f", "--gain=99", "a|b", "a\nb", "\"quoted\"", "x" * 81, "%TEMP%"])
def test_a_bad_device_name_never_reaches_the_player(bad):
    argv = ep.WindowsAudioOut(python="py").wav_argv(bad, "c.wav")
    assert argv[-2:] == ["--device", "default"]


@pytest.mark.parametrize("value,ok", [
    (None, True), ("default", True), (0, True), (12, True), ("Speakers (Realtek(R) Audio)", True),
    ("USB PnP Sound Device: Audio [3]", True), (1000, False), (-1, False), (True, False), (1.5, False),
    ("", False), ("-x", False), ("a;b", False), ("a&b", False), ("a\"b", False), ("x" * 81, False), (["x"], False),
])
def test_windows_device_names(value, ok):
    assert devices.valid_windows_device(value) is ok


def test_a_windows_config_refuses_a_bad_device_before_anything_uses_it():
    assert win_cfg(audio={"output_device": 3}).output_device == 3
    assert win_cfg(music={"alsa_device": "Speakers"}).music_alsa_device == "Speakers"
    for where, key in (("audio", "input_device"), ("audio", "output_device"), ("music", "alsa_device")):
        with pytest.raises(ValueError, match="not a Windows audio device"):
            win_cfg(**{where: {key: "x & calc.exe"}})
    # A Linux laptop's ALSA names are not held to the Windows rule here.
    client.Config.from_toml({"device": {"profile": "generic_pc"}, "music": {"alsa_device": "plughw:0,0"}})


def test_the_wake_acknowledgement_plays_in_the_engine_on_windows(tmp_path, monkeypatch):
    monkeypatch.setenv(debug_audio.SINK_ENV, f"wav:{tmp_path}")
    wav = tmp_path / "chime.wav"
    from satellite import chime

    chime.ensure(wav)
    out = ep.WindowsAudioOut(python="py")
    proc = out.spawn_ack(out.wav_argv("default", str(wav)))
    assert isinstance(proc, win_player.InProcessPlay)
    assert proc.wait(5) == 0 and proc.poll() == 0
    written = [p for p in tmp_path.glob("*-ack-wav.wav")]
    assert len(written) == 1


class FakeEndpoint:
    def __init__(self, level=0.4, fail=False):
        self.level, self.muted, self.fail, self.sets = level, True, fail, []

    def get_scalar(self):
        if self.fail:
            raise OSError("no default device")
        return self.level

    def set_scalar(self, level, *, unmute=True):
        if self.fail:
            raise OSError("no default device")
        self.sets.append(level)
        self.level = level
        if unmute:
            self.muted = False


def test_windows_volume_reads_and_sets_the_default_speakers_and_unmutes():
    ep_ = FakeEndpoint(0.404)
    vol = ep.WindowsVolume(endpoint=ep_)
    assert vol.available() and vol.get() == 40
    assert vol.set(65) is True and ep_.sets == [0.65] and ep_.muted is False
    assert vol.set(150) is True and ep_.sets[-1] == 1.0
    broken = ep.WindowsVolume(endpoint=FakeEndpoint(fail=True))
    assert broken.get() is None and broken.set(10) is False


# ─── the gates (POSIX root paths are plantable files on Windows) ──────────

def test_no_reboot_helper_on_a_windows_satellite_even_if_the_path_exists(tmp_path, monkeypatch):
    helper = tmp_path / "domovoi-reboot"
    helper.write_text("planted")
    monkeypatch.setattr(client, "REBOOT_HELPER", helper)
    assert client._reboot_helper_installed(win_cfg()) is False
    pi = client.Config.from_toml({"device": {"profile": "xvf3800_usb"}})
    assert client._reboot_helper_installed(pi) is True


def test_root_s_network_record_is_never_read_on_windows(monkeypatch):
    sat = client.Satellite(win_cfg())
    read = []
    monkeypatch.setattr(client.Satellite, "_read_net_report", lambda self, m: read.append(m))
    sat._check_net_settings()
    assert read == [] and sat._net_step is None


@windows_only
def test_the_setup_helper_never_runs_on_windows(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(client.subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(client.os, "access", lambda *a: True)
    client._setup_status("say-code", "482913")
    assert ran == []


@windows_only
def test_there_is_no_root_owned_pin_on_windows():
    from importlib import reload

    from satellite import server_identity

    # The module as Windows imports it (the conftest re-points paths).
    assert server_identity.sys.platform == "win32"
    fresh = reload(server_identity)
    try:
        assert fresh.ROOT_PIN is None
        assert fresh.pinned_fingerprint(FP) == (FP, "config")
    finally:
        reload(server_identity)


@pytest.mark.skipif(WINDOWS, reason="the POSIX pin path")
def test_the_root_owned_pin_stays_on_linux():
    from satellite import server_identity

    assert server_identity.ROOT_PIN is not None


def test_windows_pc_runs_on_windows_and_nothing_else_does(tmp_path, capsys):
    other = "generic_pc" if WINDOWS else "windows_pc"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'[device]\nprofile = "{other}"\n[satellite]\nroom_id = "den"\n'
                        f'domovoi_url = "ws://127.0.0.1:6370"\nserver_fingerprint = "{FP}"\n', encoding="utf-8")
    # Windows: exit 78, which its supervisor leaves stopped and the tray
    # explains; elsewhere the unit's 2, as before.
    assert client.main(["--config", str(cfg_path)]) == (ep.EXIT_CONFIG if WINDOWS else 2)
    assert "does not run on" in capsys.readouterr().err


# ─── how a moved core is looked for ───────────────────────────────────────

@pytest.mark.parametrize("cfg_doc,want", [
    ({"device": {"profile": "windows_pc"}}, "names"),
    ({"device": {"profile": "generic_pc"}}, "sweep"),
    ({"device": {"profile": "xvf3800_usb"}, "pc": {"server_search": "address"}}, "sweep"),
    ({"device": {"profile": "windows_pc"}, "pc": {"server_search": "address"}}, "address"),
    ({"device": {"profile": "generic_pc"}, "pc": {"server_search": "names"}}, "names"),
])
def test_server_search(cfg_doc, want):
    assert pc_engine.server_search(client.Config.from_toml(cfg_doc)) == want


def test_a_typo_in_server_search_fails_loud():
    with pytest.raises(ValueError, match="server_search"):
        client.Config.from_toml({"device": {"profile": "windows_pc"}, "pc": {"server_search": "lan"}})


@pytest.mark.parametrize("search,names,scan", [("sweep", True, True), ("names", True, False),
                                               ("address", False, False)])
def test_the_search_order_follows_server_search(monkeypatch, search, names, scan):
    from satellite import discovery

    seen = {}

    def locate(**kw):
        seen.update(kw)
        return None

    monkeypatch.setattr(discovery, "locate", locate)
    cfg = win_cfg(pc={"server_search": search})
    client._locate_server(cfg)
    assert bool(seen["names"]) is names and seen["scan"] is scan


# ─── the log path, the battery ────────────────────────────────────────────

def test_the_logs_verb_names_the_supervisor_s_folder_on_windows(monkeypatch, tmp_path):
    monkeypatch.setenv("DOMOVOI_SERVICE_SCOPE", "windows")
    monkeypatch.setenv("DOMOVOI_LOG_DIR", str(tmp_path))
    got = client.Satellite(win_cfg()).control_logs_path()
    assert got["kind"] == "file" and got["path"] == str(tmp_path) and "satellite.log" in got["hint"]


@pytest.mark.parametrize("ac,flag,pct,want", [
    (1, 8, 57, {"percent": 57, "charging": True, "on_power": True}),
    (0, 1, 80, {"percent": 80, "charging": False, "on_power": False}),
    (1, 1, 100, {"percent": 100, "charging": False, "on_power": True}),
    (255, 255, 66, {"percent": 66, "charging": None, "on_power": None}),
    (1, 128, 255, None),        # a desktop: no system battery
    (255, 255, 255, None),      # Windows can't say
    (1, 0, 200, None),
])
def test_windows_battery_is_the_health_shape(ac, flag, pct, want):
    got = win32.battery_from_status(ac, flag, pct)
    assert got == ({} if want is None else {"battery": want})


@windows_only
def test_the_real_battery_reads_without_privilege():
    got = win32.power_status()
    assert got == {} or set(got["battery"]) == {"percent", "charging", "on_power"}


# ─── Windows itself (read-only) ───────────────────────────────────────────

@windows_only
def test_who_this_user_is_and_the_pipe_name():
    sid = win32.current_user_sid()
    assert sid.startswith("S-1-5-")
    assert win32.control_pipe_name(sid) == r"\\.\pipe\domovoi-satellite-" + sid
    assert win32.process_user_sid(os.getpid()) == sid
    assert win32.peer_is_this_user(os.getpid()) is True
    assert win32.peer_is_this_user(4) is False, "the System process is nobody's user"
    assert win32.peer_is_this_user(None) is False


def test_the_pipe_dacl_admits_one_sid_and_nothing_else():
    assert win32.pipe_sddl("S-1-5-21-1-2-3-1001") == "D:P(A;;GA;;;S-1-5-21-1-2-3-1001)"
    for bad in ("S-1-5-21)(A;;GA;;;WD", "WD", "", "S-1-5-21 1"):
        with pytest.raises(ValueError):
            win32.pipe_sddl(bad)


@windows_only
def test_named_events_and_the_single_instance_mutex():
    name = f"Local\\domovoi-satellite-test-{uuid.uuid4().hex}"
    ev = win32.NamedEvent(name)
    try:
        assert not ev.is_set()
        other = win32.NamedEvent.open_existing(name)
        other.set()
        assert ev.wait(1)
        ev.reset()
        assert not other.is_set()
        other.close()
    finally:
        ev.close()
    assert win32.NamedEvent.open_existing(name) is None
    a = win32.SingleInstance(name + "-mx")
    b = win32.SingleInstance(name + "-mx")
    assert (a.already_running, b.already_running) == (False, True)
    a.close()
    b.close()


@windows_only
def test_core_audio_reads_the_default_speakers():
    # Read only: this test never changes anyone's volume.
    vol = win32.EndpointVolume()
    try:
        level = vol.get_scalar()
    except OSError as e:
        pytest.skip(f"no default output device here: {e}")
    assert 0.0 <= level <= 1.0
    assert isinstance(vol.get_mute(), bool)


@windows_only
def test_the_stop_event_shuts_a_windows_engine_down(monkeypatch):
    name = f"Local\\domovoi-satellite-test-stop-{uuid.uuid4().hex}"
    monkeypatch.setattr(win32, "object_name", lambda kind, sid=None: name)
    sat = client.Satellite(win_cfg())
    exits = []
    monkeypatch.setattr(sat, "_request_exit", exits.append)
    sat._watch_windows_stop()
    ev = win32.NamedEvent(name)
    ev.set()
    deadline = time.monotonic() + 5
    while not exits and time.monotonic() < deadline:
        time.sleep(0.05)
    ev.close()
    assert exits == [0]


# ─── the test-only audio hooks ────────────────────────────────────────────

def _wav(path: Path, samples: bytes, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples)
    return path


def test_without_the_variables_the_real_streams_are_made(monkeypatch):
    monkeypatch.delenv(debug_audio.MIC_ENV, raising=False)
    monkeypatch.delenv(debug_audio.SINK_ENV, raising=False)
    made = []
    fake_sd = SimpleNamespace(RawInputStream=lambda **kw: made.append(("in", kw)) or "IN",
                              RawOutputStream=lambda **kw: made.append(("out", kw)) or "OUT")
    assert debug_audio.input_stream(fake_sd, samplerate=16000) == "IN"
    assert debug_audio.output_stream(fake_sd, samplerate=22050) == "OUT"
    assert [m[0] for m in made] == ["in", "out"]


def test_the_debug_mic_plays_each_file_once_then_quiet_noise(tmp_path, monkeypatch):
    monkeypatch.setenv(debug_audio.MIC_ENV, str(tmp_path))
    loud = (b"\x00\x40" * 480) * 3                       # three 30 ms blocks of 0x4000
    _wav(tmp_path / "01.wav", loud)
    _wav(tmp_path / "00-wrong-rate.wav", b"\x00\x00" * 100, rate=8000)
    blocks = []
    stream = debug_audio.input_stream(None, samplerate=16000, channels=1, dtype="int16", blocksize=480,
                                      callback=lambda data, frames, t, s: blocks.append(bytes(data)))
    stream.start()
    deadline = time.monotonic() + 5
    while len(blocks) < 8 and time.monotonic() < deadline:
        time.sleep(0.02)
    stream.close()
    assert blocks[:3] == [b"\x00\x40" * 480] * 3
    assert all(max(abs(int.from_bytes(b[i:i + 2], "little", signed=True)) for i in range(0, 960, 2))
               <= debug_audio.NOISE_AMPLITUDE for b in blocks[3:8])
    assert (tmp_path / "01.wav.done").exists() and not (tmp_path / "01.wav").exists()
    assert (tmp_path / "00-wrong-rate.wav.done").exists()
    assert stream.played == ["01.wav"]


def test_the_debug_mic_takes_only_the_satellite_s_format(tmp_path):
    with pytest.raises(ValueError):
        debug_audio.FileMicStream(tmp_path, samplerate=16000, channels=2, dtype="int32",
                                  blocksize=480, callback=lambda *a: None)


def test_the_debug_sink_writes_a_wav_in_real_time(tmp_path, monkeypatch):
    monkeypatch.setenv(debug_audio.SINK_ENV, f"wav:{tmp_path}")
    out = debug_audio.output_stream(None, label="speech", samplerate=8000, channels=1, dtype="int16")
    out.start()
    t0 = time.monotonic()
    out.write(b"\x01\x00" * 1600)                         # 0.2 s
    elapsed = time.monotonic() - t0
    out.stop()
    out.close()
    assert elapsed >= 0.15
    written = list(tmp_path.glob("*-speech.wav"))
    with wave.open(str(written[0]), "rb") as w:
        assert (w.getframerate(), w.getnframes()) == (8000, 1600)


def test_a_bad_sink_value_is_an_error(monkeypatch):
    monkeypatch.setenv(debug_audio.SINK_ENV, "speakers")
    with pytest.raises(ValueError):
        debug_audio.sink_spec()


# ─── the player ───────────────────────────────────────────────────────────

def test_gain_is_applied_and_clipped():
    import array

    got = win_player.scale(array.array("h", [1000, -1000, 30000, -30000]), 2.0)
    assert list(got) == [2000, -2000, 32767, -32768]


@pytest.mark.parametrize("value,want", [("default", None), ("", None), ("3", 3), ("Speakers", "Speakers")])
def test_the_player_s_device_argument(value, want):
    assert win_player._device(value) == want


def test_the_player_refuses_a_bad_device_name():
    with pytest.raises(win_player.PlayerError):
        win_player._device("x & y")


def test_the_player_plays_a_wav_and_a_stopped_ack_stops_at_once(tmp_path, monkeypatch):
    monkeypatch.setenv(debug_audio.SINK_ENV, f"wav:{tmp_path / 'sink'}")
    wav = _wav(tmp_path / "c.wav", b"\x10\x00" * 16000)   # 1 s
    assert win_player.main(["wav", str(wav)]) == 0
    long = _wav(tmp_path / "long.wav", b"\x10\x00" * 16000 * 5)
    play = win_player.InProcessPlay("wav", str(long))
    time.sleep(0.2)
    t0 = time.monotonic()
    play.kill()
    assert play.wait(2) == -9 and time.monotonic() - t0 < 1.0
    with pytest.raises(subprocess.TimeoutExpired):
        win_player.InProcessPlay("wav", str(long)).wait(0.05)


@needs_miniaudio
def test_a_broken_file_is_a_reason_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(debug_audio.SINK_ENV, "null")
    bad = tmp_path / "x.mp3"
    bad.write_bytes(b"not an mp3")
    assert win_player.main(["mp3", str(bad)]) == 1
    assert "error:" in capsys.readouterr().err
    play = win_player.InProcessPlay("mp3", str(bad))
    assert play.wait(5) == 1 and play.error


FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(scope="module")
def mp3(tmp_path_factory) -> Path:
    if not FFMPEG:
        pytest.skip("ffmpeg makes the test MP3")
    out = tmp_path_factory.mktemp("mp3") / "tone.mp3"
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
                    "-ar", "44100", "-ac", "2", "-b:a", "128k", str(out)], check=True)
    return out


@needs_miniaudio
def test_the_player_decodes_an_mp3_with_gain(mp3, tmp_path, monkeypatch):
    monkeypatch.setenv(debug_audio.SINK_ENV, f"wav:{tmp_path}")
    pcm, rate, channels = win_player.decode_mp3(str(mp3), 0.5)
    assert (rate, channels) == (44100, 2) and len(pcm) > 44100 * 2 * 2
    assert win_player.main(["mp3", str(mp3), "--gain", "0.5"]) == 0
    assert list(tmp_path.glob("*-clip.wav"))


class _OneFile(http.server.BaseHTTPRequestHandler):
    body = b""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


@needs_miniaudio
def test_the_player_streams_http_mp3_like_mpg123(mp3, tmp_path, monkeypatch):
    monkeypatch.setenv(debug_audio.SINK_ENV, f"wav:{tmp_path}")
    _OneFile.body = mp3.read_bytes()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _OneFile)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/stream.mp3"
        assert win_player.main(["stream", url]) == 0
    finally:
        server.shutdown()
    written = list(tmp_path.glob("*-music.wav"))
    with wave.open(str(written[0]), "rb") as w:
        assert w.getnchannels() == 2 and w.getnframes() > 44100


@needs_miniaudio
def test_a_refused_stream_says_so_in_the_words_the_retry_logic_reads(capsys, monkeypatch):
    monkeypatch.setenv(debug_audio.SINK_ENV, "null")
    with __import__("socket").socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert win_player.main(["stream", f"http://127.0.0.1:{port}/"]) == 1
    err = capsys.readouterr().err
    assert client._music_exit_is_retryable(1, err), err
    assert win_player.main(["stream", "file:///C:/Windows/win.ini"]) == 1


# ─── after wf/sat-pc's review round (eb866ce): refuse at load, remote_config ──

def _bad_device_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text('[device]\nprofile = "windows_pc"\n[satellite]\nroom_id = "den"\n'
                    f'server_fingerprint = "{FP}"\n[music]\nalsa_device = "file:\\"|calc\\""\n',
                    encoding="utf-8")
    return path


def test_a_windows_satellite_refuses_a_bad_stored_device_name_like_a_laptop(tmp_path):
    """eb866ce: a Pi starts on its board's default and says so; a spare
    computer, Windows included, refuses: a person is at the keyboard."""
    with pytest.raises(ValueError, match=r"\[music\] alsa_device .* is not a Windows audio device"):
        client.Config.load(_bad_device_config(tmp_path))
    assert win_cfg().settings_ignored == ()


@pytest.mark.skipif(not WINDOWS, reason="the Windows engine's refusal")
def test_the_refusal_is_one_clear_line_and_exit_78(tmp_path, capsys):
    path = _bad_device_config(tmp_path)
    assert client.main(["--config", str(path)]) == ep.EXIT_CONFIG == 78
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("domovoi-satellite: not starting: [music] alsa_device")
    assert "--list-devices" in err[0] and str(path) in err[0]
    # A file that isn't TOML at all is refused the same way.
    path.write_text("[satellite\n", encoding="utf-8")
    assert client.main(["--config", str(path)]) == 78
    assert capsys.readouterr().err.startswith("domovoi-satellite: not starting: ")


@pytest.mark.parametrize("key", ["pc.server_search", "pc.permissions", "device.profile",
                                 "satellite.server_fingerprint", "satellite.domovoi_url",
                                 "satellite.server_fingerprint ", "wifi.enabled", "leds.enabled"])
def test_the_core_can_not_set_what_a_windows_pc_keeps_to_itself(key):
    from satellite import remote_config

    kept, refused = remote_config.screen({key: "x"}, pc=True, windows=True)
    assert kept == {} and refused


@pytest.mark.parametrize("key", ["audio.input_device", "audio.output_device", "music.alsa_device"])
def test_a_device_name_the_dashboard_sends_is_held_to_the_windows_rule(key):
    from satellite import remote_config

    good = "Speakers (Realtek(R) Audio)"
    assert remote_config.screen({key: good}, pc=True, windows=True)[0] == {key: good}
    assert remote_config.screen({key: 3}, pc=True, windows=True)[0] == {key: 3}
    for bad in ('file:"|calc"', "x & calc.exe", "-x", "a;b", "x" * 81):
        assert remote_config.screen({key: bad}, pc=True, windows=True)[0] == {}, bad
    # The ALSA rule still holds everywhere else.
    assert remote_config.screen({"music.alsa_device": good}, pc=True)[0] == {}


def test_set_config_writes_nothing_a_windows_satellite_would_refuse(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    original = ('[device]\nprofile = "windows_pc"\n[satellite]\nroom_id = "den"\n'
                f'server_fingerprint = "{FP}"\n')
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(client, "CONFIG_PATH", path)
    sat = client.Satellite(win_cfg())
    restarts = []
    monkeypatch.setattr(sat, "_restart_service", lambda: restarts.append(1) or True)
    # Each refused at the frame (the Windows rule)...
    sat._apply_config_changes({"music.alsa_device": "x & calc.exe", "audio.output_device": "-x"})
    # ...and one the frame lets through but the loader would refuse.
    sat._apply_config_changes({"wake.ack_mode": "bogus"})
    assert path.read_text(encoding="utf-8") == original and not restarts
    assert not (tmp_path / "config.toml.bak").exists()
    # A good one is written (the check is not refusing everything).
    sat._apply_config_changes({"music.alsa_device": "Speakers (Realtek(R) Audio)"})
    assert 'alsa_device = "Speakers (Realtek(R) Audio)"' in path.read_text(encoding="utf-8")
    assert restarts == [1]


def test_an_engine_that_can_not_claim_its_pipe_leaves_before_the_microphone_opens(monkeypatch):
    """F2 (security review): exit 78 is asked for in _start_control; run()
    returns right there, with no microphone, playback or session."""
    import asyncio

    sat = client.Satellite(win_cfg())

    async def no_pipe():
        sat._request_exit(ep.EXIT_CONFIG)

    monkeypatch.setattr(sat, "_arm_upgrade_watchdog", lambda: None)
    monkeypatch.setattr(sat, "_start_control", no_pipe)
    monkeypatch.setattr(sat, "_start_voice_input", lambda *a, **k: pytest.fail("the microphone opened"))
    asyncio.run(sat.run())
    assert sat.exit_code == 78
    assert getattr(sat, "_playback_thread", None) is None



@pytest.mark.skipif(not WINDOWS, reason="Windows' program search")
def test_the_health_sample_runs_no_linux_tool_on_windows(tmp_path, monkeypatch):
    """Security review F5: the sampler ran ``ip route`` (and vcgencmd,
    nmcli) by bare name every sample; Windows finds a bare name in the
    program's folder, the current folder and PATH first, so a planted
    ip.exe ran as the satellite's account. On Windows none of them runs."""
    from satellite import health

    bin_dir = tmp_path / "writable-path-dir"
    bin_dir.mkdir()
    shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe", bin_dir / "ip.exe")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.chdir(bin_dir)
    seen = []

    def spy(argv, timeout=5.0):
        out = health.run_tool(argv, timeout)
        seen.append((argv[0], out))
        return out

    s = health.Sampler(battery_reader=lambda: {})
    s.run = spy
    sample = s.host()
    assert seen and all(out is None for _tool, out in seen), seen   # nothing ran, the planted ip.exe included
    assert sample.get("gateway") is None
    assert health.run_tool(["ip", "route"]) is None
    assert health.read_default_gateway() is None
