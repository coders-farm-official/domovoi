"""The places a chat is NOT shown or read (design-notes PRIVATE-CHATS.md
§2.4), pinned so a new one has to go through ``domovoi/chat_access.py``
and its enumeration test (test_chat_access_enumeration.py):

* no Home, on the web or either app, has a "recent chats" or reads a chat;
* the plugin SDK has no chat API;
* the chat-mode tool bridge (Letta) never hands a handler a chat thread,
  and never reads one;
* there is no voice "read my last chat": no voice handler reads a chat's
  messages, and only the phone voice turn (after its chat_access check)
  ever names a chat thread to a handler.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from domovoi import chat_access

ROOT = Path(__file__).resolve().parents[2]
TABLES = re.compile(r"\b(" + "|".join(chat_access.CHAT_TABLES) + r")\b")
CHAT_API = re.compile(r"/api/chat/|/api/chat\b(?!\")|chat/threads|chat\.changed")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ─── Home ───────────────────────────────────────────────────────────────

HOME_FILES = [
    "web/static/home.jsx",
    *sorted(p.relative_to(ROOT).as_posix()
            for p in (ROOT / "android/app/src/main/java/com/domovoi/app/ui/screens/home").glob("*.kt")),
    *sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "ios/Domovoi/Home").glob("*.swift")),
    *sorted(p.relative_to(ROOT).as_posix()
            for p in (ROOT / "ios/Packages/DomovoiKit/Sources/DomovoiKit").glob("Home*.swift")),
]


@pytest.mark.parametrize("rel", HOME_FILES)
def test_no_home_reads_a_chat(rel: str) -> None:
    src = _read(rel)
    assert not CHAT_API.search(src), f"{rel} reads chats; Home never shows one (no 'recent chats')"


def test_home_keeps_its_never_chat_titles_rule() -> None:
    web = " ".join(_read("web/static/home.jsx").replace("*", " ").split())
    assert "Never on Home: transcripts, voice notes, memories, people's names, chat titles" in web
    ios = " ".join(_read("ios/Domovoi/Home/HomeAttentionView.swift").replace("///", " ").split())
    assert "chat titles" in ios


def test_home_files_cover_all_three_clients() -> None:
    assert any(f.startswith("android/") for f in HOME_FILES)
    assert any(f.startswith("ios/") for f in HOME_FILES)


def test_the_home_backend_reads_no_chat() -> None:
    for p in (ROOT / "web/backend/api").glob("home*.py"):
        assert not TABLES.search(p.read_text(encoding="utf-8")), p.name


# ─── Plugin SDK ─────────────────────────────────────────────────────────


def test_the_plugin_sdk_has_no_chat_api() -> None:
    for p in sorted((ROOT / "domovoi/sdk").rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        assert not TABLES.search(src), f"{p.name} names a chat table"
        assert "chat_access" not in src and "/api/chat/" not in src, p.name
        assert "web.backend.api.chat" not in src, p.name
    import domovoi.sdk as sdk

    names = {n.lower() for n in dir(sdk)}
    assert not {n for n in names if "thread" in n or n.startswith("chat")}, names


# ─── The Letta bridge ───────────────────────────────────────────────────


def test_the_letta_bridge_never_reads_a_chat() -> None:
    for rel in ("domovoi/letta_tools.py", "domovoi/clients/letta.py"):
        src = _read(rel)
        assert not TABLES.search(src), rel
        assert "chat_thread_id" not in src, rel


@pytest.mark.asyncio
async def test_a_chat_exposed_handler_gets_no_chat_thread(monkeypatch) -> None:
    from domovoi import letta_tools
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.models import Response

    seen: list[Any] = []

    class _Probe:
        name = "probe_chat_tool"
        chat_exposed = True
        requires_network = "no"
        plugin_slug = None

        async def execute_from_tool(self, args, ctx, session):
            seen.append(ctx)
            return Response(text="done")

    monkeypatch.setitem(HANDLER_BY_NAME, "probe_chat_tool", _Probe())

    class _NoSession:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    import domovoi.db.session as db_session

    monkeypatch.setattr(db_session, "session_scope", lambda: _NoSession())
    said = await letta_tools._dispatch_handler("probe_chat_tool", {}, app=None)
    assert said == "done"
    assert seen and seen[0].chat_thread_id is None


# ─── No voice "read my last chat" ───────────────────────────────────────


def _sets_chat_thread_id(src: str) -> bool:
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.keyword) and n.arg == "chat_thread_id":
            return True
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "chat_thread_id":
                    return True
    return False


def test_only_the_checked_phone_voice_turn_names_a_chat_thread() -> None:
    """Voice printing's "print it" reads ``ctx.chat_thread_id``; the only
    path that sets it is the phone voice turn, which checks the caller
    with chat_access first."""
    setters = []
    for base in ("domovoi", "web/backend", "plugins"):
        for p in (ROOT / base).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if "/tests/" in rel:
                continue
            src = p.read_text(encoding="utf-8")
            if "chat_thread_id" in src and _sets_chat_thread_id(src):
                setters.append(rel)
    assert setters == ["domovoi/phone_voice.py"], setters
    pv = _read("domovoi/phone_voice.py")
    assert pv.index("await _check_thread(thread_id") < pv.index("chat_thread_id=int(thread_id)")


#: Voice handlers that touch a chat, and why. A new one (a "read my last
#: chat") fails here and in the enumeration test.
VOICE_CHAT_HANDLERS = {
    "domovoi/handlers/printing.py": "print it: the checked phone chat's draft (drafts.store)",
    "domovoi/handlers/media_search.py": "photos to a phone: makes its own chat (help_desk.delivery)",
    "domovoi/handlers/help_desk.py": "voice help (being removed in 1.5.0): makes its own chat",
    "domovoi/handlers/documents.py": "a letter taken by voice: makes its own shared chat (voice_docs.letters)",
}


def test_no_voice_handler_reads_a_chat() -> None:
    touching = {}
    for p in sorted((ROOT / "domovoi/handlers").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        src = p.read_text(encoding="utf-8")
        assert not TABLES.search(src) or not re.search(r"\b(SELECT|FROM)\b", src), (
            f"{rel} reads a chat table: a voice handler never reads a chat (no 'read my last chat')"
        )
        if re.search(r"drafts\.store|drafts import store|help_desk import delivery|help_desk\.delivery|"
                     r"voice_docs import letters|voice_docs\.letters", src):
            touching[rel] = True
    assert set(touching) <= set(VOICE_CHAT_HANDLERS), sorted(set(touching) - set(VOICE_CHAT_HANDLERS))
