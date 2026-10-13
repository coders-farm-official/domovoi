"""The Piper engine on ONNX Runtime + domovoi.phonemizer (no piper-tts,
no espeak-ng): what it feeds the model, what it refuses, and what it says
when a text has nothing it can read. DB-free; the ONNX session is a
stand-in (the real voices are 63 MB downloads, not in the repo)."""

from __future__ import annotations

import io
import json
import re
import wave
from pathlib import Path

import numpy as np
import pytest

from domovoi.clients import tts as tts_mod
from domovoi.phonemizer import default_id_map, phoneme_ids

REPO = Path(__file__).resolve().parents[2]


def _config(espeak_voice="en", **extra) -> dict:
    cfg = {
        "audio": {"sample_rate": 22050},
        "espeak": {"voice": espeak_voice},
        "phoneme_type": "espeak",
        "inference": {"noise_scale": 0.667, "length_scale": 1.0, "noise_w": 0.8},
        "num_speakers": 1,
        "phoneme_id_map": default_id_map(),
    }
    cfg.update(extra)
    return cfg


class _Session:
    def __init__(self):
        self.calls = []

    def run(self, _outputs, feeds):
        self.calls.append(feeds)
        n = int(feeds["input_lengths"][0])
        return [np.linspace(-0.25, 0.5, n * 10, dtype=np.float32).reshape(1, 1, 1, -1)]


@pytest.fixture(autouse=True)
def _converter_loaded_first():
    """The converter's own model session is real: load it before any test
    swaps ONNX Runtime's session class for the voice's stand-in."""
    from domovoi.phonemizer import get_phonemizer

    get_phonemizer()


@pytest.fixture
def voice(monkeypatch, tmp_path):
    import onnxruntime as ort

    session = _Session()
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: session)
    onnx = tmp_path / "en_US-norman-medium.onnx"
    onnx.write_bytes(b"model")
    Path(f"{onnx}.json").write_text(json.dumps(_config()), encoding="utf-8")
    v = _checked(tts_mod.PiperVoice(onnx), session)
    v.test_session = session
    return v


def _checked(v, session):
    """A stand-in voice after its one-time runaway check (the stand-in
    says everything quickly, so short sentences are said alone), with the
    check's calls forgotten: the tests below are about what a sentence
    feeds the model."""
    assert v.runs_away() is False
    session.calls.clear()
    return v


def test_ids_scales_and_speed_reach_the_model(voice):
    pcm = voice.synthesize_pcm(["hˈeɪ.", "wˈɪn."], 1.25)
    a, b = voice.test_session.calls
    assert a["input"].tolist() == [phoneme_ids("hˈeɪ.")]
    assert b["input"].tolist() == [phoneme_ids("wˈɪn.")]
    assert a["input_lengths"].tolist() == [len(phoneme_ids("hˈeɪ."))]
    assert a["scales"].tolist() == pytest.approx([0.667, 1.0 / 1.25, 0.8])
    assert "sid" not in a
    samples = np.frombuffer(pcm, dtype=np.int16)
    gap = int(tts_mod.SENTENCE_SILENCE_SEC * 22050)
    n_a = len(phoneme_ids("hˈeɪ.")) * 10
    # Each sentence peak-normalized to full scale, a silence between them.
    assert samples[:n_a].max() == 32767
    assert not samples[n_a:n_a + gap].any()
    assert len(samples) == n_a + gap + len(phoneme_ids("wˈɪn.")) * 10


@pytest.mark.parametrize(("cfg", "why"), [
    (_config("de"), "espeak's 'de' phonemes"),
    (_config("en-gb-x-rp"), "espeak's 'en-gb-x-rp' phonemes"),
    (_config(""), "espeak's 'unnamed' phonemes"),
    (_config(phoneme_type="text"), "'text' phonemes"),
    ({"audio": {"sample_rate": 22050}}, "no phoneme map"),
])
def test_a_voice_on_another_phoneme_set_is_refused(monkeypatch, tmp_path, cfg, why):
    import onnxruntime as ort

    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: pytest.fail("no session for a refused voice"))
    onnx = tmp_path / "v.onnx"
    onnx.write_bytes(b"model")
    Path(f"{onnx}.json").write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(tts_mod.UnsupportedPiperVoice, match=re.escape(why)):
        tts_mod.PiperVoice(onnx)
    # The same words for the owner, without loading the model.
    assert why in tts_mod.piper_config_problem(cfg)


@pytest.mark.parametrize("espeak_voice,dialect", [("en", "en"), ("en-us", "en-us"), ("en_US", "en-us"), ("EN", "en")])
def test_both_english_sets_speak(monkeypatch, tmp_path, espeak_voice, dialect):
    """A voice trained on espeak "en-us" (what most people train with:
    the owner's uploads say so) is spoken in that set, not refused."""
    import onnxruntime as ort

    session = _Session()
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: session)
    onnx = tmp_path / "mine.onnx"
    onnx.write_bytes(b"model")
    Path(f"{onnx}.json").write_text(json.dumps(_config(espeak_voice)), encoding="utf-8")
    v = _checked(tts_mod.PiperVoice(onnx), session)
    assert v.dialect == dialect and tts_mod.piper_config_problem(_config(espeak_voice)) is None
    monkeypatch.setattr(tts_mod, "_piper_voice", lambda ref: v)
    assert _frames(tts_mod._synth_piper_sync("Better water, Domovoi.", "mine", 1.0)) > 0
    from domovoi.phonemizer import get_phonemizer

    want = get_phonemizer().phonemize("Better water, Domovoi.", dialect=dialect)
    assert [c["input"].tolist()[0] for c in session.calls] == [phoneme_ids(s) for s in want]
    if dialect == "en-us":
        assert want == ["bˈɛɾɚ wˈɔːɾɚ, dˈoʊmoʊvˌɔɪ."]


def test_a_symbol_the_voice_lacks_is_said_as_the_nearest_one(monkeypatch, tmp_path, caplog):
    """A voice trained on fewer symbols: the flap and the reduced vowel
    become t and ɪ (logged once), never a crash or silence."""
    import onnxruntime as ort

    session = _Session()
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: session)
    small = {k: v for k, v in default_id_map().items() if k not in ("ɾ", "ᵻ")}
    onnx = tmp_path / "small.onnx"
    onnx.write_bytes(b"model")
    Path(f"{onnx}.json").write_text(json.dumps(_config("en-us", phoneme_id_map=small)), encoding="utf-8")
    v = _checked(tts_mod.PiperVoice(onnx), session)
    assert v.synthesize_pcm(["bˈɛɾɚ ɹᵻmˈaɪnd.", "ɹᵻpˈiːt."], 1.0)
    fed = [c["input"].tolist()[0] for c in session.calls]
    assert fed == [phoneme_ids("bˈɛtɚ ɹɪmˈaɪnd.", small), phoneme_ids("ɹɪpˈiːt.", small)]
    warned = [r.getMessage() for r in caplog.records if "phoneme map" in r.getMessage()]
    assert len(warned) == 1 and "'ɾ'" in warned[0] and "'ᵻ'" in warned[0]


def test_a_voice_that_knows_none_of_the_sounds_falls_through(monkeypatch, tmp_path):
    """Nothing in the text is a sound the voice's map has: no audio (so the
    router moves on to the system voice) rather than a run on silence."""
    import onnxruntime as ort

    session = _Session()
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: session)
    tiny = {k: default_id_map()[k] for k in ("^", "$", "_", " ", ".")}
    onnx = tmp_path / "tiny.onnx"
    onnx.write_bytes(b"model")
    Path(f"{onnx}.json").write_text(json.dumps(_config("en-us", phoneme_id_map=tiny)), encoding="utf-8")
    v = tts_mod.PiperVoice(onnx)
    monkeypatch.setattr(tts_mod, "_piper_voice", lambda ref: v)
    assert tts_mod._synth_piper_sync("Hello there.", "tiny", 1.0) is None
    assert session.calls == []


def test_a_multi_speaker_voice_gets_speaker_zero(monkeypatch, tmp_path):
    import onnxruntime as ort

    session = _Session()
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: session)
    onnx = tmp_path / "v.onnx"
    onnx.write_bytes(b"model")
    Path(f"{onnx}.json").write_text(json.dumps(_config(num_speakers=4)), encoding="utf-8")
    tts_mod.PiperVoice(onnx).synthesize_pcm(["hˈaɪ"], 1.0)
    assert session.calls[0]["sid"].tolist() == [0]


@pytest.fixture
def engine(monkeypatch, voice):
    monkeypatch.setattr(tts_mod, "_piper_voice", lambda ref: voice)
    return voice


def _frames(wav: bytes) -> int:
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getframerate() == 22050 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.getnframes()


def test_text_goes_through_the_converter(engine):
    wav = tts_mod._synth_piper_sync("Hey Domovoi! It's 72°F.", "en_US-norman-medium", 1.0)
    assert _frames(wav) > 0
    fed = [c["input"].tolist()[0] for c in engine.test_session.calls]
    assert fed == [phoneme_ids("hˈeɪ dˈəʊməʊvˌɔɪ!"),
                   phoneme_ids("ɪts sˈɛvənti tˈuː dɪɡɹˈiːz fˈeəɹənhˌaɪt.")]


def test_text_with_nothing_readable_says_the_fallback_line(engine, caplog):
    wav = tts_mod._synth_piper_sync("Привет, как дела?", "en_US-norman-medium", 1.0)
    assert _frames(wav) > 0
    from domovoi.phonemizer import get_phonemizer

    expected = [phoneme_ids(s) for s in get_phonemizer().phonemize(tts_mod.UNREADABLE_TEXT_FALLBACK)]
    assert [c["input"].tolist()[0] for c in engine.test_session.calls] == expected
    assert any("no pronounceable words" in r.getMessage() for r in caplog.records)


def test_mixed_text_skips_only_what_it_cannot_read(engine):
    tts_mod._synth_piper_sync("The word is Привет.", "en_US-norman-medium", 1.0)
    fed = [c["input"].tolist()[0] for c in engine.test_session.calls]
    assert len(fed) == 1 and fed[0] == phoneme_ids("ðə wˈɜːd ˈɪz.")


@pytest.mark.parametrize("text", ["", "...", "?!", "🙂"])
def test_nothing_to_say_is_a_failure_not_silence(engine, text):
    assert tts_mod._synth_piper_sync(text, "en_US-norman-medium", 1.0) is None
    assert engine.test_session.calls == []


def test_the_router_falls_through_when_a_voice_is_refused(monkeypatch, tmp_path):
    import onnxruntime as ort

    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: _Session())
    vdir = tmp_path / "voices"
    vdir.mkdir()
    (vdir / "german-voice.onnx").write_bytes(b"m")
    (vdir / "german-voice.onnx.json").write_text(json.dumps(_config("de")), encoding="utf-8")
    monkeypatch.setattr(tts_mod.settings, "voice_models_dir", str(vdir))
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    other = tts_mod._pcm_to_wav_bytes(b"\x01\x02" * 50, 16_000)
    monkeypatch.setattr(tts_mod, "_synth_system_sync", lambda text: other)
    client = tts_mod.RealTTSClient(preferred_engine="piper", piper_voice="german-voice", speed=1.0)
    result = client._synth_detailed_blocking("hello")
    assert result.engine == "system"
    # ...and the UI can say why without loading the model.
    assert "espeak's 'de' phonemes" in tts_mod.piper_voice_problem("german-voice")
    assert tts_mod.piper_voice_problem("not-on-disk") is None


def test_no_code_imports_piper_tts_or_espeak():
    """piper-tts (GPL-3.0, espeak-ng inside) is gone; nothing may come back
    to it by accident."""
    pat = re.compile(r"^\s*(from\s+piper(\.|\s)|import\s+piper\b|import\s+piper_phonemize|from\s+piper_phonemize)", re.M)
    hits = []
    for top in ("domovoi", "web", "satellite", "plugins", "scripts"):
        for p in (REPO / top).rglob("*.py"):
            if "tests" in p.parts:
                continue
            if pat.search(p.read_text(encoding="utf-8", errors="replace")):
                hits.append(str(p.relative_to(REPO)))
    assert hits == []


def test_pins_are_the_three_public_domain_voices():
    assert set(tts_mod.PIPER_PINS) == {
        "en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium"}
    for v in tts_mod.SHIPPED_VOICES:
        sha, size, blob, jsize = tts_mod.PIPER_PINS[v.model_ref]
        assert re.fullmatch(r"[0-9a-f]{64}", sha) and size == 63531379
        assert re.fullmatch(r"[0-9a-f]{40}", blob) and 4000 < jsize < 6000
    assert tts_mod.PIPER_PINNED_REVISION == "c10ece1aade47bb51c153c893d14e5bf8e5b7117"


@pytest.mark.parametrize("configured,first", [
    ("", None),                                          # no configured voice: just the three
    ("/srv/my voices/mine.onnx", "/srv/my voices/mine.onnx"),   # a path with a space, unpinned
    ("en_US-kristin-medium", "en_US-kristin-medium"),    # a shipped one: listed once
])
def test_the_provisioning_reads_one_value_per_line(monkeypatch, capsys, configured, first):
    """scripts/linux/provision.sh's piper-info snippet, run for real: the
    folder, then three lines a voice (name, SHA-256 or "-", size)."""
    import contextlib
    import io as _io

    text = (REPO / "scripts/linux/provision.sh").read_text(encoding="utf-8")
    snippet = re.search(r"PY_PIPER_INFO='(#provision:piper-info\n.*?)'\n", text, re.S).group(1)
    monkeypatch.setattr(tts_mod.settings, "tts_piper_voice", configured)
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(snippet, "piper-info", "exec"), {})
    lines = buf.getvalue().splitlines()
    assert lines[0] == tts_mod.settings.voice_models_dir
    triples = [lines[i:i + 3] for i in range(1, len(lines), 3)]
    names = [t[0] for t in triples]
    expected = ([first] if first and first not in tts_mod.PIPER_PINS else []) + [
        "en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium"]
    if first in tts_mod.PIPER_PINS:
        expected = [first] + [n for n in expected if n != first]
    assert names == expected
    for name, sha, size in triples:
        pin = tts_mod.PIPER_PINS.get(name)
        assert (sha, size) == ((pin[0], str(pin[1])) if pin else ("-", "0"))
