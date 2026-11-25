"""Spoken yes/no confirmations end in a real question (house rule).

Piper reads a declarative sentence that ends in "?" flat ("Delete the
grocery list?"), so a spoken confirmation doesn't sound like a question.
The rule for every yes/no confirmation the house speaks: the asked part
is its own final sentence and starts with "Should I", "Want me to" or
"Do you want" ("Should I delete your grocery list?").

The prompts are built at run time, so this test reads the source: every
module that parks a pending confirmation (it calls
``request_confirmation`` or declares ``confirmation_kinds``; found by
scanning, so a new handler or bundled plugin is covered without editing
this file), and every question string in it. The final sentence of each
must start with an allowed confirm opener, or be a genuine question that
doesn't ask leave to act: a wh-question ("Which one?") or an inverted
one about a fact ("Is that you?", "Did I get that right?"). A
declarative with a trailing "?" fails, and so does a permission question
in other words ("Can I delete it?", "Is that okay?").
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Where confirmation-owning code lives: core and the bundled plugins.
_ROOTS = (REPO / "domovoi", REPO / "plugins")

CONFIRM_OPENERS = ("should i", "want me to", "do you want")
# Questions that don't ask leave to act: their answer is a name, a
# choice or a fact, and Piper lifts an inverted question on its own.
_WH = r"what|what's|which|who|whose|where|when|how|why"
_FACT = r"is|are|was|were|did|do|does|has|have"
_ALLOWED_RE = re.compile(
    rf"^(?:{'|'.join(re.escape(o) for o in CONFIRM_OPENERS)})\b|^(?:{_WH}|{_FACT})\b",
    re.IGNORECASE,
)
# Asking leave to act in any words but the allowed ones.
_PERMISSION_RE = re.compile(
    r"^(?:can|could|may|shall|would) (?:i|you)\b|^is (?:that|it) (?:ok|okay|alright|all right)\b",
    re.IGNORECASE,
)
# Strings that are not spoken text: regular expressions and the like.
_NOT_SPOKEN_RE = re.compile(r"\(\?|\\[a-z]|\[|\|")
_WORDS_RE = re.compile(r"[A-Za-z]{2,} [A-Za-z]")
_PLACEHOLDER = "Alex"

#: Questions another change is rewording, by file and literal text
#: (placeholders as {}). An entry that no longer matches is harmless.
_REWORDED_ELSEWHERE = {
    # The friend-houses send confirm becomes "Your message to X says: ….
    # Should I send it?" in its own change.
    ("domovoi/handlers/friend_houses.py", 'Send "{}" to {}?'),
}


def _confirm_modules() -> list[Path]:
    out = []
    for root in _ROOTS:
        for p in sorted(root.rglob("*.py")):
            rel = p.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith("domovoi/sdk/"):
                continue
            src = p.read_text(encoding="utf-8")
            if "request_confirmation(" in src or "confirmation_kinds = (" in src:
                out.append(p)
    return out


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _text(node: ast.AST) -> str | None:
    """The literal text of a string node, placeholders filled in."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(v.value) if isinstance(v, ast.Constant) else _PLACEHOLDER for v in node.values
        )
    return None


def _strings(tree: ast.AST):
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            skip.update(id(v) for v in node.values)
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        t = _text(node)
        if t is not None:
            yield node.lineno, t


def _final_sentence(text: str) -> str:
    text = text.replace("%s", _PLACEHOLDER).replace("%d", "2").strip()
    body = text[:-1]
    cut = max(body.rfind(". "), body.rfind("! "), body.rfind("? "))
    last = (body[cut + 2:] if cut >= 0 else body).strip()
    # "By the way, was that you, Alex?": the lead-in is not the question.
    last = re.sub(r"^(?:by the way|okay|ok|also|and|so),\s*", "", last, flags=re.I)
    # "{statement} {have} Want me to …?": placeholders that are whole
    # sentences of their own.
    last = re.sub(rf"^(?:{_PLACEHOLDER}\s+)+(?=[A-Z])", "", last)
    return last.strip(" \"'—-")


def allowed(last: str) -> bool:
    return bool(_ALLOWED_RE.match(last)) and not _PERMISSION_RE.match(last)


def offending_questions() -> list[str]:
    bad = []
    for path in _confirm_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO).as_posix()
        for line, text in _strings(tree):
            t = text.strip()
            if not t.endswith("?") or _NOT_SPOKEN_RE.search(t) or not _WORDS_RE.search(t):
                continue
            if (rel, t.replace(_PLACEHOLDER, "{}")) in _REWORDED_ELSEWHERE:
                continue
            if not allowed(_final_sentence(t)):
                bad.append(f"{rel}:{line}: {t!r}")
    return bad


def test_spoken_confirmations_end_in_an_allowed_question():
    bad = offending_questions()
    assert not bad, (
        "A spoken question in a confirmation module ends in a declarative clause "
        "with '?' (Piper reads it flat) or asks leave in other words. Make the "
        "asked part its own final sentence starting with 'Should I', 'Want me "
        "to' or 'Do you want' (a wh-question is fine for a choice):\n"
        + "\n".join(bad)
    )


def test_the_scan_finds_the_confirmation_modules():
    # Guards against the scan silently finding nothing.
    names = {p.name for p in _confirm_modules()}
    assert {"lists.py", "printing.py", "router.py", "friend_houses.py", "radio.py"} <= names


def test_rule_examples():
    for s in ("That deletes the grocery list. Should I delete it?",
              "Want me to send the steps to your phone?",
              "Do you want the full article?",
              "By the way, was that you, Alex?",
              "Your message to Alex says: hi there. Should I send it?",
              "Which one?"):
        assert allowed(_final_sentence(s)), s
    for s in ("Delete the grocery list?", "Print it?", "Can I delete it?",
              "The kitchen wants to drop in. Is that okay?",
              "I'd need the internet — want me to check?"):
        assert not allowed(_final_sentence(s)), s


if __name__ == "__main__":
    print("\n".join(offending_questions()))
