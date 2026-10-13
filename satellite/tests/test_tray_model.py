"""The tray's model (satellite/tray.py): status → icon, tooltip, menu and
pop-ups; the icons' pixels; what each action asks of the engine; and the
Tk status window (where a display exists). No D-Bus here: that half is
satellite/tests/test_tray_dbus.py, on a session bus in a Linux container.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

from satellite import tray

ROOT = Path(__file__).resolve().parents[2]
CONTROL = json.loads((ROOT / "docs" / "uml" / "satellite-control-fixture.json").read_text(encoding="utf-8"))


def status(**over):
    st = copy.deepcopy(CONTROL["engine"]["status"])
    st.update({"connection": "connected", "mic": "listening", "output": "idle", "pending": []})
    st.update(over)
    return st


# ─── icon states ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("over,want", [
    ({}, "idle"),
    ({"mic": "capturing"}, "capturing"),
    ({"mic": "capturing", "output": "speaking"}, "capturing"),
    ({"mic": "muted"}, "muted"),
    ({"output": "speaking"}, "speaking"),
    ({"output": "music"}, "speaking"),
    ({"connection": "awaiting_approval", "mic": "off"}, "awaiting"),
    ({"connection": "offline", "reason": "no route"}, "offline"),
    ({"connection": "unpinned", "reason": "a different server"}, "offline"),
    ({"connection": "refused"}, "offline"),
])
def test_each_status_has_its_icon(over, want):
    assert tray.icon_state(status(**over)) == want


def test_no_engine_is_grey():
    assert tray.icon_state(None) == "offline"
    title, text = tray.tooltip(None)
    assert "isn't running" in text


def test_the_tooltip_says_why_it_is_grey():
    _t, text = tray.tooltip(status(connection="unpinned", reason="this is a different server"))
    assert "identity check" in text and "different server" in text


def test_the_tooltip_carries_the_code_while_waiting():
    title, text = tray.tooltip(status(connection="awaiting_approval", mic="off"), "482913")
    assert title == "Domovoi · Kid's Room"
    assert "482913" in text and "Settings → Satellites" in text


def test_the_live_mic_is_said_plainly():
    assert "Microphone live" in tray.tooltip(status(mic="capturing"))[1]


# ─── the menu ─────────────────────────────────────────────────────────────

def labels(items):
    return [i.label for i in items if not i.separator]


def test_the_menu_has_every_item_of_the_design():
    items = tray.menu(status())
    got = labels(items)
    assert got[0] == "Kid's Room · connected to 192.168.0.117"
    for want in ("Mute listening", "Volume up", "Volume down", "Which room is this? (plays a chime)",
                 "Open dashboard", "Show logs folder", "About",
                 "Quit tray (the satellite keeps running)", "Stop satellite…"):
        assert want in got
    assert items[0].enabled is False
    assert [i.id for i in items] == [i.id for i in tray.menu(status(mic="muted"))], "ids are stable"


def test_mute_becomes_unmute():
    item = next(i for i in tray.menu(status(mic="muted")) if i.action in ("mute", "unmute"))
    assert (item.label, item.action) == ("Unmute listening", "unmute")


def test_pending_actions_get_allow_and_not_now():
    st = status(pending=[{"id": "a1", "kind": "update", "summary": "new satellite code", "received_at": 1}])
    actions = [i.action for i in tray.menu(st)]
    assert "approve:a1" in actions and "decline:a1" in actions


def test_with_no_engine_only_quit_and_about_work():
    enabled = {i.action for i in tray.menu(None) if i.enabled and i.action}
    assert enabled == {"about", "quit"}


# ─── pop-ups ──────────────────────────────────────────────────────────────

def test_the_code_pops_up_once_then_approved_replaces_it():
    planner = tray.NotePlanner()
    waiting = status(connection="awaiting_approval", mic="off")
    first = planner.plan(waiting, "482913")
    assert [n.key for n in first] == ["approval"]
    assert "482913" in first[0].summary and first[0].persistent
    assert planner.plan(waiting, "482913") == [], "not again for the same code"
    done = planner.plan(status(), None)
    assert [n.key for n in done] == ["approval"]
    assert done[0].summary == "Approved: this device is now the Kid's Room"
    assert planner.plan(status(), None) == []


def test_each_waiting_action_pops_up_once_with_allow_and_not_now():
    planner = tray.NotePlanner()
    st = status(pending=[{"id": "a1", "kind": "restart", "summary": "the core asked for a restart", "received_at": 1}])
    notes = planner.plan(st, None)
    assert [n.key for n in notes] == ["pending:a1"]
    assert notes[0].actions == [("approve:a1", "Allow"), ("decline:a1", "Not now")]
    assert planner.plan(st, None) == []


def test_about_names_the_pinned_fingerprint():
    note = tray.about_note(status())
    assert CONTROL["engine"]["status"]["server"]["fingerprint"] in note.body


# ─── icons ────────────────────────────────────────────────────────────────

def pixels(state, size=32):
    raw = tray.render_icon(state, size)
    assert len(raw) == size * size * 4
    return [tuple(raw[i:i + 4]) for i in range(0, len(raw), 4)]


def opaque(px):
    return [p for p in px if p[0] > 200]


def test_every_state_has_every_size():
    for state in tray.ICON_STATES:
        sizes = [(w, h, len(data)) for w, h, data in tray.icon_pixmaps(state)]
        assert sizes == [(s, s, s * s * 4) for s in tray.ICON_SIZES]


def test_capturing_is_a_filled_red_mic_and_idle_an_outline():
    red = [p for p in opaque(pixels("capturing")) if p[1] > 200 and p[2] < 100]
    green = [p for p in opaque(pixels("idle")) if p[2] > 150 and p[1] < 120]
    assert len(red) > len(green) > 20, "filled has more ink than outline"


def test_muted_has_a_red_strike_on_a_grey_mic():
    px = opaque(pixels("muted"))
    assert any(p[1] > 200 and p[2] < 100 for p in px)
    assert any(abs(p[1] - p[2]) < 10 and 120 < p[1] < 180 for p in px)


def test_speaking_has_a_blue_badge_and_awaiting_is_amber():
    assert any(p[3] > 200 and p[1] < 100 for p in opaque(pixels("speaking")))
    assert any(p[1] > 240 and 150 < p[2] < 200 and p[3] < 50 for p in opaque(pixels("awaiting")))


# ─── actions ──────────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self, st=None):
        self.st = st
        self.calls = []

    def call(self, verb, **args):
        self.calls.append((verb, args))
        if verb == "status":
            if self.st is None:
                raise ConnectionError("no engine")
            return {"ok": True, **self.st}
        if verb == "code":
            return {"ok": True, "waiting": True, "code": "482913"}
        if verb == "logs-path":
            return {"ok": True, "kind": "journal", "path": "/home/x/.domovoi",
                    "hint": "journalctl --user -u domovoi-satellite -e"}
        return {"ok": True}


def test_each_action_asks_the_engine_for_the_right_thing():
    client = FakeClient(status())
    opened = []
    ran = []
    acts = tray.TrayActions(client, opener=lambda t: opened.append(t) or True,
                            run=lambda argv, **kw: ran.append(argv))
    acts.status()
    for action in ("mute", "unmute", "identify", "volume_up", "volume_down",
                   "approve:a1", "decline:a1", "dashboard", "logs"):
        acts.do(action)
    assert ("volume", {"delta": 10}) in client.calls and ("volume", {"delta": -10}) in client.calls
    assert ("approve", {"action": "a1"}) in client.calls and ("decline", {"action": "a1"}) in client.calls
    assert opened == ["http://192.168.0.117:6369", "/home/x/.domovoi"]
    assert acts.do("stop_ask").key == "stop" and ran == []
    acts.do("stop")
    assert ran == [["systemctl", "--user", "stop", "domovoi-satellite.service"]]


def test_an_engine_that_is_gone_is_a_pop_up_not_a_crash():
    class Gone(FakeClient):
        def call(self, verb, **args):
            raise ConnectionError("no engine")
    note = tray.TrayActions(Gone()).do("mute")
    assert note is not None and "isn't answering" in note.body


# ─── the window (needs a display: the Linux container runs it under Xvfb) ─

needs_display = pytest.mark.skipif(
    sys.platform == "win32" or not os.environ.get("DISPLAY"),
    reason="the status window needs an X display (xvfb-run in the container)",
)


@needs_display
def test_the_window_shows_the_state_and_its_buttons_drive_the_engine():
    import tkinter as tk

    client = FakeClient(status(mic="capturing"))
    root = tk.Tk()
    try:
        win = tray.StatusWindow(root, tray.TrayActions(client, opener=lambda t: True))
        win.refresh()
        assert win.mic.cget("text") == "● MICROPHONE LIVE"
        assert win.mute_btn.cget("text") == "Mute"
        win.mute_btn.invoke()
        assert ("mute", {}) in client.calls
        client.st = status(connection="awaiting_approval", mic="off")
        win.refresh()
        assert win.code.cget("text") == "code 482913"
        client.st = status(pending=[{"id": "a1", "kind": "update", "summary": "s", "received_at": 1}])
        win.refresh()
        buttons = [w for f in win.pending.winfo_children() for w in f.winfo_children()
                   if isinstance(w, tk.Button)]
        assert [b.cget("text") for b in buttons] == ["Allow", "Not now"]
        buttons[0].invoke()
        assert ("approve", {"action": "a1"}) in client.calls
        client.st = None
        win.refresh()
        assert win.mic.cget("text") == "Offline" and win.mute_btn.cget("state") == "disabled"
    finally:
        root.destroy()
