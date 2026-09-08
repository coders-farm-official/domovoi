"""Finding the core on the LAN, and undoing USB-gadget mode afterwards.

Discovery is deliberately a unicast sweep rather than mDNS — multicast over
home Wi-Fi is the thing that fails at 3 a.m., and a customer's first five
minutes is the worst place to inherit that. Every probe here is injected;
nothing touches a real network.
"""

from __future__ import annotations

import pytest

from satellite import discovery
from satellite import provisioning_mode as pm


# ─── candidate addresses ──────────────────────────────────────────────────


def test_sweep_covers_the_subnet_without_us():
    hosts = discovery.candidate_hosts("192.168.0.117")
    assert len(hosts) == 253                      # /24 hosts, minus ourselves
    assert "192.168.0.117" not in hosts
    assert "192.168.0.1" in hosts


def test_nearest_addresses_are_probed_first():
    """A home core is usually a low reservation or a near-by lease, so
    distance ordering finds it in a handful of probes, not two hundred."""
    hosts = discovery.candidate_hosts("192.168.0.117")
    assert hosts[0] in ("192.168.0.116", "192.168.0.118")
    assert hosts.index("192.168.0.118") < hosts.index("192.168.0.250")


def test_oversized_networks_are_refused():
    """Nobody's house is a /16, and sweeping one would hammer the network
    for a minute before giving up."""
    assert discovery.candidate_hosts("10.0.0.5", prefix=16) == []


def test_bad_address_is_not_fatal():
    assert discovery.candidate_hosts("not-an-ip") == []


# ─── the probe ────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(body: bytes, status: int = 200):
    return lambda url, timeout=None: _Resp(body, status)


def test_probe_accepts_a_real_core():
    body = b'{"status": "ok", "bot_name": "Domovoi", "use_stubs": "false"}'
    assert discovery.probe("192.168.0.117", opener=_opener(body)) is True


@pytest.mark.parametrize("body", [
    b'{"status": "ok"}',                    # no bot_name — not us
    b'{"status": "degraded", "bot_name": "Domovoi"}',
    b'not json at all',
    b'[]',
])
def test_probe_rejects_everything_else(body):
    """The signature check is what stops us adopting some unrelated service
    that happens to be listening on 6370."""
    assert discovery.probe("192.168.0.9", opener=_opener(body)) is False


def test_probe_swallows_connection_errors():
    def boom(url, timeout=None):
        raise OSError("connection refused")
    assert discovery.probe("192.168.0.9", opener=boom) is False


# ─── the sweep ────────────────────────────────────────────────────────────


def test_find_core_returns_the_host_that_answers():
    hosts = [f"192.168.0.{n}" for n in range(2, 40)]
    found = discovery.find_core(
        hosts=hosts, probe_fn=lambda h, port=None: h == "192.168.0.31",
    )
    assert found == "192.168.0.31"


def test_find_core_returns_none_when_nothing_answers():
    assert discovery.find_core(
        hosts=["10.0.0.2", "10.0.0.3"], probe_fn=lambda h, port=None: False,
    ) is None


def test_find_core_handles_an_empty_sweep():
    assert discovery.find_core(hosts=[]) is None


# ─── the sentinel ─────────────────────────────────────────────────────────


def test_a_typed_address_always_wins():
    """An address entered in the setup portal must never be second-guessed
    by a sweep."""
    url = discovery.resolve_url(
        "ws://192.168.0.117:6370",
        finder=lambda port=None: pytest.fail("discovery should not have run"),
    )
    assert url == "ws://192.168.0.117:6370"


def test_auto_resolves_to_a_websocket_url():
    url = discovery.resolve_url("auto", finder=lambda port=None: "192.168.0.117")
    assert url == "ws://192.168.0.117:6370"


@pytest.mark.parametrize("configured", ["auto", "AUTO", " auto ", ""])
def test_sentinel_forms_all_trigger_discovery(configured):
    assert discovery.resolve_url(
        configured, finder=lambda port=None: "10.0.0.4",
    ) == "ws://10.0.0.4:6370"


def test_failed_discovery_reports_rather_than_guessing():
    assert discovery.resolve_url("auto", finder=lambda port=None: None) is None


# ─── undoing USB-gadget mode ──────────────────────────────────────────────


@pytest.fixture
def boot(tmp_path):
    d = tmp_path / "firmware"
    d.mkdir()
    (d / "config.txt").write_text(
        "# stock\ndtparam=audio=on\n"
        "# --- domovoi satellite (media-prep) ---\n"
        "dtoverlay=dwc2,dr_mode=peripheral\n"
        "# --- end domovoi satellite ---\n"
    )
    (d / "cmdline.txt").write_text(
        "console=serial0,115200 modules-load=dwc2 root=PARTUUID=xxx rootwait\n"
    )
    return d


def test_revert_frees_the_usb_port(boot):
    """Left pinned, a Pi Zero 2 W's only data port stays a peripheral and a
    USB mic array can never enumerate — the satellite adopts and is deaf."""
    changed = pm.revert_usb_gadget_boot_config([boot])
    assert len(changed) == 2
    assert "dr_mode=peripheral" not in (boot / "config.txt").read_text()
    assert "modules-load=dwc2" not in (boot / "cmdline.txt").read_text()


def test_revert_keeps_the_rest_of_the_boot_config(boot):
    pm.revert_usb_gadget_boot_config([boot])
    config = (boot / "config.txt").read_text()
    cmdline = (boot / "cmdline.txt").read_text()
    assert "dtparam=audio=on" in config
    assert "root=PARTUUID=xxx" in cmdline and "rootwait" in cmdline


def test_cmdline_stays_a_single_line(boot):
    """A malformed cmdline can stop the Pi booting altogether."""
    pm.revert_usb_gadget_boot_config([boot])
    raw = (boot / "cmdline.txt").read_text()
    assert raw.count("\n") == 1 and raw.endswith("\n")


def test_revert_is_idempotent(boot):
    pm.revert_usb_gadget_boot_config([boot])
    assert pm.revert_usb_gadget_boot_config([boot]) == []


def test_revert_never_raises_on_a_missing_boot_dir(tmp_path):
    assert pm.revert_usb_gadget_boot_config([tmp_path / "nope"]) == []


# ─── where setup-AP credentials come from ─────────────────────────────────
#
# This ordering is what makes mass production work. firstrun copies
# boot:domovoi/ap.json into ~/.domovoi inside its `code` step, which is
# already done on a golden master — so a card personalized after flashing
# has fresh credentials on its BOOT partition that nothing would ever copy
# across. Reading only the home copy leaves every mass-flashed unit with no
# portal at all.


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    boot = tmp_path / "firmware"
    (boot / "domovoi").mkdir(parents=True)
    home = tmp_path / "dot-domovoi"
    home.mkdir()
    monkeypatch.setattr(pm, "BOOT_DIRS", (boot,))
    monkeypatch.setattr(pm, "CONFIG_DIR", home)
    return boot, home


def _creds(path, ssid, psk="abcdefghijkm"):
    import json
    path.write_text(json.dumps({"ssid": ssid, "psk": psk}), encoding="utf-8")


def test_credentials_are_read_from_the_boot_partition(dirs):
    boot, _home = dirs
    _creds(boot / "domovoi" / "ap.json", "Domovoi-Setup-BOOT")
    assert pm.portal_credentials()["ssid"] == "Domovoi-Setup-BOOT"


def test_the_home_copy_still_works(dirs):
    _boot, home = dirs
    _creds(home / "ap.json", "Domovoi-Setup-HOME")
    assert pm.portal_credentials()["ssid"] == "Domovoi-Setup-HOME"


def test_the_boot_partition_wins(dirs):
    """A personalized card's own identity must beat whatever the golden
    master happened to be carrying."""
    boot, home = dirs
    _creds(boot / "domovoi" / "ap.json", "Domovoi-Setup-BOOT")
    _creds(home / "ap.json", "Domovoi-Setup-HOME")
    assert pm.portal_credentials()["ssid"] == "Domovoi-Setup-BOOT"


def test_a_personalized_card_selects_the_portal(dirs):
    """The end-to-end case: flashed from a sanitized master (no home copy),
    personalized on the bench (boot copy only)."""
    boot, _home = dirs
    _creds(boot / "domovoi" / "ap.json", "Domovoi-Setup-K4T9")
    (pm.CONFIG_DIR / "image_device_profile").write_text("xvf3800_usb\n")
    from satellite import portal_transport as pt
    transport = pm._default_transport()
    assert isinstance(transport, pt.PortalTransport)
    assert transport.ap_ssid == "Domovoi-Setup-K4T9"


def test_no_credentials_anywhere_means_the_usb_gadget(dirs):
    assert pm.portal_credentials() is None
    assert isinstance(pm._default_transport(), pm.GadgetBackend)


@pytest.mark.parametrize("body", ['{"ssid": "x"}', "garbage", "{}", "[]"])
def test_an_unusable_boot_copy_falls_through_to_home(dirs, body):
    boot, home = dirs
    (boot / "domovoi" / "ap.json").write_text(body, encoding="utf-8")
    _creds(home / "ap.json", "Domovoi-Setup-HOME")
    assert pm.portal_credentials()["ssid"] == "Domovoi-Setup-HOME"
