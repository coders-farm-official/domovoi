"""Wake phrases with words the CMU dictionary lacks ("domovoi", "domo").

openWakeWord's train.py builds adversarial negatives from each phrase
word's CMU pronunciation and, for a word without one, downloads a
DeepPhonemizer checkpoint from an S3 bucket that now answers 403 (and
imports a ``dp`` package the image doesn't have), so "hey domovoi" failed
the whole training run in the --generate_clips stage. The trainer image now
runs train.py through ``scripts/wake_word/docker/train_runner.py``, which
gives such words an espeak-ng pronunciation first. These tests hold the
pure parts (IPA -> ARPAbet, which words need it) and that the entrypoint
really goes through the runner. No Docker, espeak-ng or openWakeWord needed:
the IPA strings below are what espeak-ng 1.51 (the image's) prints for
``espeak-ng -q --ipa -v en-us <word>``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parents[2] / "scripts" / "wake_word" / "docker"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_wake_{name}", DOCKER_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load("train_runner")


@pytest.mark.parametrize(
    ("ipa", "arpabet"),
    [
        # Out of the dictionary: the words this fix is for.
        ("dˈɑːmuːvˌɔɪ", "D AA1 M UW0 V OY2"),  # domovoi
        ("dˈoʊmoʊ", "D OW1 M OW0"),  # domo
        # In the dictionary: the mapping agrees with CMU's own entry.
        ("həlˈoʊ", "HH AH0 L OW1"),  # hello
        ("hˈeɪ", "HH EY1"),  # hey
        ("dʒˈɑːɹvɪs", "JH AA1 R V IH0 S"),  # jarvis
        ("kəmpjˈuːɾɚ", "K AH0 M P Y UW1 T ER0"),  # computer
        ("θˈæŋks", "TH AE1 NG K S"),  # thanks
        ("tʃˈɜːtʃ", "CH ER1 CH"),  # church
        ("bˈoːɹʃt", "B AO1 R SH T"),  # borscht
    ],
)
def test_ipa_to_arpabet(ipa, arpabet):
    assert runner.ipa_to_arpabet(ipa) == arpabet


def test_unknown_symbols_are_skipped_not_fatal():
    assert runner.ipa_to_arpabet("(en)dˈɑː-m") == "D AA1 M"
    assert runner.ipa_to_arpabet("") == ""


def test_missing_words_are_the_ones_without_a_pronunciation():
    known = {"hey": ["HH EY1"], "hello": ["HH AH0 L OW1"]}

    def phones_for_word(word):
        return known.get(word.lower(), [])

    assert runner.missing_words("hey domovoi", phones_for_word) == ["domovoi"]
    assert runner.missing_words("Hello Domo domo", phones_for_word) == ["domo"]
    assert runner.missing_words("hey hello", phones_for_word) == []


def test_every_training_stage_runs_through_the_runner(monkeypatch, tmp_path):
    entrypoint = _load("entrypoint")
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(entrypoint.subprocess, "run", fake_run)
    cfg = tmp_path / "train_config.yaml"
    for flag in ("--generate_clips", "--augment_clips", "--train_model"):
        entrypoint.run_stage(cfg, flag, "cpu")

    assert len(seen) == 3
    for argv, flag in zip(seen, ("--generate_clips", "--augment_clips", "--train_model")):
        assert argv[0] == sys.executable
        assert argv[1] == str(entrypoint.TRAIN_RUNNER)
        assert argv[2] == str(entrypoint.TRAIN_PY)
        assert argv[-1] == flag


def test_the_image_copies_the_runner_where_the_entrypoint_looks():
    dockerfile = (DOCKER_DIR / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = _load("entrypoint")
    assert f"COPY train_runner.py {entrypoint.TRAIN_RUNNER.as_posix()}" in dockerfile


# ─── the host's pronunciations (domovoi/phonemizer, MIT) come first ───────

def test_host_pronunciations_parse_and_refuse_anything_else():
    got = runner.parse_pronunciations("domovoi=D OW1 M OW2 V OY2;domo=D OW1 M OW2")
    assert got == {"domovoi": "D OW1 M OW2 V OY2", "domo": "D OW1 M OW2"}
    assert runner.parse_pronunciations("x=$(reboot);y=D AA1 Q;=HH EY1;z") == {}
    assert runner.parse_pronunciations("") == {}


class _FakePronouncing:
    """The bits of ``pronouncing`` add_pronunciations touches."""

    def __init__(self, known):
        from collections import defaultdict

        self.lookup = defaultdict(list, {k: list(v) for k, v in known.items()})
        self.pronunciations = []

    def init_cmu(self):
        pass

    def phones_for_word(self, word):
        return self.lookup.get(word.lower(), [])


def test_the_hosts_pronunciation_wins_and_espeak_covers_the_rest(monkeypatch):
    fake = _FakePronouncing({"hey": ["HH EY1"], "hello": ["HH AH0 L OW1"]})
    monkeypatch.setitem(sys.modules, "pronouncing", fake)
    monkeypatch.setattr(runner, "espeak_ipa", lambda word: "dˈoʊmoʊ")
    added = runner.add_pronunciations(
        "hey domovoi domo hello",
        {"domovoi": "D OW1 M OW2 V OY2", "hello": "HH EH0 L OW1"},
    )
    assert added["domovoi"] == ("D OW1 M OW2 V OY2", "Domovoi's phonemizer")
    assert added["domo"] == ("D OW1 M OW0", "espeak-ng")
    # A listed word goes first even over the dictionary's own entry...
    assert fake.phones_for_word("hello")[0] == "HH EH0 L OW1"
    # ...and a dictionary word nobody sent is left alone.
    assert "hey" not in added and fake.phones_for_word("hey") == ["HH EY1"]


def test_warm_downloads_and_trains_nothing(monkeypatch, tmp_path, capsys):
    entrypoint = _load("entrypoint")
    done = []
    monkeypatch.setattr(entrypoint, "CACHE", tmp_path)
    for name in ("ensure_dataset", "ensure_rirs", "ensure_background"):
        monkeypatch.setattr(entrypoint, name, lambda n=name: done.append(n))
    monkeypatch.setattr(entrypoint, "run_stage", lambda *a, **k: done.append("TRAINED"))
    monkeypatch.setattr(entrypoint, "lower_priority", lambda: None)
    monkeypatch.setattr(sys, "argv", ["entrypoint.py", "--warm"])
    monkeypatch.delenv("WAKE_SLUG", raising=False)
    assert entrypoint.main() == 0
    assert done == ["ensure_dataset", "ensure_rirs", "ensure_background"]
    assert "warm: 0 bytes in the cache" in capsys.readouterr().out
