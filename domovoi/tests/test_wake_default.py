"""The household's default wake word (domovoi/wake_default.py, V054).

The rule the owner approved (2026-11-14): a new install's rooms are given
Domovoi's published word; rooms that existed before keep theirs; a new room
in an upgraded house matches the house; an admin's push is final; when the
published model can't be here (internet "never", not published, the
download failing) a new room waits on its boot word and the dashboard says
why, or says plainly that rooms can't wake at all.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import egress, wake_default
from domovoi.db.session import session_scope


MIGRATION = Path(__file__).resolve().parents[1] / "db" / "migrations" / "V054__wake_default.sql"
MODEL = b"onnx-bytes-of-the-published-word" * 64


@pytest_asyncio.fixture
async def house(db_session, monkeypatch, tmp_path):
    """A clean household: no rooms, the household word as the test sets it,
    the published word pinned to MODEL, the wake-models folder in tmp."""
    monkeypatch.setattr(wake_default.settings, "wake_models_dir", str(tmp_path / "wake_models"))
    pinned = wake_default.PublishedWord(
        slug="domovoi_v1", name="Domovoi", phrase="domovoi", tag="domovoi-v1",
        sha256=hashlib.sha256(MODEL).hexdigest(), size=len(MODEL))
    monkeypatch.setitem(wake_default.PUBLISHED, "domovoi_v1", pinned)
    wake_default.reset_for_tests()
    row = (await db_session.execute(text(
        "SELECT value FROM household_settings WHERE key = 'wake_default'"))).first()
    await db_session.execute(text("DELETE FROM household_settings WHERE key = 'wake_default'"))
    await db_session.execute(text("UPDATE wake_words SET is_default = FALSE"))
    await db_session.commit()
    yield db_session
    wake_default.reset_for_tests()
    async with session_scope() as s:
        await s.execute(text("DELETE FROM household_settings WHERE key = 'wake_default'"))
        if row is not None:
            await s.execute(text("INSERT INTO household_settings (key, value) "
                                 "VALUES ('wake_default', CAST(:v AS JSONB))"),
                            {"v": json.dumps(row[0])})
        await s.execute(text("DELETE FROM wake_words WHERE slug LIKE 'test_wd_%'"))


async def _set_default(slug: str) -> None:
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO household_settings (key, value) VALUES ('wake_default', CAST(:v AS JSONB)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
            {"v": json.dumps({"slug": slug, "set_by": "test"})})


async def _state(room: str) -> dict | None:
    async with session_scope() as s:
        return await wake_default.room_state(s, room)


def _place_model(data: bytes = MODEL) -> Path:
    p = wake_default.published_path(wake_default.PUBLISHED["domovoi_v1"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class Pushes:
    def __init__(self):
        self.sent: list[tuple[str, float | None]] = []

    async def __call__(self, slug, threshold):
        self.sent.append((slug, threshold))


# ─── V054: how "an existing install" and "a new install" are told apart ────

def _seed_statements() -> list[str]:
    sql = re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8"))
    return [st.strip() for st in sql.split(";") if st.strip().upper().startswith("INSERT")]


async def _run_seed() -> str:
    async with session_scope() as s:
        for st in _seed_statements():
            await s.execute(text(st))
        return await wake_default.household_default(s)


async def test_a_new_install_starts_on_the_published_word(house):
    assert await _run_seed() == "domovoi_v1"


async def test_a_server_that_had_rooms_keeps_hey_jarvis_and_its_rooms(house):
    async with session_scope() as s:
        await s.execute(text("INSERT INTO satellite_pairings (room_id, token_hash) "
                             "VALUES ('kitchen', 'x')"))
        await s.execute(text("INSERT INTO satellites (room_id) VALUES ('office')"))
    assert await _run_seed() == "hey_jarvis"
    assert (await _state("kitchen"))["state"] == "kept"
    assert (await _state("office"))["state"] == "kept"


async def test_a_household_default_trained_word_stays_the_default(house):
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO wake_words (name, slug, phrase, status, model_ref, is_default) "
            "VALUES ('Test WD Hey Alex', 'test_wd_hey_alex', 'hey alex', 'ready', "
            "'test_wd_hey_alex', TRUE)"))
        await s.execute(text("INSERT INTO satellites (room_id) VALUES ('office')"))
    assert await _run_seed() == "test_wd_hey_alex"


async def test_the_seed_never_overwrites_a_setting(house):
    await _set_default("hey_jarvis")
    assert await _run_seed() == "hey_jarvis"


async def test_no_setting_reads_as_the_boot_word(house):
    async with session_scope() as s:
        assert await wake_default.household_default(s) == "hey_jarvis"


# ─── a room's first connection ─────────────────────────────────────────────

async def test_a_new_room_is_given_the_household_word(house):
    await _set_default("domovoi_v1")
    _place_model()
    push = Pushes()
    state = await wake_default.on_hello("kitchen", "hey_jarvis", True, push)
    assert state == "assigned" and push.sent == [("domovoi_v1", None)]
    assert await _state("kitchen") == {"state": "assigned", "slug": "domovoi_v1"}
    # Its next hello (it restarted on the new word) changes nothing.
    again = Pushes()
    assert await wake_default.on_hello("kitchen", "domovoi_v1", True, again) == "assigned"
    assert again.sent == []


async def test_a_room_that_existed_is_never_touched(house):
    await _set_default("domovoi_v1")
    _place_model()
    async with session_scope() as s:
        await s.execute(text("INSERT INTO room_wake_words (room_id, state) VALUES ('kitchen', 'kept')"))
    push = Pushes()
    assert await wake_default.on_hello("kitchen", "hey_jarvis", True, push) == "kept"
    assert push.sent == []


async def test_a_chosen_word_is_never_replaced(house):
    await _set_default("domovoi_v1")
    _place_model()
    async with session_scope() as s:
        await wake_default.mark_chosen(s, "kitchen", "hey_jarvis")
    push = Pushes()
    assert await wake_default.on_hello("kitchen", "hey_jarvis", True, push) == "chosen"
    assert push.sent == []


async def test_a_new_room_in_an_upgraded_house_matches_it(house):
    await _set_default("hey_jarvis")
    push = Pushes()
    assert await wake_default.on_hello("attic", "hey_jarvis", True, push) == "assigned"
    assert push.sent == []


async def test_a_device_that_cant_take_a_word_keeps_its_own_for_good(house):
    await _set_default("domovoi_v1")
    _place_model()
    push = Pushes()
    assert await wake_default.on_hello("hall", "hey_jarvis", False, push) == "kept"
    # Even once an update teaches it to take one.
    assert await wake_default.on_hello("hall", "hey_jarvis", True, push) == "kept"
    assert push.sent == []


async def test_a_trained_household_word_carries_its_threshold(house):
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO wake_words (name, slug, phrase, status, model_ref, threshold) "
            "VALUES ('Test WD Hey Sam', 'test_wd_hey_sam', 'hey sam', 'ready', "
            "'test_wd_hey_sam', 0.8)"))
    await _set_default("test_wd_hey_sam")
    push = Pushes()
    assert await wake_default.on_hello("den", "hey_jarvis", True, push) == "assigned"
    assert push.sent == [("test_wd_hey_sam", pytest.approx(0.8))]


# ─── when the published model can't be here ────────────────────────────────

async def test_internet_never_waits_on_the_boot_word_and_says_why(house, monkeypatch):
    await _set_default("domovoi_v1")
    from domovoi.satellite_media import fetchers

    def no_network(*a, **k):
        raise AssertionError("nothing may be downloaded under internet access never")

    monkeypatch.setattr(fetchers, "_fetch_pinned", no_network)
    monkeypatch.setattr(wake_default, "ALLOW_DOWNLOAD_UNDER_STUBS", True)
    push = Pushes()
    with egress.override_policy("never"):
        assert await wake_default.on_hello("kitchen", "hey_jarvis", True, push) == "pending"
        async with session_scope() as s:
            word = await wake_default.describe(s, "domovoi_v1")
    assert push.sent == []
    assert await _state("kitchen") == {"state": "pending", "slug": "domovoi_v1"}
    assert word["available"] is False and egress.TURNED_OFF_REASON in word["reason"]


async def test_not_published_yet_says_so(house, monkeypatch):
    await _set_default("domovoi_v1")
    monkeypatch.setitem(wake_default.PUBLISHED, "domovoi_v1", wake_default.PublishedWord(
        slug="domovoi_v1", name="Domovoi", phrase="domovoi", tag="domovoi-v1",
        sha256=None, size=None))
    push = Pushes()
    assert await wake_default.on_hello("kitchen", "hey_jarvis", True, push) == "pending"
    assert push.sent == []
    assert "isn't published yet" in wake_default.not_here_reason("domovoi_v1")


async def test_a_waiting_room_is_given_the_word_when_it_arrives(house, monkeypatch):
    await _set_default("domovoi_v1")
    from domovoi.satellite_media import fetchers

    def fetched(url, dest, *, sha256, size, progress=None):
        assert url == ("https://github.com/coders-farm-official/domovoi-wake-word-models"
                       "/releases/download/domovoi-v1/domovoi_v1.onnx")
        assert sha256 == hashlib.sha256(MODEL).hexdigest() and size == len(MODEL)
        dest.write_bytes(MODEL)

    calls = []

    def failing(*a, **k):
        calls.append(1)
        raise ValueError("HTTP 503")

    monkeypatch.setattr(wake_default, "ALLOW_DOWNLOAD_UNDER_STUBS", True)
    monkeypatch.setattr(fetchers, "_fetch_pinned", failing)
    assert await wake_default.on_hello("kitchen", "hey_jarvis", True, Pushes()) == "pending"
    assert "HTTP 503" in wake_default.not_here_reason("domovoi_v1")
    # A second hello soon after doesn't hammer the release.
    assert await wake_default.on_hello("kitchen", "hey_jarvis", True, Pushes()) == "pending"
    assert len(calls) == 1

    monkeypatch.setattr(fetchers, "_fetch_pinned", fetched)
    ok, _msg = await wake_default.ensure_published("domovoi_v1", force=True)
    assert ok and wake_default.published_ready("domovoi_v1")

    sent = []

    class Room:
        abilities = None

        async def request_set_wake_word(self, *, slug, threshold=None):
            sent.append(slug)

    from domovoi import satellite_abilities

    monkeypatch.setattr(satellite_abilities, "session_abilities",
                        lambda t: type("A", (), {"allows": lambda self, a: True})())
    given = await wake_default.give_pending({"kitchen": Room(), "nobody": Room()})
    assert given == ["kitchen"] and sent == ["domovoi_v1"]
    assert await _state("kitchen") == {"state": "assigned", "slug": "domovoi_v1"}


async def test_a_copy_that_does_not_match_the_pin_is_never_served(house):
    p = _place_model(b"something else")
    assert wake_default.published_ready("domovoi_v1") is False
    assert wake_default.remove_unverified_published() == ["domovoi_v1.onnx"]
    assert not p.exists()
    _place_model()
    assert wake_default.published_ready("domovoi_v1") is True
    assert wake_default.remove_unverified_published() == []


async def test_under_stubs_nothing_is_downloaded(house, monkeypatch):
    from domovoi.satellite_media import fetchers

    monkeypatch.setattr(fetchers, "_fetch_pinned",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("downloaded")))
    ok, msg = wake_default.fetch_published("domovoi_v1")
    assert ok is False and "USE_STUBS" in msg


# ─── what the dashboard says ───────────────────────────────────────────────

@pytest.mark.parametrize("boot_here, says", [
    (True, 'New rooms wake on "Hey Jarvis" until they can be given "Domovoi"'),
    (False, "New rooms can't wake yet"),
])
async def test_the_note_says_what_rooms_wake_on(house, monkeypatch, boot_here, says):
    from domovoi import wake_base_models

    await _set_default("domovoi_v1")
    monkeypatch.setattr(wake_base_models, "served_models",
                        lambda: {"hey_jarvis_v0.1.onnx": "x"} if boot_here else {})
    async with session_scope() as s:
        st = await wake_default.status(s, {"kitchen": "hey_jarvis"})
    assert says in st["note"]
    assert st["default"]["slug"] == "domovoi_v1" and st["default"]["available"] is False
    assert [o["slug"] for o in st["options"]][:2] == ["domovoi_v1", "hey_jarvis"]


async def test_no_note_when_the_word_is_here(house):
    await _set_default("domovoi_v1")
    _place_model()
    async with session_scope() as s:
        st = await wake_default.status(s, {})
    assert st["note"] is None and st["default"]["available"] is True


async def test_setting_the_household_word(house):
    async with session_scope() as s:
        with pytest.raises(ValueError):
            await wake_default.set_household_default(s, "no_such_word")
        await s.execute(text(
            "INSERT INTO wake_words (name, slug, phrase, status) "
            "VALUES ('Test WD Untrained', 'test_wd_untrained', 'hey bo', 'recording')"))
        with pytest.raises(ValueError, match="isn't trained yet"):
            await wake_default.set_household_default(s, "test_wd_untrained")
        # Not downloaded yet is allowed: new rooms wait on their boot word.
        word = await wake_default.set_household_default(s, "domovoi_v1")
        assert word["kind"] == "published"
        assert await wake_default.household_default(s) == "domovoi_v1"
        await s.execute(text(
            "INSERT INTO wake_words (name, slug, phrase, status, model_ref) "
            "VALUES ('Test WD Ready', 'test_wd_ready', 'hey jo', 'ready', 'test_wd_ready')"))
        await wake_default.set_household_default(s, "test_wd_ready")
        flags = dict((await s.execute(text(
            "SELECT slug, is_default FROM wake_words WHERE slug LIKE 'test_wd_%'"))).all())
    assert flags == {"test_wd_untrained": False, "test_wd_ready": True}


def test_trained_words_cant_take_a_room_word():
    from domovoi.canned_sounds import wake_slug

    for name in ("Hey Jarvis", "Domovoi v1", "Alexa"):
        assert wake_default.is_reserved_slug(wake_slug(name)), name
    assert not wake_default.is_reserved_slug(wake_slug("Domovoi"))


def test_the_published_word_comes_from_an_exact_release_tag():
    word = wake_default.PUBLISHED["domovoi_v1"]
    assert word.url.startswith(
        "https://github.com/coders-farm-official/domovoi-wake-word-models/releases/download/")
    assert "/main/" not in word.url and "latest" not in word.url
    assert word.url.endswith(f"/{word.tag}/domovoi_v1.onnx")


def test_the_published_word_is_pinned():
    word = wake_default.PUBLISHED["domovoi_v1"]
    assert word.pinned
    assert re.fullmatch(r"[0-9a-f]{64}", word.sha256) and word.size > 100_000
