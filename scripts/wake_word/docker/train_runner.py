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

The host sends its own pronunciations first, in WAKE_PRONUNCIATIONS
("domovoi=D OW1 M OW2 V OY2;domo=D OW1 M OW2"): the core works them out
with Domovoi's own permissive phonemizer (domovoi/phonemizer/, MIT), the
household's "say it like" list included, so the near-miss phrases are
built around the way the house says the word. Those win, also over a
dictionary entry; espeak-ng only covers a missing word the host sent
nothing for (a hand-run trainer, or an older core).

    python train_runner.py /opt/openWakeWord/openwakeword/train.py --training_config cfg.yaml --generate_clips

Reads the phrase from WAKE_PHRASE (the entrypoint's env). Pure helpers
(``ipa_to_arpabet``, ``missing_words``, ``parse_pronunciations``) are
unit-tested without the image.
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


# The CMU dictionary's 39 phones (a vowel with its stress digit): what
# WAKE_PRONUNCIATIONS may carry, and nothing else.
_ARPA_VOWELS = "AA AE AH AO AW AY EH ER EY IH IY OW OY UH UW".split()
_ARPA_CONSONANTS = "B CH D DH F G HH JH K L M N NG P R S SH T TH V W Y Z ZH".split()
_PHONE = re.compile(
    r"^(?:(?:" + "|".join(_ARPA_VOWELS) + r")[0-2]|" + "|".join(_ARPA_CONSONANTS) + r")$"
)
_WORD = re.compile(r"^[a-z0-9'.-]+$")


def parse_pronunciations(text: str) -> dict[str, str]:
    """WAKE_PRONUNCIATIONS ("word=PH ON ES;word2=...") -> {word: phones}.
    An entry that isn't a lower-case word and ARPAbet phones is left out."""
    out: dict[str, str] = {}
    for item in (text or "").split(";"):
        word, sep, phones = item.partition("=")
        word = word.strip().lower()
        parts = phones.split()
        if (not sep or not _WORD.match(word) or not parts
                or not all(_PHONE.match(p) for p in parts)):
            continue
        out[word] = " ".join(parts)
    return out


def add_pronunciations(phrase: str, given: dict[str, str] | None = None
                       ) -> dict[str, tuple[str, str]]:
    """Give ``pronouncing`` a pronunciation for every phrase word: the
    host's (``given``) first, also over a dictionary entry; espeak-ng for a
    word the dictionary lacks and the host sent nothing for. Returns
    {word: (phones, where from)}."""
    import pronouncing

    pronouncing.init_cmu()
    given = given or {}
    added: dict[str, tuple[str, str]] = {}
    for word in dict.fromkeys(w.lower() for w in phrase.split()):
        if word in given:
            phones, source = given[word], "Domovoi's phonemizer"
            # First in the lookup, so phones_for_word(word)[0] is this one.
            pronouncing.lookup[word].insert(0, phones)
        elif not pronouncing.phones_for_word(word):
            phones = ipa_to_arpabet(re.sub(r"\s+", "", espeak_ipa(word)))
            if not phones:
                continue
            source = "espeak-ng"
            pronouncing.lookup[word].append(phones)
        else:
            continue
        pronouncing.pronunciations.append((word, phones))
        added[word] = (phones, source)
    return added


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage: train_runner.py TRAIN_PY [train.py args...]")
    phrase = os.environ.get("WAKE_PHRASE", "")
    given = parse_pronunciations(os.environ.get("WAKE_PRONUNCIATIONS", ""))
    for word, (phones, source) in add_pronunciations(phrase, given).items():
        print(f"[train_runner] '{word}': {phones} ({source})", flush=True)
    train_py = argv[0]
    sys.argv = [train_py, *argv[1:]]
    sys.path.insert(0, os.path.dirname(os.path.abspath(train_py)))
    runpy.run_path(train_py, run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
