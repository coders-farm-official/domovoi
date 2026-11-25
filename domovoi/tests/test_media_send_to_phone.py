"""A voice photo search offers to send the results to the asker's phone
(1.5.0, design MEDIA-UNDERSTANDING §9 "Voice"; domovoi/handlers/media_search.py
over the send-to-their-phone path, domovoi/help_desk/delivery.py).

"I found 1 photo that might show the beach. It's on the Photos page. Want
them on your phone?" only for a recognized household voice with a phone
marked as theirs; never a guest, a voice nobody recognized, a person with
no phone (or only a shared screen), or a search that found nothing. A yes
sends a "Photos: …" chat carrying the cards to THEIR phone.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from domovoi.handlers.media_search import OFFER_TEXT
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_help_desk_voice import _pending, voice  # noqa: F401 — fixture
from domovoi.tests.test_media_photos import _seed_item
from domovoi.tests.test_media_understanding import house  # noqa: F401 — fixture


async def _beach() -> int:
    return await _seed_item("Trips/beach.jpg", taken=None, caption="A dog on a beach.", tags=["beach", "dog"])


async def _replies(db) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT d.person_id, d.title, m.content, m.stats FROM help_deliveries d "
        "JOIN chat_messages m ON m.thread_id = d.thread_id AND m.role = 'assistant' ORDER BY d.id"
    ))).mappings().all()
    return [dict(r) for r in rows]


@requires_db
@pytest.mark.asyncio
async def test_a_recognized_voice_with_a_phone_is_offered_and_yes_sends_the_cards(voice, house) -> None:  # noqa: F811
    item = await _beach()
    sarah = await voice.person("FT Sarah")
    alex = await voice.person("FT Alex")
    await voice.phone("android-sarah", sarah)
    await voice.phone("android-alex", alex)
    r = await voice("show me photos of the beach", person=sarah)
    assert r.matched_handler == "media_search"
    assert r.text.startswith("I found 1 photo") and r.text.endswith(OFFER_TEXT) and r.expect_followup
    pending = await _pending(voice)
    assert pending["kind"] == "core.media_send" and pending["person_id"] == sarah
    r = await voice("yes", person=sarah)
    assert r.text == "Sent to your phone. It's in Chat as Photos: The beach."
    got = await _replies(voice.db)
    assert len(got) == 1 and got[0]["person_id"] == sarah and got[0]["title"] == "Photos: The beach"
    stats = got[0]["stats"] if isinstance(got[0]["stats"], dict) else json.loads(got[0]["stats"])
    assert stats["model_role"] == "media" and [c["id"] for c in stats["media"]["items"]] == [item]
    assert got[0]["content"].startswith("I found 1 photo")
    # The same search again within the hour: not sent twice.
    await voice("show me photos of the beach", person=sarah)
    r = await voice("yes", person=sarah)
    assert r.text.startswith("I already sent those photos to your phone")
    assert len(await _replies(voice.db)) == 1


@requires_db
@pytest.mark.asyncio
async def test_no_sends_nothing(voice, house) -> None:  # noqa: F811
    await _beach()
    sarah = await voice.person("FT Sarah")
    await voice.phone("android-sarah", sarah)
    await voice("show me photos of the beach", person=sarah)
    r = await voice("no", person=sarah)
    assert r.text == "Okay, I won't send them."
    assert await _replies(voice.db) == []


@requires_db
@pytest.mark.asyncio
async def test_never_offered_to_a_guest_a_phoneless_person_or_for_nothing_found(voice, house) -> None:  # noqa: F811
    await _beach()
    sarah = await voice.person("FT Sarah")
    bo = await voice.person("FT Bo")
    await voice.phone("android-sarah", sarah)
    await voice.phone("kitchen-tablet", bo, shared=True)       # a shared screen is nobody's phone
    for who, said in ((None, "show me photos of the beach"),   # a voice nobody recognized
                      (bo, "show me photos of the beach"),     # no phone of their own
                      (sarah, "show me photos of a giraffe")):  # nothing found
        r = await voice(said, person=who)
        assert r.matched_handler == "media_search", said
        assert OFFER_TEXT not in r.text and not r.expect_followup, (who, said)
        pending = await _pending(voice)
        assert not pending or pending.get("kind") != "core.media_send"
    assert await _replies(voice.db) == []


@requires_db
@pytest.mark.asyncio
async def test_a_photo_send_still_reaches_the_phone_after_voice_help_stopped_sending(voice, house) -> None:  # noqa: F811
    """1.5.0 removed voice help's send (core.help_send / core.help_who) but
    the photos ride the same path to the phone: the row, the NOTIFY the
    live socket turns into ``help_deliveries.changed``, that channel's
    snapshot (content-free since 1.5.0: the kind, never the title), and
    ``GET /api/help/deliveries`` (the apps' catch-up read, with the title).
    Every piece must still carry a "Photos: …" send, end to end."""
    import asyncio

    import asyncpg

    from domovoi.config import settings
    from domovoi.help_desk import delivery
    from web.backend import realtime
    from web.backend.api import help as help_api

    await _beach()
    sarah = await voice.person("FT Sarah")
    await voice.phone("android-sarah", sarah)
    # The worker's own database (xdist gives each worker a copy, and a
    # NOTIFY only reaches listeners on the same database).
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    heard: asyncio.Queue[str] = asyncio.Queue()
    conn = await asyncpg.connect(url)
    try:
        await conn.add_listener(delivery.NOTIFY_CHANNEL, lambda *a: heard.put_nowait(a[3]))
        await voice("show me photos of the beach", person=sarah)
        r = await voice("yes", person=sarah)
        assert r.text == "Sent to your phone. It's in Chat as Photos: The beach."
        delivery_id = r.data["delivery_id"]
        assert await asyncio.wait_for(heard.get(), timeout=5) == str(delivery_id)
    finally:
        await conn.close()
    # The live socket maps the NOTIFY to the channel the apps listen on.
    assert realtime.NOTIFY_CHANNEL_TO_REALTIME[delivery.NOTIFY_CHANNEL] == "help_deliveries"
    # Every socket gets the push, so it carries no chat title (1.5.0,
    # PRIVATE-CHATS §2.4): only the kind. The title is in the read below.
    pushed = await realtime._snapshot_help_deliveries()
    assert [(d["id"], d["person_id"], d["title"]) for d in pushed][:1] == [
        (delivery_id, sarah, realtime.HELP_DELIVERIES_PHOTOS_TITLE)]
    assert "The beach" not in str(pushed)
    # The catch-up read: the asker's row with its real title, and nothing
    # for anyone else.
    mine = (await help_api.deliveries(person_id=sarah, since_id=None, limit=20))["deliveries"]
    assert [(d["id"], d["title"], d["thread_id"]) for d in mine] == [
        (delivery_id, "Photos: The beach", r.data["thread_id"])]
    assert (await help_api.deliveries(person_id=sarah, since_id=delivery_id, limit=20))["deliveries"] == []
