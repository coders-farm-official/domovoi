"""Drafts (domovoi/drafts): the shared vectors, the layout's promises, and
the round trips that make a saved file trustworthy.

``shared_cases.json`` is what the server and the phone are held to (the
app's DraftSharedCasesTest.kt reads the same file): the same phrases, the
same blocks, the same pages and lines to the hundredth of a point, the same
diff. The rest pins what matters on its own: NFKC before drawing (the fonts
have no ligature glyphs), an explicit space at every wrap (so "if I" never
reads back as "ifI"), the PDF's text reading back exactly as laid out, and
the ATS self-check catching a file that doesn't.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.drafts import (FONTS_DIR, TEMPLATES_DIR, ats, blocks, diff, docx_file, kinds, layout, metrics,
                            pdf_file, phrases, prompts, templates)

CASES = json.loads((Path(layout.__file__).parent / "shared_cases.json").read_text(encoding="utf-8"))
ROOT = Path(__file__).resolve().parents[2]
ANDROID_ASSETS = ROOT / "android" / "app" / "src" / "main" / "assets" / "drafts"


def test_shared_cases_are_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "dev" / "draft_cases.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_app_builds_its_templates_and_fonts_from_these_files():
    """The phone renders from the same files: its build copies
    domovoi/drafts/templates and fonts into the assets (DraftAssetsTask),
    so there is no second copy to drift."""
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert 'dir("../domovoi/drafts")' in gradle and "DraftAssetsTask::outputDir" in gradle
    assert not ANDROID_ASSETS.exists()


def test_fonts_are_the_liberation_release_and_ofl():
    want = {
        "LiberationSans-Regular.ttf": "76d04c18ea243f426b7de1f3ad208e927008f961dc5945e5aad352d0dfde8ee8",
        "LiberationSans-Bold.ttf": "788abee4c806d660e8aee46689dd8540cd4bb98da03dcc9d171ce3efd99a9173",
        "LiberationSerif-Regular.ttf": "058ea80864aef09a23f45cbec2bb5400bc3dfbdea01c3f10538a21fcb497fb74",
        "LiberationSerif-Bold.ttf": "d754ba427cfe0bca54ae052384baa8f842da5bd6550ad4da024ac441e7a7d5ce",
    }
    for name, sha in want.items():
        assert hashlib.sha256((FONTS_DIR / name).read_bytes()).hexdigest() == sha
    lic = (FONTS_DIR / "LICENSE-OFL.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in lic and "GPL" not in lic


# ─── The shared vectors ──────────────────────────────────────────────────
def test_kinds_and_defaults():
    assert CASES["kinds"]["resume"] == {"label": "Resume", "template": "resume-plain", "format": "docx"}
    assert CASES["kinds"]["cover_letter"]["format"] == "docx"
    assert CASES["kinds"]["letter"]["format"] == "pdf"
    for c in CASES["card_line"]:
        assert kinds.card_line(*c["in"]) == c["out"]


def test_font_widths():
    for stem, c in CASES["fonts"].items():
        f = metrics.font(stem)
        assert f.units_per_em == c["units_per_em"] == 2048
        assert not f.has("ﬀ") and f.has("•")
        for w in c["widths"]:
            assert f.width_units(w["text"]) == w["units"]


@pytest.mark.parametrize("case", CASES["phrases"], ids=lambda c: c["in"][:24])
def test_phrases(case):
    ask = phrases.draft_request(case["in"])
    got = None if ask is None else {"kind": ask.kind, "template": ask.template, "topic": ask.topic}
    assert got == case["draft"]
    assert phrases.revision_request(case["in"]) == case["revise"]
    assert phrases.revision_request(case["in"], after_draft=True) == case["revise_after"]
    assert phrases.print_request(case["in"]) == case["print"]
    assert phrases.normalize(case["in"]) == case["normalized"]


def test_what_the_phrases_must_say():
    assert phrases.draft_request("Write a cover letter for the warehouse lead job at Acme").kind == "cover_letter"
    assert phrases.draft_request("How do I write a cover letter?") is None
    assert phrases.draft_request("make the letter shorter") is None          # a change, not a new letter
    assert phrases.revision_request("make the letter shorter")
    assert phrases.draft_request("write an email to my boss") is None
    assert phrases.draft_request("draft a letter to my landlord").template == "letter-personal"
    assert phrases.print_request("print it") and not phrases.print_request("print my cover letter please now")


@pytest.mark.parametrize("case", CASES["blocks"], ids=lambda c: c["in"][:16])
def test_blocks(case):
    assert [[b.style, b.lines] for b in blocks.parse(case["in"])] == case["out"]


@pytest.mark.parametrize("case", CASES["layout"], ids=lambda c: c["name"])
def test_layout(case):
    lay = layout.layout(case["markdown"], templates.get(case["template"]))
    got = [[[ln.style, ln.font, ln.size, ln.x, ln.y, ln.text, ln.marker, ln.marker_x] for ln in p.lines]
           for p in lay.pages]
    assert got == case["pages"]
    assert lay.text() == case["text"]


@pytest.mark.parametrize("case", CASES["wrap"], ids=lambda c: c["text"][:12])
def test_wrap(case):
    f = metrics.font(case["font"])
    assert layout.wrap(layout.prepare(case["text"], f), f, case["size"], case["room"]) == case["out"]


def test_xml_illegal_characters_one_rule():
    for c in CASES["xml_safe"]:
        assert blocks.xml_safe(c["in"]) == c["out"]
    assert blocks.parse("A \x01 B") == blocks.parse("A  B")


def test_prepare_and_diff_and_clean_cases():
    sans = metrics.font("LiberationSans-Regular")
    for c in CASES["prepare"]:
        assert layout.prepare(c["in"], sans) == c["out"]
    for c in CASES["diff"]:
        o, n = diff.display_text(c["old"]), diff.display_text(c["new"])
        assert diff.diff(o, n) == c["ops"]
        assert diff.change_line(c["old"], c["new"]) == c["change"]
    for c in CASES["clean"]:
        assert prompts.clean(c["in"]) == c["out"]
    for c in CASES["tokens"]:
        assert diff.tokens(c["in"]) == c["out"]


# ─── The layout's promises ───────────────────────────────────────────────
def test_nfkc_before_drawing_keeps_the_ligature_letters():
    sans = metrics.font("LiberationSans-Regular")
    assert layout.prepare("coﬀee oﬃce", sans) == "coffee office"


def test_every_wrap_keeps_its_space():
    sans = metrics.font("LiberationSerif-Regular")
    text = "Thank you for your letter. If I may say so, the power was off for nine hours, if I recall."
    for room in range(6000, 46000, 1500):
        lines = layout.wrap(layout.prepare(text, sans), sans, 1150, room)
        assert "".join(lines) == text
        for ln in lines[:-1]:
            assert ln.endswith(" ")
        for ln in lines:
            assert layout.fits(ln.rstrip(" "), sans, 1150, room)


def test_a_long_letter_goes_onto_a_second_page_within_the_margins():
    case = next(c for c in CASES["layout"] if c["name"] == "long-letter")
    t = templates.get("letter-block")
    bottom = layout.cpt(t.height - t.margin)
    assert len(case["pages"]) == 2
    for page in case["pages"]:
        for ln in page:
            assert ln[4] <= bottom


def test_templates_are_single_column_and_in_reach():
    for tid in templates.TEMPLATE_IDS:
        t = templates.get(tid)
        assert t.width == 612 and t.height == 792 and 54 <= t.margin <= 72
        assert set(t.styles) == set(templates.STYLE_NAMES)
        assert t.docx_font in ("Arial", "Times New Roman")
        assert all(kinds.get(k) for k in t.kinds)


# ─── Round trips ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("case", CASES["layout"], ids=lambda c: c["name"])
def test_the_pdf_reads_back_exactly_as_laid_out(case):
    """pypdfium2's text of the PDF is the laid-out text, line for line: the
    same extractor reads the phone's PDF in the emulator gate."""
    data = pdf_file.render(case["markdown"], templates.get(case["template"]), title="t")
    assert b"/FontFile2" in data and b"/ToUnicode" in data
    read = ats.pdf_text(data).replace("\r\n", "\n")
    want = "\n".join(case["text"].split("\f"))
    assert read.split() == want.split()
    assert ats.check(data, "pdf", case["markdown"]).ok or case["name"] == "traps"


def test_if_i_never_reads_as_ifi():
    case = next(c for c in CASES["layout"] if c["name"] == "letter")
    data = pdf_file.render(case["markdown"], templates.get("letter-block"))
    assert "If I may" in " ".join(ats.pdf_text(data).split())
    assert "IfI" not in ats.pdf_text(data).replace("\r\n", "")


@pytest.mark.parametrize("tid", templates.TEMPLATE_IDS)
def test_the_docx_passes_the_ats_check(tid):
    md = next(c for c in CASES["layout"] if c["name"] == "letter")["markdown"]
    data = docx_file.render(md, templates.get(tid), title="Letter")
    check = ats.check(data, "docx", md)
    assert check.ok and check.line == "text readable · single column"
    from docx import Document
    import io

    d = Document(io.BytesIO(data))
    assert not d.tables and d.core_properties.title == "Letter"
    assert d.styles["Normal"].font.name == templates.get(tid).docx_font


def test_the_ats_check_says_where_a_file_differs():
    c = ats.compare(["the", "coee"], ["the", "coffee"])
    assert not c.ok and "“coffee” reads as “coee”" in c.line
    assert ats.check(b"not a pdf", "pdf", "# x").ok is False
    for case in CASES["ats"]["compare"]:
        assert ats.compare(case["read"], case["want"]).as_json() == case["out"]


def test_a_character_the_font_lacks_fails_the_check_after_saving():
    md = "A note in 中文."
    data = pdf_file.render(md, templates.get("letter-block"))
    assert not ats.check(data, "pdf", md).ok


def test_diff_puts_the_new_text_back_together():
    for c in CASES["diff"]:
        o, n = diff.display_text(c["old"]), diff.display_text(c["new"])
        ops = diff.diff(o, n)
        assert "".join(t for op, t in ops if op != "-") == "".join(diff.tokens(n))
        assert "".join(t for op, t in ops if op != "+") == "".join(diff.tokens(o))


def test_prompts_carry_only_the_version_being_revised():
    p = prompts.revision("Dear Sam,\n\nThe heater is broken.", "shorter")
    assert "The heater is broken." in p["user"] and "Instruction: shorter" in p["user"]
    assert "no greeting" in p["system"]
    f = prompts.first_draft("resume", "resume-plain", "help me write my resume", "October 21, 2026",
                            facts=["Name: Jordan Avery"])
    assert "## Work Experience" in f["user"] and "- Name: Jordan Avery" in f["user"]
    assert "Never invent" in f["system"]
