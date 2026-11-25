"""Settings → Privacy → Keep voice history (1.5.0), driven outside a browser.

"What's kept and for how long" lives in Settings → Privacy: the "Keep
voice history" setting (``voice_history_keep``) has its own section there
(``VoiceHistoryKeepPanel``, people.jsx), and the two spots that used to
carry their own copy of the control now show its value and link there:

* People → Voice history (``VoiceHistoryView``) keeps "Delete all voice
  history" and the counts, with no select;
* System → Advanced → Privacy (``ConfigPanel``) shows a line, not the field.

The save goes through the config-edit route (``PATCH /api/config/editable``)
like before; a shorter keep asks first. The routes and the search are
test_web_settings_routes.py and test_web_settings_health.py.

``jsx_interact_harness.js`` compiles the real components with the
dashboard's own Babel. No DB, never ``requires_db``; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests import test_web_internet_ui as inet

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
PEOPLE_FILES = ["web/static/components.jsx", "web/static/people.jsx"]
POINTER = "Settings → Privacy → Keep voice history"

CHOICES = [{"value": "30", "label": "30 days"}, {"value": "90", "label": "90 days"},
           {"value": "365", "label": "1 year"}, {"value": "forever", "label": "Forever"}]
STATUS = {"keep": "90", "keep_label": "90 days", "keep_days": 90, "choices": CHOICES,
          "turns": 12, "oldest_at": "2026-11-01T00:00:00Z", "delete_all": None, "last_cleanup": None}

PEOPLE_SETUP = (
    "useToast = () => [(m) => { window.__toasts = (window.__toasts || []).concat([m]); }, null];"
)

_PANEL_SCRIPT = r"""
  h.render(); await h.settle(); h.rerender();
  const sel = () => h.find({ type: 'select' });
  const out = { text: h.text().join(' | '),
                select: sel() ? { value: sel().props.value, disabled: !!sel().props.disabled } : null,
                options: h.findAll({ type: 'option' }).map((o) => [o.props.value, h.plain(o).text]) };
  // Longer: saved at once.
  await h.change({ type: 'select' }, '365');
  out.afterLonger = h.calls.map((c) => [c.method, c.path, c.body]);
  // Shorter: asked first, then saved.
  await h.change({ type: 'select' }, '30');
  out.asked = h.text().join(' | ');
  out.beforeConfirm = h.calls.length;
  const go = h.find({ type: 'button', text: 'Keep 30 days' });
  if (go) await h.click(go);
  out.calls = h.calls.map((c) => [c.method, c.path, c.body]);
  out.toasts = h.global('window').__toasts || [];
  return out;
"""

_VIEW_SCRIPT = r"""
  h.render(); await h.settle(); h.rerender();
  const links = h.findAll({ type: 'a' }).map((a) => [a.props.href, h.plain(a).text]);
  return { text: h.text().join(' | '), selects: h.findAll({ type: 'select' }).length, links,
           deleteAll: !!h.find({ type: 'button', text: 'Delete all voice history' }) };
"""

_CONFIG_SCRIPT = r"""
  const w = h.global('window');
  h.render(); await w.__settle(h);
  const show = h.find({ type: 'button', text: 'show the settings' });
  if (show) { await h.click(show); await w.__settle(h); }
  return {
    line: w.__byClass(h, 'config-keep-voice-pointer').map((e) => w.__deep(e)),
    links: h.findAll({ type: 'a' }).map((a) => a.props.href),
    fields: h.findAll((e) => e.props && e.props['data-config-field']).map((e) => e.props['data-config-field']),
    selects: h.findAll({ type: 'select' }).map((e) => e.props.value),
  };
"""


def _config_api() -> dict:
    fields = [
        inet._field("bot_name", "Bot name", "Identity", "str", "Domovoi"),
        inet._field("voice_history_keep", "Keep voice history", "Privacy", "choice", "90",
                    tier="reapply", choices=["30", "90", "365", "forever"],
                    choice_labels={"30": "30 days", "90": "90 days", "365": "1 year", "forever": "Forever"}),
        inet._field("music_listens_keep", "Keep listening history", "Privacy", "choice", "365",
                    tier="reapply", choices=["off", "90", "365"],
                    choice_labels={"off": "Don't keep", "90": "90 days", "365": "1 year"}),
    ]
    return {"GET /api/config/editable": {"advanced_available": True, "plugin_fields": [], "fields": fields},
            "GET /api/config": {"web_version": "w", "internet_access": "always"}}


SCENARIOS = {
    "panel": {
        "files": PEOPLE_FILES, "component": "VoiceHistoryKeepPanel", "setup": PEOPLE_SETUP,
        "api": {"GET /api/voice-history": STATUS,
                "PATCH /api/config/editable": {"applied": ["voice_history_keep"], "rejected": {}}},
        "script": _PANEL_SCRIPT,
    },
    "panel_unpaired": {
        "files": PEOPLE_FILES, "component": "VoiceHistoryKeepPanel", "setup": PEOPLE_SETUP,
        "api": {"GET /api/voice-history": {"__error": {"status": 401, "message": "pair first"}}},
        "script": _VIEW_SCRIPT,
    },
    "people_view": {
        "files": PEOPLE_FILES, "component": "VoiceHistoryView", "setup": PEOPLE_SETUP,
        "props": {"vh": {"data": STATUS, "error": None}}, "fnProps": ["onBack", "fire"],
        "api": {}, "script": _VIEW_SCRIPT,
    },
    "advanced": {
        "files": [inet.COMPONENTS, inet.SETTINGS], "setup": inet._setup("always"),
        "component": "ConfigPanel", "api": _config_api(), "script": _CONFIG_SCRIPT,
    },
}


@pytest.fixture(scope="module")
def ui(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive the dashboard's JSX"
    spec = tmp_path_factory.mktemp("kvh") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_the_privacy_section_holds_the_control_and_saves_through_config_edit(ui) -> None:
    sc = ui["panel"]
    assert "Keep voice history" in sc["text"]
    assert sc["select"] == {"value": "90", "disabled": False}
    assert sc["options"] == [["30", "30 days"], ["90", "90 days"], ["365", "1 year"], ["forever", "Forever"]]
    assert "an admin changes this" in sc["text"]
    assert "12 turns kept" in sc["text"]
    # Longer deletes nothing: saved at once, through the config-edit route.
    assert sc["afterLonger"] == [["PATCH", "/api/config/editable", {"changes": {"voice_history_keep": "365"}}]]
    # Shorter deletes now: asked first, saved only on the yes.
    assert "Keep voice history for 30 days?" in sc["asked"]
    assert sc["beforeConfirm"] == 1
    assert sc["calls"][-1] == ["PATCH", "/api/config/editable", {"changes": {"voice_history_keep": "30"}}]


def test_an_unpaired_browser_sees_no_control(ui) -> None:
    sc = ui["panel_unpaired"]
    assert sc["selects"] == 0
    assert not sc["deleteAll"]


def test_people_voice_history_links_to_privacy_instead_of_a_second_control(ui) -> None:
    sc = ui["people_view"]
    assert sc["selects"] == 0
    assert ["#settings/keep-voice-history", POINTER] in sc["links"]
    assert "90 days" in sc["text"]
    # Delete all stays on People.
    assert sc["deleteAll"]


def test_advanced_privacy_shows_a_line_not_the_field(ui) -> None:
    sc = ui["advanced"]
    assert "voice_history_keep" not in sc["fields"]
    assert "90" not in sc["selects"]
    assert "#settings/keep-voice-history" in sc["links"]
    assert len(sc["line"]) == 1 and "Keep voice history: 90 days" in sc["line"][0] and POINTER in sc["line"][0]
    # The rest of the Privacy group is untouched.
    assert "music_listens_keep" in sc["fields"]
