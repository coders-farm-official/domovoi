"""Settings -> Configuration -> Version: the update unit's warnings.

REV-15 (2026-10 review): an owner who opted into the hash-pinned lock
(``DOMOVOI_USE_LOCK=1``) and whose update could not use it (the venv moved
to another Python, the lock was renamed) got a clean "ok" on the card: the
run still ends ok, and the core dropped every step's detail. The script
now records that step as ``warn`` with "lock not applied: <why>", the core
keeps that detail (``git_version._WARN_DETAIL_STEPS``), and the card shows
it under the last update.

Rendered with the dashboard's own vendored Babel and the plain-object React
in domovoi/tests/jsx_render_harness.js. No DB; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
SETTINGS = "web/static/settings.jsx"

WHY = "requirements-linux-py314.lock is for Python 3.14 and the venv runs Python 3.15"
LOCK_WARN = f"lock not applied: {WHY}; resolved from the index without hash checks"


def _scenario(steps: list[dict], status: str = "ok") -> dict:
    return {
        "file": SETTINGS,
        "component": "LastUpdateWarnings",
        "props": {"lastUpdate": {"status": status, "mode": "update", "steps": steps}},
    }


SCENARIOS = {
    "lock_unused": _scenario([
        {"name": "signature", "status": "warn", "duration_sec": 0.1},
        {"name": "sync-deps", "status": "warn", "duration_sec": 40.0, "detail": LOCK_WARN},
        {"name": "health", "status": "ok", "duration_sec": 3.0},
    ]),
    # An older script's wording, without the prefix: still said as the lock.
    "rollback_unprefixed": _scenario(
        [{"name": "rollback-deps", "status": "warn", "duration_sec": 1.0, "detail": WHY}],
        status="rolled_back",
    ),
    "secrets_added": _scenario([
        {"name": "env-secrets", "status": "warn", "duration_sec": 0.2,
         "detail": "added LETTA_TOKEN; recreate the running domovoi-letta"},
    ]),
    "clean": _scenario([
        {"name": "sync-deps", "status": "ok", "duration_sec": 40.0},
        {"name": "signature", "status": "warn", "duration_sec": 0.1},
    ]),
    "no_steps": {"file": SETTINGS, "component": "LastUpdateWarnings",
                 "props": {"lastUpdate": {"status": "ok"}}},
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


def _lines(nodes: list[dict]) -> list[str]:
    return [n["text"] for n in nodes if n["type"] == "div" and n["text"]]


def test_an_unused_lock_is_said_on_the_card(rendered) -> None:
    assert _lines(rendered["lock_unused"]) == [LOCK_WARN]
    # One box, the warning's own (the signature has its badge; a step
    # without a kept detail says nothing here).
    boxes = [n for n in rendered["lock_unused"] if "version-update-warnings" in n["props"].get("className", "")]
    assert len(boxes) == 1


def test_the_reason_is_always_told_as_the_lock(rendered) -> None:
    assert _lines(rendered["rollback_unprefixed"]) == [f"lock not applied: {WHY}"]


def test_other_kept_warnings_are_shown_as_they_are(rendered) -> None:
    assert _lines(rendered["secrets_added"]) == ["added LETTA_TOKEN; recreate the running domovoi-letta"]


@pytest.mark.parametrize("name", ["clean", "no_steps"])
def test_nothing_to_say_renders_nothing(rendered, name) -> None:
    assert rendered[name] == []
