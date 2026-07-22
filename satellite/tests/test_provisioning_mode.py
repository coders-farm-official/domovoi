"""provisioning_mode — the device-side adoption state machine, driven
entirely through a dict-backed FakeGadgetBackend (no configfs, no mtools,
no subprocesses)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from satellite import provisioning_mode as pm
from satellite import provisioning_protocol as proto


class FakeBackend(pm.GadgetBackend):
    """Dict-backed image. ``provision_script`` maps a read index (1-based
    count of PROVISION reads) to bytes, letting tests script torn writes
    and stability windows."""

    def __init__(self, provision_script: dict[int, bytes] | None = None):
        self.files: dict[str, bytes] = {}
        self.built_infos: list[dict[str, Any]] = []
        self.bound = False
        self.rebooted = False
        self.provision_reads = 0
        self.provision_script = provision_script or {}

    def build_image(self, image: Path, device_info: dict[str, Any]) -> None:
        self.built_infos.append(device_info)
        self.files = {
            proto.DEVICE_INFO_NAME: json.dumps(device_info).encode("utf-8")
        }

    def bind(self, image: Path) -> None:
        self.bound = True

    def unbind(self) -> None:
        self.bound = False

    def read_file(self, image: Path, name: str) -> bytes | None:
        if name == proto.PROVISION_NAME:
            self.provision_reads += 1
            if self.provision_reads in self.provision_script:
                return self.provision_script[self.provision_reads]
            return self.files.get(name)
        return self.files.get(name)

    def delete_file(self, image: Path, name: str) -> None:
        self.files.pop(name, None)

    def reboot(self) -> None:
        self.rebooted = True


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate every path the module touches + neuter sleeps."""
    cfg_dir = tmp_path / ".domovoi"
    monkeypatch.setattr(pm, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(pm, "CONFIG_PATH", cfg_dir / "config.toml")
    monkeypatch.setattr(pm, "PAIRING_TOKEN_PATH", cfg_dir / "pairing_token")
    monkeypatch.setattr(pm, "STATE_FILE", cfg_dir / "provisioning_state.json")
    monkeypatch.setattr(pm, "IMAGE_FILE", cfg_dir / "setup_gadget.img")
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.setattr(pm, "read_board", lambda: ("test_board", "Test Board"))
    monkeypatch.setattr(pm, "read_wlan_mac", lambda: "b8:27:eb:aa:bb:cc")
    return cfg_dir


def _provision_bytes(nonce: str, **overrides) -> bytes:
    kw = dict(
        nonce=nonce,
        room_id="den",
        domovoi_url="ws://192.168.1.50:6370",
        sat_type="video",
        device_profile="radxa_zero3w_video",
        pairing_token="c" * 64,
        wifi_ssid="HomeNet",
        wifi_psk="hunter2hunter2",
    )
    kw.update(overrides)
    return json.dumps(proto.build_provision(**kw)).encode("utf-8")


def _install_provision_on_first_read(backend: FakeBackend, **overrides):
    """Arrange for the (valid) provision to appear from the first poll on,
    keyed to whatever nonce the backend built its device-info with."""
    real_read = FakeBackend.read_file

    def read(self, image, name):
        if name == proto.PROVISION_NAME and proto.PROVISION_NAME not in self.files:
            nonce = self.built_infos[-1]["nonce"]
            self.files[proto.PROVISION_NAME] = _provision_bytes(nonce, **overrides)
        return real_read(self, image, name)

    backend.read_file = read.__get__(backend)


def test_is_provisioned(home):
    assert pm.is_provisioned() is False
    pm.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    pm.CONFIG_PATH.write_text("[satellite]\n", encoding="utf-8")
    assert pm.is_provisioned() is True          # config + no active phase
    pm._write_state({"phase": "awaiting_provision"})
    assert pm.is_provisioned() is False
    assert pm.provisioning_active() is True
    pm._write_state({"phase": "done"})
    assert pm.is_provisioned() is True


def test_full_apply_flow(home, monkeypatch):
    """Two stable reads → apply: config written (comment-preserving from
    the example), pairing token stored, provision wiped BEFORE any
    re-expose, state done, image gone, reboot requested."""
    backend = FakeBackend()
    _install_provision_on_first_read(backend)
    monkeypatch.setattr(
        pm, "apply_wifi", lambda *a, **k: (True, None)
    )
    rc = pm.run(backend, poll_sec=0, max_loops=10)
    assert rc == 0
    assert backend.rebooted is True
    assert proto.PROVISION_NAME not in backend.files   # credentials wiped

    cfg = tomllib.loads(pm.CONFIG_PATH.read_text(encoding="utf-8"))
    assert cfg["satellite"]["room_id"] == "den"
    assert cfg["satellite"]["domovoi_url"] == "ws://192.168.1.50:6370"
    assert cfg["satellite"]["sat_type"] == "video"
    assert cfg["device"]["profile"] == "radxa_zero3w_video"
    # Comment preservation: the example's prose survives the merge.
    assert "#" in pm.CONFIG_PATH.read_text(encoding="utf-8")

    assert pm.PAIRING_TOKEN_PATH.read_text(encoding="utf-8") == "c" * 64
    assert pm._read_state()["phase"] == "done"
    assert not pm.IMAGE_FILE.exists()
    assert pm.is_provisioned() is True


def test_two_stable_reads_required(home, monkeypatch):
    """A file that changes between polls (torn write in progress) must not
    apply; only two identical consecutive reads do."""
    applied = []
    monkeypatch.setattr(
        pm, "apply_provision",
        lambda payload, **kw: (applied.append(payload), (True, None))[1],
    )
    # read1: partial bytes; read2: full doc; read3: full doc (stable pair
    # is reads 2+3).
    backend = FakeBackend()

    def scripted(self, image, name):
        if name != proto.PROVISION_NAME:
            return self.files.get(name)
        self.provision_reads += 1
        nonce = self.built_infos[-1]["nonce"]
        full = _provision_bytes(nonce)
        if self.provision_reads == 1:
            return full[: len(full) // 2]
        return full

    backend.read_file = scripted.__get__(backend)
    rc = pm.run(backend, poll_sec=0, max_loops=10)
    assert rc == 0
    assert len(applied) == 1
    # Needed at least 3 reads: torn, full, full-again.
    assert backend.provision_reads >= 3


def test_stale_nonce_rejected(home, monkeypatch):
    backend = FakeBackend()

    def scripted(self, image, name):
        if name != proto.PROVISION_NAME:
            return self.files.get(name)
        self.provision_reads += 1
        return _provision_bytes("ff" * 8)   # NOT the session nonce

    backend.read_file = scripted.__get__(backend)
    rc = pm.run(backend, poll_sec=0, max_loops=6)
    assert rc == 1                          # poll exhausted, nothing applied
    assert not pm.CONFIG_PATH.exists()
    assert backend.rebooted is False


def test_wifi_failure_represents_with_error(home, monkeypatch):
    """A failed join strips the half-applied config and re-presents the
    volume with status=wifi_failed + the error + a FRESH nonce."""
    backend = FakeBackend()
    _install_provision_on_first_read(backend)
    monkeypatch.setattr(
        pm, "apply_wifi", lambda *a, **k: (False, "wifi join failed for 'HomeNet' (wrong password?)")
    )
    rc = pm.run(backend, poll_sec=0, wifi_attempts=2, max_loops=1)
    assert rc == 1
    assert not pm.CONFIG_PATH.exists()
    assert not pm.PAIRING_TOKEN_PATH.exists()
    assert backend.rebooted is False
    # First build: awaiting; the wifi_failed rebuild happens on the next
    # loop iteration, which max_loops=1 stops before — the STATE carries it.
    assert pm._read_state()["phase"] in ("awaiting_provision", "wifi_failed")


def test_wifi_failed_rebuild_gets_fresh_nonce(home, monkeypatch):
    backend = FakeBackend()
    _install_provision_on_first_read(backend)
    monkeypatch.setattr(pm, "apply_wifi", lambda *a, **k: (False, "nope"))
    pm.run(backend, poll_sec=0, wifi_attempts=1, max_loops=2)
    assert len(backend.built_infos) >= 2
    first, second = backend.built_infos[0], backend.built_infos[1]
    assert second["status"] == "wifi_failed"
    assert second["error"] == "nope"
    assert second["nonce"] != first["nonce"]


def test_run_noop_when_provisioned(home):
    pm.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    pm.CONFIG_PATH.write_text("[satellite]\n", encoding="utf-8")
    backend = FakeBackend()
    assert pm.run(backend, poll_sec=0, max_loops=1) == 0
    assert backend.built_infos == []        # gadget never composed
