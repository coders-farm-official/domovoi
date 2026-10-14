"""The core's check of ALSA names and text in satellite settings
(domovoi/satellite_config_schema.py) is the satellite's own
(satellite/alsa_names.py, satellite/remote_config.py): what one takes, the
other takes, so the dashboard reports a refusal instead of "sent"."""

from __future__ import annotations

import pytest

from domovoi import satellite_config_schema as schema
from satellite import alsa_names, remote_config

PCM_NAMES = [
    "default", "Default", "null", "pulse", "pipewire", "dmix", "hw", "hw:0", "hw:1,0", "HW:1,0",
    "plughw:CARD=Array,DEV=0", "plughw:Array", "sysdefault:CARD=PCH", "plug:dmix", "front:CARD=PCH,DEV=0",
    "surround51:CARD=PCH", "iec958:CARD=PCH", "hdmi:CARD=PCH,DEV=0", "usbstream:CARD=Array", "dsnoop:1",
    "bluealsa", "bluealsa:DEV=00:11:22:33:44:55", "bluealsa:DEV=00:11:22:33:44:55,PROFILE=a2dp",
    "bluealsa:DEV=00:11:22:33:44:55,PROFILE=hfp", "bluealsa:DEV=bad",
    "file", "FILE", "pipe", "tee", "shm", "exec", "ladspa", "lfloat", "share",
    'file:"|id",raw', "file:/tmp/x", "pipe:cat", "plug:'file:\"|id\"'", "plug:file:x", "hw:0:1",
    "tee:x", "Shm:1", "exec:id", "x" * 64, "x" * 65, "1abc", "-x", "", " default", "default ",
    "hw:0,0 ", "plughw:CARD=Array;id", "hw:0\n", "my_pcm", "my-pcm", "upmix", "asym",
]
CARDS = ["0", "1", "Array", "PCH", "my_card-2", "", "0;reboot", "0 -D file", "x" * 32, "x" * 33, "Arr\nay"]
CONTROLS = ["PCM", "Master", "Speaker,0", "Headphone Playback", "Mic (Front)", "Digital+", "", 'a"b',
            "PCM\n", "x" * 48, "x" * 49, "Mast$er", "PCM`id`"]


def _core_takes(name: str, value: str) -> bool:
    try:
        schema.coerce_and_validate(schema.FIELD_BY_NAME[name], value)
        return True
    except ValueError:
        return False


@pytest.mark.parametrize("value", PCM_NAMES)
def test_music_device_names_the_core_takes_are_the_satellite_s(value):
    assert _core_takes("music.alsa_device", value) == alsa_names.safe_pcm(value), value


@pytest.mark.parametrize("value", CARDS)
def test_mixer_cards_the_core_takes_are_the_satellite_s(value):
    assert _core_takes("audio.output_mixer_card", value) == alsa_names.safe_card(value), value


@pytest.mark.parametrize("value", CONTROLS)
def test_mixer_controls_the_core_takes_are_the_satellite_s(value):
    satellite = value == "" or alsa_names.safe_control(value)
    assert _core_takes("audio.output_mixer_control", value) == satellite, value


def test_text_limits_are_the_satellite_s():
    assert schema.MAX_STRING == remote_config.MAX_STRING
    for name in ("audio.input_device", "audio.output_device"):
        assert _core_takes(name, "x" * schema.MAX_STRING)
        assert not _core_takes(name, "x" * (schema.MAX_STRING + 1))
        assert not _core_takes(name, "Array\tx")
        assert remote_config.screen({name: "x" * (schema.MAX_STRING + 1)}, pc=False)[0] == {}


def test_every_text_field_is_held_to_the_limits():
    for spec in schema.EDITABLE_FIELDS:
        if spec.type == "str":
            assert not _core_takes(spec.name, "a" * 121), spec.name
