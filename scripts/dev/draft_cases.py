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

from domovoi.drafts import ats, blocks, diff, docx_file, kinds, layout, metrics, phrases, prompts, templates  # noqa: E402

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

#: Review round 2's phrase sets (functional-testing/review-documents-d4-r2/phrases.json):
#: everyday messages never revise, after a draft or not; the revisions do.
#: ("try again" is in both of the reviewer's lists: right after a draft it is the
#: chat's own retry, so it stays with the everyday ones.)
#: Review round 2's typography case (soft hyphen, zero-width space, NBSP,
#: ligatures, fullwidth letters): the .docx check reads it the same on both sides.
TYPOGRAPHY = ("# Dear Mr. O\u2019Brien,\n\n\u201cSmart quotes\u201d, \u2018single ones\u2019, an em dash \u2014 an "
              "en dash \u2013 and an ellipsis\u2026 A non\u00a0breaking space, a zero\u200bwidth space, a "
              "soft\u00ad-hyphen, a soft\u00adhyphen inside, ligatures \ufb01le \ufb02ow o\ufb03ce, fullwidth "
              "\uff11\uff12\uff13 \uff21\uff22\uff23, and a \u00bd fraction with \u2122 and \u00a9.\n\n"
              "Sincerely,\n\nJordan\n")

PHRASE_SETS = {
    'everyday': [
        "turn on the kitchen lights",
        "turn off the lights",
        "turn the lights down",
        "dim the living room lights to 30 percent",
        "make it brighter in here",
        "make it warmer",
        "make it a bit warmer in here",
        "make it cooler",
        "make it warm in the bedroom",
        "set the thermostat to 70",
        "it's too cold in here",
        "it's too loud",
        "it's a bit dark in here",
        "this is too loud",
        "that's not what I asked",
        "it's not working",
        "turn it up",
        "turn it down",
        "make it louder",
        "make it quieter",
        "add milk to my shopping list",
        "add eggs and bread to the shopping list",
        "put bananas on my shopping list",
        "remove milk from the shopping list",
        "what's on my shopping list",
        "add stamps for the letter to my shopping list",
        "add envelopes to my shopping list so I can mail the letter",
        "add 'mail the cover letter' to my to-do list",
        "remind me to mail the letter tomorrow",
        "remind me to send my resume to Acme on Friday",
        "set a timer for 10 minutes",
        "set a timer for 5 minutes for the pasta",
        "cancel the timer",
        "how long is left on the timer",
        "start a timer for twenty minutes",
        "start a 3 minute timer",
        "set an alarm for 7 am",
        "wake me up at six thirty",
        "play some jazz",
        "play the beatles",
        "play music in the kitchen",
        "skip this song",
        "pause the music",
        "next song",
        "play something relaxing",
        "what song is this",
        "turn the music up",
        "stop",
        "shuffle my playlist",
        "play the letter song from sesame street",
        "what time is it",
        "what's the date today",
        "what's the weather tomorrow",
        "will it rain today",
        "how far is the moon",
        "who wrote pride and prejudice",
        "what is the capital of australia",
        "how many ounces in a cup",
        "what's 15 percent of 80",
        "tell me a joke",
        "tell me a story",
        "what's the news",
        "how do I write a good cover letter",
        "what should a resume include",
        "is two pages too long for a resume",
        "should I mention my salary in a cover letter",
        "what does a cover letter usually say",
        "thanks",
        "thank you",
        "ok",
        "great, thanks",
        "yes",
        "no",
        "sounds good",
        "perfect",
        "never mind",
        "hello",
        "good morning",
        "how are you",
        "what can you do",
        "help",
        "look up the weather in Toledo",
        "look up warehouse jobs near me",
        "what does this page say: https://example.com",
        "search for forklift certification courses",
        "find me a recipe for lasagna",
        "open my documents",
        "show me my files",
        "open the files page",
        "where are my documents",
        "send the letter to my email",
        "email the cover letter to my sister",
        "save it",
        "export it",
        "keep it as it is",
        "that's fine",
        "looks good",
        "it's perfect",
        "who is at the front door",
        "lock the front door",
        "is the garage door open",
        "close the garage door",
        "start the vacuum",
        "start the dishwasher",
        "turn on the fan",
        "open the blinds",
        "close the blinds",
        "what's on my calendar today",
        "add a dentist appointment to my calendar on tuesday",
        "call mom",
        "text dad that I'm on my way",
        "read my messages",
        "make me a sandwich",
        "make a grocery list",
        "make a note that the plumber comes monday",
        "write that down",
        "write a note to myself",
        "make it 20 minutes",
        "make it 7 instead",
        "change the timer to 15 minutes",
        "change the song",
        "use the living room speaker",
        "move the music to the kitchen",
        "keep playing",
        "give me a minute",
        "say that again",
        "end the call",
        "open the door",
    ],
    'everyday_after_a_draft': [
        "it's too cold in here",
        "it's too loud",
        "it's a bit dark in here",
        "this is too loud",
        "that's not what I asked",
        "it's not working",
        "try again",
        "a bit warmer",
        "warmer",
        "mention it to dad later",
        "it's not raining is it",
        "this is not my phone",
    ],
    'revise_any_time': [
        "make it shorter",
        "make it more formal",
        "add my phone number to the letter",
        "change the date in the cover letter",
        "make the letter shorter",
        "rewrite it",
        "shorten this",
        "add my forklift licence to the resume",
        "the cover letter is too long",
        "make it a bit more friendly",
        "tone it down",
        "proofread the letter",
        "fix the typos in my resume",
        "sign it Jordan Avery",
        "address it to Ms Patel",
        "remove the second paragraph from the letter",
        "put my email at the top of the resume",
        "can you make the letter more formal please",
        "please change the greeting in the letter to Dear Hiring Manager",
        "update the cover letter with my new address",
    ],
    'revise_after_a_draft': [
        "shorter",
        "more formal",
        "a bit shorter",
        "less formal",
        "too long",
        "it's too long",
        "mention my forklift licence",
        "tone it down",
        "another version",
        "fix the spelling",
        "more formal and less wordy",
    ],
}


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
    # Review round 1: not about the draft, after it or not (they must route as usual).
    "turn on the kitchen lights", "add milk to my shopping list", "put on some jazz",
    "use the oven timer for ten minutes", "say hello to grandma", "more coffee please", "fix the wifi",
    "change the thermostat to 70", "remove the dentist appointment tomorrow",
    "move my dentist appointment to Friday", "make it louder", "more bass", "turn it up",
    # ...and real revisions: naming the draft, "it" edited, and edit-only words.
    "add my forklift licence to the resume", "the cover letter is too long", "rewrite it", "shorten this",
    "tone it down", "a bit shorter", "less formal and more friendly", "fix the typos",
    "sign it Jordan Avery", "address it to Ms. Lee",
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
    cases["xml_safe"] = [{"in": t, "out": blocks.xml_safe(t)} for t in (
        "Salary \x01 expected", "A\x00B\tC\nD\rE",
        "no\ufffe and\uffff here", "keep \u00e9 \u2014 \U0001f600 ok",
        "\x0b\x0c\x1f\x7f stays? no: 7f is fine")]
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
    cases["phrase_sets"] = PHRASE_SETS
    cases["phrases"] = []
    seen: set[str] = set()
    for t in PHRASES + [t for v in PHRASE_SETS.values() for t in v]:
        if t in seen:
            continue
        seen.add(t)
        ask = phrases.draft_request(t)
        cases["phrases"].append({"in": t, "normalized": phrases.normalize(t),
                                 "draft": None if ask is None else {"kind": ask.kind, "template": ask.template,
                                                                     "topic": ask.topic},
                                 "revise": phrases.revision_request(t),
                                 "revise_after": phrases.revision_request(t, after_draft=True),
                                 "print": phrases.print_request(t)})
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
        "expected_words": [{"in": md, "out": ats.expected_words(md)} for md in (PERSONAL, "- • bullet · x", TYPOGRAPHY)],
        "docx_check": [{"in": md, "template": tpl, "out": ats.check(docx_file.render(md, templates.get(tpl)), "docx", md).as_json()}
                       for md, tpl in ((TYPOGRAPHY, "letter-block"),)],
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
