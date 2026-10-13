#!/usr/bin/env python3
"""Run openWakeWord's train.py after giving every word of the wake phrase a
pronunciation.

Why: train.py's --generate_clips stage builds adversarial negatives with
``openwakeword.data.generate_adversarial_texts``, which looks each phrase
word up in the CMU dictionary (``pronouncing``). For a word the dictionary
lacks ("domovoi", "domo", most names) it downloads a DeepPhonemizer
checkpoint from a public S3 bucket and imports the ``dp`` package. That
bucket answers 403 for every DeepPhonemizer model (checked 2026-10-13) and
``dp`` isn't in the image, so any phrase with such a word failed the
whole training run. Here the missing words get their phones from espeak-ng
(already in the image for Piper) mapped to ARPAbet, added to
``pronouncing``'s tables in this process, and then train.py runs in this
same process, so the DeepPhonemizer branch is never reached.

    python train_runner.py /opt/openWakeWord/openwakeword/train.py --training_config cfg.yaml --generate_clips

Reads the phrase from WAKE_PHRASE (the entrypoint's env). Pure helpers
(``ipa_to_arpabet``, ``missing_words``) are unit-tested without the image.
"""

from __future__ import annotations

import os
import re
import runpy
import subprocess
import sys

# espeak-ng --ipa (en-us) symbols -> ARPAbet. Longest match first.
_VOWELS = {
    "eɪ": "EY", "aɪ": "AY", "ɔɪ": "OY", "aʊ": "AW", "oʊ": "OW", "əʊ": "OW",
    "ɑː": "AA", "ɑ": "AA", "ɒ": "AA", "æ": "AE", "ʌ": "AH", "ə": "AH",
    "ɐ": "AH", "ɔː": "AO", "ɔ": "AO", "oː": "AO", "o": "OW", "ɛ": "EH",
    "e": "EH", "ɜː": "ER", "ɜ": "ER", "ɚ": "ER", "ɝ": "ER", "ɪ": "IH",
    "ᵻ": "IH", "iː": "IY", "i": "IY", "ʊ": "UH", "uː": "UW", "u": "UW",
    "a": "AA",
}
_CONSONANTS = {
    "tʃ": "CH", "dʒ": "JH", "θ": "TH", "ð": "DH", "ʃ": "SH", "ʒ": "ZH",
    "ŋ": "NG", "ɹ": "R", "r": "R", "j": "Y", "w": "W", "h": "HH", "ɾ": "T",
    "ʔ": "T", "ɡ": "G", "g": "G", "b": "B", "d": "D", "f": "F", "k": "K",
    "l": "L", "m": "M", "n": "N", "p": "P", "s": "S", "t": "T", "v": "V",
    "z": "Z", "x": "K",
}
_SYMBOLS = sorted(
    [(k, v, True) for k, v in _VOWELS.items()]
    + [(k, v, False) for k, v in _CONSONANTS.items()],
    key=lambda t: -len(t[0]),
)


def ipa_to_arpabet(ipa: str) -> str:
    """espeak-ng IPA for ONE word -> CMU-style phones ("D AA1 M UW0 V OY2").
    A stress mark (ˈ primary, ˌ secondary) applies to the next vowel; other
    vowels are 0. Symbols it doesn't know are skipped, and so are espeak's
    language-switch tags such as "(en)"."""
    ipa = re.sub(r"\([a-z-]+\)", "", ipa)
    out: list[str] = []
    stress = "0"
    i = 0
    while i < len(ipa):
        ch = ipa[i]
        if ch == "ˈ":
            stress, i = "1", i + 1
            continue
        if ch == "ˌ":
            stress, i = "2", i + 1
            continue
        for sym, arpa, vowel in _SYMBOLS:
            if ipa.startswith(sym, i):
                if vowel:
                    out.append(arpa + stress)
                    stress = "0"
                else:
                    out.append(arpa)
                i += len(sym)
                break
        else:
            i += 1  # length marks, syllabic marks, anything unknown
    return " ".join(out)


def missing_words(phrase: str, phones_for_word) -> list[str]:
    """The phrase's words (as train.py splits them) the dictionary lacks."""
    seen: list[str] = []
    for word in phrase.split():
        if not phones_for_word(word) and word.lower() not in seen:
            seen.append(word.lower())
    return seen


def espeak_ipa(word: str) -> str:
    r = subprocess.run(
        ["espeak-ng", "-q", "--ipa", "-v", "en-us", word],
        capture_output=True, text=True, timeout=30,
    )
    return r.stdout.strip()


def add_pronunciations(phrase: str) -> dict[str, str]:
    import pronouncing

    pronouncing.init_cmu()
    added: dict[str, str] = {}
    for word in missing_words(phrase, pronouncing.phones_for_word):
        phones = ipa_to_arpabet(re.sub(r"\s+", "", espeak_ipa(word)))
        if not phones:
            continue
        pronouncing.pronunciations.append((word, phones))
        pronouncing.lookup[word].append(phones)
        added[word] = phones
    return added


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage: train_runner.py TRAIN_PY [train.py args...]")
    phrase = os.environ.get("WAKE_PHRASE", "")
    for word, phones in add_pronunciations(phrase).items():
        print(f"[train_runner] '{word}' is not in the CMU dictionary; "
              f"espeak-ng pronunciation: {phones}", flush=True)
    train_py = argv[0]
    sys.argv = [train_py, *argv[1:]]
    sys.path.insert(0, os.path.dirname(os.path.abspath(train_py)))
    runpy.run_path(train_py, run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
