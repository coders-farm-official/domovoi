"""Friend Houses keys, signatures and address statements
(domovoi/friend_houses/identity.py)."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from domovoi.friend_houses import identity as fid


def test_create_then_load_round_trips(tmp_path) -> None:
    path = tmp_path / "fh" / "house-keys.json"
    keys = fid.create(path)
    again = fid.load(path)
    assert again is not None
    assert again.house_key == keys.house_key
    assert again.session_key == keys.session_key
    assert keys.fingerprint.startswith("SHA256:")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_key_file_is_private(tmp_path) -> None:
    path = tmp_path / "fh" / "house-keys.json"
    fid.create(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_create_never_overwrites(tmp_path) -> None:
    path = tmp_path / "house-keys.json"
    fid.create(path)
    with pytest.raises(FileExistsError):
        fid.create(path)


def test_a_missing_file_is_none_and_a_damaged_one_is_not_replaced(tmp_path) -> None:
    path = tmp_path / "house-keys.json"
    assert fid.load(path) is None
    path.write_text('{"schema": 1, "house_seed": "nope"}', encoding="utf-8")
    assert fid.load(path) is None
    assert path.read_text(encoding="utf-8").startswith('{"schema": 1')
    with pytest.raises(FileExistsError):
        fid.create(path)


def test_signatures_are_bound_to_their_context(tmp_path) -> None:
    keys = fid.create(tmp_path / "k.json")
    sig = keys.sign(fid.CTX_MESSAGE, b"body")
    assert fid.verify(keys.house_key, fid.CTX_MESSAGE, b"body", sig)
    assert not fid.verify(keys.house_key, fid.CTX_ADDRESS, b"body", sig)
    assert not fid.verify(keys.house_key, fid.CTX_MESSAGE, b"bodY", sig)
    assert not fid.verify(b"\x00" * 31, fid.CTX_MESSAGE, b"body", sig)
    assert not fid.verify(keys.house_key, fid.CTX_MESSAGE, b"body", sig[:63])
    with pytest.raises(ValueError):
        keys.sign(b"made-up", b"body")


def test_onion_addresses_round_trip_and_reject_bad_checksums() -> None:
    key = bytes(range(32))
    address = fid.onion_address(key)
    assert address.endswith(".onion") and len(address) == 62
    assert fid.parse_onion(address) == key
    assert fid.parse_onion(address.upper()) == key
    broken = ("b" if address[0] != "b" else "c") + address[1:]
    assert fid.parse_onion(broken) is None
    assert fid.parse_onion("short.onion") is None
    assert fid.parse_onion(None) is None  # type: ignore[arg-type]


def test_a_known_onion_address_parses() -> None:
    # The Tor Project's own v3 address, as published.
    known = "2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion"
    key = fid.parse_onion(known)
    assert key is not None and fid.onion_address(key) == known


def test_address_statements_verify_and_order_by_time(tmp_path) -> None:
    keys = fid.create(tmp_path / "k.json")
    onion = bytes(range(32))
    first = keys.address_statement(onion, 1_800_000_000)
    parsed = fid.AddressStatement.parse(first.to_bytes())
    assert parsed == first
    assert parsed.session_key == keys.session_key
    newer = keys.address_statement(onion, 1_800_000_001)
    assert newer.supersedes(first) and not first.supersedes(newer)
    assert not first.supersedes(first)


def test_a_tampered_or_foreign_statement_is_refused(tmp_path) -> None:
    keys = fid.create(tmp_path / "a.json")
    other = fid.create(tmp_path / "b.json")
    raw = bytearray(keys.address_statement(bytes(32), 5).to_bytes())
    raw[-70] ^= 1  # inside issued_at
    assert fid.AddressStatement.parse(bytes(raw)) is None
    assert fid.AddressStatement.parse(b"") is None
    assert fid.AddressStatement.parse(bytes(fid.STATEMENT_SIZE)) is None
    theirs = other.address_statement(bytes(32), 10)
    assert not theirs.supersedes(keys.address_statement(bytes(32), 5))


def test_the_epoch_survives_a_clean_restart_and_changes_after_a_rollback() -> None:
    epoch, marker, changed = fid.resolve_epoch(None, None, None)
    assert changed and len(epoch) == fid.EPOCH_BYTES
    assert fid.resolve_epoch(epoch, marker, marker) == (epoch, marker, False)
    new_epoch, new_marker, changed = fid.resolve_epoch(epoch, "older-marker", marker)
    assert changed and new_epoch != epoch and new_marker != marker
