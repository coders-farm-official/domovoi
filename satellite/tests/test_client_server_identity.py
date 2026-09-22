"""The client's side of "which server is ours".

Three behaviours, all of them things that used to be missing:

* a discovered address is used for this boot and kept OUT of config.toml
  until the server says this device is paired;
* every connect makes the server prove its identity first, not just the
  first one ever;
* a device that has nothing pinned records the first identity it meets and
  is held to it afterwards.

No network, no audio stack, no Postgres.
"""

from __future__ import annotations

import inspect
import types

import pytest

from satellite import server_identity
from satellite.tests._client_import import import_client
from satellite.tests.test_server_identity import _health_opener, _server

client = import_client()


@pytest.fixture(autouse=True)
def _isolate_sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server_identity, "RECORD_SIDECAR", tmp_path / "recorded.json"
    )
    monkeypatch.setattr(server_identity, "ROOT_PIN", tmp_path / "etc-pin.json")
    monkeypatch.setattr(
        server_identity, "PENDING_SERVER_SIDECAR", tmp_path / "pending-server.json"
    )
    # The "this core has none" latch is per process; each test starts fresh.
    monkeypatch.setattr(client, "_SERVER_HAS_NO_IDENTITY", False)


def _cfg(url="auto", fingerprint=""):
    return types.SimpleNamespace(
        domovoi_url=url, server_fingerprint=fingerprint, room_id="kitchen"
    )


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        "[satellite]\n"
        "# the server this room talks to\n"
        'domovoi_url = "auto"\n'
        'room_id = "kitchen"\n',
        encoding="utf-8",
    )
    return p


# ─── a discovered address is a candidate, not configuration ───────────────

def test_a_discovered_address_is_used_this_boot(config_file, monkeypatch):
    from satellite import discovery

    monkeypatch.setattr(discovery, "find_core", lambda port=None: "192.168.0.117")
    cfg = _cfg()
    assert client._resolve_server_url(cfg, config_file) is True
    assert cfg.domovoi_url == "ws://192.168.0.117:6370"


def test_a_discovered_address_does_not_reach_the_config_file(config_file, monkeypatch):
    from satellite import discovery

    monkeypatch.setattr(discovery, "find_core", lambda port=None: "192.168.0.117")
    before = config_file.read_text(encoding="utf-8")
    client._resolve_server_url(_cfg(), config_file)
    assert config_file.read_text(encoding="utf-8") == before
    assert server_identity.read_pending_server()[0] == "ws://192.168.0.117:6370"


def test_a_second_boot_reuses_the_pending_address_instead_of_sweeping(config_file):
    server_identity.write_pending_server("ws://192.168.0.117:6370")
    cfg = _cfg()

    def never(*a, **k):      # a sweep here would be the bug
        raise AssertionError("swept the subnet again")

    from satellite import discovery

    original = discovery.find_core
    discovery.find_core = never
    try:
        assert client._resolve_server_url(cfg, config_file) is True
    finally:
        discovery.find_core = original
    assert cfg.domovoi_url == "ws://192.168.0.117:6370"


def test_an_address_someone_typed_is_left_alone(config_file):
    cfg = _cfg("ws://192.168.0.5:6370")
    assert client._resolve_server_url(cfg, config_file) is True
    assert cfg.domovoi_url == "ws://192.168.0.5:6370"
    assert server_identity.read_pending_server() == (None, None)


# ─── approval is what makes it configuration ──────────────────────────────

def test_approval_writes_the_address_into_the_config(config_file):
    server_identity.write_pending_server("ws://192.168.0.117:6370", "SHA256:ours")
    client._promote_pending_server(config_file)
    body = config_file.read_text(encoding="utf-8")
    assert 'domovoi_url = "ws://192.168.0.117:6370"' in body
    assert 'server_fingerprint = "SHA256:ours"' in body
    assert "# the server this room talks to" in body      # comments survive
    assert server_identity.read_pending_server() == (None, None)


def test_a_device_with_nothing_pending_is_not_touched_by_approval(config_file):
    before = config_file.read_text(encoding="utf-8")
    client._promote_pending_server(config_file)
    assert config_file.read_text(encoding="utf-8") == before


def test_the_ready_frame_is_what_promotes_it():
    """``ready`` means the core accepted this device, which it only does
    once a person approved it on the dashboard."""
    src = inspect.getsource(client.Satellite._handle_text_frame)
    ready = src.index('if t == "ready"')
    promote = src.index("_promote_pending_server(")
    transcript = src.index('elif t == "transcript"')
    assert ready < promote < transcript


# ─── proving the server on every connect ──────────────────────────────────

def test_the_server_that_holds_our_key_is_accepted():
    seed, public, fingerprint = _server()
    cfg = _cfg("ws://192.168.0.117:6370", fingerprint)
    assert client._verify_server_identity(
        cfg, opener=_health_opener(seed, public)
    ) is True


def test_a_different_server_on_our_address_is_refused(caplog):
    _seed_a, _public_a, ours = _server(1)
    seed_b, public_b, _ = _server(2)
    cfg = _cfg("ws://192.168.0.117:6370", ours)
    with caplog.at_level("ERROR"):
        assert client._verify_server_identity(
            cfg, opener=_health_opener(seed_b, public_b)
        ) is False
    assert "refusing to connect" in caplog.text


def test_a_device_with_nothing_pinned_records_what_it_meets():
    seed, public, fingerprint = _server()
    cfg = _cfg("ws://192.168.0.117:6370")
    assert client._verify_server_identity(
        cfg, opener=_health_opener(seed, public)
    ) is True
    assert server_identity.pinned_fingerprint() == (fingerprint, "recorded")


def test_and_is_then_held_to_it():
    seed_a, public_a, _ours = _server(1)
    seed_b, public_b, _ = _server(2)
    cfg = _cfg("ws://192.168.0.117:6370")
    client._verify_server_identity(cfg, opener=_health_opener(seed_a, public_a))
    assert client._verify_server_identity(
        cfg, opener=_health_opener(seed_b, public_b)
    ) is False


def test_an_older_core_with_no_identity_is_still_talked_to():
    """The compatibility promise: the two Pis in the house are already
    paired to a core that will not carry an identity until it is upgraded,
    and refusing here would take them off the air."""
    seed, public, _fingerprint = _server()
    cfg = _cfg("ws://192.168.0.117:6370")
    assert client._verify_server_identity(
        cfg, opener=_health_opener(seed, public, identity=False)
    ) is True
    assert server_identity.pinned_fingerprint() == (None, "none")


def test_an_older_core_is_only_asked_once_a_boot():
    """Every reconnect attempt during an outage would otherwise spend a
    round trip in front of the socket, asking a core that will not have
    grown an identity since the last attempt."""
    seed, public, _fingerprint = _server()
    cfg = _cfg("ws://192.168.0.117:6370")
    client._verify_server_identity(
        cfg, opener=_health_opener(seed, public, identity=False)
    )

    def must_not_ask(*a, **k):
        raise AssertionError("asked the same core again")

    assert client._verify_server_identity(cfg, opener=must_not_ask) is True


def test_a_pinned_device_keeps_asking_an_unreachable_server():
    """The latch is only for a core that HAS no identity. One that is
    merely down has to be re-checked, because the next attempt may reach
    the real server."""
    _seed, _public, ours = _server()
    cfg = _cfg("ws://192.168.0.117:6370", ours)
    calls = []

    def down(url, timeout=None):
        calls.append(url)
        raise OSError("connection refused")

    assert client._verify_server_identity(cfg, opener=down) is False
    assert client._verify_server_identity(cfg, opener=down) is False
    assert len(calls) == 2


def test_an_unreachable_server_does_not_block_an_unpinned_device():
    """Nothing to compare against, so the socket attempt below is what
    reports the outage — with its own message and its own backoff."""
    cfg = _cfg("ws://192.168.0.117:6370")

    def down(url, timeout=None):
        raise OSError("connection refused")

    assert client._verify_server_identity(cfg, opener=down) is True


def test_the_identity_is_proved_before_the_socket_is_opened():
    src = inspect.getsource(client.Satellite._run_session)
    verify = src.index("_verify_server_identity")
    connect = src.index("websockets.connect")
    assert verify < connect


def test_the_upgrade_path_carries_the_fingerprint_into_both_syncs():
    src = inspect.getsource(client.Satellite)
    assert src.count("expected_fingerprint=_pinned_fingerprint(self.cfg)[0]") == 2


def test_the_pin_is_read_from_the_config_first():
    cfg = _cfg("ws://x:6370", "SHA256:from-config")
    server_identity.record_fingerprint("SHA256:recorded")
    assert client._pinned_fingerprint(cfg) == ("SHA256:from-config", "config")


def test_a_config_without_the_key_at_all_still_works():
    """An older config.toml has no server_fingerprint line; reading it must
    not be an AttributeError on a device that is otherwise fine."""
    assert client._pinned_fingerprint(types.SimpleNamespace()) == (None, "none")
