"""A LAN caller cannot push the sounds channel's serial ahead of the clock
(V-st-01 / REV-09, 2026-10-08).

``GET /v1/sounds/manifest.sig`` is open and takes a caller-chosen
``?voice=``. While every voice's list shared one serial-store entry,
alternating two voices flipped the channel's digest on every request and
minted serial + 1 each time: 2000 alternations ran the serial 1991 s ahead
of the clock (phase-2 measurement). The store's own promise — a LOST store
still mints above anything a satellite remembers, because no serial was
ever ahead of the clock — then failed: after a lost or restored
``manifest-serials.json`` every pinned satellite refused the sounds lists
until the clock caught up.

Now each voice's list keeps its own entry (``<channel>@<voice>``), minted
only when THAT list changes, from the channel's highest serial; a name with
no rendered clips shares one entry. DB-free: the voice roots are tmp dirs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from domovoi import admin_auth, main, server_identity
from satellite import server_identity as sat_identity


@pytest.fixture(autouse=True)
def identity_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    server_identity.reset_cache()
    yield server_identity.load_or_create()
    server_identity.reset_cache()


@pytest.fixture
def voices(tmp_path, monkeypatch) -> dict[str, Path]:
    """Two voices with rendered clips, a default, and nothing else."""
    root = tmp_path / "voices"
    dirs = {}
    for slug, body in (("ryan", b"ryan"), ("amy", b"amy"), ("lessac", b"lessac")):
        d = root / slug
        (d / "greetings").mkdir(parents=True)
        (d / "greetings" / "hello.mp3").write_bytes(body)
        dirs[slug] = d

    async def resolve(voice):
        if not voice:
            return dirs["lessac"]                 # the registry default
        return root / voice.strip().lower()       # what voice_dir() does

    monkeypatch.setattr(main, "_resolve_voice_root", resolve)
    return dirs


def _store(identity) -> dict:
    return json.loads(identity.serials_path().read_text(encoding="utf-8"))


async def _serial(voice: str | None) -> int:
    envelope = await main.sounds_manifest_signed(voice)
    return envelope["serial"]


@pytest.mark.asyncio
async def test_alternating_voices_mints_nothing(voices) -> None:
    """The phase-2 loop: ``?voice=ryan`` then ``?voice=amy``, over and over."""
    serials = [await _serial("ryan" if i % 2 == 0 else "amy") for i in range(60)]
    assert len(set(serials)) == 2, sorted(set(serials))
    assert max(serials) <= int(time.time()) + 1, "the serial ran ahead of the clock"
    # And each voice keeps the same one it was first given.
    assert len(set(serials[0::2])) == 1 and len(set(serials[1::2])) == 1


@pytest.mark.asyncio
async def test_names_with_no_clips_share_one_entry(identity_in_tmp, voices) -> None:
    """A caller inventing voice names cannot mint, nor grow the store."""
    serials = [await _serial(f"zz-none-{i}") for i in range(40)]
    assert len(set(serials)) == 1
    keys = set(_store(identity_in_tmp))
    assert keys == {server_identity.sounds_serial_key("?")}, keys


@pytest.mark.asyncio
async def test_the_default_voice_keeps_its_own_entry(identity_in_tmp, voices) -> None:
    default = await _serial(None)
    named = await _serial("ryan")
    assert await _serial(None) == default
    assert await _serial("ryan") == named
    assert set(_store(identity_in_tmp)) == {
        server_identity.sounds_serial_key(""), server_identity.sounds_serial_key("ryan"),
    }


@pytest.mark.asyncio
async def test_a_real_change_to_one_voice_gets_a_serial_above_every_voice(voices) -> None:
    ryan, amy = await _serial("ryan"), await _serial("amy")
    (voices["ryan"] / "greetings" / "new.mp3").write_bytes(b"new greeting")
    changed = await _serial("ryan")
    assert changed > max(ryan, amy)
    assert await _serial("amy") == amy, "a change to ryan's clips is not a change to amy's"


@pytest.mark.asyncio
async def test_the_envelope_still_verifies_on_a_satellite(identity_in_tmp, voices) -> None:
    envelope = await main.sounds_manifest_signed("amy")
    assert sat_identity.verify_manifest_envelope(
        envelope, channel=sat_identity.SOUNDS_CHANNEL,
        expected_fingerprint=identity_in_tmp.fingerprint,
    ) == {"greetings/hello.mp3": envelope["manifest"]["greetings/hello.mp3"]}


# ─── the pure function: the promise the store makes ─────────────────────

T = 1_760_000_000


def test_a_lost_store_after_an_alternation_still_moves_forward(identity_in_tmp) -> None:
    identity = identity_in_tmp
    channel = server_identity.SOUNDS_CHANNEL
    a, b = server_identity.sounds_serial_key("a"), server_identity.sounds_serial_key("b")
    issued = []
    for i in range(2000):
        key, lst = (a, {"x.mp3": "1" * 64}) if i % 2 == 0 else (b, {"x.mp3": "2" * 64})
        issued.append(identity.signed_manifest(channel, lst, now=T + i // 500, serial_key=key)["serial"])
    assert max(issued) <= T + 1, f"ran {max(issued) - T} s ahead of the clock"
    identity.serials_path().unlink()
    after = identity.signed_manifest(channel, {"x.mp3": "1" * 64}, now=T + 10, serial_key=a)
    assert after["serial"] > max(issued)


def test_an_upgraded_store_mints_above_the_old_shared_serial(identity_in_tmp) -> None:
    """A store written before the per-voice keys has one ``satellite-sounds``
    entry, possibly far ahead (it may have been pushed there). The first
    per-voice serial must still beat it: pinned satellites remember it."""
    identity = identity_in_tmp
    channel = server_identity.SOUNDS_CHANNEL
    path = identity.serials_path()
    path.write_text(json.dumps({
        channel: {"serial": T + 5000, "issued_at": T, "digest": "old"},
        server_identity.CODE_CHANNEL: {"serial": T + 99999, "issued_at": T, "digest": "c"},
    }), encoding="utf-8")
    first = identity.signed_manifest(
        channel, {"x.mp3": "1" * 64}, now=T, serial_key=server_identity.sounds_serial_key("")
    )
    assert first["serial"] == T + 5001
    # Another channel's serials are not this channel's high water.
    assert first["serial"] < T + 99999


def test_other_channels_keep_one_entry_each(identity_in_tmp) -> None:
    identity = identity_in_tmp
    channel = server_identity.CODE_CHANNEL
    first = identity.signed_manifest(channel, {"a.py": "1" * 64}, now=T)
    changed = identity.signed_manifest(channel, {"a.py": "2" * 64}, now=T)
    assert changed["serial"] == first["serial"] + 1
    assert set(_store(identity)) == {channel}
