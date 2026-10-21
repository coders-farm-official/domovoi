"""Calculate exactly, against its adversarial set (domovoi/doc_qa/calc.py; review round 1).

Every case's expected answer in ``calc_cases.json`` was written by hand
(``scripts/dev/calc_cases.py``): a result worked out on paper, or the plain
refusal when a number's sign, scale, separators, unit or a date's order
can't be read with certainty. The app's DocCalc.kt runs the same set
(DocCalcAdversarialTest).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "domovoi" / "doc_qa" / "calc_cases.json").read_text(encoding="utf-8"))["cases"]
sys.path.insert(0, str(ROOT / "scripts" / "dev"))
import calc_cases  # noqa: E402


def test_the_set_is_current_and_large_enough():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "dev" / "calc_cases.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(CASES) >= 40
    kinds = " ".join(c["name"] for c in CASES)
    for word in ("minus", "brackets", "million", "Spanish", "unknown language", "percent", "twice", "days"):
        assert word in kinds, word


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_each_case_gives_its_hand_written_answer(case):
    head, notes = calc_cases.run_python(case)
    assert (head, notes) == (case["head"], case["notes"])


def test_nothing_unsure_is_ever_labelled_worked_out_by_the_app():
    for case in CASES:
        head, notes = calc_cases.run_python(case)
        if "calc_worked" in notes:
            assert head.startswith("**"), case["name"]
        if head.startswith("I can't be sure") or head.startswith("These numbers are in"):
            assert "calc_worked" not in notes, case["name"]


# ─── The review's sets (round 2): a wrong number is never acceptable ────
REVIEW = json.loads((ROOT / "domovoi" / "doc_qa" / "calc_cases_review.json").read_text(encoding="utf-8"))["cases"]


def _parsed(head: str):
    """(kind, unit, value) of a result line, or the days line; as the review
    compared them."""
    import re
    from decimal import Decimal

    h = head.strip("*")
    m = re.match(r"(Total|Average|Difference): (\S+)", h)
    if m:
        tok = m.group(2)
        unit = "%" if tok.endswith("%") else next((c for c in tok if c in "$€£"), "")
        num = re.sub(r"[^\d.\-−()]", "", tok).replace("−", "-")
        neg = num.startswith("(") or num.startswith("-")
        try:
            v = Decimal(num.strip("()-")) * (-1 if neg else 1)
        except Exception:  # noqa: BLE001
            return (m.group(1), unit, None)
        return (m.group(1), unit, v)
    m = re.match(r"([\d,]+ days? from .+?\d{4} to .+?\d{4})", h)
    return ("days", m.group(1)) if m else ("other", h)


def test_the_review_sets_never_get_a_wrong_number():
    wrong, correct, refused = [], 0, 0
    for case in REVIEW:
        head, notes = calc_cases.run_python(case)
        if "calc_worked" not in notes:
            refused += 1
        elif case["truth"] is not None and _parsed(head) == _parsed(case["truth"]):
            correct += 1
        else:
            wrong.append(f"{case['name']}: {head} (truth {case['truth']})")
    assert wrong == [], "\n".join(wrong)
    assert len(REVIEW) == 135 and correct >= 40


def test_the_review_sets_record_what_the_server_gives():
    """calc_cases_review.json holds the server's head and notes for each case;
    the phone's DocCalcAdversarialTest must give the same."""
    for case in REVIEW:
        assert calc_cases.run_python(case) == (case["head"], case["notes"]), case["name"]
