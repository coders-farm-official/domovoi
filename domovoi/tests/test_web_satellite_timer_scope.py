"""The satellite drawer's "Only reminders for this device" switch and the
Timers tab (web/static/satellites.jsx), driven outside a browser.

Owner decision 2026-09-30: every satellite announces every room's timers
and reminders; each satellite has a setting "Only reminders for this
device", default OFF, that limits it to the ones set on it. The
dashboard's switch is ``SatTimerScopeControl`` in the drawer's Overview,
right after the announce block. What these tests pin:

* the section label, the checkbox label and the help text, exactly;
* where it sits: after "announce to <room>", before "drop in";
* the write: ``PUT /api/satellites/<room>/timer-announcements`` with
  ``{"own_only": true|false}``, the exact toasts, the roster refresh, the
  box disabled while the request is out, and a failure reported through
  ``reportMutationFailure`` with the box left as it was;
* it works for an offline room (no admin check, no online check);
* the Timers tab: a reminder the server masked (no household credential)
  reads ``reminder (words hidden)``, and the "recently fired here" list;
  on a shared screen a reminder's words show in neither (``reminder``);
* the switch is a 44px target on a phone.

Driven by domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React); the page's calls are scripted.

No DB, never ``requires_db``; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/satellite_media.jsx", "web/static/satellites.jsx"]

LABEL = "Only reminders for this device"
HELP = ("Covers timers and reminders. Off: this satellite also announces the ones set in other "
        "rooms and says which room they came from. On: it announces only the ones set on this "
        "satellite. The room a timer or reminder was set in always announces it.")
TOAST_ON = "kitchen now announces only its own timers and reminders"
TOAST_OFF = "kitchen now announces timers and reminders from every room"
PUT_PATH = "/api/satellites/kitchen/timer-announcements"

# apiFetch / reportMutationFailure are data.js's; the harness scripts them.
SETUP = r"""
globalThis.__writes = [];
globalThis.__failures = [];
globalThis.__answer = 'ok';
apiFetch = (path, opts) => {
  __writes.push({ path, method: (opts && opts.method) || 'GET', body: (opts && opts.body) || null });
  if (__answer === 'hang') return new Promise(() => {});
  if (__answer === 'fail') return Promise.reject(Object.assign(new Error('503 Service Unavailable'), { status: 503 }));
  return Promise.resolve({ room_id: 'kitchen', own_only: JSON.parse(opts.body).own_only, since: null });
};
reportMutationFailure = (fire, verb, e) => { __failures.push(verb); fire(`${verb} failed`); };
"""

SNAP = r"""
  const box = () => h.find({ type: 'input' });
  const state = () => ({ checked: !!box().props.checked, disabled: !!box().props.disabled,
                         texts: h.text(), writes: h.global('__writes').slice(),
                         failures: h.global('__failures').slice(),
                         fired: h.fnCalls.map((c) => [c.name].concat(c.args)) });
"""


def room(**over) -> dict:
    r = {"room_id": "kitchen", "status": "offline", "last_connected_at": None, "wifi": None,
         "now_playing": None, "mpd_ports": {"control": 6650, "http": 8050}, "version": None,
         "full_duplex": False, "in_call_with": None, "voice": None, "volume": None,
         "pairing": {"paired": True}, "sat_type": "voice", "mic_enabled": True,
         "capture_commands": False, "timers_own_only": False}
    r.update(over)
    return r


def _control(s: dict, script: str, answer: str = "ok") -> dict:
    return {"files": FILES, "component": "SatTimerScopeControl",
            "props": {"s": s}, "fnProps": ["fire", "refresh"],
            "setup": SETUP + f"__answer = {json.dumps(answer)};",
            "script": SNAP + script}


def _now_iso(minus_sec: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=minus_sec)).isoformat()


_MASKED_ROW = {"id": 7, "expires_at": _now_iso(-600), "created_at": _now_iso(60), "label": None,
               "message": None, "room_id": "kitchen", "is_reminder": True, "masked": True}
_TIMER_ROW = {"id": 8, "expires_at": _now_iso(-300), "created_at": _now_iso(60), "label": "pasta",
              "message": None, "room_id": "kitchen", "is_reminder": False, "masked": False}
_OPEN_REMINDER_ROW = {**_MASKED_ROW, "id": 9, "label": "call mom", "message": "call mom", "masked": False}


def _fire(fid: int, **over) -> dict:
    f = {"id": fid, "timer_id": 100 + fid, "kind": "timer", "is_reminder": False, "label": "pasta",
         "message": None, "masked": False, "room_id": "kitchen", "created_at": _now_iso(700),
         "due_at": _now_iso(100), "fired_at": _now_iso(100), "settled_at": None, "acked_at": None,
         "acked_by": None, "heard_in": ["kitchen"], "summary": "heard in kitchen", "deliveries": []}
    f.update(over)
    return f


SCENARIOS = {
    "overview": {
        "files": FILES, "component": "OverviewBody",
        "props": {"s": room(), "sats": [room()]}, "fnProps": ["fire", "onClose", "refresh"],
        "setup": SETUP,
        "script": "h.render(); await h.settle(); h.rerender();"
                  " const box = h.findAll({ type: 'input' }).find((e) => e.props.type === 'checkbox');"
                  " return { texts: h.text(), box: box ? { checked: !!box.props.checked,"
                  " disabled: !!box.props.disabled } : null };",
    },
    "turn_on": _control(room(), r"""
  h.render();
  const before = state();
  await h.change({ type: 'input' }, true);
  return { before, after: state() };
"""),
    "turn_off": _control(room(timers_own_only=True), r"""
  h.render();
  const before = state();
  await h.change({ type: 'input' }, false);
  return { before, after: state() };
"""),
    # The write never answers: the handler is called, not awaited.
    "in_flight": _control(room(), r"""
  h.render();
  const press = () => { box().props.onChange({ target: { checked: true } }); };
  press(); await h.settle(); h.rerender();
  const first = state();
  press(); await h.settle(); h.rerender();
  return { first, second: state() };
""", answer="hang"),
    "fails": _control(room(), r"""
  h.render();
  await h.change({ type: 'input' }, true);
  return state();
""", answer="fail"),
    "timers_tab": {
        "files": FILES, "component": "RoomTimersBody",
        "props": {"room": "kitchen"}, "fnProps": ["fire"], "setup": SETUP,
        "api": {
            "GET /api/satellites/kitchen/timers": [_OPEN_REMINDER_ROW, _MASKED_ROW, _TIMER_ROW],
            "GET /api/timers/fires": {"server_now": _now_iso(), "fires": [
                _fire(3),
                _fire(2, kind="reminder", is_reminder=True, label=None, message=None, masked=True,
                      summary="not heard in any room (kitchen offline)", heard_in=[]),
            ]},
        },
        "script": "h.render(); await h.settle(); h.rerender();"
                  " const cells = h.findAll({ type: 'td' }).map((e) => e.text.trim()).filter(Boolean);"
                  " const fired = h.findAll((e) => String(e.props.className || '').includes('sat-timer-fired-row'))"
                  ".map((e) => e.text);"
                  " return { cells, fired, hooks: h.hookCalls };",
    },
    # A shared screen (the kitchen tablet) holding the household token: the
    # server sends the words, the screen keeps them to itself.
    "timers_tab_shared": {
        "files": FILES, "component": "RoomTimersBody",
        "props": {"room": "kitchen"}, "fnProps": ["fire"],
        "setup": SETUP + "DeviceIdentity = { sharedScreen: () => true };",
        "api": {
            "GET /api/satellites/kitchen/timers": [_OPEN_REMINDER_ROW, _TIMER_ROW],
            "GET /api/timers/fires": {"server_now": _now_iso(), "fires": [
                _fire(4, kind="reminder", is_reminder=True, label="call mom", message="call mom"),
                _fire(3),
            ]},
        },
        "script": "h.render(); await h.settle(); h.rerender();"
                  " const cells = h.findAll({ type: 'td' }).map((e) => e.text.trim()).filter(Boolean);"
                  " const fired = h.findAll((e) => String(e.props.className || '').includes('sat-timer-fired-row'))"
                  ".map((e) => e.text);"
                  " return { cells, fired, texts: h.text() };",
    },
    "timers_tab_empty": {
        "files": FILES, "component": "RoomTimersBody",
        "props": {"room": "kitchen"}, "fnProps": ["fire"], "setup": SETUP,
        "api": {
            "GET /api/satellites/kitchen/timers": [],
            "GET /api/timers/fires": {"server_now": _now_iso(), "fires": [_fire(3)]},
        },
        "script": "h.render(); await h.settle(); h.rerender();"
                  " return { texts: h.text(), fired: h.findAll((e) => String(e.props.className || '')"
                  ".includes('sat-timer-fired-row')).map((e) => e.text) };",
    },
    "timers_tab_no_history": {
        "files": FILES, "component": "RoomTimersBody",
        "props": {"room": "kitchen"}, "fnProps": ["fire"], "setup": SETUP,
        "api": {
            "GET /api/satellites/kitchen/timers": [_TIMER_ROW],
            "GET /api/timers/fires": {"__error": {"status": 503, "message": "503 V018"}},
        },
        "script": "h.render(); await h.settle(); h.rerender(); return { texts: h.text() };",
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("sat-timer-scope") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), "@" + str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_switch_label_and_help_text_are_exact(driven) -> None:
    texts = driven["turn_on"]["before"]["texts"]
    assert "timers & reminders" in texts
    assert LABEL in texts
    assert HELP in texts


def test_it_sits_after_announce_and_before_drop_in_and_works_offline(driven) -> None:
    out = driven["overview"]
    texts = out["texts"]
    announce = next(i for i, t in enumerate(texts) if t.startswith("announce to kitchen"))
    ours = texts.index("timers & reminders")
    drop_in = texts.index("drop in")
    assert announce < ours < drop_in
    # An offline room: the database row is all it needs.
    assert out["box"] == {"checked": False, "disabled": False}


def test_turning_it_on_puts_own_only_true_and_says_so(driven) -> None:
    out = driven["turn_on"]
    assert out["before"]["checked"] is False
    assert out["after"]["writes"] == [
        {"path": PUT_PATH, "method": "PUT", "body": json.dumps({"own_only": True}, separators=(",", ":"))}]
    assert ["fire", TOAST_ON] in out["after"]["fired"]
    assert ["refresh"] in out["after"]["fired"]
    assert out["after"]["checked"] is True and out["after"]["disabled"] is False


def test_turning_it_off_puts_own_only_false_and_says_so(driven) -> None:
    out = driven["turn_off"]
    assert out["before"]["checked"] is True
    assert out["after"]["writes"] == [
        {"path": PUT_PATH, "method": "PUT", "body": json.dumps({"own_only": False}, separators=(",", ":"))}]
    assert ["fire", TOAST_OFF] in out["after"]["fired"]
    assert out["after"]["checked"] is False


def test_the_box_is_disabled_while_the_write_is_out(driven) -> None:
    out = driven["in_flight"]
    assert out["first"]["disabled"] is True
    assert len(out["second"]["writes"]) == 1


def test_a_refused_write_is_reported_and_the_box_is_left_alone(driven) -> None:
    out = driven["fails"]
    assert out["failures"] == ["change timer announcements"]
    assert out["checked"] is False and out["disabled"] is False
    assert not any(c[0] == "fire" and c[1] in (TOAST_ON, TOAST_OFF) for c in out["fired"])


def test_a_masked_reminder_reads_words_hidden(driven) -> None:
    out = driven["timers_tab"]
    cells = out["cells"]
    assert "reminder (words hidden)" in cells
    assert "call mom" in cells and "pasta" in cells
    assert "—" not in cells


def test_the_timers_tab_lists_what_fired_here_lately(driven) -> None:
    out = driven["timers_tab"]
    assert "/api/timers/fires?room_id=kitchen&limit=10" in out["hooks"]
    first, second = out["fired"]
    assert first.startswith("fired ") and first.endswith(" · pasta timer · heard in kitchen")
    assert second.endswith(" · reminder (words hidden) · not heard in any room (kitchen offline)")
    # Shown with no timer running too; and not at all without fire history.
    assert driven["timers_tab_empty"]["fired"] and "no active timers or reminders" in driven[
        "timers_tab_empty"]["texts"]
    assert "recently fired here" not in driven["timers_tab_no_history"]["texts"]


def test_a_shared_screen_shows_no_reminder_words_in_the_timers_tab(driven) -> None:
    """Home and the alert cards keep a reminder's words off a shared screen;
    the drawer's Timers tab and its "recently fired here" list do too."""
    out = driven["timers_tab_shared"]
    assert "call mom" not in " ".join(out["texts"])
    assert "reminder" in out["cells"] and "pasta" in out["cells"]
    first, second = out["fired"]
    assert first.endswith(" · reminder · heard in kitchen")
    assert second.endswith(" · pasta timer · heard in kitchen")


def test_the_switch_is_a_44px_target_on_a_phone() -> None:
    sats = (REPO_ROOT / "web" / "static" / "satellites.jsx").read_text(encoding="utf-8")
    block = sats[sats.index("const SatTimerScopeControl"):sats.index("const OverviewBody")]
    assert 'className="sat-timer-scope"' in block
    css = (REPO_ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".sat-timer-scope { min-height: 44px; }" in css
    before = css[:css.index(".sat-timer-scope {")]
    assert before.rfind("@media (max-width: 760px)") == before.rfind("@media")
