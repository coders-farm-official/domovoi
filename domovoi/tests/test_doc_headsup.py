"""The honest heads-up's rules (domovoi/doc_qa/headsup.py, calc.py; documents plan D3).

No database and no model: the rules are pure. The shared vectors
(``headsup_cases.json``, written by ``scripts/dev/headsup_cases.py``) hold
the app's ``DocHeadsUp.kt`` / ``DocCalc.kt`` to the same results; the
tests here check the file is current, then the behaviours that matter by
name: the table decides (not the model), only options that work are
offered, a kind shows once per chat, "don't warn me" and the master switch
silence a card but keep the two notes that guard against a wrong answer,
nothing applies with AI off, and "calculate exactly" adds up only numbers
its page really shows.
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from domovoi.doc_qa import answer as qa
from domovoi.doc_qa import calc
from domovoi.doc_qa import headsup as h
from domovoi.doc_qa.text import Passage

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "domovoi" / "doc_qa" / "headsup_cases.json").read_text(encoding="utf-8"))
TABLE = h.load_table()

LONG = 60000
GIVEN = [("p. 1", "The monthly rent is $1,200, due on the first day of each month."),
         ("p. 2", "The security deposit is $1,500. Move in on March 1, 2026; inspection on April 15, 2026.")]


def _req(question: str, *, mode: str = "question", chars: int = LONG, pages: int | None = 30, scanned=(),
         sample: str = "", where: str = "server", can=("calculate",), given=GIVEN, facts=None,
         ai_on: bool = True, picture=None) -> h.Request:
    return h.Request(mode=mode, question=question, where=where,
                     docs=[h.Doc("lease.pdf", chars, pages, 10000, list(scanned), sample)],
                     given=list(given), facts=facts or {}, picture=picture or {}, can=frozenset(can), ai_on=ai_on)


def _given(items) -> list[qa.Given]:
    return [qa.Given(Passage(0, i, lab, txt, ""), lab, 1.0) for i, (lab, txt) in enumerate(items)]


# ─── The shared vectors ──────────────────────────────────────────────────
def test_the_shared_vectors_are_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "dev" / "headsup_cases.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr


def test_the_shared_vectors_cover_every_kind_and_option():
    kinds = {c["kind"] for f in CASES["findings"] for c in f["cards"]}
    assert kinds == set(h.KINDS)
    options = {o for f in CASES["findings"] for c in f["cards"] for o in c["options"]}
    assert options >= {"calculate", "try_anyway", "go_ahead", "home_server", "choose_another", "send_anyway"}


# ─── The table ───────────────────────────────────────────────────────────
def test_every_kind_has_its_card_options_and_what_to_do_instead():
    labels = TABLE["options"]
    for kind in h.KINDS:
        k = TABLE["kinds"][kind]
        assert k["title"] and k["card"] and k["instead"], kind
        assert k["options"] and all(o in labels for o in k["options"]), kind
    assert TABLE["kinds"]["calc"]["note"] and TABLE["kinds"]["scanned"]["note"]
    assert h.NOTE_KINDS == {"calc", "scanned"}
    assert labels["calculate"] == "calculate exactly" and TABLE["never_label"] == "don't warn me about this again"


def test_every_cell_says_what_was_measured_where_and_when():
    for c in TABLE["cells"]:
        for key in ("kind", "where", "band", "verdict", "source", "eval", "date", "device", "manual", "measure"):
            assert c.get(key) not in (None, ""), (c["kind"], c["band"], key)
        assert c["where"] in ("phone", "server", "both")
        assert c["verdict"] in ("good", "okay", "not_reliable", "not_measured", "slow")
        # A measured pass rate decides the verdict; the table can't say otherwise.
        assert c["verdict"] == h.verdict(TABLE, c), (c["kind"], c["band"])
    assert TABLE["bar"] == 0.7 and TABLE["tested_languages"] == ["en", "es"]


def test_a_card_fires_from_the_table_not_from_the_wording_alone():
    """The same comparison fires or not as the table's measured cell says."""
    q = "Does section 2 contradict section 9?"
    table = json.loads(json.dumps(TABLE))
    cell = next(c for c in table["cells"] if (c["kind"], c["band"]) == ("compare", "long"))
    cell["pass"] = 0.4
    assert [c.kind for c in h.findings(_req(q), table)] == ["compare"]
    cell["pass"] = 0.93
    assert h.findings(_req(q), table) == []


# ─── Recognising the task (no model involved) ───────────────────────────
@pytest.mark.parametrize("q,op", [
    ("What is the total of the rent and the deposit?", "sum"),
    ("How much do I pay altogether?", "sum"),
    ("What is the average monthly bill?", "average"),
    ("What does error code E4 mean?", None),          # "mean" the verb (E-HEADSUP's false alarm)
    ("How many days between move-in and the inspection?", "days"),
    ("How many days do I have to return the keys?", None),
    ("How much more is the deposit than the rent?", "difference"),
    ("What's the difference between plan A and plan B?", None),
    ("What percentage of the deposit is kept?", "percent"),
    ("When is the rent due?", None),
])
def test_calculations_are_recognised_from_the_wording(q, op):
    assert h.calc_request(q) == op


@pytest.mark.parametrize("q,yes", [
    ("Rewrite this in plain English", True), ("Translate the whole lease into Spanish", True),
    ("Proofread the document", True), ("Make the whole manual shorter", True), ("Fix the grammar", True),
    ("Rewrite the second paragraph", False), ("Translate page 3", False), ("When is the rent due?", False),
])
def test_whole_document_rewrites_are_told_from_rewrites_of_a_part(q, yes):
    assert h.rewrite_request(q) is yes


def test_the_language_guess_names_only_what_it_is_sure_of():
    by_text = {c["text"][:20]: c["lang"] for c in CASES["language"]}
    assert set(by_text.values()) == {"en", "es", "pl", "fr", "de", None}
    assert h.guess_language("Rent is due on the first.") is None


# ─── The cards ───────────────────────────────────────────────────────────
def test_only_options_that_work_here_are_offered():
    q = "What is the total of the rent and the deposit?"
    assert h.check(_req(q, can=("calculate",))).options == ["calculate", "try_anyway"]
    phone = h.check(_req(q, can=()))
    assert phone.options == ["try_anyway"] and "Check the result yourself" in phone.text
    # No calculator for a percentage: the card says so, it offers no button.
    assert h.check(_req("What percent of the deposit is the rent?")).options == ["try_anyway"]
    # The home server only where its offer stands (reachable, measured faster).
    facts = {"parts": 8, "estimate_s": 130.0, "home_server_s": 25.0}
    with_offer = h.check(_req("Summarize this", mode="long_summary", where="phone", can=("home_server",), facts=facts))
    assert with_offer.options == ["go_ahead", "home_server"] and "about 25 s" in with_offer.text
    assert h.check(_req("Summarize this", mode="long_summary", where="phone", can=(), facts=facts)) is None


def test_a_stated_total_is_quoted_not_calculated():
    given = [("p. 4", "The total due at signing is $2,700."), ("p. 5", "Rent $1,200; deposit $1,500.")]
    assert h.check(_req("What's the total due at signing?", given=given)) is None


def test_a_comparison_whose_named_parts_were_both_found_needs_no_card():
    table = json.loads(json.dumps(TABLE))
    next(c for c in table["cells"] if (c["kind"], c["band"]) == ("compare", "long"))["pass"] = 0.4
    assert h.findings(_req("Compare page 1 and page 2"), table) == []
    assert [c.kind for c in h.findings(_req("Compare page 1 and page 20"), table)] == ["compare"]


def test_scanned_pages_are_a_note_under_an_answer_and_a_card_before_a_summary():
    q = h.findings(_req("When is the rent due?", scanned=[4, 7]))
    assert [(c.kind, c.note_only) for c in q] == [("scanned", True)]
    d = h.decide(q)
    assert d.card is None and d.notes[0]["text"] == "Pages 4 and 7 are pictures of text and weren't read for this answer."
    s = h.decide(h.findings(_req("Summarize this", mode="summary", scanned=[3], chars=4000, pages=3)))
    assert s.card.kind == "scanned" and s.card.text.startswith("Page 3 of lease.pdf is a picture of text.")


def test_a_long_summary_warns_only_when_it_is_slow_here():
    slow = h.check(_req("Summarize this", mode="long_summary", facts={"parts": 6, "estimate_s": 430.0}))
    assert slow.kind == "long" and "about 7 min 10 s" in slow.text and slow.options == ["go_ahead"]
    assert h.check(_req("Summarize this", mode="long_summary", facts={"parts": 6, "estimate_s": 14.0})) is None
    assert h.check(_req("Summarize this", mode="long_summary", facts={"parts": 6, "estimate_s": None})) is None


def test_a_blurry_picture_warns_only_when_words_are_to_be_read():
    pic = {"blur": TABLE["thresholds"]["blur_variance"] - 1}
    assert h.check(_req("What does this letter say?", mode="picture", picture=pic)).kind == "blurry"
    assert h.check(_req("Is this a nice sunset?", mode="picture", picture=pic)) is None
    assert h.check(_req("What does this letter say?", mode="picture", picture={"blur": 712.0})) is None


def test_with_ai_off_no_model_warning_applies():
    for r in (_req("What is the total of the rent and the deposit?", scanned=[3], ai_on=False),
              _req("Summarize this", mode="long_summary", facts={"parts": 6, "estimate_s": 900.0}, ai_on=False),
              _req("What does this letter say?", mode="picture", picture={"blur": 1.0}, ai_on=False)):
        assert h.findings(r) == []


# ─── Not nagging ─────────────────────────────────────────────────────────
CALC_Q = "What is the total of the rent and the deposit?"


def test_a_kind_shows_once_per_chat_then_goes_ahead():
    shown: set[str] = set()
    cards = []
    for _ in range(5):
        d = h.decide(h.findings(_req(CALC_Q)), shown=shown)
        if d.card:
            shown.add(d.card.kind)
        cards.append(d.card.kind if d.card else None)
    assert cards == ["calc", None, None, None, None]
    # ... and the arithmetic note still goes under each later answer.
    assert h.decide(h.findings(_req(CALC_Q)), shown=shown).notes[0]["code"] == "heads_up_calc"


def test_dont_warn_me_and_the_master_switch_keep_only_the_notes_that_guard_a_wrong_answer():
    rewrite = h.findings(_req("Rewrite this in plain English"))
    assert h.decide(rewrite, skip=["rewrite"]) == h.Decision(card=None, notes=[])
    assert h.decide(rewrite, off=True) == h.Decision(card=None, notes=[])
    calc_f = h.findings(_req(CALC_Q))
    assert [n["code"] for n in h.decide(calc_f, skip=["calc"]).notes] == ["heads_up_calc"]
    assert [n["code"] for n in h.decide(calc_f, off=True).notes] == ["heads_up_calc"]


def test_a_pick_on_the_card_answers_without_another_card():
    many = h.findings(_req(CALC_Q, scanned=[2], sample=next(c["text"] for c in CASES["language"] if c["lang"] == "pl")))
    assert [c.kind for c in many] == ["scanned", "language", "calc"]
    # Scanned pages are a note under a question's answer, so the card is the
    # next kind; its pick leaves the notes.
    assert h.decide(many).card.kind == "language"
    after = h.decide(many, chosen=("language", "try_anyway"))
    assert after.card is None and [n["code"] for n in after.notes] == ["heads_up_scanned", "heads_up_calc"]
    assert h.decide(h.findings(_req(CALC_Q)), chosen=("calc", "calculate")).notes == []
    assert h.decide(h.findings(_req("Rewrite this in plain English")),
                    chosen=("rewrite", "try_anyway")).notes[0]["code"] == "heads_up_rewrite"


def test_never_more_than_one_card_per_kind_per_chat_over_a_scripted_chat():
    """The scripted check from E-HEADSUP: every question type, twice, in one chat."""
    script = [CALC_Q, "Rewrite this in plain English", "Summarize this", "When is the rent due?",
              "How many days between move-in and the inspection?", "Translate the whole lease into Spanish",
              CALC_Q, "Does section 2 contradict section 9?", "Summarize this"]
    shown: set[str] = set()
    counts: dict[str, int] = {}
    for q in script:
        mode = "long_summary" if q == "Summarize this" else "question"
        r = _req(q, mode=mode, scanned=[3], facts={"parts": 6, "estimate_s": 400.0})
        d = h.decide(h.findings(r), shown=shown)
        if d.card:
            shown.add(d.card.kind)
            counts[d.card.kind] = counts.get(d.card.kind, 0) + 1
    assert counts and max(counts.values()) == 1


# ─── Calculate exactly ───────────────────────────────────────────────────
def test_calculate_exactly_adds_only_numbers_their_page_shows():
    c = calc.finish("- $1,200 [p. 1] rent\n- $1,500 [p. 2] deposit\n- $999 [p. 2] made up\n- 5 [p. 9] no page",
                    _given(GIVEN), "total?", ["lease.pdf"], "sum")
    assert c.content.startswith("**Total: $2,700 (1,200 + 1,500 = 2,700)**")
    assert "$999" not in c.content and [n["code"] for n in c.notes] == ["calc_worked", "calc_dropped"]
    assert c.cited == ["p. 1", "p. 2"] and all(s["cited"] for s in c.sources)


def test_calculate_exactly_is_decimal_not_floating_point():
    given = _given([("p. 1", "Fees of 0.10 and 0.20 apply."), ("p. 2", "A third fee is 0.005.")])
    c = calc.finish("- 0.10 [p. 1] fee\n- 0.20 [p. 1] fee", given, "total?", ["x"], "sum")
    assert c.content.startswith("**Total: 0.30 ")
    avg = calc.compute("average", [calc.Item("1", "p", "", value=Decimal("1")),
                                   calc.Item("2", "p", "", value=Decimal("2")),
                                   calc.Item("2", "p", "", value=Decimal("2"))])
    assert avg.text.startswith("Average: 1.67 ")       # 1.666… rounded half up


def test_days_between_two_dates_their_page_shows():
    c = calc.finish("- March 1, 2026 [p. 2] move in\n- April 15, 2026 [p. 2] inspection",
                    _given(GIVEN), "days?", ["lease.pdf"], "days")
    assert c.content.startswith("**45 days from March 1, 2026 to April 15, 2026**")
    off = calc.finish("- March 2, 2026 [p. 2] move in\n- April 15, 2026 [p. 2] inspection",
                      _given(GIVEN), "days?", ["lease.pdf"], "days")
    assert off.content.startswith("That needs exactly two dates.")


def test_a_model_that_answers_instead_of_listing_gets_couldnt_find():
    c = calc.finish("The total is 2,700.", _given(GIVEN), "total?", ["lease.pdf"], "sum")
    assert c.content == qa.NOT_FOUND and not c.found


def test_the_listing_prompt_keeps_the_passages_as_data():
    p = calc.build_prompt("total?", _given(GIVEN), ["lease.pdf"], "sum", nonce="abcd")
    assert "Don't calculate anything" in p["rules"] and "<<<PASSAGES abcd" in p["user"]


# ─── Pictures ────────────────────────────────────────────────────────────
def test_blur_variance_is_the_laplacians():
    for case in CASES["blur"]:
        v = h.blur_variance(case["grey"])
        assert (None if v == float("inf") else round(v, 6)) == case["variance"]
