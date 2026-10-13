"""The "say it like" list end to end on the core side: the effective list
(defaults + the owner's rows), loading it into the converter, the core's
reload and preview routes, and clips re-rendering when an entry they use
changes (domovoi/voice_settings.py, domovoi/main.py, domovoi/canned_sounds.py)."""

from __future__ import annotations

import io
import wave
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from domovoi import voice_settings
from domovoi.phonemizer import Phonemizer
from domovoi.tests.auth_testkit import _db, core_client  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _row(key, word, say, when=None):
    return {"key": key, "word": word, "say_like": say, "updated_at": when}


def test_the_effective_list_overlays_the_owners_rows_on_the_defaults():
    rows = [
        _row("nguyen", "Nguyen", None),                     # a default removed
        _row("domo", "Domo", "DOH-mo"),                     # a default changed
        _row("siobhan", "Siobhan", "shiv-AWN"),             # a word of the owner's
        _row("bob", "Bob", None),                           # removed, never listed
    ]
    assert voice_settings.effective_pronunciations(rows) == [
        ("Domovoi", "DOH-moe-voy"), ("Domo", "DOH-mo"), ("Siobhan", "shiv-AWN")]
    assert voice_settings.effective_pronunciations([]) == voice_settings.default_pronunciations()


def test_updated_at_is_the_newest_change_or_the_defaults_date():
    assert voice_settings.updated_at([]) == voice_settings.DEFAULTS_UPDATED_AT
    assert voice_settings.updated_at([_row("a", "a", "b", T0)]) == T0


def test_the_voices_listed_for_the_phone():
    ids = [v["id"] for v in voice_settings.shipped_voices()]
    assert ids == ["en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium"]
    assert {v["sex"] for v in voice_settings.shipped_voices()} == {"male", "female"}


@pytest.fixture
def fresh_converter(monkeypatch):
    ph = Phonemizer(pronunciations=[])
    monkeypatch.setattr("domovoi.phonemizer.get_phonemizer", lambda: ph)
    return ph


@requires_db
@pytest.mark.asyncio
async def test_apply_loads_the_list_and_leaves_out_what_it_cannot_use(db_session, fresh_converter):
    await db_session.execute(text(
        "INSERT INTO pronunciations (key, word, say_like) VALUES "
        "('siobhan', 'Siobhan', 'shiv-AWN'), ('bob', 'Bob', 'shh'), ('nguyen', 'Nguyen', NULL)"))
    await db_session.commit()
    errors = await voice_settings.apply_pronunciations()
    assert errors == ["Bob: 'shh' has no vowel sound"]
    assert set(fresh_converter.pronunciations()) == {"domovoi", "domo", "siobhan"}
    assert fresh_converter.phonemize("Nguyen")[0] != "wˈɪn"


@requires_db
@pytest.mark.asyncio
async def test_the_core_reload_route(_db, monkeypatch):  # noqa: F811
    from domovoi import main

    applied, rendered = [], []

    async def apply():
        applied.append(1)
        return ["Bob: no"]

    async def regen():
        rendered.append(1)

    monkeypatch.setattr(voice_settings, "apply_pronunciations", apply)
    monkeypatch.setattr(main, "_run_sounds_regenerate", regen)
    async with core_client() as c:
        r = await c.post("/v1/admin/pronunciations/reload")
    assert r.status_code == 200, r.text
    assert r.json() == {"reloaded": True, "left_out": ["Bob: no"]}
    assert applied == [1]
    await main.app.state.sounds_render_task
    assert rendered == [1]


@requires_db
@pytest.mark.asyncio
async def test_the_core_preview_route(_db, monkeypatch, fresh_converter):  # noqa: F811
    from domovoi import main
    from domovoi.clients import tts

    said = []

    def fake_wav(phonemes, voice):
        said.append((phonemes, voice))
        return tts._pcm_to_wav_bytes(b"\x01\x02" * 100, 22050)

    monkeypatch.setattr(main, "_say_phonemes_wav", fake_wav)
    async with core_client() as c:
        r = await c.post("/v1/admin/voice/say-like",
                         json={"word": "Domovoi", "say_like": "DOM-uh-voy", "voice": "en_US-kristin-medium"})
        assert r.status_code == 200, r.text
        assert said[-1] == (["dˈɒməvˌɔɪ."], "en_US-kristin-medium")
        assert r.headers["x-phonemes"] == "d%CB%88%C9%92m%C9%99v%CB%8C%C9%94%C9%AA."
        with wave.open(io.BytesIO(r.content)) as w:
            assert w.getnframes() == 100
        # Not saved: the converter still says it the shipped way... here, its own way.
        assert "domovoi" not in fresh_converter.pronunciations()
        r = await c.post("/v1/admin/voice/say-like", json={"word": "Bob", "say_like": "shh"})
        assert r.status_code == 400 and "no vowel" in r.json()["detail"]
        r = await c.post("/v1/admin/voice/say-like", json={"word": "Bob", "say_like": "bob", "voice": "en_US-ryan-high"})
        assert r.status_code == 400


def test_clips_rerender_only_when_an_entry_they_use_changes(monkeypatch, fresh_converter):
    from domovoi import canned_sounds as cs
    from domovoi.config import settings

    monkeypatch.setattr(settings, "use_stubs", False)
    fresh_converter.set_pronunciations([("Domovoi", "DOH-moe-voy")])
    hey = cs._spoken_key("Hey, I'm Domovoi.", "piper")
    plain = cs._spoken_key("I'm listening.", "piper")
    assert plain == "I'm listening."                      # no entry used: the hash is unchanged
    assert hey != "Hey, I'm Domovoi." and "domovoi=DOH-moe-voy" in hey
    assert cs._spoken_key("Hey, I'm Domovoi.", "system") == "Hey, I'm Domovoi."   # the OS voice ignores it
    fresh_converter.set_pronunciations([("Domovoi", "DOM-uh-voy")])
    assert cs._spoken_key("Hey, I'm Domovoi.", "piper") != hey
    assert cs._spoken_key("I'm listening.", "piper") == plain
    monkeypatch.setattr(settings, "use_stubs", True)
    assert cs._spoken_key("Hey, I'm Domovoi.", "piper") == "Hey, I'm Domovoi."
