"""The whiteboard must never open blank over a saved scene, and the first
stroke must count as an edit.

Two data-loss bugs sat under the Close guard, and they compose into the
worst case: open a saved board, it renders blank, draw one stroke, the
board reads as clean, close it — and both the stroke and the original
scene are gone.

1. ``DrawingCanvas`` had a loading branch for the Excalidraw BUNDLE and
   none for the DATA. It rendered ``<Excalidraw initialData={initialData
   || null}>`` the moment ``lib`` was truthy, and Excalidraw reads
   ``initialData`` once, at mount. ``lib`` belongs to the page and
   survives the overlay, so from the second whiteboard of a session
   onwards the canvas mounted with ``null`` while the read was still in
   flight: a blank board for a file that is not empty, and the next Save
   writes that blank over it.

2. ``onSceneChange`` took its baseline from whatever onChange arrived
   first. If the first one a bundle fires is the operator's first
   stroke, that stroke becomes the baseline — the header says nothing,
   and Close discards it without asking.

These are behaviours, not spellings, so this module DRIVES the real
component through ``jsx_interact_harness.js``: the actual drawings.jsx
and components.jsx compiled with the dashboard's own vendored Babel, in
a Node vm with a stateful React, against a fake ExcalidrawLib that
records every ``initialData`` it is handed and hands back its
``onChange`` so a scenario can fire one stroke and no more.

Its sibling ``test_web_drawing_close_guard.py`` pins the Close guard's
shape by reading the source; this module pins what the component
actually does.

No DB, no ``requires_db`` — never skips. Needs ``node`` (the runtime the
JSX compile check already relies on) and fails, not skips, without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
DRAWINGS = "web/static/drawings.jsx"

CONFIRM = "You have unsaved changes. Discard them and close?"

# A saved scene with one element, version 7. The fake bundle's
# getSceneVersion is the real one's arithmetic — the sum of the
# per-element version counters (verified against the vendored 0.17.6
# bundle: `e.reduce((a, t) => a + t.version, 0)`) — and its
# restoreElements mirrors the real `version: e.version || 1` default, so
# the baseline the component computes is the number Excalidraw itself
# would report for the scene it was handed.
SAVED_SCENE = {"elements": [{"id": "a", "version": 7}], "appState": {}, "files": {}}
STROKE = [{"id": "a", "version": 7}, {"id": "b", "version": 1}]

# The fake bundle. `Excalidraw` records the initialData of EVERY render —
# the real component consumes only the first, so mounts[0] is the scene
# the operator would actually be looking at — and renders a node that
# carries onChange, so a scenario can fire a stroke through it.
SETUP = r"""
globalThis.mutationErrorText = (e, verb, o) => {
  if (e && e.authCancelled) return (verb || 'Save') + ' cancelled.';
  return (verb || 'Save') + ' failed: ' + String((e && e.message) || e);
};
window.__mounts = [];
window.__confirms = [];
window.__scene = [];
window.confirm = (m) => { window.__confirms.push(m); return false; };
window.__lib = {
  Excalidraw: function Excalidraw(props) {
    window.__mounts.push(
      props.initialData === undefined ? 'undefined'
      : props.initialData === null ? 'null'
      : (props.initialData.elements || []).map((e) => e.id + '@' + e.version).join(',') || 'empty');
    if (props.excalidrawAPI) props.excalidrawAPI({
      getSceneElements: () => window.__scene,
      getAppState: () => ({}),
      getFiles: () => ({}),
    });
    return React.createElement('div', { name: 'excalidraw-canvas', onChange: props.onChange });
  },
  getSceneVersion: (els) => (els || []).reduce((a, e) => a + (e.version || 0), 0),
  restoreElements: (els) => (els || []).map((e) => Object.assign({}, e, { version: e.version || 1 })),
  serializeAsJSON: (els) => JSON.stringify({ elements: els || [] }),
  exportToSvg: () => Promise.resolve({}),
};
"""

# files.jsx and documents.jsx borrow this overlay through
# window.DrawingSuite, so driving DrawingOverlay drives all three.
COMPONENT = "(p) => React.createElement(DrawingOverlay, Object.assign({}, p, { lib: window.__lib }))"

# `lib` is truthy on the first render — the second-open-of-a-session case
# that is the whole bug. `early` is that first render, before the read
# has resolved; `loaded` is after it has.
LOAD = r"""
  h.render();
  const w = h.global('window');
  const early = { mounts: w.__mounts.slice(), texts: h.text(),
                  save: h.plain(h.find({ type: 'button', text: 'Save' })),
                  exportSvg: h.plain(h.find({ type: 'button', text: 'Export SVG' })) };
  await h.settle(); h.rerender(); await h.settle(); h.rerender();
  const loaded = { mounts: w.__mounts.slice(), texts: h.text(),
                   save: h.plain(h.find({ type: 'button', text: 'Save' })),
                   calls: h.calls.map((c) => c.method + ' ' + c.path) };
"""

CLOSE = r"""
  await h.click({ type: 'button', text: 'Close' });
  const closing = { confirms: w.__confirms.slice(),
                    closed: h.fnCalls.filter((c) => c.name === 'onClose').length };
"""


def _stroke(elements: list[dict], name: str = "afterChange") -> str:
    """Fire ONE onChange, the way Excalidraw does when the scene moves."""
    return (f"  await h.fire({{ name: 'excalidraw-canvas' }}, 'onChange', {json.dumps(elements)});\n"
            f"  const {name} = {{ unsaved: h.text().some((t) => String(t).includes('unsaved')) }};\n")


def _scenario(*, rel_path: str | None, read: object, script: str) -> dict:
    api = {"POST /api/documents/drawings/read": read} if rel_path else {}
    return {
        "files": [COMPONENTS, DRAWINGS],
        "component": COMPONENT,
        "props": {"file": {"rel_path": rel_path} if rel_path else {}},
        "fnProps": ["onClose", "onSaved", "fire"],
        "api": api,
        "setup": SETUP,
        "script": script,
    }


OK_READ = {"content": json.dumps(SAVED_SCENE)}

# The compound case, as the operator lives it: open a board that already
# has something on it, draw ONE stroke, press Close. Both bugs had to be
# fixed for this to end well — if the canvas mounts before the read
# lands, the original is already gone; if the first onChange is eaten as
# the baseline, the stroke goes when Close does.
#
# `w.__scene` is what the live canvas would hand back, so setting it
# alongside the onChange is the fake bundle's way of saying "this is on
# the board now".
COMPOUND = r"""
  const wrote = () => h.calls.filter((c) => c.path === '/api/documents/drawings/write');
  w.__scene = """ + json.dumps(STROKE) + r""";
  await h.fire({ name: 'excalidraw-canvas' }, 'onChange', """ + json.dumps(STROKE) + r""");
  const drawn = { unsaved: h.text().some((t) => String(t).includes('unsaved')),
                  mounts: w.__mounts.slice(), writes: wrote().length };
  await h.click({ type: 'button', text: 'Close' });
  const refused = { confirms: w.__confirms.slice(),
                    closed: h.fnCalls.filter((c) => c.name === 'onClose').length,
                    writes: wrote().length,
                    mounts: w.__mounts.slice(),
                    unsaved: h.text().some((t) => String(t).includes('unsaved')),
                    onCanvas: w.__scene.map((e) => e.id) };
  await h.click({ type: 'button', text: 'Save' });
  const saved = { body: wrote().map((c) => c.body),
                  unsaved: h.text().some((t) => String(t).includes('unsaved')) };
  await h.click({ type: 'button', text: 'Close' });
  const closing = { confirms: w.__confirms.slice(),
                    closed: h.fnCalls.filter((c) => c.name === 'onClose').length };
  return { loaded, drawn, refused, saved, closing };
"""

SCENARIOS = {
    # Bug 1: what is on the canvas while the read is in flight.
    "reopen_saved_board": _scenario(
        rel_path="board.excalidraw", read=OK_READ,
        script=LOAD + "  return { early, loaded };"),
    # Bug 2: the ONLY onChange this scene ever sees is the first stroke.
    "first_stroke_is_the_only_onchange": _scenario(
        rel_path="board.excalidraw", read=OK_READ,
        script=LOAD + _stroke(STROKE) + CLOSE + "  return { afterChange, closing };"),
    # The other arrival order: a mount echo of the loaded scene first,
    # then the stroke. Both must end up armed.
    "mount_echo_then_stroke": _scenario(
        rel_path="board.excalidraw", read=OK_READ,
        script=LOAD + _stroke(SAVED_SCENE["elements"], "afterEcho") + _stroke(STROKE) + CLOSE
        + "  return { afterEcho, afterChange, closing };"),
    # …and the guard still must not cry wolf.
    "untouched_board_closes_silently": _scenario(
        rel_path="board.excalidraw", read=OK_READ,
        script=LOAD + _stroke(SAVED_SCENE["elements"]) + CLOSE
        + "  return { afterChange, closing };"),
    "no_onchange_at_all": _scenario(
        rel_path="board.excalidraw", read=OK_READ,
        script=LOAD + CLOSE + "  return { loaded, closing };"),
    # A new blank whiteboard has nothing to load, so consuming an
    # onChange as the baseline buys nothing and costs the first stroke.
    "new_whiteboard_first_stroke": _scenario(
        rel_path=None, read=None,
        script=LOAD + _stroke([{"id": "a", "version": 1}]) + CLOSE
        + "  return { loaded, afterChange, closing };"),
    # A read that never arrives is the same loss by another door.
    "read_refused": _scenario(
        rel_path="board.excalidraw", read={"__error": {"status": 500, "message": "boom"}},
        script=LOAD + CLOSE + "  return { loaded, closing };"),
    "read_is_not_json": _scenario(
        rel_path="board.excalidraw", read={"content": "not json at all"},
        script=LOAD + "  return { loaded };"),
    # The compound case end to end: the guard fires AND nothing is lost.
    "saved_board_one_stroke_close": _scenario(
        rel_path="board.excalidraw", read=OK_READ, script=LOAD + COMPOUND),
    # The save path still works and still re-baselines.
    "save_rebaselines": _scenario(
        rel_path="board.excalidraw", read=OK_READ,
        script=LOAD + _stroke(STROKE)
        + "  w.__scene = " + json.dumps(STROKE) + ";\n"
          "  await h.click({ type: 'button', text: 'Save' });\n"
          "  const afterSave = { unsaved: h.text().some((t) => String(t).includes('unsaved')),\n"
          "                      calls: h.calls.map((c) => c.method + ' ' + c.path),\n"
          "                      toasts: h.fnCalls.filter((c) => c.name === 'fire').map((c) => c.args[0]) };\n"
        + CLOSE + "  return { afterChange, afterSave, closing };"),
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


# ── 1. the canvas never mounts over a scene it has not got ───────────

def test_the_canvas_waits_for_the_scene_not_only_for_the_bundle(driven):
    """Before the fix this was ``{"mounts": ["null"]}`` on the first
    render: Excalidraw had already taken null as its scene for good."""
    early = driven["reopen_saved_board"]["early"]
    assert early["mounts"] == [], (
        "Excalidraw was mounted before the saved scene arrived — it reads "
        f"initialData once, so this board is blank for good: {early['mounts']}")
    assert any("Loading the scene" in str(t) for t in early["texts"]), early["texts"]


def test_the_scene_that_is_mounted_is_the_one_that_was_read(driven):
    loaded = driven["reopen_saved_board"]["loaded"]
    assert loaded["calls"] == ["POST /api/documents/drawings/read"]
    assert loaded["mounts"], "the canvas never mounted at all"
    assert set(loaded["mounts"]) == {"a@7"}, loaded["mounts"]


def test_a_board_that_has_not_loaded_cannot_be_saved_over(driven):
    """The belt to the canvas's braces: while there is no scene, Save and
    Export SVG would write an empty board over a file that is not."""
    early = driven["reopen_saved_board"]["early"]
    assert early["save"]["props"].get("disabled") is True
    assert early["exportSvg"]["props"].get("disabled") is True
    after = driven["reopen_saved_board"]["loaded"]
    assert after["save"]["props"].get("disabled") is False, "Save never came back"


@pytest.mark.parametrize("case", ["read_refused", "read_is_not_json"])
def test_a_scene_that_could_not_be_read_is_not_a_blank_canvas(driven, case):
    """Silently falling back to an empty board is the same data loss with
    an extra step: the operator draws on it and Save overwrites."""
    loaded = driven[case]["loaded"]
    assert loaded["mounts"] == [], f"{case}: a blank canvas was mounted anyway"
    assert loaded["save"]["props"].get("disabled") is True, f"{case}: Save is live"
    said = " ".join(str(t) for t in loaded["texts"])
    assert "couldn’t be opened" in said, said


def test_a_board_that_failed_to_open_still_closes_without_a_prompt(driven):
    closing = driven["read_refused"]["closing"]
    assert closing["confirms"] == []
    assert closing["closed"] == 1


# ── 2. the first stroke is an edit, whatever order onChange arrives ──

def test_the_first_stroke_arms_the_guard_even_if_it_is_the_first_onchange(driven):
    """Before the fix: ``{"unsaved": false}``, no prompt, and onClose ran
    — one stroke discarded silently."""
    r = driven["first_stroke_is_the_only_onchange"]
    assert r["afterChange"]["unsaved"] is True, "the first stroke read as saved"
    assert r["closing"]["confirms"] == [CONFIRM], r["closing"]
    assert r["closing"]["closed"] == 0, "Close threw the stroke away anyway"


def test_a_mount_echo_before_the_stroke_works_too(driven):
    """The baseline comes from the loaded scene, so an onChange that
    merely re-reports it changes nothing — and the stroke after it still
    counts."""
    r = driven["mount_echo_then_stroke"]
    assert r["afterEcho"]["unsaved"] is False, "re-reporting the loaded scene read as an edit"
    assert r["afterChange"]["unsaved"] is True
    assert r["closing"]["confirms"] == [CONFIRM]
    assert r["closing"]["closed"] == 0


def test_a_new_whiteboards_first_stroke_counts(driven):
    r = driven["new_whiteboard_first_stroke"]
    assert r["loaded"]["calls"] == [], "a new whiteboard must not read anything"
    assert r["afterChange"]["unsaved"] is True
    assert r["closing"]["confirms"] == [CONFIRM]
    assert r["closing"]["closed"] == 0


# ── 3. …and the guard still does not cry wolf ────────────────────────

@pytest.mark.parametrize("case", ["untouched_board_closes_silently", "no_onchange_at_all"])
def test_looking_at_a_saved_board_is_not_an_edit(driven, case):
    closing = driven[case]["closing"]
    assert closing["confirms"] == [], f"{case}: asked about changes nobody made"
    assert closing["closed"] == 1, f"{case}: Close did not close"


def test_a_save_makes_the_saved_scene_the_new_baseline(driven):
    r = driven["save_rebaselines"]
    assert r["afterChange"]["unsaved"] is True
    assert r["afterSave"]["calls"][-1] == "POST /api/documents/drawings/write"
    assert r["afterSave"]["toasts"] == ["Saved"]
    assert r["afterSave"]["unsaved"] is False, "the save did not clear the flag"
    assert r["closing"]["confirms"] == []
    assert r["closing"]["closed"] == 1


# ── 4. the compound case, which needed both fixes ────────────────────


def test_the_saved_board_is_on_the_canvas_before_the_stroke(driven):
    """Half of the loss: if the canvas mounted while the read was in
    flight, the rectangle was never there to lose."""
    r = driven["saved_board_one_stroke_close"]
    # The fake records initialData on every render; the real Excalidraw
    # consumes only the first, so what matters is that they all say the
    # same thing and that thing is the scene that was read.
    assert r["loaded"]["mounts"][0] == "a@7", r["loaded"]["mounts"]
    assert set(r["drawn"]["mounts"]) == {"a@7"}, (
        "the canvas was handed a different scene while the stroke was drawn: "
        f"{r['drawn']['mounts']}")


def test_one_stroke_on_a_saved_board_stops_close(driven):
    r = driven["saved_board_one_stroke_close"]
    assert r["drawn"]["unsaved"] is True, "the stroke read as saved"
    assert r["refused"]["confirms"] == [CONFIRM], r["refused"]
    assert r["refused"]["closed"] == 0, "Close threw the stroke away"


def test_the_refused_close_loses_neither_the_stroke_nor_the_scene(driven):
    """Answering "no" to the prompt has to leave the board exactly as it
    was — both elements still on it, still flagged unsaved, and nothing
    written to the file in the meantime."""
    r = driven["saved_board_one_stroke_close"]["refused"]
    assert r["onCanvas"] == ["a", "b"], r["onCanvas"]
    assert r["unsaved"] is True, "the flag was cleared by a close nobody completed"
    assert r["writes"] == 0, "a cancelled close still wrote to the file"
    assert set(r["mounts"]) == {"a@7"}, r["mounts"]


def test_saving_then_writes_both_the_original_and_the_stroke(driven):
    """The file that comes out the other end is the point: the rectangle
    that was already there AND the stroke just drawn."""
    r = driven["saved_board_one_stroke_close"]
    assert len(r["saved"]["body"]) == 1, r["saved"]["body"]
    body = r["saved"]["body"][0]
    assert body["rel_path"] == "board.excalidraw"
    assert body["fmt"] == "excalidraw"
    written = [e["id"] for e in json.loads(body["content"])["elements"]]
    assert written == ["a", "b"], written
    assert r["saved"]["unsaved"] is False, "the save did not clear the flag"


def test_and_then_it_closes_without_asking(driven):
    r = driven["saved_board_one_stroke_close"]["closing"]
    assert r["confirms"] == [CONFIRM], "the second Close asked again after a save"
    assert r["closed"] == 1, "Close did not close after the work was saved"
