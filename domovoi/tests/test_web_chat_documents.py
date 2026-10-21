"""The chat page draws documents, their notes and checked sources (chat_documents.jsx).

Driven outside a browser (jsx_interact_harness.js) with a thread whose
user row attached a PDF and whose reply carries ``doc``: the chip with
the document's summary and notes, the reply's notes, the sources (a cited
one and one only read), and a source opened as the passage sheet with the
matched sentence marked and, for a PDF, "open page N" at #page=N.
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
         "web/static/chat_documents.jsx", "web/static/chat_lookups.jsx", "web/static/chat.jsx"]
TOKEN = "a" * 32


def _setup() -> str:
    src = DATA_JS.read_text(encoding="utf-8")
    m = re.search(r"^const useRetryAfterCredential = .*?^};$", src, re.M | re.S)
    assert m
    return ("window.innerWidth = 1280;\nglobalThis.API_BASE = '';\n"
            "globalThis.withDeviceToken = (u) => u + '?device_token=t';\n"
            "globalThis.liveRelTime = () => 'now';\n" + m.group(0))


DOC = {"token": TOKEN, "name": "lease.pdf", "kind": "pdf", "pages": 3, "words": 120, "parts": 3,
       "scanned_pages": [3], "garbled_pages": [], "summary": "lease.pdf · 3 pages · 120 words",
       "notes": [{"code": "scanned_pages", "text": "Page 3 is a picture of text (scanned), so Domovoi can't search it yet."}],
       "size": 999}
THREAD = {"id": 11, "title": "About lease.pdf", "archived": False, "created_at": "2026-10-21T08:00:00Z",
          "updated_at": "2026-10-21T08:00:00Z", "message_count": 4, "last_snippet": "x"}
MESSAGES = [
    {"id": 1, "role": "user", "content": "", "images": [], "documents": [DOC], "doc": None,
     "model": None, "error": None, "created_at": "2026-10-21T08:00:00Z"},
    {"id": 2, "role": "assistant", "content": "I've read lease.pdf (3 pages). Ask me anything about it.",
     "images": None, "documents": None, "model": None, "error": None, "created_at": "2026-10-21T08:00:01Z",
     "doc": {"mode": "attached", "found": True, "sources": [], "notes": []}},
    {"id": 3, "role": "user", "content": "How much is the deposit?", "images": [], "documents": None,
     "doc": None, "model": None, "error": None, "created_at": "2026-10-21T08:00:02Z"},
    {"id": 4, "role": "assistant", "content": "The deposit is $1,500 [p. 2].", "images": None,
     "documents": None, "model": "llama3.2:3b", "error": None, "created_at": "2026-10-21T08:00:03Z",
     "stats": {"model_role": "documents"},
     "doc": {"mode": "question", "found": True, "notes": [],
             "sources": [
                 {"token": TOKEN, "name": "lease.pdf", "label": "p. 1", "page": 1,
                  "text": "The monthly rent is $1,200.", "match": "The monthly rent is $1,200.", "cited": False},
                 {"token": TOKEN, "name": "lease.pdf", "label": "p. 2", "page": 2,
                  "text": "The security deposit is $1,500. It is returned within thirty days.",
                  "match": "The security deposit is $1,500.", "cited": True},
             ]}},
    {"id": 5, "role": "user", "content": "Can I keep a cat?", "images": [], "documents": None,
     "doc": None, "model": None, "error": None, "created_at": "2026-10-21T08:00:04Z"},
    {"id": 6, "role": "assistant", "content": "I couldn't find that in the document.", "images": None,
     "documents": None, "model": None, "error": None, "created_at": "2026-10-21T08:00:05Z",
     "doc": {"mode": "question", "found": False, "sources": [],
             "notes": [{"code": "not_found", "text": "Domovoi searched lease.pdf for the words in your question. "
                                                      "If the document puts it another way, try its words."}]}},
]
API = {
    "GET /api/chat/threads": {"threads": [THREAD]},
    "GET /api/chat/models": {"installed": [], "default_model": "m", "vision_model": "v", "vision_installed": True},
    "GET /api/chat/threads/11/messages": {"messages": MESSAGES},
}

SCRIPT = r"""
h.render(); await h.settle(); h.rerender();
const has = (node, s) => node != null && (
  node === s
  || (Array.isArray(node) ? node.some((n) => has(n, s))
      : (typeof node === 'object' && node.props ? has(node.props.children, s) : false)));
await h.click((el) => el.type === 'div' && typeof el.props.onClick === 'function'
                      && has(el.props.children, 'About lease.pdf'));
const before = h.text();
const sources = h.findAll((el) => el.type === 'button' && String(el.props.className || '').startsWith('chat-doc-source'))
  .map((el) => ({ label: h.plain(el).text || '', cls: el.props.className }));
const accept = (h.find((el) => el.type === 'input' && el.props.type === 'file') || { props: {} }).props.accept;
await h.click((el) => el.type === 'button' && el.props.className === 'chat-doc-source cited');
const sheet = !!h.find((el) => el.props.className === 'chat-passage-text');
const link = h.find((el) => el.type === 'a' && String(el.props.href || '').includes('#page='));
const mark = h.find((el) => el.type === 'mark');
return { before, sources, accept, sheet, href: link && link.props.href,
         mark: mark ? h.plain(mark).text : null };
"""


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    scen = {"doc": {"files": FILES, "component": "ChatPage", "setup": _setup(), "api": API, "script": SCRIPT}}
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(scen)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["doc"]
    assert "__harness_error" not in out, out.get("__harness_error")
    return out


def test_the_attachment_shows_its_summary_and_notes(driven):
    t = " ".join(driven["before"])
    assert "lease.pdf" in t and "3 pages · 120 words" in t
    assert "Page 3 is a picture of text (scanned)" in t


def test_sources_cited_first_and_the_not_found_note(driven):
    labels = [s["cls"] for s in driven["sources"]]
    assert labels == ["chat-doc-source cited", "chat-doc-source"]
    assert any("try its words" in x for x in driven["before"])


def test_a_source_opens_the_passage_with_its_sentence_marked_and_the_pdf_page(driven):
    assert driven["sheet"] is True
    assert driven["mark"] == "The security deposit is $1,500."
    assert driven["href"] == f"/api/chat/documents/{TOKEN}/file?device_token=t#page=2"


def test_the_paperclip_takes_documents(driven):
    assert ".pdf" in driven["accept"] and ".docx" in driven["accept"] and "image/*" in driven["accept"]
