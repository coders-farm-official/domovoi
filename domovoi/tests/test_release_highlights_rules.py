"""The release script's Highlights rules (scripts/release/finish_release.py;
design note UPDATE-NOTICES §10).

From 1.5.0 a release's ``docs/release/v<version>.md`` carries exactly one
``## Highlights`` of 3 plain items right above ``## At a glance``, its
dated RELEASE_NOTES.md section the same 3 under ``### Highlights``, and
the GitHub page shows them right after Download, before What's new. The
script parses them with the house's own parser (domovoi/release_highlights.py).

Pure functions only (the script is loaded by path; no git, no gh, no
harness): the end-to-end runs are test_finish_release.py's, in CI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release" / "finish_release.py"


def _load():
    spec = importlib.util.spec_from_file_location("finish_release_hl", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["finish_release_hl"] = mod
    spec.loader.exec_module(mod)
    return mod


fr = _load()

HL = ["Chat looks things up on the web when the offline library has no answer.",
      "Music keeps playing after you ask a question.",
      "Friend Houses is open to everyone in the household, by voice too."]


def _doc(items=HL, *, order="hl-first") -> str:
    hl = "## Highlights\n\n" + "\n".join(f"- {i}" for i in items) + "\n\n"
    glance = "## At a glance\n\n- **A highlight**, in a line.\n\n### Upgrading\n\n- One line.\n\n"
    body = hl + glance if order == "hl-first" else glance + hl
    return f"# Domovoi 1.5.0\n\nThe intro.\n\n{body}## What is new since 1.4.1\n\nThe long story.\n"


def _notes(items=HL, version="1.5.0") -> str:
    hl = "### Highlights\n\n" + "\n".join(f"- {i}" for i in items) + "\n\n" if items else ""
    return (f"# Release notes\n\nNewest first.\n\n## 2026-12-01 — {version}\n\nThe release, in a paragraph.\n\n"
            f"{hl}- **What changed.** Things.\n\n## 2026-11-24 — 1.4.1\n\nOlder.\n")


def test_the_parser_is_the_house_s_own():
    from domovoi import release_highlights

    assert fr.release_highlights.parse_highlights(_doc().encode()) == release_highlights.parse_highlights(_doc().encode())
    assert fr.release_highlights_of(_doc()) == HL


@pytest.mark.parametrize(("version", "required"), [
    ("1.4.1", False), ("1.4.0", False), ("1.0.0-rc2", False),
    ("1.5.0", True), ("1.5.0-rc1", True), ("1.5.1", True), ("2.0.0", True), (None, False),
])
def test_required_from_1_5_0(version, required):
    assert fr.highlights_required(version) is required


def test_a_good_1_5_0_doc_passes():
    assert fr.release_doc_problems(_doc(), "1.5.0") == []


def test_the_1_4_x_docs_need_none():
    """Their tags can't change: no Highlights asked of them."""
    for v in ("1.4.0", "1.4.1"):
        text = (REPO / "docs" / "release" / f"v{v}.md").read_text(encoding="utf-8")
        assert fr.release_doc_problems(text, v) == [], v


@pytest.mark.parametrize(("text", "why"), [
    (_doc(HL[:2]), "exactly one \"## Highlights\" section of exactly 3 items"),
    (_doc(HL + ["A fourth highlight here."]), "exactly 3 items"),
    (_doc(order="glance-first"), "must come right above \"## At a glance\""),
    (_doc(["Music *keeps* playing on.", HL[1], HL[2]]), "plain words only, no Markdown"),
    (_doc(["Music <b>keeps</b> playing.", HL[1], HL[2]]), "plain words only, no Markdown"),
    (_doc(["a" * 81, HL[1], HL[2]]), "exactly 3 items"),
    (_doc().replace("## At a glance", "## Highlights\n\n- " + "\n- ".join(HL) + "\n\n## At a glance"),
     "exactly one \"## Highlights\""),
])
def test_bad_highlights_are_refused(text, why):
    problems = fr.release_doc_problems(text, "1.5.0")
    assert problems and any(why in p for p in problems), problems


CAT = "🐱"   # one code point, two UTF-16 units


def test_an_emoji_highlight_is_counted_in_code_points():
    """The release check's rule is the house's: 8 to 80 code points, so an
    emoji counts once (the web and the phones count the same way)."""
    at_80, at_8 = CAT + "a" * 79, CAT + "a" * 7
    assert fr.release_doc_problems(_doc([at_80, at_8, HL[2]]), "1.5.0") == []
    assert fr.release_highlights_of(_doc([at_80, at_8, HL[2]])) == [at_80, at_8, HL[2]]
    for bad in (CAT + "a" * 80, CAT + "a" * 6):
        problems = fr.release_doc_problems(_doc([bad, HL[1], HL[2]]), "1.5.0")
        assert problems and any("exactly 3 items" in p for p in problems), (len(bad), problems)


def test_every_markdown_character_is_refused():
    for c in "`*_[]<>|#\\":
        assert fr.release_highlights.markdown_problems([f"Words {c} and more."]), c


def test_release_notes_must_carry_the_same_three():
    assert fr.highlights_match_problems(_doc(), _notes(), "1.5.0") == []
    assert "differ" in fr.highlights_match_problems(_doc(), _notes([HL[1], HL[0], HL[2]]), "1.5.0")[0]
    assert "differ" in fr.highlights_match_problems(_doc(), _notes([]), "1.5.0")[0]
    assert "no dated section for 1.5.0" in fr.highlights_match_problems(_doc(), _notes(version="1.4.9"), "1.5.0")[0]
    assert fr.notes_highlights(_notes(), "1.5.0") == HL


def _release(**kw):
    base = dict(tag="v1.5.0", version="1.5.0", prerelease=False, tag_commit="a" * 40, cert="c" * 64,
                apks=[], setup_sha256="3" * 64, linux_install=None,
                notes="## What's new since 1.4.1\n\n- Something.\n", full_notes=True)
    base.update(kw)
    return fr.Release(**base)


def test_the_page_order_is_download_highlights_what_s_new_files():
    text = fr.body(_release(highlights=HL))
    order = [text.index(x) for x in ("## Download", "## Highlights", "- " + HL[0], "- " + HL[2],
                                     "## What's new since 1.4.1", "**More detail:**", "## Files and checksums")]
    assert order == sorted(order)
    assert text.count("## Highlights") == 1


def test_a_release_without_highlights_has_no_section_on_the_page():
    for hl in (None, []):
        text = fr.body(_release(highlights=hl))
        assert "## Highlights" not in text
        assert text.index("## Download") < text.index("## What's new")
