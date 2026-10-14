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


# ── Synthesis: the household's pronunciation ──────────────────────────────
#
# piper-sample-generator (dscripka's fork, the one train.py drives) turns
# each phrase into phonemes itself, with espeak-ng (espeak_phonemizer's
# Phonemizer.phonemize) and has no phoneme input. espeak-ng says "domovoi"
# DOM-oo-voy (dˈɑːmuːvˌɔɪ); this household says DOH-moe-voy. The host sends
# WAKE_SYNTH_IPA ("domovoi=dˈoʊmoʊvˌɔɪ;domo=dˈoʊmoʊ"): Domovoi's own
# phonemizer's IPA, in the voice's espeak "en-us" symbols, for every phrase
# word the household's "say it like" list (or the repo lexicon's own fixes)
# gives a pronunciation. install_synthesis_overrides wraps Phonemizer.
# phonemize in this process, so every synthetic positive, and any negative
# phrase with such a word, is spoken from those phonemes.

def parse_synth_ipa(text: str) -> dict[str, str]:
    """WAKE_SYNTH_IPA -> {word: IPA}. Words as in WAKE_PRONUNCIATIONS; the
    IPA has no blanks, '=' or ';', at most 64 characters."""
    out: dict[str, str] = {}
    for item in (text or "").split(";"):
        word, sep, ipa = item.partition("=")
        word, ipa = word.strip().lower(), ipa.strip()
        if (not sep or not _WORD.match(word) or not ipa or len(ipa) > 64
                or any(c.isspace() or c in "=;\"'`$\\" for c in ipa)):
            continue
        out[word] = ipa
    return out


def _norm_token(token: str) -> str:
    return re.sub(r"[^\w'-]", "", token.lower()).strip("'-")


def apply_overrides(text: str, phonemes: str, overrides: dict[str, str],
                    phonemize_word, word_separator: str = " ") -> str:
    """espeak's ``phonemes`` for ``text`` with the overridden words' IPA put
    in. espeak gives one IPA word per text word (punctuation dropped), so
    the words are swapped in place; when the counts differ (a number read
    as several words), each word is phonemized on its own instead."""
    tokens = text.split()
    keys = [_norm_token(t) for t in tokens]
    if not any(k in overrides for k in keys):
        return phonemes
    words = phonemes.split(word_separator) if phonemes else []
    if len(words) == len(tokens):
        return word_separator.join(overrides.get(k, w) for k, w in zip(keys, words))
    return word_separator.join(
        overrides[k] if k in overrides else phonemize_word(t) for k, t in zip(keys, tokens))


def voice_symbols() -> set[str] | None:
    """The symbols the generator's default voice knows (its .pt.json
    phoneme_id_map), or None when it can't be read."""
    import glob
    import json

    for path in sorted(glob.glob("/opt/piper-sample-generator/models/*.pt.json")):
        try:
            return set(json.load(open(path, encoding="utf-8"))["phoneme_id_map"])
        except (OSError, ValueError, KeyError):
            continue
    return None


def usable_overrides(overrides: dict[str, str], symbols: set[str] | None) -> tuple[dict, dict]:
    """(kept, dropped: {word: the symbols the voice lacks})."""
    import unicodedata

    if symbols is None:
        return dict(overrides), {}
    kept, dropped = {}, {}
    for word, ipa in overrides.items():
        missing = sorted({c for c in unicodedata.normalize("NFD", ipa) if c not in symbols})
        if missing:
            dropped[word] = "".join(missing)
        else:
            kept[word] = ipa
    return kept, dropped


def install_synthesis_overrides(overrides: dict[str, str], say=print, examples: int = 5):
    """Wrap espeak_phonemizer.Phonemizer.phonemize so the generator speaks
    the overridden words as given. Logs the choice, then the first
    ``examples`` distinct phrases it changed, with their phonemes. Returns
    a dict of counters (texts changed / seen) for the end-of-run line."""
    import espeak_phonemizer

    stats = {"changed": 0, "seen": 0}
    if not overrides:
        return stats
    original = espeak_phonemizer.Phonemizer.phonemize
    shown: set[str] = set()

    def phonemize(self, text, *args, **kwargs):
        out = original(self, text, *args, **kwargs)
        stats["seen"] += 1
        sep = kwargs.get("word_separator", " ")
        new = apply_overrides(text, out, overrides,
                              lambda w: original(self, w, *args, **kwargs), sep)
        if new != out:
            stats["changed"] += 1
            if len(shown) < examples and text not in shown:
                shown.add(text)
                say(f"[train_runner] synthesising {text!r} as {new} "
                    f"(espeak-ng alone: {out})")
        return new

    espeak_phonemizer.Phonemizer.phonemize = phonemize
    for word, ipa in overrides.items():
        try:
            espeak = original(espeak_phonemizer.Phonemizer("en-us"), word)
        except Exception:  # noqa: BLE001 - only for the log line
            espeak = "?"
        say(f"[train_runner] synthesis: {word!r} spoken as {ipa} (the household's "
            f"pronunciation, from Domovoi's phonemizer; espeak-ng would say {espeak})")
    return stats


# ── Near-miss negatives ────────────────────────────────────────────────────
#
# train.py's custom_negative_phrases: real words that sound like a wake
# word's start or like the whole word, put in the phrase in its place
# ("hey domino", "hello demo"). Worked out from the phrase and the CMU
# dictionary, never listed per wake word. Call words ("hey", "hello") are
# kept: a near miss of the name is what a household says by accident.

CALL_WORDS = frozenset({"hey", "hi", "hello", "hallo", "hiya", "heya", "ok", "okay",
                        "yo", "oi", "ahoy", "o", "oh"})
_VOWEL_PHONES = frozenset("AA AE AH AO AW AY EH ER EY IH IY OW OY UH UW".split())


def _plain(phones) -> list[str]:
    return [p.rstrip("012") for p in (phones.split() if isinstance(phones, str) else phones)]


def _edit(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _syllables(phones: list[str]) -> int:
    return sum(1 for p in phones if p in _VOWEL_PHONES)


def spelling_stem(word: str) -> str:
    """The spelling to the end of the first vowel group plus one letter:
    domovoi -> dom, domo -> dom, jarvis -> jar, computer -> com."""
    i = 0
    while i < len(word) and word[i] not in "aeiouy":
        i += 1
    while i < len(word) and word[i] in "aeiouy":
        i += 1
    return word[: i + 1]


def near_miss_words(word: str, targets: list, lexicon: dict[str, list[str]],
                    limit: int = 9) -> list[str]:
    """Up to ``limit`` dictionary words that sound near ``word`` (said as
    any of ``targets``, ARPAbet): words that are its start (dome, dom),
    words spelled and starting like it (domino, dominate), and words a
    phone or so away (demo). A word with its own regular inflections in the
    dictionary (dominoes, domed) goes before a surname, and among equals
    the one spelled most like ``word`` (espeak-ng reads the spelling).
    Homophones of a target are never a near miss."""
    def inflected(w: str, c: list[str]) -> int:
        n = 0
        for suffix in ("s", "es", "ed", "d", "ing", "er", "ers"):
            forms = lexicon.get(w + suffix)
            if forms and _plain(forms[0])[: len(c)] == c:
                n += 1
        return n

    stem = spelling_stem(word)
    ts = [_plain(t) for t in targets if t]
    if not ts:
        return []
    starts, family, close = [], [], []
    for w, prons in lexicon.items():
        if w == word or not w.isalpha() or len(w) < 2:
            continue
        c = _plain(prons[0])
        if any(c == t for t in ts):
            continue
        common = inflected(w, c)
        spelled = _edit(list(w), list(word))
        for t in ts:
            d = _edit(t, c)
            if 2 <= len(c) < len(t) and c == t[: len(c)] and _syllables(c):
                starts.append((-common, -len(c), spelled, w))
            elif w.startswith(stem) and len(w) <= len(word) + 3:
                family.append((common == 0, abs(_syllables(c) - _syllables(t)), d, spelled, w))
            elif d <= max(1, len(t) // 3) and abs(len(c) - len(t)) <= 2:
                close.append((common == 0, d, c[0] != t[0], spelled, w))
    out: list[str] = []
    for pool, n in ((starts, 3), (family, 4), (close, 2)):
        took = 0
        for *_, w in sorted(pool):
            if took >= n:
                break
            if w not in out:
                out.append(w)
                took += 1
    return out[:limit]


def near_miss_phrases(phrase: str, phones_for, lexicon: dict[str, list[str]],
                      per_word: int = 9, limit: int = 24) -> list[str]:
    """The phrase with one word at a time swapped for a near miss of it.
    ``phones_for(word)`` -> the ARPAbet readings to measure against (the
    household's first)."""
    words = [_norm_token(w) for w in phrase.split()]
    out: list[str] = []
    for i, w in enumerate(words):
        if not w or w in CALL_WORDS:
            continue
        for alt in near_miss_words(w, phones_for(w), lexicon, per_word):
            text = " ".join(words[:i] + [alt] + words[i + 1:])
            if text not in out:
                out.append(text)
    return out[:limit]


def readings(word: str, given: dict[str, str]) -> list[str]:
    """A word's ARPAbet readings: the household's (WAKE_PRONUNCIATIONS)
    first, then the dictionary's, then espeak-ng's for a word it lacks."""
    import pronouncing

    pronouncing.init_cmu()
    out = [given[word]] if word in given else []
    cmu = pronouncing.phones_for_word(word)
    if cmu:
        out.append(cmu[0])
    else:
        try:
            out.append(ipa_to_arpabet(re.sub(r"\s+", "", espeak_ipa(word))))
        except Exception:  # noqa: BLE001 - no espeak-ng: the given reading only
            pass
    return list(dict.fromkeys(p for p in out if p))


def phrase_near_misses(phrase: str) -> list[str]:
    """custom_negative_phrases for ``phrase`` (the entrypoint renders them
    into the training config)."""
    import pronouncing

    pronouncing.init_cmu()
    lexicon: dict[str, list[str]] = {}
    for w, p in pronouncing.pronunciations:
        lexicon.setdefault(w, []).append(p)
    given = parse_pronunciations(os.environ.get("WAKE_PRONUNCIATIONS", ""))
    return near_miss_phrases(phrase, lambda w: readings(w, given), lexicon)


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage: train_runner.py TRAIN_PY [train.py args...]")
    phrase = os.environ.get("WAKE_PHRASE", "")
    given = parse_pronunciations(os.environ.get("WAKE_PRONUNCIATIONS", ""))
    for word, (phones, source) in add_pronunciations(phrase, given).items():
        print(f"[train_runner] '{word}': {phones} ({source})", flush=True)
    synth, dropped = usable_overrides(
        parse_synth_ipa(os.environ.get("WAKE_SYNTH_IPA", "")), voice_symbols())
    for word, missing in dropped.items():
        print(f"[train_runner] synthesis: {word!r} left to espeak-ng: the voice has no "
              f"symbol {missing!r}", flush=True)
    stats = install_synthesis_overrides(synth, say=lambda s: print(s, flush=True))
    if synth:
        import atexit

        atexit.register(lambda: print(
            f"[train_runner] synthesis: {stats['changed']} of {stats['seen']} phrases "
            f"spoken with the household's pronunciation", flush=True))
    train_py = argv[0]
    sys.argv = [train_py, *argv[1:]]
    sys.path.insert(0, os.path.dirname(os.path.abspath(train_py)))
    runpy.run_path(train_py, run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
