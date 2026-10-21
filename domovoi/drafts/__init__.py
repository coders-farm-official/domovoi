"""Drafts in Chat: write, revise and save real documents (documents plan, D4).

The model writes **content, never layout**: a draft is markdown in a fixed
skeleton for its kind (a resume's sections, a letter's parts), and our own
template code turns it into .docx or PDF. Every version is kept.

Pure code, shared in behaviour with the Android app's
``assistant/drafts/`` (the same rules, the same results, held together by
``shared_cases.json`` beside this file, which both test suites read):

* :mod:`.kinds` — the document kinds (resume, cover letter, letter), their
  default template and save format, and how a draft is titled;
* :mod:`.phrases` — what in a message asks for a draft, a revision of the
  draft, or "print it";
* :mod:`.templates` — the four templates (``templates/*.json``: fonts,
  sizes, spacing, margins, section order), single column, ATS-friendly;
* :mod:`.blocks` — a draft's markdown as blocks (title, heading, bold
  line, paragraph line, bullet);
* :mod:`.metrics` — advance widths read from the bundled Liberation fonts
  (``fonts/``, SIL OFL 1.1) with our own small TrueType reader, so the
  server and the phone measure text identically;
* :mod:`.layout` — NFKC first (the fonts have no ff/ffi/ffl ligature
  glyphs), our own greedy line breaking in integer arithmetic, an explicit
  space at every wrap (so text extraction never merges the words either
  side of it), and pagination: the same pages and lines on both renderers;
* :mod:`.pdf_file` (ReportLab, one ``drawString`` per laid-out line) and
  :mod:`.docx_file` (python-docx, naming Arial or Times New Roman, embedding
  nothing);
* :mod:`.diff` — a word-level Myers diff between versions and the one-line
  "what changed";
* :mod:`.ats` — the self-check after a save: the saved file's text read
  back and compared with the draft;
* :mod:`.prompts` — what the model is asked, for a first draft and for a
  revision (only the version being revised goes to the model).
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
FONTS_DIR = HERE / "fonts"
TEMPLATES_DIR = HERE / "templates"
