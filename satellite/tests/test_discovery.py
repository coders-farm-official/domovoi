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
from satellite import server_identity
from satellite.tests.test_server_identity import _health_opener, _server


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


# ─── the probe, once the card knows whose server it is ────────────────────


def test_probe_accepts_the_core_the_card_was_prepared_for():
    seed, public, fingerprint = _server()
    assert discovery.probe(
        "192.168.0.117", opener=_health_opener(seed, public),
        expected_fingerprint=fingerprint,
    ) is True


def test_probe_refuses_a_host_that_answers_as_domovoi_but_is_not_ours(caplog):
    """The bot_name is something anyone can type. Holding the key is not."""
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _ = _server(2)
    with caplog.at_level("WARNING"):
        assert discovery.probe(
            "192.168.0.9", opener=_health_opener(seed_b, public_b),
            expected_fingerprint=ours,
        ) is False
    assert "192.168.0.9" in caplog.text


def test_probe_refuses_a_host_with_no_identity_when_the_card_has_one():
    seed, public, fingerprint = _server()
    assert discovery.probe(
        "192.168.0.9", opener=_health_opener(seed, public, identity=False),
        expected_fingerprint=fingerprint,
    ) is False


# ─── the probe, against a host that merely relays the core ────────────────
#
# Discovery is nearest-address-first and the first host that verifies wins.
# A host nearer to the satellite than the core, forwarding /v1/health to the
# core and handing back its answer, used to win that race and be written
# down as the server. The probe now names the address it dialed; the core
# signs for its own addresses only; the relay's never match.

def test_probe_asks_the_core_to_sign_for_the_address_it_dialed():
    seed, public, fingerprint = _server()
    asked: list[dict[str, str]] = []
    assert discovery.probe(
        "192.168.0.117", port=6370,
        opener=_health_opener(seed, public, asked=asked),
        expected_fingerprint=fingerprint,
    ) is True
    assert asked[0]["addr"] == "192.168.0.117:6370"


def test_probe_refuses_a_relay_the_core_would_not_sign_for(caplog):
    """The honest relay: it forwards our question intact, the core refuses
    to sign for the relay's address, and that refusal comes back to us."""
    seed, public, fingerprint = _server()
    with caplog.at_level("WARNING"):
        assert discovery.probe(
            "192.168.0.141", opener=_health_opener(seed, public, bind="refuse"),
            expected_fingerprint=fingerprint,
        ) is False
    assert "192.168.0.141 answers as Domovoi but" in caplog.text
    assert "the signed address is not the one dialed" in caplog.text


def test_probe_refuses_a_relay_that_forwards_an_answer_signed_for_the_core():
    """The relay that rewrites our question to name the core's own address:
    the core signs, but for an address we did not dial."""
    seed, public, fingerprint = _server()
    assert discovery.probe(
        "192.168.0.141",
        opener=_health_opener(seed, public, bind="192.168.0.2:6370"),
        expected_fingerprint=fingerprint,
    ) is False


def test_probe_refuses_a_relay_that_strips_our_question(caplog):
    """The relay that drops ``addr`` so the core answers the old way: an
    unbound answer is not proof of THIS host to a pinned device."""
    seed, public, fingerprint = _server()
    with caplog.at_level("WARNING"):
        assert discovery.probe(
            "192.168.0.141", opener=_health_opener(seed, public, bind=None),
            expected_fingerprint=fingerprint,
        ) is False
    assert "not bound to the address dialed" in caplog.text


def test_a_sweep_that_finds_only_relays_finds_nothing():
    seed, public, fingerprint = _server()
    opener = _health_opener(seed, public, bind="192.168.0.2:6370")
    found = discovery.find_core(
        hosts=["192.168.0.140", "192.168.0.141"],
        probe_fn=lambda h, port=None, expected_fingerprint=None: discovery.probe(
            h, opener=opener, expected_fingerprint=expected_fingerprint
        ),
        expected_fingerprint=fingerprint,
    )
    assert found is None


def test_an_unpinned_probe_is_exactly_what_it_always_was():
    """No challenge, no address: the compatibility promise for a device
    prepared before identities. The bot_name answer is still enough."""
    body = b'{"status": "ok", "bot_name": "Domovoi", "use_stubs": "false"}'
    seen: list[str] = []

    def opener(url, timeout=None):
        seen.append(url)
        return _Resp(body)

    assert discovery.probe("192.168.0.117", opener=opener) is True
    assert "challenge=" not in seen[0] and "addr=" not in seen[0]


def test_a_sweep_that_finds_only_impostors_finds_nothing():
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _ = _server(2)
    opener = _health_opener(seed_b, public_b)
    found = discovery.find_core(
        hosts=["192.168.0.4", "192.168.0.5"],
        probe_fn=lambda h, port=None, expected_fingerprint=None: discovery.probe(
            h, opener=opener, expected_fingerprint=expected_fingerprint
        ),
        expected_fingerprint=ours,
    )
    assert found is None


def test_resolve_url_has_no_address_to_offer_when_nothing_verifies():
    assert discovery.resolve_url(
        "auto", finder=lambda port=None, expected_fingerprint=None: None,
        expected_fingerprint="SHA256:ours",
    ) is None


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


# ─── the auto sentinel must not survive provisioning ──────────────────────
#
# stage2.sh reads domovoi_url from config.toml with plain tomllib and curls
# it. It cannot expand a sentinel — so "auto" makes stage 2 fail to reach the
# server, never enable domovoi-satellite.service, and the client that COULD
# resolve it never runs. The device sits on the network invisible to the
# core. Found on hardware after a successful onboarding.


@pytest.fixture(autouse=True)
def _isolate_identity_sidecars(tmp_path, monkeypatch):
    """Keep every pin and every pending address inside the test's own
    directory: the real ones live in ~/.domovoi and /etc/domovoi."""
    monkeypatch.setattr(
        server_identity, "RECORD_SIDECAR", tmp_path / "recorded.json"
    )
    monkeypatch.setattr(server_identity, "ROOT_PIN", tmp_path / "etc-pin.json")
    monkeypatch.setattr(
        server_identity, "PENDING_SERVER_SIDECAR", tmp_path / "pending-server.json"
    )


@pytest.fixture
def cfgfile(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[satellite]\n"
        '# the server this room talks to\n'
        'domovoi_url = "auto"\n'
        'room_id = "kitchen"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pm, "CONFIG_PATH", cfg)
    return cfg


def test_a_discovered_address_is_returned_for_this_boot(cfgfile, monkeypatch):
    monkeypatch.setattr(discovery, "find_core", lambda port=None: "192.168.0.117")
    assert pm.resolve_auto_url("auto") == "ws://192.168.0.117:6370"


def test_a_discovered_address_is_not_written_into_the_config(cfgfile, monkeypatch):
    """It is a candidate, not configuration. Config.toml only learns the
    address once the server says this device is paired, which means a
    person approved it on the dashboard."""
    monkeypatch.setattr(discovery, "find_core", lambda port=None: "192.168.0.117")
    pm.resolve_auto_url("auto")
    body = cfgfile.read_text()
    assert 'domovoi_url = "auto"' in body
    assert "192.168.0.117" not in body


def test_a_discovered_address_waits_in_the_pending_sidecar(cfgfile, monkeypatch):
    monkeypatch.setattr(discovery, "find_core", lambda port=None: "192.168.0.117")
    pm.resolve_auto_url("auto")
    assert server_identity.read_pending_server()[0] == "ws://192.168.0.117:6370"


def test_the_config_file_is_left_exactly_as_it_was(cfgfile, monkeypatch):
    monkeypatch.setattr(discovery, "find_core", lambda port=None: "10.0.0.4")
    before = cfgfile.read_text()
    pm.resolve_auto_url("auto")
    assert cfgfile.read_text() == before


def test_an_explicit_address_is_left_alone(cfgfile):
    """Someone who typed an address into the setup form meant it."""
    assert pm.resolve_auto_url("ws://192.168.0.5:6370") == "ws://192.168.0.5:6370"
    assert 'domovoi_url = "auto"' in cfgfile.read_text()   # untouched


def test_failed_discovery_leaves_the_sentinel_and_says_so(cfgfile, monkeypatch, caplog):
    monkeypatch.setattr(discovery, "find_core", lambda port=None: None)
    with caplog.at_level("ERROR"):
        assert pm.resolve_auto_url("auto") is None
    assert "no Domovoi server" in caplog.text
    assert "by hand" in caplog.text                        # actionable
    assert 'domovoi_url = "auto"' in cfgfile.read_text()
    assert server_identity.read_pending_server() == (None, None)


def test_a_pinned_card_sweeps_for_its_own_server_only(cfgfile, monkeypatch):
    """The fingerprint the card was prepared with rides into the sweep, so
    a host that cannot sign for it is never even a candidate."""
    seen = {}

    def fake_find(port=None, expected_fingerprint=None):
        seen["fingerprint"] = expected_fingerprint
        return "192.168.0.117"

    server_identity.ROOT_PIN.write_text(
        '{"fingerprint": "SHA256:ours"}', encoding="utf-8"
    )
    monkeypatch.setattr(discovery, "find_core", fake_find)
    pm.resolve_auto_url("auto")
    assert seen["fingerprint"] == "SHA256:ours"


def test_it_runs_only_after_wifi_joins(monkeypatch, tmp_path):
    """Discovery needs the LAN, so it cannot run before the join — and must
    run before apply_provision returns, because the reboot follows."""
    import inspect
    src = inspect.getsource(pm.apply_provision)
    join = src.index("apply_wifi(")
    resolve = src.index("resolve_auto_url(")
    assert join < resolve


# ─── the order: the core's name, the last-known address, the sweep ────────
#
# Kamron, 2026-10-12: "the less the user has to do the better". Before this
# a satellite used whatever address it had written down, for ever: a core
# whose DHCP lease moved left every satellite dialling the old address until
# someone edited each one. The name comes first now, and the sweep last —
# and every candidate, a name's included, must sign a fresh nonce for the
# address dialled with the pinned key, so a name can never hand a paired
# satellite to an impostor.

OURS = _server(1)
THEIRS = _server(2)


def _resolver(table):
    """getaddrinfo, from a dict of name -> addresses."""
    def resolve(name, port, family=0, type=0, *a, **k):
        if name not in table:
            raise OSError(f"{name} does not resolve")
        return [(2, 1, 6, "", (addr, port)) for addr in table[name]]
    return resolve


def _lan(owners, asked=None):
    """A probe over a pretend LAN: ``owners`` maps an address to the
    server (seed, public, fingerprint) that answers there; anything else
    is silent. The real `discovery.probe` runs, identity check and all."""
    def probe_fn(host, *, port=6370, timeout=None, expected_fingerprint=None):
        if asked is not None:
            asked.append(host)
        server = owners.get(host)
        if server is None:
            return False
        seed, public, _fp = server
        return discovery.probe(host, port=port, opener=_health_opener(seed, public),
                               expected_fingerprint=expected_fingerprint)
    return probe_fn


def _no_sweep(**k):
    raise AssertionError("swept the subnet although something earlier answered")


def test_the_core_name_comes_before_the_last_known_address():
    """The core moved from .117 to .50; its name says so."""
    asked = []
    found = discovery.locate(
        names=["domovoi.local"], last_known="ws://192.168.0.117:6370",
        expected_fingerprint=OURS[2],
        resolver=_resolver({"domovoi.local": ["192.168.0.50"]}),
        probe_fn=_lan({"192.168.0.50": OURS, "192.168.0.117": OURS}, asked),
        finder=_no_sweep,
    )
    assert found == discovery.Located("ws://192.168.0.50:6370", "name", "domovoi.local -> 192.168.0.50")
    assert asked == ["192.168.0.50"]


def test_a_name_answered_by_an_impostor_is_refused():
    """Anyone on the LAN can claim domovoi.local over mDNS. Holding the
    pinned key is what they cannot do."""
    asked = []
    found = discovery.locate(
        names=["domovoi.local"], last_known="ws://192.168.0.117:6370",
        expected_fingerprint=OURS[2],
        resolver=_resolver({"domovoi.local": ["192.168.0.66"]}),
        probe_fn=_lan({"192.168.0.66": THEIRS, "192.168.0.117": OURS}, asked),
        finder=_no_sweep,
    )
    assert found is not None and found.via == "last-known"
    assert found.url == "ws://192.168.0.117:6370"
    assert asked == ["192.168.0.66", "192.168.0.117"]


def test_a_name_that_points_at_a_relay_is_refused():
    """The name resolves to a host that forwards our question to the real
    core: the core signs for its own address, not the relay's."""
    seed, public, fp = OURS

    def probe_fn(host, *, port=6370, timeout=None, expected_fingerprint=None):
        bind = "echo" if host == "192.168.0.117" else "192.168.0.117:6370"
        return discovery.probe(host, port=port, opener=_health_opener(seed, public, bind=bind),
                               expected_fingerprint=expected_fingerprint)

    found = discovery.locate(
        names=["domovoi.local"], last_known=None, expected_fingerprint=fp,
        resolver=_resolver({"domovoi.local": ["192.168.0.141"]}),
        probe_fn=probe_fn, finder=lambda **k: None,
    )
    assert found is None


def test_the_sweep_is_the_last_resort():
    order = []

    def finder(**k):
        order.append(("sweep", k.get("expected_fingerprint")))
        return "192.168.0.50"

    found = discovery.locate(
        names=["domovoi.local"], last_known="ws://192.168.0.117:6370",
        expected_fingerprint=OURS[2],
        resolver=_resolver({}),
        probe_fn=_lan({}, order), finder=finder,
    )
    assert found == discovery.Located("ws://192.168.0.50:6370", "scan", "192.168.0.50")
    # The name did not resolve, the last-known address was asked, then the sweep.
    assert order == ["192.168.0.117", ("sweep", OURS[2])]


def test_every_address_a_name_resolves_to_is_tried_in_order():
    asked = []
    found = discovery.locate(
        names=["domovoi.local"], expected_fingerprint=OURS[2],
        resolver=_resolver({"domovoi.local": ["192.168.0.9", "192.168.0.50"]}),
        probe_fn=_lan({"192.168.0.50": OURS}, asked), finder=_no_sweep,
    )
    assert found.url == "ws://192.168.0.50:6370"
    assert asked == ["192.168.0.9", "192.168.0.50"]


def test_nothing_verifying_anywhere_is_none():
    found = discovery.locate(
        names=["domovoi.local"], last_known="ws://192.168.0.117:6370",
        expected_fingerprint=OURS[2],
        resolver=_resolver({"domovoi.local": ["192.168.0.66"]}),
        probe_fn=_lan({"192.168.0.66": THEIRS}), finder=lambda **k: None,
    )
    assert found is None


def test_the_names_are_the_default_then_what_the_core_advertised(tmp_path):
    path = tmp_path / "server-names.json"
    assert discovery.server_names(path) == ["domovoi.local"]
    assert discovery.remember_names(
        ["Beelink.local.", "not a name!", "192.168.0.5", "localhost", "domovoi.local"], path
    ) is True
    assert discovery.server_names(path) == ["domovoi.local", "beelink.local"]
    # Saying the same thing again writes nothing.
    assert discovery.remember_names(["beelink.local", "domovoi.local"], path) is False
    assert discovery.remember_names("beelink.local", path) is False   # not a list


def test_a_name_lookup_never_hangs_and_never_returns_loopback():
    import threading

    gate = threading.Event()

    def slow(*a, **k):
        gate.wait(5)
        return []

    assert discovery.resolve_name("domovoi.local", timeout=0.05, resolver=slow) == []
    gate.set()
    mixed = _resolver({"domovoi.local": ["127.0.1.1", "192.168.0.50", "192.168.0.50"]})
    assert discovery.resolve_name("domovoi.local", resolver=mixed) == ["192.168.0.50"]


def test_in_tests_a_name_nobody_resolved_does_not_resolve():
    """satellite/tests/conftest.py: no real mDNS query from the suite."""
    assert discovery.resolve_name("domovoi.local") == []


def test_setup_looks_for_the_server_by_name_first(cfgfile, monkeypatch):
    """Provisioning mode's discovery after the Wi-Fi join is the same order."""
    asked = []
    monkeypatch.setattr(discovery, "_DEFAULT_RESOLVER", _resolver({"domovoi.local": ["192.168.0.50"]}))
    monkeypatch.setattr(discovery, "probe", lambda host, **k: asked.append(host) or True)
    monkeypatch.setattr(discovery, "find_core", _no_sweep)
    assert pm.resolve_auto_url("auto") == "ws://192.168.0.50:6370"
    assert asked == ["192.168.0.50"]


def test_a_core_found_by_name_keeps_the_configured_scheme_and_port():
    """`wss://` or a custom port typed into config.toml is not traded for
    `ws://<ip>:6370` when the name finds the core."""
    asked = []

    def probe_fn(host, *, port=6370, timeout=None, expected_fingerprint=None, scheme="http"):
        asked.append((host, port, scheme))
        return host == "192.168.0.50"

    found = discovery.locate(
        names=["domovoi.local"], last_known="wss://192.168.0.117:7443",
        expected_fingerprint=OURS[2],
        resolver=_resolver({"domovoi.local": ["192.168.0.50"]}),
        probe_fn=probe_fn, finder=_no_sweep,
    )
    assert found.url == "wss://192.168.0.50:7443"
    assert asked == [("192.168.0.50", 7443, "https")]


def test_a_refusing_last_address_skips_the_sweep_but_not_the_names():
    asked = []
    found = discovery.locate(
        names=["domovoi.local"], last_known="ws://192.168.0.117:6370",
        expected_fingerprint=OURS[2],
        resolver=_resolver({}),
        probe_fn=_lan({}, asked), finder=_no_sweep,
        refused=lambda host, port: (host, port) == ("192.168.0.117", 6370),
    )
    assert found is None
    assert asked == ["192.168.0.117"]


def test_the_http_base_of_a_core_address():
    assert discovery.http_base_of("ws://192.168.0.5:6370") == "http://192.168.0.5:6370"
    assert discovery.http_base_of("wss://domovoi.local") == "https://domovoi.local:6370"
    assert discovery.host_port_of("ws://10.0.0.2") == ("10.0.0.2", 6370)



# ─── a spare computer moves only within the home network (review round 2) ─

def test_a_laptop_never_follows_its_server_off_the_home_network(tmp_path, monkeypatch):
    """Even when the address answers with the pinned key (the server, or a
    relay of it, on a public address): a laptop's microphone streams only
    within the house, like the enrolment code that named the server."""
    from types import SimpleNamespace

    from satellite.tests._client_import import import_client

    client = import_client()
    cfg = client.Config.from_toml({
        "device": {"profile": "generic_pc"},
        "satellite": {"room_id": "study", "domovoi_url": "ws://192.168.0.117:6370",
                      "server_fingerprint": "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"},
    })
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[satellite]\ndomovoi_url = "ws://192.168.0.117:6370"\n', encoding="utf-8")
    for url in ("ws://203.0.113.9:6370", "ws://8.8.8.8:6370", "ws://core.example.com:6370", "ws://127.0.0.1:6370"):
        client._adopt_located(cfg, cfg_path, SimpleNamespace(url=url, via="sweep", detail=""), False)
        assert cfg.domovoi_url == "ws://192.168.0.117:6370", url
        assert "192.168.0.117" in cfg_path.read_text(encoding="utf-8")
    client._adopt_located(cfg, cfg_path, SimpleNamespace(url="ws://domovoi.local:6370", via="name", detail=""), False)
    assert cfg.domovoi_url == "ws://domovoi.local:6370" and "domovoi.local" in cfg_path.read_text(encoding="utf-8")
    # A Pi follows its pinned server wherever it proves itself, as before.
    pi = client.Config.from_toml({"device": {"profile": "xvf3800_usb"},
                                  "satellite": {"domovoi_url": "ws://192.168.0.117:6370"}})
    client._adopt_located(pi, cfg_path, SimpleNamespace(url="ws://203.0.113.9:6370", via="sweep", detail=""), False)
    assert pi.domovoi_url == "ws://203.0.113.9:6370"
