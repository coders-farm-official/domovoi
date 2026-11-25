"""V073 on a house from before 1.5.0: a pure re-key.

Builds a throwaway database next to the test lane, migrates it to the last
version before V073, fills it the way a 1.4 house looks (several one-device
rooms, two of them sharing a label, a room known only by its pairing, one
only by its music player, a phone's chat mic, a parked approval, and two
wake-word rows left behind by satellites 1.4.x removed), runs V073 and
checks that every room, id, token and label survived, that rooms sharing
a label were NOT merged, and that the leftover wake-word rows made no
room (pre-merge review M2).
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from domovoi.config import settings
from domovoi.tests.conftest import requires_db

pytestmark = requires_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
V073 = 73


def _version(p: Path) -> int:
    return int(re.match(r"V(\d+)__", p.name).group(1))


def _files() -> list[Path]:
    return sorted(MIGRATIONS.glob("V*__*.sql"), key=_version)


LEGACY = """
INSERT INTO satellites (room_id, sat_type, room_label, voice, quiet_mode, quiet_start, quiet_end,
                        board, adopted_via)
VALUES ('lg_kitchen', 'voice', 'Kitchen', 'amy', 'custom', '22:00', '07:00', 'pi4', 'usb'),
       ('lg_kitchen_2', 'voice', 'Kitchen', NULL, 'house', NULL, NULL, NULL, 'hello'),
       ('lg_office', 'video', NULL, NULL, 'off', NULL, NULL, NULL, 'hello'),
       ('lg_den', 'voice', '  ', NULL, 'house', NULL, NULL, 'android', 'hello');
UPDATE satellites SET battery_say = FALSE WHERE room_id = 'lg_den';
INSERT INTO satellite_pairings (room_id, token_hash)
VALUES ('lg_kitchen', 'hash-kitchen'), ('lg_office', 'hash-office'),
       ('lg_garage', 'hash-garage'), ('phone-chat1', 'hash-phone');
INSERT INTO satellite_approvals (room_id, token_hash, code) VALUES ('lg_new', 'hash-new', '123456');
INSERT INTO mpd_rooms (room_id, control_port, http_port, container_name)
VALUES ('lg_kitchen', 6651, 8051, 'mpd-lg_kitchen'), ('lg_porch', 6652, 8052, 'mpd-lg_porch'),
       ('phone-chat1', 6653, 8053, 'mpd-phone');
INSERT INTO room_wake_words (room_id, state, slug, slugs, device_max)
VALUES ('lg_kitchen', 'chosen', 'domovoi_v1', ARRAY['domovoi_v1'], 3),
       ('lg_attic', 'kept', NULL, NULL, NULL),
       ('lg_removed', 'chosen', 'domovoi_v1', ARRAY['domovoi_v1'], 2);
INSERT INTO satellite_health (room_id, sample) VALUES ('lg_kitchen', '{"cpu": 1}');
INSERT INTO satellite_outages (room_id, report) VALUES ('lg_office', '{"why": "x"}');
INSERT INTO room_battery (room_id, percent) VALUES ('lg_den', 40);
INSERT INTO people (id, name) VALUES (1, 'Alex');
INSERT INTO voice_profiles (person_id, embedding, model, room_id)
VALUES (1, '\\x00', 'm', 'lg_office');
"""


@pytest.fixture
async def legacy_db():
    asyncpg = pytest.importorskip("asyncpg")
    url = make_url(settings.database_url)
    kw = {"user": url.username, "password": url.password,
          "host": url.host or "127.0.0.1", "port": url.port or 5432}
    name = f"{url.database}_v072_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(database="postgres", **kw)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except asyncpg.exceptions.InsufficientPrivilegeError:
        await admin.close()
        pytest.skip("can't create a scratch database here")
    conn = await asyncpg.connect(database=name, **kw)
    try:
        for f in _files():
            if _version(f) < V073:
                await conn.execute(f.read_text(encoding="utf-8"))
        yield conn
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _migrate_from_v072(conn) -> None:
    for f in _files():
        if _version(f) >= V073:
            await conn.execute(f.read_text(encoding="utf-8"))


async def test_a_1_4_house_keeps_its_rooms_ids_and_tokens(legacy_db):
    conn = legacy_db
    await conn.execute(LEGACY)
    await _migrate_from_v072(conn)

    rooms = {r["room_id"]: r for r in await conn.fetch("SELECT * FROM rooms")}
    # Every room the house had, the phone's chat mic not among them.
    assert set(rooms) == {"lg_kitchen", "lg_kitchen_2", "lg_office", "lg_den",
                          "lg_garage", "lg_porch"}
    # M2: a wake-word row whose satellite is gone (1.4.x never deleted
    # them) is no room; the row itself is left as it was.
    assert "lg_attic" not in rooms and "lg_removed" not in rooms
    assert {r[0] for r in await conn.fetch("SELECT room_id FROM room_wake_words")} >= {"lg_attic", "lg_removed"}
    # Rooms that shared a label are still two rooms.
    assert rooms["lg_kitchen"]["name"] == rooms["lg_kitchen_2"]["name"] == "Kitchen"
    assert rooms["lg_office"]["name"] == "lg_office"
    assert rooms["lg_den"]["name"] == "lg_den"
    # Room settings moved over from the device row.
    k = rooms["lg_kitchen"]
    assert (k["voice"], k["quiet_mode"], k["quiet_start"], k["quiet_end"]) == (
        "amy", "custom", "22:00", "07:00")
    assert rooms["lg_office"]["quiet_mode"] == "off"
    assert rooms["lg_porch"]["quiet_mode"] == "house"

    sats = {r["satellite_id"]: r for r in await conn.fetch("SELECT * FROM satellites")}
    # Every device keeps its id, in the room of the same id; a device known
    # only by its pairing got its inventory row.
    assert set(sats) == {"lg_kitchen", "lg_kitchen_2", "lg_office", "lg_den", "lg_garage"}
    assert all(s["room_id"] == sid for sid, s in sats.items())
    assert sats["lg_kitchen"]["wake_max"] == 3 and sats["lg_kitchen"]["board"] == "pi4"
    assert sats["lg_office"]["sat_type"] == "video"
    assert sats["lg_den"]["battery_say"] is False
    gone = {r["column_name"] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'satellites'")}
    assert not gone & {"room_label", "voice", "quiet_mode", "quiet_start", "quiet_end"}

    # Tokens: same hash under the same id, so old devices still pair.
    pairs = {r[0]: r[1] for r in await conn.fetch(
        "SELECT satellite_id, token_hash FROM satellite_pairings")}
    assert pairs == {"lg_kitchen": "hash-kitchen", "lg_office": "hash-office",
                     "lg_garage": "hash-garage", "phone-chat1": "hash-phone"}
    assert await conn.fetchval(
        "SELECT token_hash FROM satellite_approvals WHERE satellite_id = 'lg_new'") == "hash-new"
    assert await conn.fetchval(
        "SELECT satellite_id FROM satellite_health") == "lg_kitchen"
    assert await conn.fetchval(
        "SELECT satellite_id FROM satellite_outages") == "lg_office"
    assert await conn.fetchval("SELECT percent FROM room_battery WHERE satellite_id = 'lg_den'") == 40
    assert await conn.fetchval("SELECT satellite_id FROM voice_profiles") == "lg_office"
    wake = await conn.fetchrow("SELECT * FROM room_wake_words WHERE room_id = 'lg_kitchen'")
    assert wake["slugs"] == ["domovoi_v1"] and "device_max" not in wake.keys()


async def test_an_empty_house_migrates(legacy_db):
    await _migrate_from_v072(legacy_db)
    assert await legacy_db.fetchval("SELECT count(*) FROM rooms") == 0
