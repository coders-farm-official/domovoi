"""Results sent to the asker's own phone: a voice photo and video search's
"Do you want them on your phone?" (1.5.0, design MEDIA-UNDERSTANDING §9
"Voice"; ``domovoi/handlers/media_search.py``).

* **the asker only.** The results go to the phones marked "This phone
  belongs to <person>" (``devices.owner_person_id``, V035) for the person
  voice identification recognised. :func:`phones_of` is that list;
  nothing here can address "every phone";
* **no phone linked** → nothing is sent, and the handler says so and
  points to the app's Settings → Household → Phones & browsers → "This
  phone belongs to";
* **the results open as a home-server chat titled "Photos: …"**, which is
  household-shared like every server chat, so they stay findable; the
  phone's notification opens it (``help_deliveries``, V035, NOTIFY
  ``help_deliveries_changed`` → the live socket's
  ``help_deliveries.changed``, and ``GET /api/help/deliveries``);
* the limit: delivery needs the app to reach the server, so in practice
  it works at home.

The names say "help" because voice help built this path first (V035,
1.0). Voice help no longer sends anything (1.5.0, owner decision
2026-11-25: "how do I …" answers aloud and points to the manual), so the
photo search is its only sender; the table, the channel and the route keep
their names for the apps that already read them.

:data:`TITLE_PREFIX` and :func:`mark_title_kept` are the help CHAT's (the
dashboard's **ask Domovoi**, ``web/backend/api/chat.py``): a "Help: …"
thread keeps its title.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

NOTIFY_CHANNEL = "help_deliveries_changed"
#: A help chat's title starts so (the dashboard's "ask Domovoi").
TITLE_PREFIX = "Help: "
TITLE_MAX = 60


def help_title(question: str, prefix: str) -> str:
    """The sent chat's title: ``prefix`` then what was asked, capitalised."""
    q = " ".join((question or "").split()).rstrip("?.! ")
    # A transcript is lower case: "how do i …" reads "How do I …".
    q = re.sub(r"\bi\b", "I", q)
    q = q[:1].upper() + q[1:] if q else "Domovoi"
    title = prefix + q
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


#: Sends one person (and so each of their phones) gets in an hour.
PER_PERSON_HOUR = 3
#: Sends one asking session may make in an hour, to anyone.
PER_SESSION_HOUR = 3
#: The same steps to the same person: once in this long.
DEDUPE_SEC = 3600

# Sends per asking session (process memory: a restart forgets, the
# per-person limit in the database still holds).
_SESSION_SENDS: dict[str, list[float]] = {}


def note_sent(session_id: Any) -> None:
    import time

    if session_id is None:
        return
    now = time.monotonic()
    key = str(session_id)
    _SESSION_SENDS[key] = [t for t in _SESSION_SENDS.get(key, []) if now - t < 3600] + [now]
    if len(_SESSION_SENDS) > 512:
        for k in list(_SESSION_SENDS)[:256]:
            _SESSION_SENDS.pop(k, None)


async def refusal(session: AsyncSession, *, person_id: int, title: str, what: str,
                  asker_session: Any = None) -> str | None:
    """Why these results (``what``: "photos") may not be sent now, in
    words, or None. A few sends an hour per person (each person's phones
    share it), per asking session, and the same thing to the same person
    once an hour."""
    import time

    if asker_session is not None:
        now = time.monotonic()
        recent_here = [t for t in _SESSION_SENDS.get(str(asker_session), []) if now - t < 3600]
        if len(recent_here) >= PER_SESSION_HOUR:
            return f"I've sent enough {what} from here for now, so I haven't sent this one. Try again later."
    try:
        row = (await session.execute(text(
            "SELECT count(*) AS n, count(*) FILTER (WHERE title = :t AND created_at > now() - make_interval(secs => :d)) AS same "
            "FROM help_deliveries WHERE person_id = :p AND created_at > now() - interval '1 hour'"
        ), {"p": person_id, "t": title, "d": DEDUPE_SEC})).mappings().first()
    except Exception as e:  # noqa: BLE001 — no table: nothing was sent yet
        log.info("phone delivery: limits unreadable: %s", e)
        await session.rollback()
        return None
    if row and row["same"]:
        return f"I already sent those {what} to your phone in the last hour; they're in Chat."
    if row and row["n"] >= PER_PERSON_HOUR:
        return f"I've sent your phone enough {what} this hour, so I haven't sent this one. Try again later."
    return None


async def has_title_generated(session: AsyncSession) -> bool:
    """Whether chat history's V034 ``chat_threads.title_generated`` is here."""
    row = (await session.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'chat_threads' AND column_name = 'title_generated'"
    ))).first()
    return row is not None


async def mark_title_kept(session: AsyncSession, thread_id: int) -> None:
    """A "Help: …" title is final: chat history's title job (C2) must not
    rename it. Written only where V034's column exists (C2 also skips any
    "Help:" title)."""
    if await has_title_generated(session):
        await session.execute(text("UPDATE chat_threads SET title_generated = true WHERE id = :t"),
                              {"t": thread_id})


async def person_name(session: AsyncSession, person_id: int) -> str | None:
    row = (await session.execute(text("SELECT name FROM people WHERE id = :p"), {"p": person_id})).first()
    return row[0] if row else None


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
    except Exception as e:  # noqa: BLE001 — V035 missing: nobody has a phone yet
        log.warning("phone delivery: devices.owner_person_id unreadable (run Flyway, V035): %s", e)
        await session.rollback()
        return []
    return [Phone(r[0], r[1]) for r in rows]


async def deliver(
    session: AsyncSession,
    *,
    person_id: int,
    question: str,
    reply: str,
    phones: list[Phone],
    title: str,
    stats: dict[str, Any],
) -> Delivery:
    """Write the chat (what was asked, then ``reply`` with ``stats``, e.g.
    a photo search's ``media`` cards) and the delivery row, and queue the
    NOTIFYs; the caller commits. ``phones`` must be :func:`phones_of` for
    ``person_id`` and not empty."""
    if not phones:
        raise ValueError("deliver needs the person's phones")
    thread_id = (await session.execute(
        text("INSERT INTO chat_threads (title, person_id) VALUES (:t, :p) RETURNING id"),
        {"t": title, "p": person_id},
    )).scalar_one()
    await mark_title_kept(session, thread_id)
    await session.execute(
        text("INSERT INTO chat_messages (thread_id, role, content, source, speaker_person_id) "
             "VALUES (:t, 'user', :c, 'voice', :p)"),
        {"t": thread_id, "c": question.strip() or title, "p": person_id},
    )
    # stats.model_role names the writer ("media"): not a model, so chat
    # history (C2) never offers to retry from it.
    await session.execute(
        text("INSERT INTO chat_messages (thread_id, role, content, model, stats) "
             "VALUES (:t, 'assistant', :c, NULL, CAST(:st AS JSONB))"),
        {"t": thread_id, "c": reply, "st": json.dumps(stats)},
    )
    delivery_id = (await session.execute(
        text("INSERT INTO help_deliveries (person_id, thread_id, title) VALUES (:p, :t, :ti) RETURNING id"),
        {"p": person_id, "t": thread_id, "ti": title},
    )).scalar_one()
    await session.execute(text("SELECT pg_notify('chat_changed', 'phone-delivery')"))
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
