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
