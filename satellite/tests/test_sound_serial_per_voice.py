"""A device remembers the sounds serial per voice, as the server mints it
(V-st-01 / REV-09, 2026-10-08).

The server now keeps one serial per voice on the sounds channel, so an open
``?voice=`` alternation cannot run the channel's serial ahead of the clock.
Two voices' lists therefore carry serials that need not be in the order a
device meets them: a device that switches from a voice minted later to one
minted earlier (and back) would refuse the earlier one as "older than the
one this device already accepted" if it still kept a single record for the
whole channel, and keep speaking with the old voice's cached clips. So the
device keeps one record per voice too, and judges a voice's first list
against the record it kept before this change (the floor), so a recording
replayed across the upgrade is still refused.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from satellite import server_identity, sound_sync
from satellite.tests.test_signed_manifests import _envelope, _server


class _Resp:
    status_code = 200

    def __init__(self, *, json_data=None, content=b""):
        self._json = json_data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def _serve(monkeypatch, seed, public, lists: dict[str | None, tuple[dict[str, bytes], int]]):
    """A server answering ``manifest.sig?voice=`` with each voice's own list
    under its own serial (``None`` = no ``?voice=``)."""

    def fake_get(url, params=None, timeout=None):
        voice = (params or {}).get("voice")
        clips, serial = lists[voice]
        if url.endswith("/v1/sounds/manifest.sig"):
            manifest = {rel: hashlib.sha256(b).hexdigest() for rel, b in clips.items()}
            return _Resp(json_data=_envelope(
                seed, public, "satellite-sounds", manifest, serial=serial))
        rel = url.split("/v1/sounds/", 1)[1]
        return _Resp(content=clips[rel])

    monkeypatch.setattr(sound_sync.requests, "get", fake_get)


def test_switching_voice_and_back_keeps_syncing(tmp_path, monkeypatch) -> None:
    seed, public, fingerprint = _server()
    # amy's list was minted after ryan's.
    _serve(monkeypatch, seed, public, {
        "ryan": ({"greetings/hi.mp3": b"ryan"}, 100),
        "amy": ({"greetings/hi.mp3": b"amy"}, 200),
    })
    cache = tmp_path / "cache"
    for voice, body in (("ryan", b"ryan"), ("amy", b"amy"), ("ryan", b"ryan")):
        sound_sync.sync("http://server:6370", cache, voice=voice,
                        expected_fingerprint=fingerprint)
        assert (cache / "greetings" / "hi.mp3").read_bytes() == body, voice


def test_a_replay_within_one_voice_is_still_refused(tmp_path, monkeypatch) -> None:
    seed, public, fingerprint = _server()
    _serve(monkeypatch, seed, public, {"ryan": ({"greetings/hi.mp3": b"new"}, 100)})
    cache = tmp_path / "cache"
    sound_sync.sync("http://server:6370", cache, voice="ryan", expected_fingerprint=fingerprint)
    _serve(monkeypatch, seed, public, {"ryan": ({"greetings/hi.mp3": b"old"}, 90)})
    with pytest.raises(RuntimeError) as e:
        sound_sync.sync("http://server:6370", cache, voice="ryan",
                        expected_fingerprint=fingerprint)
    assert "older" in str(e.value)
    assert (cache / "greetings" / "hi.mp3").read_bytes() == b"new"


def test_the_record_kept_before_the_change_is_the_floor(tmp_path, monkeypatch) -> None:
    """A device upgraded from one record per channel: a recording older
    than what it accepted then is refused under the new record too."""
    seed, public, fingerprint = _server()
    sidecar = server_identity.FRESHNESS_SIDECAR
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({
        "satellite-sounds": {"serial": 500, "issued_at": 1, "digest": "x"},
    }), encoding="utf-8")
    cache = tmp_path / "cache"
    _serve(monkeypatch, seed, public, {None: ({"network_issues.mp3": b"replayed"}, 400)})
    with pytest.raises(RuntimeError):
        sound_sync.sync("http://server:6370", cache, expected_fingerprint=fingerprint)
    _serve(monkeypatch, seed, public, {None: ({"network_issues.mp3": b"fresh"}, 600)})
    assert sound_sync.sync("http://server:6370", cache, expected_fingerprint=fingerprint) == 1
    store = json.loads(sidecar.read_text(encoding="utf-8"))
    assert store[server_identity.sounds_record(None)]["serial"] == 600
    assert store["satellite-sounds"]["serial"] == 500, "the old record is left as the floor"


def test_other_channels_keep_one_record() -> None:
    """Only the sounds sync passes a record; the code, plugin and wake-model
    channels judge against the channel's own record as before."""
    seed, public, fingerprint = _server()
    doc = _envelope(seed, public, server_identity.CODE_CHANNEL, {"a.py": "1" * 64}, serial=7)
    server_identity.accept_manifest_envelope(
        doc, channel=server_identity.CODE_CHANNEL, expected_fingerprint=fingerprint,
    )
    store = json.loads(server_identity.FRESHNESS_SIDECAR.read_text(encoding="utf-8"))
    assert set(store) == {server_identity.CODE_CHANNEL}
