"""The "Restart now?" question after a setting is saved that only a restart
applies (OWNER REQUEST 2026-11-12: after adding the AcoustID key the owner
had to hunt for the restart button).

RestartAfterSave (components.jsx) is what Settings -> Advanced, Settings ->
Internet and the model switch show under a save whose answer carries a
non-empty ``restart_required``. Driven here through the same scripted server
test_web_restart_button.py uses (the real auth.js, data.js and
components.jsx; only ``fetch`` and ``window.confirm`` are scripted), so
"Restart now" is the dashboard's one restart, not a copy:

* a fresh save asks, in words, what was saved and when it takes effect, with
  Restart now and Later; nothing is sent until one is pressed;
* Restart now sends the restart with the admin Bearer, asks no second
  question (the card is the question), shows the restarting note while the
  server is away and says "restarted" when it is back;
* Later sends nothing and leaves the standing notice (with Restart now, and
  About & updates still has its own);
* a page read that merely lists names (no save just made) shows only the
  notice;
* a host that can't restart itself gets the command, not a button that
  can't work;
* no admin sign-in: the button says so and opens the sign-in; a view-only
  tab's press is refused, signs in and replays; a dismissed sign-in says
  "restart cancelled", never a raw status.

Satellites are not here: a room's settings save restarts the room itself
(satellites.jsx says so before and after: "Save & restart"), so there is
nothing to ask. No DB; needs ``node`` and fails without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from domovoi.tests import test_web_restart_button as base

PW = json.dumps(base.PASSWORD)

PROMPT = ("(window.__Auth = Auth, function Prompt() { const [fire, node] = useToast();"
          " return React.createElement(React.Fragment, null,"
          "  React.createElement(RestartAfterSave, { words: window.__words || ['AcoustID key'],"
          "    asked: window.__asked !== false, fire }),"
          "  node, React.createElement(AuthModalHost)); })")

EXTRA = r"""
const ask = () => { const e = byClass('restart-after-save')[0]; return e ? deep(e) : null; };
const kind = () => (byClass('restart-prompt')[0] ? 'prompt' : byClass('restart-notice')[0] ? 'notice'
  : byClass('restart-done')[0] ? 'done' : null);
const buttons = () => h.findAll({ type: 'button' }).filter((e) => !inModal(e)).map((e) => e.text.trim() + (e.props.disabled ? ' [disabled]' : ''));
const look = () => ({ kind: kind(), text: ask(), buttons: buttons(), note: note(), hint: hint(),
                      toasts: toasts(), restarts: restarts(), confirms: w.__confirms.length, modal: !!A.modalOpen });
const pressText = (t) => press((e) => e.type === 'button' && e.text.trim() === t && !inModal(e));
"""


def scn(script: str, **kw) -> dict:
    return base.scenario(EXTRA + script, component=PROMPT, **kw)


SCENARIOS = {
    "fresh": scn("await A.login(__PW); await start(); return look();".replace("__PW", PW)),
    "many": scn("w.__words = ['AcoustID key', 'speech models download checks'];"
                " await A.login(__PW); await start(); return look();".replace("__PW", PW)),
    "now": scn(r"""
await A.login(__PW); await start();
const out = { before: look() };
const flow = pressText('Restart now'); await step();
await pumpUntil(() => !!note());
out.away = look();
letBack();
out.result = await flow; await step(); await step();
out.after = look();
return out;
""".replace("__PW", PW)),
    "later": scn(r"""
await A.login(__PW); await start();
pressText('Later'); await step();
return look();
""".replace("__PW", PW)),
    "later_then_now": scn(r"""
await A.login(__PW); await start();
pressText('Later'); await step();
const flow = pressText('Restart now'); await step();
await pumpUntil(() => !!note());
const away = look();
letBack();
const result = await flow; await step(); await step();
return { away, result, after: look() };
""".replace("__PW", PW)),
    "notice_only": scn("w.__asked = false; await A.login(__PW); await start(); return look();".replace("__PW", PW)),
    "incapable": scn("await A.login(__PW); await start(); return look();".replace("__PW", PW), srv={
        "capable": False, "command": base.LINUX_RESTART, "hint": "no passwordless sudoers grant"}),
    "signed_out": scn(r"""
await start();
const before = look();
pressText('Sign in to restart'); await step();
return { before, after: look() };
""", cookie=None),
    "view_only": scn(r"""
await start();
const before = look();
const flow = pressText('Restart now'); await step();
const prompted = { modal: !!A.modalOpen, restarts: restarts() };
await h.type(password, __PW);
await press(modalButton('log in')); await step();
await pumpUntil(() => !!note());
const away = look();
letBack();
const result = await flow; await step(); await step();
return { before, prompted, away, result, after: look() };
""".replace("__PW", PW)),
    "sign_in_dismissed": scn(r"""
await start();
const flow = pressText('Restart now'); await step();
const prompted = !!A.modalOpen;
A.closeModal(); await step();
const result = await flow; await step(); await step();
return { prompted, result, after: look() };
"""),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    spec = tmp_path_factory.mktemp("restart-prompt") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(base.HARNESS), str(base.REPO_ROOT), "@" + str(spec)],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert proc.returncode == 0, proc.stderr
    return base._Outcomes(json.loads(proc.stdout))


def _said(text: str) -> str:
    return " ".join(text.split())


def test_a_save_that_needs_a_restart_asks_in_words(driven) -> None:
    o = driven["fresh"]
    assert o["kind"] == "prompt"
    said = _said(o["text"])
    assert "AcoustID key" in said
    assert "It takes effect after Domovoi restarts (about 30 seconds; music and voice pause briefly)." in said
    assert o["buttons"] == ["Restart now", "Later"]
    assert o["restarts"] == [] and o["confirms"] == 0, "nothing is sent until one is pressed"


def test_several_names_are_listed_and_plural(driven) -> None:
    said = _said(driven["many"]["text"])
    assert "AcoustID key, speech models download checks" in said
    assert "They take effect after Domovoi restarts" in said


def test_restart_now_is_the_dashboards_restart(driven) -> None:
    o = driven["now"]
    assert o["away"]["restarts"] == ["bearer"], "one restart, with the admin Bearer"
    assert o["away"]["confirms"] == 0, "the card is the question: no second one"
    assert o["away"]["buttons"] == ["Restarting… [disabled]"], o["away"]["buttons"]
    assert o["away"]["note"].startswith("Restarting —"), o["away"]["note"]
    assert o["result"] is True
    assert o["after"]["kind"] == "done" and "restarted" in o["after"]["text"]
    assert base.RESTARTED in o["after"]["toasts"], o["after"]["toasts"]
    assert o["after"]["restarts"] == ["bearer"]


def test_later_sends_nothing_and_leaves_the_notice(driven) -> None:
    o = driven["later"]
    assert o["kind"] == "notice"
    assert _said(o["text"]).startswith("saved — restart the Domovoi server to apply: AcoustID key")
    assert o["restarts"] == [] and o["confirms"] == 0
    assert "Restart now" in o["buttons"] and "Later" not in o["buttons"]


def test_the_notice_can_still_restart(driven) -> None:
    o = driven["later_then_now"]
    assert o["away"]["restarts"] == ["bearer"] and o["away"]["note"].startswith("Restarting —")
    assert o["result"] is True and o["after"]["kind"] == "done"


def test_a_page_read_without_a_save_shows_only_the_notice(driven) -> None:
    o = driven["notice_only"]
    assert o["kind"] == "notice" and "Later" not in o["buttons"]
    assert o["restarts"] == []


def test_a_host_that_cannot_restart_itself_gets_the_command(driven) -> None:
    o = driven["incapable"]
    assert "Restart now" not in o["buttons"], o["buttons"]
    assert o["hint"] and base.LINUX_RESTART in o["hint"], o["hint"]
    assert o["buttons"] == ["Later"]


def test_without_an_admin_sign_in_the_button_asks_for_one(driven) -> None:
    o = driven["signed_out"]
    assert "Sign in to restart" in o["before"]["buttons"] and "Restart now" not in o["before"]["buttons"]
    assert o["after"]["modal"] is True
    assert o["after"]["restarts"] == []


def test_a_view_only_tab_signs_in_and_the_restart_is_replayed(driven) -> None:
    o = driven["view_only"]
    assert o["prompted"] == {"modal": True, "restarts": ["cookie"]}
    assert o["away"]["restarts"] == ["cookie", "bearer"]
    assert o["result"] is True and o["after"]["kind"] == "done"


def test_a_dismissed_sign_in_says_cancelled(driven) -> None:
    o = driven["sign_in_dismissed"]
    assert o["prompted"] is True and o["result"] is False
    assert "restart cancelled — not signed in" in o["after"]["toasts"], o["after"]["toasts"]
    assert o["after"]["kind"] == "prompt", "still asking: nothing was restarted"
    assert not any("403" in t or "Forbidden" in t for t in o["after"]["toasts"])
