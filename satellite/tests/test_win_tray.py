"""The Windows tray (satellite/win_tray.py) and what it shares with the
Linux one (satellite/tray.py).

The model parts run on any OS. On Windows the real notification-area icon
is created, driven through its states, its menu built and read back, and
removed again before the test ends; balloons and message boxes are
replaced by recorders, so nothing pops up on anyone's screen.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

from satellite import tray

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="the Windows shell")

if WINDOWS:
    from satellite import win_tray

FULL_KEYS = [f"wake.setting_{i:02d}" for i in range(30)]


def status(**kw):
    st = {"connection": "connected", "mic": "listening", "output": "idle",
          "room": {"id": "den", "name": "Den"}, "server": {"host": "192.168.1.20"},
          "dashboard": "http://192.168.1.20:6369", "volume": 40, "pending": []}
    st.update(kw)
    return st


class FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, verb, **kw):
        self.calls.append((verb, kw))
        if verb == "code":
            return {"ok": True, "waiting": True, "code": "482913"}
        return {"ok": True, "verb": verb}


# ─── shared model ─────────────────────────────────────────────────────────

def test_stop_becomes_start_only_where_the_tray_can_start_it():
    up = [i.label for i in tray.menu(status(), can_start=True)]
    assert "Stop the room…" in up and "Start the room" not in up
    down = tray.menu(None, can_start=True)
    assert [i.action for i in down if i.label == "Start the room"] == ["start"]
    linux = [i.label for i in tray.menu(None)]
    assert "Stop the room…" in linux and "Start the room" not in linux


def test_stop_and_start_use_what_the_platform_gives():
    got = []
    actions = tray.TrayActions(FakeClient(), stopper=lambda: got.append("stop") or tray.Note("stop", "s", "b"),
                               starter=lambda: got.append("start") or tray.Note("start", "s", "b"),
                               run=lambda *a, **k: got.append("systemctl"))
    assert actions.do("stop").key == "stop" and actions.do("start").key == "start"
    assert got == ["stop", "start"], "no systemctl on Windows"


# ─── the Win32 tray ───────────────────────────────────────────────────────

def test_argb_becomes_bgra():
    if not WINDOWS:
        pytest.skip("win_tray imports the Windows shell")
    assert win_tray.argb_to_bgra(bytes([1, 2, 3, 4, 5, 6, 7, 8])) == bytes([4, 3, 2, 1, 8, 7, 6, 5])


@pytest.fixture
def icon(tmp_path):
    actions = tray.TrayActions(FakeClient(), opener=lambda target: True)
    t = win_tray.WinTray(actions, root=tmp_path)
    t.balloons = []
    t.asked = []
    t.show_balloon = t.balloons.append
    t.ask = lambda title, text: t.asked.append((title, text)) or False
    t.create()
    try:
        yield t
    finally:
        t.remove()
        assert t.hwnd is None, "the test's tray icon is gone again"


def _labels(t) -> list[str]:
    hmenu, _ = t.build_menu()
    try:
        out = []
        for i in range(t.user32.GetMenuItemCount(hmenu)):
            buf = ctypes.create_unicode_buffer(256)
            t.user32.GetMenuStringW(hmenu, i, buf, 256, 0x400)
            out.append(buf.value)
        return out
    finally:
        t.user32.DestroyMenu(hmenu)


@windows_only
def test_the_icon_follows_the_mic_and_shows_the_code_never_logging_it(icon, caplog):
    icon.deliver(status(connection="awaiting_approval", mic="off"))
    icon.apply_status()
    assert icon.state == "awaiting"
    assert "482913" in icon.tip()
    assert icon.balloons and "482913" in icon.balloons[-1].summary
    assert _labels(icon)[0] == "Den · approval code 482913"
    icon.deliver(status(mic="capturing"))
    icon.apply_status()
    assert icon.state == "capturing" and "Microphone live" in icon.tip()
    assert icon.balloons[-1].summary.startswith("Approved")
    icon.deliver(status(mic="muted"))
    icon.apply_status()
    assert icon.state == "muted"
    assert "Unmute listening" in _labels(icon)
    assert "482913" not in caplog.text


@windows_only
def test_not_running_is_grey_with_the_supervisor_s_reason_and_start_offered(icon, tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "supervisor-status.json").write_text(
        '{"state": "stopped", "reason": "no pinned server identity: enrol it again from the dashboard"}')
    icon.deliver(None)
    icon.apply_status()
    assert icon.state == "offline" and "no pinned server identity" in icon.tip()
    assert "Start the room" in _labels(icon)


@windows_only
def test_a_refusal_to_start_is_a_toast_once_with_the_engine_s_words(icon, tmp_path):
    import json

    (tmp_path / "logs").mkdir()
    doc = {"state": "stopped", "at": 1000.0, "exit_code": 78, "refused": True,
           "reason": "not starting: [music] alsa_device 'x & y' is not a Windows audio device: use "
                     "\"default\", a device number, or a name from --list-devices (in C:\\S\\config.toml)"}
    path = tmp_path / "logs" / "supervisor-status.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    for _ in range(3):                          # the watcher delivers None every few seconds
        icon.deliver(None)
        icon.apply_status()
    refused = [b for b in icon.balloons if b.key == "refused"]
    assert len(refused) == 1, "once per refusal, not every poll"
    note = refused[0]
    assert note.summary == "Domovoi room didn't start" and len(note.summary) <= win_tray.INFO_TITLE_MAX
    assert "alsa_device 'x & y' is not a Windows audio device" in note.body
    assert note.body.endswith("Start the room in this menu.") and len(note.body) <= win_tray.INFO_MAX
    assert "Not running: not starting" in icon.tip()
    # Clicking it opens the menu, where Start the room is.
    shown = []
    icon.show_menu = lambda: shown.append(True)
    icon._balloon_clicked()
    assert shown and "Start the room" in _labels(icon)
    # A new refusal (a later start) is said again; a plain stop is not.
    path.write_text(json.dumps({**doc, "at": 2000.0}), encoding="utf-8")
    icon.deliver(None)
    icon.apply_status()
    assert len([b for b in icon.balloons if b.key == "refused"]) == 2
    path.write_text(json.dumps({"state": "stopped", "at": 3000.0, "exit_code": 0, "reason": "stopped"}),
                    encoding="utf-8")
    icon.deliver(None)
    icon.apply_status()
    assert len([b for b in icon.balloons if b.key == "refused"]) == 2


@windows_only
def test_a_long_reason_is_cut_with_an_ellipsis_in_the_toast_only():
    note = win_tray.refusal_note("x" * 400)
    assert len(note.body) <= win_tray.INFO_MAX and "\u2026 Fix that" in note.body
    assert win_tray.refusal_note("short reason").body.startswith("short reason. Fix that")


@windows_only
def test_every_state_has_an_icon(icon):
    for state in tray.ICON_STATES:
        assert icon.icon(state)


@windows_only
def test_allow_shows_every_key_in_full_and_asks_first(icon):
    summary = f"{len(FULL_KEYS)} setting(s) from the dashboard"
    details = [f"{k} = {i}" for i, k in enumerate(FULL_KEYS)]
    icon.deliver(status(pending=[{"id": "abc", "kind": "config", "summary": summary, "details": details}]))
    icon.apply_status()
    title, text = icon.confirm_text("approve:abc")
    for line in details:
        assert line in text
    assert summary in text
    toast = icon.balloons[-1]
    assert toast.key == "pending:abc" and "Allow" in toast.body and FULL_KEYS[0] not in toast.body, \
        "the toast never shows a cut-down list"
    icon._do("approve:abc")
    assert icon.asked and icon.actions.client.calls == [], "declined at the question: nothing approved"
    icon.ask = lambda title, text: True
    icon._do("approve:abc")
    assert ("approve", {"action": "abc"}) in icon.actions.client.calls


@windows_only
def test_stop_asks_first(icon, monkeypatch):
    stopped = []
    icon.actions.stopper = lambda: stopped.append(1) or tray.Note("stop", "Room stopped", "")
    icon._do("stop_ask")
    assert stopped == [] and icon.asked
    icon.ask = lambda title, text: True
    icon._do("stop_ask")
    assert stopped == [1]


@windows_only
def test_stop_sets_the_user_s_stop_event(monkeypatch):
    import uuid

    from satellite import win32

    name = f"Local\\domovoi-satellite-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(win32, "object_name", lambda kind, sid=None: name)
    watcher = win32.NamedEvent(name)
    try:
        note = win_tray.stop_satellite()
        assert watcher.wait(1) and note.summary == "Room stopped"
    finally:
        watcher.close()


@windows_only
def test_start_runs_the_supervisor_without_a_second_tray(monkeypatch, tmp_path):
    seen = {}

    class P:
        def __init__(self, argv, **kw):
            seen["argv"], seen["kw"] = argv, kw

    monkeypatch.setattr(win_tray.subprocess, "Popen", P)
    note = win_tray.start_satellite(tmp_path)
    assert seen["argv"] == [str(tmp_path / "python" / "pythonw.exe"), "-m", "satellite.win_service", "--no-tray"]
    assert note.key == "start"


@windows_only
def test_the_tray_is_one_per_user(monkeypatch):
    import uuid

    from satellite import win32

    name = f"Local\\domovoi-satellite-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(win32, "object_name", lambda kind, sid=None: name)
    first = win32.SingleInstance(name)
    try:
        assert win_tray.main([]) == 0, "a second tray leaves at once"
    finally:
        first.close()


@windows_only
def test_the_tray_opens_only_a_web_address_or_a_folder(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(win_tray.os, "startfile", opened.append)
    assert win_tray._open("http://192.168.1.20:6369") is True
    assert win_tray._open(str(tmp_path)) is True
    for bad in (r"C:\Windows\System32\calc.exe", "file:///C:/x", "", "ms-settings:privacy"):
        assert win_tray._open(bad) is False
    assert opened == ["http://192.168.1.20:6369", str(tmp_path)]


@windows_only
def test_a_settings_refusal_toast_shows_the_problem_not_the_path():
    """The engine's line names the whole config path first; the toast has
    255 characters, so it keeps the file's name and the problem."""
    folder = "C:\\Users\\Jos\u00e9\\AppData\\Local\\Domovoi\\Satellite\\.domovoi\\" + "x" * 60
    reason = (f"not starting: its settings in {folder}\\config.toml don't load: [music] alsa_device "
              "'x & calc.exe' is not a Windows audio device: use \"default\", a device number, or a name "
              "from --list-devices")
    body = win_tray.refusal_note(reason).body
    assert body.startswith("config.toml doesn't load: [music] alsa_device 'x & calc.exe' is not a Windows audio device")
    assert folder not in body and len(body) <= win_tray.INFO_MAX
    assert win_tray.refusal_note("not starting: another process holds the pipe").body.startswith(
        "another process holds the pipe.")
