"""The dashboard pages that read household speech keep working now that
those reads answer to a PAIRED DEVICE (owner decision, 2026-09-26).

The server half is :mod:`test_web_speech_reads`. This is the client half,
driven through ``jsx_interact_harness.js`` against the real page files:

* **People** — a person's conversations and memory tab are refused on a
  browser that is not paired yet. The tab says so (instead of "hasn't
  said anything yet"), and the moment a credential ARRIVES — the pair
  modal answered, or an admin signing in — the per-person reads run again
  and the rows render. A notify that changes no credential (the modal
  merely opening) re-reads nothing, so a read refused again cannot loop;
  a second token typed after a mistyped one is re-read.
* **Chat** — a thread's messages recover the same way, and a chat image
  is rendered through ``withDeviceToken``: an ``<img src>`` cannot send
  the header, so the household token rides in the query the read half of
  the device tier accepts. A refused thread list says so rather than "no
  chats yet".
* **Satellites drawer** — a refused conversations or notes tab says so
  rather than "satellite has been quiet" (the tab is a ``useApiList``,
  which re-reads on its own once a credential arrives).
* **Wake words** — a clip's ``<audio>`` URL carries the token the same
  way, on the selected server's base URL.

The retry helper under test is the REAL ``useRetryAfterCredential``,
lifted out of ``web/static/data.js`` into the scenario's setup (the page
files reference it as a global, exactly as they do in the browser). The
rest of data.js is not loaded: the harness scripts the fetch layer.

No DB — this never skips. Needs ``node``, like the JSX compile check.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERACT = Path(__file__).with_name("jsx_interact_harness.js")
DATA_JS = REPO_ROOT / "web" / "static" / "data.js"
COMPONENTS = "web/static/components.jsx"
SATELLITE_FILES = [COMPONENTS, "web/static/satellite_media.jsx", "web/static/satellites.jsx"]


def _helper_source() -> str:
    src = DATA_JS.read_text(encoding="utf-8")
    m = re.search(r"^const useRetryAfterCredential = .*?^};$", src, re.M | re.S)
    assert m, "useRetryAfterCredential is gone from web/static/data.js"
    return m.group(0)


# An Auth the scenario drives: not signed in, not paired, and two levers —
# __pair() stores a household token (credentialVersion moves) and
# __notify() is a modal opening or closing (nothing moves).
SETUP = r"""
let __version = 0;
let __paired = false;
const __listeners = new Set();
Auth = {
  status: { setup_complete: true, authenticated: false },
  isLoggedIn: () => false,
  isPaired: () => __paired,
  get credentialVersion() { return __version; },
  subscribe(fn) { __listeners.add(fn); return () => __listeners.delete(fn); },
  headers: () => ({}),
  modalOpen: false,
};
globalThis.__pair = () => { __paired = true; __version += 1; __listeners.forEach((f) => f()); };
globalThis.__notify = () => { __listeners.forEach((f) => f()); };
globalThis.API_BASE = '';
globalThis.withDeviceToken = (url) =>
  __paired ? `${url}${url.includes('?') ? '&' : '?'}device_token=phrase-of-eight` : url;
globalThis.__audio = [];
Audio = function (url) { __audio.push(url); return { play: () => Promise.resolve(), pause() {} }; };
globalThis.liveRelTime = () => 'now';
"""


def _refused() -> dict:
    """What apiGet rejects with when the device tier refuses a read: the
    detail names the header, so data.js marks it ``deviceTokenRequired``
    (and has already opened the pair modal)."""
    return {"__error": {
        "status": 401, "deviceTokenRequired": True, "loginPrompted": True,
        "message": '401 Unauthorized: {"detail":"X-Device-Token or admin session required"}',
    }}


HAS = r"""
  const has = (node, s) => node != null && (
    node === s
    || (Array.isArray(node) ? node.some((n) => has(n, s))
        : (typeof node === 'object' && node.props ? has(node.props.children, s) : false)));
  const texts = () => h.text().map(String);
  const gets = (p) => h.calls.filter((c) => c.method === 'GET' && c.path.startsWith(p)).length;
"""

PERSON = {"id": 7, "name": "Mira", "last_seen_at": None, "voice_profile_count": 1,
          "presence_tier": "high", "notes": None, "created_at": None}
TURN = {"id": 1, "session_id": "s-1", "at": "2026-09-26T08:00:00Z", "room_id": "kitchen",
        "user_text": "remind me about the dentist", "assistant_text": "okay",
        "matched_handler": "timer", "matched_path": "fast", "utterance_trigger": "wake_word"}
MEMORY = {"id": 3, "person_id": 7, "body": "allergic to peanuts", "topic": None,
          "source": "manual", "status": "active", "created_at": "2026-09-26T08:00:00Z"}

PEOPLE_SCRIPT = HAS + r"""
  h.render(); await h.settle(); h.rerender();
  await h.click((el) => el.type === 'button' && has(el.props.children, 'Mira'));
  await h.click({ type: 'button', text: 'Conversations' });
  const refused = { texts: texts(), reads: gets('/api/people/7/conversations') };

  // The pair modal opening (or being dismissed) changes no credential.
  h.global('__notify')(); await h.settle(); h.rerender();
  const afterNotify = gets('/api/people/7/conversations');

  // A mistyped token is still a credential arriving: one re-read, refused
  // again — and the modal that refusal re-opens re-reads nothing (no loop).
  h.global('__pair')(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const mistyped = gets('/api/people/7/conversations');
  h.global('__notify')(); await h.settle(); h.rerender();
  h.global('__notify')(); await h.settle(); h.rerender();
  const afterLoopCheck = gets('/api/people/7/conversations');

  // The right token: the reads run again and the rows render.
  h.api['GET /api/people/7/conversations'] = [%(turn)s];
  h.api['GET /api/people/7/memories'] = [%(memory)s];
  h.global('__pair')(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const paired = { texts: texts(), reads: gets('/api/people/7/conversations') };
  await h.click({ type: 'button', text: 'Memory' });
  return { refused, afterNotify, mistyped, afterLoopCheck, paired, memory: texts() };
""" % {"turn": json.dumps(TURN), "memory": json.dumps(MEMORY)}

THREAD = {"id": 11, "title": "shopping for Mira", "archived": False,
          "created_at": "2026-09-26T08:00:00Z", "updated_at": "2026-09-26T08:00:00Z",
          "message_count": 1, "last_snippet": "oats"}
MESSAGE = {"id": 21, "role": "user", "content": "what goes with oats?",
           "images": [{"token": "ab" * 16, "name": "oats.png"}], "model": None,
           "error": None, "created_at": "2026-09-26T08:00:00Z"}

CHAT_SCRIPT = HAS + r"""
  h.render(); await h.settle(); h.rerender();
  const row = (el) => el.type === 'div' && typeof el.props.onClick === 'function'
                      && has(el.props.children, 'shopping for Mira');
  await h.click(row);
  const refused = { texts: texts(), reads: gets('/api/chat/threads/11/messages') };
  h.global('__notify')(); await h.settle(); h.rerender();
  const afterNotify = gets('/api/chat/threads/11/messages');

  h.api['GET /api/chat/threads/11/messages'] = { messages: [%(message)s] };
  h.global('__pair')(); await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const imgs = h.findAll({ type: 'img' }).map((el) => el.props.src);
  const links = h.findAll((el) => el.type === 'a' && el.props.target === '_blank').map((el) => el.props.href);
  return { refused, afterNotify, texts: texts(),
           reads: gets('/api/chat/threads/11/messages'), imgs, links };
""" % {"message": json.dumps(MESSAGE)}

CLIPS = {"slug": "hey_domovoi", "count": 1, "selected_count": 1, "min_clips": 1, "clips": [{
    "name": "clip_001.wav", "verdict": "good", "issues": [], "selected": True,
    "raw_duration_ms": 900, "trimmed_duration_ms": 700, "has_trimmed": True,
    "metrics": {"peak_dbfs": -3, "rms_dbfs": -20, "noise_dbfs": -60, "snr_db": 40,
                "clipping_pct": 0, "speech_ratio": 0.8, "voiced_ms": 600,
                "leading_silence_ms": 100, "trailing_silence_ms": 100},
    "envelope": [0.1, 0.5, 0.2], "trim": {}, "score": None,
}]}

CLIP_SCRIPT = r"""
  h.global('__pair')();
  h.render(); await h.settle(); h.rerender();
  await h.click({ type: 'button', text: 'raw' });
  return { audio: h.global('__audio') };
"""


def _scenarios() -> dict:
    setup = SETUP + "\n" + _helper_source() + "\n"
    return {
        "people": {
            "files": [COMPONENTS, "web/static/people.jsx"],
            "component": "PeoplePage",
            "setup": setup,
            "api": {
                "GET /api/people": [PERSON],
                "GET /api/denylist": [],
                "GET /api/people/7/sessions": [],
                "GET /api/people/7/conversations": _refused(),
                "GET /api/people/7/memories": _refused(),
                "GET /api/people/7/favorites": _refused(),
                "GET /api/people/7/preferences": _refused(),
            },
            "script": PEOPLE_SCRIPT,
        },
        "chat": {
            "files": [COMPONENTS, "web/static/chat.jsx"],
            "component": "ChatPage",
            "setup": setup,
            "api": {
                "GET /api/chat/threads": {"threads": [THREAD]},
                "GET /api/chat/models": {"installed": [], "default_model": "m",
                                         "vision_model": "v"},
                "GET /api/chat/threads/11/messages": _refused(),
            },
            "script": CHAT_SCRIPT,
        },
        "chat_rail_refused": {
            "files": [COMPONENTS, "web/static/chat.jsx"],
            "component": "ChatPage",
            "setup": setup,
            "api": {
                "GET /api/chat/threads": _refused(),
                "GET /api/chat/models": {"installed": [], "default_model": "m",
                                         "vision_model": "v"},
            },
            "script": "h.render(); await h.settle(); h.rerender(); "
                      "return { texts: h.text().map(String) };",
        },
        **{
            f"room_{what}_refused": {
                "files": SATELLITE_FILES,
                "component": component,
                "props": {"room": "kitchen"},
                "setup": setup,
                "api": {path: _refused()},
                "script": "h.render(); await h.settle(); h.rerender(); "
                          "return { texts: h.text().map(String) };",
            }
            for what, component, path in (
                ("conversations", "RoomConversationsBody",
                 "GET /api/satellites/kitchen/conversations"),
                ("notes", "RoomNotesBody", "GET /api/satellites/kitchen/notes"),
            )
        },
        "wake_clip": {
            "files": [COMPONENTS, "web/static/settings.jsx"],
            "component": "WakeClipGrid",
            "props": {"wid": 4, "canScore": False},
            "fnProps": ["fire"],
            "setup": setup,
            "api": {"GET /api/wake-words/4/clips": CLIPS},
            "script": CLIP_SCRIPT,
        },
    }


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("speech-reads") / "scenarios.json"
    spec.write_text(json.dumps(_scenarios()), encoding="utf-8")
    proc = subprocess.run(
        [node, str(INTERACT), str(REPO_ROOT), f"@{spec}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def _shows(texts: list[str], s: str) -> bool:
    return any(s in t for t in texts)


# ─── People ───────────────────────────────────────────────────────────────


def test_a_refused_person_read_says_paired_devices_only(driven) -> None:
    o = driven["people"]["refused"]
    assert o["reads"] == 1
    assert _shows(o["texts"], "paired devices only")
    # Not the copy for an empty history: nothing here says Mira was silent.
    assert not _shows(o["texts"], "hasn't said anything")


def test_a_modal_opening_rereads_nothing(driven) -> None:
    assert driven["people"]["afterNotify"] == 1


def test_each_credential_gets_one_reread_and_a_refusal_does_not_loop(driven) -> None:
    """A mistyped token re-reads once and is refused again; the modal that
    refusal re-opens (and closes) re-reads nothing — the loop the helper
    exists to avoid."""
    o = driven["people"]
    assert o["mistyped"] == 2
    assert o["afterLoopCheck"] == 2


def test_pairing_rereads_the_person_and_renders_the_rows(driven) -> None:
    """The right token after the mistyped one is a new credential, so it
    gets its own re-read — and the rows come back."""
    o = driven["people"]["paired"]
    assert o["reads"] == 3
    assert _shows(o["texts"], "remind me about the dentist")
    assert not _shows(o["texts"], "paired devices only")
    assert _shows(driven["people"]["memory"], "allergic to peanuts")


# ─── Chat ─────────────────────────────────────────────────────────────────


def test_a_chat_thread_rereads_after_pairing(driven) -> None:
    o = driven["chat"]
    assert o["refused"]["reads"] == 1
    assert o["afterNotify"] == 1
    assert o["reads"] == 2
    assert _shows(o["texts"], "what goes with oats?")


def test_a_refused_chat_list_does_not_claim_there_are_no_chats(driven) -> None:
    texts = driven["chat_rail_refused"]["texts"]
    assert _shows(texts, "paired devices only")
    assert not _shows(texts, "no chats yet")


def test_a_chat_image_carries_the_household_token(driven) -> None:
    """An ``<img src>`` (and the new tab behind it) cannot send the
    header, so the URL goes through withDeviceToken."""
    o = driven["chat"]
    want = f"/api/chat/uploads/{'ab' * 16}?device_token=phrase-of-eight"
    assert o["imgs"] == [want]
    assert o["links"] == [want]


# ─── Satellites drawer ────────────────────────────────────────────────────


@pytest.mark.parametrize("what", ["conversations", "notes"])
def test_a_refused_room_tab_says_paired_devices_only(driven, what) -> None:
    """The drawer's tab is a useApiList, which re-reads by itself once a
    credential arrives; what it must not do meanwhile is say the room has
    been quiet."""
    texts = driven[f"room_{what}_refused"]["texts"]
    assert _shows(texts, "paired devices only")
    assert not _shows(texts, "been quiet")
    assert not _shows(texts, "no notes from this room")


# ─── Wake words ───────────────────────────────────────────────────────────


def test_a_wake_clip_plays_through_a_url_carrying_the_token(driven) -> None:
    assert driven["wake_clip"]["audio"] == [
        "/api/wake-words/4/clips/clip_001.wav/audio?variant=raw&device_token=phrase-of-eight"
    ]
