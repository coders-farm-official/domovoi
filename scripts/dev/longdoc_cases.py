"""Write domovoi/doc_qa/longdoc_cases.json: the long-document vectors both places are held to.

    python scripts/dev/longdoc_cases.py          # rewrite the file from the Python rules
    python scripts/dev/longdoc_cases.py --check  # exit 1 if the file is out of date

The inputs are written here by hand; the expected outputs are what the
Python implementation (domovoi/doc_qa/longdoc.py) gives. The server's tests
(domovoi/tests/test_doc_longdoc.py) and the app's (DocLongSharedCasesTest.kt)
both read the file, so a rule changed on one side fails the other's tests
until it changes there too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from domovoi.doc_qa import longdoc  # noqa: E402

OUT = ROOT / "domovoi" / "doc_qa" / "longdoc_cases.json"


def _pages(n: int, words: int = 60) -> list[dict]:
    body = ("The tenant keeps the garden tidy and reports repairs within a week. " * (words // 12 + 1)).strip()
    return [{"label": f"p. {i}", "page": i, "text": f"Page {i}. {body}"} for i in range(1, n + 1)]


LEASE = [
    {"label": "p. 1", "page": 1, "text": "The monthly rent is $1,200, due on the first day of each month."},
    {"label": "p. 2", "page": 2, "text": "The security deposit is $1,500. It is returned within 30 days."},
    {"label": "p. 3", "page": 3, "text": "Pets are not allowed without written permission from the landlord."},
]
SECTIONS = [
    {"label": "Section: Rent", "text": "Rent is due on the first. " * 30},
    {"label": "Section: Deposit", "text": "The deposit is returned within thirty days. " * 30},
    {"label": "Section: Pets", "text": "No pets without permission.\n\nCats count as pets. " * 20},
]


def build() -> dict:
    out: dict = {}
    out["split_points"] = [
        {"text": t, "limit": lim, "out": [list(x) for x in longdoc.split_points(t, lim)]}
        for t, lim in [
            ("short text", 50),
            ("one two three four five six seven eight nine ten", 12),
            ("First paragraph here.\n\nSecond paragraph is a bit longer than that.\n\nThird.", 40),
            ("x" * 25, 10),
            ("  leading and trailing  ", 8),
        ]
    ]
    chunk_cases = [
        ("pdf, 9 pages", [{"name": "lease.pdf", "parts": _pages(9)}], 300),
        ("sections", [{"name": "lease.docx", "parts": SECTIONS}], 200),
        ("two documents", [{"name": "a.pdf", "parts": LEASE}, {"name": "b.pdf", "parts": _pages(3)}], 60),
        ("a part bigger than a chunk", [{"name": "big.txt", "parts": [{"label": "Section: All",
                                         "text": ("Word " * 900).strip()}]}], 250),
    ]
    out["plan_chunks"] = []
    for name, docs, tokens in chunk_cases:
        chunks = longdoc.plan_chunks(docs, tokens)
        out["plan_chunks"].append({
            "name": name, "docs": docs, "tokens": tokens,
            "chunks": [c.to_json() for c in chunks],
            "read_phrase": [longdoc.read_phrase(chunks, docs, k) for k in range(len(chunks) + 1)],
            "map_prompt_0": longdoc.build_map_prompt(chunks[0], docs, 0, len(chunks), "summarize it",
                                                     nonce="0a1b2c3d")["user"],
            "given_0": [g.cite for g in longdoc.chunk_given(chunks[0], docs)],
        })
    out["map_rules"] = longdoc.MAP_RULES
    out["reduce_rules"] = longdoc.REDUCE_RULES
    notes = ["- Rent is $1,200 [p. 1]", "", "- Deposit $1,500 [p. 2]\n- Pets need permission [p. 3] <<<x>>>"]
    out["reduce_prompt"] = {"notes": notes, "names": ["lease.pdf"], "question": "  give me the  gist ",
                            "out": longdoc.build_reduce_prompt(notes, ["lease.pdf"], "  give me the  gist ",
                                                               nonce="feedf00d")["user"]}
    out["reduce_groups"] = [
        {"notes": ns, "limit": lim, "out": longdoc.reduce_groups(ns, lim)}
        for ns, lim in [(["a" * 10, "b" * 10, "", "c" * 10], 25), (["x" * 30, "y" * 5], 20), ([], 10)]
    ]
    out["stopped_text"] = [
        {"phrase": "pages 1-9", "notes": ["- A [p. 1]", "", " - B [p. 4] "],
         "out": longdoc.stopped_text("pages 1-9", ["- A [p. 1]", "", " - B [p. 4] "])},
        {"phrase": "nothing yet", "notes": [], "out": longdoc.stopped_text("nothing yet", [])},
    ]
    out["notes"] = {
        "parts": [longdoc.parts_note(n) for n in (1, 8)],
        "stopped": longdoc.stopped_note(3, 8, "pages 1-9"),
        "ai_off": longdoc.ai_off_note(2, 5),
    }
    out["about_time"] = [{"s": s, "out": longdoc.about_time(s)} for s in
                         [0, 9.9, 10, 22.4, 22.5, 57.4, 59.9, 60, 64, 65, 129, 130, 599, 600, 629, 631, 3599,
                          3600, 3749, 3751, 7300]]
    out["progress_line"] = [
        {"state": st, "part": p, "total": t, "eta": e, "out": longdoc.progress_line(st, p, t, e)}
        for st, p, t, e in [("reading", 3, 8, 61.0), ("reading", 1, 8, None), ("reducing", 8, 8, 14.0),
                            ("waiting", 4, 8, 30.0), ("paused", 5, 8, None)]
    ]
    out["offer_line"] = [{"s": s, "out": longdoc.offer_line(s)} for s in (25.0, 130.0)]
    rows = [
        {"prompt_tokens": 3449, "prompt_ms": 49000, "output_tokens": 150, "generate_ms": 15000, "prompt_chars": 16400},
        {"prompt_tokens": 60, "prompt_ms": 400, "output_tokens": 40, "generate_ms": 3000},
        {"prompt_tokens": 3000, "prompt_ms": 42000, "output_tokens": 5, "generate_ms": 400},
    ]
    out["speed_from_stats"] = [
        {"rows": rows, "out": (lambda s: s.to_json() if s else None)(longdoc.speed_from_stats(rows))},
        {"rows": rows[1:2], "out": (lambda s: s.to_json() if s else None)(longdoc.speed_from_stats(rows[1:2]))},
        {"rows": [{"prompt_tokens": 100}], "out": None},
    ]
    speed = longdoc.Speed(70.0, 9.7, 4.75)
    out["estimate"] = {
        "speed": speed.to_json(),
        "cases": [{"chunks": c, "out": longdoc.estimate_seconds(c, speed)} for c in
                  [[14000] * 8, [14000, 9000], [5000], [14000] * 120]],
        "even_chunks": [{"chars": n, "tokens": t, "out": longdoc.even_chunks(n, t)}
                        for n, t in [(100000, 3500), (14000, 3500), (14001, 3500), (0, 3500), (50000, 2500)]],
        "eta": [
            {"done": [[14000, 60.0], [14000, 62.0]], "remaining": [14000, 9000], "job_speed": None,
             "out": longdoc.eta_seconds([(14000, 60.0), (14000, 62.0)], [14000, 9000])},
            {"done": [[14000, 60.0]], "remaining": [14000], "job_speed": speed.to_json(),
             "out": longdoc.eta_seconds([(14000, 60.0)], [14000], speed)},
            {"done": [], "remaining": [14000, 14000], "job_speed": None, "fallback": speed.to_json(),
             "out": longdoc.eta_seconds([], [14000, 14000], None, speed)},
            {"done": [], "remaining": [14000], "job_speed": None, "out": longdoc.eta_seconds([], [14000])},
        ],
    }
    given_docs = [{"name": "lease.pdf", "parts": LEASE}]
    chunks = longdoc.plan_chunks(given_docs)
    given = longdoc.chunk_given(chunks[0], given_docs)
    out["clean_note"] = [
        {"reply": r, "out": longdoc.clean_note(r, given)} for r in
        ["- Rent is $1,200 [p. 1]\n- A pool [p. 99]", "The passages don't mention anything useful.", "",
         "- Deposit [page 2] and pets (p. 3)"]
    ]
    out["constants"] = {
        "MAP_CHUNK_TOKENS": longdoc.MAP_CHUNK_TOKENS, "PHONE_MAP_CHUNK_TOKENS": longdoc.PHONE_MAP_CHUNK_TOKENS,
        "CHARS_PER_TOKEN": longdoc.CHARS_PER_TOKEN, "OVERHEAD_TOKENS": longdoc.OVERHEAD_TOKENS,
        "MAP_OUT_TOKENS": longdoc.MAP_OUT_TOKENS, "REDUCE_OUT_TOKENS": longdoc.REDUCE_OUT_TOKENS,
        "LONG_PROMPT_TOKENS": longdoc.LONG_PROMPT_TOKENS,
    }
    return out


def main() -> int:
    data = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != data:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python scripts/dev/longdoc_cases.py")
            return 1
        return 0
    OUT.write_bytes(data.encode("utf-8"))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
