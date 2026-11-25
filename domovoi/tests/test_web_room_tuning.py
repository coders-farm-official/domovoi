"""The room drawer's "Tune the speakers" (1.5.0, slice 7).

* an admin with two speakers that can take part sees the button; it posts
  to /api/rooms/{room}/calibrate and shows the answer's sentence;
* a refusal (409) shows the server's sentence, not a generic error;
* the last run's outcome and each satellite's value are shown to everyone;
  a household member gets no button;
* a room with one speaker that can take part says why there's nothing to tune.

Driven through domovoi/tests/jsx_interact_harness.js. No DB; needs node.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess

import pytest

from domovoi.tests.test_web_rooms_drawer import _OPEN_KITCHEN, _ROOMS, _SETUP, FILES, HARNESS, REPO_ROOT, _sat

_LAST = {"room_id": "kitchen", "status": "partial", "reason": "joined",
         "message": "Tuned 2 of 3 speakers. Shelf keeps its previous setting.",
         "finished_at": "2026-11-22T15:00:00+00:00", "satellites": []}


def _rooms(tunable: int, last=None) -> dict:
    doc = copy.deepcopy(_ROOMS)
    k = doc["rooms"][0]
    k["tunable_speakers"] = tunable
    k["calibration"] = last
    k["satellites"][1]["output_latency_us"] = 11_250
    return doc


def _scenario(script: str, *, admin: bool = True, tunable: int = 2, last=None,
              answer=None) -> dict:
    api = {
        "GET /api/satellites": [_sat("kitchen"), _sat("kitchen_2")],
        "GET /api/rooms": _rooms(tunable, last),
        "GET /api/satellites/approvals": [],
        "GET /api/satellites/pending": [],
        "GET /api/config/version": {"sha": "44f0fff"},
        "GET /api/capabilities": {"host_kind": "linux"},
        "POST /api/rooms/kitchen/calibrate": answer or {
            "room_id": "kitchen", "status": "done", "message": "Tuned 2 speakers.", "satellites": []},
    }
    return {"files": FILES, "component": "SatellitesPanel", "api": api,
            "setup": _SETUP.replace("%ADMIN%", "true" if admin else "false"),
            "script": "const w = h.global('window'); h.render(); await w.__settle(h);\n"
                      + _OPEN_KITCHEN + script}


_LOOK = """
const block = h.find((e) => w.__cls(e).includes('room-tuning'));
return { block: block ? w.__deep(block) : null, text: h.text().join(' | '),
         button: labelsNow().includes('Tune the speakers') };
"""

_TUNE = """
await h.click({ type: 'button', text: 'Tune the speakers' }); await w.__settle(h);
const block = h.find((e) => w.__cls(e).includes('room-tuning'));
return { calls: writes(), block: block ? w.__deep(block) : null };
"""


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    refused = {"__error": {"status": 409, "message": "409 error", "detail": {
        "detail": "Something is playing in this room. Stop it, then tune the speakers."}}}
    sc = {
        "admin": _scenario(_LOOK, last=_LAST),
        "household": _scenario(_LOOK, admin=False, last=_LAST),
        "alone": _scenario(_LOOK, tunable=1),
        "tune": _scenario(_TUNE),
        "refused": _scenario(_TUNE, answer=refused),
    }
    spec = tmp_path_factory.mktemp("room-tuning") / "scenarios.json"
    spec.write_text(json.dumps(sc), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_admin_sees_the_button_the_last_run_and_the_values(driven) -> None:
    d = driven["admin"]
    assert d["button"] is True
    assert "Tuned 2 of 3 speakers. Shelf keeps its previous setting." in d["block"]
    assert "speaker tuned (11.3 ms)" in d["text"]


def test_the_household_sees_the_outcome_but_no_button(driven) -> None:
    d = driven["household"]
    assert d["button"] is False
    assert "Shelf keeps its previous setting" in d["block"]


def test_one_speaker_that_can_take_part_has_nothing_to_tune(driven) -> None:
    d = driven["alone"]
    assert d["button"] is False
    assert "needs two connected speakers" in d["block"]


def test_tuning_posts_and_shows_the_answer(driven) -> None:
    d = driven["tune"]
    assert [(c["method"], c["path"]) for c in d["calls"]] == [("POST", "/api/rooms/kitchen/calibrate")]
    assert "Tuned 2 speakers." in d["block"]


def test_a_refusal_shows_the_servers_sentence(driven) -> None:
    d = driven["refused"]
    assert "Something is playing in this room. Stop it, then tune the speakers." in d["block"]
