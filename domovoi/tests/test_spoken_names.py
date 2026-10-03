"""The spoken-forms normalization (domovoi/handlers/shared/spoken_names.py).

DB-free on purpose, so it never skips. The examples are the owner's own
(2026-10-02: "$uicideboy$" said "suicide boys", GENER8ION said
"generation") and textbook cases of each rule — none is taken from the
held-out spoken-match gate set, which only ever measures (checked against
functional-testing/spoken-gate/audit_set_v2.json when they were written).
"""

from __future__ import annotations

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
        ("twenty-one savage", "21 Savage"),
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
    assert sn.alias_key("The Notorious B.I.G.") == "notoriousbig"
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
        ("Tupac", "2Pac", False, True),
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
        ("Bad Girls (Official Video)", "Bad Girls"),
        ("Song (feat. Someone) - Remastered 2011", "Song"),
        ("JAŸ-Z", "JAY-Z"),
    ],
)
def test_speakable(stored: str, spoken: str) -> None:
    assert sn.speakable(stored) == spoken


# ─── library names ───────────────────────────────────────────────────────


def test_split_credit() -> None:
    assert sn.split_credit("Jeezy, JAŸ-Z, André 3000") == ["Jeezy", "JAŸ-Z", "André 3000"]
    assert sn.split_credit("$UICIDEBOY$ Ft. Tech N9ne") == ["$UICIDEBOY$", "Tech N9ne"]
    assert sn.split_credit("Skrillex x Diplo") == ["Skrillex", "Diplo"]
    assert sn.split_credit("Lil Nas X") == ["Lil Nas X"]
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
