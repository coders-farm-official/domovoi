"""provisioning_protocol — checksums, validation, and the torn-write /
stale-nonce rejection rules both sides depend on."""

from __future__ import annotations

import pytest

from satellite import provisioning_protocol as proto


def _provision(**overrides):
    kw = dict(
        nonce="ab" * 8,
        room_id="den",
        domovoi_url="ws://192.168.1.50:6370",
        sat_type="video",
        device_profile="radxa_zero3w_video",
        pairing_token="c" * 64,
        wifi_ssid="HomeNet",
        wifi_psk="hunter2hunter2",
        wifi_country="US",
    )
    kw.update(overrides)
    return proto.build_provision(**kw)


def test_checksum_is_key_order_independent() -> None:
    a = proto.payload_checksum({"b": 1, "a": {"y": 2, "x": 3}})
    b = proto.payload_checksum({"a": {"x": 3, "y": 2}, "b": 1})
    assert a == b


def test_provision_roundtrip() -> None:
    doc = _provision()
    payload = proto.validate_provision(doc, "ab" * 8)
    assert payload["room_id"] == "den"
    assert payload["wifi"]["psk"] == "hunter2hunter2"


def test_provision_rejects_stale_nonce() -> None:
    doc = _provision()
    with pytest.raises(proto.ProvisionInvalid, match="nonce"):
        proto.validate_provision(doc, "ff" * 8)


def test_provision_rejects_torn_write() -> None:
    doc = _provision()
    doc["payload"]["wifi"]["psk"] = "corrupted-mid-write"
    with pytest.raises(proto.ProvisionInvalid, match="checksum"):
        proto.validate_provision(doc, "ab" * 8)


def test_provision_rejects_missing_fields() -> None:
    doc = _provision()
    del doc["payload"]["pairing_token"]
    doc["payload_sha256"] = proto.payload_checksum(doc["payload"])
    with pytest.raises(proto.ProvisionInvalid, match="pairing_token"):
        proto.validate_provision(doc, "ab" * 8)


def test_provision_error_messages_never_leak_credentials() -> None:
    doc = _provision()
    doc["payload"]["wifi"]["psk"] = "SUPERSECRET-LEAK-CHECK"
    try:
        proto.validate_provision(doc, "ab" * 8)
    except proto.ProvisionInvalid as e:
        assert "SUPERSECRET" not in str(e)


def test_device_info_roundtrip_and_mac_normalization() -> None:
    info = proto.build_device_info(
        nonce="1234abcd", mac="B8:27:EB:AA:BB:CC",
        board="pi", model="Pi", status="wifi_failed", error="bad psk",
    )
    out = proto.validate_device_info(info)
    assert out["mac"] == "b8:27:eb:aa:bb:cc"
    assert out["status"] == "wifi_failed"


def test_device_info_rejects_bad_version_and_nonce() -> None:
    with pytest.raises(proto.ProvisionInvalid):
        proto.validate_device_info({"domovoi_setup": 2, "nonce": "x" * 16})
    with pytest.raises(proto.ProvisionInvalid):
        proto.validate_device_info({"domovoi_setup": 1, "nonce": "abc"})


def test_device_info_coerces_unknown_sat_type() -> None:
    info = proto.build_device_info(nonce="1234abcd", mac=None, board=None, model=None)
    info["sat_type"] = "toaster"
    assert proto.validate_device_info(info)["sat_type"] == "voice"


def test_build_device_info_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        proto.build_device_info(
            nonce="1234abcd", mac=None, board=None, model=None, status="exploded"
        )


# ─── the server address ───────────────────────────────────────────────────
#
# The setup portal's address field is typed by whoever joined the setup
# network, and the satellite hands its pairing token to whatever it dials.
# So the portal accepts a ws:// address on the house LAN and nothing else;
# the device-side validation checks the shape on every path.


@pytest.mark.parametrize("raw,expected", [
    ("", "auto"),
    ("   ", "auto"),
    ("auto", "auto"),
    ("AUTO", "auto"),
    ("ws://192.168.0.117:6370", "ws://192.168.0.117:6370"),
    ("ws://192.168.0.117:6370/", "ws://192.168.0.117:6370"),
    ("wss://10.1.2.3:8443", "wss://10.1.2.3:8443"),
    ("ws://172.31.255.254", "ws://172.31.255.254:6370"),
    ("192.168.1.20", "ws://192.168.1.20:6370"),
    ("192.168.1.20:6371", "ws://192.168.1.20:6371"),
    ("domovoi.local", "ws://domovoi.local:6370"),
    ("ws://Domovoi-Server.local:6370", "ws://domovoi-server.local:6370"),
])
def test_lan_server_addresses_are_normalised(raw, expected) -> None:
    assert proto.normalize_server_url(raw, lan_only=True) == expected


@pytest.mark.parametrize("raw", [
    "http://192.168.0.117:6370",       # not a WebSocket address
    "https://domovoi.local",
    "ftp://192.168.0.117",
    "ws://8.8.8.8:6370",               # public
    "ws://1.1.1.1",
    "ws://example.com:6370",           # a public name
    "ws://domovoi.example.com:6370",
    "ws://domovoi.local.example.com",  # .local somewhere other than the end
    "ws://127.0.0.1:6370",             # the satellite itself
    "ws://169.254.1.1:6370",           # link-local
    "ws://100.64.0.1:6370",            # carrier-grade NAT
    "ws://172.32.0.1:6370",            # just outside 172.16/12
    "ws://[fd00::1]:6370",             # IPv6 is not what the LAN rule allows
    "ws://user:pw@192.168.0.117:6370", # credentials
    "ws://192.168.0.117:6370/v1/x",    # a path
    "ws://192.168.0.117:6370?x=1",
    "ws://192.168.0.117:0",
    "ws://192.168.0.117:99999",
    "ws://192.168.0.117:abc",
    "ws://",
    "ws:///v1",
    "ws://192.168.0.\n117:6370",       # a newline inside is not a host
    "ws://192.168.0.117:6370\tx",
    "ws://" + "a" * 300 + ".local",
])
def test_the_portal_refuses_addresses_off_the_lan_or_off_shape(raw) -> None:
    with pytest.raises(proto.ProvisionInvalid) as e:
        proto.normalize_server_url(raw, lan_only=True)
    # The reason is the form's help text, never an echo of what was typed.
    assert str(e.value)
    assert "pw@" not in str(e.value) and "example.com" not in str(e.value)


def test_without_the_lan_rule_a_hostname_is_still_a_ws_address() -> None:
    """The USB adoption flow may carry an admin-set hostname; the shape
    rule still applies to it."""
    assert proto.normalize_server_url("ws://beelink:6370") == "ws://beelink:6370"
    assert proto.normalize_server_url("beelink") == "ws://beelink:6370"
    with pytest.raises(proto.ProvisionInvalid):
        proto.normalize_server_url("http://beelink:6370")


def test_validate_provision_requires_a_websocket_address() -> None:
    with pytest.raises(proto.ProvisionInvalid):
        proto.validate_provision(_provision(domovoi_url="http://192.168.1.50:6370"), "ab" * 8)
    with pytest.raises(proto.ProvisionInvalid):
        proto.validate_provision(_provision(domovoi_url="ws://a:b@192.168.1.50"), "ab" * 8)
    # ...and the discovery sentinel is still a valid answer.
    payload = proto.validate_provision(_provision(domovoi_url="auto"), "ab" * 8)
    assert payload["domovoi_url"] == "auto"
