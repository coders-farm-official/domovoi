"""The install trust screen (web/static/plugins.jsx TrustConfirmModal)
renders what the preview carries — rendered outside a browser with
domovoi/tests/jsx_render_harness.js (the same vendored Babel, a
plain-object React), so the assertions run without Postgres or a server.

Covers: each resolved dependency's origin and the off-index flag (PLG-2),
the satellite root-payload panel (PLG-1) and the open-endpoints list
(PLG-1).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
PLUGINS = "web/static/plugins.jsx"
MODAL_FNS = ["onDone", "onCancel", "fire"]

BASE_PREVIEW = {
    "slug": "demo", "name": "Demo", "version": "1.2.3", "publisher": "Coders Farm", "license": "MIT",
    "description": "a demo", "permissions": {"warnings": []},
    "requirements": {"direct": [], "transitive": []},
    "handlers": [], "migration_count": 0, "capabilities": [],
    "open_endpoints": [], "satellite": None,
    "trust_statement": "This plugin runs with full access to your Domovoi server.",
}


def _modal(preview_overrides: dict) -> dict:
    return {
        "file": PLUGINS, "component": "TrustConfirmModal",
        "props": {"stagedId": "abc", "preview": {**BASE_PREVIEW, **preview_overrides},
                  "sourceLabel": "demo.zip", "verb": "install"},
        "fnProps": MODAL_FNS,
    }


SCENARIOS = {
    "origins": _modal({
        "requirements": {
            "direct": ["certifi==2024.2.2"],
            "transitive": [
                {"name": "certifi", "version": "2024.2.2", "hashed": True,
                 "origin": "https://files.pythonhosted.org", "origin_ok": True},
                {"name": "idna", "version": "3.6", "hashed": True,
                 "origin": "https://mirror.example", "origin_ok": False},
            ],
        },
    }),
    "origins_clean": _modal({
        "requirements": {
            "direct": ["certifi==2024.2.2"],
            "transitive": [
                {"name": "certifi", "version": "2024.2.2", "hashed": True,
                 "origin": "https://files.pythonhosted.org", "origin_ok": True},
            ],
        },
    }),
    "satellite_root": _modal({
        "permissions": {"satellite_root": True,
                        "warnings": ["Installs a kernel overlay on every satellite."]},
        "satellite": {
            "apt_packages": ["i2c-tools", "libasound2-plugins"],
            "post_install": "satellite/post-install.sh",
            "pip_requirements": ["smbus2==0.4.3"],
            "files_count": 3,
            "payload_mb": 1.25,
        },
    }),
    "satellite_files_only": _modal({
        "satellite": {
            "apt_packages": [], "post_install": None, "pip_requirements": [],
            "files_count": 2, "payload_mb": 0.01,
        },
    }),
    "no_satellite": _modal({"satellite": None}),
    "open_endpoints": _modal({
        "open_endpoints": [
            {"method": "POST", "path": "/tune", "module": "domovoi_plugin_demo.core",
             "function": "tune", "process": "core"},
            {"method": "POST", "path": "/play", "module": "domovoi_plugin_demo.web",
             "function": "play", "process": "web"},
        ],
    }),
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


def _texts(elements: list[dict]) -> list[str]:
    return [e["text"] for e in elements if e.get("text")]


# ─── PLG-2: origins ───────────────────────────────────────────────────────


def test_each_resolved_dependency_shows_its_origin(rendered) -> None:
    texts = _texts(rendered["origins"])
    assert any("certifi==2024.2.2" in t and "from files.pythonhosted.org" in t for t in texts), texts
    assert any("idna==3.6" in t and "from mirror.example" in t for t in texts), texts


def test_an_off_index_origin_is_flagged(rendered) -> None:
    texts = _texts(rendered["origins"])
    assert any("idna==3.6" in t and "NOT the configured index" in t for t in texts), texts
    assert any("from outside the configured package index" in t for t in texts), texts
    clean = _texts(rendered["origins_clean"])
    assert not any("outside the configured package index" in t for t in clean)
    assert not any("NOT the configured index" in t for t in clean)


# ─── PLG-1: the satellite root payload gets its own warning panel ────────


def test_satellite_payload_renders_in_its_own_panel(rendered) -> None:
    texts = _texts(rendered["satellite_root"])
    assert any("runs as root on every satellite" in t for t in texts), texts
    assert any("i2c-tools" in t and "libasound2-plugins" in t for t in texts), texts
    assert any("satellite/post-install.sh" in t for t in texts), texts
    assert any("smbus2==0.4.3" in t for t in texts), texts
    assert any("3 files" in t and "1.25 MB" in t for t in texts), texts


def test_satellite_files_only_payload_is_still_listed(rendered) -> None:
    texts = _texts(rendered["satellite_files_only"])
    assert any("2 files" in t for t in texts), texts
    assert not any("runs as root" in t for t in texts), texts


def test_no_satellite_section_means_no_panel(rendered) -> None:
    texts = _texts(rendered["no_satellite"])
    assert not any("satellite" in t.lower() and "payload" in t.lower() for t in texts), texts


# ─── PLG-1: open endpoints ────────────────────────────────────────────────


def test_open_endpoints_are_listed_with_their_full_paths(rendered) -> None:
    texts = _texts(rendered["open_endpoints"])
    assert any("POST /v1/plugins/demo/tune" in t for t in texts), texts
    assert any("POST /api/plugins/demo/play" in t for t in texts), texts
    assert any("without signing in" in t for t in texts), texts
