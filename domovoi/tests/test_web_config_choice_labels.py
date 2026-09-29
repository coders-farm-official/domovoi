"""A choice field in the dashboard's config editors shows its labels.

`ConfigField` (web/static/components.jsx) renders every setting in the
per-satellite Settings tab and the server Configuration tab. A choice whose
raw values aren't words a person would pick carries `choice_labels` — the
satellite's Wake acknowledgement is "greeting" / "chime" / "none" on the
wire and "Spoken greeting, then listen" / … on screen — and the select
must still SAVE the raw value. A value that is none of the choices (a
satellite too old to report the setting sends none at all) shows as what
it is, not silently as the first choice.

Rendered with the dashboard's own vendored Babel and the plain-object React
in domovoi/tests/jsx_render_harness.js. No DB; needs `node`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.satellite_config_schema import FIELD_BY_NAME

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
COMPONENTS = "web/static/components.jsx"

_ACK = FIELD_BY_NAME["wake.ack_mode"]
ACK_FIELD = {
    "name": _ACK.name, "label": _ACK.label, "group": _ACK.group, "section": _ACK.section,
    "tier": _ACK.tier, "type": _ACK.type, "choices": _ACK.choices,
    "choice_labels": _ACK.choice_labels, "help": _ACK.help,
}
LOG_FIELD = {
    "name": "log.level", "label": "Log level", "group": "Logging", "section": "common",
    "tier": "hot", "type": "choice", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"],
    "choice_labels": None, "help": "h",
}


def _scenario(field: dict, value) -> dict:
    return {
        "file": COMPONENTS, "component": "ConfigField",
        "props": {"f": field, "value": value}, "fnProps": ["onChange"],
    }


SCENARIOS = {
    "ack_chime": _scenario(ACK_FIELD, "chime"),
    "ack_unreported": _scenario(ACK_FIELD, None),
    "log_info": _scenario(LOG_FIELD, "INFO"),
    "log_odd": _scenario(LOG_FIELD, "TRACE"),
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _select(nodes: list[dict]) -> dict:
    (select,) = [n for n in nodes if n["type"] == "select"]
    return select


def _options(nodes: list[dict]) -> list[tuple[str, str, bool]]:
    return [
        (n["props"].get("value"), n["text"], bool(n["props"].get("disabled")))
        for n in nodes if n["type"] == "option"
    ]


def test_the_labels_are_shown_and_the_values_saved(rendered):
    nodes = rendered["ack_chime"]
    assert _select(nodes)["props"]["value"] == "chime"
    assert _options(nodes) == [
        ("greeting", "Spoken greeting, then listen", False),
        ("chime", "Short chime, then listen", False),
        ("none", "Light only, listen right away", False),
    ]


def test_an_unreported_value_shows_as_not_set(rendered):
    nodes = rendered["ack_unreported"]
    assert _select(nodes)["props"]["value"] == ""
    opts = _options(nodes)
    assert opts[0] == ("", "(not set)", True)
    assert [v for v, _t, _d in opts[1:]] == ["greeting", "chime", "none"]


def test_a_field_without_labels_shows_its_values(rendered):
    nodes = rendered["log_info"]
    assert _select(nodes)["props"]["value"] == "INFO"
    assert _options(nodes) == [(c, c, False) for c in LOG_FIELD["choices"]]


def test_a_value_outside_the_choices_shows_as_itself(rendered):
    opts = _options(rendered["log_odd"])
    assert opts[0] == ("", "TRACE", True)
