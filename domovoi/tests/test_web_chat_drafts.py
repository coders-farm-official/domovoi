"""The chat page draws drafts as cards and opens them in the document panel
(chat_drafts.jsx, documents plan D4).

Driven outside a browser (jsx_interact_harness.js) with a thread whose
replies made v1 and v2 of a cover letter and offered to print it: the cards
(kind · title, the version chip, "latest v2" on the older one, the one line
of what changed), the panel with its version picker and the changes view
(on for a version this browser hasn't seen), saving a version, a hand edit
saved as the next version, and the print card opening the Print dialog on
the draft's own routes. Wide (1,600 px) the panel sits beside the
conversation; narrow (390 px) it takes the thread's place above the
composer.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
DATA_JS = REPO_ROOT / "web" / "static" / "data.js"
FILES = ["web/static/components.jsx", "web/static/chat_markdown.js", "web/static/chat_details.js",
         "web/static/chat_documents.jsx", "web/static/chat_drafts.jsx", "web/static/chat.jsx",
         "web/static/printers.jsx"]


def _setup(width: int) -> str:
    src = DATA_JS.read_text(encoding="utf-8")
    m = re.search(r"^const useRetryAfterCredential = .*?^};$", src, re.M | re.S)
    assert m
    return (f"window.innerWidth = {width};\nglobalThis.API_BASE = '';\n"
            "globalThis.withDeviceToken = (u) => u + (u.includes('?') ? '&' : '?') + 'device_token=t';\n"
            "globalThis.useDebouncedValue = (v) => v;\n"
            "globalThis.deviceBlockReason = () => null;\n"
            "globalThis.liveRelTime = () => 'now';\n" + m.group(0))


V1 = "Dear Ms. Lee,\n\nI am writing to apply for the warehouse lead job.\n\nI would welcome a chat.\n\nSincerely,\n\nJordan"
V2 = "Dear Ms. Lee,\n\nI am writing to apply for the warehouse lead job.\n\nSincerely,\n\nJordan"
CARD = {"id": 5, "kind": "cover_letter", "title": "Cover letter for Acme", "template": "letter-block", "words": 20}
THREAD = {"id": 11, "title": "Cover letter", "archived": False, "created_at": "2026-10-21T08:00:00Z",
          "updated_at": "2026-10-21T08:00:00Z", "message_count": 6, "last_snippet": "x"}


def _msg(i, role, content, draft=None):
    return {"id": i, "role": role, "content": content, "images": None, "documents": None, "doc": None,
            "model": "llama3.2:3b" if role == "assistant" else None, "error": None,
            "created_at": "2026-10-21T08:00:0%dZ" % i, "draft": draft,
            "stats": {"model_role": "draft"} if draft else None}


MESSAGES = [
    _msg(1, "user", "Write a cover letter for Acme"),
    _msg(2, "assistant", "First draft of your cover letter: 24 words.", {**CARD, "version": 1, "action": "create"}),
    _msg(3, "user", "shorter"),
    _msg(4, "assistant", "Version 2: cut 5 words (24 → 19).", {**CARD, "version": 2, "action": "revise",
                                                                   "change": "cut 5 words (24 → 19)"}),
    _msg(5, "user", "print it"),
    _msg(6, "assistant", "Ready to print “Cover letter for Acme”, version 2. Check the preview, then press Print.",
         {**CARD, "version": 2, "action": "print"}),
]
VERSIONS = [
    {"version": 1, "body": V1, "display": V1, "instruction": None, "author": "model", "source": "first",
     "saved": [], "words": 24, "changes": None, "created_at": "2026-10-21T08:00:02Z"},
    {"version": 2, "body": V2, "display": V2, "instruction": "shorter", "author": "model", "source": "revision",
     "saved": [], "words": 19, "created_at": "2026-10-21T08:00:04Z",
     "changes": [["=", "Dear Ms. Lee,\n\nI am writing to apply for the warehouse lead job.\n\n"],
                 ["-", "I would welcome a chat.\n\n"], ["=", "Sincerely,\n\nJordan"]]},
]
API = {
    "GET /api/chat/threads": {"threads": [THREAD]},
    "GET /api/chat/models": {"installed": [], "default_model": "m", "vision_model": "v", "vision_installed": True},
    "GET /api/chat/threads/11/messages": {"messages": MESSAGES},
    "GET /api/chat/threads/11/drafts": {"drafts": [{**CARD, "version": 2, "first_lines": "Dear Ms. Lee,",
                                                    "format": "docx"}]},
    "GET /api/chat/drafts/5": {"draft": {**CARD, "thread_id": 11, "format": "docx"}, "versions": VERSIONS},
    "GET /api/printers": {"has_printer": True, "has_scanner": False, "printers": [
        {"id": 1, "name": "office printer", "can_print": True, "choices": {}}], "printable": {"exts": [".pdf"]}},
    "POST /api/chat/drafts/5/save": {"rel_path": "From Chat/Cover letter for Acme v1.docx", "format": "docx",
                                     "version": 1, "check": {"ok": True, "line": "text readable · single column"}},
    "POST /api/chat/drafts/5/versions": {"version": {"version": 3, "author": "person", "source": "edited"}},
    "POST /api/chat/drafts/5/print/preview": {"title": "Cover letter for Acme v2", "summary": "1 page",
                                              "pages_total": 1, "printer_id": 1, "thumbs": []},
}

SCRIPT = r"""
const all = () => h.tree();
const textOf = (el) => all().filter((x) => x === el || h.ancestors(x).includes(el)).map((x) => x.text)
  .filter(Boolean).join(' ');
h.render(); await h.settle(); h.rerender();
if (h.find((el) => el.type === 'button' && textOf(el).includes('your chats'))) {
  await h.click((el) => el.type === 'button' && textOf(el).includes('your chats'));
}
await h.click((el) => el.type === 'div' && typeof el.props.onClick === 'function'
                      && textOf(el).includes('Cover letter'));
await h.settle(); h.rerender();
const cards = h.findAll((el) => el.props && el.props.className === 'chat-draft-card').map((el) => textOf(el));
// Open v2 from its card.
const opens = h.findAll((el) => el.type === 'button' && textOf(el).trim() === 'open');
const openV2 = opens[1];
await openV2.props.onClick(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
const canvas = !!h.find((el) => el.props && typeof el.props.className === 'string' && el.props.className.startsWith('chat-canvas'));
const wide = !!h.find((el) => el.props && el.props.className === 'chat-canvas wide');
const tabs = h.findAll((el) => el.type === 'button' && el.props.role === 'tab').map((el) => textOf(el));
const del = h.find((el) => el.type === 'del');
const threadHidden = !!h.find((el) => el.props && el.props.className === 'chat-thread' && el.props.style
                                     && el.props.style.display === 'none');
const composer = !!h.find((el) => el.props && el.props.className === 'chat-composer');
// v1, then save it as .docx.
await h.click((el) => el.type === 'button' && el.props.role === 'tab' && textOf(el) === 'v1');
const v1Text = h.text().join(' ');
await h.click((el) => el.type === 'button' && textOf(el).includes('save .docx'));
await h.settle(); h.rerender();
const savedLine = h.text().join(' ');
// A hand edit, saved as v3.
await h.click((el) => el.type === 'button' && textOf(el).trim() === 'edit');
await h.change((el) => el.type === 'textarea' && el.props['aria-label'] === 'edit the draft', 'Dear Ms. Lee,\n\nHello.');
await h.click((el) => el.type === 'button' && textOf(el).includes('save as v3'));
await h.settle(); h.rerender();
// The print card's Print opens the dialog on the draft's routes.
await h.click((el) => el.type === 'button' && textOf(el).trim() === 'Print');
await h.settle(); h.rerender(); await h.settle();
return { cards, canvas, wide, tabs, del: del ? (del.text || textOf(del)) : null, threadHidden, composer, v1Text, savedLine,
         calls: h.calls.filter((c) => c.method !== 'GET') };
"""


def _drive(width: int) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    scen = {"d": {"files": FILES, "component": "ChatPage", "setup": _setup(width), "api": API, "script": SCRIPT}}
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(scen)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["d"]
    assert "__harness_error" not in out, out.get("__harness_error")
    return out


@pytest.fixture(scope="module")
def wide() -> dict:
    return _drive(1600)


@pytest.fixture(scope="module")
def narrow() -> dict:
    return _drive(390)


def test_the_replies_are_draft_cards(wide):
    first, second, printing = wide["cards"]
    assert "cover letter" in first and "Cover letter for Acme" in first and "v1" in first and "latest v2" in first
    assert "Version 2: cut 5 words" in second and "Dear Ms. Lee," in second and "latest" not in second
    assert "Ready to print" in printing and "Print" in printing


def test_the_panel_opens_beside_the_conversation_with_the_changes(wide):
    assert wide["canvas"] and wide["wide"] and not wide["threadHidden"] and wide["composer"]
    assert wide["tabs"] == ["v1", "v2"]
    assert wide["del"] == "I would welcome a chat.\n\n"


def test_narrow_the_panel_takes_the_threads_place_above_the_composer(narrow):
    assert narrow["canvas"] and not narrow["wide"] and narrow["threadHidden"] and narrow["composer"]


def test_save_edit_and_print_go_to_the_drafts_routes(wide):
    calls = wide["calls"]
    save = next(c for c in calls if c["path"] == "/api/chat/drafts/5/save")
    assert save["body"]["version"] == 1 and save["body"]["format"] == "docx"
    assert "text readable · single column" in wide["savedLine"]
    edit = next(c for c in calls if c["path"] == "/api/chat/drafts/5/versions")
    assert edit["body"] == {"text": "Dear Ms. Lee,\n\nHello.", "base_version": 2}
    prev = next(c for c in calls if c["path"] == "/api/chat/drafts/5/print/preview")
    assert prev["body"]["version"] == 2
    assert not any(c["path"].startswith("/api/print/") for c in calls)
