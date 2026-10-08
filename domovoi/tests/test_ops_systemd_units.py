"""The shipped systemd units and the docker-group warning (OPS-11).

Pure file checks: no systemd, no DB, never skips.

* ``scripts/linux/units/`` ships the three units docs/LINUX_HOST.md
  installs, each with systemd's sandboxing: a read-only system with only
  the checkout, the media mount points and the service user's home
  writable, a private /tmp, a 0027 umask, the kernel interfaces off limits.
* The core keeps what the dashboard's restart and the hardware need. Its
  ``sudo -n systemctl`` needs the set-uid bit honoured, so it carries none
  of the options systemd answers with the no_new_privs flag for a non-root
  ``User=`` (``IMPLIES_NO_NEW_PRIVS``: every seccomp-backed one, not just
  ``NoNewPrivileges`` itself; 2026-10 review REV-01), only the
  mount-namespace part. It also hides no devices (GPU, SDR) and keeps
  W+X memory (the speech stack's JIT). The web, which never runs sudo,
  and the database unit, which only drives the docker CLI, keep the whole
  set and drop privileges outright.
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

# systemd.exec(5), NoNewPrivileges=: "Some configurations may ensure that
# this setting is automatically set to true" -- for a unit without
# CAP_SYS_ADMIN, which a non-root User= is, systemd (exec-invoke.c,
# context_has_no_new_privileges) sets PR_SET_NO_NEW_PRIVS whenever any of
# these is on, because each is enforced with seccomp (DynamicUser= through
# the RestrictSUIDSGID= it implies). Under that flag sudo's set-uid bit is
# ignored and it refuses: "The no new privileges flag is set".
IMPLIES_NO_NEW_PRIVS = (
    "NoNewPrivileges",
    "DynamicUser",
    "LockPersonality",
    "MemoryDenyWriteExecute",
    "PrivateDevices",
    "ProtectClock",
    "ProtectHostname",
    "ProtectKernelLogs",
    "ProtectKernelModules",
    "ProtectKernelTunables",
    "RestrictAddressFamilies",
    "RestrictNamespaces",
    "RestrictRealtime",
    "RestrictSUIDSGID",
    "SystemCallArchitectures",
    "SystemCallFilter",
    "SystemCallLog",
)
# The seccomp-backed set the units that never run sudo keep.
SECCOMP_SET = (
    "ProtectKernelTunables",
    "ProtectKernelModules",
    "ProtectKernelLogs",
    "ProtectHostname",
    "RestrictRealtime",
    "RestrictNamespaces",
    "RestrictSUIDSGID",
    "LockPersonality",
)


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
    """The mount-namespace part every unit carries, the core included."""
    svc = _service(name)
    assert _one(svc, "User") == "domovoi"
    assert _one(svc, "ProtectSystem") == "strict"
    for key in ("PrivateTmp", "ProtectControlGroups"):
        assert _one(svc, key) == "yes", f"{name}: {key}"
    assert _one(svc, "UMask") == "0027"
    # The service user's home stays writable: ~/.domovoi, the docker CLI's
    # config, the model caches.
    assert "/home/domovoi" in _paths(svc, "ReadWritePaths") | _paths(svc, "BindPaths")


@pytest.mark.parametrize("name", ["domovoi-db", "domovoi-web"])
def test_units_that_never_run_sudo_keep_the_seccomp_set(name: str) -> None:
    svc = _service(name)
    for key in SECCOMP_SET:
        assert _one(svc, key) == "yes", f"{name}: {key}"
    assert _one(svc, "SystemCallArchitectures") == "native"
    families = set(_one(svc, "RestrictAddressFamilies").split())
    assert families == {"AF_UNIX", "AF_INET", "AF_INET6", "AF_NETLINK"}
    assert _one(svc, "NoNewPrivileges") == "yes"


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
    # domovoi/self_restart.py runs `sudo -n systemctl ...` from this process:
    # sudo needs every capability in the bounding set as well.
    for key in ("CapabilityBoundingSet", "AmbientCapabilities"):
        assert key not in svc, f"domovoi-core: {key}= stops sudo working"
    assert _one(svc, "ExecStart") == "/opt/domovoi/.venv/bin/python -m domovoi.main"
    assert _one(svc, "WorkingDirectory") == "/opt/domovoi"
    assert _one(svc, "TimeoutStopSec") == "30"
    assert _one(svc, "KillMode") == "control-group"


def test_core_sets_nothing_that_implies_no_new_privileges() -> None:
    """REV-01: NoNewPrivileges= left out is not enough. Any one of the
    seccomp-backed options makes systemd set no_new_privs for a non-root
    unit, and sudo then refuses: the Restart button and the update unit
    stop working on every box with the shipped core unit."""
    svc = _service("domovoi-core")
    present = [key for key in IMPLIES_NO_NEW_PRIVS if key in svc]
    assert present == [], f"domovoi-core sets {present}: systemd implies NoNewPrivileges=yes, sudo refuses"


def test_no_section_of_the_core_unit_sneaks_one_in() -> None:
    """The same check on the raw text, every section and spelling systemd
    accepts (leading blanks, blanks around '='), comments excepted."""
    text = (UNITS / "domovoi-core.service").read_text(encoding="utf-8")
    for key in IMPLIES_NO_NEW_PRIVS:
        assert not re.search(rf"^[ \t]*{key}[ \t]*=", text, re.MULTILINE), key


def test_core_unit_says_which_options_it_leaves_out_and_why() -> None:
    text = (UNITS / "domovoi-core.service").read_text(encoding="utf-8")
    comments = " ".join(
        line.lstrip("#; ").strip() for line in text.splitlines() if line.lstrip().startswith(("#", ";"))
    )
    assert "sudo -n systemctl" in comments
    assert "no_new_privs" in comments
    for key in IMPLIES_NO_NEW_PRIVS[1:]:
        assert key in comments, f"the comment does not name {key}"


def test_linux_host_says_why_the_core_unit_is_looser() -> None:
    doc = LINUX_HOST.read_text(encoding="utf-8")
    start = doc.index("### Sandboxing the units")
    section = doc[start : doc.index("\n### ", start + 1)]
    assert "sudo -n" in section
    assert "domovoi-update" in section
    # It names the trap, not just the one key: the seccomp-backed options
    # imply NoNewPrivileges for a non-root unit.
    for key in ("RestrictSUIDSGID", "SystemCallArchitectures", "RestrictAddressFamilies", "ProtectKernelTunables"):
        assert key in section, key
    assert re.search(r"impl(y|ies|ied)", section)


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
