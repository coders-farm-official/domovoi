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
