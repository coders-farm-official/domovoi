"""Find a document in Documents by what someone called it ("my cover
letter"), for voice printing.

Reads the Documents folder (``settings.documents_dir``) the way the Files
tab does: inside the folder only (links that lead out are skipped), no
hidden folders, and the Files tab's own secret-name filter
(``web.backend.api.files_security.is_sensitive_name`` — called, not
copied: the one shared filter for every door into that folder). Only kinds
that can print are offered. Bounded: 4 folders deep, 5,000 files.

Matching is plain words: every word said (minus "my", "the", "document",
…) must be in the file's name; an exact name wins outright, and the
closest few are offered as a choice. Reading aloud (documents plan D7,
``domovoi/voice_docs/reading.py``) finds documents the same way, over the
kinds it can read (``exts``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from domovoi.printing.jobs import KIND_BY_EXT

MAX_DEPTH = 4
MAX_FILES = 5000
_STOP = {"my", "the", "a", "an", "our", "document", "documents", "doc", "docs", "file", "files", "pdf", "please", "of",
         "for", "to", "copy"}
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Doc:
    rel: str
    title: str


def documents_root() -> Path:
    from domovoi.config import settings

    return Path(settings.documents_dir).expanduser()


def _sensitive(name: str) -> bool:
    try:
        from web.backend.api.files_security import is_sensitive_name
    except Exception:  # noqa: BLE001 — the web package missing: be strict
        return name.startswith(".")
    return is_sensitive_name(name)


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def all_documents(root: Path | None = None, exts: "set[str] | frozenset[str] | None" = None) -> list[Doc]:
    """Every document in Documents whose extension is in ``exts`` (the kinds
    that can print, by default)."""
    root = root or documents_root()
    wanted = KIND_BY_EXT if exts is None else exts
    try:
        real_root = root.resolve(strict=True)
    except OSError:
        return []
    out: list[Doc] = []
    for dirpath, dirnames, filenames in os.walk(real_root):
        depth = len(Path(dirpath).relative_to(real_root).parts)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not _sensitive(d) and depth < MAX_DEPTH]
        for name in filenames:
            if name.startswith(".") or _sensitive(name):
                continue
            if Path(name).suffix.lower() not in wanted:
                continue
            p = Path(dirpath) / name
            try:
                p.resolve(strict=True).relative_to(real_root)
            except (OSError, ValueError):
                continue
            out.append(Doc(rel=p.relative_to(real_root).as_posix(), title=Path(name).stem))
            if len(out) >= MAX_FILES:
                return out
    return out


def find(query: str, root: Path | None = None, exts: "set[str] | frozenset[str] | None" = None) -> list[Doc]:
    """The best matches for ``query``: one when it's clear, several when
    it isn't, none when nothing fits."""
    want = _words(query)
    if not want:
        return []
    docs = all_documents(root, exts)
    exact = [d for d in docs if _words(d.title) == want]
    if exact:
        return exact[:3]
    scored = []
    for d in docs:
        have = _words(d.title)
        if all(any(h == w or (len(w) > 3 and h.startswith(w)) for h in have) for w in want):
            scored.append((len(have) - len(want), d.title.lower(), d))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [d for _, _, d in scored[:3]]


def resolve(rel: str, root: Path | None = None) -> Path | None:
    """``rel`` inside Documents (re-checked), or None."""
    root = root or documents_root()
    try:
        real_root = root.resolve(strict=True)
        p = (real_root / rel).resolve(strict=True)
        p.relative_to(real_root)
    except (OSError, ValueError):
        return None
    if not p.is_file() or any(_sensitive(part) for part in Path(rel).parts):
        return None
    return p
