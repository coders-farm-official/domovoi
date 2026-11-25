"""N1: a server-derived "proven admin" on a turn (owner, 2026-11-25).

* ``Context.proven_admin`` is set only from the server's own check of the
  turn's request (``admin_auth.request_is_proven_admin``: a live Bearer,
  ``check_admin_request == "ok"``), per turn, never cached: a dashboard
  chat's send / try again / heads-up answer, and the dashboard's play box
  and room controls (``main._admin_route_intent``, owner D2).
* Never: voice (a satellite, room mode, the phone chat mic, even voice ID's
  owner), ``/v1/intent``, Friend Houses mail, timers, background work; no
  request body can carry it.
* WORDS ONLY: the off answers' admin steps (``off_answers``) and the help
  desk's admin-task steps (``help_desk/admin_tasks``). Admin-only STATE
  (an update's failure details, the internet question) never enters a
  chat, even a proven admin's (D1, part b).

The cases are CAPABILITY-QUESTIONS §8 (8.1–8.7e, 8.8).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from domovoi import admin_auth, ai_features, egress, off_answers
from domovoi.help_desk import admin_tasks
from domovoi.help_desk import answer as help_answer
from domovoi.models import Context, Intent
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, make_request
from domovoi.tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"

# What each §8 case hears. Member lines (and every voice turn's), then the
# steps a proven admin hears.
ADD_ROOM_MEMBER = "An admin adds rooms, on the dashboard: Settings → Household → Rooms."
UPDATE_MEMBER = "Yes. An admin updates Domovoi on the dashboard, in Settings → System → About & updates."
PLUGINS_MEMBER = "Yes, on the dashboard: an admin installs plugins from the Plugins page."
DELETE_MEMBER = "Deleting needs the admin: an admin deletes files on the dashboard, in Files."
VIDEO_MEMBER = "I can't download videos here."
VIDEO_ADMIN = "Not on this house. An admin can add video downloads from the Plugins page."

CASES = [
    # (case, question, task key, who: "member" | "admin")
    ("8.1", "How do I add a room?", "add_room", "member"),
    ("8.2", "How do I add a room?", "add_room", "admin"),
    ("8.3", "Can you update yourself?", "update", "member"),
    ("8.4", "Can you update yourself?", "update", "admin"),
    ("8.5", "Can you install plugins?", "plugins", "member"),
    ("8.6", "Can you install plugins?", "plugins", "admin"),
    ("8.7", "Can you change the admin password?", "admin_password", "admin"),
    ("8.7d", "Can you download videos?", "video_downloads", "admin"),
    ("8.7e", "Can you download videos?", "video_downloads", "member"),
    ("8.8", "Can you delete this song?", "delete_files", "member"),
]


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ─── off_answers: the admin variant ────────────────────────────────────────


def test_the_off_lines_member_and_admin() -> None:
    assert off_answers.turn_on_line("ai") == "An admin can turn it on in Settings → Chat & AI → Models."
    assert off_answers.turn_on_line("ai", admin=True) == (
        "Turn it on in Settings → Chat & AI → Models: press turn on on the AI chat and questions card."
    )
    assert off_answers.turn_on_line("internet", admin=True) == (
        "Change that in Settings → Privacy → Internet: pick Yes or Sometimes, then press Save."
    )
    assert off_answers.with_turn_on("Drop-in is off.", "drop_in", admin=True) == (
        "Drop-in is off. Turn it on in Settings → System → Advanced → Drop-in: press show the settings, "
        "switch on Enable drop-in, then press Save."
    )
    for f in off_answers.FEATURES:
        member, admin = off_answers.turn_on_line(f), off_answers.turn_on_line(f, admin=True)
        assert member.startswith("An admin can ") and not admin.startswith("An admin")
        assert off_answers.FEATURES[f].place in admin and off_answers.FEATURES[f].steps


def test_each_admin_step_names_a_real_control() -> None:
    from domovoi.config_schema import EDITABLE_FIELDS

    models, settings_src, fh = _src("models.jsx"), _src("settings.jsx"), _src("friend_houses.jsx")
    labels = {f.label for f in EDITABLE_FIELDS}
    assert 'title="AI chat and questions"' in models and ">turn on</Button>" in models
    assert "sometimes: 'Sometimes'" in settings_src and "always: 'Yes'" in settings_src
    assert "'Save'" in settings_src and ">show the settings</Button>" in settings_src
    assert "'Turn on'" in fh
    assert {"Enable drop-in", "Find songs by their words"} <= labels


def test_the_shared_off_replies_take_the_admin_variant() -> None:
    tail = off_answers.turn_on_line("ai", admin=True)
    assert ai_features.off_lookup_line() == ai_features.OFF_LOOKUP_LINE
    assert ai_features.off_lookup_line(admin=True) == f"I can't look that up with AI turned off. {tail}"
    assert ai_features.off_feature_line("make pictures", admin=True).endswith(tail)
    assert ai_features.off_feature_line("make pictures").endswith(off_answers.turn_on_line("ai"))
    with egress.override_policy("never"):
        assert egress.spoken_offline_reply("check that", admin=True).endswith(
            off_answers.turn_on_line("internet", admin=True))
        assert egress.spoken_offline_reply("check that").endswith(off_answers.turn_on_line("internet"))
    with egress.override_policy("always"):
        # Nothing was switched off: nothing to turn on, admin or not.
        assert egress.spoken_offline_reply("check that", admin=True) == (
            "I don't have internet right now, so I can't check that.")


# ─── the help desk's admin tasks ───────────────────────────────────────────


@pytest.mark.parametrize(("case", "question", "key", "who"), CASES)
def test_each_case_matches_its_task(case, question, key, who) -> None:
    task = admin_tasks.match(question)
    assert task is not None and task.key == key, case
    said = admin_tasks.answer(task, admin=(who == "admin"))
    assert said == (task.admin if who == "admin" else task.member)
    assert said != admin_tasks.answer(task, admin=(who != "admin")), case


def test_the_member_lines_say_who_and_where() -> None:
    by = admin_tasks.TASK_BY_KEY
    assert by["add_room"].member == ADD_ROOM_MEMBER
    assert by["update"].member == UPDATE_MEMBER
    assert by["plugins"].member == PLUGINS_MEMBER
    assert by["delete_files"].member == DELETE_MEMBER
    assert by["video_downloads"].member == VIDEO_MEMBER        # 8.7e: no admin steps at all
    assert by["video_downloads"].admin == VIDEO_ADMIN          # 8.7d, as the case reads
    for t in admin_tasks.TASKS:
        if t.key != "video_downloads":
            assert "admin" in t.member.lower(), t.key


def test_the_admin_steps_name_only_real_places_and_buttons() -> None:
    by = admin_tasks.TASK_BY_KEY
    sats, settings_src, plugins, files = (_src("satellites.jsx"), _src("settings.jsx"),
                                          _src("plugins.jsx"), _src("files.jsx"))
    groups = re.findall(r"\{ id: '[a-z-]+', label: '([^']+)', icon:", settings_src)
    assert {"Household", "System"} <= set(groups)
    for words in ("prepare a room's SD card", "add a spare device", "make the code", "waiting for approval"):
        assert words in by["add_room"].admin and words in sats, words
    assert "Settings → Household → Rooms" in by["add_room"].admin
    for words in ("Check for updates", "Update Domovoi", "Restart to apply changes"):
        assert words in by["update"].admin and words in settings_src, words
    for words in ("Install from zip", "install from GitHub", "fetch & preview"):
        assert words in by["plugins"].admin and words in plugins, words
    assert "Delete" in by["delete_files"].admin and ">Delete</Button>" in files
    # There is no password-change control on the dashboard: the steps say so
    # rather than invent one, and name the reset the manual documents.
    assert "/api/auth/password" not in "".join(_src(p.name) for p in STATIC.glob("*.jsx"))
    assert "no button" in by["admin_password"].admin and "--reset-admin" in by["admin_password"].admin


def test_no_answer_names_a_plugin_or_a_provider() -> None:
    banned = re.compile(r"yout[u]be|yt[-_]?d[l]p|imagegen|radio plugin", re.I)
    for t in admin_tasks.TASKS:
        assert not banned.search(t.member + t.admin), t.key


@pytest.mark.parametrize("question", [
    "can you update my playlist", "can you add a song to the queue", "how do I add a printer",
    "can you play videos", "how do I delete a playlist", "can you remove the song from the queue",
    "can you delete this song from the playlist", "how do I download a video", "add a room",
    "can you update the shopping list", "how do I add a speaker to the kitchen",
])
def test_neighbours_are_no_admin_task(question) -> None:
    assert admin_tasks.match(question) is None


def test_with_a_downloader_installed_video_downloads_is_no_admin_task() -> None:
    rows = [{"slug": "fetcher", "enabled": True, "status": "ok",
             "manifest": {"capabilities": {"provides": [admin_tasks.DOWNLOADER_CAPABILITY]}}}]
    assert admin_tasks.provided_by(rows) == {admin_tasks.DOWNLOADER_CAPABILITY}
    assert admin_tasks.match("Can you download videos?", provided=admin_tasks.provided_by(rows)) is None
    off = [{**rows[0], "enabled": False}]
    assert admin_tasks.match("Can you download videos?", provided=admin_tasks.provided_by(off)).key == (
        "video_downloads")


def test_the_plan_answers_an_admin_task_with_no_model() -> None:
    import asyncio

    from domovoi.help_desk.index import get_index

    index = get_index()

    class Never:
        calls = 0

        async def __call__(self, messages):  # pragma: no cover — must not run
            Never.calls += 1
            return "no"

    for who, want in ((False, ADD_ROOM_MEMBER), (True, admin_tasks.TASK_BY_KEY["add_room"].admin)):
        turn = help_answer.plan("how do I add a room", index, admin=who)
        out = asyncio.run(help_answer.answer(turn, ai_on=True, ask_model=Never()))
        assert out.text == want and not out.used_model and out.covered
        assert help_answer.extractive_answer(turn) == want
    assert Never.calls == 0
    # Not an admin task: the turn is as it always was.
    plain = help_answer.plan("how do I change the wake word", index, admin=True)
    assert plain.task is None and plain.task_answer is None


# ─── who counts: the server's check, per request ───────────────────────────


@pytest.mark.asyncio
async def test_only_a_live_bearer_is_a_proven_admin(monkeypatch) -> None:
    state = install_fake_db(monkeypatch, admin=True, sessions={"live"}, device_token="dev")
    proven = admin_auth.request_is_proven_admin
    assert await proven(make_request(bearer("live"))) is True
    assert await proven(make_request(cookies={COOKIE: "live"})) is False          # cookie-only
    assert await proven(make_request({HEADER: "dev"})) is False                    # the household token
    assert await proven(make_request(bearer("gone"))) is False                     # invalid
    assert await proven(make_request()) is False                                   # no-auth
    assert await proven(None) is False
    # Asking moves no idle clock.
    assert state.acted == []
    # A session that ended is a member at once: nothing is cached.
    state.sessions.discard("live")
    assert await proven(make_request(bearer("live"))) is False
    # No admin set up yet: nobody has proven anything.
    state.admin = False
    assert await proven(make_request(bearer("live"))) is False


@pytest.mark.asyncio
async def test_a_check_that_cannot_run_is_a_member(monkeypatch) -> None:
    async def down(request, session=None, *, activity=False):
        raise admin_auth.AuthCheckUnavailable()

    monkeypatch.setattr(admin_auth, "check_admin_request", down)
    assert await admin_auth.request_is_proven_admin(make_request(bearer("x"))) is False


def test_caller_is_admin_is_unchanged() -> None:
    import inspect

    import web.backend.api.help as help_api

    src = inspect.getsource(help_api.caller_is_admin)
    assert 'result in ("ok", "cookie-only", "pre-setup")' in src


# ─── no client can set it ──────────────────────────────────────────────────


def test_request_bodies_drop_the_flag() -> None:
    from web.backend.api.chat import HeadsUpAnswer, SendBody

    for body in (Intent.model_validate({"transcript": "how do I add a room", "admin": True,
                                        "proven_admin": True}),
                 SendBody.model_validate({"content": "x", "admin": True, "proven_admin": True}),
                 HeadsUpAnswer.model_validate({"message_id": 1, "option": "a", "proven_admin": True})):
        dumped = body.model_dump()
        assert "admin" not in dumped and "proven_admin" not in dumped
    assert "proven_admin" not in Intent.model_fields
    assert Context().proven_admin is False


def test_no_route_takes_a_context_from_its_body() -> None:
    from domovoi.main import app as core_app
    from domovoi.tests.route_walk import iter_route_contexts
    from web.backend.main import app as web_app

    for app in (core_app, web_app):
        for rc in iter_route_contexts(app.routes):
            dep = getattr(rc, "dependant", None)
            for p in getattr(dep, "body_params", []) or []:
                assert getattr(p, "type_", getattr(p.field_info, "annotation", None)) is not Context, rc.path


_ALLOWED_SETTERS = {("domovoi/main.py", "_admin_route_intent")}


def _py_files():
    for top in ("domovoi", "web"):
        for p in (REPO / top).rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith("domovoi/tests"):
                continue
            yield rel, p


def _scan() -> tuple[list[tuple[str, str]], list[tuple[str, int, str]]]:
    """Every place that SETS ``proven_admin`` (a keyword on a call, an
    attribute assignment, a dict key), with its function; and every read
    of ``.proven_admin`` that is not handed on as a wording argument
    (``admin=...``)."""
    sets: list[tuple[str, str]] = []
    reads: list[tuple[str, int, str]] = []
    for rel, p in _py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def func_of(n: ast.AST, parents: dict[ast.AST, ast.AST] = parents) -> str:
            while n in parents:
                n = parents[n]
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return n.name
            return "<module>"

        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "proven_admin":
                sets.append((rel, func_of(node)))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Attribute) and t.attr == "proven_admin":
                        sets.append((rel, func_of(node)))
            elif isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and k.value == "proven_admin":
                        sets.append((rel, func_of(node)))
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setattr":
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and \
                        node.args[1].value == "proven_admin":
                    sets.append((rel, func_of(node)))
            if isinstance(node, ast.Attribute) and node.attr == "proven_admin" and isinstance(node.ctx, ast.Load):
                parent = parents.get(node)
                if not (isinstance(parent, ast.keyword) and parent.arg == "admin"):
                    reads.append((rel, node.lineno, func_of(node)))
    return sets, reads


def test_the_flag_is_set_in_one_core_place_only() -> None:
    """Every never-path (streaming, room mode, the phone chat mic,
    /v1/intent, Friend Houses, timers, workers) leaves it at False: the
    one core place that sets it is the play box, from its request."""
    sets, _ = _scan()
    assert set(sets) == _ALLOWED_SETTERS, sets
    src = (REPO / "domovoi" / "main.py").read_text(encoding="utf-8")
    assert "proven_admin=await admin_auth_mod.request_is_proven_admin(request)" in src


def test_the_flag_only_ever_picks_words() -> None:
    """Every read of ``.proven_admin`` hands it on as ``admin=`` to a
    wording helper: no code branches on it, so it can't open a gate."""
    _, reads = _scan()
    assert reads == [], reads


def test_no_gate_reads_the_flag() -> None:
    """The gates' modules never touch a turn's flag (their code, not their
    words: admin_auth's docstrings name it)."""
    for name in ("admin_auth.py", "plugin_http.py", "auth.py"):
        p = REPO / "domovoi" / name
        if not p.exists():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        touched = [n for n in ast.walk(tree)
                   if (isinstance(n, ast.Attribute) and n.attr == "proven_admin")
                   or (isinstance(n, ast.keyword) and n.arg == "proven_admin")]
        assert touched == [], name


# ─── the play box (D2): only with a live sign-in on its request ────────────


@pytest.fixture
def play_box(monkeypatch):
    import contextlib

    import domovoi.main as main

    seen: list[Context] = []

    async def fake_route(intent, ctx, session):
        seen.append(ctx)
        from domovoi.models import Response

        return Response(text="ok", session_id=None)

    @contextlib.asynccontextmanager
    async def no_db():
        yield object()

    async def no_dispatch(*a, **k):
        return None

    monkeypatch.setattr(main, "route", fake_route)
    monkeypatch.setattr(main, "session_scope", no_db)
    monkeypatch.setattr(main, "_admin_dispatch_music", no_dispatch)
    had = hasattr(main.app.state, "probe")
    old = getattr(main.app.state, "probe", None)
    main.app.state.probe = SimpleNamespace(online=True)
    yield main, seen
    if had:
        main.app.state.probe = old
    else:
        del main.app.state.probe


@pytest.mark.asyncio
async def test_the_play_box_is_a_proven_admin_only_with_a_live_bearer(monkeypatch, play_box) -> None:
    main, seen = play_box
    install_fake_db(monkeypatch, admin=True, sessions={"live"}, device_token="dev")
    await main._admin_route_intent("play jazz", "kitchen", request=make_request(bearer("live")))
    await main._admin_route_intent("play jazz", "kitchen", request=make_request({HEADER: "dev"}))
    await main._admin_route_intent("pause", "kitchen", request=make_request())          # a guest
    await main._admin_route_intent("play jazz", "kitchen",
                                   request=make_request(cookies={COOKIE: "live"}))
    await main._admin_route_intent("play jazz", "kitchen")                             # no request
    assert [c.proven_admin for c in seen] == [True, False, False, False, False]


def test_the_play_routes_check_their_own_request(monkeypatch, play_box) -> None:
    main, seen = play_box
    install_fake_db(monkeypatch, admin=True, sessions={"live"}, device_token="dev")
    c = TestClient(main.app)
    body = {"room_id": "kitchen", "query": "jazz", "proven_admin": True, "admin": True}
    assert c.post("/v1/admin/music/play", json=body, headers=bearer("live")).status_code == 200
    assert c.post("/v1/admin/music/play", json=body, headers={HEADER: "dev"}).status_code == 200
    assert [x.proven_admin for x in seen] == [True, False]


# ─── never: /v1/intent, the phone chat mic, a recognised voice ─────────────


def test_v1_intent_is_never_a_proven_admin(monkeypatch, play_box) -> None:
    main, seen = play_box
    install_fake_db(monkeypatch, admin=True, sessions={"live"}, device_token="dev")
    c = TestClient(main.app)
    r = c.post("/v1/intent", json={"transcript": "how do I add a room", "room_id": "kitchen",
                                   "admin": True, "proven_admin": True}, headers=bearer("live"))
    assert r.status_code == 200, r.text
    assert len(seen) == 1 and seen[0].proven_admin is False


@requires_db
@pytest.mark.asyncio
async def test_the_phone_chat_mic_is_never_a_proven_admin(monkeypatch) -> None:
    import domovoi.router as router_mod
    from domovoi import phone_voice
    from domovoi.main import app as core_app
    from domovoi.models import Response
    from domovoi.tests.test_phone_voice import _Whisper, _client, _thread, _turn

    seen: list[Context] = []

    async def fake_route(intent, ctx, session):
        seen.append(ctx)
        return Response(text="An admin adds rooms.", session_id=None)

    fake = _Whisper("how do I add a room")
    monkeypatch.setattr(phone_voice, "get_whisper_client", lambda: fake)
    monkeypatch.setattr(router_mod, "route", fake_route)
    core_app.dependency_overrides[admin_auth.require_device_after_setup] = lambda: None
    phone_voice.reset_for_tests()
    try:
        tid = await _thread()
        async with _client() as c:
            c.headers.update(bearer("whatever"))
            r = await _turn(c, tid, proven_admin="true", admin="true")
        assert r.status_code == 200, r.text
    finally:
        core_app.dependency_overrides.pop(admin_auth.require_device_after_setup, None)
        phone_voice.reset_for_tests()
    assert len(seen) == 1 and seen[0].proven_admin is False and seen[0].device_kind == "phone"


@requires_db
@pytest.mark.asyncio
async def test_a_voice_turn_recognised_as_the_owner_hears_the_member_answer(db_session) -> None:
    """8.7a: voice ID never makes someone an admin."""
    from sqlalchemy import text

    from domovoi.router import route

    owner = (await db_session.execute(text("INSERT INTO people (name) VALUES ('Owner') RETURNING id"))
             ).scalar_one()
    await db_session.commit()
    ctx = Context(room_id="kitchen", person_id=int(owner), presence_tier="high", online=True,
                  trigger="wake_word")
    r = await route(Intent(transcript="how do I add a room", room_id="kitchen"), ctx, db_session)
    await db_session.commit()
    assert r.matched_handler == "help_desk"
    assert r.text.startswith(ADD_ROOM_MEMBER) and "approve" not in r.text
    # The same words from the play box with a live sign-in: the steps.
    admin_ctx = Context(room_id="kitchen", online=True, proven_admin=True, answerable=False)
    r2 = await route(Intent(transcript="how do I add a room", room_id="kitchen"), admin_ctx, db_session)
    await db_session.commit()
    assert r2.text.startswith(admin_tasks.TASK_BY_KEY["add_room"].admin)


# ─── web chat: send, try again, heads-up (DB) ──────────────────────────────


@pytest.fixture
def chat(monkeypatch):
    """The web app with the admin check faked (auth_testkit), Ollama
    stubbed, and no core to ask for the help's live state."""
    import web.backend.api.chat as chat_api
    import web.backend.api.help as help_api
    from web.backend.main import app

    state = install_fake_db(monkeypatch, admin=True, sessions={"live"}, device_token="dev")
    calls: list[dict[str, Any]] = []

    async def _stream(messages, model, **kw):
        calls.append({"messages": messages})
        yield "From the manual: model words."

    async def rooms():
        return []

    async def health():
        return {"db_reachable": True, "domovoi_reachable": True}

    async def version():
        return "1.4.1"

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    monkeypatch.setattr(help_api, "_rooms", rooms)
    monkeypatch.setattr(help_api, "_health", health)
    monkeypatch.setattr(help_api, "running_version", version)
    with TestClient(app, headers={"X-Requested-With": "domovoi-tests"}) as client:
        yield SimpleNamespace(c=client, state=state, calls=calls, help_api=help_api)


ADMIN_H = {**bearer("live")}
COOKIE_H = {HEADER: "dev", "Cookie": f"{COOKIE}=live"}
MEMBER_H = {HEADER: "dev"}


def _done(r) -> dict:
    assert r.status_code == 200, r.text
    for frame in r.text.split("\n\n"):
        if frame.startswith("event: done"):
            return json.loads(frame.split("data: ", 1)[1])
    raise AssertionError(r.text)


def _thread(c: TestClient, title: str | None, headers: dict) -> int:
    r = c.post("/api/chat/threads", json={"title": title} if title else {}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _ask(c: TestClient, tid: int, q: str, headers: dict, **extra) -> dict:
    return _done(c.post(f"/api/chat/threads/{tid}/messages", json={"content": q, **extra}, headers=headers))


@requires_db
@pytest.mark.parametrize(("case", "question", "key", "who"), CASES)
@pytest.mark.parametrize("title", ["Help: Settings", None])
def test_each_case_in_a_chat(chat, case, question, key, who, title) -> None:
    """The §8 cases in a help chat and an ordinary one: a member's device
    (8.1, 8.3, 8.5, 8.7e, 8.8) and a live admin sign-in (8.2, 8.4, 8.6,
    8.7, 8.7d). No model is asked."""
    headers = ADMIN_H if who == "admin" else MEMBER_H
    tid = _thread(chat.c, title, headers)
    done = _ask(chat.c, tid, question, headers)
    task = admin_tasks.TASK_BY_KEY[key]
    assert done["content"] == (task.admin if who == "admin" else task.member), case
    assert chat.calls == [] and done["model"] is None
    assert done["stats"]["help"]["admin_task"] == key
    assert done["stats"]["help"]["admin_steps"] is (who == "admin")


@requires_db
@pytest.mark.parametrize("headers", [MEMBER_H, COOKIE_H], ids=["device-token", "cookie-only"])
def test_cookie_only_and_the_household_token_are_members(chat, headers) -> None:
    tid = _thread(chat.c, "Help: Rooms", headers)
    assert _ask(chat.c, tid, "How do I add a room?", headers)["content"] == ADD_ROOM_MEMBER


@requires_db
def test_before_setup_everyone_is_a_member(chat) -> None:
    """8.7b and pre-setup: the LAN grace lets the chat in, but nobody has
    proven anything."""
    chat.state.admin = False
    tid = _thread(chat.c, "Help: Rooms", {})
    assert _ask(chat.c, tid, "How do I add a room?", {})["content"] == ADD_ROOM_MEMBER


@requires_db
def test_a_spoofed_admin_field_is_ignored(chat) -> None:
    tid = _thread(chat.c, "Help: Rooms", MEMBER_H)
    done = _ask(chat.c, tid, "How do I add a room?", MEMBER_H, admin=True, proven_admin=True)
    assert done["content"] == ADD_ROOM_MEMBER and done["stats"]["help"]["admin_steps"] is False


@requires_db
def test_a_session_that_ends_mid_thread_gets_the_member_answer_next(chat) -> None:
    """8.7c: re-checked on every turn, never cached; "try again" too."""
    steps = admin_tasks.TASK_BY_KEY["add_room"].admin
    tid = _thread(chat.c, "Help: Rooms", ADMIN_H)
    assert _ask(chat.c, tid, "How do I add a room?", ADMIN_H)["content"] == steps
    chat.state.sessions.discard("live")                     # signed out / the idle hour
    # The same tab still sends its (now dead) Bearer: a household device
    # token lets it in, and it is a member from this turn.
    after = {**ADMIN_H, HEADER: "dev"}
    assert _ask(chat.c, tid, "How do I add a room?", after)["content"] == ADD_ROOM_MEMBER
    # Signed in again: "try again" re-checks, and gives the steps.
    chat.state.sessions.add("live")
    r = chat.c.post(f"/api/chat/threads/{tid}/retry", headers=ADMIN_H)
    assert _done(r)["content"] == steps
    chat.state.sessions.discard("live")
    r = chat.c.post(f"/api/chat/threads/{tid}/retry", headers=after)
    assert _done(r)["content"] == ADD_ROOM_MEMBER


@requires_db
def test_admin_only_state_never_enters_a_chat_even_a_proven_admins(chat, monkeypatch) -> None:
    """D1, part b: an update's failure details and the internet question
    stay out of the chat for a live admin sign-in; the finding says where
    an admin reads them."""
    async def failed_update():
        return {"status": "failed", "error": "pip exploded in /srv/secret-build-dir",
                "steps": [{"name": "deps", "status": "failed"}]}

    seen: list[bool] = []
    real = chat.help_api.household_findings

    async def spy(rooms, plugins, *, admin):
        seen.append(admin)
        return await real(rooms, plugins, admin=admin)

    monkeypatch.setattr(chat.help_api, "_last_update", failed_update)
    monkeypatch.setattr(chat.help_api, "household_findings", spy)
    monkeypatch.setattr(chat.help_api.egress, "policy", lambda: "")
    tid = _thread(chat.c, "Help: Settings", ADMIN_H)
    _ask(chat.c, tid, "why did the update fail?", ADMIN_H)
    system = chat.calls[-1]["messages"][0]["content"]
    assert seen == [False]
    assert "secret-build-dir" not in system and "the step that failed" not in system
    assert "tell Domovoi whether this box has internet" not in system
    assert "an admin can read why in Settings → System → About & updates" in system


@requires_db
def test_the_flag_never_changes_what_a_chat_may_do(chat) -> None:
    """A proven admin's chat turn is still the chat: the admin's steps are
    words; nothing the admin tier gates runs from a chat turn."""
    tid = _thread(chat.c, None, MEMBER_H)
    # A member's device can't reach an admin route by asking in chat…
    assert _ask(chat.c, tid, "Can you update yourself?", MEMBER_H)["content"] == UPDATE_MEMBER
    # …and an admin's chat answer is the same fixed words, no action.
    done = _ask(chat.c, tid, "Can you update yourself?", ADMIN_H)
    assert done["content"] == admin_tasks.TASK_BY_KEY["update"].admin
    assert done["error"] is None and not done.get("draft")
