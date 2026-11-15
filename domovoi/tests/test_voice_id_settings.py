"""Voice ID's shipped settings are the ones scripts/eval_voice_id.py chose
for the speaker model (1.3.0: 3D-Speaker's ERes2Net), and every place that
states them agrees.

The cosine scale belongs to the model: a value carried over from
Resemblyzer (0.75) would name almost nobody now. domovoi/config.py says
how each value was chosen; the eval's ``SHIPPED`` variants are what it
measures; domovoi/.env.example tells an owner the defaults. A change to one
without the others fails here. Pure: no DB, no model file.
"""

from __future__ import annotations

import re
from pathlib import Path

from domovoi.clients import speaker_encoder
from domovoi.clients.voice_embedder import EMBEDDING_DIM
from domovoi.config import Settings
from domovoi.db.repositories import VoiceDenylistRepository, VoiceProfilesRepository

REPO = Path(__file__).resolve().parents[2]

SHIPPED = {
    "voice_profile_match_threshold": 0.58,
    "voice_profile_match_margin": 0.05,
    "voice_profile_same_room_margin": 0.01,
    "voice_profile_noisy_snr_db": 10.0,
    "voice_profile_drift_near_threshold_margin": 0.05,
    "voice_profile_drift_reenroll_after": 3,
    "voice_profile_min_utterance_sec": 1.0,
    "voice_profile_encoder_threads": 2,
    "third_party_cluster_merge_threshold": 0.5,
}


def test_the_defaults_are_the_tuned_ones() -> None:
    fields = Settings.model_fields
    assert {k: fields[k].default for k in SHIPPED} == SHIPPED


def test_the_eval_measures_the_shipped_values() -> None:
    import scripts.eval_voice_id as ev

    office, rooms = ev.SHIPPED
    for v in (office, rooms):
        assert v.pipeline == "prep"
        assert v.threshold == SHIPPED["voice_profile_match_threshold"]
        assert v.margin == SHIPPED["voice_profile_match_margin"]
        assert v.noisy_snr == SHIPPED["voice_profile_noisy_snr_db"]
        assert v.confident_margin == SHIPPED["voice_profile_drift_near_threshold_margin"]
    assert rooms.same_room_margin == SHIPPED["voice_profile_same_room_margin"]


def test_what_is_embedded_is_what_the_eval_chose() -> None:
    import numpy as np

    from domovoi import voice_prep

    rng = np.random.default_rng(0)
    wav = np.concatenate([np.zeros(4000), rng.standard_normal(24000) * 0.1, np.zeros(16000)])
    wav = (wav + rng.standard_normal(wav.size) * 1e-4).astype(np.float32)
    a, b = voice_prep.for_embedding(wav), voice_prep.prepare(wav)
    assert np.array_equal(a.audio, b.audio) and a.snr_db == b.snr_db


def test_env_example_states_the_defaults() -> None:
    text = (REPO / "domovoi" / ".env.example").read_text(encoding="utf-8")
    for key, value in SHIPPED.items():
        m = re.search(rf"^# {key.upper()}=(\S+)$", text, re.M)
        if m is None:
            continue
        assert float(m.group(1)) == float(value), f"{key.upper()} in .env.example says {m.group(1)}"


def test_one_model_tag_everywhere() -> None:
    assert VoiceProfilesRepository.EMBEDDING_MODEL_TAG == speaker_encoder.MODEL_TAG
    assert VoiceDenylistRepository.EMBEDDING_MODEL_TAG == speaker_encoder.MODEL_TAG
    assert not hasattr(VoiceProfilesRepository, "LEGACY_MODEL_TAG")
    assert EMBEDDING_DIM == speaker_encoder.EMBEDDING_DIM == 192
    migration = REPO / "domovoi" / "db" / "migrations" / "V061__voice_id_eres2net.sql"
    assert f"'{speaker_encoder.MODEL_TAG}'" in migration.read_text(encoding="utf-8")
