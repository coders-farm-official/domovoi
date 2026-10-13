"""A "play a sample" button plays the voice it names, or is not shown
(owner, 2026-10-12): live synthesis when the voice is on this server, the
shipped voice's built-in clip (domovoi/voice_clips/) before it is
downloaded, nothing for a voice that can't speak here.

DB-free: the voices folder is a temporary one, the web routes run over
``install_fake_db`` (the clip paths never reach Postgres or the core).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

import pytest

from domovoi.clients import tts
from domovoi.phonemizer import default_id_map
from domovoi.tests.auth_testkit import HEADER, install_fake_db, web_client
from domovoi.voice_sample import CLIP_DIR, clip_line, piper_sample_source, shipped_clip

REPO = Path(__file__).resolve().parents[2]
SHIPPED = [v.model_ref for v in tts.SHIPPED_VOICES]


def _voice(folder: Path, name: str, espeak_voice: str = "en") -> None:
    (folder / f"{name}.onnx").write_bytes(b"model")
    (folder / f"{name}.onnx.json").write_text(json.dumps({
        "audio": {"sample_rate": 22050}, "espeak": {"voice": espeak_voice}, "phoneme_type": "espeak",
        "phoneme_id_map": default_id_map()}), encoding="utf-8")


@pytest.fixture
def voices(monkeypatch, tmp_path) -> Path:
    """An empty voices folder (never the real ~/.domovoi one)."""
    folder = tmp_path / "piper_voices"
    folder.mkdir()
    monkeypatch.setattr(tts.settings, "voice_models_dir", str(folder))
    monkeypatch.setattr(tts, "_piper_voice_cache", {})
    return folder


# ── the clips ─────────────────────────────────────────────────────────────


def test_every_shipped_voice_has_a_small_clip_with_its_hash():
    lines = [ln for ln in (CLIP_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
             if ln and not ln.startswith("#")]
    sums = {name: h for h, name in (ln.split(None, 1) for ln in lines)}
    assert set(sums) == {f"{ref}.ogg" for ref in SHIPPED}
    assert {p.name for p in CLIP_DIR.iterdir()} == set(sums) | {"SHA256SUMS"}
    for ref in SHIPPED:
        data = shipped_clip(ref).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sums[f"{ref}.ogg"]
        assert 4_000 < len(data) < 40_000, (ref, len(data))     # a few seconds, well under 100 KB
        assert data[:4] == b"OggS" and b"OpusHead" in data[:64], ref
        name = next(v.name for v in tts.SHIPPED_VOICES if v.model_ref == ref)
        assert clip_line(name).encode() in data                 # the title tag says what it says


def test_the_clips_are_made_by_the_committed_script():
    script = (REPO / "scripts/voice_clips/make_clips.py").read_text(encoding="utf-8")
    assert "tts.PiperVoice" in script and "set_seed" in script and "opusenc" in script
    head = (CLIP_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines()[0]
    assert "make_clips.py" in head and "seed" in head
    assert shipped_clip("not-a-voice") is None


# ── what a button can play ────────────────────────────────────────────────


def test_a_shipped_voice_plays_its_clip_until_it_is_downloaded(voices):
    assert [piper_sample_source(r) for r in SHIPPED] == ["clip"] * 3
    _voice(voices, "en_US-kristin-medium")
    assert piper_sample_source("en_US-kristin-medium") == "voice"
    assert piper_sample_source("en_US-norman-medium") == "clip"


def test_an_uploaded_voice_plays_only_when_it_can_speak(voices):
    _voice(voices, "kronk", "en-us")
    _voice(voices, "heidi", "de")
    assert piper_sample_source("kronk") == "voice"
    assert piper_sample_source("heidi") is None             # the system voice would answer
    assert piper_sample_source("gone") is None              # not on disk, no clip


def test_a_kept_catalogue_voice_downloads_on_its_first_sample(voices):
    """Kamron's Cori (en_GB-cori-high, kept from the old catalogue): not on
    a fresh box until something speaks in it, and its first sample fetches
    it as its first reply would. Not under "never" (nothing can fetch it),
    nor a catalogue voice in another language (it could not speak here)."""
    from domovoi import egress

    with egress.override_policy("always"):
        assert piper_sample_source("en_GB-cori-high") == "voice"
        assert piper_sample_source("de_DE-thorsten-medium") is None
        assert piper_sample_source("en_US-lessac-medium") is None   # retired
    with egress.override_policy("never"):
        assert piper_sample_source("en_GB-cori-high") is None


# ── the web routes ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_page_learns_what_each_button_plays(monkeypatch, voices):
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-tok")
    _voice(voices, "en_US-ljspeech-medium")
    async with web_client() as c:
        assert (await c.get("/api/voice/samples")).status_code == 401
        r = await c.get("/api/voice/samples", headers={HEADER: "dev-tok"})
    assert r.status_code == 200, r.text
    assert r.json() == {"samples": {"en_US-norman-medium": "clip", "en_US-kristin-medium": "clip",
                                    "en_US-ljspeech-medium": "voice"}}


@pytest.mark.asyncio
async def test_a_voice_not_downloaded_plays_its_clip_not_another_voice(monkeypatch, voices):
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-tok")
    from web.backend.api import voice as voice_api

    async def no_core(*a, **k):
        raise AssertionError("a clip needs no synthesis")

    monkeypatch.setattr(voice_api, "post_admin_bytes", no_core)
    async with web_client() as c:
        r = await c.get("/api/voice/sample/en_US-norman-medium", headers={HEADER: "dev-tok"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/ogg"
    assert r.headers["x-sample-source"] == "clip"
    assert unquote(r.headers["x-sample-text"]) == "Hi, I'm Norman. This is how Domovoi sounds."
    assert r.content == shipped_clip("en_US-norman-medium").read_bytes()


def test_the_registry_rows_say_what_plays_and_why_not(voices):
    from web.backend.api import voices as voices_api

    _voice(voices, "kronk", "en-us")
    _voice(voices, "heidi", "de")
    rows = [
        {"id": 1, "name": "Norman", "engine": "piper", "model_ref": "en_US-norman-medium", "is_default": True},
        {"id": 2, "name": "Kronk", "engine": "piper", "model_ref": "kronk", "is_default": False},
        {"id": 3, "name": "Heidi", "engine": "piper", "model_ref": "heidi", "is_default": False},
        {"id": 4, "name": "Aria", "engine": "edge", "model_ref": "en-US-AriaNeural", "is_default": False},
    ]
    got = [(m.sample, m.problem) for m in map(voices_api._to_model, rows)]
    assert got[0] == ("clip", None)
    assert got[1] == ("voice", None)                         # an en-us upload speaks now
    assert got[2][0] is None and "espeak's 'de' phonemes" in got[2][1]
    assert got[3] == ("voice", None)


def test_the_settings_page_hides_a_button_with_nothing_to_play():
    jsx = (REPO / "web/static/settings.jsx").read_text(encoding="utf-8")
    assert "canPlayType('audio/ogg; codecs=opus')" in jsx
    assert "voiceSamplePlayable(sampleInfo?.samples?.[v.id])" in jsx
    assert "voiceSamplePlayable(v.sample)" in jsx and "Can’t speak here: {v.problem}" in jsx
