"""The smaller satellite hardening items (SAT-6): the sandboxed units, the
USB adoption label check on POSIX, the SDR listener's bind address, and
the XVF3800 tool fetched at a pinned commit with verified digests.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SAT = REPO / "satellite"

# systemd.exec(5): every one of these implies NoNewPrivileges=yes for a
# unit that does not run as root - and sudo is how the satellite restarts
# itself, re-joins Wi-Fi and applies plugin payloads.
_IMPLIES_NO_NEW_PRIVILEGES = (
    "NoNewPrivileges", "PrivateDevices", "ProtectKernelTunables",
    "ProtectKernelModules", "ProtectKernelLogs", "ProtectClock",
    "ProtectHostname", "ProtectControlGroups", "RestrictAddressFamilies",
    "RestrictNamespaces", "RestrictRealtime", "RestrictSUIDSGID",
    "LockPersonality", "MemoryDenyWriteExecute", "SystemCallArchitectures",
    "SystemCallFilter", "SystemCallLog", "PrivateUsers", "DynamicUser",
)


def _unit(name: str, user: str = "domovoi", home: str = "/home/domovoi") -> dict[str, list[str]]:
    """The unit rendered the way install-service.sh renders it, as
    {directive: [values]} for the [Service] section."""
    text = (SAT / name if (SAT / name).is_file() else SAT / "scripts" / name).read_text(encoding="utf-8")
    text = text.replace("@USER@", user).replace("@HOME@", home)
    out: dict[str, list[str]] = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line
            continue
        if section != "[Service]" or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out.setdefault(k.strip(), []).append(v.strip())
    return out


# ─── the units ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["domovoi-satellite.service", "domovoi-kiosk.service"])
def test_the_user_units_run_with_a_read_only_file_system(name):
    svc = _unit(name)
    assert svc.get("ProtectSystem") == ["strict"]
    paths = " ".join(svc.get("ReadWritePaths", [])).split()
    assert paths, "ProtectSystem=strict with no ReadWritePaths would leave nothing writable"
    for p in paths:
        assert "@" not in p, f"unrendered placeholder in {p}"
        assert p.lstrip("-").startswith("/"), p


def test_the_client_can_write_exactly_its_config_dir_its_code_tree_and_tmp():
    svc = _unit("domovoi-satellite.service")
    paths = set(" ".join(svc["ReadWritePaths"]).split())
    assert paths == {"/home/domovoi/.domovoi", "/home/domovoi/domovoi", "/tmp"}
    # ...and those are the trees the client actually uses.
    assert svc["WorkingDirectory"] == ["/home/domovoi/domovoi"]
    from satellite import plugin_sync

    assert plugin_sync.CONFIG_DIR == Path("~/.domovoi").expanduser()


@pytest.mark.parametrize("name", ["domovoi-satellite.service", "domovoi-kiosk.service"])
def test_nothing_in_the_units_implies_no_new_privileges(name):
    """sudo has to gain privileges for the helpers to work at all."""
    svc = _unit(name)
    for directive in _IMPLIES_NO_NEW_PRIVILEGES:
        assert directive not in svc, f"{directive} would imply NoNewPrivileges"


def test_the_root_oneshots_are_not_sandboxed_and_say_why():
    """provisioning writes /etc, /boot and configfs; bootstrap installs
    packages. A sandbox there would break the thing it protects."""
    for name in ("domovoi-provisioning.service",):
        svc = _unit(name)
        assert svc.get("User") == ["root"]
        assert "ProtectSystem" not in svc
    unit = (REPO / "domovoi" / "satellite_media" / "templates" / "domovoi-bootstrap.service")
    assert "ProtectSystem" not in unit.read_text(encoding="utf-8")


def test_the_payload_helper_escapes_the_sandbox_it_inherits():
    """A sudo'd child of the strict unit sees the same read-only /usr and
    /var, so the helper that runs apt re-runs itself as a transient unit."""
    helper = (SAT / "scripts" / "domovoi-apply-payload").read_text(encoding="utf-8")
    assert "exec systemd-run --quiet --wait --pipe --collect" in helper
    assert "DOMOVOI_APPLY_UNSANDBOXED" in helper
    assert "[ -d /run/systemd/system ]" in helper


def test_the_rendered_units_have_no_placeholders_left():
    for name in ("domovoi-satellite.service", "domovoi-kiosk.service"):
        raw = (SAT / name).read_text(encoding="utf-8")
        rendered = raw.replace("@USER@", "domovoi").replace("@HOME@", "/home/domovoi")
        assert not re.search(r"@[A-Z_]+@", rendered)


# ─── USB adoption: the label check on POSIX ───────────────────────────────


NONCE = "4c1d90fe2ab73155"


@pytest.fixture
def posix_stick(tmp_path, monkeypatch):
    """A removable volume as detect_removable() reports it, on a host that
    is not Windows, with no udev by-label directory to consult."""
    import web.backend.satellite_adoption as adoption
    from satellite import provisioning_protocol as proto

    monkeypatch.delenv("SATELLITE_ADOPTION_SCAN_DIRS", raising=False)
    monkeypatch.setattr(adoption.sys, "platform", "linux")
    monkeypatch.setattr(adoption, "_BY_LABEL_DIR", tmp_path / "no-such-dir")
    vol = tmp_path / "stick"
    vol.mkdir()
    info = proto.build_device_info(
        nonce=NONCE, mac="b8:27:eb:11:22:33", board="raspberry_pi_zero_2_w",
        model="Raspberry Pi Zero 2 W Rev 1.0", profiles_supported=["xvf3800_usb"],
    )
    (vol / proto.DEVICE_INFO_NAME).write_text(json.dumps(info), encoding="utf-8")
    monkeypatch.setattr(
        adoption, "detect_removable",
        lambda: [{"mount": str(vol), "device": "/dev/sdz1", "read_only": False}],
    )
    adoption.invalidate_cache()
    yield adoption, vol
    adoption.invalidate_cache()


def _lsblk_saying(label: str | None):
    def run(cmd, **kw):
        assert cmd[:3] == ["lsblk", "-no", "LABEL"]
        if label is None:
            raise OSError("no lsblk here")
        return subprocess.CompletedProcess(cmd, 0, stdout=label + "\n", stderr="")
    return run


def test_a_stick_with_the_wrong_label_is_not_a_pending_satellite(posix_stick, monkeypatch):
    adoption, _ = posix_stick
    monkeypatch.setattr(adoption.subprocess, "run", _lsblk_saying("HOLIDAY-PICS"))
    assert adoption.scan_pending() == []
    assert adoption.mount_for(NONCE) is None


def test_an_unlabelled_stick_is_not_a_pending_satellite(posix_stick, monkeypatch):
    adoption, _ = posix_stick
    monkeypatch.setattr(adoption.subprocess, "run", _lsblk_saying(""))
    assert adoption.scan_pending() == []


def test_the_setup_volume_label_is_accepted(posix_stick, monkeypatch):
    adoption, vol = posix_stick
    monkeypatch.setattr(adoption.subprocess, "run", _lsblk_saying("DOMOVOI-SET"))
    pending = adoption.scan_pending()
    assert [p["pending_id"] for p in pending] == [NONCE]
    assert adoption.mount_for(NONCE) == Path(vol)


def test_the_udev_symlink_is_consulted_before_lsblk(posix_stick, monkeypatch, tmp_path):
    adoption, _ = posix_stick
    by_label = tmp_path / "by-label"
    by_label.mkdir()
    # A by-label entry for our device (a plain file standing in for the
    # symlink: what matters is that its resolved path is the device's).
    entry = by_label / "DOMOVOI-SET"
    entry.write_text("", encoding="utf-8")
    monkeypatch.setattr(adoption, "_BY_LABEL_DIR", by_label)
    real = adoption.os.path.realpath

    def realpath(p):
        return "/dev/sdz1" if str(p) in (str(entry), "/dev/sdz1") else real(p)

    monkeypatch.setattr(adoption.os.path, "realpath", realpath)

    def no_lsblk(cmd, **kw):
        raise AssertionError("lsblk should not be needed when udev knows the label")

    monkeypatch.setattr(adoption.subprocess, "run", no_lsblk)
    assert adoption._volume_label_posix("/dev/sdz1") == "DOMOVOI-SET"


def test_udev_escapes_are_decoded():
    import web.backend.satellite_adoption as adoption

    assert adoption._udev_unescape("MY\\x20STICK") == "MY STICK"
    assert adoption._udev_unescape("DOMOVOI-SET") == "DOMOVOI-SET"


def test_windows_keeps_asking_the_volume_root(monkeypatch):
    """The Windows path is unchanged: the label comes from the mount root,
    and the device is not needed."""
    import web.backend.satellite_adoption as adoption

    if sys.platform != "win32":
        pytest.skip("Windows-only branch")
    # A directory is not a volume root, so the API says no label - and the
    # POSIX helper is never touched.
    monkeypatch.setattr(adoption, "_volume_label_posix", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert adoption._volume_label(str(REPO), "/dev/sdz1") is None


# ─── the XVF3800 tool is fetched at one commit, and checked ───────────────


def test_the_pin_and_digests_are_well_formed():
    from domovoi.satellite_media import fetchers

    assert re.fullmatch(r"[0-9a-f]{40}", fetchers.XVF_HOST_COMMIT)
    assert set(fetchers.XVF_HOST_SHA256) == {"xvf_host", "libcommand_map.so"}
    for digest in fetchers.XVF_HOST_SHA256.values():
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


class _FakeGit:
    """Records every git call; `fetch` materialises the upstream tree."""

    def __init__(self, files: dict[str, bytes], head: str | None = None):
        self.files = files
        self.head = head
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        sub = cmd[1] if cmd[1] != "-C" else cmd[3]
        clone = Path(cmd[2] if cmd[1] == "-C" else cmd[-1])
        if sub == "fetch":
            from domovoi.satellite_media import fetchers

            d = clone / fetchers.XVF_HOST_SUBDIR
            d.mkdir(parents=True, exist_ok=True)
            for name, body in self.files.items():
                (d / name).write_bytes(body)
        if sub == "rev-parse":
            from domovoi.satellite_media import fetchers

            return subprocess.CompletedProcess(cmd, 0, stdout=(self.head or fetchers.XVF_HOST_COMMIT) + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture
def scratch_cache(tmp_path, monkeypatch):
    from domovoi.satellite_media import cache

    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "cache")
    return tmp_path / "cache"


def test_the_fetch_asks_for_the_pinned_commit_and_never_a_branch(scratch_cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    files = {"xvf_host": b"\x7fELF-fake", "libcommand_map.so": b"\x7fELF-map", "extra.yaml": b"x"}
    monkeypatch.setattr(fetchers, "XVF_HOST_SHA256", {
        name: hashlib.sha256(files[name]).hexdigest() for name in ("xvf_host", "libcommand_map.so")
    })
    git = _FakeGit(files)
    ok, msg = fetchers.fetch_xvf_host(run=git)
    assert ok, msg
    subs = [c[1] if c[1] != "-C" else c[3] for c in git.calls]
    assert subs == ["init", "remote", "fetch", "checkout", "rev-parse"]
    fetch = next(c for c in git.calls if "fetch" in c)
    assert fetch[-1] == fetchers.XVF_HOST_COMMIT
    assert "--depth" in fetch
    assert not any(c[1] == "clone" for c in git.calls)
    checkout = next(c for c in git.calls if "checkout" in c)
    assert checkout[-1] == fetchers.XVF_HOST_COMMIT
    # The whole folder lands in the cache, and the message names the pin.
    assert sorted(p.name for p in (scratch_cache / "xvf_host").iterdir()) == sorted(files)
    assert fetchers.XVF_HOST_COMMIT[:12] in msg


def test_a_tree_whose_digests_differ_never_reaches_the_cache(scratch_cache):
    from domovoi.satellite_media import fetchers

    git = _FakeGit({"xvf_host": b"not the pinned binary", "libcommand_map.so": b"nor the map"})
    ok, msg = fetchers.fetch_xvf_host(run=git)
    assert ok is False
    assert "does not match the pinned digest" in msg
    assert not (scratch_cache / "xvf_host").exists() or not any((scratch_cache / "xvf_host").iterdir())


def test_a_checkout_that_is_not_the_pin_is_refused(scratch_cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    files = {"xvf_host": b"a", "libcommand_map.so": b"b"}
    monkeypatch.setattr(fetchers, "XVF_HOST_SHA256", {
        name: hashlib.sha256(body).hexdigest() for name, body in files.items()
    })
    git = _FakeGit(files, head="0" * 40)
    ok, msg = fetchers.fetch_xvf_host(run=git)
    assert ok is False
    assert "is not" in msg and fetchers.XVF_HOST_COMMIT[:12] in msg


def test_a_missing_file_at_the_pin_is_refused(scratch_cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    monkeypatch.setattr(fetchers, "XVF_HOST_SHA256", {
        "xvf_host": hashlib.sha256(b"a").hexdigest(),
        "libcommand_map.so": hashlib.sha256(b"b").hexdigest(),
    })
    git = _FakeGit({"xvf_host": b"a"})          # no libcommand_map.so
    ok, msg = fetchers.fetch_xvf_host(run=git)
    assert ok is False
    assert "libcommand_map.so is missing" in msg


def test_a_stale_cache_is_replaced_not_merged(scratch_cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    stale = scratch_cache / "xvf_host"
    stale.mkdir(parents=True)
    (stale / "libold.so").write_bytes(b"stale")
    files = {"xvf_host": b"a", "libcommand_map.so": b"b"}
    monkeypatch.setattr(fetchers, "XVF_HOST_SHA256", {
        name: hashlib.sha256(body).hexdigest() for name, body in files.items()
    })
    ok, _ = fetchers.fetch_xvf_host(run=_FakeGit(files))
    assert ok
    assert not (stale / "libold.so").exists()
