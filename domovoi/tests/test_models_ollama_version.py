"""The oldest Ollama Domovoi runs on, against whatever Ollama is configured
("use my existing Ollama", mac-installer PLAN §12.2).

``domovoi.clients.ollama.server_version`` asks ``GET /api/version``; the
Models page's ``GET /api/models/installed`` carries the answer as
``ollama_version``, and models.jsx says it in words when that Ollama is
older. provision.sh stops before pulling into an older one; its
``OLLAMA_MIN_VERSION`` is held to the Python constant here.

DB-free. The page half drives models.jsx in the Node interact harness
(needs ``node``).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from domovoi.clients import ollama

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
PROVISION = REPO_ROOT / "scripts" / "linux" / "provision.sh"


def _serve(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler), timeout=None)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _version(v):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        return httpx.Response(200, json={"version": v})
    return handler


def test_the_minimum_is_the_one_provision_sh_checks():
    m = re.search(r"^OLLAMA_MIN_VERSION=([0-9]+\.[0-9]+\.[0-9]+)$", PROVISION.read_text(encoding="utf-8"), re.M)
    assert m, "OLLAMA_MIN_VERSION= not found in provision.sh"
    assert tuple(int(x) for x in m.group(1).split(".")) == ollama.MIN_OLLAMA_VERSION


def test_the_pinned_ollama_is_new_enough():
    doc = (REPO_ROOT / "docs" / "LINUX_HOST.md").read_text(encoding="utf-8")
    pinned = re.search(r"^OLLAMA_VERSION=([0-9.]+)$", doc, re.M).group(1)
    assert ollama._parse_ollama_version(pinned) >= ollama.MIN_OLLAMA_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize("served, ok", [
    ("0.34.4", True),
    ("0.12.7", True),
    ("0.12.7-rc1", True),   # the suffix is ignored, as the router reads it
    ("0.12.6", False),
    ("0.9.0", False),
    ("1.0.0", True),
    ("0.0.0", None),        # a build from source: can't tell
    ("nonsense", None),
])
async def test_the_served_version_against_the_minimum(monkeypatch, served, ok):
    _serve(monkeypatch, _version(served))
    got = await ollama.server_version("http://x")
    assert got == {"version": served, "ok": ok, "min": "0.12.7"}


@pytest.mark.asyncio
async def test_an_ollama_that_does_not_answer_is_unknown_not_old(monkeypatch):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _serve(monkeypatch, down)
    assert await ollama.server_version("http://x") == {"version": None, "ok": None, "min": "0.12.7"}
    _serve(monkeypatch, lambda r: httpx.Response(500))
    assert (await ollama.server_version("http://x"))["ok"] is None
    _serve(monkeypatch, lambda r: httpx.Response(200, json=["not", "a", "dict"]))
    assert (await ollama.server_version("http://x"))["ok"] is None


@pytest.mark.asyncio
async def test_it_asks_the_configured_ollama(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"version": "0.34.4"})

    _serve(monkeypatch, handler)
    monkeypatch.setattr(ollama.settings, "ollama_url", "http://host.lima.internal:11500/")
    await ollama.server_version()
    assert seen == ["http://host.lima.internal:11500/api/version"]


@pytest.mark.asyncio
async def test_the_installed_list_carries_the_version(monkeypatch):
    import web.backend.api.models as models_api

    async def _tags():
        return [{"name": "llama3.2:3b"}]

    async def _ps():
        return []

    async def _ver():
        return {"version": "0.12.6", "ok": False, "min": "0.12.7"}

    monkeypatch.setattr(models_api.ollama_client, "list_models", _tags)
    monkeypatch.setattr(models_api.ollama_client, "ps", _ps)
    monkeypatch.setattr(models_api.ollama_client, "server_version", _ver)
    body = await models_api.get_installed()
    assert body["ollama_version"] == {"version": "0.12.6", "ok": False, "min": "0.12.7"}
    assert body["ollama_reachable"] is True and body["installed"][0]["name"] == "llama3.2:3b"


# ─── the page ───────────────────────────────────────────────────────────────

SCRIPT = r"""
h.render(); await h.settle(); h.rerender();
const notes = h.findAll((el) => String((el.props || {}).className || '').includes('models-ollama-too-old'));
return { notes: notes.map((n) => n.text), role: notes.map((n) => n.props.role || null) };
"""


def _scenario(version):
    return {
        "files": ["web/static/components.jsx", "web/static/models.jsx"],
        "component": "ModelsPanel",
        "api": {
            "GET /api/config": {"bot_name": "Domovoi", "internet_access": ""},
            "GET /api/models/installed": {"installed": [{"name": "llama3.2:3b", "size_bytes": 1, "loaded": False}],
                                          "ollama_reachable": True, "ollama_version": version},
            "GET /api/models/catalog": {"version": 1, "ollama": [], "whisper": []},
            "GET /api/models/active": {"roles": []},
            "GET /api/models/hardware": {"gpus": [], "ram": None},
            "GET /api/models/jobs": {"jobs": []},
            "GET /api/models/ai": {"enabled": True},
        },
        "script": SCRIPT,
    }


SCENARIOS = {
    "old": _scenario({"version": "0.12.6", "ok": False, "min": "0.12.7"}),
    "new": _scenario({"version": "0.34.4", "ok": True, "min": "0.12.7"}),
    "unknown": _scenario({"version": None, "ok": None, "min": "0.12.7"}),
    "older_web": _scenario(None),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    scen = tmp_path_factory.mktemp("ollama_version") / "scenarios.json"
    scen.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{scen}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def test_an_older_ollama_is_said_in_words(driven):
    (note,) = driven["old"]["notes"]
    assert "version 0.12.6" in note and "needs 0.12.7 or newer" in note
    assert "update that Ollama" in note
    assert driven["old"]["role"] == ["alert"]


@pytest.mark.parametrize("name", ["new", "unknown", "older_web"])
def test_nothing_is_said_unless_it_is_known_to_be_older(driven, name):
    assert driven[name]["notes"] == []
