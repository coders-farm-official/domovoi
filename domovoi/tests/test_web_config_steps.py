"""The dashboard's number settings: arrows step, typing is free, a slider
for the everyday sensitivity-style ones (2026-11-09).

`ConfigField` (web/static/components.jsx) draws every setting in Settings →
Configuration and in a room drawer's satellite Settings tab. It used to draw
a decimal as ``<input type="number" step="any">``, and the browser's arrows
then moved by 1: "Wake sensitivity" (0-1) jumped from 0.5 to 1 in one click.

* a number box steps by the server's ``step`` and blurs itself on a mouse
  wheel while focused, so scrolling the page over it changes nothing;
* a ``widget: "slider"`` field is a range slider from min (left) to max
  (right), its ends in words, the exact number in a box beside it; with no
  reported value it is the box alone (no thumb parked at one end);
* a typed value off the step saves as typed; one out of range says why
  under its row (the server's own words) and Save waits, in both editors.

Driven through the dashboard's own Babel (jsx_render_harness.js,
jsx_interact_harness.js). No DB; needs ``node``. The server half is
test_config_steps.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER = Path(__file__).with_name("jsx_render_harness.js")
INTERACT = Path(__file__).with_name("jsx_interact_harness.js")
COMPONENTS = "web/static/components.jsx"
SETTINGS = "web/static/settings.jsx"
SATELLITES = "web/static/satellites.jsx"

WAKE = {
    "name": "wake.threshold", "label": "Wake sensitivity", "group": "Wake word",
    "section": "common", "tier": "hot", "type": "float", "min": 0.0, "max": 1.0,
    "choices": None, "choice_labels": None, "unit": None, "help": "h",
    "step": 0.05, "widget": "slider", "slider_labels": ["more sensitive", "fewer false wakes"],
}
SILENCE = {
    "name": "listen.silence_timeout", "label": "End-of-speech silence", "group": "Listening",
    "section": "common", "tier": "hot", "type": "float", "min": 0.3, "max": 5.0,
    "choices": None, "choice_labels": None, "unit": "sec", "help": "h",
    "step": 0.1, "widget": None, "slider_labels": None,
}
# What an older server sends: no step at all.
OLD_FLOAT = {**SILENCE, "name": "old.float", "step": None}
del OLD_FLOAT["widget"], OLD_FLOAT["slider_labels"]


def _render(field: dict, value) -> dict:
    return {"file": COMPONENTS, "component": "ConfigField",
            "props": {"f": field, "value": value}, "fnProps": ["onChange"]}


RENDERS = {
    "slider": _render(WAKE, 0.5),
    "slider_at_min": _render(WAKE, 0.0),
    "slider_unreported": _render(WAKE, None),
    "box": _render(SILENCE, 0.8),
    "box_out_of_range": _render(SILENCE, 9),
    "box_empty": _render(SILENCE, ""),
    "slider_out_of_range": _render(WAKE, 1.5),
    "old_server": _render(OLD_FLOAT, 0.8),
}


def _run(harness: Path, scenarios: dict, tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("config-steps") / "scenarios.json"
    spec.write_text(json.dumps(scenarios), encoding="utf-8")
    arg = f"@{spec}" if harness == INTERACT else json.dumps(scenarios)
    proc = subprocess.run([node, str(harness), str(REPO_ROOT), arg],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> dict:
    return _run(RENDER, RENDERS, tmp_path_factory)


def _inputs(nodes: list[dict], typ: str) -> list[dict]:
    return [n for n in nodes if n["type"] == "input" and n["props"].get("type") == typ]


def _problem(nodes: list[dict]) -> str | None:
    rows = [n for n in nodes if n["props"].get("className") == "config-field-problem"]
    return rows[0]["text"] if rows else None


def _text_of(nodes: list[dict], cls: str) -> list[str]:
    return [n["text"] for n in nodes if n["props"].get("className") == cls]


# ─── Rendering ───────────────────────────────────────────────────────────


def test_a_slider_runs_from_min_to_max_by_the_step(rendered) -> None:
    nodes = rendered["slider"]
    (rng,) = _inputs(nodes, "range")
    assert (rng["props"]["min"], rng["props"]["max"], rng["props"]["step"]) == (0.0, 1.0, 0.05)
    assert rng["props"]["value"] == 0.5
    assert rng["props"]["aria-label"] == "Wake sensitivity"
    assert rng["props"]["aria-valuetext"] == "0.5"
    assert rng["props"]["aria-describedby"] == "cfg-wake.threshold-ends"
    assert "onChange" in rng["on"]
    # The ends, left then right, are the words the server sent.
    ends = [n for n in nodes if n["props"].get("className") == "config-slider-ends"]
    assert ends and ends[0]["props"]["id"] == "cfg-wake.threshold-ends"
    spans = [n["text"] for n in nodes if n["type"] == "span" and n["text"] in WAKE["slider_labels"]]
    assert spans == ["more sensitive", "fewer false wakes"]
    # The exact number beside it, still typeable, stepping the same way.
    (box,) = _inputs(nodes, "number")
    assert box["props"]["step"] == 0.05 and box["props"]["value"] == 0.5
    assert box["props"]["aria-label"] == "Wake sensitivity, exact value"
    assert "onWheel" in box["on"]
    assert _problem(nodes) is None


def test_a_slider_end_reads_out_what_it_does(rendered) -> None:
    (rng,) = _inputs(rendered["slider_at_min"], "range")
    assert rng["props"]["aria-valuetext"] == "0.0, more sensitive" or \
        rng["props"]["aria-valuetext"] == "0, more sensitive"


def test_an_unreported_slider_is_the_box_alone(rendered) -> None:
    nodes = rendered["slider_unreported"]
    assert _inputs(nodes, "range") == []
    (box,) = _inputs(nodes, "number")
    assert box["props"]["value"] == ""


def test_a_number_box_steps_by_the_servers_step_and_ignores_the_wheel(rendered) -> None:
    nodes = rendered["box"]
    assert _inputs(nodes, "range") == []
    (box,) = _inputs(nodes, "number")
    assert box["props"]["step"] == 0.1
    assert box["props"]["step"] != "any"
    assert (box["props"]["min"], box["props"]["max"]) == (0.3, 5.0)
    assert box["props"]["aria-label"] == "End-of-speech silence"
    assert "onWheel" in box["on"]
    assert _problem(nodes) is None


def test_an_older_server_without_a_step_still_never_steps_by_one(rendered) -> None:
    (box,) = _inputs(rendered["old_server"], "number")
    assert box["props"]["step"] == pytest.approx((5.0 - 0.3) / 20)


def test_out_of_range_says_why_under_the_row(rendered) -> None:
    nodes = rendered["box_out_of_range"]
    assert _problem(nodes) == "must be at most 5 (0.3–5 sec) — fix it to save"
    (box,) = _inputs(nodes, "number")
    assert box["props"]["aria-invalid"] is True
    assert box["props"]["aria-describedby"] == "cfg-listen.silence_timeout-problem"
    assert _problem(rendered["box_empty"]) == "expected a number (0.3–5 sec) — fix it to save"


def test_a_typed_value_past_a_slider_end_is_named_and_the_thumb_stays_in_range(rendered) -> None:
    nodes = rendered["slider_out_of_range"]
    (rng,) = _inputs(nodes, "range")
    assert rng["props"]["value"] == 1.0
    assert _problem(nodes) == "must be at most 1 (0–1) — fix it to save"


# ─── Saving (both editors) ───────────────────────────────────────────────

_SANDBOX = r"""
globalThis.Auth = {
  token: 't', status: { setup_complete: true, authenticated: true }, modalOpen: false,
  subscribe: () => () => {}, refreshStatus: () => Promise.resolve(Auth.status),
  isLoggedIn: () => true, isPaired: () => true, headers: () => ({}),
  openModal: () => {}, logout: () => Promise.resolve(true),
};
globalThis.mutationErrorText = (e, verb) => `${verb} failed: ${(e && e.message) || e}`;
globalThis.useGuestViewer = () => false;
window.confirm = () => true;
if (typeof document === 'undefined') globalThis.document = {};
window.__focus = (el) => { document.activeElement = el; };
window.requestAnimationFrame = (fn) => fn();
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
window.__settle = async (h, n = 4) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
"""

# Type out of range, see Save held; type an off-step value, Save it.
_SCRIPT = r"""
  const w = h.global('window');
  const box = (name) => h.find((e) => e.type === 'input' && e.props.type === 'number'
    && h.ancestors(e).some((a) => a.props && a.props['data-config-field'] === name));
  const range = (name) => h.find((e) => e.type === 'input' && e.props.type === 'range'
    && h.ancestors(e).some((a) => a.props && a.props['data-config-field'] === name));
  const save = () => h.findAll({ type: 'button' }).find((e) => /^Save/.test(w.__deep(e)));
  const problem = () => { const p = h.find((e) => e.props && e.props.className === 'config-field-problem');
                          return p ? w.__deep(p) : null; };
  const held = () => { const p = h.find((e) => e.props && e.props.className === 'config-save-held');
                       return p ? w.__deep(p) : null; };
  const look = () => ({ problem: problem(), held: held(), saveDisabled: !!save().props.disabled });
  h.render(); await w.__settle(h);
  const first = look();
  // A wheel over the focused box lets go of the focus instead of nudging it.
  let blurred = 0;
  const el = { blur: () => { blurred += 1; } };
  w.__focus(el);
  await h.fire((e) => e === box('__BOX__'), 'onWheel', { currentTarget: el });
  const wheel = { blurred, calls: h.calls.length };
  await h.type((e) => e === box('__BOX__'), '__BAD__');
  const bad = look();
  await h.click((e) => e === save()); await w.__settle(h);
  const afterBadSave = h.calls.length;
  await h.type((e) => e === box('__BOX__'), '__GOOD__');
  const good = look();
  await h.type((e) => e === range('wake.threshold'), '0.55');
  await h.click((e) => e === save()); await w.__settle(h);
  return { first, wheel, bad, afterBadSave, good, calls: h.calls };
"""


def _driver(component: str, files: list[str], api: dict, props: dict | None = None,
            fn_props: list[str] | None = None, box: str = "", bad: str = "", good: str = "") -> dict:
    return {
        "files": files, "component": component, "props": props or {}, "fnProps": fn_props or [],
        "setup": _SANDBOX, "api": api,
        "script": _SCRIPT.replace("__BOX__", box).replace("__BAD__", bad).replace("__GOOD__", good),
    }


_CORE_EDITABLE = {"fields": [
    {**WAKE, "value": 0.5},     # not a server setting, but the same field shape
    {**SILENCE, "value": 0.8},
], "plugin_fields": [], "advanced_available": True}
_SAT_CONFIG = {"room_id": "office", "reported": True, "fields": [
    {**WAKE, "value": 0.5, "supported": True},
    {**SILENCE, "value": 0.8, "supported": True},
]}

DRIVES = {
    "config_panel": _driver(
        "ConfigPanel", [COMPONENTS, SETTINGS],
        {"GET /api/config/editable": _CORE_EDITABLE,
         "PATCH /api/config/editable": {"applied": ["listen.silence_timeout", "wake.threshold"],
                                        "restart_required": [], "rejected": {}}},
        box="listen.silence_timeout", bad="7.5", good="0.83"),
    "room_settings": _driver(
        "RoomSettingsBody", [COMPONENTS, SATELLITES],
        {"GET /api/satellites/office/config": _SAT_CONFIG,
         "PATCH /api/satellites/office/config": {"sent": ["listen.silence_timeout"], "rejected": {},
                                                 "restarting": True}},
        props={"room": "office"}, fn_props=["fire"],
        box="listen.silence_timeout", bad="0.1", good="0.83"),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    return _run(INTERACT, DRIVES, tmp_path_factory)


@pytest.mark.parametrize("which", ["config_panel", "room_settings"])
def test_out_of_range_holds_save_and_an_off_step_value_saves_as_typed(driven, which) -> None:
    o = driven[which]
    assert o["first"] == {"problem": None, "held": None, "saveDisabled": True}
    assert o["wheel"] == {"blurred": 1, "calls": 0}
    bad = o["bad"]
    assert bad["problem"].startswith("must be at")
    assert bad["problem"].endswith("(0.3–5 sec) — fix it to save")
    assert bad["held"] == "one value needs fixing"
    assert bad["saveDisabled"] is True
    assert o["afterBadSave"] == 0                   # nothing was sent
    assert o["good"] == {"problem": None, "held": None, "saveDisabled": False}
    patches = [c for c in o["calls"] if c["method"] == "PATCH"]
    assert len(patches) == 1
    # 0.83 is off the 0.1 grid and is saved exactly as typed; the slider
    # moved the wake threshold one step, to 0.55.
    assert patches[0]["body"] == {"changes": {"listen.silence_timeout": 0.83, "wake.threshold": 0.55}}
