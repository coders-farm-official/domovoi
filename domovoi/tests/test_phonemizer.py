"""The phonemizer's behaviour beyond the golden fixture: the shipped
pronunciations, the "say it like" format, the data files, and that no
input makes it raise (it sits in front of every spoken reply)."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from domovoi.phonemizer import (
    DATA_DIR,
    Phonemizer,
    PronunciationError,
    format_pronunciation_text,
    get_phonemizer,
    parse_pronunciation_text,
    parse_say_it_like,
    phoneme_ids,
    pronunciation_key,
    unknown_symbols,
)


@pytest.fixture(scope="module")
def ph() -> Phonemizer:
    return get_phonemizer()


# ── the shipped list ──────────────────────────────────────────────────────


def test_domovoi_is_doh_moe_voy(ph):
    """The owner's pronunciation (2026-10-12): DOH-moe-voy, stress on the
    first syllable, a full "oh" in the middle (not DOM-uh-voy)."""
    assert ph.phonemize("Domovoi") == ["dˈəʊməʊvˌɔɪ"]
    assert ph.phonemize("Hey Domovoi!") == ["hˈeɪ dˈəʊməʊvˌɔɪ!"]
    assert ph.phonemize("Domovoi's timer.") == ["dˈəʊməʊvˌɔɪz tˈaɪmə."]
    assert ph.pronunciations()["domovoi"].phones == ("D", "OW1", "M", "OW2", "V", "OY2")


def test_domo_is_doh_moe(ph):
    assert ph.phonemize("Hey Domo.") == ["hˈeɪ dˈəʊməʊ."]


def test_nguyen_is_win(ph):
    assert ph.phonemize("Nguyen") == ["wˈɪn"]


def test_without_the_list_the_dictionary_and_model_decide():
    bare = Phonemizer(pronunciations=[])
    assert bare.phonemize("Nguyen") == ["nuːjˈɛn"]
    assert bare.phonemize("Domovoi") != ["dˈəʊməʊvˌɔɪ"]


# ── the "say it like" format ──────────────────────────────────────────────


def test_capitals_mark_the_stress(ph):
    first = ph.resolve_pronunciation("Siobhan", "SHIV-awn").phones
    second = ph.resolve_pronunciation("Siobhan", "shiv-AWN").phones
    assert [p for p in first if p[-1] == "1"] != [p for p in second if p[-1] == "1"]
    assert second[-2] == "AO1"


def test_spaces_and_hyphens_both_split_syllables(ph):
    a = ph.resolve_pronunciation("Domovoi", "DOH-moe-voy").phones
    assert ph.resolve_pronunciation("Domovoi", "doh moe voy").phones == a
    assert ph.resolve_pronunciation("Domovoi", "DOH moe VOY").phones == a  # later capitals: lighter


def test_an_unstressed_uh_is_a_schwa(ph):
    assert ph.resolve_pronunciation("Domovoi", "DOM-uh-voy").phones == ("D", "AA1", "M", "AH0", "V", "OY2")


def test_the_list_beats_rules_and_weak_forms():
    p = Phonemizer(pronunciations={"live": "liv", "the": "THEE"})
    assert p.phonemize("a live show") == ["ɐ lˈɪv ʃˈəʊ"]
    assert "ðˈiː" in p.phonemize("the end")[0]


def test_matching_ignores_case_and_accents():
    p = Phonemizer(pronunciations={"Beyoncé": "bee-YON-say"})
    out = p.phonemize("Beyonce, BEYONCÉ, beyoncé.")[0]
    assert out.count("biːjˈɒnseɪ") == 3


def test_an_acronym_can_have_an_entry():
    p = Phonemizer(pronunciations={"SZA": "SIZ-uh"})
    assert p.phonemize("SZA") == ["sˈɪzə"]
    assert Phonemizer(pronunciations={}).phonemize("SZA") != ["sˈɪzə"]


def test_the_phoneme_form_is_used_as_written():
    p = Phonemizer(pronunciations={"Domovoi": "/dˈəʊməʊvɔɪ/"})
    assert p.phonemize("Domovoi's turn") == ["dˈəʊməʊvɔɪz tˈɜːn"]


@pytest.mark.parametrize("word,say,why", [
    ("", "win", "empty"),
    ("two words", "win", "one word only"),
    ("Bob", "", "say it like what"),
    ("Bob", "b0b", "plain letters"),
    ("Bob", "/wɪn", "two slashes"),
    ("Bob", "/w€n/", "don't know"),
    ("Bob", "/^/", "don't know"),
    ("Bob", "shh", "no vowel"),
])
def test_bad_entries_say_why(ph, word, say, why):
    with pytest.raises(PronunciationError, match=why):
        ph.resolve_pronunciation(word, say)


def test_a_bad_entry_leaves_the_old_list_in_force():
    p = Phonemizer(pronunciations={"Nguyen": "win"})
    with pytest.raises(PronunciationError):
        p.set_pronunciations({"Nguyen": "NOO-yen", "Bob": "b@b"})
    assert p.phonemize("Nguyen") == ["wˈɪn"]


def test_text_form_round_trips():
    text = "# comment\n\nDomovoi = DOH-moe-voy\nNguyen=win\n"
    pairs = parse_pronunciation_text(text)
    assert pairs == [("Domovoi", "DOH-moe-voy"), ("Nguyen", "win")]
    assert parse_pronunciation_text(format_pronunciation_text(pairs)) == pairs
    with pytest.raises(PronunciationError, match="line 2"):
        parse_pronunciation_text("Nguyen = win\nno equals sign here\n")


def test_keys():
    assert pronunciation_key(" Beyoncé ") == "beyonce"
    assert pronunciation_key("Jay-Z") == "jay-z"
    assert parse_say_it_like("Nguyen", "WIN").stressed == 0  # all capitals: nothing singled out


# ── data ──────────────────────────────────────────────────────────────────


def test_data_files_match_their_pinned_hashes():
    data = Path(DATA_DIR)
    lines = [ln for ln in (data / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
             if ln and not ln.startswith("#")]
    listed = set()
    for ln in lines:
        want, rel = ln.split(None, 1)
        listed.add(rel)
        assert hashlib.sha256((data / rel).read_bytes()).hexdigest() == want, rel
    on_disk = {p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file()}
    assert on_disk - {"SHA256SUMS"} == listed


def test_cmudict_licence_ships_beside_it():
    text = (Path(DATA_DIR) / "cmudict" / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("Copyright (C) 1993-2015 Carnegie Mellon University")
    assert "Redistribution and use in source and binary forms" in text


def test_no_media_provider_names_in_the_data():
    banned = ("yout" + "ube", "yt" + "-dlp", "yt" + "_dlp", "spot" + "ify")
    for p in Path(DATA_DIR).rglob("*.txt"):
        low = p.read_text(encoding="utf-8").lower()
        assert not any(b in low for b in banned), p.name


# ── robustness ────────────────────────────────────────────────────────────

ODD_INPUTS = [
    "", " ", "...", "?!", "—", "🙂🙂", "<b>bold</b>", "a" * 500, "x" * 41,
    "$123456789012345678901234567890", "99999999999999999999th", "1e10", "3/4/5/6",
    "12:99", "25:00", "-", "--5", "$", "£.", "°", "%", "Mr.", "U.S.A.S.", "AC/DC/EF",
    "\u0000​﻿", "ÆØÅ ß ł", "Привет", "日本語", "'''", "-'-", "O'Neil's", "rock'n'roll",
    "word" * 300, "1,2,3,4,5", "0.0.0.0", "192.168.1.1", "10:30:45", "1st 2nd 3rd 4th",
]


@pytest.mark.parametrize("text", ODD_INPUTS)
def test_no_input_raises(ph, text):
    for s in ph.phonemize(text):
        assert isinstance(s, str) and s
        assert unknown_symbols(s) == []
        assert phoneme_ids(s)[0] == 1 and phoneme_ids(s)[-1] == 2


def test_threads_share_one_converter(ph):
    want = ph.phonemize("Skrillex and Oluwaseun at 4:30 PM.")
    got, errors = [], []

    def run():
        try:
            for _ in range(20):
                got.append(ph.phonemize("Skrillex and Oluwaseun at 4:30 PM."))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and all(g == want for g in got)


def test_without_onnx_runtime_unknown_words_are_spelled():
    p = Phonemizer(model=False, pronunciations=[])
    assert not p.has_model
    assert p.phonemize("hello") == ["həlˈəʊ"]
    assert p.phonemize("Skrillex")[0]  # spelled out, but it still speaks
