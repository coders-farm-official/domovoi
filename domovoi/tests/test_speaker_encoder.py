"""domovoi/clients/speaker_encoder.py: voice ID's speaker encoder, 3D-Speaker's
ERes2Net on ONNX Runtime with a numpy front end (1.3.0).

The audio is fixed: 1.7 s excerpts of the repo's own voice clips
(domovoi/voice_clips, two speakers, 16 kHz), in fixtures/speaker_encoder
with ``reference.json``: the start of the Kaldi filter bank torchaudio
computes for one of them (the features 3D-Speaker trained the model on),
and the embeddings the encoder made of each on the ONNX Runtime a server
installs. ``_about`` in the file says how each was recorded.

The front-end tests need numpy only, and run everywhere. The embedding
tests need the model file, which is not in the repo (25 MB, fetched by a
server on first start): they run when ``DOMOVOI_TEST_SPEAKER_MODEL`` names
it (CI fetches it, pinned) and skip, saying so, elsewhere. They are what
would catch an ONNX Runtime upgrade that silently changes the model's
answers (as graph optimizations broke the CAM++ exports in the 2026-11
comparison): equal embeddings, and two speakers still told apart.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from domovoi.clients import speaker_encoder as se

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "speaker_encoder"
REF = json.loads((FIXTURES / "reference.json").read_text(encoding="utf-8"))

MODEL_ENV = "DOMOVOI_TEST_SPEAKER_MODEL"
MODEL = Path(os.environ[MODEL_ENV]) if os.environ.get(MODEL_ENV) else None
needs_model = pytest.mark.skipif(
    MODEL is None or not MODEL.is_file(),
    reason=f"the speaker model isn't here: set {MODEL_ENV} to the pinned {se.MODEL_FILE}",
)


def pcm(name: str) -> bytes:
    with wave.open(str(FIXTURES / name)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        return w.readframes(w.getnframes())


def wav(name: str) -> np.ndarray:
    return np.frombuffer(pcm(name), dtype=np.int16).astype(np.float32) / 32768.0


# ─── the front end (numpy only) ──────────────────────────────────────────


def test_the_filter_bank_is_torchaudios_kaldi_fbank() -> None:
    x = wav("kristin_a.wav")
    got = se.kaldi_fbank(x)
    ref = np.array(REF["fbank_kristin_a_first_frames"], dtype=np.float32)
    assert got.shape == (1 + (len(x) - 400) // 160, 80)        # whole 25 ms frames only
    assert got.dtype == np.float32
    # Log units; float64 here against torchaudio's float32.
    assert float(np.max(np.abs(got[:12] - ref))) < 5e-3


def test_the_mel_banks() -> None:
    f = se.mel_banks()
    assert f.shape == (80, 257) and (f >= 0).all()
    assert (f.sum(axis=1) > 0).all()                            # no empty band
    assert (np.diff(f.argmax(axis=1)) >= 0).all()               # bands rise in frequency
    assert (f[:, -1] == 0).all()                                # Nyquist unweighted (Kaldi)


def test_the_features_have_the_utterance_mean_taken_off() -> None:
    f = se.features(wav("norman_a.wav"))
    assert np.allclose(f.mean(axis=0), 0.0, atol=1e-4)


def test_less_than_a_frame_is_no_features() -> None:
    assert se.kaldi_fbank(np.zeros(399, dtype=np.float32)).shape == (0, 80)


def test_the_pin_is_a_sha256_of_a_github_release_asset() -> None:
    assert len(se.MODEL_SHA256) == 64 and int(se.MODEL_SHA256, 16) >= 0
    assert se.MODEL_URL.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
    assert se.MODEL_URL.endswith("/" + se.MODEL_FILE)
    assert 20_000_000 < se.MODEL_BYTES < 40_000_000
    assert se.EMBEDDING_DIM == 192 and se.MODEL_TAG == "eres2net-vox-v1"


def test_a_file_of_the_right_size_but_another_digest_is_not_the_model(tmp_path) -> None:
    fake = tmp_path / se.MODEL_FILE
    with open(fake, "wb") as f:
        f.truncate(se.MODEL_BYTES)
    assert not se.model_on_disk(fake)
    with pytest.raises(se.ModelUnavailable):
        se.SpeakerEncoder(fake)


def test_the_download_refuses_with_the_internet_off(tmp_path, monkeypatch) -> None:
    from domovoi import egress

    monkeypatch.setenv(egress.ENV_KEY, "never")
    with egress.override_policy("never"), pytest.raises(egress.InternetTurnedOff):
        se.download_model(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_the_download_moves_only_a_verified_file_into_place(tmp_path, monkeypatch) -> None:
    from domovoi import egress
    from domovoi.satellite_media import fetchers

    calls = []

    def fetch(url, dest, *, sha256, size, progress=None):
        calls.append((url, sha256, size))
        raise ValueError("checksum mismatch: fixture")

    monkeypatch.setattr(fetchers, "_fetch_pinned", fetch)
    monkeypatch.setattr(se, "_last_failure", None)
    with egress.override_policy("always"), pytest.raises(se.ModelUnavailable, match="checksum"):
        se.download_model(tmp_path)
    assert calls == [(se.MODEL_URL, se.MODEL_SHA256, se.MODEL_BYTES)]
    assert not (tmp_path / se.MODEL_FILE).exists()
    assert not se.may_retry_download()                          # not again for a while
    assert "couldn't download" in (se.model_problem() or "")


def test_the_problem_says_the_internet_is_off(tmp_path, monkeypatch) -> None:
    from domovoi import egress
    from domovoi.config import settings

    monkeypatch.setattr(settings, "voice_models_dir", str(tmp_path))
    with egress.override_policy("never"):
        assert "internet access is turned off" in (se.model_problem() or "")


# ─── the embeddings (the model file) ─────────────────────────────────────


@needs_model
@pytest.mark.parametrize("name", sorted(REF["embeddings"]))
def test_the_embedding_is_the_recorded_one(name) -> None:
    got = se.SpeakerEncoder(MODEL).embed_utterance(wav(name))
    ref = np.array(REF["embeddings"][name], dtype=np.float32)
    assert got.shape == (192,) and got.dtype == np.float32
    assert float(np.linalg.norm(got)) == pytest.approx(1.0, abs=1e-5)
    assert float(got @ ref) == pytest.approx(1.0, abs=1e-4)


@needs_model
def test_two_speakers_are_still_told_apart() -> None:
    """What an ONNX Runtime upgrade that broke the model would show first:
    one speaker's two excerpts no longer closer than two speakers'."""
    enc = se.SpeakerEncoder(MODEL)
    ka, kb, na = (enc.embed_utterance(wav(n)) for n in ("kristin_a.wav", "kristin_b.wav", "norman_a.wav"))
    assert float(ka @ kb) > 0.6
    assert float(ka @ na) < 0.2 and float(kb @ na) < 0.2


@needs_model
def test_loading_and_embedding_import_no_torch() -> None:
    code = ("import sys, numpy as np\n"
            "from pathlib import Path\n"
            "from domovoi.clients.speaker_encoder import SpeakerEncoder\n"
            f"SpeakerEncoder(Path({str(MODEL)!r})).embed_utterance(np.zeros(32000, dtype=np.float32))\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
            "('torch', 'torchaudio', 'librosa', 'resemblyzer', 'sherpa_onnx', 'scipy'))\n"
            "print(','.join(bad))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[2], timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1:] in ([], [""])


@needs_model
async def test_the_real_embedder_embeds_the_speech_of_a_recording(monkeypatch, tmp_path) -> None:
    """Through RealVoiceEmbedder (the model found in the voice-models
    directory): a satellite-like recording — a quarter second of room
    before, a second after — embeds like the clip alone."""
    import shutil

    from domovoi.clients.voice_embedder import RealVoiceEmbedder
    from domovoi.config import settings

    shutil.copyfile(MODEL, tmp_path / se.MODEL_FILE)
    monkeypatch.setattr(settings, "voice_models_dir", str(tmp_path))
    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    x = wav("norman_a.wav")
    rng = np.random.default_rng(0)
    rec = np.concatenate([np.zeros(4000), x, np.zeros(16000)]).astype(np.float32)
    rec += rng.standard_normal(rec.size).astype(np.float32) * 1e-4
    got = await RealVoiceEmbedder().embed_print((rec * 32767).astype(np.int16).tobytes())
    ref = np.array(REF["embeddings"]["norman_a.wav"], dtype=np.float32)
    assert got is not None and got.current.shape == (192,)
    assert float(got.current @ ref) > 0.9
    assert got.speech_sec < 1.75 and got.snr_db > 30


# ─── voice ID end to end (the model + the test DB) ───────────────────────


from domovoi.tests.conftest import requires_db  # noqa: E402


@needs_model
@requires_db
async def test_voice_id_end_to_end_with_the_real_encoder(db_session, monkeypatch, tmp_path) -> None:
    """Enrol two speakers from one excerpt each, through the real encoder,
    then identify a different excerpt of the first: the identifier names
    her, at the shipped threshold, and names the second from his own."""
    import shutil

    from domovoi import voice_identifier
    from domovoi.clients import voice_embedder
    from domovoi.config import settings
    from domovoi.db.repositories import PeopleRepository, VoiceProfilesRepository

    shutil.copyfile(MODEL, tmp_path / se.MODEL_FILE)
    monkeypatch.setattr(settings, "voice_models_dir", str(tmp_path))
    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    monkeypatch.setattr(voice_embedder, "_client", voice_embedder.RealVoiceEmbedder())
    people, profiles = PeopleRepository(db_session), VoiceProfilesRepository(db_session)
    ids = {}
    for who, clip in (("Kristin", "kristin_a.wav"), ("Norman", "norman_a.wav")):
        emb = await voice_identifier.embed_voice(pcm(clip))
        assert emb is not None
        ids[who] = await people.create(name=who)
        await profiles.add(person_id=ids[who], embedding=emb.astype(np.float32).tobytes(),
                           model=VoiceProfilesRepository.EMBEDDING_MODEL_TAG, room_id="kitchen",
                           sample_seconds=1.7)
    await db_session.commit()

    her = await voice_identifier.identify(pcm("kristin_b.wav"))
    assert her.person_id == ids["Kristin"]
    assert her.best_similarity > settings.voice_profile_match_threshold
    assert her.runner_up_similarity < 0.2
    him = await voice_identifier.identify(pcm("norman_a.wav"))
    assert him.person_id == ids["Norman"] and him.best_similarity > 0.999
