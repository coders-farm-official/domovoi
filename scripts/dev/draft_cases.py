"""Write domovoi/drafts/shared_cases.json: the vectors both places are held to.

    python scripts/dev/draft_cases.py          # rewrite the file from the Python rules
    python scripts/dev/draft_cases.py --check  # exit 1 if the file is out of date

The inputs are written here by hand; the expected outputs are what the
Python implementation (domovoi/drafts) gives. The server's tests
(domovoi/tests/test_drafts.py) and the app's (DraftSharedCasesTest.kt) both
read the file, so a rule changed on one side fails the other's tests until
it changes there too. The layout cases are the strongest of them: the same
pages and lines, to the hundredth of a point, is what makes a template's
PDF text identical from the server and the phone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from domovoi.drafts import ats, blocks, diff, kinds, layout, metrics, phrases, prompts, templates  # noqa: E402

OUT = ROOT / "domovoi" / "drafts" / "shared_cases.json"

RESUME = """# Jordan Avery
Toledo, OH · 555-0142 · jordan.avery@example.com

## Summary
Warehouse lead with six years of experience running receiving and shipping teams. Known for safe, \
steady work and for training new staff; coffee-fuelled, efficient and calm when the trucks are late.

## Work Experience
**Warehouse Lead, Lakeshore Supply — 2021 to 2024**
- Led a team of eight associates across two shifts, cutting late shipments by a third over two years.
- Trained new hires on forklift safety and the scanner system.
- Kept the inventory count within half a percent at every quarterly audit.

**Associate, Lakeshore Supply — 2018 to 2021**
- Received, checked and shelved about 200 cartons a day.

## Education
**Toledo Technical College — Certificate in Logistics, 2018**

## Skills
Forklift (certified), inventory systems, scheduling, first aid
"""

LETTER = """Jordan Avery
12 Example Street
Toledo, OH 43604
jordan.avery@example.com

October 21, 2026

Dana Whitfield
Lakeshore Mutual Insurance Company
1200 Example Avenue
Toledo, OH 43604

**Re: Request for review of claim LM-2026-048213**

Dear Ms. Whitfield,

Thank you for your letter of October 3. I'm asking for a review of the decision on the food lost \
from the refrigerator. If I may say so, the building's power was off for nine hours on September 28 \
— the manager's notice is attached — so the loss followed a power outage, as section 4 of \
my policy requires.

I've enclosed the receipts (total $212.40), the manager's notice and two photos. Please send the \
decision to the address above or to jordan.avery@example.com.

Sincerely,

Jordan Avery
"""

PERSONAL = """October 21, 2026

Dear Mr. Okafor,

The heater in apartment 3B has stopped working again, and the nights are getting cold. Could \
someone take a look this week? I'm home after five every day, and the office is welcome to use the \
spare key.

Thank you for your help,

Sam Lee
"""

TRAPS = """# Typography ﬀ trap

The oﬃce coﬀee is ﬂuffy. “Smart quotes” — and an em dash; café \
naïve résumé. A very long address: \
https://example.com/a/really/long/path/that/goes/on/and/on/without/any/spaces/at/all/for/a/while/now
"""


def long_letter() -> str:
    para = ("This paragraph is here to make the letter run onto a second page, so the page break is "
            "held to the same rules on both renderers. ")
    return LETTER.replace("Sincerely,", "\n\n".join(para * 4 for _ in range(7)) + "\n\nSincerely,")


SAMPLES = [
    ("resume", "resume-plain", RESUME),
    ("resume-classic", "resume-classic", RESUME),
    ("letter", "letter-block", LETTER),
    ("personal", "letter-personal", PERSONAL),
    ("traps", "resume-plain", TRAPS),
    ("long-letter", "letter-block", long_letter()),
]

PHRASES = [
    "Write a cover letter for the warehouse lead job at Acme Logistics.",
    "can you please draft a letter to my landlord about the broken heater?",
    "Help me write my resume",
    "I need a cover letter for Beta Corp",
    "write a thank-you letter to my neighbour",
    "Draft my résumé",
    "Could you write me a complaint letter to the phone company about my bill",
    "make a CV",
    "How do I write a cover letter?",
    "What should a resume include?",
    "write an email to my boss",
    "write a poem about letters",
    "I got a letter from the bank",
    "Shorter",
    "make it more formal",
    "Make the letter shorter, please.",
    "It’s too long",
    "mention my forklift licence",
    "add that I can start on Monday",
    "Can you rewrite the opening?",
    "what does ATS mean?",
    "thanks!",
    "print it",
    "Print the letter.",
    "please print a copy",
    "print my cover letter",
    "print two copies of the lease",
]

DIFFS = [
    (RESUME, RESUME.replace("Led a team of eight associates across two shifts, cutting late shipments by a third "
                            "over two years.", "Led eight associates over two shifts.")),
    (LETTER, LETTER.replace("Thank you for your letter of October 3. ", "")),
    (PERSONAL, PERSONAL + "\nP.S. The radiator in the bathroom is fine.\n"),
    (PERSONAL, PERSONAL),
    ("", "# New\n\nA first line."),
    ("One two three four five six seven eight nine ten.", "One two three four five six seven eight nine eleven."),
]

CLEANS = [
    "Here is your cover letter:\n\n```markdown\nDear Ms. Lee,\n\nI'm writing to apply.\n```\n\n"
    "Let me know if you'd like any changes.",
    "Hello. I am Domovoi, your household assistant.\n\n# Jordan Avery\n\n## Summary\nSteady worker.",
    "Hi Sam,\n\nThe heater is broken.\n\nThanks,\nAlex",
    "Sure! Here's a shorter version:\n\nDear Dana,\n\nPlease review my claim.\n\nSincerely,\nJordan\n\n"
    "I hope this helps!",
    "<<<DOCUMENT\nDear Dana,\nDOCUMENT>>>",
]


def lines_json(lay: layout.Layout) -> list:
    return [[[ln.style, ln.font, ln.size, ln.x, ln.y, ln.text, ln.marker, ln.marker_x] for ln in p.lines]
            for p in lay.pages]


def main() -> int:
    cases: dict = {}
    cases["kinds"] = {k: {"label": v.label, "template": v.template, "format": v.fmt} for k, v in kinds.KINDS.items()}
    cases["card_line"] = [{"in": [k, v, w], "out": kinds.card_line(k, v, w)}
                          for k, v, w in (("resume", 3, 380), ("letter", 1, 1), ("cover_letter", 2, 245))]
    cases["fonts"] = {}
    probe = ["a", "W", " ", "Jordan Avery", "•", "·", "—", "“q”", "café", "0123456789"]
    for stem in metrics.FONT_STEMS:
        f = metrics.font(stem)
        cases["fonts"][stem] = {"units_per_em": f.units_per_em, "glyphs": len(f.advances),
                                "has_ff": f.has("ﬀ"), "has_bullet": f.has("•"),
                                "widths": [{"text": t, "units": f.width_units(t)} for t in probe]}
    sans = metrics.font("LiberationSans-Regular")
    cases["prepare"] = [{"in": t, "out": layout.prepare(t, sans)} for t in (
        "coﬀee oﬃce ﬂy", "two  spaces\tand a tab", " lead ", "① circled",
        "CJK 中文 here", "“smart” — dash", "  ")]
    cases["wrap"] = []
    for text, size, room in (("if I can help, I will, and if I can't I will say so plainly", 1150, 18000),
                             ("Supercalifragilisticexpialidocious-and-then-some-more-letters", 1050, 9000),
                             ("short", 1050, 46800), ("", 1050, 46800),
                             ("one two three four five six seven eight nine ten eleven twelve", 1800, 20000)):
        cases["wrap"].append({"font": "LiberationSans-Regular", "size": size, "room": room, "text": text,
                              "out": layout.wrap(layout.prepare(text, sans), sans, size, room)})
    cases["blocks"] = [{"in": md, "out": [[b.style, b.lines] for b in blocks.parse(md)]} for md in (
        RESUME, LETTER, "# Name ##\n\n* star bullet\n+ plus bullet\n• dot bullet\n\n---\n\n```\ncode line\n```",
        "**Bold** start but more\n**all bold**\n__also bold__\nA [link](https://example.com) and `code` "
        "and *it* and snake_case_word and 2*3*4.", "\n\n  \n", "### Third level\nplain \\*escaped\\* text")]
    cases["plain_text"] = [{"in": md, "out": blocks.plain_text(blocks.parse(md)), "words": blocks.word_count(md)}
                           for md in (RESUME, PERSONAL)]
    cases["layout"] = []
    for name, tid, md in SAMPLES:
        lay = layout.layout(md, templates.get(tid))
        cases["layout"].append({"name": name, "template": tid, "markdown": md, "width": lay.width,
                                "height": lay.height, "pages": lines_json(lay), "text": lay.text()})
    cases["display_text"] = [{"in": md, "out": diff.display_text(md)} for md in (RESUME, PERSONAL)]
    cases["tokens"] = [{"in": t, "out": diff.tokens(t)} for t in ("a  b\nc\n\n\nd", " lead", "")]
    cases["diff"] = []
    for old, new in DIFFS:
        o, n = diff.display_text(old), diff.display_text(new)
        cases["diff"].append({"old": old, "new": new, "ops": diff.diff(o, n), "change": diff.change_line(old, new)})
    cases["phrases"] = []
    for t in PHRASES:
        ask = phrases.draft_request(t)
        cases["phrases"].append({"in": t, "normalized": phrases.normalize(t),
                                 "draft": None if ask is None else {"kind": ask.kind, "template": ask.template,
                                                                     "topic": ask.topic},
                                 "revise": phrases.revision_request(t), "print": phrases.print_request(t)})
    cases["titles"] = [{"in": [k, t], "out": phrases.title_for(k, t)} for k, t in (
        ("Cover letter", "for the warehouse lead job at Acme Logistics"), ("Resume", ""), ("Letter", "to my landlord"))]
    cases["clean"] = [{"in": t, "out": prompts.clean(t)} for t in CLEANS]
    cases["prompts"] = {
        "rules": prompts.RULES, "revise_rules": prompts.REVISE_RULES, "skeletons": prompts.SKELETONS,
        "first_draft": [{"in": ["cover_letter", "letter-block", "Write a cover letter for Acme", "October 21, 2026"],
                         "out": prompts.first_draft("cover_letter", "letter-block", "Write a cover letter for Acme",
                                                    "October 21, 2026")}],
        "revision": [{"in": [PERSONAL, "shorter"], "out": prompts.revision(PERSONAL, "shorter")}],
    }
    cases["ats"] = {
        "passed": ats.PASSED,
        "expected_words": [{"in": md, "out": ats.expected_words(md)} for md in (PERSONAL, "- • bullet · x")],
        "compare": [{"read": r, "want": w, "out": ats.compare(r, w).as_json()} for r, w in (
            (["a", "b"], ["a", "b"]), (["a", "c"], ["a", "b"]), (["a"], ["a", "b"]), (["a", "b", "c"], ["a", "b"]),
            (["coee"], ["coffee"]))],
    }
    data = json.dumps(cases, indent=1, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != data:
            print(f"{OUT} is out of date: run scripts/dev/draft_cases.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_bytes(data.encode("utf-8"))
    print(f"wrote {OUT} ({len(data):,} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
