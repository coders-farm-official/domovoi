"""What the model is asked to write, and how its answer becomes a draft.

The model writes content into a fixed skeleton per template, in markdown
(:mod:`.blocks` reads it); our templates do the layout. Only the version
being revised goes to the model, never the others, so revisions don't
fill the context window. Facts come from the person: where one is missing,
the model writes a placeholder in square brackets ("[your phone number]")
rather than inventing it. The phone's twin is ``DraftPrompts.kt``.

D0 found the small models greet ("Hello. I am Domovoi…") and wrap their
answer in remarks; the rules forbid both and :func:`clean` drops what slips
through.

D5 (guided flows) starts a draft from a form: :func:`first_draft` takes
the form's fields as ``facts`` (one "label: value" line each), and the
same skeleton and rules apply.
"""

from __future__ import annotations

import re

from domovoi.drafts import kinds as kinds_mod

RULES = (
    "You write documents for the person, in markdown, following the skeleton exactly: the same headings "
    "and parts, in the same order. Write only the document itself: no greeting, no introduction, no "
    "remarks before or after it, no code fences. Use only facts the person gave. Where a fact is missing, "
    "write a short placeholder in square brackets, like [your phone number]. Never invent names, "
    "employers, dates, numbers, addresses or skills."
)

REVISE_RULES = (
    "You revise a document. You get the current version and one instruction. Write the whole document "
    "again with the instruction applied. Keep every fact unless the instruction removes it, add no facts "
    "the person didn't give, and keep the same skeleton: the same headings and parts, in the same order. "
    "Write only the revised document, in markdown: no greeting, no remarks, no code fences."
)

SKELETONS = {
    "resume-plain": (
        "# [Full name]\n"
        "[City, State] · [phone] · [email]\n\n"
        "## Summary\n"
        "[Two or three sentences.]\n\n"
        "## Work Experience\n"
        "**[Job title], [Employer] — [start] to [end]**\n"
        "- [What you did, starting with a verb]\n"
        "- [Another one]\n\n"
        "## Education\n"
        "**[School] — [qualification], [year]**\n\n"
        "## Skills\n"
        "[Skill], [skill], [skill]\n"
    ),
    "letter-block": (
        "[Your name]\n[Street]\n[City, State ZIP]\n[email or phone]\n\n"
        "[Date]\n\n"
        "[Recipient name]\n[Organisation]\n[Street]\n[City, State ZIP]\n\n"
        "**Re: [subject]**\n\n"
        "Dear [name],\n\n"
        "[Paragraph.]\n\n[Paragraph.]\n\n"
        "Sincerely,\n\n"
        "[Your name]\n"
    ),
    "letter-personal": (
        "[Date]\n\n"
        "Dear [name],\n\n"
        "[Paragraph.]\n\n[Paragraph.]\n\n"
        "Best wishes,\n\n"
        "[Your name]\n"
    ),
}
SKELETONS["resume-classic"] = SKELETONS["resume-plain"]

#: Lines a small model writes around a document, dropped from its edges.
_CHATTER = re.compile(
    r"^(?:here(?:'s| is| are)\b|sure\b|certainly\b|of course\b|absolutely\b|okay\b|ok\b|hello\b|hi\b|"
    r"i(?:'ve| have) (?:written|drafted|revised|made|updated)\b|below is\b|this (?:version|draft)\b|"
    r"let me know\b|feel free\b|i hope\b|i am domovoi\b|i'm domovoi\b|note:)",
    re.I,
)
_FENCE = re.compile(r"^\s*```")


def first_draft(kind: str, template: str, request: str, today: str, facts: list[str] | None = None) -> dict:
    """``{"system": ..., "user": ...}`` for a first draft."""
    k = kinds_mod.get(kind)
    skeleton = SKELETONS.get(template) or SKELETONS["letter-block"]
    lines = [f"Write a {k.label.lower()}.", f"The person asked: {request.strip()}", f"Today's date: {today}"]
    if facts:
        lines.append("Facts the person gave:")
        lines += [f"- {f}" for f in facts]
    lines += ["", "Skeleton:", skeleton]
    return {"system": RULES, "user": "\n".join(lines)}


def revision(markdown: str, instruction: str) -> dict:
    """``{"system": ..., "user": ...}`` for a revision: the one version
    being revised, as data, and the instruction."""
    user = (
        "Current version:\n<<<DOCUMENT\n" + (markdown or "").strip() + "\nDOCUMENT>>>\n\n"
        "Instruction: " + (instruction or "").strip()
    )
    return {"system": REVISE_RULES, "user": user}


def _remark(line: str) -> bool:
    """A remark about the document rather than part of it: one of the
    chatter openings, ending like a sentence or a colon ("Hi Sam," stays)."""
    t = line.strip()
    return bool(t) and t[-1] in ":.!" and bool(_CHATTER.match(t))


def clean(reply: str) -> str:
    """The document in a model's reply: code fences, a greeting or remark
    on the first lines and a sign-off remark on the last ones dropped."""
    lines = (reply or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln for ln in lines if not _FENCE.match(ln) and ln.strip() not in ("DOCUMENT>>>", "<<<DOCUMENT")]
    while lines and (not lines[0].strip() or _remark(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _remark(lines[-1])):
        lines.pop()
    return "\n".join(ln.rstrip() for ln in lines).strip()
