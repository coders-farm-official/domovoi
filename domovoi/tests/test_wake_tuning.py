""""Tune Domovoi to your household" (domovoi/wake_tuning.py).

A household copy of the published "Domovoi" word, trained with the house's
own recordings, used only in that house:

* the option's routes sit at the trainer's own tiers (admin read to look,
  admin mutation to start, undo and use again), on the core and the web;
* rooms keep the published word until the copy is trained, then switch
  (connected rooms at once, an offline one on its next hello), and the
  household default follows; a retrain keeps them on the copy;
* "Go back to the standard Domovoi word" moves them back, and "use again"
  forward again;
* the copy is never offered for upload or export beyond what every trained
  word has (its Download zip): it is not a published word, "use a
  published word" refuses it, no other word may take its name, it can't be
  deleted while rooms use it;
* its model card says it includes the household's recordings and is private.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import admin_auth, wake_default, wake_model_card, wake_tuning
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.tests.route_walk import iter_route_contexts

BASE = "domovoi_v1"
TUNED = "domovoi_v1_tuned"
MODEL = b"onnx-bytes-of-the-published-word" * 64


# ─── tiers ─────────────────────────────────────────────────────────────────

def _gates(app, method: str, path: str) -> list:
    def calls(dep):
        for d in getattr(dep, "dependencies", ()):
            if d.call is not None:
                yield d.call
            yield from calls(d)

    for rc in iter_route_contexts(app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return list(calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a route")


def test_the_option_sits_at_the_trainers_tiers():
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    # The trainer's own: creating and training a word is an admin mutation.
    assert admin_auth.require_admin_mutation in _gates(web_app, "POST", "/api/wake-words")
    assert admin_auth.require_admin_mutation in _gates(
        web_app, "POST", "/api/wake-words/{wake_word_id}/train")
    for app, prefix in ((core_app, "/v1/admin/wake/tuning"), (web_app, "/api/wake-words/tuning")):
        read = _gates(app, "GET", prefix)
        assert admin_auth.require_admin_read in read
        assert admin_auth.require_device_read not in read
        for path in (prefix, f"{prefix}/undo", f"{prefix}/use"):
            gates = _gates(app, "POST", path)
            assert admin_auth.require_admin_mutation in gates, path
            assert admin_auth.require_device_read not in gates, path


def test_the_web_routes_come_before_the_word_id_routes():
    """POST /tuning/use must not be read as a word id."""
    from web.backend.api.wake_words import router

    paths = [getattr(r, "path", "") for r in router.routes]
    first_id = next(i for i, p in enumerate(paths) if "{wake_word_id}" in p)
    for p in ("/api/wake-words/tuning", "/api/wake-words/tuning/undo", "/api/wake-words/tuning/use"):
        assert paths.index(p) < first_id


# ─── the household, rooms and a copy ───────────────────────────────────────

class Room:
    """A connected room's session: records what it was told to load."""

    def __init__(self):
        self.sent: list[tuple[str, float | None]] = []
        self.lists: list[list[str] | None] = []

    async def request_set_wake_word(self, slug, threshold=None, words=None):
        self.sent.append((slug, threshold))
        self.lists.append([w["slug"] for w in words] if words else None)


class _Request:
    headers: dict = {}
    client = None


class Pushes:
    """A hello's push: the room's whole list, main word first."""

    def __init__(self):
        self.sent: list[tuple[str, float | None]] = []
        self.lists: list[list[str]] = []

    async def __call__(self, words):
        self.sent.append((words[0]["slug"], words[0]["threshold"]))
        self.lists.append([w["slug"] for w in words])


async def _cleanup() -> None:
    async with session_scope() as s:
        await s.execute(text("DELETE FROM household_settings WHERE key IN ('wake_tuning', 'wake_default')"))
        await s.execute(text("UPDATE wake_words SET is_default = FALSE WHERE slug = :t"), {"t": TUNED})
        await s.execute(text("DELETE FROM wake_words WHERE slug = :t"), {"t": TUNED})


@pytest_asyncio.fixture
async def house(db_session, monkeypatch, tmp_path):
    """The published word pinned and on this server, the household default
    on it, training turned on, no copy yet."""
    monkeypatch.setattr(settings, "wake_models_dir", str(tmp_path / "wake_models"))
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path / "wake_clips"))
    monkeypatch.setattr(settings, "wake_word_trainer_enabled", True)
    pinned = wake_default.PublishedWord(
        slug=BASE, name="Domovoi", phrase="domovoi", tag="domovoi-v1",
        sha256=hashlib.sha256(MODEL).hexdigest(), size=len(MODEL))
    monkeypatch.setitem(wake_default.PUBLISHED, BASE, pinned)
    wake_default.reset_for_tests()
    p = wake_default.published_path(pinned)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(MODEL)
    await _cleanup()
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO household_settings (key, value) VALUES ('wake_default', CAST(:v AS JSONB))"),
            {"v": json.dumps({"slug": BASE, "set_by": "test"})})
    yield
    wake_default.reset_for_tests()
    await _cleanup()


async def _room(room: str, state: str, slug: str | None) -> None:
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO room_wake_words (room_id, state, slug) VALUES (:r, :st, :sl) "
            "ON CONFLICT (room_id) DO UPDATE SET state = EXCLUDED.state, slug = EXCLUDED.slug"),
            {"r": room, "st": state, "sl": slug})


async def _room_slug(room: str) -> str | None:
    async with session_scope() as s:
        return (await wake_default.room_state(s, room) or {}).get("slug")


async def _default() -> str:
    async with session_scope() as s:
        return await wake_default.household_default(s)


async def _start() -> dict:
    async with session_scope() as s:
        return await wake_tuning.start(s)


async def _trained(threshold: float = 0.62) -> None:
    """The trainer finished the copy (what the worker's mark_ready does)."""
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE wake_words SET status = 'ready', model_ref = slug, threshold = :t, "
            "trained_clips = 12 WHERE slug = :s"), {"s": TUNED, "t": threshold})
    (Path(settings.wake_models_dir) / f"{TUNED}.onnx").write_bytes(b"tuned-model")


async def _status() -> dict:
    async with session_scope() as s:
        return await wake_tuning.status(s, {})


# ─── start ─────────────────────────────────────────────────────────────────

async def test_start_makes_the_copy_ready_for_clips_and_moves_no_room(house):
    await _room("kitchen", "assigned", BASE)
    assert (await _status())["state"] == "off"
    row = await _start()
    assert row["slug"] == TUNED and row["phrase"] == "domovoi"
    assert row["status"] == "recording" and not row["synthetic_only"]
    assert row["name"] == wake_tuning.TUNED_NAME
    st = await _status()
    assert st["state"] == "recording" and st["use"] and not st["ready"]
    assert st["wake_word_id"] == row["id"] and st["private"] is True
    # Not trained yet: every room keeps the published word.
    kitchen = Room()
    out = await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": BASE})
    assert kitchen.sent == [] and out["moved"] == []
    assert await _room_slug("kitchen") == BASE
    assert await _default() == BASE
    # A second start doesn't make a second word.
    assert (await _start())["id"] == row["id"]


async def test_start_is_refused_with_a_reason_when_training_is_off(house, monkeypatch):
    monkeypatch.setattr(settings, "wake_word_trainer_enabled", False)
    st = await _status()
    assert st["state"] == "off" and not st["can_start"]
    assert "training" in st["reason"]


async def test_a_server_that_cant_train_says_so_not_turn_it_on(house, monkeypatch):
    """An ARM server (the Mac installer's VM) can never train: the card says
    so instead of pointing at a switch that can't help."""
    from domovoi import wake_trainer_setup

    monkeypatch.setattr(settings, "wake_word_trainer_enabled", False)
    monkeypatch.setattr(wake_trainer_setup, "machine_arch", lambda: "aarch64")
    monkeypatch.setattr(wake_trainer_setup, "enabled", lambda: False)
    st = await _status()
    assert st["state"] == "off" and not st["can_start"]
    assert "can't train" in st["reason"] and "Turn on" not in st["reason"]


async def test_the_trainer_gets_the_published_words_near_misses():
    from domovoi.workers.wake_word_trainer import trainer_env

    extra = wake_tuning.extra_negatives_env(TUNED)
    assert "domino" in extra.split(";") and "the movie" in extra.split(";")
    assert wake_tuning.extra_negatives_env("hey_alex") == ""
    assert wake_tuning.extra_negatives_env(BASE) == ""
    env = trainer_env(3600, extra_negatives=extra)
    assert env["WAKE_EXTRA_NEGATIVES"] == extra
    assert "WAKE_EXTRA_NEGATIVES" not in trainer_env(3600)
    # The wrapper passes it on to the container (and only a clean list).
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "wake_word" / "train_wake_word.py"
    spec = importlib.util.spec_from_file_location("_wake_wrapper_tuning", path)
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    assert wrapper._EXTRA_NEG_RE.match(extra)
    assert not wrapper._EXTRA_NEG_RE.match('domino"; rm -rf /')


# ─── the switch-over ───────────────────────────────────────────────────────

async def test_rooms_switch_when_the_copy_is_trained(house):
    await _room("kitchen", "assigned", BASE)
    await _room("office", "chosen", BASE)
    await _room("den", "chosen", "hey_jarvis")
    await _start()
    await _trained(0.62)
    kitchen, office, den = Room(), Room(), Room()
    out = await wake_tuning.apply({"kitchen": kitchen, "office": office, "den": den},
                                  {"kitchen": BASE, "office": BASE, "den": "hey_jarvis"})
    assert kitchen.sent == [(TUNED, 0.62)] and office.sent == [(TUNED, 0.62)]
    assert den.sent == []                       # a room on another word is left alone
    assert sorted(out["moved"]) == ["kitchen", "office"]
    assert await _room_slug("kitchen") == TUNED and await _room_slug("office") == TUNED
    assert await _room_slug("den") == "hey_jarvis"
    async with session_scope() as s:
        states = await wake_default.room_states(s)
    assert states["kitchen"]["state"] == "assigned" and states["office"]["state"] == "chosen"
    # New rooms get the copy too.
    assert await _default() == TUNED
    assert (await _status())["state"] == "in_use"


async def test_the_trainer_worker_moves_the_rooms_when_the_copy_lands(house, monkeypatch):
    await _room("kitchen", "assigned", BASE)
    await _start()
    await _trained()

    class _App:
        class state:  # noqa: N801 - a stand-in for app.state
            active_sessions = {}
            satellite_wake_word = {"kitchen": BASE}

    kitchen = Room()
    _App.state.active_sessions = {"kitchen": kitchen}
    monkeypatch.setattr(wake_tuning, "_APP", _App)
    assert await wake_tuning.after_trained("hey_alex") is None    # any other word: nothing
    out = await wake_tuning.after_trained(TUNED)
    assert out["moved"] == ["kitchen"] and kitchen.sent[0][0] == TUNED


async def test_an_offline_room_switches_on_its_next_hello(house):
    await _room("attic", "assigned", BASE)
    await _start()
    await _trained(0.62)
    out = await wake_tuning.apply({}, {"attic": BASE})
    assert out["waiting"] == ["attic"]
    # Its record only changes when it is really told.
    assert await _room_slug("attic") == BASE
    push = Pushes()
    assert await wake_tuning.on_hello("attic", BASE, True, push) == [TUNED]
    assert push.sent == [(TUNED, 0.62)]
    assert await _room_slug("attic") == TUNED
    # Already on the copy: nothing more.
    again = Pushes()
    assert await wake_tuning.on_hello("attic", TUNED, True, again) is None
    assert again.sent == []


async def test_a_room_that_cant_take_a_word_is_never_pushed(house):
    await _room("hall", "kept", None)
    await _start()
    await _trained()
    push = Pushes()
    assert await wake_tuning.on_hello("hall", BASE, False, push) is None
    assert push.sent == []


async def test_before_the_copy_is_trained_a_hello_changes_nothing(house):
    await _room("attic", "assigned", BASE)
    await _start()
    push = Pushes()
    assert await wake_tuning.on_hello("attic", BASE, True, push) is None
    assert push.sent == []


async def test_a_retrain_keeps_the_rooms_on_the_copy(house):
    await _room("kitchen", "assigned", TUNED)
    await _start()
    await _trained()
    async with session_scope() as s:
        await s.execute(text("UPDATE wake_words SET status = 'training' WHERE slug = :s"), {"s": TUNED})
    push = Pushes()
    assert await wake_tuning.on_hello("kitchen", TUNED, True, push) is None
    assert push.sent == []


async def test_a_never_tuned_house_is_untouched(house):
    await _room("kitchen", "assigned", BASE)
    push = Pushes()
    assert await wake_tuning.on_hello("kitchen", BASE, True, push) is None
    out = await wake_tuning.apply({"kitchen": Room()}, {"kitchen": BASE})
    assert out == {"moved": [], "waiting": [], "failed": []}
    assert await _default() == BASE


# ─── undo, and use again ───────────────────────────────────────────────────

async def test_go_back_to_the_standard_word_and_use_it_again(house):
    await _room("kitchen", "assigned", BASE)
    await _room("attic", "assigned", BASE)
    await _start()
    await _trained(0.62)
    kitchen = Room()
    await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": BASE})
    push = Pushes()
    await wake_tuning.on_hello("attic", BASE, True, push)
    assert await _room_slug("kitchen") == TUNED and await _room_slug("attic") == TUNED

    # Undo: the connected room goes back now, the offline one on its hello.
    async with session_scope() as s:
        assert (await wake_tuning.set_use(s, False))["use"] is False
    kitchen.sent.clear()
    out = await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": TUNED, "attic": TUNED})
    assert kitchen.sent == [(BASE, None)] and out["moved"] == ["kitchen"]
    assert out["waiting"] == ["attic"]
    assert await _room_slug("kitchen") == BASE
    assert await _default() == BASE
    back = Pushes()
    assert await wake_tuning.on_hello("attic", TUNED, True, back) == [BASE]
    assert back.sent == [(BASE, None)] and await _room_slug("attic") == BASE
    st = await _status()
    assert st["state"] == "standard" and st["ready"]
    # The copy is kept: it can be used again.
    async with session_scope() as s:
        assert await wake_tuning.set_use(s, True)
    kitchen.sent.clear()
    await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": BASE})
    assert kitchen.sent == [(TUNED, 0.62)] and await _default() == TUNED


async def test_undo_with_the_published_model_missing_keeps_rooms_where_they_are(house):
    await _room("kitchen", "assigned", BASE)
    await _start()
    await _trained()
    kitchen = Room()
    await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": BASE})
    wake_default.published_path(wake_default.PUBLISHED[BASE]).unlink()
    async with session_scope() as s:
        await wake_tuning.set_use(s, False)
    kitchen.sent.clear()
    out = await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": TUNED})
    # Never told to load a model it can't get (downloads are off in tests).
    assert kitchen.sent == [] and out["failed"] == ["kitchen"] and out["reason"]
    assert await _room_slug("kitchen") == TUNED


async def test_undo_before_ever_tuning_says_so(house):
    async with session_scope() as s:
        assert await wake_tuning.set_use(s, False) is None


# ─── it stays in the house ─────────────────────────────────────────────────

def test_the_copy_is_never_a_published_word():
    assert TUNED not in wake_default.PUBLISHED
    assert wake_tuning.base_of(TUNED) == BASE
    assert not wake_tuning.is_tuned(BASE) and not wake_tuning.is_tuned("hey_jarvis")
    # No other word may be created under its name.
    assert wake_default.is_reserved_slug(TUNED)
    assert not wake_default.is_reserved_slug("hey_alex")


def test_the_module_never_reaches_the_internet():
    """Nothing in the tuning code uploads or fetches anything itself (the
    published word's download stays wake_default's, under its pin)."""
    src = Path(wake_tuning.__file__).read_text(encoding="utf-8")
    for needle in ("http://", "https://", "urllib", "httpx", "requests", "upload",
                   "_fetch_pinned", "socket"):
        assert needle not in src.replace("never uploads", "").replace("nothing uploads", "") \
            .replace("Nothing here uploads", ""), needle


async def test_use_a_published_word_refuses_the_copy(house, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app

    monkeypatch.setattr(core_app.state, "active_sessions", {"kitchen": Room()}, raising=False)
    async with AsyncClient(transport=ASGITransport(app=core_app), base_url="http://t") as c:
        r = await c.post("/v1/admin/wake/use", json={"room_id": "kitchen", "slug": TUNED})
    assert r.status_code == 404


async def test_creating_a_word_under_the_copys_name_is_refused(house):
    from fastapi import HTTPException

    from web.backend.api.wake_words import WakeWordCreate, create_wake_word

    with pytest.raises(HTTPException) as e:
        await create_wake_word(WakeWordCreate(name="Domovoi v1 tuned", phrase="domovoi"))
    assert e.value.status_code == 409


async def test_the_copy_cant_be_deleted_while_rooms_use_it(house, monkeypatch):
    from fastapi import HTTPException

    from web.backend.api import wake_words as api

    row = await _start()
    with pytest.raises(HTTPException) as e:
        await api.delete_wake_word(row["id"], request=_Request())
    assert e.value.status_code == 409 and "standard Domovoi word" in e.value.detail
    async with session_scope() as s:
        assert row["id"] == (await wake_tuning._word(s, BASE))["id"]

    # After going back it can be deleted like any word.
    async with session_scope() as s:
        await wake_tuning.set_use(s, False)

    async def _post_admin(*a, **kw):
        return 200, {}

    monkeypatch.setattr(api, "post_admin", _post_admin)
    await api.delete_wake_word(row["id"], request=_Request())
    async with session_scope() as s:
        assert await wake_tuning._word(s, BASE) is None


async def test_the_list_marks_the_copy(house):
    from web.backend.api.wake_words import list_wake_words

    await _start()
    words = {w.slug: w for w in await list_wake_words()}
    assert words[TUNED].tuned_from == BASE


# ─── its model card ────────────────────────────────────────────────────────

def _bundle(tmp_path, monkeypatch, slug: str, trained_clips: int) -> dict[str, bytes]:
    models = tmp_path / "m"
    clips = tmp_path / "c"
    models.mkdir()
    (clips / slug).mkdir(parents=True)
    (clips / slug / "clip_0001.wav").write_bytes(b"RIFF" + bytes(40))
    monkeypatch.setattr(settings, "wake_models_dir", str(models))
    monkeypatch.setattr(settings, "wake_clips_dir", str(clips))
    (models / f"{slug}.onnx").write_bytes(b"ONNX" + bytes(range(64)))
    row = {"id": 9, "name": wake_tuning.TUNED_NAME, "slug": slug, "phrase": "domovoi",
           "threshold": 0.7, "model_ref": slug, "is_default": False, "status": "ready",
           "source_room_id": None, "clip_count": 1, "error": None, "synthetic_only": False,
           "trained_clips": trained_clips}
    with zipfile.ZipFile(io.BytesIO(wake_model_card.build_bundle(row))) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def test_the_copys_card_says_it_includes_the_households_recordings_and_is_private(
        tmp_path, monkeypatch):
    files = _bundle(tmp_path, monkeypatch, TUNED, 14)
    # The same zip every trained word's Download gives: no recording in it.
    assert sorted(n.split("/", 1)[1] for n in files) == sorted(
        [f"{TUNED}.onnx", "MODEL_CARD.md", "LICENSE-NOTICE.md", "SHA256SUMS"])
    assert not any(n.endswith(".wav") for n in files)
    md = files[f"{TUNED}/MODEL_CARD.md"].decode()
    assert "| Tuned from | Domovoi's published `domovoi_v1`" in md
    assert "| Private | yes: includes this household's recordings" in md
    assert "## Private: this household's own copy" in md
    assert "14 recordings of this household" in md
    assert "never uploads or publishes" in md
    before = md.split("## Before sharing", 1)[1]
    assert "private to this household" in before and "includes the household's recordings" in before


def test_another_words_card_says_nothing_about_tuning(tmp_path, monkeypatch):
    md = _bundle(tmp_path, monkeypatch, "hey_alex", 14)["hey_alex/MODEL_CARD.md"].decode()
    assert "Tuned from" not in md and "Private:" not in md


# ─── several words per room (V063) ─────────────────────────────────────────

async def _room_words(room: str) -> list[str] | None:
    async with session_scope() as s:
        return (await wake_default.room_state(s, room) or {}).get("slugs")


async def _room_list(room: str, state: str, slugs: list[str], device_max: int | None = None) -> None:
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO room_wake_words (room_id, state, slug, slugs) "
            "VALUES (:r, :st, :sl, :sls) ON CONFLICT (room_id) DO UPDATE SET "
            "state = EXCLUDED.state, slug = EXCLUDED.slug, slugs = EXCLUDED.slugs"),
            {"r": room, "st": state, "sl": slugs[0], "sls": slugs})
        # The device's limit lives on its own row (V072).
        await wake_default.record_device_max(s, room, device_max)


def test_the_word_is_replaced_in_its_own_place():
    assert wake_tuning.replace_word([BASE, "hey_jarvis"], BASE, TUNED) == [TUNED, "hey_jarvis"]
    assert (wake_tuning.replace_word(["hey_jarvis", BASE, "porch_cat"], BASE, TUNED)
            == ["hey_jarvis", TUNED, "porch_cat"])
    assert wake_tuning.replace_word(["hey_jarvis"], BASE, TUNED) is None
    # Never twice: the copy already there, the published word just goes.
    assert wake_tuning.replace_word([BASE, TUNED], BASE, TUNED) == [TUNED]


def test_a_rooms_list_is_what_its_device_runs_then_what_it_was_given():
    row = {"slug": BASE, "slugs": [BASE, "hey_jarvis", "porch_cat"]}
    # A device running two of three: the third is kept after them.
    assert (wake_tuning.room_words_list(row, BASE, [BASE, "hey_jarvis"])
            == [BASE, "hey_jarvis", "porch_cat"])
    # An older device that reports only its main word.
    assert wake_tuning.room_words_list(row, BASE, None) == [BASE, "hey_jarvis", "porch_cat"]
    assert wake_tuning.room_words_list(None, None, None) == []


async def test_a_room_with_two_words_switches_and_undoes_keeping_both(house):
    await _room_list("kitchen", "chosen", [BASE, "hey_jarvis"], device_max=3)
    await _start()
    await _trained(0.62)
    kitchen = Room()
    out = await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": BASE},
                                  {"kitchen": [BASE, "hey_jarvis"]})
    assert out["moved"] == ["kitchen"]
    # The whole list is pushed, the copy in the published word's place.
    assert kitchen.sent == [(TUNED, 0.62)] and kitchen.lists == [[TUNED, "hey_jarvis"]]
    assert await _room_words("kitchen") == [TUNED, "hey_jarvis"]
    assert await _room_slug("kitchen") == TUNED
    async with session_scope() as s:
        row = await wake_default.room_state(s, "kitchen")
    assert row["state"] == "chosen" and row["device_max"] == 3

    # Go back to the standard Domovoi word.
    async with session_scope() as s:
        await wake_tuning.set_use(s, False)
    kitchen = Room()
    out = await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": TUNED},
                                  {"kitchen": [TUNED, "hey_jarvis"]})
    assert out["moved"] == ["kitchen"]
    assert kitchen.sent == [(BASE, None)] and kitchen.lists == [[BASE, "hey_jarvis"]]
    assert await _room_words("kitchen") == [BASE, "hey_jarvis"]
    assert await _room_slug("kitchen") == BASE

    # Use the tuned word again.
    async with session_scope() as s:
        await wake_tuning.set_use(s, True)
    kitchen = Room()
    await wake_tuning.apply({"kitchen": kitchen}, {"kitchen": BASE},
                            {"kitchen": [BASE, "hey_jarvis"]})
    assert kitchen.lists == [[TUNED, "hey_jarvis"]]
    assert await _room_words("kitchen") == [TUNED, "hey_jarvis"]


async def test_a_room_whose_tuned_word_isnt_first_keeps_its_main_word(house):
    await _room_list("office", "chosen", ["hey_jarvis", BASE], device_max=5)
    await _start()
    await _trained(0.62)
    office = Room()
    await wake_tuning.apply({"office": office}, {"office": "hey_jarvis"},
                            {"office": ["hey_jarvis", BASE]})
    # The main word stays first, and is what the frame's slug names.
    assert office.sent == [("hey_jarvis", None)] and office.lists == [["hey_jarvis", TUNED]]
    assert await _room_words("office") == ["hey_jarvis", TUNED]
    assert await _room_slug("office") == "hey_jarvis"

    async with session_scope() as s:
        await wake_tuning.set_use(s, False)
    # Offline for the undo: on its hello the published word goes back in place.
    out = await wake_tuning.apply({}, {"office": "hey_jarvis"}, {"office": ["hey_jarvis", TUNED]})
    assert out["waiting"] == ["office"]
    push = Pushes()
    got = await wake_tuning.on_hello("office", "hey_jarvis", True, push,
                                     reported_list=["hey_jarvis", TUNED])
    assert got == ["hey_jarvis", BASE]
    assert push.lists == [["hey_jarvis", BASE]] and push.sent == [("hey_jarvis", None)]
    assert await _room_words("office") == ["hey_jarvis", BASE]
    assert await _room_slug("office") == "hey_jarvis"


async def test_a_word_the_device_isnt_running_is_still_replaced_in_the_record(house):
    """A device running 1 of its 2 given words: the copy replaces the
    published word in the room's list anyway; the device is sent no more
    than it runs, and the list never grows."""
    await _room_list("den", "chosen", ["hey_jarvis", BASE], device_max=1)
    await _start()
    await _trained()
    den = Room()
    await wake_tuning.apply({"den": den}, {"den": "hey_jarvis"}, {"den": ["hey_jarvis"]})
    assert await _room_words("den") == ["hey_jarvis", TUNED]
    assert den.sent[0][0] == "hey_jarvis"


class _FakeWS:
    def __init__(self) -> None:
        from types import SimpleNamespace

        self.app = SimpleNamespace(state=SimpleNamespace())
        self.sent: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


@pytest.mark.parametrize("wake_max,expect", [
    (None, {"type": "set_wake_word", "slug": TUNED, "threshold": 0.62}),
    (1, {"type": "set_wake_word", "slug": TUNED, "threshold": 0.62,
         "words": [{"slug": TUNED, "threshold": 0.62}]}),
    (3, {"type": "set_wake_word", "slug": TUNED, "threshold": 0.62,
         "words": [{"slug": TUNED, "threshold": 0.62}, {"slug": "hey_jarvis"}]}),
])
async def test_the_switch_reaches_the_device_as_its_whole_list_or_the_main_word(wake_max, expect):
    """The real frame: a device that said how many words it runs gets the
    list (no more than that); an older one the main word alone."""
    from domovoi.streaming import StreamSession

    ws = _FakeWS()
    session = StreamSession(ws, "tune_kitchen")  # type: ignore[arg-type]
    session.wake_max = wake_max
    await wake_tuning.session_push(session)(
        [{"slug": TUNED, "threshold": 0.62}, {"slug": "hey_jarvis", "threshold": None}])
    assert ws.sent == [expect]
