"""The web chat page on a phone (owner report 2026-10-13).

With no chat open, a phone showed the 250 px chat list beside the
conversation, which left the conversation a 77 px strip at 375 px, and the
page was 100dvh - 52 - 48 tall inside a .main that also has the phone's
tab strip under it: the composer's send button sat under the strip.

Now (chat.jsx, styles.css .chat-page):

* below a 666 px page width the list and the conversation take turns: the
  page opens on the conversation ("ask anything" and the composer, with
  the list one press away), opening a chat puts the list away, and the
  composer is one row (paperclip, text, send);
* the page is exactly the height .main shows: the topbar, the tab strip
  (phone), the docked player and the guard banner come off, and .main's
  bottom padding comes down so a short thread leaves nothing to scroll;
* the keyboard: the viewport asks for interactive-widget=resizes-content,
  and a browser that overlays the keyboard gets --kb-h off --app-h.

The page is driven (jsx_interact_harness.js) at a phone's and a desktop's
window width; the CSS and the viewport are read as text. No DB.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
DATA_JS = STATIC / "data.js"
FILES = ["web/static/components.jsx", "web/static/chat_markdown.js",
         "web/static/chat_details.js", "web/static/chat_documents.jsx", "web/static/chat.jsx"]


def _retry_helper() -> str:
    src = DATA_JS.read_text(encoding="utf-8")
    m = re.search(r"^const useRetryAfterCredential = .*?^};$", src, re.M | re.S)
    assert m, "useRetryAfterCredential is gone from web/static/data.js"
    return m.group(0)


def _setup(width: int) -> str:
    return (f"window.innerWidth = {width};\n"
            "globalThis.API_BASE = '';\n"
            "globalThis.withDeviceToken = (u) => u;\n"
            "globalThis.liveRelTime = () => 'now';\n" + _retry_helper())


THREAD = {"id": 11, "title": "shopping for Mira", "archived": False,
          "created_at": "2026-09-26T08:00:00Z", "updated_at": "2026-09-26T08:00:00Z",
          "message_count": 1, "last_snippet": "oats"}
MESSAGE = {"id": 21, "role": "user", "content": "what goes with oats?", "images": [],
           "model": None, "error": None, "created_at": "2026-09-26T08:00:00Z"}
API = {
    "GET /api/chat/threads": {"threads": [THREAD]},
    "GET /api/chat/models": {"installed": [], "default_model": "m", "vision_model": "v",
                             "vision_installed": True},
    "GET /api/chat/threads/11/messages": {"messages": [MESSAGE]},
}

SCRIPT = r"""
const state = () => {
  const page = h.find((el) => String(el.props.className || '').startsWith('chat-page'));
  const ta = h.find({ type: 'textarea' });
  const row = h.find((el) => el.props.className === 'chat-composer-row');
  return {
    pageClass: page && page.props.className,
    rail: !!h.find({ type: 'button', text: 'new chat' }),
    pane: !!ta,
    rows: ta ? ta.props.rows : null,
    // the composer on one row: the text box sits inside the row with send
    oneRow: !!(ta && row && h.ancestors(ta).includes(row)),
    yourChats: !!h.find({ type: 'button', text: 'your chats (1)' }),
    empty: !!h.find({ text: 'ask anything' }),
  };
};
h.render(); await h.settle(); h.rerender();
const open = state();
const listBtn = h.find({ type: 'button', text: 'your chats' });
if (listBtn) await h.click((el) => el === listBtn);
const list = state();
const has = (node, s) => node != null && (
  node === s
  || (Array.isArray(node) ? node.some((n) => has(n, s))
      : (typeof node === 'object' && node.props ? has(node.props.children, s) : false)));
await h.click((el) => el.type === 'div' && typeof el.props.onClick === 'function'
                      && has(el.props.children, 'shopping for Mira'));
const chat = state();
return { open, list, chat, texts: h.text() };
"""

SCENARIOS = {
    "phone": {"files": FILES, "component": "ChatPage", "setup": _setup(375), "api": API,
              "script": SCRIPT},
    "fold_inner": {"files": FILES, "component": "ChatPage", "setup": _setup(884), "api": API,
                   "script": SCRIPT},
    "desktop": {"files": FILES, "component": "ChatPage", "setup": _setup(1280), "api": API,
                "script": SCRIPT},
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


@pytest.mark.parametrize("screen", ["phone", "fold_inner"])
def test_a_narrow_page_opens_on_the_conversation_with_the_list_a_press_away(driven, screen) -> None:
    o = driven[screen]["open"]
    assert o["pageClass"] == "chat-page narrow"
    assert o["pane"] and not o["rail"], "the list must not squeeze the conversation"
    assert o["empty"] and o["yourChats"]
    assert o["oneRow"] and o["rows"] == 1


@pytest.mark.parametrize("screen", ["phone", "fold_inner"])
def test_narrow_the_list_and_the_conversation_take_turns(driven, screen) -> None:
    lst = driven[screen]["list"]
    assert lst["rail"] and not lst["pane"]
    chat = driven[screen]["chat"]
    assert chat["pane"] and not chat["rail"]
    assert "what goes with oats?" in driven[screen]["texts"]


def test_a_desktop_keeps_the_list_beside_the_conversation(driven) -> None:
    o = driven["desktop"]["open"]
    assert o["pageClass"] == "chat-page"
    assert o["rail"] and o["pane"] and not o["yourChats"]
    assert o["rows"] == 2 and not o["oneRow"]
    chat = driven["desktop"]["chat"]
    assert chat["rail"] and chat["pane"]


def _blocks(css: str, media: str) -> list[str]:
    """The body of every `media { ... }` block, braces matched."""
    out = []
    for m in re.finditer(re.escape(media) + r"\s*\{", css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append(css[m.end():i - 1])
    return out


def _rule(css: str, selector: str, media: str | None = None) -> str:
    """The declarations of `selector`, at the top level or in a `media` block."""
    if media:
        bodies = _blocks(css, media)
    else:
        bodies = [re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)]
    for body in bodies:
        m = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{(.*?)\}", body, re.S)
        if m:
            return re.sub(r"\s+", " ", m.group(1))
    raise AssertionError(f"{selector} not in {media or 'the top level'}")


def test_the_page_is_exactly_what_main_shows() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    desk = _rule(css, ".chat-page")
    assert ("height: calc(var(--app-h) - var(--topbar-h) - 48px - var(--player-h, 0px)"
            " - var(--guard-banner-h, 0px));") in desk
    assert "padding-bottom: calc(24px + var(--player-h, 0px));" in _rule(css, ".main:has(> .chat-page)")
    phone = _rule(css, ".chat-page", "@media (max-width: 760px)")
    assert ("height: calc(var(--app-h) - var(--topbar-h) - var(--phone-nav-h) - 32px"
            " - var(--player-h, 0px) - var(--guard-banner-h, 0px));") in phone
    assert "padding-bottom: calc(16px + var(--player-h, 0px));" in _rule(
        css, ".main:has(> .chat-page)", "@media (max-width: 760px)")
    # The thread keeps a drag to itself (no pull-to-refresh mid-reply).
    assert "overscroll-behavior: contain;" in _rule(css, ".chat-thread")


def test_the_keyboard_comes_off_the_shell() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert ":root { --app-h: calc(100vh - var(--kb-h, 0px)); }" in css
    assert "@supports (height: 100dvh) { :root { --app-h: calc(100dvh - var(--kb-h, 0px)); } }" in css
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<meta name="viewport" content="[^"]*interactive-widget=resizes-content', html)
    assert "root.style.setProperty('--kb-h'" in html
