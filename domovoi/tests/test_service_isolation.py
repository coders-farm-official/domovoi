"""The suite never reaches a live service on the machine running it.

A Domovoi installed on the same PC answers at the defaults (core :6370,
Ollama :11434, SearXNG :6888). Tests that made the web forward to the core
without pointing it anywhere reached that live core, so the conftest pins
every service URL a test could fall through to at a dead port before
anything imports domovoi. These tests hold that pin, and fail when the code
grows a new localhost default the pin doesn't cover.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from domovoi.tests.conftest import DEAD_SERVICE_URL, LIVE_SERVICE_ENV

REPO = Path(__file__).resolve().parents[2]

# `os.environ.get("NAME", "http://localhost:...")` and the getenv spelling.
_ENV_DEFAULT = re.compile(
    r"""(?:os\.environ\.get|os\.getenv)\(\s*["']([A-Z0-9_]+)["']\s*,\s*["']"""
    r"""(?:https?|wss?)://(?:localhost|127\.0\.0\.1|\[::1\])[:/"']"""
)
# A settings field in domovoi/config.py: `name: str = "http://localhost:..."`.
_SETTINGS_DEFAULT = re.compile(
    r"""^\s+([a-z0-9_]+)\s*:\s*str\s*=\s*["'](?:https?|wss?)://(?:localhost|127\.0\.0\.1|\[::1\])[:/"']""",
    re.M,
)


def _sources() -> list[Path]:
    roots = [REPO / "domovoi", REPO / "web" / "backend", *sorted((REPO / "plugins").glob("*/domovoi_plugin_*"))]
    out = []
    for root in roots:
        for p in root.rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith("domovoi/tests"):
                continue
            out.append(p)
    return out


def test_the_service_urls_are_pinned_dead_for_the_whole_run() -> None:
    assert DEAD_SERVICE_URL == "http://127.0.0.1:9"
    for key in LIVE_SERVICE_ENV:
        assert os.environ.get(key) == DEAD_SERVICE_URL, key


def test_what_the_code_resolves_is_the_dead_port() -> None:
    from domovoi import webkit
    from domovoi.config import settings
    from web.backend import domovoi_client
    from web.backend.api import plugins as web_plugins

    assert domovoi_client.domovoi_url() == DEAD_SERVICE_URL
    assert webkit.core_url() == DEAD_SERVICE_URL
    assert web_plugins._core_url() == DEAD_SERVICE_URL
    # The settings are built once, at import, after the conftest's pin.
    assert settings.ollama_url == DEAD_SERVICE_URL
    assert settings.searxng_url == DEAD_SERVICE_URL
    assert settings.letta_base_url == DEAD_SERVICE_URL


def test_a_test_can_still_point_the_web_at_its_own_core(monkeypatch) -> None:
    from web.backend import domovoi_client

    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:65000")
    assert domovoi_client.domovoi_url() == "http://127.0.0.1:65000"


def test_every_localhost_default_in_the_code_is_pinned() -> None:
    """A service URL read from the environment with a localhost default, or
    a localhost settings default, is one a test can fall through to: its
    variable must be in conftest.LIVE_SERVICE_ENV."""
    found: dict[str, str] = {}
    for p in _sources():
        src = p.read_text(encoding="utf-8", errors="replace")
        for m in _ENV_DEFAULT.finditer(src):
            found.setdefault(m.group(1), p.relative_to(REPO).as_posix())
    config = (REPO / "domovoi" / "config.py").read_text(encoding="utf-8")
    for m in _SETTINGS_DEFAULT.finditer(config):
        found.setdefault(m.group(1).upper(), "domovoi/config.py")
    # The scan itself works: the defaults this guard was written for.
    assert {"DOMOVOI_URL", "OLLAMA_URL", "SEARXNG_URL"} <= found.keys(), found
    missing = {k: where for k, where in found.items() if k not in LIVE_SERVICE_ENV}
    assert not missing, (
        f"localhost service defaults not pinned dead in domovoi/tests/conftest.py: {missing}; "
        "add them to LIVE_SERVICE_ENV"
    )
