"""The spoken-forms normalization (domovoi/handlers/shared/spoken_names.py).

DB-free on purpose, so it never skips. The examples are the owner's own
(2026-10-02: "$uicideboy$" said "suicide boys", GENER8ION said
"generation") and textbook cases of each rule — none is taken from the
held-out spoken-match gate set, which only ever measures (checked against
functional-testing/spoken-gate/audit_set_v2.json when they were written).
"""

from __future__ import annotations

import time
from itertools import product

import pytest

from domovoi.handlers.shared import spoken_names as sn


# ─── alias_key: the frozen identity of a name ─────────────────────────────


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Subtract", "subtract!"),
        (" SUBTRACT ", "subtract"),
        ("Alley Cat", "alley-cat"),
        ("$uicideboy$", "Suicide Boys"),
        ("$UICIDEBOY$", "suicideboys"),
        ("P!nk", "Pink"),
        ("JAŸ-Z", "Jay Z"),
        ("3 Doors Down", "three doors down"),
        ("twenty-two lanterns", "22 Lanterns"),
        ("T.I.", "ti"),
        ("U.S.D.A.", "U, S, D, A"),
        ("AC/DC", "ACDC"),
        ("Florence + The Machine", "Florence and the Machine"),
        ("The Who", "Who"),
        ("A$AP Rocky", "ASAP Rocky"),
        ("Mötley Crüe", "Motley Crue"),
        ("*NSYNC", "NSYNC"),
    ],
)
def test_alias_key_is_shared_by_spellings_of_one_name(a: str, b: str) -> None:
    assert sn.alias_key(a) == sn.alias_key(b) != ""


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("subtract", "SBTRKT"),        # sounds alike, different names
        ("tech nine", "Tech N9ne"),    # a reading, not the stored spelling
        ("Motley Crew", "Mötley Crüe"),
        ("Pink Floyd", "Pink"),
    ],
)
def test_alias_key_keeps_different_names_apart(a: str, b: str) -> None:
    assert sn.alias_key(a) != sn.alias_key(b)


def test_alias_key_is_pinned_at_key_version_1() -> None:
    """Stored keys are compared with this function: a change to any of these
    outputs needs KEY_VERSION bumped and library_aliases re-keyed."""
    assert sn.KEY_VERSION == 1
    assert sn.alias_key("$uicideboy$") == "suicideboys"
    assert sn.alias_key("GENER8ION") == "gener8ion"
    assert sn.alias_key("The Velvet K.O.D.") == "velvetkod"
    assert sn.alias_key("Blink-182") == "blinkonehundredeightytwo"
    assert sn.alias_key("3rd Eye Blind") == "thirdeyeblind"
    assert sn.alias_key("") == ""
    assert sn.alias_key("!!!") == ""


# ─── spoken_forms: every way a name may be said ───────────────────────────


@pytest.mark.parametrize(
    ("stored", "said"),
    [
        ("GENER8ION", "generation"),        # 8 as its sound, silent e dropped
        ("Tech N9ne", "tech nine"),         # 9 as its word, overlap merged
        ("Se7en", "seven"),
        ("deadmau5", "deadmaus"),           # 5 as a letter
        ("6LACK", "black"),
        ("sk8er boi", "skater boi"),
        ("Learn2Walk", "learntowalk"),
        ("H2O", "h two o"),                 # a short token spelled out
        ("M2M", "m two m"),
        ("Blink-182", "blink one eighty two"),  # how a 3-digit number is said
        ("1999", "nineteen ninety nine"),
        ("The Who", "who"),                 # leading "the" optional
    ],
)
def test_spoken_forms_include_the_reading(stored: str, said: str) -> None:
    assert sn.compact(said) in sn.compact_forms(stored)


def test_spoken_forms_put_the_primary_reading_first_and_cap_the_count() -> None:
    forms = sn.spoken_forms("Tech N9ne")
    assert forms[0] == "tech n9ne"
    assert len(forms) <= 2 * sn.MAX_FORMS
    assert sn.spoken_forms("") == ()


def _forms_the_old_way(text: str) -> tuple[str, ...]:
    """spoken_forms as first written: every combination of the tokens'
    readings built, sorted by departures from the primary, cut at
    MAX_FORMS — the order the lazy version must keep."""
    tokens = sn._clean(sn.fold(text)).split()
    if not tokens:
        return ()
    alts = [sn._token_variants(t) for t in tokens]
    combos = sorted(product(*[range(len(a)) for a in alts]), key=lambda idx: sum(1 for i in idx if i))
    forms: dict[str, None] = {}
    for idx in combos:
        forms.setdefault(" ".join(" ".join(alts[t][i] for t, i in enumerate(idx)).split()), None)
        if len(forms) >= sn.MAX_FORMS:
            break
    out: dict[str, None] = {}
    for f in forms:
        out.setdefault(f, None)
        bare = sn._drop_leading_the(f)
        if bare != f:
            out.setdefault(bare, None)
    return tuple(out)


@pytest.mark.parametrize(
    "name",
    [
        "Tech N9ne", "GENER8ION", "Blink-182", "1999 2 4 8", "H2O M2M 2 4", "Se7en Sk8er 4ever",
        "The 2 Lanterns of 1999", "22 Lanterns", "Room 0417", "Harbor Waltz, Pt. 2",
        "2 4 8 2 4 8", "Learn2Walk 3700 8", "",
    ],
)
def test_lazy_readings_keep_the_old_order(name: str) -> None:
    assert sn.spoken_forms(name) == _forms_the_old_way(name)


def test_many_multi_reading_words_cost_nothing() -> None:
    """2026-10-03 review: building every combination of readings before
    keeping eight grew 4x per two words ("2 2 2 …" — 0.6 s at 20 words,
    2^60 combinations at the 120-character alias limit). Now the readings
    are generated fewest-departures-first and stop at MAX_FORMS."""
    for text in (
        " ".join(["2"] * 60),
        "9" * 120,
        " ".join(["2 4 8"] * 20),
        " ".join(str(y) for y in range(2000, 2030)),
        " ".join(["4k"] * 40),
    ):
        sn.spoken_forms.cache_clear()
        t0 = time.perf_counter()
        forms = sn.spoken_forms(text)
        key = sn.alias_key(text)
        assert (time.perf_counter() - t0) < 0.05, text[:20]
        assert 0 < len(forms) <= 2 * sn.MAX_FORMS
        assert forms[0] == " ".join(sn._token_variants(t)[0] for t in sn._clean(sn.fold(text)).split())
        assert key == sn.compact(sn._drop_leading_the(forms[0]))
    assert sn.spoken_forms(" ".join(["2"] * 60))[0] == " ".join(["two"] * 60)


def test_a_number_written_with_leading_zeros_is_also_said_with_them() -> None:
    """Readings only: the primary (and so alias_key) is the number itself."""
    forms = sn.spoken_forms("Room 0417")
    assert forms[0] == "room four hundred seventeen"
    for said in ("room zero four seventeen", "room oh four one seven", "room zero four one seven"):
        assert said in forms, said
    assert "oh oh eight" in sn.spoken_forms("008")
    assert sn.alias_key("Room 0417") == "roomfourhundredseventeen"
    assert sn.alias_key("008") == "eight"


def test_pt_is_also_said_part() -> None:
    assert sn.compact("harbor waltz part two") in sn.compact_forms("Harbor Waltz, Pt. 2")
    assert sn.alias_key("Harbor Waltz, Pt. 2") == "harborwaltzpttwo"


def test_no_symbol_names_and_no_slang_table() -> None:
    """The audit (2026-10-02) found these fitted to single test items."""
    assert sn.spoken_forms("÷") == ()
    assert "degrees" not in " ".join(sn.spoken_forms("98º"))
    assert sn.spoken_forms("tha luv") == ("tha luv",)


# ─── same_name / sounds_like ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b", "same", "sounds"),
    [
        ("Suicide Boys", "$uicideboy$", True, True),
        ("Fifty Cent", "50 Cent", True, True),
        ("Dead Mouse", "deadmau5", False, True),
        ("Churches", "CHVRCHES", False, True),
        ("Toolate", "2Late", False, True),
        ("Jackson", "50 Cent", False, False),        # a real name, not a way to say it
        ("Curtis Jackson", "50 Cent", False, False),
        ("Alecia Moore", "P!nk", False, False),
        ("Shawn Carter", "JAŸ-Z", False, False),
    ],
)
def test_same_name_and_sounds_like(a: str, b: str, same: bool, sounds: bool) -> None:
    assert sn.same_name(a, b) is same
    assert sn.sounds_like(a, b) is sounds


def test_phonetic_key_is_double_metaphone_per_word() -> None:
    assert sn.phonetic_key("dead mouse") == "TTMS"
    assert sn.phonetic_key("") == ""


# ─── speakable ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stored", "spoken"),
    [
        ("P!nk", "Pink"),
        ("$uicideboy$", "suicideboys"),
        ("T.I.", "T I"),
        ("U.S.D.A.", "U S D A"),
        ("Florence + The Machine", "Florence and The Machine"),
        ("Paper Comets (Official Video)", "Paper Comets"),
        ("Song (feat. Someone) - Remastered 2011", "Song"),
        ("JAŸ-Z", "JAY-Z"),
    ],
)
def test_speakable(stored: str, spoken: str) -> None:
    assert sn.speakable(stored) == spoken


def test_speakable_leaves_pictographs_and_symbols_out() -> None:
    """anyascii writes 💿 as ":cd:" and ™ as "TM"; a reply should say
    neither. A name that is nothing but a symbol keeps its reading."""
    assert sn.speakable("💿 Cinder Static Sessions ✨") == "Cinder Static Sessions"
    assert sn.speakable("Kettle Bloom™") == "Kettle Bloom"
    assert sn.speakable("Kettle Bloom ©") == "Kettle Bloom"
    assert sn.speakable("✨") == ":sparkles:"
    # The key is frozen: what is said back changes, what is stored doesn't.
    assert sn.alias_key("Kettle Bloom™") == "kettlebloomtm"


# ─── library names ───────────────────────────────────────────────────────


def test_split_credit() -> None:
    assert sn.split_credit("Corbin Hale, Ivy Marsh, Rook 3000") == ["Corbin Hale", "Ivy Marsh", "Rook 3000"]
    assert sn.split_credit("$UICIDEBOY$ Ft. Tech N9ne") == ["$UICIDEBOY$", "Tech N9ne"]
    assert sn.split_credit("Sable Fern x Orrin Vale") == ["Sable Fern", "Orrin Vale"]
    assert sn.split_credit("Little Fern X") == ["Little Fern X"]
    assert sn.split_credit("AC/DC") == ["AC/DC"]
    assert sn.split_credit("") == []


def test_entity_names_index_the_whole_credit_and_its_parts() -> None:
    names = sn.entity_names("STORM", "MTV, CBS, Yung Lean, GENER8ION", None)
    assert ("credit", "MTV, CBS, Yung Lean, GENER8ION") in names
    assert ("artist", "GENER8ION") in names
    assert ("title", "STORM") in names


def test_entity_names_recover_the_artist_from_a_filename_title() -> None:
    names = sn.entity_names("Metallica_ Whiskey in the Jar (Official Music Video)", None, None)
    assert ("artist", "Metallica") in names
    assert ("title", "Whiskey in the Jar") in names


def test_clean_title_keeps_titles_that_are_only_decoration() -> None:
    assert sn.clean_title("My Generation - Mono Version") == "My Generation"
    assert sn.clean_title("Dancing with Myself") == "Dancing with Myself"
    assert sn.clean_title("(Untitled)") == "(Untitled)"


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "zero"), (7, "seven"), (21, "twenty one"), (40, "forty"),
        (105, "one hundred five"), (182, "one hundred eighty two"),
        (1999, "one thousand nine hundred ninety nine"),
        (3700, "three thousand seven hundred"),
    ],
)
def test_int_to_words(n: int, words: str) -> None:
    assert sn.int_to_words(n) == words


def test_int_to_ordinal_words() -> None:
    assert sn.int_to_ordinal_words(1) == "first"
    assert sn.int_to_ordinal_words(12) == "twelfth"
    assert sn.int_to_ordinal_words(21) == "twenty first"
    assert sn.int_to_ordinal_words(40) == "fortieth"
