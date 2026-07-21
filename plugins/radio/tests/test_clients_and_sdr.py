"""Client scaffolding: stub determinism, ffmpeg-grab failure paths, the
plugin UA, and SdrTuner's config gates + URL shape (subprocess plumbing
is exercised only where it can fail fast without hardware)."""

from __future__ import annotations

import asyncio

import pytest

from domovoi_plugin_radio import USER_AGENT
from domovoi_plugin_radio.clients import shazam_stream
from domovoi_plugin_radio.clients.fcc_fm import FccFmStubClient
from domovoi_plugin_radio.clients.radio_browser import RadioBrowserStubClient
from domovoi_plugin_radio.clients.rtl_sdr import SdrTuner
from domovoi_plugin_radio.clients.shazam_stream import (
    ShazamStreamStubClient,
    TrackIdentity,
)


def test_plugin_user_agent_branding() -> None:
    assert USER_AGENT.startswith("domovoi-radio/1.0")
    assert "github.com/coders-farm-official/domovoi" in USER_AGENT


# ─── Stub clients (what USE_STUBS=true wires in) ────────────────────────


async def test_radio_browser_stub_paginates() -> None:
    stub = RadioBrowserStubClient()
    assert await stub.search() == []                      # empty query guard
    page = await stub.search(name="jazz", limit=30)
    assert len(page) == 3
    assert all(s.external_id.startswith("stub-jazz-") for s in page)
    assert await stub.search(name="jazz", offset=3) == []


async def test_fcc_stub_shape() -> None:
    stub = FccFmStubClient()
    rows = await stub.fetch_state("mi".upper())
    assert [r.call_sign for r in rows] == ["KMIA", "KMIB"]
    assert await stub.fetch_state("X") == []


async def test_shazam_stub_encodes_input() -> None:
    stub = ShazamStreamStubClient()
    ident = await stub.identify_wav("C:/tmp/sample-abc.wav")
    assert ident == TrackIdentity(title="stub-wav-sample-abc", artist="Stub Artist")


# ─── ffmpeg grab failure paths ──────────────────────────────────────────


async def test_grab_missing_ffmpeg_returns_none(monkeypatch, tmp_path) -> None:
    async def boom(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    out = await shazam_stream.grab_to_tempfile("http://x/stream", 15)
    assert out is None


async def test_grab_nonzero_rc_returns_none(monkeypatch) -> None:
    class FakeProc:
        returncode = 1

        async def communicate(self):
            return b"", b"err: no stream"

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await shazam_stream.grab_to_tempfile("http://x/stream", 15) is None


async def test_grab_tiny_output_rejected(monkeypatch) -> None:
    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    # mkstemp creates a 0-byte file; the < 1 KB sanity check rejects it.
    assert await shazam_stream.grab_to_tempfile("http://x/stream", 15) is None


# ─── SdrTuner gates + URL shape ─────────────────────────────────────────


async def test_tuner_disabled_probe_and_tune() -> None:
    tuner = SdrTuner(enabled=False)
    assert await tuner.probe() is False
    with pytest.raises(RuntimeError, match="disabled"):
        await tuner.tune(97.5)


async def test_tuner_rejects_out_of_band_frequency() -> None:
    tuner = SdrTuner(enabled=True)
    with pytest.raises(ValueError, match="out of FM band"):
        await tuner.tune(200.0)
    with pytest.raises(ValueError):
        await tuner.tune(50.0)


def test_tuner_stream_url_shape() -> None:
    tuner = SdrTuner(
        enabled=True, http_port=6391, stream_base="http://192.168.1.10/"
    )
    # Trailing slash normalized; literal path, NO query string (ffmpeg's
    # -listen matcher rejects cache-buster params).
    assert tuner.stream_url == "http://192.168.1.10:6391/fm.mp3"
    assert "?" not in tuner.stream_url


async def test_tuner_probe_without_rtl_test_is_false(monkeypatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    tuner = SdrTuner(enabled=True)
    assert await tuner.probe() is False


async def test_tuner_stop_is_idempotent() -> None:
    tuner = SdrTuner(enabled=True)
    await tuner.stop()      # nothing running — must not raise
    assert tuner.current_frequency_mhz is None
