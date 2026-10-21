"""Write domovoi/doc_qa/headsup_cases.json: the heads-up vectors both places are held to.

    python scripts/dev/headsup_cases.py          # rewrite the file from the Python rules
    python scripts/dev/headsup_cases.py --check  # exit 1 if the file is out of date

The inputs are written here by hand; the expected outputs are what the
Python rules (domovoi/doc_qa/headsup.py, calc.py) give. The server's tests
(domovoi/tests/test_doc_headsup.py) and the app's (DocHeadsUpSharedCasesTest.kt)
both read the file, so a rule changed on one side fails the other's tests
until it changes there too. All text is made up for these tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from domovoi.doc_qa import answer as qa  # noqa: E402
from domovoi.doc_qa import calc  # noqa: E402
from domovoi.doc_qa import headsup as h  # noqa: E402
from domovoi.doc_qa.text import Passage  # noqa: E402

OUT = ROOT / "domovoi" / "doc_qa" / "headsup_cases.json"

QUESTIONS = [
    "What is the total of the rent and the deposit?",
    "What's the total due at signing?",
    "How many days between move-in and the first inspection?",
    "How many days do I have to return the keys?",
    "What is the average monthly bill?",
    "What percentage of the deposit is kept for cleaning?",
    "What's the difference between the rent in 2025 and 2026?",
    "What's the difference between plan A and plan B?",
    "How much more is the deposit than the rent?",
    "Rewrite this in plain English",
    "Translate the whole lease into Spanish",
    "Proofread the document",
    "Rewrite the second paragraph",
    "Make it more formal",
    "Fix the grammar",
    "Does section 2 contradict section 9?",
    "Compare page 3 and p. 20",
    "Is the deposit amount consistent throughout the contract?",
    "Find every mention of the landlord",
    "When is the rent due?",
    "Who pays for repairs?",
    "Summarize this",
    "¿Cuándo se paga la renta?",
    "Add up the amounts in the table",
    "How much do I pay altogether?",
]

TEXTS = {
    "en": "The tenant pays the rent on the first day of each month. Payments are made by bank transfer, and the "
          "landlord will send a receipt for each one. If the payment is late, a fee of five percent of the rent "
          "for that month is charged, and it must be paid with the rent.",
    "es": "El arrendatario pagará la renta el primer día de cada mes. Los pagos se harán por transferencia y no "
          "se aceptan pagos en efectivo. Si el pago llega tarde, se cobrará una multa del cinco por ciento sobre "
          "el monto de la renta para el mes.",
    "pl": "Najemca płaci czynsz pierwszego dnia każdego miesiąca. Płatności są przyjmowane tylko przelewem, nie "
          "jest możliwa płatność gotówką. Jeśli płatność się spóźni, to naliczana jest opłata, która wynosi pięć "
          "procent czynszu za ten miesiąc oraz jest to także koszt.",
    "fr": "Le locataire paie le loyer le premier jour de chaque mois. Les paiements sont faits par virement et il "
          "ne peut pas payer en espèces. Si le paiement est en retard, des frais de cinq pour cent du loyer sont "
          "ajoutés pour ce mois et ils sont dus avec le loyer.",
    "de": "Der Mieter zahlt die Miete am ersten Tag jedes Monats. Die Zahlungen werden per Überweisung gemacht und "
          "es ist nicht möglich, bar zu zahlen. Wenn die Zahlung zu spät ist, wird eine Gebühr von fünf Prozent "
          "der Miete für den Monat erhoben, die mit der Miete zu zahlen ist.",
    "short": "Rent is due on the first.",
    "mixed": "The tenant pays el arrendatario the rent la renta on the first day el primer día of each month de "
             "cada mes and the landlord y el propietario sends a receipt una factura for each one para cada pago.",
}

LOOKUPS = [
    ("sum", ["The total due at signing is $2,700, which covers the first month and the deposit."]),
    ("sum", ["The monthly rent is $1,200.", "The security deposit is $1,500."]),
    ("average", ["Column totals for Budget: Amount: total 1,320, average 110, lowest 80 (12 numbers)"]),
    ("average", ["January 80, February 95, March 140."]),
    ("percent", ["Up to 10% of the deposit may be kept for cleaning."]),
    ("percent", ["The deposit is $1,500 and cleaning costs $150."]),
    ("days", ["Move in on March 1, 2026."]),
]

NUMBERS = ["The rent is $1,200.50 a month, up 4% from 1,150 in 2025; 30 days' notice.",
           "Section 3.2 says (40) and -12 and €9,000,000.", "No numbers here."]

DATES = ["Move in on March 1, 2026; the inspection is on 15 April 2026.",
         "Due 2026-05-01 or 05/02/2026, otherwise 1 de junio de 2026.", "Feb 30, 2026 is not a date."]


def _doc(name, chars, pages=None, words=0, scanned=(), sample=""):
    return {"name": name, "chars": chars, "pages": pages, "words": words, "scanned_pages": list(scanned),
            "sample": sample}


LEASE_GIVEN = [
    {"label": "p. 1", "text": "The monthly rent is $1,200, due on the first day of each month."},
    {"label": "p. 2", "text": "The security deposit is $1,500. Move in on March 1, 2026; inspection on April 15, 2026."},
]
LONG = 60000

REQUESTS = [
    ("ordinary question", {"mode": "question", "question": "When is the rent due?", "where": "server",
                           "docs": [_doc("lease.pdf", LONG, 30, 10000, sample=TEXTS["en"])], "given": LEASE_GIVEN}),
    ("calc with calculator", {"mode": "question", "question": "What is the total of the rent and the deposit?",
                              "where": "server", "can": ["calculate"],
                              "docs": [_doc("lease.pdf", LONG, 30, 10000, sample=TEXTS["en"])], "given": LEASE_GIVEN}),
    ("calc without calculator", {"mode": "question", "question": "What is the total of the rent and the deposit?",
                                 "where": "phone", "docs": [_doc("lease.pdf", LONG, 30, 10000)], "given": LEASE_GIVEN}),
    ("percent never has the calculator", {"mode": "question", "question": "What percent of the deposit is the rent?",
                                          "where": "server", "can": ["calculate"],
                                          "docs": [_doc("lease.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("a stated total is a lookup", {"mode": "question", "question": "What's the total due at signing?", "where": "server",
                                    "can": ["calculate"], "docs": [_doc("lease.pdf", LONG, 30)],
                                    "given": [{"label": "p. 4", "text": "The total due at signing is $2,700."},
                                              {"label": "p. 5", "text": "Rent $1,200; deposit $1,500."}]}),
    ("days", {"mode": "question", "question": "How many days between move-in and the inspection?", "where": "phone",
              "can": ["calculate"], "docs": [_doc("lease.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("compare long, measured weak", {"mode": "question", "question": "Does section 2 contradict section 9?",
                                     "where": "server", "cells": {"compare/long": 0.4},
                                     "docs": [_doc("contract.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("compare, one place missing, measured weak", {"mode": "question", "question": "Compare page 1 and page 20",
                                                   "where": "server", "cells": {"compare/long": 0.4},
                                                   "docs": [_doc("lease.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("compare, both found, measured weak", {"mode": "question", "question": "Compare page 1 and page 2",
                                            "where": "server", "cells": {"compare/long": 0.4},
                                            "docs": [_doc("lease.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("compare long", {"mode": "question", "question": "Does section 2 contradict section 9?", "where": "server",
                      "docs": [_doc("contract.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("compare short", {"mode": "question", "question": "Does section 2 contradict section 9?", "where": "server",
                       "docs": [_doc("note.txt", 3000)], "given": LEASE_GIVEN}),
    ("compare, both places found", {"mode": "question", "question": "Compare page 1 and page 2", "where": "server",
                                    "docs": [_doc("lease.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("compare, one place missing", {"mode": "question", "question": "Compare page 1 and page 20", "where": "server",
                                    "docs": [_doc("lease.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("rewrite long", {"mode": "question", "question": "Rewrite this in plain English", "where": "phone",
                      "docs": [_doc("lease.pdf", LONG, 30, 10000)], "given": LEASE_GIVEN}),
    ("rewrite long, words", {"mode": "question", "question": "Proofread the document", "where": "server",
                             "docs": [_doc("essay.docx", 48000, None, 8123)], "given": LEASE_GIVEN}),
    ("rewrite short", {"mode": "question", "question": "Rewrite this in plain English", "where": "phone",
                       "docs": [_doc("note.txt", 3000)], "given": LEASE_GIVEN}),
    ("rewrite a part", {"mode": "question", "question": "Rewrite the second paragraph", "where": "phone",
                        "docs": [_doc("lease.pdf", LONG, 30)], "given": LEASE_GIVEN}),
    ("scanned", {"mode": "question", "question": "When is the rent due?", "where": "server",
                 "docs": [_doc("lease.pdf", LONG, 30, scanned=[4, 7])], "given": LEASE_GIVEN}),
    ("scanned, one page", {"mode": "summary", "question": "Summarize this", "where": "phone",
                           "docs": [_doc("claim.pdf", 4000, 3, scanned=[3])], "given": LEASE_GIVEN}),
    ("scanned, many", {"mode": "question", "question": "When is the rent due?", "where": "server",
                       "docs": [_doc("scan.pdf", 100, 18, scanned=list(range(1, 19)))], "given": LEASE_GIVEN}),
    ("polish", {"mode": "question", "question": "Kiedy płacę czynsz?", "where": "server",
                "docs": [_doc("umowa.pdf", LONG, 30, sample=TEXTS["pl"])], "given": LEASE_GIVEN}),
    ("spanish is tested", {"mode": "question", "question": "¿Cuándo se paga la renta?", "where": "server",
                           "docs": [_doc("contrato.pdf", LONG, 30, sample=TEXTS["es"])], "given": LEASE_GIVEN}),
    ("scanned before language before calc", {"mode": "question", "question": "What is the total of the rent and the deposit?",
                                             "where": "server", "can": ["calculate"],
                                             "docs": [_doc("umowa.pdf", LONG, 30, scanned=[2], sample=TEXTS["pl"])],
                                             "given": LEASE_GIVEN}),
    ("long and slow", {"mode": "long_summary", "question": "Summarize this", "where": "server",
                       "docs": [_doc("contract.pdf", LONG, 30)], "facts": {"parts": 6, "estimate_s": 430.0}}),
    ("long and quick", {"mode": "long_summary", "question": "Summarize this", "where": "server",
                        "docs": [_doc("contract.pdf", LONG, 30)], "facts": {"parts": 6, "estimate_s": 14.0}}),
    ("long, home server faster", {"mode": "long_summary", "question": "Summarize this", "where": "phone",
                                  "can": ["home_server"], "docs": [_doc("contract.pdf", LONG, 30)],
                                  "facts": {"parts": 8, "estimate_s": 130.0, "home_server_s": 25.0}}),
    ("long, home server offer but cannot", {"mode": "long_summary", "question": "Summarize this", "where": "phone",
                                            "docs": [_doc("contract.pdf", LONG, 30)],
                                            "facts": {"parts": 8, "estimate_s": 130.0, "home_server_s": 25.0}}),
    ("long, unmeasured", {"mode": "long_summary", "question": "Summarize this", "where": "server",
                          "docs": [_doc("contract.pdf", LONG, 30)], "facts": {"parts": 6, "estimate_s": None}}),
    ("ai off", {"mode": "question", "question": "What is the total of the rent and the deposit?", "where": "server",
                "ai_on": False, "can": ["calculate"], "docs": [_doc("lease.pdf", LONG, 30, scanned=[3])],
                "given": LEASE_GIVEN}),
    ("blurry", {"mode": "picture", "question": "What does this letter say?", "where": "server",
                "picture": {"blur": 12.5}}),
    ("blurry, nothing to read", {"mode": "picture", "question": "Is this a nice sunset?", "where": "server",
                                 "picture": {"blur": 12.5}}),
    ("sharp", {"mode": "picture", "question": "Read me this receipt", "where": "server", "picture": {"blur": 900.0}}),
    ("handwritten", {"mode": "picture", "question": "What does the note say?", "where": "phone",
                     "picture": {"blur": 900.0, "handwritten": True}}),
]

DECIDES = [
    ("first kind is the card", "scanned before language before calc", {}),
    ("shown kind skipped, calc note stays", "calc with calculator", {"shown": ["calc"]}),
    ("silenced by skip", "rewrite long", {"skip": ["rewrite"]}),
    ("master switch off: notes only", "scanned before language before calc", {"off": True}),
    ("chosen calculate: no note", "calc with calculator", {"chosen": ["calc", "calculate"]}),
    ("chosen try anyway: the note", "calc with calculator", {"chosen": ["calc", "try_anyway"]}),
    ("chosen on rewrite: the after note", "rewrite long", {"chosen": ["rewrite", "try_anyway"]}),
    ("chosen scanned, then calc is a note", "scanned before language before calc", {"chosen": ["scanned", "go_ahead"]}),
    ("nothing found", "ordinary question", {}),
]

BLURS = [
    [[0, 0, 0, 0], [0, 255, 255, 0], [0, 255, 255, 0], [0, 0, 0, 0]],
    [[10, 10, 10, 10], [10, 10, 10, 10], [10, 10, 10, 10]],
    [[0, 255, 0, 255, 0], [255, 0, 255, 0, 255], [0, 255, 0, 255, 0], [255, 0, 255, 0, 255]],
    [[1, 2], [3, 4]],
]

CALCS = [
    ("sum", "- $1,200 [p. 1] monthly rent\n- $1,500 [p. 2] security deposit"),
    ("sum", "- $1,200 [p. 1] rent\n- $1,500 [p. 2] deposit\n- $999 [p. 2] made up\n- 5 [p. 9] no such page"),
    ("average", "1. 1,200 [p. 1] rent\n2. 1,500 [p. 2] deposit"),
    ("difference", "- $1,500 [p. 2] deposit\n- $1,200 [p. 1] rent"),
    ("difference", "- $1,500 [p. 2] deposit"),
    ("days", "- March 1, 2026 [p. 2] move in\n- April 15, 2026 [p. 2] inspection"),
    ("days", "- March 2, 2026 [p. 2] move in\n- April 15, 2026 [p. 2] inspection"),
    ("sum", "I couldn't find that in the document."),
    ("sum", "The total is 2,700."),
]


def _request(d: dict) -> h.Request:
    return h.Request(
        mode=d["mode"], question=d.get("question", ""), where=d.get("where", "server"),
        docs=[h.Doc(name=x["name"], chars=x["chars"], pages=x.get("pages"), words=x.get("words") or 0,
                    scanned_pages=x.get("scanned_pages") or [], sample=x.get("sample") or "") for x in d.get("docs", [])],
        given=[(g["label"], g["text"]) for g in d.get("given", [])], facts=d.get("facts") or {},
        picture=d.get("picture") or {}, can=frozenset(d.get("can") or []), ai_on=d.get("ai_on", True))


def table_for(req: dict) -> dict:
    """The table, with a request's "cells" ({"kind/band": pass}) measured
    differently: how a case shows a card the shipped table doesn't fire."""
    t = json.loads(json.dumps(h.load_table()))
    for key, value in (req.get("cells") or {}).items():
        kind, band = key.split("/")
        for c in t["cells"]:
            if c["kind"] == kind and c["band"] == band:
                c["pass"] = value
    return t


def _card(c: h.Card) -> dict:
    return {"kind": c.kind, "text": c.text, "options": c.options, "note": c.note, "op": c.op, "after": c.after,
            "note_only": c.note_only}


def _given(items: list[dict]) -> list[qa.Given]:
    return [qa.Given(Passage(doc=0, part=i, label=g["label"], text=g["text"], head=""), g["label"], 1.0)
            for i, g in enumerate(items)]


def build() -> dict:
    by_name = {name: req for name, req in REQUESTS}
    out = {
        "_note": "Written by scripts/dev/headsup_cases.py from the Python rules; read by "
                 "domovoi/tests/test_doc_headsup.py and DocHeadsUpSharedCasesTest.kt. Don't edit by hand.",
        "recognise": [{"question": q, "rewrite": h.rewrite_request(q), "compare": h.compare_request(q),
                       "calc": h.calc_request(q), "places": h.named_places(q)} for q in QUESTIONS],
        "language": [{"text": t, "lang": h.guess_language(t)} for t in TEXTS.values()],
        "lookup": [{"op": op, "texts": texts, "lookup": h.is_lookup(op, texts)} for op, texts in LOOKUPS],
        "numbers": [{"text": t, "numbers": h.numbers_in(t)} for t in NUMBERS],
        "dates": [{"text": t, "dates": [d.isoformat() for d in calc.dates_in(t)]} for t in DATES],
        "findings": [{"name": name, "request": req,
                      "cards": [_card(c) for c in h.findings(_request(req), table_for(req))]}
                     for name, req in REQUESTS],
        "decide": [],
        "blur": [{"grey": g, "variance": (None if h.blur_variance(g) == float("inf") else round(h.blur_variance(g), 6))}
                 for g in BLURS],
        "calc": [],
    }
    for name, req_name, kw in DECIDES:
        found = h.findings(_request(by_name[req_name]), table_for(by_name[req_name]))
        chosen = tuple(kw["chosen"]) if kw.get("chosen") else None
        dec = h.decide(found, shown=kw.get("shown", []), skip=kw.get("skip", []), off=kw.get("off", False),
                       chosen=chosen)
        out["decide"].append({"name": name, "request": req_name, "shown": kw.get("shown", []),
                              "skip": kw.get("skip", []), "off": kw.get("off", False),
                              "chosen": list(chosen) if chosen else None,
                              "card": dec.card.kind if dec.card else None, "notes": dec.notes})
    for op, reply in CALCS:
        given = _given(LEASE_GIVEN)
        checked = calc.finish(reply, given, "how much?", ["lease.pdf"], op)
        out["calc"].append({"op": op, "reply": reply, "given": LEASE_GIVEN, "content": checked.content,
                            "found": checked.found, "cited": checked.cited,
                            "notes": [n["code"] for n in checked.notes]})
    return out


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python scripts/dev/headsup_cases.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_bytes(text.encode("utf-8"))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
