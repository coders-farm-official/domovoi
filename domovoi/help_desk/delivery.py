"""Help steps sent to the asker's own phone (voice "how do I…").

Owner decisions (2026-10-21), all enforced here and in the voice handler
(``domovoi/handlers/help_desk.py``):

* **the asker only.** The steps go to the phones marked "This phone
  belongs to <person>" (``devices.owner_person_id``, V033) for the person
  voice identification recognised, or the person they named when asked
  "Who should I send it to?". :func:`phones_of` is that list; nothing here
  can address "every phone";
* **no phone linked** → nothing is sent, and the handler says so and
  points to the app's Settings → Connection → "This phone belongs to";
* **the steps open as a home-server chat titled "Help: …"**, which is
  household-shared like every server chat, so they stay findable; the
  phone's notification opens it (``help_deliveries``, V033, NOTIFY
  ``help_deliveries_changed`` → the live socket's
  ``help_deliveries.changed``);
* the limit: delivery needs the app to reach the server, so in practice
  it works at home.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

NOTIFY_CHANNEL = "help_deliveries_changed"
TITLE_PREFIX = "Help: "
TITLE_MAX = 60


def help_title(question: str) -> str:
    q = " ".join((question or "").split()).rstrip("?.! ")
    q = q[:1].upper() + q[1:] if q else "Domovoi"
    title = TITLE_PREFIX + q
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1].rstrip() + "…"


@dataclass(frozen=True)
class Phone:
    device_id: str
    name: str


@dataclass(frozen=True)
class Delivery:
    id: int
    thread_id: int
    title: str
    phones: tuple[Phone, ...]


async def person_name(session: AsyncSession, person_id: int) -> str | None:
    row = (await session.execute(text("SELECT name FROM people WHERE id = :p"), {"p": person_id})).first()
    return row[0] if row else None


async def people_named(session: AsyncSession, name: str) -> list[tuple[int, str]]:
    """People whose name is ``name`` (case and spacing ignored)."""
    said = " ".join((name or "").split()).lower()
    if not said:
        return []
    rows = (await session.execute(
        text("SELECT id, name FROM people WHERE lower(regexp_replace(trim(name), '\\s+', ' ', 'g')) = :n "
             "ORDER BY id"),
        {"n": said},
    )).all()
    return [(int(r[0]), r[1]) for r in rows]


async def phones_of(session: AsyncSession, person_id: int) -> list[Phone]:
    """The devices marked as ``person_id``'s. Shared screens never count:
    the kitchen tablet is nobody's phone."""
    try:
        rows = (await session.execute(
            text(
                "SELECT device_id, name FROM devices WHERE owner_person_id = :p "
                "AND NOT COALESCE(shared_screen, false) ORDER BY last_seen_at DESC"
            ),
            {"p": person_id},
        )).all()
    except Exception as e:  # noqa: BLE001 — V033 missing: nobody has a phone yet
        log.warning("help delivery: devices.owner_person_id unreadable (run Flyway, V033): %s", e)
        await session.rollback()
        return []
    return [Phone(r[0], r[1]) for r in rows]


async def deliver(
    session: AsyncSession,
    *,
    person_id: int,
    question: str,
    steps: str,
    phones: list[Phone],
) -> Delivery:
    """Write the "Help: …" chat (the question, then the steps) and the
    delivery row, and queue the NOTIFYs; the caller commits. ``phones``
    must be :func:`phones_of` for ``person_id`` and not empty."""
    if not phones:
        raise ValueError("deliver needs the person's phones")
    title = help_title(question)
    thread_id = (await session.execute(
        text("INSERT INTO chat_threads (title, person_id) VALUES (:t, :p) RETURNING id"),
        {"t": title, "p": person_id},
    )).scalar_one()
    await session.execute(
        text("INSERT INTO chat_messages (thread_id, role, content, source, speaker_person_id) "
             "VALUES (:t, 'user', :c, 'voice', :p)"),
        {"t": thread_id, "c": question.strip() or title, "p": person_id},
    )
    await session.execute(
        text("INSERT INTO chat_messages (thread_id, role, content, model) VALUES (:t, 'assistant', :c, NULL)"),
        {"t": thread_id, "c": steps},
    )
    delivery_id = (await session.execute(
        text("INSERT INTO help_deliveries (person_id, thread_id, title) VALUES (:p, :t, :ti) RETURNING id"),
        {"p": person_id, "t": thread_id, "ti": title},
    )).scalar_one()
    await session.execute(text("SELECT pg_notify('chat_changed', 'help-delivery')"))
    await session.execute(text("SELECT pg_notify(:c, :r)"), {"c": NOTIFY_CHANNEL, "r": str(delivery_id)})
    return Delivery(int(delivery_id), int(thread_id), title, tuple(phones))


async def recent(session: AsyncSession, *, person_id: int | None = None, since_id: int | None = None,
                 limit: int = 20) -> list[dict[str, Any]]:
    """Deliveries newest first (``since_id``: only newer ones, oldest first)."""
    where, params = [], {"l": max(1, min(int(limit), 100))}
    if person_id is not None:
        where.append("person_id = :p")
        params["p"] = person_id
    if since_id is not None:
        where.append("id > :s")
        params["s"] = since_id
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = "ASC" if since_id is not None else "DESC"
    rows = (await session.execute(
        text(f"SELECT id, person_id, thread_id, title, created_at FROM help_deliveries {clause} "
             f"ORDER BY id {order} LIMIT :l"),
        params,
    )).mappings().all()
    return [
        {"id": int(r["id"]), "person_id": int(r["person_id"]), "thread_id": int(r["thread_id"]),
         "title": r["title"], "created_at": r["created_at"].isoformat()}
        for r in rows
    ]
