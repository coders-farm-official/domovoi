"""R2 end to end (security review of wf/sat-pc): a ``set_config`` naming an
ALSA ``file`` PCM with a pipe used to become a shell command the moment
the tray's "Which room is this?" played the chime (``aplay -D``). Now the
change is refused, and the player would not pass such a name anyway.
Needs aplay (alsa-utils): the Linux container runner has it."""

from __future__ import annotations

import shutil
import time

import pytest

from satellite.tests._client_import import import_client

client = import_client()
FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"


@pytest.mark.skipif(shutil.which("aplay") is None, reason="needs aplay (the Linux container runs this)")
def test_R2_e2e_set_config_then_identify_runs_no_command(tmp_path):
    marker = tmp_path / "pwned-by-set-config"
    client.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    client.CONFIG_PATH.write_text(
        '[device]\nprofile = "generic_pc"\n[satellite]\nroom_id = "study"\n'
        f'domovoi_url = "ws://192.168.1.20:6370"\nserver_fingerprint = "{FP}"\n'
        '[pc]\npermissions = "automatic"\n', encoding="utf-8")
    sat = client.Satellite(client.Config.load(client.CONFIG_PATH))
    sat._restart_service = lambda: True
    sat._apply_config_changes({"music.alsa_device": f'file:"|id > {marker}",raw'})
    assert "file:" not in client.CONFIG_PATH.read_text(encoding="utf-8")
    after_restart = client.Satellite(client.Config.load(client.CONFIG_PATH))
    after_restart.control_identify()
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not marker.exists(), "a command ran"
