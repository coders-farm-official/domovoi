"""What a ``set_config`` frame may change (satellite/remote_config.py,
config_writer, alsa_names), on every board.

Regressions from the security review of wf/sat-pc (2026-10-13): R1 (a key
with a trailing space rewrote the pin and pointed the room off the LAN),
R2 (an ALSA ``file:"|cmd"`` device reached ``aplay -D``), R2b (the "ask me"
pop-up cut the key list, so padding hid the change), and the reviewer's
controls C1/C2, kept as they were written where they still hold.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from satellite import alsa_names, config_writer, control, engine_platform, pc_engine, remote_config, server_identity
from satellite.tests._client_import import import_client

client = import_client()
ROOT = Path(__file__).resolve().parents[2]
FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
EVIL = "SHA256:" + "E" * 43
PIPE = 'file:"|id > /tmp/pwned-by-set-config",raw'


def pc_sat(**sections):
    doc = {
        "device": {"profile": "generic_pc"},
        "satellite": {"room_id": "study", "room_name": "Study",
                      "domovoi_url": "ws://192.168.0.117:6370", "server_fingerprint": FP},
    }
    for name, values in sections.items():
        doc.setdefault(name, {}).update(values)
    return client.Satellite(client.Config.from_toml(doc))


def pi_sat():
    return client.Satellite(client.Config.from_toml({"device": {"profile": "xvf3800_usb"}}))


def installed_config(permissions: str = "automatic", profile: str = "generic_pc") -> str:
    text = (f'[device]\nprofile = "{profile}"\n'
            '[satellite]\nroom_id = "study"\ndomovoi_url = "ws://192.168.0.117:6370"\n'
            f'server_fingerprint = "{FP}"\n'
            f'[pc]\npermissions = "{permissions}"\n[music]\n')
    client.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    client.CONFIG_PATH.write_text(text, encoding="utf-8")
    return text


# ─── R1: no key variant reaches the pin or the address, on any board ─────

@pytest.mark.parametrize("make", [pc_sat, pi_sat], ids=["laptop", "pi"])
@pytest.mark.parametrize("key", [
    "satellite.server_fingerprint ", " satellite.server_fingerprint", "satellite.server_fingerprint",
    "satellite .server_fingerprint", "Satellite.server_fingerprint", "satellite.server_fingerprint\t",
])
def test_R1_no_spelling_of_the_pin_key_changes_the_pin(make, key):
    sat = make()
    before = installed_config(profile="generic_pc" if make is pc_sat else "xvf3800_usb")
    sat._restart_service = lambda: pytest.fail("nothing applied, nothing to restart")
    sat._apply_config_changes({key: EVIL, "satellite.domovoi_url": "ws://203.0.113.9:6370"})
    assert client.CONFIG_PATH.read_text(encoding="utf-8") == before
    cfg = client.Config.load(client.CONFIG_PATH)
    assert cfg.server_fingerprint == FP and cfg.domovoi_url == "ws://192.168.0.117:6370"
    assert server_identity.pinned_fingerprint(cfg.server_fingerprint) == (FP, "config")


@pytest.mark.parametrize("key", ["wake.threshold ", " wake.threshold", "Wake.threshold", "wake.threshold\t",
                                 "wake..threshold", "wake.threshold.x", "wake.thresh\u043eld", "wake.threshold\n"])
@pytest.mark.parametrize("pc", [True, False], ids=["laptop", "pi"])
def test_R1_the_screen_refuses_a_near_miss_of_a_settable_key(key, pc):
    """Not only the pin: a key that would become a settable one once
    tidied up is refused as it is, before any writer sees it."""
    accepted, refused = remote_config.screen({key: 0.5}, pc=pc)
    assert accepted == {} and refused == [repr(key)[:80]]
    assert remote_config.screen({"wake.threshold": 0.5}, pc=pc) == ({"wake.threshold": 0.5}, [])


def test_R1_the_writer_itself_refuses_a_key_that_is_not_canonical():
    for key in ("satellite.server_fingerprint ", "a.b.c", "a", ".b", "A.b", "a. b", "a.b\n"):
        with pytest.raises(ValueError):
            config_writer.apply_changes("[satellite]\nserver_fingerprint = \"x\"\n", {key: "y"})


def test_R1_a_value_cannot_break_out_of_its_line():
    with pytest.raises(ValueError):
        config_writer.apply_changes("[log]\nlevel = \"INFO\"\n",
                                    {"log.level": 'DEBUG"\nserver_fingerprint = "SHA256:x'})


@pytest.mark.parametrize("key", ["satellite.domovoi_url", "satellite.room_id", "device.profile",
                                 "device.full_duplex", "pc.permissions", "leds.xvf_host_path",
                                 "wake.wake_word_model_path", "control.enabled", "health.enabled"])
def test_only_the_dashboard_s_settings_are_settable_on_any_board(key):
    assert remote_config.screen({key: "x"}, pc=False) == ({}, [repr(key)])
    assert remote_config.screen({key: "x"}, pc=True) == ({}, [repr(key)])


def test_a_laptop_takes_none_of_the_sections_it_never_declared():
    for key in ("wifi.enabled", "leds.enabled", "mic_gain.enabled", "display.idle_mode"):
        assert remote_config.screen({key: True}, pc=True)[0] == {}
        assert remote_config.screen({key: True}, pc=False)[0] == {key: True}


def test_the_settable_keys_are_the_dashboard_s_and_the_report_s():
    """remote_config.SETTABLE == domovoi/satellite_config_schema.py's keys
    == client._config_report's keys (read as text: the satellite tests
    never import the core)."""
    schema = (ROOT / "domovoi" / "satellite_config_schema.py").read_text(encoding="utf-8")
    schema_keys = set(re.findall(r'^\s+"([a-z_]+\.[a-z_]+)", "', schema, re.M))
    sat = pc_sat()
    assert set(sat._config_report()) == remote_config.SETTABLE == schema_keys


# ─── R2: an ALSA name that runs a command never gets in or out ───────────

@pytest.mark.parametrize("make", [pc_sat, pi_sat], ids=["laptop", "pi"])
def test_R2_a_pipe_device_is_refused_by_set_config(make):
    sat = make()
    before = installed_config(profile="generic_pc" if make is pc_sat else "xvf3800_usb")
    sat._restart_service = lambda: pytest.fail("nothing applied")
    sat._apply_config_changes({"music.alsa_device": PIPE})
    assert client.CONFIG_PATH.read_text(encoding="utf-8") == before


def test_R2_a_laptop_config_carrying_one_does_not_load():
    """A spare computer has a terminal and a person at it: it refuses."""
    with pytest.raises(ValueError, match="alsa_device"):
        client.Config.from_toml({"device": {"profile": "generic_pc"}, "music": {"alsa_device": PIPE}})
    for audio in ({"output_mixer_card": "0;reboot"}, {"output_mixer_control": "PCM`id`"},
                  {"volume_backend": "bogus"}):
        with pytest.raises(ValueError):
            client.Config.from_toml({"device": {"profile": "generic_pc"}, "audio": audio})
    with pytest.raises(ValueError, match="permissions"):
        client.Config.from_toml({"device": {"profile": "generic_pc"}, "pc": {"permissions": "sometimes"}})


BAD_STORED = {
    "music": {"alsa_device": PIPE},
    "audio": {"output_mixer_card": "0;reboot", "output_mixer_control": "PCM`id`", "volume_backend": "bogus"},
    "pc": {"permissions": "sometimes"},
}


@pytest.mark.parametrize("profile", ["xvf3800_usb", "respeaker_2mic_hat", "radxa_zero3w_video"])
def test_a_pi_with_a_bad_stored_value_starts_on_its_profile_s_default_and_says_so(profile, caplog):
    """A Pi has no SSH: a config that does not load leaves it dead until its
    card is reflashed. It logs loudly, runs on the profile's default for
    each such key, and says so in its hello and every health sample."""
    from satellite import devices

    prof = devices.resolve(profile)
    with caplog.at_level("ERROR", logger="satellite"):
        cfg = client.Config.from_toml({"device": {"profile": profile}, **BAD_STORED})
    assert cfg.music_alsa_device == prof.music_alsa_device
    assert cfg.output_mixer_card == str(prof.output_mixer_card)
    assert cfg.output_mixer_control == prof.output_mixer_control
    assert cfg.volume_backend == "" and cfg.permissions == "automatic"
    assert cfg.settings_ignored == (
        "[pc] permissions setting ignored: not automatic or ask",
        "volume backend setting ignored: not a known backend",
        "music device setting ignored: not a plain ALSA name",
        "output mixer card setting ignored: not a card index or name",
        "output mixer control setting ignored: not a mixer control name",
    )
    assert caplog.text.count("using the " + profile + " profile's default") == 5
    sat = client.Satellite(cfg)
    assert sat._hello_frame()["settings_ignored"] == list(cfg.settings_ignored)
    assert sat._health_process_facts(time.monotonic())["settings_ignored"] == list(cfg.settings_ignored)
    # Still refused at the moment of use, whatever the config held.
    out = engine_platform.AlsaAudioOut()
    assert PIPE not in out.wav_argv(PIPE, "/tmp/x.wav") and "default" in out.wav_argv(PIPE, "/tmp/x.wav")


def test_a_healthy_pi_says_nothing_new():
    sat = client.Satellite(client.Config.from_toml({"device": {"profile": "xvf3800_usb"}}))
    assert sat.cfg.settings_ignored == ()
    assert "settings_ignored" not in sat._hello_frame()
    assert "settings_ignored" not in sat._health_process_facts(time.monotonic())


def test_R2_the_player_never_passes_one_on():
    out = engine_platform.AlsaAudioOut()
    assert out.wav_argv(PIPE, "chime.wav") == ["aplay", "-q", "-D", "default", "chime.wav"]
    assert "-a" in out.mp3_argv(PIPE, "x.mp3", 1.0) and PIPE not in out.mp3_argv(PIPE, "x.mp3", 1.0)
    assert PIPE not in out.stream_argv(PIPE, "http://192.168.0.117:8050")


@pytest.mark.parametrize("name", [
    "default", "null", "pulse", "pipewire", "dmix", "hw:0", "hw:1,0", "plughw:0,0",
    "plughw:CARD=Array,DEV=0", "sysdefault:CARD=PCH", "front:CARD=PCH,DEV=0", "plug:dmix",
    "hdmi:CARD=HDMI,DEV=0", "bluealsa", "bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp", "my_usb-dac",
])
def test_plain_device_names_are_taken(name):
    assert alsa_names.safe_pcm(name)


@pytest.mark.parametrize("name", [
    PIPE, 'file:"/tmp/x",raw', "file:/tmp/x,raw", "file", "pipe", "shm", "plug:'hw:0'",
    'plug:"file:x"', "plug:file:x", "hw:0|id", "hw:0;id", "hw:0 0", "", " default",
    "default\n", "../x", "/dev/snd/pcmC0D0p", "tee:hw:0,x", "exec:id", "bluealsa:DEV=AA:BB",
    "x" * 70, "hw:" + "1" * 70, "ladspa:x",
])
def test_names_that_reach_further_are_refused(name):
    assert not alsa_names.safe_pcm(name)


def test_mixer_card_and_control_are_checked_too():
    assert alsa_names.safe_card("Array") and alsa_names.safe_card("0")
    assert not alsa_names.safe_card("0;id") and not alsa_names.safe_card("x" * 40)
    assert alsa_names.safe_control("Master") and alsa_names.safe_control("Speaker,0")
    assert not alsa_names.safe_control("PCM\n") and not alsa_names.safe_control('a"b')
    with pytest.raises(ValueError):
        client.Config.from_toml({"device": {"profile": "generic_pc"}, "audio": {"output_mixer_card": "0 -D file"}})
    for key, bad in (("audio.output_mixer_card", "0 -D file"), ("audio.output_mixer_control", 'a"b'),
                     ("music.alsa_device", "pipe:x")):
        for pc in (True, False):
            assert remote_config.screen({key: bad}, pc=pc)[0] == {}, (key, pc)
    assert remote_config.screen({"audio.output_mixer_card": 1}, pc=False)[0] == {"audio.output_mixer_card": 1}
    assert remote_config.screen({"audio.output_mixer_control": "PCM"}, pc=False)[0] == {"audio.output_mixer_control": "PCM"}


@pytest.mark.parametrize("profile", ["respeaker_2mic_hat", "xvf3800_usb", "radxa_zero3w_video", "generic_pc"])
def test_every_profile_s_own_devices_are_plain(profile):
    from satellite import devices

    p = devices.resolve(profile)
    assert alsa_names.safe_pcm(p.music_alsa_device)
    assert not p.provisioned_music_alsa_device or alsa_names.safe_pcm(p.provisioned_music_alsa_device)
    assert alsa_names.safe_card(p.output_mixer_card)


# ─── R2b: "ask me" shows every change, in full ───────────────────────────

def test_R2b_the_allow_prompt_lists_every_change_and_nothing_hides_behind_padding(monkeypatch):
    sat = pc_sat(pc={"permissions": "ask"})
    applied = []
    monkeypatch.setattr(sat, "_apply_config_changes", lambda c: applied.append(c))
    pad = {f"audio.a{'a' * 60}{i}": 1 for i in range(5)}     # not settable: refused, not hidden
    sat._handle_text_frame({"type": "set_config",
                            "changes": {**pad, "music.alsa_device": "hw:1,0", "wake.threshold": 0.6}})
    pending = sat.control_status()["pending"]
    assert len(pending) == 1
    assert pending[0]["details"] == ["music.alsa_device = 'hw:1,0'", "wake.threshold = 0.6"]
    got = control.handle(sat, {"verb": "approve", "action": pending[0]["id"]})
    assert got["ok"]
    deadline = time.monotonic() + 3
    while not applied and time.monotonic() < deadline:
        time.sleep(0.01)
    assert applied == [{"music.alsa_device": "hw:1,0", "wake.threshold": 0.6}]


def test_R2b_a_frame_of_only_refused_keys_holds_nothing(monkeypatch):
    sat = pc_sat(pc={"permissions": "ask"})
    sat._handle_text_frame({"type": "set_config", "changes": {"music.alsa_device": PIPE,
                                                                "satellite.server_fingerprint": EVIL}})
    # The pipe device is a settable key with a value the frame may not
    # carry: refused at the screen, so nothing is even held for an OK.
    assert sat.control_status()["pending"] == []
    before = installed_config("ask")
    sat._restart_service = lambda: pytest.fail("nothing applied")
    sat._apply_config_changes({"music.alsa_device": PIPE})
    assert client.CONFIG_PATH.read_text(encoding="utf-8") == before


# ─── the reviewer's controls, as written ─────────────────────────────────

def test_C1_exact_protected_keys_and_sections_are_dropped():
    kept, refused = pc_engine.filter_config_changes({
        "pc.permissions": "automatic", "device.full_duplex": True,
        "satellite.server_fingerprint": EVIL})
    assert kept == {} and len(refused) == 3


def test_C2_a_new_pc_section_variant_cannot_land():
    sat = pc_sat()
    installed_config("ask")
    sat._restart_service = lambda: True
    for key in ("pc .permissions", " pc.permissions", "device .full_duplex"):
        sat._apply_config_changes({key: "automatic" if "perm" in key else True})
    cfg = client.Config.load(client.CONFIG_PATH)
    assert cfg.permissions == "ask" and cfg.full_duplex_override is None


# ─── a laptop's server moves only within the home network ────────────────

def test_discovery_never_moves_a_laptop_off_the_home_network(tmp_path):
    from types import SimpleNamespace

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[satellite]\ndomovoi_url = "ws://192.168.0.117:6370"\n', encoding="utf-8")
    sat = pc_sat()
    found = SimpleNamespace(url="ws://203.0.113.9:6370", via="name", detail="domovoi.local")
    client._adopt_located(sat.cfg, cfg_path, found, False)
    assert sat.cfg.domovoi_url == "ws://192.168.0.117:6370"
    assert "203.0.113.9" not in cfg_path.read_text(encoding="utf-8")
    home = SimpleNamespace(url="ws://192.168.0.200:6370", via="sweep", detail="")
    client._adopt_located(sat.cfg, cfg_path, home, False)
    assert sat.cfg.domovoi_url == "ws://192.168.0.200:6370"
