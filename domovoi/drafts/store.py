"""Reading a chat's draft from the core (V038 tables), for "print it" said
into the app's chat mic in a home-server chat (handlers/printing.py). The
web process owns writing drafts (web/backend/api/chat_drafts.py); the core
only reads the latest version and draws its PDF.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from domovoi.drafts import pdf_file, templates


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
