"""Nothing reads or writes a chat except through ``domovoi/chat_access.py``
(design-notes PRIVATE-CHATS.md §2.9.1, the enumeration guard).

Three parts:

* **(a) SQL.** Every SQL statement under ``domovoi/`` and ``web/backend/``
  (tests excluded) that names a chat table must sit in a function on
  :data:`SQL_ALLOWED`, with its reason. A new one fails with "route it
  through chat_access or add it here with a reason". Route handlers and
  the ``visible_sql`` lists must also call ``chat_access`` themselves.
* **(b) Routes.** Every route under ``/api/chat``, ``/api/help/deliveries``
  and ``/v1/phone/voice`` in both processes carries the
  ``chat_access.chat_viewer`` dependency, or is on :data:`ROUTES_ALLOWED`.
* **(c) Behaviour**, generated from the route list: every route with a
  thread id or a child id (a draft, a job, a message, a file token) is
  asked by another device for the owner's chat. :data:`MODE` says what
  that device must get.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from domovoi import ai_features, chat_access

ROOT = Path(__file__).resolve().parents[2]

# ════════════════════════════════════════════════════════════════════════
# THE SWITCH. "private" (since V086 and the device key, 1.5.0): another
# device gets the 404 a missing chat gets, and an admin's GET gets 200.
# "shared": the 1.4.x rule (every chat is the household's), what this
# check asserted before the device key landed.
MODE = "private"
# ════════════════════════════════════════════════════════════════════════

# ─── (a) SQL ────────────────────────────────────────────────────────────

TABLES = re.compile(r"\b(" + "|".join(chat_access.CHAT_TABLES) + r")\b")
SQL_WORDS = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|JOIN|INTO)\b")

# Reasons (§2.9.1): the function is
VISIBLE = "visible_sql"            # a list filtered by chat_access.visible_sql
GATED = "after thread_for"         # a route that called chat_access first
HELPER = "after thread_for (helper)"  # only ever reached from a GATED route, for its thread
WORKER = "worker write"            # writes into a thread it was given; nothing goes to a caller
AGGREGATE = "aggregate stats"      # speeds, counts, token sweeps: no content leaves
ADMIN = "admin maintenance"
VOICE = "voice (system)"           # a voice handler, in a thread a checked request named, or one it made
ACCESS = "chat_access itself"
DIGEST = "realtime digest"         # content-free

#: ``file::qualname`` -> (reason, why). A function whose reason is VISIBLE
#: or GATED must call ``chat_access.`` (or a checked helper named in
#: :data:`CHECKED_HELPERS`) itself.
SQL_ALLOWED: dict[str, tuple[str, str]] = {
    "domovoi/chat_access.py::load_thread": (ACCESS, "the rule's own read"),
    "domovoi/chat_access.py::token_threads": (ACCESS, "which threads name a file token"),
    "domovoi/chat_access.py::create_columns": (ACCESS, "a new thread's owner and sharing"),
    "domovoi/chat_access.py::set_shared": (ACCESS, "the share switch"),
    # The video runner (core): watches a video a checked send queued.
    "domovoi/chat_video/runner.py::Runner._finish": (WORKER, "writes the video's reply into its thread"),
    "domovoi/chat_video/runner.py::Runner.run_next": (WORKER, "claims the next queued job"),
    "domovoi/chat_video/runner.py::_cancel_requested": (WORKER, "its own job's cancel flag"),
    "domovoi/chat_video/runner.py::_save_row": (WORKER, "its own job's progress"),
    "domovoi/chat_video/runner.py::_update": (WORKER, "its own job's progress"),
    "domovoi/chat_video/runner.py::results": (WORKER, "its own job's pieces, for its reply"),
    "domovoi/chat_video/runner.py::server_speeds": (AGGREGATE, "speeds of finished jobs, no content"),
    "domovoi/chat_video/runner.py::sweep_at_boot": (WORKER, "ends jobs a restart interrupted"),
    # Voice printing "print it": ctx.chat_thread_id is set only by the
    # phone voice turn, after its chat_access check (test below).
    "domovoi/drafts/store.py::latest_in_thread": (VOICE, "the checked phone chat's latest draft"),
    "domovoi/drafts/store.py::get": (VOICE, "the draft that same turn's confirmation named"),
    # Photos to a phone (voice): the core makes the chat and its delivery.
    "domovoi/help_desk/delivery.py::deliver": (VOICE, "creates the Photos chat through chat_access.create_columns"),
    "domovoi/help_desk/delivery.py::has_title_generated": (AGGREGATE, "a schema check"),
    "domovoi/help_desk/delivery.py::mark_title_kept": (VOICE, "the chat it just made"),
    "domovoi/help_desk/delivery.py::recent": (HELPER, "GET /api/help/deliveries, filtered by the caller's device"),
    "domovoi/help_desk/delivery.py::refusal": (AGGREGATE, "per-person send counts, no content leaves"),
    "domovoi/media_understanding/faces.py::strip_chat_results": (ADMIN, "removes a forgotten face's results from every chat"),
    "domovoi/phone_voice.py::<module>": (HELPER, "the row columns the checked turn returns"),
    "domovoi/phone_voice.py::_write_turn": (HELPER, "the turn into the thread run_phone_turn checked"),
    "domovoi/voice_docs/letters.py::_say_line": (VOICE, "a line into the letter chat it made"),
    "domovoi/voice_docs/letters.py::_start_thread": (VOICE, "a room's letter chat, shared (chat_access.create_columns)"),
    "domovoi/voice_docs/letters.py::write_and_save": (VOICE, "the letter's draft in the chat it made"),
    # The web's chat routes.
    "web/backend/api/chat.py::list_threads": (VISIBLE, "the thread list and its search"),
    "web/backend/api/chat.py::create_thread": (GATED, "a new thread, owned through chat_access.create_columns"),
    "web/backend/api/chat.py::patch_thread": (GATED, "rename, archive, internet, share"),
    "web/backend/api/chat.py::delete_thread": (GATED, "delete"),
    "web/backend/api/chat.py::list_messages": (GATED, "the transcript"),
    "web/backend/api/chat.py::send_message": (GATED, "the user turn"),
    "web/backend/api/chat.py::retry_reply": (GATED, "try again"),
    "web/backend/api/chat.py::answer_heads_up": (GATED, "a heads-up card's pick"),
    "web/backend/api/chat.py::decide_lookup": (GATED, "a lookup card's answer"),
    "web/backend/api/chat.py::_select_threads": (HELPER, "the list's SELECT: list_threads hands it visible_sql, "
                                                         "_one_thread a thread its route checked"),
    "web/backend/api/chat.py::_answer": (HELPER, "the reply stream of a checked send"),
    "web/backend/api/chat.py::_answer._finish_retry": (HELPER, "the reply stream of a checked retry"),
    "web/backend/api/chat.py::_answer._reply_events": (HELPER, "the reply stream of a checked send"),
    "web/backend/api/chat.py::_allowed_sites": (HELPER, "the checked thread's earlier lookups"),
    "web/backend/api/chat.py::_history_for_model": (HELPER, "the checked thread's history, for the model"),
    "web/backend/api/chat.py::_insert_assistant_row": (HELPER, "a reply into the checked thread"),
    "web/backend/api/chat.py::_letter_reply": (HELPER, "the checked send's pictures"),
    "web/backend/api/chat.py::_picture_heads_up": (HELPER, "the checked send's pictures"),
    "web/backend/api/chat.py::_previous_lookup_query": (HELPER, "the checked thread's last lookup"),
    "web/backend/api/chat.py::_write_title": (WORKER, "the title job, for a thread a checked send named"),
    "web/backend/api/chat.py::sweep_unsent_pictures": (AGGREGATE, "upload tokens still referenced, no content"),
    "web/backend/api/chat_doc_jobs.py::_checked_job": (GATED, "a job's chat, checked before the job is loaded"),
    "web/backend/api/chat_doc_jobs.py::_job": (HELPER, "loads a job _checked_job let through"),
    "web/backend/api/chat_doc_jobs.py::_save": (WORKER, "its own job's progress"),
    "web/backend/api/chat_doc_jobs.py::_start_thread_job": (HELPER, "a job for a checked send"),
    "web/backend/api/chat_doc_jobs.py::_write_reply": (WORKER, "the job's reply into its thread"),
    "web/backend/api/chat_doc_jobs.py::measured_speed": (AGGREGATE, "reply speeds, no content"),
    "web/backend/api/chat_doc_jobs.py::thread_jobs": (HELPER, "the checked thread's jobs"),
    "web/backend/api/chat_documents.py::previous_question": (HELPER, "the checked thread's last question"),
    "web/backend/api/chat_documents.py::shown_kinds": (HELPER, "the checked thread's heads-up kinds"),
    "web/backend/api/chat_documents.py::sweep_unsent": (AGGREGATE, "document tokens still referenced, no content"),
    "web/backend/api/chat_documents.py::thread_documents": (HELPER, "the checked thread's documents"),
    "web/backend/api/chat_documents.py::thread_from_document": (GATED, "a new thread, owned through chat_access.create_columns"),
    "web/backend/api/chat_drafts.py::<module>": (HELPER, "a FROM clause the draft reads share"),
    "web/backend/api/chat_drafts.py::DraftTurn.finish": (HELPER, "a draft written by a checked send"),
    "web/backend/api/chat_drafts.py::_after_draft": (HELPER, "the checked thread's last reply"),
    "web/backend/api/chat_drafts.py::_draft": (HELPER, "the draft whose chat the route checks next"),
    "web/backend/api/chat_drafts.py::attachment_print_turn": (HELPER, "the checked thread's attachments"),
    "web/backend/api/chat_drafts.py::draft_in_thread": (HELPER, "after the send's thread_for"),
    "web/backend/api/chat_drafts.py::edit_draft": (GATED, "a hand edit"),
    "web/backend/api/chat_drafts.py::latest_draft": (HELPER, "the checked thread's draft"),
    "web/backend/api/chat_drafts.py::list_all_drafts": (VISIBLE, "every visible chat's drafts"),
    "web/backend/api/chat_drafts.py::list_thread_drafts": (GATED, "one chat's drafts"),
    "web/backend/api/chat_drafts.py::save_draft": (GATED, "save to Documents (_target checks)"),
    "web/backend/api/chat_media.py::rules": (HELPER, "the checked thread's photo results"),
    "web/backend/api/chat_videos.py::_ahead": (AGGREGATE, "queue positions, ids only"),
    "web/backend/api/chat_videos.py::_live_tokens": (AGGREGATE, "video tokens in use, no content"),
    "web/backend/api/chat_videos.py::_row": (GATED, "a video job, its chat checked"),
    "web/backend/api/chat_videos.py::_server_speeds": (AGGREGATE, "speeds of finished jobs"),
    "web/backend/api/chat_videos.py::attach_reply": (AGGREGATE, "how many jobs are ahead"),
    "web/backend/api/chat_videos.py::cancel_video_job": (GATED, "cancel (_row checks first)"),
    "web/backend/api/chat_videos.py::chat_rules": (HELPER, "the checked thread's videos, for the model"),
    "web/backend/api/chat_videos.py::get_video_results": (GATED, "results (_row checks first)"),
    "web/backend/api/chat_videos.py::queue_jobs": (HELPER, "jobs for a checked send"),
    "web/backend/api/chat_videos.py::thread_tokens": (HELPER, "the checked thread's videos, before its delete"),
    "web/backend/api/chat_videos.py::thread_videos": (HELPER, "the checked thread's videos"),
    "web/backend/realtime.py::_snapshot_chat": (DIGEST, "an opaque digest: no count, time or id"),
    "web/backend/realtime.py::_snapshot_help_deliveries": (DIGEST, "ids and times, never a title"),
}

#: Helpers that do the chat_access check themselves; a GATED route that
#: calls one of them has called chat_access.
CHECKED_HELPERS = ("chat_access.", "_target(", "_row(", "_checked_job(", "_token_readable(")


def _sql_hits() -> dict[str, tuple[set[str], str]]:
    """``file::qualname`` -> (chat tables named, the function's source)."""
    out: dict[str, tuple[set[str], str]] = {}
    for base in ("domovoi", "web/backend"):
        for p in sorted((ROOT / base).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if "/tests/" in rel or "/migrations/" in rel:
                continue
            src = p.read_text(encoding="utf-8")
            if not TABLES.search(src):
                continue
            tree = ast.parse(src)
            docs = {
                id(n.body[0].value)
                for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
            }

            def walk(node: ast.AST, stack: list[ast.AST], rel: str = rel, src: str = src,
                     docs: set[int] = docs) -> None:
                for ch in ast.iter_child_nodes(node):
                    if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        walk(ch, [*stack, ch], rel, src, docs)
                        continue
                    if (isinstance(ch, ast.Constant) and isinstance(ch.value, str) and id(ch) not in docs
                            and TABLES.search(ch.value) and SQL_WORDS.search(ch.value)):
                        key = f"{rel}::{'.'.join(n.name for n in stack) or '<module>'}"
                        fn_src = ast.get_source_segment(src, stack[-1]) if stack else ""
                        tables, _ = out.get(key, (set(), fn_src or ""))
                        tables.update(TABLES.findall(ch.value))
                        out[key] = (tables, fn_src or "")
                    walk(ch, stack, rel, src, docs)

            walk(tree, [])
    return out


def test_every_sql_statement_on_a_chat_table_goes_through_chat_access() -> None:
    hits = _sql_hits()
    new = sorted(k for k in hits if k not in SQL_ALLOWED)
    assert not new, (
        "SQL on a chat table outside chat_access's view — route it through chat_access "
        "(thread_for / visible_sql) or add it here with a reason:\n  " + "\n  ".join(
            f"{k} {sorted(hits[k][0])}" for k in new)
    )


def test_gated_functions_really_call_chat_access() -> None:
    hits = _sql_hits()
    missing = [
        k for k, (reason, _) in SQL_ALLOWED.items()
        if reason in (GATED, VISIBLE) and k in hits and not any(h in hits[k][1] for h in CHECKED_HELPERS)
    ]
    assert not missing, f"listed as checked, but they never call chat_access: {missing}"


def test_the_allow_list_has_no_stale_entries() -> None:
    hits = _sql_hits()
    # chat_access's own writers arrive with the device key work (V086).
    stale = sorted(k for k in SQL_ALLOWED if k not in hits and not k.startswith("domovoi/chat_access.py::"))
    assert not stale, f"no SQL on a chat table there any more; take them off SQL_ALLOWED: {stale}"


def test_every_reason_is_one_of_the_named_kinds() -> None:
    kinds = {VISIBLE, GATED, HELPER, WORKER, AGGREGATE, ADMIN, VOICE, ACCESS, DIGEST}
    assert {r for r, _ in SQL_ALLOWED.values()} <= kinds
    assert all(why.strip() for _, why in SQL_ALLOWED.values())


# ─── (b) Routes ─────────────────────────────────────────────────────────

#: Chat routes with no chat to check: they name no thread and hand back
#: nothing from one.
ROUTES_ALLOWED: dict[tuple[str, str], str] = {
    ("GET", "/api/chat/models"): "the installed models",
    ("GET", "/api/chat/internet"): "the household's internet answer",
    ("GET", "/api/chat/lookup-sources"): "the household's lookup sources",
    ("POST", "/api/chat/lookup"): "a lookup for a phone's own chat; no thread",
    ("GET", "/api/chat/letters/ready"): "whether a letter photo can be read",
    ("POST", "/api/chat/letters/read"): "a phone's letter photo, nothing kept, no thread",
    ("POST", "/api/chat/uploads"): "stages a picture; no thread names it yet",
    ("POST", "/api/chat/documents"): "stages a document; no thread names it yet",
    ("POST", "/api/chat/documents/extract"): "reads a phone's document, keeps nothing",
    ("POST", "/api/chat/videos"): "stages a video; no thread names it yet",
    ("POST", "/api/chat/doc-jobs"): "a phone's own document job, no thread",
    ("GET", "/api/chat/doc-jobs/speed"): "this server's measured speed",
}


class _Route:
    """A route as the guard reads it: its full path, methods and dependant."""

    def __init__(self, path: str, methods: Any, dependant: Any) -> None:
        self.path, self.methods, self.dependant = path, methods, dependant


#: The web modules whose ``router`` serves chats: chat.py (prefix
#: ``/api/chat``), the routers chat.py includes into it (their own paths
#: get that prefix) and help.py (``/api/help/deliveries``). Read straight
#: from the modules, not from the global web app's route table, which a
#: test elsewhere in a CI shard can leave changed (two CI runs saw part or
#: all of the chat routes missing from it). test_the_guard_reads_every_router_chat_py_includes
#: keeps the list in step with chat.py.
CHAT_SUBROUTERS = ("chat_documents", "chat_drafts", "chat_doc_jobs", "chat_letters", "chat_videos")


def _is_chat_path(path: str) -> bool:
    return path.startswith("/api/chat") or path == "/api/help/deliveries" or path == "/v1/phone/voice"


def _shaped(r: Any) -> bool:
    return hasattr(r, "dependant") and bool(getattr(r, "methods", None)) and isinstance(getattr(r, "path", None), str)


def _module_routes() -> list[_Route]:
    """The web's chat routes, from the routers the modules define."""
    import importlib

    out: list[_Route] = []
    chat = importlib.import_module("web.backend.api.chat")
    for r in chat.router.routes:
        if _shaped(r):
            out.append(_Route(r.path, r.methods, r.dependant))
    prefix = chat.router.prefix
    for name in CHAT_SUBROUTERS:
        sub = importlib.import_module(f"web.backend.api.{name}").router
        for r in sub.routes:
            if _shaped(r):
                path = r.path if r.path.startswith(prefix) else prefix + r.path
                out.append(_Route(path, r.methods, r.dependant))
    for r in importlib.import_module("web.backend.api.help").router.routes:
        if _shaped(r):
            out.append(_Route(r.path, r.methods, r.dependant))
    return out


def _chat_routes(app: Any) -> list[Any]:
    """The chat routes: for the web, its modules' routers (plus whatever the
    app's table also has); for the core, its app's table. One per
    (methods, path)."""
    sources: list[Any] = [_Route(r.path, r.methods, r.dependant) for r in app.routes if _shaped(r)]
    if app is _web_app():
        sources = _module_routes() + sources
    out, seen = [], set()
    for r in sources:
        if not _is_chat_path(r.path):
            continue
        key = (tuple(sorted(r.methods or ())), r.path)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def test_the_guard_reads_every_router_chat_py_includes() -> None:
    src = (ROOT / "web/backend/api/chat.py").read_text(encoding="utf-8")
    aliases = dict(re.findall(r"^from web\.backend\.api import (\w+) as (\w+)$", src, re.M))
    by_alias = {alias: mod for mod, alias in aliases.items()}
    included = re.findall(r"^router\.include_router\((\w+)\.router\)", src, re.M)
    assert sorted(by_alias[a] for a in included) == sorted(CHAT_SUBROUTERS)


def _carries_marker(dependant: Any) -> bool:
    for dep in dependant.dependencies:
        if getattr(dep.call, "__chat_access__", False) or _carries_marker(dep):
            return True
    return False


def _web_app() -> Any:
    from web.backend.main import app

    return app


def _core_app() -> Any:
    from domovoi.main import app

    return app


def test_every_chat_route_in_both_processes_carries_chat_access() -> None:
    bare = []
    seen = set()
    for app in (_web_app(), _core_app()):
        for r in _chat_routes(app):
            for method in sorted(r.methods or ()):
                key = (method, r.path)
                seen.add(key)
                if key in ROUTES_ALLOWED:
                    continue
                if not _carries_marker(r.dependant):
                    bare.append(key)
    assert not bare, (
        "chat routes without chat_access.chat_viewer — add the dependency, or list the route in "
        f"ROUTES_ALLOWED with a reason: {bare}"
    )
    assert ("POST", "/v1/phone/voice") in seen
    assert ("GET", "/api/help/deliveries") in seen
    assert set(ROUTES_ALLOWED) <= seen, f"stale ROUTES_ALLOWED: {sorted(set(ROUTES_ALLOWED) - seen)}"


def test_no_allowed_route_names_a_thread_or_a_child() -> None:
    for method, path in ROUTES_ALLOWED:
        assert "{" not in path, (method, path)


# ─── (c) Behaviour, generated from the route list ──────────────────────

#: The path parameters that address a chat or one of its children.
CHILD_PARAMS = {"thread_id", "draft_id", "job_id", "token", "message_id"}

#: A body for a route that needs one (everything else gets ``{}``).
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("PATCH", "/api/chat/threads/{thread_id}"): {"json": {"title": "Renamed"}},
    ("POST", "/api/chat/threads/{thread_id}/messages"): {"json": {"content": "hello there"}},
    ("POST", "/api/chat/threads/{thread_id}/heads-up"): {"json": {"message_id": "$card", "option": "go_ahead"}},
    ("POST", "/api/chat/threads/{thread_id}/lookups/{message_id}"): {"json": {"decision": "no"}},
    ("POST", "/api/chat/threads/{thread_id}/voice"): {
        "data": {"phone_id": "android-other1"}, "files": {"audio": ("t.pcm", b"\0" * 64000, "application/octet-stream")}},
    ("POST", "/api/chat/drafts/{draft_id}/versions"): {"json": {"text": "Dear Ms. Lee,\n\nThank you.", "base_version": 1}},
}


async def _seed(s: Any, thread_id: int, up_tok: str, doc_tok: str) -> dict[str, Any]:
    """A thread's children: a user message with a picture and a document,
    a heads-up card, a lookup card, a draft, a document job, a video job."""
    await s.execute(text(
        "INSERT INTO chat_messages (thread_id, role, content, images, documents) VALUES "
        "(:t, 'user', 'what is this', CAST(:i AS JSONB), CAST(:d AS JSONB))"),
        {"t": thread_id, "i": json.dumps([{"token": up_tok, "name": "cat.png"}]),
         "d": json.dumps([{"token": doc_tok, "name": "lease.pdf", "kind": "pdf"}])})
    card = (await s.execute(text(
        "INSERT INTO chat_messages (thread_id, role, content, doc) VALUES (:t, 'assistant', 'card', "
        "CAST(:doc AS JSONB)) RETURNING id"),
        {"t": thread_id, "doc": json.dumps({"mode": "heads_up", "heads_up": {
            "kind": "long", "options": [{"id": "go_ahead"}], "chosen": None}})})).scalar_one()
    lookup = (await s.execute(text(
        "INSERT INTO chat_messages (thread_id, role, content, stats) VALUES (:t, 'assistant', 'look?', "
        "CAST(:st AS JSONB)) RETURNING id"),
        {"t": thread_id, "st": json.dumps({"lookup_card": {"state": "pending", "kind": "search", "query": "x"}})}
    )).scalar_one()
    draft = (await s.execute(text(
        "INSERT INTO chat_drafts (thread_id, kind, template, title) VALUES (:t, 'cover_letter', 'letter-block', "
        "'Cover letter') RETURNING id"), {"t": thread_id})).scalar_one()
    await s.execute(text(
        "INSERT INTO chat_draft_versions (draft_id, version, body) VALUES (:d, 1, 'Dear Ms. Lee,\n\nHello.')"),
        {"d": draft})
    doc_job = uuid.uuid4().hex
    await s.execute(text("INSERT INTO chat_doc_jobs (id, thread_id, state) VALUES (:id, :t, 'stopped')"),
                    {"id": doc_job, "t": thread_id})
    video_job = uuid.uuid4().hex
    await s.execute(text(
        "INSERT INTO chat_video_jobs (id, thread_id, token, name, duration_s, state) "
        "VALUES (:id, :t, :tok, 'clip.mp4', 3, 'done')"),
        {"id": video_job, "t": thread_id, "tok": uuid.uuid4().hex})
    return {"card": card, "lookup": lookup, "draft": draft, "doc_job": doc_job, "video_job": video_job}


def _fill(path: str, ids: dict[str, Any], *, missing: bool) -> str:
    """The route's URL for the seeded chat, or (``missing``) with its first
    id swapped for one that doesn't exist."""
    params = re.findall(r"{(\w+)}", path)
    first = params[0] if params else None
    for p in params:
        if p == "thread_id":
            v = ids["thread"]
        elif p == "draft_id":
            v = ids["draft"]
        elif p == "message_id":
            v = ids["lookup"]
        elif p == "job_id":
            v = ids["video_job"] if "/video-jobs/" in path else ids["doc_job"]
        elif p == "token":
            v = ids["doc_tok"] if path.startswith("/api/chat/documents/") else ids["up_tok"]
        else:  # pragma: no cover — a new kind of id: say which
            raise AssertionError(f"{path}: no seeded value for {{{p}}}; add one to _fill")
        if missing and p == first:
            v = "f" * 32 if p in ("job_id", "token") else 2**62
        path = path.replace("{" + p + "}", str(v))
    return path


def _body(method: str, path: str, ids: dict[str, Any]) -> dict[str, Any]:
    raw = BODIES.get((method, path))
    if raw is None:
        return {"json": {}} if method in ("POST", "PATCH", "PUT") else {}
    out = json.loads(json.dumps({k: v for k, v in raw.items() if k != "files"}))
    if "json" in out:
        out["json"] = {k: (ids["card"] if v == "$card" else v) for k, v in out["json"].items()}
    if "files" in raw:
        out["files"] = raw["files"]
    return out


#: Routes with an id that never act on a chat's child.
NOT_A_CHAT_CHILD: dict[tuple[str, str], str] = {
    ("DELETE", "/api/chat/doc-jobs/{job_id}"): "a phone's private job only; a chat's job is a 404 for everyone",
}


def _child_routes() -> list[tuple[str, str]]:
    out = []
    for r in _chat_routes(_web_app()):
        if not CHILD_PARAMS & set(re.findall(r"{(\w+)}", r.path)):
            continue
        for m in sorted(r.methods or ()):
            if (m, r.path) not in NOT_A_CHAT_CHILD:
                out.append((m, r.path))
    return out


def test_the_behaviour_check_covers_every_child_route() -> None:
    routes = _child_routes()
    assert len(routes) >= 25, routes
    for method, path in routes:
        _fill(path, {"thread": 1, "draft": 1, "lookup": 1, "video_job": "a", "doc_job": "a",
                     "doc_tok": "a", "up_tok": "a", "card": 1}, missing=False)


from domovoi.tests.auth_testkit import _db  # noqa: E402,F401 — fixture


async def _ask(c: Any, method: str, url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, bytes]:
    """(status, body). A live stream (a job's events) that is still going
    after a few seconds was answered: it is never a 404."""
    import asyncio

    try:
        r = await asyncio.wait_for(c.request(method, url, headers=headers, **body), 8)
    except asyncio.TimeoutError:
        return 200, b"<a stream still going>"
    return r.status_code, r.content
from domovoi.tests.conftest import requires_db  # noqa: E402


@requires_db
@pytest.mark.asyncio
async def test_another_device_asking_for_a_chat_by_id(_db, tmp_path, monkeypatch) -> None:
    """Generated from the route list. MODE "private": another device gets
    exactly the answer a missing chat gets, and an admin's GET gets 200.
    MODE "shared": it gets the chat (anything but that answer)."""
    from domovoi.db.session import session_scope
    from domovoi.tests.auth_testkit import HEADER, bearer, claim_admin, db_device_token, web_client
    from web.backend.api import chat as chat_api
    from web.backend.api import chat_documents as docs_api

    # The global web app must still serve every chat route; if a test
    # elsewhere left its route table changed, say so here (with what is
    # missing) rather than as a puzzling 404 below.
    served = {(m, r.path) for r in _web_app().routes if _shaped(r) for m in r.methods}
    lost = sorted(set(_child_routes()) - served)
    assert not lost, f"the web app's route table lost chat routes (state leaked from another test?): {lost}"
    monkeypatch.setattr(ai_features, "turned_off", lambda: False)
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", tmp_path / "up")
    monkeypatch.setattr(docs_api, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "up").mkdir()
    (tmp_path / "docs").mkdir()

    async def stream(messages, model, **kw):
        yield "ok"

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", stream)

    async with web_client() as c:
        admin = await claim_admin(c)
        # The admin cookie the claim set would make every later request an
        # admin's read: the devices below speak with their own keys only.
        c.cookies.clear()
        token = await db_device_token()
        base = {"X-Requested-With": "domovoi-tests", **({HEADER: token} if token else {})}

        async def device(dev: str) -> dict[str, str]:
            r = await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True}, headers=base)
            assert r.status_code == 200, r.text
            key = r.json().get("device_key")
            c.cookies.clear()  # the key cookie: each device presents its own header
            return {**base, "X-Device-Id": dev, **({chat_access.DEVICE_KEY_HEADER: key} if key else {})}

        owner = await device(f"browser-own{uuid.uuid4().hex[:8]}")
        other = await device(f"browser-oth{uuid.uuid4().hex[:8]}")
        admin_h = {**base, **bearer(admin)}

        checked = 0
        for method, path in _child_routes():
            r = await c.post("/api/chat/threads", json={"title": "Mine"}, headers=owner)
            assert r.status_code == 200, r.text
            tid = r.json()["id"]
            up_tok, doc_tok = uuid.uuid4().hex, uuid.uuid4().hex
            (tmp_path / "up" / f"{up_tok}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (tmp_path / "docs" / f"{doc_tok}.pdf").write_bytes(b"%PDF-1.4\n")
            (tmp_path / "docs" / f"{doc_tok}-parts.json").write_text(
                json.dumps({"meta": {"name": "lease.pdf"}, "parts": []}), encoding="utf-8")
            async with session_scope() as s:
                ids = await _seed(s, tid, up_tok, doc_tok)
                await s.commit()
            ids |= {"thread": tid, "up_tok": up_tok, "doc_tok": doc_tok}
            try:
                body = _body(method, path, ids)
                gone = await _ask(c, method, _fill(path, ids, missing=True), owner, body)
                got = await _ask(c, method, _fill(path, ids, missing=False), other, body)
                hidden = got == gone
                if MODE == "private":
                    assert gone[0] == 404, (method, path, gone)
                    assert hidden, (method, path, got, gone)
                    if method == "GET":
                        seen = await _ask(c, method, _fill(path, ids, missing=False), admin_h, body)
                        assert seen[0] == 200, (path, seen)
                else:
                    assert not hidden, (method, path, got)
                checked += 1
            finally:
                async with session_scope() as s:
                    await s.execute(text("DELETE FROM chat_threads WHERE id = :t"), {"t": tid})
                    await s.commit()
        assert checked == len(_child_routes())


@requires_db
@pytest.mark.asyncio
async def test_lists_and_search_follow_the_mode(_db) -> None:
    from domovoi.db.session import session_scope
    from domovoi.tests.auth_testkit import HEADER, db_device_token, web_client

    async with web_client() as c:
        token = await db_device_token()
        base = {"X-Requested-With": "domovoi-tests", **({HEADER: token} if token else {})}

        async def device(dev: str) -> dict[str, str]:
            r = await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True}, headers=base)
            key = r.json().get("device_key")
            c.cookies.clear()
            return {**base, **({chat_access.DEVICE_KEY_HEADER: key} if key else {})}

        owner = await device(f"browser-own{uuid.uuid4().hex[:8]}")
        other = await device(f"browser-oth{uuid.uuid4().hex[:8]}")
        word = "zebra" + uuid.uuid4().hex[:8]
        tid = (await c.post("/api/chat/threads", json={"title": f"About {word}"}, headers=owner)).json()["id"]
        try:
            async with session_scope() as s:
                await s.execute(text(
                    "INSERT INTO chat_drafts (thread_id, kind, template, title) VALUES (:t, 'letter', 'letter-block', 'L')"),
                    {"t": tid})
                await s.commit()
            listed = [t["id"] for t in (await c.get("/api/chat/threads", headers=other)).json()["threads"]]
            found = (await c.get("/api/chat/threads", params={"q": word}, headers=other)).json()["threads"]
            drafts = [d["thread_id"] for d in (await c.get("/api/chat/drafts", headers=other)).json()["drafts"]]
            if MODE == "private":
                assert tid not in listed and not found and tid not in drafts
            else:
                assert tid in listed and [t["id"] for t in found] == [tid] and tid in drafts
            mine = [t["id"] for t in (await c.get("/api/chat/threads", headers=owner)).json()["threads"]]
            assert tid in mine
        finally:
            async with session_scope() as s:
                await s.execute(text("DELETE FROM chat_threads WHERE id = :t"), {"t": tid})
                await s.commit()
