"""domovoi/clients/speaker_encoder.py: Resemblyzer's speaker encoder
without librosa (2026-10-15), held to Resemblyzer's own answers.

The audio is fixed: 1.7 s excerpts of the repo's own voice clips
(domovoi/voice_clips, two speakers, 16 kHz), in fixtures/speaker_encoder
with ``reference.json``: the embeddings and the start of a mel spectrogram
that Resemblyzer 0.1.4 computed for them with librosa, recorded once.
Equal embeddings mean every voice already enrolled keeps matching.

The mel tests need numpy only. The embedding tests need torch and
Resemblyzer's weights (the ``voice-profile`` extra and ``pip install
--no-deps resemblyzer``); they skip, saying so, where those are not
installed, as on the suite's own lock.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from domovoi.clients import speaker_encoder as se

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "speaker_encoder"
REF = json.loads((FIXTURES / "reference.json").read_text(encoding="utf-8"))

HAVE_ENCODER = importlib.util.find_spec("torch") is not None and importlib.util.find_spec("resemblyzer") is not None
needs_encoder = pytest.mark.skipif(not HAVE_ENCODER, reason="torch and Resemblyzer's weights (voice-profile) not installed")


def pcm(name: str) -> bytes:
    with wave.open(str(FIXTURES / name)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        return w.readframes(w.getnframes())


def wav(name: str) -> np.ndarray:
    return np.frombuffer(pcm(name), dtype=np.int16).astype(np.float32) / 32768.0


# ─── the mel spectrogram (numpy only) ────────────────────────────────────


def test_the_mel_spectrogram_is_librosas() -> None:
    got = se.mel_spectrogram(wav("kristin_a.wav"))
    ref = np.array(REF["mel_kristin_a_first_frames"], dtype=np.float32)
    assert got.shape == (1 + len(wav("kristin_a.wav")) // se.HOP, se.N_MELS)
    assert got.dtype == np.float32
    assert float(np.max(np.abs(got[:12] - ref)) / np.max(np.abs(ref))) < 1e-5


def test_the_filter_bank() -> None:
    f = se.mel_filters()
    assert f.shape == (40, 201) and (f >= 0).all()
    assert (f.sum(axis=1) > 0).all()                                  # no empty band
    peaks = f.argmax(axis=1)
    assert (np.diff(peaks) >= 0).all()                                # bands rise in frequency


@pytest.mark.parametrize("seconds,expected", [(0.5, 1), (1.6, 1), (1.7, 1), (2.5, 2), (3.4, 3)])
def test_partial_slices(seconds, expected) -> None:
    wavs, mels = se.partial_slices(int(seconds * 16000))
    assert len(wavs) == len(mels) == expected
    assert all(m.stop - m.start == se.PARTIAL_FRAMES for m in mels)
    assert all(w.start == m.start * 160 and w.stop == m.stop * 160 for w, m in zip(wavs, mels))


def test_the_weights_are_found_without_importing_resemblyzer(monkeypatch, tmp_path) -> None:
    pkg = tmp_path / "resemblyzer"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise RuntimeError('must not be imported')\n")
    (pkg / "pretrained.pt").write_bytes(b"weights")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "resemblyzer", raising=False)
    assert se.weights_path() == pkg / "pretrained.pt"
    (pkg / "pretrained.pt").unlink()
    with pytest.raises(FileNotFoundError, match="--no-deps resemblyzer"):
        se.weights_path()


# ─── the embeddings (torch + weights) ────────────────────────────────────


@needs_encoder
@pytest.mark.parametrize("name", sorted(REF["embeddings"]))
def test_the_embedding_is_resemblyzers(name) -> None:
    got = se.SpeakerEncoder(device="cpu").embed_utterance(wav(name))
    ref = np.array(REF["embeddings"][name], dtype=np.float32)
    assert got.shape == (256,) and got.dtype == np.float32
    cos = float(got @ ref / np.linalg.norm(got) / np.linalg.norm(ref))
    assert cos == pytest.approx(1.0, abs=1e-6)
    assert float(np.max(np.abs(got - ref))) < 1e-5


@needs_encoder
def test_loading_and_embedding_import_neither_librosa_nor_resemblyzer() -> None:
    code = ("import sys, numpy as np\n"
            "from domovoi.clients.speaker_encoder import SpeakerEncoder\n"
            "SpeakerEncoder(device='cpu').embed_utterance(np.zeros(32000, dtype=np.float32))\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
            "('librosa', 'soxr', 'soundfile', 'resemblyzer', 'webrtcvad', 'numba', 'scipy'))\n"
            "print(','.join(bad))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[2], timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1:] in ([], [""])


@needs_encoder
async def test_the_real_embedder_gives_the_reference_embedding(monkeypatch) -> None:
    from domovoi.clients.voice_embedder import RealVoiceEmbedder
    from domovoi.config import settings

    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    got = await RealVoiceEmbedder().embed(pcm("norman_a.wav"))
    assert float(got @ np.array(REF["embeddings"]["norman_a.wav"], dtype=np.float32)) == pytest.approx(1.0, abs=1e-6)


# ─── voice ID end to end (torch + weights + the test DB) ─────────────────


from domovoi.tests.conftest import requires_db  # noqa: E402


@needs_encoder
@requires_db
async def test_voice_id_end_to_end_with_the_real_encoder(db_session, monkeypatch) -> None:
    """Enrol two speakers from one excerpt each, through the real encoder,
    then identify a different excerpt of the first: the identifier names
    her, and names the second from his own excerpt."""
    from domovoi import voice_identifier
    from domovoi.clients import voice_embedder
    from domovoi.config import settings
    from domovoi.db.repositories import PeopleRepository, VoiceProfilesRepository

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
    assert her.best_similarity == pytest.approx(0.7593, abs=1e-3)       # Resemblyzer's own score
    him = await voice_identifier.identify(pcm("norman_a.wav"))
    assert him.person_id == ids["Norman"] and him.best_similarity > 0.999
