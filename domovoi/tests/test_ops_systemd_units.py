"""The shipped systemd units and the docker-group warning (OPS-11).

Pure file checks: no systemd, no DB, never skips.

* ``scripts/linux/units/`` ships the three units docs/LINUX_HOST.md
  installs, each with systemd's sandboxing: a read-only system with only
  the checkout, the media mount points and the service user's home
  writable, a private /tmp, a 0027 umask, the kernel interfaces off limits.
* The core keeps what the dashboard's restart and the hardware need: no
  ``NoNewPrivileges`` (``sudo -n systemctl`` from the core), no device
  hiding (GPU, SDR), no W^X (the speech stack's JIT). The web, which never
  runs sudo, and the database unit, which only drives the docker CLI, drop
  privileges outright.
* LINUX_HOST.md installs the shipped files instead of listing its own
  copies, and says next to the ``usermod -aG docker`` step that the group
  is root-equivalent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UNITS = REPO_ROOT / "scripts" / "linux" / "units"
LINUX_HOST = REPO_ROOT / "docs" / "LINUX_HOST.md"
NAMES = ("domovoi-db", "domovoi-core", "domovoi-web")


def _service(name: str) -> dict[str, list[str]]:
    """The [Service] section as key -> every value it is given (systemd
    keys repeat; an empty assignment resets, so it is kept as "")."""
    text = (UNITS / f"{name}.service").read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            section = line
            continue
        if section != "[Service]":
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), []).append(value.strip())
    return out


def _one(svc: dict[str, list[str]], key: str) -> str:
    assert key in svc, f"{key}= is missing"
    return svc[key][-1]


def _paths(svc: dict[str, list[str]], key: str) -> set[str]:
    return {p.lstrip("-") for v in svc.get(key, []) for p in v.split()}


@pytest.mark.parametrize("name", NAMES)
def test_unit_ships_with_lf_endings(name: str) -> None:
    raw = (UNITS / f"{name}.service").read_bytes()
    assert b"\r" not in raw, f"{name}.service has CR bytes (systemd reads them as part of the value)"


@pytest.mark.parametrize("name", NAMES)
def test_unit_is_sandboxed(name: str) -> None:
    svc = _service(name)
    assert _one(svc, "User") == "domovoi"
    assert _one(svc, "ProtectSystem") == "strict"
    for key in (
        "PrivateTmp",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "ProtectHostname",
        "RestrictRealtime",
        "RestrictNamespaces",
        "RestrictSUIDSGID",
        "LockPersonality",
    ):
        assert _one(svc, key) == "yes", f"{name}: {key}"
    assert _one(svc, "SystemCallArchitectures") == "native"
    assert _one(svc, "UMask") == "0027"
    families = set(_one(svc, "RestrictAddressFamilies").split())
    assert families == {"AF_UNIX", "AF_INET", "AF_INET6", "AF_NETLINK"}
    # The service user's home stays writable: ~/.domovoi, the docker CLI's
    # config, the model caches.
    assert "/home/domovoi" in _paths(svc, "ReadWritePaths") | _paths(svc, "BindPaths")


@pytest.mark.parametrize("name", ["domovoi-core", "domovoi-web"])
def test_python_units_see_the_checkout_media_and_only_their_own_home(name: str) -> None:
    svc = _service(name)
    rw = _paths(svc, "ReadWritePaths")
    assert "/opt/domovoi" in rw, "pulls, plugin installs and .env need the checkout"
    assert {"/mnt", "/media", "/run/media", "/srv"} <= rw
    # Optional mount points carry the "-" prefix, so a box without one still starts.
    for value in svc["ReadWritePaths"]:
        for p in value.split():
            if p.lstrip("-") != "/opt/domovoi":
                assert p.startswith("-"), f"{name}: {p} would fail the unit where it is missing"
    assert _one(svc, "ProtectHome") == "tmpfs"
    assert _paths(svc, "BindPaths") == {"/home/domovoi"}
    # The speech stack (numba, torch, onnxruntime) JIT-compiles; devices
    # (GPU, SDR, the card the dashboard reads a label from) stay visible.
    for key in ("MemoryDenyWriteExecute", "PrivateDevices", "ProtectClock", "DevicePolicy", "DeviceAllow"):
        assert key not in svc, f"{name}: {key}= would break the hardware paths"


def test_core_keeps_what_the_sudo_restart_needs() -> None:
    svc = _service("domovoi-core")
    # domovoi/self_restart.py runs `sudo -n systemctl ...` from this process.
    for key in ("NoNewPrivileges", "CapabilityBoundingSet", "SystemCallFilter", "AmbientCapabilities"):
        assert key not in svc, f"domovoi-core: {key}= stops sudo working"
    assert _one(svc, "ExecStart") == "/opt/domovoi/.venv/bin/python -m domovoi.main"
    assert _one(svc, "WorkingDirectory") == "/opt/domovoi"
    assert _one(svc, "TimeoutStopSec") == "30"
    assert _one(svc, "KillMode") == "control-group"


def test_web_drops_every_privilege() -> None:
    svc = _service("domovoi-web")
    assert _one(svc, "NoNewPrivileges") == "yes"
    assert _one(svc, "CapabilityBoundingSet") == ""
    assert _one(svc, "ExecStart") == "/opt/domovoi/.venv/bin/python -m web.backend.main"


def test_db_unit_is_locked_down_around_the_docker_cli() -> None:
    svc = _service("domovoi-db")
    assert svc["ExecStart"] == [
        "/usr/bin/docker compose up -d postgres",
        "/usr/bin/docker compose run --rm flyway",
    ]
    assert _one(svc, "WorkingDirectory") == "/opt/domovoi/domovoi"
    for key in ("NoNewPrivileges", "PrivateDevices", "ProtectClock", "MemoryDenyWriteExecute"):
        assert _one(svc, key) == "yes", key
    assert _one(svc, "CapabilityBoundingSet") == ""
    assert _one(svc, "ProtectHome") == "read-only"


def test_linux_host_installs_the_shipped_units() -> None:
    doc = LINUX_HOST.read_text(encoding="utf-8")
    for name in NAMES:
        assert f"scripts/linux/units/{name}.service" in doc
        # The page no longer keeps a copy of its own that could drift.
        assert f"**`/etc/systemd/system/{name}.service`**" not in doc
    assert "### Sandboxing the units" in doc


def test_docker_group_warning_sits_with_the_usermod_step() -> None:
    doc = LINUX_HOST.read_text(encoding="utf-8")
    i = doc.index("sudo usermod -aG docker $USER")
    nearby = doc[i : i + 1500]
    assert re.search(r"`docker` group is root", nearby), "the Install step must say the group is root-equivalent"
