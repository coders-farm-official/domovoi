"""Reading a chat's draft from the core (V039 tables), for "print it" said
into the app's chat mic in a home-server chat (handlers/printing.py), and
the one writer into Documents → "From Chat" (:func:`write_into_chat_folder`).

The web process writes a chat's drafts (web/backend/api/chat_drafts.py);
the core reads the latest version and draws its PDF, and writes one draft
of its own: a letter taken by voice in a room (documents plan D7,
``domovoi/voice_docs/letters.py``). Both save through
:func:`write_into_chat_folder`, so the folder rules are the same whichever
process saves.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from domovoi.drafts import pdf_file, templates

#: The folder in Documents saved drafts land in (plan Q5).
CHAT_FOLDER = "From Chat"
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


class SaveRefused(Exception):
    """The From Chat folder can't be written safely; ``str()`` says why."""


@dataclass(frozen=True)
class DraftVersion:
    draft_id: int
    version: int
    title: str
    template: str
    body: str


async def latest_in_thread(session, thread_id: int) -> DraftVersion | None:
    row = (await session.execute(text(
        "SELECT d.id, d.title, d.template, v.version, v.body FROM chat_drafts d "
        "JOIN chat_draft_versions v ON v.draft_id = d.id WHERE d.thread_id = :t "
        "ORDER BY d.updated_at DESC, d.id DESC, v.version DESC LIMIT 1"), {"t": int(thread_id)})).first()
    if row is None:
        return None
    return DraftVersion(draft_id=row[0], version=row[3], title=row[1], template=row[2], body=row[4])


async def get(session, draft_id: int, version: int) -> DraftVersion | None:
    row = (await session.execute(text(
        "SELECT d.id, d.title, d.template, v.version, v.body FROM chat_drafts d "
        "JOIN chat_draft_versions v ON v.draft_id = d.id WHERE d.id = :d AND v.version = :v"),
        {"d": int(draft_id), "v": int(version)})).first()
    if row is None:
        return None
    return DraftVersion(draft_id=row[0], version=row[3], title=row[1], template=row[2], body=row[4])


def pdf_to_temp(d: DraftVersion) -> Path:
    """The version's PDF in a temporary file (the caller deletes it)."""
    data = pdf_file.render(d.body, templates.get(d.template), title=d.title)
    f = tempfile.NamedTemporaryFile(prefix="domovoi-draft-", suffix=".pdf", delete=False)
    try:
        f.write(data)
    finally:
        f.close()
    return Path(f.name)


def file_name(title: str, version: int, fmt: str) -> str:
    """"Letter to the landlord v1.docx": the title made safe for a file name."""
    stem = " ".join(_BAD_NAME.sub(" ", title or "").split()).strip(" .")[:100] or "Draft"
    return f"{stem} v{version}.{fmt}"


def documents_root() -> Path:
    from domovoi.config import settings

    return Path(settings.documents_dir).expanduser().resolve(strict=False)


def _sensitive_or_unstorable(name: str) -> bool:
    try:
        from web.backend.api.files_security import is_sensitive_name, unstorable_reason
    except Exception:  # noqa: BLE001 — the web package missing: be strict
        return name.startswith(".")
    return is_sensitive_name(name) or unstorable_reason(name) is not None


def _unique_path(folder: Path, name: str) -> Path:
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        cand = folder / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def write_into_chat_folder(name: str, data: bytes) -> str:
    """Writes ``data`` as ``Documents/From Chat/<name>`` (a new name when
    taken; "Draft" when the name is one the Files tab's secret-name filter
    hides) and returns its Documents-relative path. Raises
    :class:`SaveRefused` when the folder is a link or leads outside
    Documents: nothing is written through it."""
    if _sensitive_or_unstorable(name):
        name = "Draft" + Path(name).suffix
    base = documents_root()
    base.mkdir(parents=True, exist_ok=True)
    folder = base / CHAT_FOLDER
    if folder.is_symlink():
        raise SaveRefused("Documents/From Chat is a link; nothing is written through it")
    folder.mkdir(exist_ok=True)
    real = folder.resolve()
    try:
        real.relative_to(base)
    except ValueError:
        raise SaveRefused("Documents/From Chat leads outside Documents") from None
    target = _unique_path(real, name)
    tmp = real / f".{uuid.uuid4().hex}.part"
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return target.relative_to(base).as_posix()
