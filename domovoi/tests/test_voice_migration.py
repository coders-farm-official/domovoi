"""Existing installs move off the Piper voices Domovoi no longer offers
(domovoi/voice_migration.py): the configured voice, and the model files."""

from __future__ import annotations

import pytest

from domovoi import voice_migration
from domovoi.clients.tts import DEFAULT_PIPER_VOICE, RETIRED_PIPER_VOICES, SHIPPED_VOICES
from domovoi.config import settings


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    for k in [k for k in __import__("os").environ if k.upper() == "TTS_PIPER_VOICE"]:
        monkeypatch.delenv(k)


def test_the_default_is_norman():
    assert DEFAULT_PIPER_VOICE == "en_US-norman-medium"
    assert type(settings).model_fields["tts_piper_voice"].default == DEFAULT_PIPER_VOICE


def test_a_retired_voice_in_the_env_file_moves_to_the_default(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# mine\nTTS_ENGINE=piper\nTTS_PIPER_VOICE=en_US-lessac-medium\nBOT_NAME=Domovoi\n",
                   encoding="utf-8")
    monkeypatch.setattr(settings, "tts_piper_voice", "en_US-lessac-medium")
    assert voice_migration.migrate_configured_voice(env) == "en_US-lessac-medium"
    assert settings.tts_piper_voice == DEFAULT_PIPER_VOICE
    assert env.read_text(encoding="utf-8") == (
        "# mine\nTTS_ENGINE=piper\nTTS_PIPER_VOICE=en_US-norman-medium\nBOT_NAME=Domovoi\n")


@pytest.mark.parametrize("voice", [v.model_ref for v in SHIPPED_VOICES] + ["my-own-upload", "/srv/v/mine.onnx"])
def test_a_voice_still_offered_or_the_owners_own_is_kept(monkeypatch, tmp_path, voice):
    env = tmp_path / ".env"
    env.write_text(f"TTS_PIPER_VOICE={voice}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "tts_piper_voice", voice)
    assert voice_migration.migrate_configured_voice(env) is None
    assert settings.tts_piper_voice == voice
    assert env.read_text(encoding="utf-8") == f"TTS_PIPER_VOICE={voice}\n"


def test_a_retired_voice_from_the_code_default_writes_nothing(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("TTS_ENGINE=piper\n", encoding="utf-8")
    monkeypatch.setattr(settings, "tts_piper_voice", "en_US-amy-medium")
    assert voice_migration.migrate_configured_voice(env) == "en_US-amy-medium"
    assert settings.tts_piper_voice == DEFAULT_PIPER_VOICE
    assert env.read_text(encoding="utf-8") == "TTS_ENGINE=piper\n"


def test_an_environment_variable_is_not_rewritten_but_the_voice_still_moves(monkeypatch, tmp_path, caplog):
    env = tmp_path / ".env"
    env.write_text("TTS_PIPER_VOICE=en_US-ryan-high\n", encoding="utf-8")
    monkeypatch.setenv("TTS_PIPER_VOICE", "en_US-ryan-high")
    monkeypatch.setattr(settings, "tts_piper_voice", "en_US-ryan-high")
    assert voice_migration.migrate_configured_voice(env) == "en_US-ryan-high"
    assert settings.tts_piper_voice == DEFAULT_PIPER_VOICE
    assert env.read_text(encoding="utf-8") == "TTS_PIPER_VOICE=en_US-ryan-high\n"
    assert any("set in the environment" in r.getMessage() for r in caplog.records)


def test_only_the_retired_voice_files_in_the_voices_folder_go(tmp_path):
    vdir = tmp_path / "piper_voices"
    vdir.mkdir()
    keep = ["en_US-norman-medium.onnx", "en_US-norman-medium.onnx.json", "mine.onnx", "mine.onnx.json",
            "en_US-lessac-medium.onnx.bak", "notes.txt"]
    gone = ["en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json", "en_US-ryan-high.onnx"]
    for n in keep + gone:
        (vdir / n).write_bytes(b"x")
    outside = tmp_path / "en_US-amy-medium.onnx"
    outside.write_bytes(b"x")
    sub = vdir / "sub"
    sub.mkdir()
    (sub / "en_US-amy-medium.onnx").write_bytes(b"x")

    removed = voice_migration.remove_retired_voice_files(vdir)

    assert sorted(removed) == sorted(gone)
    assert sorted(p.name for p in vdir.iterdir() if p.is_file()) == sorted(keep)
    assert outside.exists() and (sub / "en_US-amy-medium.onnx").exists()
    assert voice_migration.remove_retired_voice_files(vdir) == []
    assert voice_migration.remove_retired_voice_files(tmp_path / "missing") == []


def test_every_retired_voice_was_a_catalog_voice_and_none_is_shipped():
    assert not RETIRED_PIPER_VOICES & {v.model_ref for v in SHIPPED_VOICES}
