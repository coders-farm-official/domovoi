"""Memory commands in typed chat (web/backend/api/chat_memory.py): done and
said by the code, for the person the asking device belongs to, never by
the model. Since 1.5.0 the device proves itself (its device key, or an
older app's claim key: design-notes PRIVATE-CHATS.md, "Scope addition"); a
self-asserted device id alone names nobody."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from domovoi.clients import ollama
from domovoi.handlers import memory as mem_handler
from domovoi.house_facts import FORGET_YES, MEMORY_YES
from domovoi.tests.conftest import requires_db
from web.backend.api import chat_memory as cm

SAVED = mem_handler._SAVED_REPLY
FORGET_WHAT = mem_handler._FORGET_WHAT_TEXT


# ─── Recognising the commands (no database) ──────────────────────────────

@pytest.mark.parametrize("said, kind, arg", [
    ("Remember that my locker is 12.", "remember", "my locker is 12"),
    ("can you remember that I park on Level 3", "remember", "I park on Level 3"),
    ("Hey Domovoi, remember that Anna likes Tea!", "remember", "Anna likes Tea"),
    ("Please remember this: Bob’s birthday is May 3", "remember", "Bob's birthday is May 3"),
    ("forget that my locker is 12", "forget", "my locker is 12"),
    ("Stop remembering that I work nights", "forget", "I work nights"),
    ("forget that", "forget_last", ""),
    ("Forget what I just said.", "forget_last", ""),
    ("What do you remember about me?", "list", ""),
    ("what do you know about me", "list", ""),
])
def test_the_voice_commands_are_recognised(said: str, kind: str, arg: str) -> None:
    c = cm.command(said)
    assert c is not None and (c.kind, c.arg) == (kind, arg)


@pytest.mark.parametrize("said", [
    "remember to call mom", "forget that name", "hello", "do you remember the 90s?",
    "what should I remember for my exam", "forget me",
])
def test_everything_else_is_not_a_memory_command(said: str) -> None:
    assert cm.command(said) is None


@pytest.mark.parametrize("said", [
    "Can you remember things about me?", "could you remember stuff", "can you remember facts",
    "Can you remember anything?", "can you remember everything", "can you remember what I tell you",
    "can you remember what I say", "can you remember things about us", "can you remember stuff like that",
    "would you please remember things for me", "can you forget that stuff", "can you stop remembering things",
    "remember things about me", "forget that stuff",
])
def test_a_generic_object_is_never_a_memory_command(said: str) -> None:
    # G1: the question about the ability, never "things about me" saved.
    assert cm.command(said) is None


@requires_db
@pytest.mark.parametrize("said, expected", [
    ("Can you remember things about me?", MEMORY_YES),
    ("Could you remember stuff?", MEMORY_YES),
    ("can you forget stuff about me", FORGET_YES),
])
def test_a_memory_capability_question_is_answered_and_nothing_saved(house, model, said, expected) -> None:
    with _client() as c:
        t = _thread(c)
        reply, done = _send(c, t, said, house["dev"]["ana"])
    assert reply == expected
    assert done["stats"]["model_role"] == "house_facts"
    assert _memories(house["ana"]) == []
    assert model == []


def test_forget_it_is_a_brush_off_and_forget_that_is_not() -> None:
    assert cm.command("forget it").brush_off is True
    assert cm.command("never mind, forget that").brush_off is True
    assert cm.command("forget that").brush_off is False


def test_the_chat_prompt_says_the_model_cannot_save_memories() -> None:
    p = ollama.chat_system_prompt()
    assert "You can't save or delete memories yourself" in p
    assert '"remember that …"' in p
    # The spoken Q&A prompt is not this one, and isn't touched.
    assert "remember that …" not in ollama.voice_qa_system_prompt()


def test_the_linking_line_names_a_real_settings_place() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "web" / "static" / "settings.jsx").read_text(encoding="utf-8")
    assert "{ id: 'household', label: 'Household'" in src
    assert "{ id: 'people', label: 'People'" in src
    assert cm.LINK_PLACE == "Settings → Household → People"
    assert cm.unlinked_text("save that") == (
        "I can't save that from this device yet: it isn't linked to a person. "
        'Open your name in Settings → Household → People and press "this is me" to link it.')


# ─── Through the web chat ────────────────────────────────────────────────

def _client():
    from fastapi.testclient import TestClient

    from web.backend.main import app

    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _events(body: str) -> list[tuple[str, Any]]:
    out = []
    for frame in body.split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((ev, json.loads(data)))
    return out


def _run(coro):
    return asyncio.run(coro)


async def _sql(q: str, params: dict[str, Any] | None = None) -> list[Any]:
    from web.backend.db import session_scope

    async with session_scope() as s:
        res = await s.execute(text(q), params or {})
        rows = res.all() if res.returns_rows else []
        await s.commit()
    return rows


@pytest.fixture
def model(monkeypatch):
    """The chat model, stubbed: records each call; answers "model says"."""
    import web.backend.api.chat as chat_api

    calls: list[Any] = []

    async def _stream(messages, model, **kw):
        calls.append(messages)
        yield "model says"

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)

    async def _none(*a, **kw):
        return None

    monkeypatch.setattr(chat_api.ollama_client, "tags_or_none", _none)
    monkeypatch.setattr(chat_api.ollama_client, "model_system_or_none", _none)
    from web.backend import domovoi_client

    before = domovoi_client.get_cached_snapshot()
    domovoi_client.set_cached_snapshot({})
    yield calls
    domovoi_client.set_cached_snapshot(before)


#: device id -> its device key, for the devices the ``house`` fixture keyed.
_KEYS: dict[str, str] = {}


@pytest.fixture
def house():
    """Two people (Ana, Ben), Ana's phone, Ben's phone, an unlinked
    browser and a shared screen, each holding a device key (V086); and
    Ana's old phone (``ana_old``): an app from before 1.5.0, no device key,
    a claim key from its "this is me". Removed afterwards."""
    from domovoi import chat_access
    from web.backend.api import devices as devices_api

    tag = uuid.uuid4().hex[:8]
    ana = _run(_sql("INSERT INTO people (name) VALUES (:n) RETURNING id", {"n": f"Ana {tag}"}))[0][0]
    ben = _run(_sql("INSERT INTO people (name) VALUES (:n) RETURNING id", {"n": f"Ben {tag}"}))[0][0]
    dev = {
        "ana": f"android-ana{tag}", "ben": f"android-ben{tag}",
        "nobody": f"browser-none{tag}", "shared": f"browser-shared{tag}",
        "ana_old": f"android-anaold{tag}",
    }
    for key, owner, shared in (("ana", ana, False), ("ben", ben, False), ("nobody", None, False),
                               ("shared", None, True)):
        _KEYS[dev[key]] = f"test-device-key-{key}-{tag}"
        _run(_sql("INSERT INTO devices (device_id, name, platform, owner_person_id, shared_screen, "
                  "device_key_hash, device_key_at) VALUES (:d, :n, 'android', :o, :s, :h, now())",
                  {"d": dev[key], "n": key, "o": owner, "s": shared,
                   "h": chat_access.device_key_hash(_KEYS[dev[key]])}))
    claim = f"test-claim-key-{tag}"
    _run(_sql("INSERT INTO devices (device_id, name, platform, owner_person_id, owner_claimed_at, claim_key_hash) "
              "VALUES (:d, 'ana old', 'android', :o, now(), :h)",
              {"d": dev["ana_old"], "o": ana, "h": devices_api.claim_key_hash(claim)}))
    yield {"ana": ana, "ben": ben, "dev": dev, "claim": claim}
    for d in dev.values():
        _KEYS.pop(d, None)
    _run(_sql("DELETE FROM devices WHERE device_id = ANY(:ids)", {"ids": list(dev.values())}))
    _run(_sql("DELETE FROM people WHERE id IN (:a, :b)", {"a": ana, "b": ben}))


def _memories(person_id: int) -> list[str]:
    return [r[0] for r in _run(_sql(
        "SELECT body FROM memories WHERE person_id = :p AND status = 'active' ORDER BY id", {"p": person_id}))]


def _send(c, thread_id: int, content: str, device: str | None = None, how: str = "key",
          claim: str | None = None) -> tuple[str, dict]:
    """Send as ``device``: by its device KEY (``how="key"``, for a device
    the fixture keyed; any other id is named in the body), or by a
    self-asserted id alone: the body's ``device_id`` (``"body"``) or the
    dashboard's header (``"header"``). ``claim``: an X-Domovoi-Claim-Key."""
    body: dict[str, Any] = {"content": content}
    headers = {}
    if device and how == "key" and device in _KEYS:
        headers["X-Domovoi-Device-Key"] = _KEYS[device]
    elif device and how == "header":
        headers["X-Domovoi-Device-Id"] = device
    elif device:
        body["device_id"] = device
    if claim:
        headers["X-Domovoi-Claim-Key"] = claim
    r = c.post(f"/api/chat/threads/{thread_id}/messages", json=body, headers=headers)
    assert r.status_code == 200, r.text
    ev = _events(r.text)
    done = next(d for e, d in ev if e == "done")
    return "".join(d["text"] for e, d in ev if e == "delta"), done


def _thread(c) -> int:
    return c.post("/api/chat/threads", json={}).json()["id"]


@requires_db
def test_save_from_a_linked_device(house, model) -> None:
    with _client() as c:
        t = _thread(c)
        said, done = _send(c, t, "Remember that my locker is 12.", house["dev"]["ana"])
    assert said == SAVED
    assert done["stats"]["model_role"] == "memory"
    assert done["stats"]["memory"]["action"] == "saved"
    assert done["stats"]["memory"]["person_id"] == house["ana"]
    assert done["model"] is None
    assert _memories(house["ana"]) == ["my locker is 12"]
    assert _memories(house["ben"]) == []
    assert model == []


@requires_db
@pytest.mark.parametrize("how", ["body", "header"])
@pytest.mark.parametrize("who", ["ben", "ana_old"])
def test_a_self_asserted_id_alone_acts_for_nobody(house, model, how, who) -> None:
    """1.5.0: an id the caller names itself by proves nothing. A keyed
    device named without its key, and an old app's row named without its
    claim key, are answered as an unknown device is: nothing saved, read
    or forgotten."""
    _run(_sql("INSERT INTO memories (person_id, body, source, status) VALUES (:p, 'my locker is 40', 'explicit', "
              "'active')", {"p": house["ben"]}))
    device = house["dev"][who]
    with _client() as c:
        t = _thread(c)
        said, done = _send(c, t, "remember that I like jazz", device, how=how)
        listed, _ = _send(c, t, "what do you remember about me?", device, how=how)
        forgot, _ = _send(c, t, "forget that my locker is 40", device, how=how)
    assert said == cm.unlinked_text("save that")
    assert listed == cm.unlinked_text("read your memories")
    assert forgot == cm.unlinked_text("forget that")
    assert done["stats"]["memory"] == {"action": "refused_unlinked"}
    assert _memories(house["ben"]) == ["my locker is 40"] and _memories(house["ana"]) == []
    assert model == []


@requires_db
def test_an_old_app_proves_itself_with_its_claim_key(house, model) -> None:
    """The compatibility window: an app from before 1.5.0 has no device
    key, but its claim key (from "this is me") proves it is Ana's phone."""
    old = house["dev"]["ana_old"]
    with _client() as c:
        t = _thread(c)
        said, done = _send(c, t, "remember that I like tea", old, how="body", claim=house["claim"])
        listed, _ = _send(c, t, "what do you remember about me?", old, how="header", claim=house["claim"])
        forgot, _ = _send(c, t, "forget that I like tea", old, how="body", claim=house["claim"])
        wrong, _ = _send(c, t, "remember that I like coffee", old, how="body", claim="not-the-claim-key")
    assert said == SAVED and done["stats"]["memory"]["person_id"] == house["ana"]
    assert listed.startswith("Here's what I remember: I like tea.")
    assert forgot == "OK, forgotten: I like tea."
    assert wrong == cm.unlinked_text("save that")
    assert _memories(house["ana"]) == []
    assert model == []


@requires_db
def test_a_keyed_device_proving_its_claim_key_acts_for_its_person(house, model) -> None:
    """A keyed row whose app never kept the key (a 1.4.x app the server
    minted one for) still proves itself with its claim key."""
    from web.backend.api import devices as devices_api

    claim = "test-claim-key-ben-" + uuid.uuid4().hex[:8]
    _run(_sql("UPDATE devices SET claim_key_hash = :h, owner_claimed_at = now() WHERE device_id = :d",
              {"h": devices_api.claim_key_hash(claim), "d": house["dev"]["ben"]}))
    with _client() as c:
        said, _ = _send(c, _thread(c), "remember that I like jazz", house["dev"]["ben"], how="body", claim=claim)
    assert said == SAVED and _memories(house["ben"]) == ["I like jazz"]


@requires_db
def test_a_device_key_beats_the_id_the_caller_names(house, model) -> None:
    """Ana's key with Ben's id in the body: Ana's memory, never Ben's."""
    with _client() as c:
        r = c.post(f"/api/chat/threads/{_thread(c)}/messages",
                   json={"content": "remember that I like jazz", "device_id": house["dev"]["ben"]},
                   headers={"X-Domovoi-Device-Key": _KEYS[house["dev"]["ana"]]})
    assert r.status_code == 200, r.text
    assert _memories(house["ana"]) == ["I like jazz"] and _memories(house["ben"]) == []


@requires_db
@pytest.mark.parametrize("who", ["nobody", "unknown", None])
def test_an_unlinked_device_is_told_how_to_link_and_nothing_is_saved(house, model, who) -> None:
    device = {"nobody": house["dev"]["nobody"], "unknown": "browser-neverseen1", None: None}[who]
    with _client() as c:
        t = _thread(c)
        said, done = _send(c, t, "remember that my locker is 12", device)
        listed, _ = _send(c, t, "what do you remember about me?", device)
        forgot, _ = _send(c, t, "forget that my locker is 12", device)
    assert said == cm.unlinked_text("save that")
    assert listed == cm.unlinked_text("read your memories")
    assert forgot == cm.unlinked_text("forget that")
    assert done["stats"]["memory"] == {"action": "refused_unlinked"}
    assert _run(_sql("SELECT count(*) FROM memories WHERE person_id IN (:a, :b)",
                     {"a": house["ana"], "b": house["ben"]}))[0][0] == 0
    assert model == []


@requires_db
def test_a_shared_screen_saves_reads_and_forgets_nobodys(house, model) -> None:
    _run(_sql("INSERT INTO memories (person_id, body, source, status) VALUES (:p, 'Ana secret', 'explicit', 'active')",
              {"p": house["ana"]}))
    with _client() as c:
        t = _thread(c)
        replies = [_send(c, t, m, house["dev"]["shared"])[0] for m in (
            "remember that the wifi is upstairs", "what do you remember about me", "forget that Ana secret")]
    assert replies == [cm.SHARED_SCREEN_TEXT] * 3
    assert _memories(house["ana"]) == ["Ana secret"]
    assert model == []


@requires_db
def test_list_and_forget_touch_only_the_askers_memories(house, model) -> None:
    for p, b in ((house["ana"], "my locker is 12"), (house["ben"], "my locker is 40"), (house["ben"], "I like jazz")):
        _run(_sql("INSERT INTO memories (person_id, body, source, status) VALUES (:p, :b, 'explicit', 'active')",
                  {"p": p, "b": b}))
    with _client() as c:
        t = _thread(c)
        listed, _ = _send(c, t, "What do you remember about me?", house["dev"]["ana"])
        # "locker" matches Ben's too, but only Ana's is looked at.
        forgot, done = _send(c, t, "forget that my locker", house["dev"]["ana"])
        nothing, _ = _send(c, t, "forget that I like jazz", house["dev"]["ana"])
    assert listed == "Here's what I remember: my locker is 12. " + mem_handler.FORGET_HINT
    assert forgot == "OK, forgotten: my locker is 12."
    assert done["stats"]["memory"]["action"] == "forgotten"
    assert nothing == 'I don\'t have anything saved about "I like jazz".'
    assert _memories(house["ana"]) == []
    assert _memories(house["ben"]) == ["my locker is 40", "I like jazz"]
    assert model == []


@requires_db
def test_bare_forget_after_a_save_deletes_exactly_that_row(house, model) -> None:
    _run(_sql("INSERT INTO memories (person_id, body, source, status) VALUES (:p, 'older fact', 'explicit', 'active')",
              {"p": house["ana"]}))
    with _client() as c:
        t = _thread(c)
        _send(c, t, "remember that the spare key is in the shed", house["dev"]["ana"])
        assert _memories(house["ana"]) == ["older fact", "the spare key is in the shed"]
        forgot, done = _send(c, t, "forget that", house["dev"]["ana"])
        # A second one has nothing just saved to forget.
        again, _ = _send(c, t, "forget that", house["dev"]["ana"])
    assert forgot == "OK, forgotten: the spare key is in the shed."
    assert done["stats"]["memory"]["action"] == "forgotten"
    assert again == FORGET_WHAT
    assert _memories(house["ana"]) == ["older fact"]
    assert model == []


@requires_db
def test_bare_forget_with_nothing_just_saved_asks_forget_what(house, model) -> None:
    with _client() as c:
        t = _thread(c)
        _send(c, t, "hello", house["dev"]["ana"])
        assert len(model) == 1
        said, done = _send(c, t, "forget that", house["dev"]["ana"])
        # A message in between: the save is no longer the reply before.
        _send(c, t, "remember that I like tea", house["dev"]["ana"])
        _send(c, t, "what's 2 plus 2", house["dev"]["ana"])
        late, _ = _send(c, t, "forget that", house["dev"]["ana"])
    assert said == FORGET_WHAT and done["stats"]["memory"]["action"] == "forget_what"
    assert late == FORGET_WHAT
    assert _memories(house["ana"]) == ["I like tea"]


@requires_db
def test_the_answer_to_forget_what_forgets_or_goes_to_the_model(house, model) -> None:
    _run(_sql("INSERT INTO memories (person_id, body, source, status) VALUES (:p, 'I like jazz', 'explicit', 'active')",
              {"p": house["ana"]}))
    with _client() as c:
        t = _thread(c)
        assert _send(c, t, "forget that", house["dev"]["ana"])[0] == FORGET_WHAT
        # Ben's device answering Ana's question: not his to answer.
        _send(c, t, "that I like jazz", house["dev"]["ben"])
        assert len(model) == 1 and _memories(house["ana"]) == ["I like jazz"]
        assert _send(c, t, "forget that", house["dev"]["ana"])[0] == FORGET_WHAT
        said, _ = _send(c, t, "that I like jazz", house["dev"]["ana"])
        assert _send(c, t, "forget that", house["dev"]["ana"])[0] == FORGET_WHAT
        _send(c, t, "the weather", house["dev"]["ana"])
    assert said == "OK, forgotten: I like jazz."
    assert _memories(house["ana"]) == []
    assert len(model) == 2


@requires_db
def test_bare_forget_of_someone_elses_save_deletes_nothing(house, model) -> None:
    with _client() as c:
        t = _thread(c)
        _send(c, t, "remember that my code is 1234", house["dev"]["ana"])
        said, _ = _send(c, t, "forget that", house["dev"]["ben"])
    assert said == cm.NOT_YOURS_TEXT
    assert _memories(house["ana"]) == ["my code is 1234"]


@requires_db
def test_forget_it_with_nothing_saved_is_acknowledged(house, model) -> None:
    from domovoi.handlers.dismiss import _ACKS

    with _client() as c:
        said, done = _send(c, _thread(c), "forget it", house["dev"]["ana"])
    assert said in _ACKS and done["stats"]["memory"]["action"] == "dismissed"
    assert model == []


@requires_db
def test_other_messages_still_reach_the_model(house, model) -> None:
    with _client() as c:
        t = _thread(c)
        said, done = _send(c, t, "what's a good name for a cat?", house["dev"]["ana"])
        _send(c, t, "remember to buy milk", house["dev"]["ana"])
    assert said == "model says" and done["stats"]["model_role"] == "chat"
    assert len(model) == 2
    assert "You can't save or delete memories yourself" in model[0][0]["content"]


@requires_db
def test_a_memory_reply_cannot_be_tried_again(house, model) -> None:
    with _client() as c:
        t = _thread(c)
        _send(c, t, "remember that my locker is 12", house["dev"]["ana"])
        r = c.post(f"/api/chat/threads/{t}/retry")
    assert r.status_code == 409 and r.json()["code"] == "not_retryable"
    assert _memories(house["ana"]) == ["my locker is 12"]


@requires_db
def test_try_again_on_a_model_reply_to_a_memory_command_does_it(house, model) -> None:
    """A thread from before the memory step: the model answered "remember
    that …". Try again does it now, and its words replace the model's."""
    with _client() as c:
        t = _thread(c)
        _run(_sql("INSERT INTO chat_messages (thread_id, role, content, device_id) "
                  "VALUES (:t, 'user', 'remember that my locker is 12', :d)", {"t": t, "d": house["dev"]["ana"]}))
        old = _run(_sql("INSERT INTO chat_messages (thread_id, role, content, model, stats) "
                        "VALUES (:t, 'assistant', 'Sure, I''ll remember that!', 'm', '{\"model_role\": \"chat\"}') "
                        "RETURNING id", {"t": t}))[0][0]
        r = c.post(f"/api/chat/threads/{t}/retry", headers={"X-Domovoi-Device-Key": _KEYS[house["dev"]["ana"]]})
        assert r.status_code == 200, r.text
        ev = _events(r.text)
        msgs = c.get(f"/api/chat/threads/{t}/messages").json()["messages"]
    assert "".join(d["text"] for e, d in ev if e == "delta") == SAVED
    done = next(d for e, d in ev if e == "done")
    assert done["replaces"] == old and done["stats"]["memory"]["action"] == "saved"
    assert [m["content"] for m in msgs] == ["remember that my locker is 12", SAVED]
    assert _memories(house["ana"]) == ["my locker is 12"]
    assert model == []


@requires_db
def test_try_again_from_an_unlinked_device_replaces_the_claim_with_the_linking_line(house, model) -> None:
    with _client() as c:
        t = _thread(c)
        _run(_sql("INSERT INTO chat_messages (thread_id, role, content) VALUES (:t, 'user', 'forget that')", {"t": t}))
        _run(_sql("INSERT INTO chat_messages (thread_id, role, content, model, stats) "
                  "VALUES (:t, 'assistant', 'Forgotten!', 'm', '{\"model_role\": \"chat\"}')", {"t": t}))
        r = c.post(f"/api/chat/threads/{t}/retry", headers={"X-Domovoi-Device-Key": _KEYS[house["dev"]["nobody"]]})
    assert "".join(d["text"] for e, d in _events(r.text) if e == "delta") == cm.unlinked_text("forget that")
    assert model == []


@requires_db
def test_a_failed_memory_step_says_so_and_never_asks_the_model(house, model, monkeypatch) -> None:
    async def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(cm, "reply", _boom)
    with _client() as c:
        t = _thread(c)
        said, _ = _send(c, t, "remember that my locker is 12", house["dev"]["ana"])
        other, _ = _send(c, t, "hello", house["dev"]["ana"])
    import web.backend.api.chat as chat_api

    assert said == chat_api.MEMORY_FAILED_TEXT
    assert other == "model says" and len(model) == 1
