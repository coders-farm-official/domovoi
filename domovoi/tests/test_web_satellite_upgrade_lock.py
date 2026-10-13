"""Settings → Satellites: Upgrade locks from the server's yes until the
satellite is back (Kamron, 2026-10-13).

The button stayed enabled after a successful upgrade request. Now, in a
room's drawer (satellites.jsx OverviewBody):

* a press the server accepts locks the button ("Upgrading…") and the
  Restart beside it, and says what is happening: syncing while the
  satellite is online, restarting while it is away, with a clock;
* it stays locked until the satellite is back: the server's record of the
  press (the row's ``upgrade``, domovoi/satellite_upgrades.py) says
  ``done``, ``rolled_back`` or ``no_news``, and the drawer says how it
  went, in a line and once in a toast;
* a refused, failed or cancelled request leaves the button offered and
  says why under it;
* a page reloaded mid-upgrade (no press of its own) locks on the record;
* a server that keeps no record (an older core) ends the press when the
  satellite reports the version it was asked for.

Driven through domovoi/tests/jsx_interact_harness.js (the dashboard's own
Babel, a small stateful React). No DB, never ``requires_db``; needs
``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from domovoi.tests.test_web_satellite_supports import FILES, HARNESS, REPO_ROOT, _SETUP, _row

NOW = datetime.now(timezone.utc).replace(microsecond=0)
STARTED = NOW.isoformat()
EARLIER = (NOW - timedelta(hours=1)).isoformat()


def _rec(state: str, *, started: str = STARTED, back: str | None = None) -> dict:
    return {"state": state, "expected_sha": "new1234", "from_sha": "old1234", "back_sha": back,
            "started_at": started, "deadline_at": None, "finished_at": None}


# A host component whose `s` the script can change between renders, the way
# the satellites list hands the drawer a fresh row after every read.
_HOST = _SETUP + r"""
window.confirm = () => true;
window.__fired = [];
window.__refreshes = 0;
reportMutationFailure = (fire, verb, e) => {
  const msg = e && e.loginPrompted ? null
    : (e && e.authCancelled ? `${verb} cancelled — not signed in.` : `${verb} failed: ${e && e.message}`);
  if (msg) fire(msg);
  return msg;
};
globalThis.UpgradeHost = () => React.createElement(OverviewBody, {
  s: window.__s, sats: [window.__s],
  fire: (m) => window.__fired.push(m),
  onClose: () => {},
  refresh: () => { window.__refreshes += 1; },
});
window.__look = (h) => {
  const w = h.global('window');
  const btn = h.find((e) => e.type === 'button' && /^(Upgrade satellite|Upgrading…)$/.test(w.__deep(e)));
  const restart = h.find((e) => e.type === 'button' && w.__deep(e) === 'Restart satellite');
  const line = (cls) => { const el = h.find((e) => w.__cls(e).includes(cls)); return el ? w.__deep(el) : null; };
  return {
    button: btn ? w.__deep(btn) : null, disabled: btn ? !!btn.props.disabled : null,
    restartDisabled: restart ? !!restart.props.disabled : null,
    progress: line('sat-upgrade-progress'), result: line('sat-upgrade-result'),
    error: line('sat-upgrade-error'),
    posts: h.calls.filter((c) => c.method === 'POST' && /\/upgrade$/.test(c.path)).length,
    fired: w.__fired.slice(), refreshes: w.__refreshes,
  };
};
window.__press = async (h) => {
  const w = h.global('window');
  await h.click((e) => e.type === 'button' && /^(Upgrade satellite|Upgrading…)$/.test(w.__deep(e)));
  await w.__settle(h);
};
"""


def _scenario(row: dict, upgrade_answer, script: str) -> dict:
    return {
        "files": FILES, "component": "UpgradeHost",
        "setup": f"window.__s = {json.dumps(row)};\n" + _HOST,
        "api": {
            "GET /api/config/version": {"sha": "new1234"},
            "POST /api/satellites/kitchen/upgrade": upgrade_answer,
        },
        "script": "const w = h.global('window'); h.render(); await w.__settle(h);\n" + script,
    }


def _kitchen(**over) -> dict:
    row = _row("kitchen", board="raspberry_pi_zero_2_w")
    row["version"] = "old1234"
    row.update(over)
    return row


ACCEPTED = {"requested": True, "room_id": "kitchen", "expected_sha": "new1234", "upgrade": _rec("running")}
OLD_SERVER = {"requested": True, "room_id": "kitchen", "expected_sha": "new1234"}

SCENARIOS = {
    "accepted_then_back": _scenario(_kitchen(), ACCEPTED, f"""
        const steps = {{ before: w.__look(h) }};
        await w.__press(h);
        steps.pressed = w.__look(h);
        await w.__press(h);                                 // a second press does nothing
        steps.again = w.__look(h);
        w.__s = {{ ...w.__s, status: 'offline', version: null, upgrade: {json.dumps(_rec("running"))} }};
        await w.__settle(h);
        steps.away = w.__look(h);
        w.__s = {{ ...w.__s, status: 'online', version: 'new1234', upgrade: {json.dumps(_rec("done"))} }};
        await w.__settle(h);
        steps.back = w.__look(h);
        return steps;
    """),
    "accepted_then_rolled_back": _scenario(_kitchen(), ACCEPTED, f"""
        await w.__press(h);
        w.__s = {{ ...w.__s, upgrade: {json.dumps(_rec("rolled_back", back="old1234"))} }};
        await w.__settle(h);
        return w.__look(h);
    """),
    # The row in hand still carries LAST time's finished record: that is not
    # news about this press.
    "an_older_record_is_not_this_press": _scenario(
        _kitchen(upgrade=_rec("done", started=EARLIER)), ACCEPTED, """
        await w.__press(h);
        return w.__look(h);
    """),
    "failed": _scenario(_kitchen(), {"__error": {"status": 502, "message": "couldn't reach satellite: closed"}}, """
        await w.__press(h);
        return w.__look(h);
    """),
    "already_upgrading_elsewhere": _scenario(
        _kitchen(), {"__error": {"status": 409, "message": "kitchen is already upgrading (asked 12 s ago)"}}, """
        await w.__press(h);
        return w.__look(h);
    """),
    "sign_in_cancelled": _scenario(_kitchen(), {"__error": {"status": 401, "authCancelled": True}}, """
        await w.__press(h);
        return w.__look(h);
    """),
    "reloaded_mid_upgrade": _scenario(_kitchen(status="offline", version=None, upgrade=_rec("running")), ACCEPTED, """
        return w.__look(h);
    """),
    "reloaded_after": _scenario(_kitchen(version="new1234", upgrade=_rec("done")), ACCEPTED, """
        return w.__look(h);
    """),
    "older_server": _scenario(_kitchen(), OLD_SERVER, """
        await w.__press(h);
        const pressed = w.__look(h);
        w.__s = { ...w.__s, status: 'offline', version: null };
        await w.__settle(h);
        const away = w.__look(h);
        w.__s = { ...w.__s, status: 'online', version: 'new1234' };
        await w.__settle(h);
        return { pressed, away, back: w.__look(h) };
    """),
    # The pure view, at the edges the drawer can't easily reach: the wait
    # for a server that keeps no record runs out.
    "view_timing": {
        "files": FILES, "component": "satUpgradeView", "setup": _SETUP,
        "script": r"""
          const v = h.global('window').__component;
          const s = { room_id: 'kitchen', status: 'online', version: 'old1234' };
          const press = { at: 1000, expected: 'new1234', serverStartedMs: null };
          return {
            waiting: v(s, press, 1000 + 60000).state,
            gaveUp: v(s, press, 1000 + 6 * 60000 + 1).state,
            serverGone: v(s, { ...press, serverStartedMs: 1000 }, 1000 + 6 * 60000 + 1).state,
            nothing: v(s, null, 0).state,
          };
        """,
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("sat-upgrade-lock") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_an_accepted_upgrade_locks_the_button_and_the_restart(driven) -> None:
    o = driven["accepted_then_back"]
    assert o["before"]["button"] == "Upgrade satellite" and o["before"]["disabled"] is False
    p = o["pressed"]
    assert p["button"] == "Upgrading…" and p["disabled"] is True
    assert p["restartDisabled"] is True
    assert p["progress"].startswith("Syncing its code to new1234 from the Domovoi server…")
    assert p["posts"] == 1 and p["error"] is None
    assert "upgrading kitchen…" in p["fired"]
    assert p["refreshes"] >= 1                     # the list is read again at once
    assert o["again"]["posts"] == 1                # one request, however many presses


def test_it_stays_locked_while_the_satellite_is_away_and_unlocks_when_it_is_back(driven) -> None:
    o = driven["accepted_then_back"]
    assert o["away"]["button"] == "Upgrading…" and o["away"]["disabled"] is True
    assert o["away"]["progress"].startswith("Restarting into the new code…")
    b = o["back"]
    assert b["button"] == "Upgrade satellite"
    assert b["disabled"] is True                   # up to date now
    assert b["restartDisabled"] is False
    assert b["progress"] is None
    assert b["result"] == "upgraded to new1234"
    assert b["fired"].count("kitchen upgraded — now on new1234") == 1


def test_a_rollback_unlocks_and_says_so(driven) -> None:
    o = driven["accepted_then_rolled_back"]
    assert o["button"] == "Upgrade satellite" and o["disabled"] is False
    assert o["result"].startswith("came back on its old code (old1234)")
    assert any(f.startswith("kitchen came back on its old code (old1234)") for f in o["fired"])


def test_last_times_record_does_not_end_this_press(driven) -> None:
    o = driven["an_older_record_is_not_this_press"]
    assert o["button"] == "Upgrading…" and o["disabled"] is True


@pytest.mark.parametrize("name, said", [
    ("failed", "upgrade failed: couldn't reach satellite: closed"),
    ("already_upgrading_elsewhere", "upgrade failed: kitchen is already upgrading (asked 12 s ago)"),
    ("sign_in_cancelled", "upgrade cancelled — not signed in."),
])
def test_a_request_that_did_not_start_leaves_the_button_offered(driven, name, said) -> None:
    o = driven[name]
    assert o["button"] == "Upgrade satellite" and o["disabled"] is False
    assert o["progress"] is None
    assert o["error"] == said
    assert said in o["fired"]


def test_a_refusal_because_one_is_running_reads_the_list_again(driven) -> None:
    assert driven["already_upgrading_elsewhere"]["refreshes"] >= 1
    assert driven["failed"]["refreshes"] == 0


def test_a_reload_mid_upgrade_is_still_locked(driven) -> None:
    o = driven["reloaded_mid_upgrade"]
    assert o["button"] == "Upgrading…" and o["disabled"] is True
    assert o["restartDisabled"] is True
    assert o["progress"].startswith("Restarting into the new code…")


def test_a_reload_after_says_how_it_went_without_a_toast(driven) -> None:
    o = driven["reloaded_after"]
    assert o["button"] == "Upgrade satellite"
    assert o["result"] == "upgraded to new1234"
    assert o["fired"] == []


def test_an_older_server_ends_the_press_on_the_reported_version(driven) -> None:
    o = driven["older_server"]
    assert o["pressed"]["button"] == "Upgrading…"
    assert o["away"]["button"] == "Upgrading…"
    assert o["back"]["button"] == "Upgrade satellite"
    assert o["back"]["result"] == "upgraded to new1234"


def test_the_wait_gives_up_without_a_record(driven) -> None:
    assert driven["view_timing"] == {
        "waiting": "running", "gaveUp": "no_news", "serverGone": "no_news", "nothing": None,
    }
