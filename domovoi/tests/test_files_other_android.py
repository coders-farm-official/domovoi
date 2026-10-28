"""The Android app's copy of the Documents/Other line matches the server's.

The app picks a default "send to home" library by type (Documents for a
document, Other for anything else that is not a picture, song or video),
from ``DOCUMENT_EXTS`` in FileKinds.kt. The server's line is
``web/backend/api/doc_types.DOCUMENT_EXTS``. A drift would send a file to
the view that refuses it.
"""

from __future__ import annotations

import re
from pathlib import Path

from web.backend.api.doc_types import DOCUMENT_EXTS

KT = (Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "java"
      / "com" / "domovoi" / "app" / "ui" / "screens" / "files" / "FileKinds.kt")


def _kotlin_sets(src: str) -> dict[str, set[str]]:
    sets: dict[str, set[str]] = {}
    for name, body in re.findall(r"val (\w+)(?:: Set<String>)? = setOf\(([^)]*)\)", src):
        sets[name] = set(re.findall(r'"([^"]+)"', body))
    return sets


def test_the_apps_document_types_are_the_servers():
    src = KT.read_text(encoding="utf-8")
    sets = _kotlin_sets(src)
    expr = re.search(r"internal val DOCUMENT_EXTS: Set<String> = (.*?)\n\n", src, re.S).group(1)
    got: set[str] = set()
    for part in (p.strip() for p in expr.split("+")):
        if part.startswith("setOf("):
            got |= set(re.findall(r'"([^"]+)"', part))
        else:
            got |= sets[part]
    assert {"." + e for e in got} == set(DOCUMENT_EXTS)
