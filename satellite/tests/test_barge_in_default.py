"""Barge-in is opt-in.

A satellite that listens during its own playback can interrupt itself on
speaker echo, and a false interruption mid-answer is worse than waiting for
the answer to finish. So a fresh unit ships with it off (nothing interrupts
playback, the wake word included - both barge monitors gate on it), in BOTH
places a default lives: the example config every prepared card is rendered
from, and the client's fallback for a config that omits the key.
"""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path

from satellite.tests._client_import import import_client

client = import_client()

EXAMPLE = Path(__file__).resolve().parents[1] / "config.toml.example"


def test_the_example_config_ships_with_barge_in_off():
    """This is the template apply_provision renders every device config
    from, so it is the default every prepared card actually gets."""
    doc = tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert doc["barge_in"]["enabled"] is False


def test_a_config_without_the_key_means_off(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[satellite]\nroom_id = "den"\ndomovoi_url = "ws://x:6370"\n',
        encoding="utf-8",
    )
    assert client.Config.load(cfg).barge_in is False


def test_an_explicit_on_still_wins(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[satellite]\nroom_id = "den"\ndomovoi_url = "ws://x:6370"\n'
        "[barge_in]\nenabled = true\n",
        encoding="utf-8",
    )
    assert client.Config.load(cfg).barge_in is True


def test_the_dashboard_says_so():
    from domovoi.satellite_config_schema import FIELD_BY_NAME

    assert "Off by default" in FIELD_BY_NAME["barge_in.enabled"].help
    assert "On by default" in FIELD_BY_NAME["barge_in.require_wake_word"].help
    assert "On by default" in FIELD_BY_NAME["noise_gate.auto_calibrate"].help


# ─── the two knobs that are ON on every board ─────────────────────────────
#
# If a room does turn barge-in on, only the wake word interrupts until the
# room opts into talk-over; and the noise gate derives its floor from the
# room it is actually in. Neither depends on the mic board any more.


def test_the_example_config_requires_the_wake_word_and_auto_calibrates():
    doc = tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert doc["barge_in"]["require_wake_word"] is True
    assert doc["noise_gate"]["auto_calibrate"] is True


def test_a_config_without_those_keys_gets_them_on_every_board(tmp_path):
    for profile in ("respeaker_2mic_hat", "xvf3800_usb", "radxa_zero3w_video"):
        cfg = tmp_path / f"{profile}.toml"
        cfg.write_text(
            '[satellite]\nroom_id = "den"\ndomovoi_url = "ws://x:6370"\n'
            f'[device]\nprofile = "{profile}"\n',
            encoding="utf-8",
        )
        loaded = client.Config.load(cfg)
        assert loaded.barge_in_require_wake_word is True, profile
        assert loaded.noise_gate_auto_calibrate is True, profile


def test_the_fallback_is_spelled_false_in_the_loader():
    src = inspect.getsource(client.Config.load)
    assert 'barge.get("enabled", False)' in src
