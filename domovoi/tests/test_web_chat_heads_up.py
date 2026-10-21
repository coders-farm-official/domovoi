"""The dashboard's heads-up cards and its Settings → Chat switch (chat_documents.jsx, chat.jsx; D3).

Driven outside a browser (jsx_interact_harness.js): a thread whose reply
is a heads-up card shows the card's sentence and its options instead of
the text twice, a picked card shows the pick and no buttons, "don't warn
me about this again" is kept in this browser and sent with the next
message, "choose another" records the pick and opens the picker, and the
Settings panel turns the heads-up off here and lists the silenced kinds.
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
         "web/static/chat_documents.jsx", "web/static/chat.jsx"]

STORAGE = r"""
globalThis.__store = {};
globalThis.localStorage = {
  getItem: (k) => (k in globalThis.__store ? globalThis.__store[k] : null),
  setItem: (k, v) => { globalThis.__store[k] = String(v); },
  removeItem: (k) => { delete globalThis.__store[k]; },
};
globalThis.__listeners = {};
window.addEventListener = (t, f) => { (globalThis.__listeners[t] = globalThis.__listeners[t] || []).push(f); };
window.removeEventListener = (t, f) => {
  globalThis.__listeners[t] = (globalThis.__listeners[t] || []).filter((g) => g !== f);
};
window.dispatchEvent = (e) => { (globalThis.__listeners[e.type] || []).forEach((f) => f(e)); return true; };
globalThis.Event = class { constructor(type) { this.type = type; } };
globalThis.__sent = [];
globalThis.apiFetchRaw = async (path, opts) => {
  globalThis.__sent.push({ path, body: JSON.parse(opts.body) });
  return { status: 200, headers: { get: () => null }, body: null };
};
"""


def _setup() -> str:
    src = DATA_JS.read_text(encoding="utf-8")
    m = re.search(r"^const useRetryAfterCredential = .*?^};$", src, re.M | re.S)
    assert m
    return ("window.innerWidth = 1280;\nglobalThis.API_BASE = '';\n"
            "globalThis.withDeviceToken = (u) => u + '?device_token=t';\n"
            "globalThis.liveRelTime = () => 'now';\n" + m.group(0) + STORAGE)


CARD = {"kind": "calc", "text": "Domovoi isn't reliable at arithmetic, and its answers are checked for their "
                                "pages, not their sums. It can pick out the numbers and let the app work it out.",
        "options": [{"id": "calculate", "label": "calculate exactly"}, {"id": "try_anyway", "label": "try anyway"}],
        "never_label": "don't warn me about this again", "note": "n", "op": "sum", "chosen": None}
BLURRY = {"kind": "blurry", "text": "This photo looks blurry, so some words may be guessed.",
          "options": [{"id": "choose_another", "label": "choose another"},
                      {"id": "send_anyway", "label": "send anyway"}],
          "never_label": "don't warn me about this again", "note": None, "op": None, "chosen": None}
THREAD = {"id": 11, "title": "About lease.pdf", "archived": False, "created_at": "2026-10-21T08:00:00Z",
          "updated_at": "2026-10-21T08:00:00Z", "message_count": 6, "last_snippet": "x"}


def _row(i, role, content, doc=None):
    return {"id": i, "role": role, "content": content, "images": [], "documents": None, "doc": doc,
            "model": None, "error": None, "created_at": "2026-10-21T08:00:00Z",
            "stats": {"model_role": "heads_up"} if doc else None}


MESSAGES = [
    _row(1, "user", "What is the total of the rent and the deposit?"),
    _row(2, "assistant", CARD["text"], {"mode": "heads_up", "found": True, "sources": [], "notes": [],
                                        "heads_up": {**CARD, "chosen": "try_anyway"}}),
    _row(3, "assistant", "Together $2,700 [p. 1].", {"mode": "question", "found": True, "sources": [],
                                                    "notes": [{"code": "heads_up_calc", "text": "Domovoi's arithmetic isn't checked."}]}),
    _row(4, "user", "What does this letter say?"),
    _row(5, "assistant", BLURRY["text"], {"mode": "heads_up", "found": True, "sources": [], "notes": [],
                                          "heads_up": BLURRY}),
]
API = {
    "GET /api/chat/threads": {"threads": [THREAD]},
    "GET /api/chat/models": {"installed": [], "default_model": "m", "vision_model": "v", "vision_installed": True},
    "GET /api/chat/threads/11/messages": {"messages": MESSAGES},
    "POST /api/chat/threads/11/heads-up": {"chosen": "choose_another", "message_id": 5},
}

CHAT_SCRIPT = r"""
h.render(); await h.settle(); h.rerender();
const has = (node, s) => node != null && (
  node === s
  || (Array.isArray(node) ? node.some((n) => has(n, s))
      : (typeof node === 'object' && node.props ? has(node.props.children, s) : false)));
await h.click((el) => el.type === 'div' && typeof el.props.onClick === 'function'
                      && has(el.props.children, 'About lease.pdf'));
const before = h.text();
const buttons = h.findAll((el) => el.type === 'button').map((el) => h.plain(el).text || '');
const mdTexts = h.findAll((el) => el.props && el.props.className === 'chat-md').length;
await h.click((el) => el.type === 'button' && el.props.className === 'chat-headsup-link');
const afterNever = h.text();
const stored = h.global('__store')['domovoi.chat.headsUp'] || null;
await h.click((el) => el.type === 'button' && (h.plain(el).text || '') === 'choose another');
await h.settle();
const afterChoose = h.text();
await h.type((el) => el.type === 'textarea', 'And the pet fee?');
await h.click((el) => el.type === 'button' && String(el.props.className || '').includes('chat-send'));
await h.settle();
return { before, buttons, mdTexts, afterNever, stored, afterChoose, calls: h.calls, sent: h.global('__sent') };
"""

PANEL_SCRIPT = r"""
h.global('__store')['domovoi.chat.headsUp'] = JSON.stringify({ off: false, skip: ['rewrite'] });
h.render(); await h.settle(); h.rerender();
const before = h.text();
await h.click((el) => el.type === 'button' && (h.plain(el).text || '') === 'warn again');
const afterAgain = h.text();
await h.change((el) => el.type === 'input' && el.props.type === 'checkbox', false);
const afterOff = h.text();
return { before, afterAgain, afterOff, stored: h.global('__store')['domovoi.chat.headsUp'],
         registered: !!((h.global('window') || {}).DomovoiSettingsSections || {}).chat };
"""


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    scen = {
        "chat": {"files": FILES, "component": "ChatPage", "setup": _setup(), "api": API, "script": CHAT_SCRIPT},
        "panel": {"files": FILES, "component": "ChatDocs.HeadsUpPanel", "setup": _setup(), "api": {},
                  "script": PANEL_SCRIPT},
    }
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(scen)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for k, v in out.items():
        assert "__harness_error" not in v, (k, v.get("__harness_error"))
    return out


def test_a_card_shows_its_sentence_and_its_options_once(driven):
    c = driven["chat"]
    t = " ".join(c["before"])
    assert "heads-up" in t and "This photo looks blurry, so some words may be guessed." in t
    assert "choose another" in c["buttons"] and "send anyway" in c["buttons"]
    assert "don't warn me about this again" in c["buttons"]
    # The card rows draw no markdown copy of their text: only the answer row does.
    assert c["mdTexts"] == 1


def test_a_picked_card_shows_the_pick_and_no_buttons(driven):
    c = driven["chat"]
    assert "calculate exactly" not in c["buttons"]
    assert any(x == "try anyway" for x in c["before"])
    assert "Domovoi's arithmetic isn't checked." in " ".join(c["before"])


def test_dont_warn_me_is_kept_in_this_browser_and_sent_with_the_next_message(driven):
    c = driven["chat"]
    assert json.loads(c["stored"]) == {"off": False, "skip": ["blurry"]}
    assert "no more heads-ups about blurry photos in this browser" in " ".join(c["afterNever"])
    sent = [s for s in c["sent"] if s["path"].endswith("/messages")]
    assert sent and sent[-1]["body"]["heads_up"] == {"off": False, "skip": ["blurry"]}


def test_choose_another_records_the_pick_and_answers_nothing(driven):
    c = driven["chat"]
    picks = [x for x in c["calls"] if x[0] == "POST" and x[1].endswith("/heads-up")] if c["calls"] and isinstance(c["calls"][0], list) else \
        [x for x in c["calls"] if "heads-up" in json.dumps(x)]
    assert picks, c["calls"]
    assert "message_id" in json.dumps(picks[0]) and "choose_another" in json.dumps(picks[0])
    assert not [s for s in c["sent"] if s["path"].endswith("/heads-up")]


def test_the_settings_panel_switches_the_heads_up_off_here(driven):
    p = driven["panel"]
    assert p["registered"] is True
    t = " ".join(p["before"])
    assert "heads-up before shaky results" in t and "rewriting a whole document" in t and "warn again" in t
    assert "rewriting a whole document" not in " ".join(p["afterAgain"])
    assert "exact sums and scanned pages still get a one-line note" in " ".join(p["afterOff"])
    assert json.loads(p["stored"]) == {"off": True, "skip": []}
