"""The sequence diagrams in the docs still draw on GitHub.

In a Mermaid ``sequenceDiagram`` a ``;`` ends the statement, the same as a
line break, so a semicolon inside a note or a message cuts that line in
two and the second half doesn't parse. GitHub then shows "Unable to render
rich display" in place of the whole diagram — seven of the twenty-three
diagrams in the docs were broken that way (2026-10). Write a comma or a
dash instead, or ``#59;`` (Mermaid's entity code) when the semicolon
itself has to show.

DB-free (file reads).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Never docs of ours: dependencies, build output, caches, VCS metadata.
SKIP_DIRS = {"node_modules", "vendor", "build", "dist", "__pycache__"}

FENCE_OPEN = re.compile(r"^\s*```\s*mermaid\s*$", re.IGNORECASE)
FENCE_CLOSE = re.compile(r"^\s*```\s*$")
# "#59;", "#35;", "#quot;" — Mermaid entity codes, the allowed way to
# write a semicolon inside diagram text.
ENTITY = re.compile(r"#\w+;")


def _markdown_files() -> list[Path]:
    found = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        found += [Path(root) / f for f in files if f.endswith(".md")]
    return sorted(found)


def _mermaid_blocks() -> list[tuple[Path, int, list[str]]]:
    """``(file, line number of the diagram's first line, lines)``."""
    blocks = []
    for path in _markdown_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            if FENCE_OPEN.match(lines[i]):
                j = i + 1
                while j < len(lines) and not FENCE_CLOSE.match(lines[j]):
                    j += 1
                blocks.append((path, i + 2, lines[i + 1:j]))
                i = j
            i += 1
    return blocks


def _kind(lines: list[str]) -> str:
    for line in lines:
        text = line.strip()
        if text and not text.startswith("%%"):
            return text.split()[0]
    return ""


def test_the_docs_have_sequence_diagrams_to_check():
    # Guards the test below against passing because it found nothing.
    kinds = [_kind(lines) for _, _, lines in _mermaid_blocks()]
    assert kinds.count("sequenceDiagram") >= 10, kinds


def test_no_sequence_diagram_text_has_a_bare_semicolon():
    offenders = []
    for path, first, lines in _mermaid_blocks():
        if _kind(lines) != "sequenceDiagram":
            continue
        for n, line in enumerate(lines):
            if line.strip().startswith("%%"):
                continue
            # A semicolon that ends the line ends nothing extra; one with
            # text after it splits the statement.
            body = ENTITY.sub("", line).rstrip().rstrip(";")
            if ";" in body:
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{first + n}: {line.strip()}")
    assert not offenders, (
        "a ';' ends the statement in a Mermaid sequence diagram, so GitHub "
        "can't draw these — use a comma or a dash, or #59; to show a "
        "semicolon:\n" + "\n".join(offenders)
    )
