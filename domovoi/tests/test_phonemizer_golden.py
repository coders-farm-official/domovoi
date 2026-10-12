"""The converter against its golden fixture (domovoi/phonemizer/golden.json).

The same file is the contract for the Kotlin port, so a failure here is a
change in what the voices say: regenerate with scripts/g2p/make_golden.py
only on purpose, and read the diff.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from domovoi.phonemizer import (
    DATA_DIR,
    Phonemizer,
    PronunciationError,
    load_default_pronunciations,
    phoneme_ids,
)

FIXTURE = Path(__file__).resolve().parents[1] / "phonemizer" / "golden.json"
GOLDEN = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = GOLDEN["cases"]
DEFAULT_CASES = [c for c in CASES if "pronunciations" not in c]
LIST_CASES = [c for c in CASES if "pronunciations" in c]


@pytest.fixture(scope="module")
def ph() -> Phonemizer:
    p = Phonemizer()
    # Words in no dictionary go through the model; without ONNX Runtime they
    # would be spelled out and half the fixture would fail for that reason.
    assert p.has_model, "onnxruntime is needed for the phonemizer tests"
    return p


def test_the_fixture_covers_what_the_brief_asks():
    counts = GOLDEN["counts"]
    assert sum(counts.values()) == len(CASES) >= 300
    for group in ("sentences", "phrases", "numbers", "normalization", "names", "artists",
                  "common_words", "heteronyms", "say_it_like"):
        assert counts[group] > 0, group
    assert len(GOLDEN["invalid_say_it_like"]) >= 5
    assert all(c["ids"] and len(c["ids"]) == len(c["phonemes"]) for c in CASES)


def test_the_fixture_was_made_from_these_data_files():
    """A data change (dictionary, model, tables, default list) must come
    with a regenerated fixture, or the two silently disagree."""
    for rel, want in GOLDEN["data_sha256"].items():
        got = hashlib.sha256((Path(DATA_DIR) / rel).read_bytes()).hexdigest()
        assert got == want, f"{rel} changed since golden.json was made (scripts/g2p/make_golden.py)"


def test_default_list_in_the_fixture_is_the_shipped_one():
    assert [list(x) for x in load_default_pronunciations(DATA_DIR)] == GOLDEN["default_pronunciations"]


@pytest.mark.parametrize("case", DEFAULT_CASES, ids=[c["id"] for c in DEFAULT_CASES])
def test_phonemes_and_ids(ph, case):
    got = ph.phonemize(case["text"])
    assert got == case["phonemes"], case["text"]
    assert [phoneme_ids(p) for p in got] == case["ids"]


@pytest.mark.parametrize("case", LIST_CASES, ids=[c["id"] for c in LIST_CASES])
def test_say_it_like_cases(case):
    p = Phonemizer(pronunciations=[tuple(x) for x in case["pronunciations"]])
    resolved = {k: ("/" + v.ipa + "/" if v.ipa is not None else " ".join(v.phones))
                for k, v in p.pronunciations().items()}
    assert resolved == case["resolved"]
    got = p.phonemize(case["text"])
    assert got == case["phonemes"], case["text"]
    assert [phoneme_ids(s) for s in got] == case["ids"]


@pytest.mark.parametrize("word,say", [tuple(x) for x in GOLDEN["invalid_say_it_like"]])
def test_invalid_entries_are_refused(ph, word, say):
    with pytest.raises(PronunciationError):
        ph.resolve_pronunciation(word, say)


def test_ids_use_the_map_of_every_shipped_voice():
    """norman's ids are kristin's and ljspeech's: the three configs share
    one phoneme_id_map (data/phoneme_ids_en.json records their hashes)."""
    meta = json.loads((Path(DATA_DIR) / "phoneme_ids_en.json").read_text(encoding="utf-8"))
    voices = {k.removesuffix(".onnx.json") for k in meta["voice_configs_sha256"]}
    assert voices == {GOLDEN["ids"]["voice"], *GOLDEN["ids"]["same_for"]}
