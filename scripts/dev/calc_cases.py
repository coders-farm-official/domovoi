"""Write domovoi/doc_qa/calc_cases.json: the "calculate exactly" adversarial set (review round 1).

    python scripts/dev/calc_cases.py          # rewrite the file
    python scripts/dev/calc_cases.py --check  # exit 1 if it is out of date, or the Python disagrees

Unlike the other shared vectors, every expected answer here is written BY
HAND: a result worked out on paper, or the plain refusal the owner wants
when a number can't be read with certainty (signs, scales, European
separators, day/month order, mixed units, a number listed twice). The
server's tests (domovoi/tests/test_doc_calc.py) and the app's
(DocCalcAdversarialTest.kt) run every case and compare the answer's first
line and its notes. All text is made up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "domovoi" / "doc_qa" / "calc_cases.json"


def P(label: str, text: str, part: int | None = None) -> dict:
    return {"label": label, "text": text, **({"part": part} if part is not None else {})}


def C(name: str, op: str, lang: str | None, given: list[dict], reply: str, head: str, notes: list[str],
      nonce: str | None = None) -> dict:
    return {"name": name, "op": op, "lang": lang, "given": given, "reply": reply, "head": head, "notes": notes,
            "nonce": nonce}


W = ["calc_worked", "calc_only_found"]
NF = "I couldn't find the numbers for that in the document."
LEASE = [P("p. 1", "Monthly rent: $1,200. Late fee 5%."), P("p. 2", "Security deposit: $1,500. Repair credit: -$40.00.")]

CASES = [
    # ── signs ────────────────────────────────────────────────────────────
    C("plain sum", "sum", "en", LEASE, "- $1,200 [p. 1] rent\n- $1,500 [p. 2] deposit",
      "**Total: $2,700 (1,200 + 1,500 = 2,700), from the 2 entries I found**", W),
    C("minus before the currency", "sum", "en", LEASE, "- $1,200 [p. 1] rent\n- -$40.00 [p. 2] credit",
      "**Total: $1,160.00 (1,200.00 + (−40.00) = 1,160.00), from the 2 entries I found**", W),
    C("minus, no bullet", "sum", "en", LEASE, "$1,200 [p. 1] rent\n-$40.00 [p. 2] credit",
      "**Total: $1,160.00 (1,200.00 + (−40.00) = 1,160.00), from the 2 entries I found**", W),
    C("unicode minus", "sum", "en", [P("p. 12", "Balance −$40 after a credit. Rent $1,200.")],
      "- −$40 [p. 12] credit\n- $1,200 [p. 12] rent",
      "**Total: $1,160 ((−40) + 1,200 = 1,160), from the 2 entries I found**", W),
    C("minus after the currency", "sum", "en", [P("p. 1", "Credit $-40 applied. Rent $1,200.")],
      "- $-40 [p. 1] credit\n- $1,200 [p. 1] rent",
      "**Total: $1,160 ((−40) + 1,200 = 1,160), from the 2 entries I found**", W),
    C("accounting brackets", "sum", "en", [P("p. 9", "Refund (40.00) applied. Rent 1,200.00.")],
      "- 1,200.00 [p. 9] rent\n- (40.00) [p. 9] refund",
      "**Total: 1,160.00 (1,200.00 + (−40.00) = 1,160.00), from the 2 entries I found**", W),
    C("en dash: minus or range", "sum", "en", [P("p. 1", "Adjustment –40. Rent 1,200.")],
      "- –40 [p. 1] adjustment\n- 1,200 [p. 1] rent",
      "I can't be sure how to read –40 here, so I won't add it up.", ["calc_unsure"]),
    C("trailing minus", "sum", "en", [P("p. 1", "Adjustment 40- and rent 1,200.")],
      "- 40- [p. 1] adjustment\n- 1,200 [p. 1] rent",
      "I can't be sure how to read 40 here, so I won't add it up.", ["calc_unsure"]),
    C("a hyphen between numbers is no minus", "sum", "en", [P("p. 3", "Units 3-4 cost 200 and 300.")],
      "- 200 [p. 3] a\n- 300 [p. 3] b",
      "**Total: 500 (200 + 300 = 500), from the 2 entries I found**", W),
    # ── scales ───────────────────────────────────────────────────────────
    C("k suffix", "sum", "en", [P("p. 13", "Annual cost $1.2k, monthly $100.")],
      "- $1.2k [p. 13] annual\n- $100 [p. 13] monthly",
      "**Total: $1,300 (1,200 + 100 = 1,300), from the 2 entries I found**", W),
    C("million in words", "sum", "en", [P("p. 2", "The grant is $1.2 million and the loan $300,000.")],
      "- $1.2 million [p. 2] grant\n- $300,000 [p. 2] loan",
      "**Total: $1,500,000 (1,200,000 + 300,000 = 1,500,000), from the 2 entries I found**", W),
    C("thousand in words", "sum", "en", [P("p. 2", "About 3 thousand visitors came, and 500 more on Sunday.")],
      "- 3 thousand [p. 2] visitors\n- 500 [p. 2] Sunday",
      "**Total: 3,500 (3,000 + 500 = 3,500), from the 2 entries I found**", W),
    C("m: metres, minutes or million", "sum", "en", [P("p. 2", "Budget 5m for roads and 2 for signs.")],
      "- 5m [p. 2] roads\n- 2 [p. 2] signs",
      "I can't be sure how to read 5m here, so I won't add it up.", ["calc_unsure"]),
    C("the page's scale, not the bare digits", "sum", "en", [P("p. 2", "A gift of $2k and a fee of $50.")],
      "- $2 [p. 2] gift\n- $50 [p. 2] fee",
      NF, ["calc_dropped"]),
    # ── European formats ─────────────────────────────────────────────────
    C("Spanish thousands", "sum", "es", [P("p. 4", "Fianza: 1.200 €. Renta mensual: 950 €.")],
      "- 1.200 € [p. 4] fianza\n- 950 € [p. 4] renta",
      "**Total: €2,150 (1,200 + 950 = 2,150), from the 2 entries I found**", W),
    C("Spanish decimals", "sum", "es", [P("p. 4", "Gastos de comunidad: 1.234,56 €. Renta: 950 €.")],
      "- 1.234,56 € [p. 4] gastos\n- 950 € [p. 4] renta",
      "**Total: €2,184.56 (1,234.56 + 950.00 = 2,184.56), from the 2 entries I found**", W),
    C("German decimal comma", "sum", "de", [P("S. 2", "Die Gebühr ist 12,5 und der Zuschlag 7,5.")],
      "- 12,5 [S. 2] Gebühr\n- 7,5 [S. 2] Zuschlag",
      "**Total: 20.0 (12.5 + 7.5 = 20.0), from the 2 entries I found**", W),
    C("Spanish millions", "sum", "es", [P("p. 1", "El presupuesto es 1.200.000 € y la reserva 50.000 €.")],
      "- 1.200.000 € [p. 1] presupuesto\n- 50.000 € [p. 1] reserva",
      "**Total: €1,250,000 (1,200,000 + 50,000 = 1,250,000), from the 2 entries I found**", W),
    C("unknown language: 1.200 can't be read", "sum", None, [P("p. 1", "Fee 1.200 and 300.")],
      "- 1.200 [p. 1] fee\n- 300 [p. 1] other",
      "I can't be sure how to read 1.200 here, so I won't add it up.", ["calc_unsure"]),
    C("unknown language: 1,200 can't be read either", "sum", None, [P("p. 1", "Fee 1,200 and 300.")],
      "- 1,200 [p. 1] fee\n- 300 [p. 1] other",
      "I can't be sure how to read 1,200 here, so I won't add it up.", ["calc_unsure"]),
    C("unknown language, the document's own numbers decide", "sum", None,
      [P("p. 1", "Rent 1,200 and fee 1,234.56 per year.")], "- 1,200 [p. 1] rent\n- 1,234.56 [p. 1] fee",
      "**Total: 2,434.56 (1,200.00 + 1,234.56 = 2,434.56), from the 2 entries I found**", W),
    C("English 1.200 is one point two", "sum", "en", [P("p. 1", "Rates 1.200 and 2.300 apply.")],
      "- 1.200 [p. 1] a\n- 2.300 [p. 1] b",
      "**Total: 3.500 (1.200 + 2.300 = 3.500), from the 2 entries I found**", W),
    C("a comma decimal in an English document", "sum", "en", [P("p. 1", "Fee 12,50 and 3.")],
      "- 12,50 [p. 1] fee\n- 3 [p. 1] other",
      "I can't be sure how to read 12,50 here, so I won't add it up.", ["calc_unsure"]),
    C("commas grouping in a Spanish document", "sum", "es", [P("p. 1", "Total 1,200,000 y 5.")],
      "- 1,200,000 [p. 1] a\n- 5 [p. 1] b",
      "I can't be sure how to read 1,200,000 here, so I won't add it up.", ["calc_unsure"]),
    C("Swiss apostrophe", "sum", None, [P("p. 1", "Miete 1'200 und Nebenkosten 300.")],
      "- 1'200 [p. 1] Miete\n- 300 [p. 1] Nebenkosten",
      "**Total: 1,500 (1,200 + 300 = 1,500), from the 2 entries I found**", W),
    # ── units ────────────────────────────────────────────────────────────
    C("dollars and percent", "sum", "en", LEASE, "- $1,200 [p. 1] rent\n- 5% [p. 1] late fee",
      "These numbers are in $ and %, so I won't add them up.", []),
    C("dollars and euros", "sum", "en", [P("p. 8", "Deposit $1,500. Agency fee €300.")],
      "- $1,500 [p. 8] deposit\n- €300 [p. 8] agency fee",
      "These numbers are in $ and €, so I won't add them up.", []),
    C("dollars and plain numbers", "sum", "en", [P("p. 10", "Clause 3: the rent is $900. See clause 12.")],
      "- $900 [p. 10] rent\n- 12 [p. 10] other",
      "These numbers are in plain numbers and $, so I won't add them up.", []),
    C("the page's unit, listed without it", "sum", "en", LEASE, "- 1,200 [p. 1] rent\n- $1,500 [p. 2] deposit",
      "**Total: $2,700 (1,200 + 1,500 = 2,700), from the 2 entries I found**", W),
    C("a unit the page doesn't have", "sum", "en", [P("p. 1", "Fee €1,500 and rent €1,200.")],
      "- $1,500 [p. 1] fee\n- €1,200 [p. 1] rent", NF, ["calc_dropped"]),
    C("percent with percent", "sum", "en", [P("p. 1", "Tax 5% and service 2.5% are added.")],
      "- 5% [p. 1] tax\n- 2.5% [p. 1] service",
      "**Total: 7.5% (5.0 + 2.5 = 7.5), from the 2 entries I found**", W),
    # ── whole tokens, places, duplicates ─────────────────────────────────
    C("3.75 is 3.75, not the 75 elsewhere", "sum", "en", [P("p. 5", "Fee 3.75 per unit. Units: 12."),
                                                          P("p. 6", "Term 75 days.")],
      "- 3.75 [p. 6] fee\n- 12 [p. 5] units", NF, ["calc_dropped"]),
    C("an unbulleted decimal", "sum", "en", [P("p. 5", "Fee 3.75 per unit. Term 75 days. Units: 12.")],
      "3.75 [p. 5] fee\n12 [p. 5] units",
      "**Total: 15.75 (3.75 + 12.00 = 15.75), from the 2 entries I found**", W),
    C("three decimals kept", "sum", "en", [P("p. 11", "Rate 0.125 and rate 0.25.")],
      "- 0.125 [p. 11] a\n- 0.25 [p. 11] b",
      "**Total: 0.375 (0.125 + 0.250 = 0.375), from the 2 entries I found**", W),
    C("listed twice, there once", "sum", "en", [P("p. 1", "Monthly rent: $1,200."), P("p. 3", "Deposit $1,500.")],
      "- $1,500 [p. 3] deposit\n- $1,500 [p. 3] deposit again\n- $1,200 [p. 1] rent",
      "**Total: $2,700 (1,500 + 1,200 = 2,700), from the 2 entries I found**", W + ["calc_repeated"]),
    C("there twice, listed twice", "sum", "en", [P("p. 1", "Two payments of $500 and $500 are due.")],
      "- $500 [p. 1] first\n- $500 [p. 1] second",
      "**Total: $1,000 (500 + 500 = 1,000), from the 2 entries I found**", W),
    C("one sentence in two overlapping passages", "sum", "en",
      [P("p. 3", "The deposit is $1,500. Pets are allowed.", part=2),
       P("p. 3", "Pets are allowed. Parking is $50.", part=2)],
      "- $1,500 [p. 3] deposit\n- $50 [p. 3] parking\n- $1,500 [p. 3] deposit again",
      "**Total: $1,550 (1,500 + 50 = 1,550), from the 2 entries I found**", W + ["calc_repeated"]),
    C("an invented number left out", "sum", "en", [P("p. 1", "Rent $1,200 and water $40."), P("p. 3", "Deposit $1,500.")],
      "- $1,300 [p. 1] rent\n- $1,500 [p. 3] deposit\n- $40 [p. 1] water",
      "**Total: $1,540 (1,500 + 40 = 1,540), from the 2 entries I found**", W + ["calc_dropped"]),
    C("a page that doesn't exist", "sum", "en", LEASE, "- $1,200 [p. 9] rent\n- $1,500 [p. 2] deposit",
      NF, ["calc_dropped"]),
    C("a time is no number", "sum", "en", [P("p. 1", "Rent $1,200 is due at 9:30. Deposit $1,500.")],
      "- 30 [p. 1] minutes\n- $1,200 [p. 1] rent\n- $1,500 [p. 1] deposit",
      "**Total: $2,700 (1,200 + 1,500 = 2,700), from the 3 entries I found**".replace("3 entries", "2 entries"),
      W + ["calc_dropped"]),
    C("a code is no number", "sum", "en", [P("p. 12", "Error E4 means 2 filters and 3 hoses.")],
      "- 4 [p. 12] error\n- 2 [p. 12] filters\n- 3 [p. 12] hoses",
      "**Total: 5 (2 + 3 = 5), from the 2 entries I found**", W + ["calc_dropped"]),
    # ── average and difference ───────────────────────────────────────────
    C("average to two places", "average", "en", [P("p. 1", "Readings 1, 2 and 2 were taken.")],
      "- 1 [p. 1] a\n- 2 [p. 1] b\n- 2 [p. 1] c",
      "**Average: 1.67 ((1 + 2 + 2) ÷ 3 = 1.67), from the 3 entries I found**", W),
    C("average of cents", "average", "en", [P("p. 1", "Bills of $10.50 and $11 came.")],
      "- $10.50 [p. 1] a\n- $11 [p. 1] b",
      "**Average: $10.75 ((10.50 + 11.00) ÷ 2 = 10.75), from the 2 entries I found**", W),
    C("difference", "difference", "en", LEASE, "- $1,500 [p. 2] deposit\n- $1,200 [p. 1] rent",
      "**Difference: $300 (1,500 − 1,200 = 300)**", W),
    C("difference of one number", "difference", "en", LEASE, "- $1,500 [p. 2] deposit",
      "That needs exactly two numbers.", []),
    # ── days between two dates ───────────────────────────────────────────
    C("days, months in words", "days", "en", [P("p. 7", "Move-in is March 1, 2026. Inspection on April 15, 2026.")],
      "- March 1, 2026 [p. 7] move in\n- April 15, 2026 [p. 7] inspection",
      "**45 days from March 1, 2026 to April 15, 2026**", ["calc_worked"]),
    C("days, English 01/02 could be either", "days", "en",
      [P("p. 6", "The lease starts 01/02/2026 and the inspection is 01/03/2026.")],
      "- 01/02/2026 [p. 6] start\n- 01/03/2026 [p. 6] inspection",
      "I can't be sure whether 01/02/2026 is January 2 or February 1 here, so I won't count the days.",
      ["calc_unsure"]),
    C("days, Spanish 01/02 is 1 February", "days", "es",
      [P("p. 6", "El contrato empieza el 01/02/2026 y la inspección es el 01/03/2026.")],
      "- 01/02/2026 [p. 6] inicio\n- 01/03/2026 [p. 6] inspección",
      "**28 days from February 1, 2026 to March 1, 2026**", ["calc_worked"]),
    C("days, the document's own dates decide", "days", "en",
      [P("p. 2", "Signed 15/01/2026, keys handed over 01/02/2026.")],
      "- 15/01/2026 [p. 2] signed\n- 01/02/2026 [p. 2] keys",
      "**17 days from January 15, 2026 to February 1, 2026**", ["calc_worked"]),
    C("days, dotted dates are day first", "days", None, [P("p. 2", "Von 01.02.2026 bis 01.03.2026.")],
      "- 01.02.2026 [p. 2] von\n- 01.03.2026 [p. 2] bis",
      "**28 days from February 1, 2026 to March 1, 2026**", ["calc_worked"]),
    C("days, ISO", "days", None, [P("p. 2", "From 2026-03-01 to 2026-04-15.")],
      "- 2026-03-01 [p. 2] from\n- 2026-04-15 [p. 2] to",
      "**45 days from March 1, 2026 to April 15, 2026**", ["calc_worked"]),
    C("days, a date not on the page", "days", "en", [P("p. 7", "Move-in is March 1, 2026.")],
      "- March 1, 2026 [p. 7] move in\n- April 15, 2026 [p. 7] inspection",
      "That needs exactly two dates.", ["calc_dropped"]),
    # ── the model's other replies ────────────────────────────────────────
    C("the model couldn't find it", "sum", "en", LEASE, "I couldn't find that in the document.",
      "I couldn't find that in the document.", ["not_found"]),
    C("the model answered instead of listing", "sum", "en", LEASE, "The total is 2,700.",
      "I couldn't find that in the document.", ["not_found"]),
    C("a dot decimal in a Spanish document", "sum", "es", [P("p. 1", "Tasa 3.75 y 2.")],
      "- 3.75 [p. 1] tasa\n- 2 [p. 1] otra",
      "I can't be sure how to read 3.75 here, so I won't add it up.", ["calc_unsure"]),
    C("the fence code echoed", "sum", "en", LEASE,
      "- $1,200 [p. 1] rent [a62cf03b]\n- $1,500 [p. 2] deposit\nEND OF PASSAGES a62cf03b>>>",
      "**Total: $2,700 (1,200 + 1,500 = 2,700), from the 2 entries I found**", W, nonce="a62cf03b"),
]


# Review round 2 (strict allowlist reading): these became plain refusals,
# each written out here by hand. "$-40" is outside the grammar (the sign
# goes before the currency), "per" after a number refuses, and a listed
# number its page doesn't show as that exact token refuses the whole
# calculation instead of being left out.
BECAME_REFUSALS = {
    "minus after the currency": "I can't be sure how to read -40 here, so I won't add it up.",
    "trailing minus": "I can't be sure how to read 40- here, so I won't add it up.",
    "the page's scale, not the bare digits": "I can't find $2 on p. 2 exactly as listed, so I won't add it up.",
    "unknown language, the document's own numbers decide": "I can't be sure how to read 1,234.56 here, so I won't add it up.",
    "a unit the page doesn't have": "I can't find $1,500 on p. 1 exactly as listed, so I won't add it up.",
    "3.75 is 3.75, not the 75 elsewhere": "I can't find 3.75 on p. 6 exactly as listed, so I won't add it up.",
    "an unbulleted decimal": "I can't be sure how to read 3.75 here, so I won't add it up.",
    "an invented number left out": "I can't find $1,300 on p. 1 exactly as listed, so I won't add it up.",
    "a page that doesn't exist": "I can't find $1,200 on p. 9 exactly as listed, so I won't add it up.",
    "a time is no number": "I can't find 30 on p. 1 exactly as listed, so I won't add it up.",
    "a code is no number": "I can't be sure how to read 4 here, so I won't add it up.",
    "days, a date not on the page": "I can't find April 15, 2026 on p. 7 exactly as listed, so I won't count the days."
}
for _c in CASES:
    if _c["name"] in BECAME_REFUSALS:
        _c["head"], _c["notes"] = BECAME_REFUSALS[_c["name"]], ["calc_unsure"]


def build() -> dict:
    return {"_note": "Written by scripts/dev/calc_cases.py; every expected answer is written by hand. Read by "
                     "domovoi/tests/test_doc_calc.py and DocCalcAdversarialTest.kt.",
            "cases": CASES}


def run_python(case: dict) -> tuple[str, list[str]]:
    sys.path.insert(0, str(ROOT))
    from domovoi.doc_qa import answer as qa
    from domovoi.doc_qa import calc
    from domovoi.doc_qa.text import Passage

    given = [qa.Given(Passage(0, g.get("part", i), g["label"], g["text"], ""), g["label"], 1.0)
             for i, g in enumerate(case["given"])]
    ctx = calc.context([g["text"] for g in case["given"]], case["lang"])
    c = calc.finish(case["reply"], given, "q", ["doc.pdf"], case["op"], case["nonce"], {0: ctx})
    return c.content.split("\n\n")[0], [n["code"] for n in c.notes]


def main(argv: list[str]) -> int:
    text = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if "--check" in argv:
        bad = 0
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print("calc_cases.json is out of date", file=sys.stderr)
            bad += 1
        for case in CASES:
            got = run_python(case)
            if got != (case["head"], case["notes"]):
                print(f"DIFF {case['name']}:\n  want {case['head']!r} {case['notes']}\n  got  {got[0]!r} {got[1]}")
                bad += 1
        print(f"{len(CASES)} cases, {bad} problems")
        return 1 if bad else 0
    OUT.write_bytes(text.encode("utf-8"))
    print(f"wrote {OUT.relative_to(ROOT)} ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
