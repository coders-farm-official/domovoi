"""playback_positions upsert + uniqueness tests."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi import spoken_audio as sa
from domovoi.tests.conftest import requires_db


async def _make_person(session, name: str) -> int:
    row = (
        await session.execute(
            text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name}
        )
    ).first()
    return int(row[0])


@requires_db
@pytest.mark.asyncio
async def test_upsert_is_idempotent_per_person(db_session) -> None:
    pid = await _make_person(db_session, "Kamron")
    for pos in (10, 55, 300):
        await sa.upsert_position(
            db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=1,
            device_id="kitchen", person_id=pid, position_sec=pos,
        )
    await db_session.commit()
    # Exactly one row; latest position wins.
    n = (await db_session.execute(
        text("SELECT COUNT(*) FROM playback_positions"))).scalar_one()
    assert n == 1
    got = await sa.get_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=1,
        device_id="kitchen", person_id=pid,
    )
    assert got["position_sec"] == 300


@requires_db
@pytest.mark.asyncio
async def test_anon_slot_is_single_and_distinct_from_person(db_session) -> None:
    pid = await _make_person(db_session, "Sam")
    # Anon slot (person_id NULL) — two upserts collapse to one row.
    await sa.upsert_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=7,
        device_id="browser-abc", person_id=None, position_sec=5,
    )
    await sa.upsert_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=7,
        device_id="browser-abc", person_id=None, position_sec=42,
    )
    # A person's slot on the SAME device+item is a separate row.
    await sa.upsert_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=7,
        device_id="browser-abc", person_id=pid, position_sec=999,
    )
    await db_session.commit()

    n = (await db_session.execute(
        text("SELECT COUNT(*) FROM playback_positions WHERE item_id = 7"))).scalar_one()
    assert n == 2
    anon = await sa.get_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=7,
        device_id="browser-abc", person_id=None,
    )
    assert anon["position_sec"] == 42
    person = await sa.get_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=7,
        device_id="browser-abc", person_id=pid,
    )
    assert person["position_sec"] == 999


@requires_db
@pytest.mark.asyncio
async def test_speed_memory_survives_position_save(db_session) -> None:
    await sa.set_speed(
        db_session, item_type=sa.ITEM_PODCAST, item_id=3,
        device_id="garage", person_id=None, speed=1.5,
    )
    # A later position save with speed=None must NOT reset speed.
    await sa.upsert_position(
        db_session, item_type=sa.ITEM_PODCAST, item_id=3,
        device_id="garage", person_id=None, position_sec=88, speed=None,
    )
    await db_session.commit()
    got = await sa.get_position(
        db_session, item_type=sa.ITEM_PODCAST, item_id=3,
        device_id="garage", person_id=None,
    )
    assert got["position_sec"] == 88
    assert got["speed"] == pytest.approx(1.5)


@requires_db
@pytest.mark.asyncio
async def test_person_cascade_delete_removes_positions(db_session) -> None:
    pid = await _make_person(db_session, "Gone")
    await sa.upsert_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=1,
        device_id="kitchen", person_id=pid, position_sec=10,
    )
    await db_session.commit()
    await db_session.execute(text("DELETE FROM people WHERE id = :id"), {"id": pid})
    await db_session.commit()
    n = (await db_session.execute(
        text("SELECT COUNT(*) FROM playback_positions"))).scalar_one()
    assert n == 0
