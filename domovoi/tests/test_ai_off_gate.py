"""AI off turns off what a plugin makes with a model (owner, Q6), and every
"off" answer says how to turn the feature back on (owner, Q2).

* N4: a plugin's feature that IS a model's work (pictures made on its own
  engine) never asks this box's Ollama, so the SDK's Ollama refusal let it
  run with AI off. A handler or fast path that says ``needs_ai`` is now
  answered by the router ("AI is off on this house, so I can't make
  pictures. …") without running; a route listed in the manifest's ``[ai]``
  table is refused by both plugin route gates; a plugin released before
  the table (the image plugin, capability ``imagegen``) is covered by its
  capability.
* N9: the off lines end with "An admin can turn it on in Settings → …",
  from one place (``domovoi/off_answers.py``), and each place is a real
  section of the dashboard's Settings.

DB-free.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

import domovoi.router as router_mod
from domovoi import ai_features, egress, off_answers, plugin_http, webkit
from domovoi.capabilities import CAPABILITIES
from domovoi.handlers import register_handler, unregister_handler
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response
from domovoi.plugins_runtime.manifest import ManifestError, parse_manifest
from domovoi.tests.test_router_ai_off import offline_router  # noqa: F401 — fixture

REPO = Path(__file__).resolve().parents[2]
AI_LINE_TAIL = "An admin can turn it on in Settings → Chat & AI → Models."
PICTURES_LINE = f"AI is off on this house, so I can't make pictures. {AI_LINE_TAIL}"


# ─── N9: one sentence per feature, at a real Settings place ──────────────

def _settings_labels() -> tuple[set[str], set[str]]:
    src = (REPO / "web" / "static" / "settings.jsx").read_text(encoding="utf-8")
    groups = set(re.findall(r"\{ id: '[a-z-]+', label: '([^']+)', icon:", src))
    sections = set(re.findall(r"\{ id: '[a-z-]+', label: '([^']+)'", src)) - groups
    return groups, sections


def _config_groups() -> set[str]:
    from domovoi.config_schema import EDITABLE_FIELDS

    return {f.group for f in EDITABLE_FIELDS}


@pytest.mark.parametrize("feature", sorted(off_answers.FEATURES))
def test_each_place_is_a_real_settings_section(feature) -> None:
    groups, sections = _settings_labels()
    parts = off_answers.FEATURES[feature].place.split(" → ")
    assert parts[0] == "Settings"
    assert parts[1] in groups, parts
    assert parts[2] in sections, parts
    if len(parts) == 4:
        # An Advanced field: the heading it sits under is its config group.
        assert parts[2] == "Advanced" and parts[3] in _config_groups(), parts


def test_the_member_line() -> None:
    assert off_answers.turn_on_line("ai") == AI_LINE_TAIL
    assert off_answers.turn_on_line("internet") == (
        "An admin can change that in Settings → Privacy → Internet."
    )
    assert off_answers.with_turn_on("Drop-in is off. ", "drop_in") == (
        "Drop-in is off. An admin can turn it on in Settings → System → Advanced → Drop-in."
    )


def test_ai_off_lookup_line_says_how_to_turn_ai_on() -> None:
    assert ai_features.OFF_LOOKUP_LINE == f"I can't look that up with AI turned off. {AI_LINE_TAIL}"


def test_internet_off_replies_say_where_to_change_it() -> None:
    with egress.override_policy("never"):
        assert egress.spoken_offline_reply("check that") == (
            "I'm set to stay off the internet, so I can't check that. "
            "An admin can change that in Settings → Privacy → Internet."
        )
    with egress.override_policy("always"):
        # A lost connection: nothing was switched off, nothing to turn on.
        assert egress.spoken_offline_reply("check that") == (
            "I don't have internet right now, so I can't check that."
        )


def test_friend_houses_off_says_where_to_turn_it_on(monkeypatch) -> None:
    from domovoi.config import settings
    from domovoi.friend_houses import friending
    from domovoi.handlers import friend_houses as fh_handler

    monkeypatch.setattr(settings, "friend_houses_enabled", False, raising=False)
    with pytest.raises(friending.FriendingError) as e:
        friending._require_on()
    assert str(e.value) == (
        "Friend Houses is off. An admin can turn it on in Settings → Household → Friend Houses."
    )
    assert fh_handler._OFF_NO_MESSAGES.endswith(
        "An admin can turn it on in Settings → Household → Friend Houses."
    )


@pytest.mark.asyncio
async def test_chat_mode_with_ai_off(offline_router) -> None:  # noqa: F811
    with ai_features.override(False):
        r = await _say("let's chat")
    assert r.matched_handler == "chat_mode"
    assert r.text == f"Chat mode needs AI, which is turned off. {AI_LINE_TAIL}"


def test_lyrics_off_line() -> None:
    from domovoi.handlers import music

    assert music._LYRIC_NONE_TEXT["disabled"] == (
        "Finding songs by their words is turned off. "
        "An admin can turn it on in Settings → System → Advanced → Library."
    )


# ─── N4: the router's gate ───────────────────────────────────────────────

class _Ran(Exception):
    pass


def _handler(name: str, *, needs_ai: bool, path_needs_ai: bool | None = None,
             slug: str = "aitest") -> Handler:
    async def run(self, m, ctx, session):
        self.ran += 1
        return Response(text=f"{name} ran", session_id=ctx.session_id)

    class _H(Handler):
        priority_band = 100
        tool_schema = {"name": name, "description": "test", "parameters": {"type": "object", "properties": {}}}
        display = HandlerDisplay(label=name)

        async def execute(self, intent, ctx, session):  # pragma: no cover
            raise _Ran()

    h = _H()
    h.name = name
    h.tool_schema = {**_H.tool_schema, "name": name}
    h.needs_ai = needs_ai
    h.plugin_slug = slug
    h.ran = 0
    h.fast_paths = [FastPath(re.compile(rf"^{name.replace('_', ' ')}\b"), run, needs_ai=path_needs_ai)]
    return h


@pytest.fixture
def plugin_handlers():
    made: list[Handler] = []

    def make(*a, **kw) -> Handler:
        h = _handler(*a, **kw)
        register_handler(h)
        made.append(h)
        return h

    ai_features.set_plugin_ai("aitest", ai_features.PluginAI(does="make pictures"))
    try:
        yield make
    finally:
        for h in made:
            unregister_handler(h)
        ai_features.set_plugin_ai("aitest", None)


async def _say(text: str):
    return await router_mod.route(
        Intent(transcript=text, room_id="kitchen"), Context(room_id="kitchen"), None
    )


@pytest.mark.asyncio
async def test_a_needs_ai_fast_path_is_answered_without_running(offline_router, plugin_handlers) -> None:  # noqa: F811
    h = plugin_handlers("zebrapaint", needs_ai=True)
    with ai_features.override(False):
        r = await _say("zebrapaint a cat")
    assert h.ran == 0
    assert r.text == PICTURES_LINE
    assert "aitest" not in r.text and r.matched_handler == "zebrapaint" and r.matched_path == "fast"
    assert len(offline_router) == 2  # still logged


@pytest.mark.asyncio
async def test_a_needs_ai_fast_path_runs_with_ai_on(offline_router, plugin_handlers) -> None:  # noqa: F811
    h = plugin_handlers("zebrapaint", needs_ai=True)
    with ai_features.override(True):
        r = await _say("zebrapaint a cat")
    assert h.ran == 1 and r.text == "zebrapaint ran"


@pytest.mark.asyncio
async def test_other_plugin_paths_still_run_with_ai_off(offline_router, plugin_handlers) -> None:  # noqa: F811
    plain = plugin_handlers("zebrastatus", needs_ai=False)
    reader = plugin_handlers("zebrahistory", needs_ai=True, path_needs_ai=False)
    with ai_features.override(False):
        assert (await _say("zebrastatus please")).text == "zebrastatus ran"
        assert (await _say("zebrahistory please")).text == "zebrahistory ran"
    assert plain.ran == 1 and reader.ran == 1


@pytest.mark.asyncio
async def test_a_path_can_need_ai_on_a_plain_handler(offline_router, plugin_handlers) -> None:  # noqa: F811
    h = plugin_handlers("zebradraw", needs_ai=False, path_needs_ai=True, slug="other")
    with ai_features.override(False):
        r = await _say("zebradraw a cat")
    assert h.ran == 0
    # No [ai] table for that plugin: the generic phrase.
    assert r.text == f"AI is off on this house, so I can't do that. {AI_LINE_TAIL}"


@pytest.mark.asyncio
async def test_run_fast_path_applies_the_gate_too(plugin_handlers) -> None:
    h = plugin_handlers("zebrapaint", needs_ai=True)
    fp = h.fast_paths[0]
    m = fp.pattern.match("zebrapaint a dog")
    with ai_features.override(False):
        r = await router_mod.run_fast_path(h, fp, m, Context(room_id="kitchen"), None)
    assert r.text == PICTURES_LINE and h.ran == 0


# ─── N4: a shipped plugin known by its capability (no handler, no [ai]) ──

@pytest.fixture
def imagegen_installed():
    CAPABILITIES.register("imagegen", object(), slug="imagegen")
    try:
        yield
    finally:
        CAPABILITIES.unregister("imagegen", slug="imagegen")


@pytest.mark.asyncio
@pytest.mark.parametrize("said", [
    "make a picture of a cat",
    "can you draw me a dragon",
    "generate an image of a sunset over the sea",
    "create a cat picture",
])
async def test_picture_requests_hear_that_ai_is_off(offline_router, imagegen_installed, said) -> None:  # noqa: F811
    with ai_features.override(False):
        r = await _say(said)
    assert r.text == PICTURES_LINE


@pytest.mark.asyncio
async def test_without_the_image_plugin_the_general_line_stays(offline_router) -> None:  # noqa: F811
    assert CAPABILITIES.absent("imagegen")
    with ai_features.override(False):
        r = await _say("make a picture of a cat")
    assert r.text == ai_features.OFF_LINE


@pytest.mark.asyncio
async def test_other_requests_are_not_taken_for_pictures(offline_router, imagegen_installed) -> None:  # noqa: F811
    with ai_features.override(False):
        r = await _say("what is the capital of mongolia")
    assert r.text == ai_features.OFF_LINE


# ─── N4: the manifest and the contract ───────────────────────────────────

_BASE = """
[plugin]
slug = "pics"
name = "Pics"
version = "1.0.0"
publisher = "Test"
license = "MIT"
description = "test"
domovoi_api = ">=1.0,<2.0"

[entry_points]
core = "domovoi_plugin_pics.core"
"""


def test_manifest_ai_table_and_handler_field() -> None:
    m = parse_manifest(_BASE + """
[ai]
does = "make pictures"
routes = ["/generate", "/jobs/{job_id}/redo/"]

[[handlers]]
name = "pics"
band = 400
label = "Pictures"
needs_ai = true
""")
    assert m.ai == ai_features.PluginAI(does="make pictures", routes=("/generate", "/jobs/{job_id}/redo"))
    assert m.handlers[0].needs_ai is True


def test_manifest_without_ai_falls_back_to_a_known_capability() -> None:
    m = parse_manifest(_BASE + '\n[capabilities]\nprovides = ["imagegen"]\n')
    assert m.ai is ai_features.KNOWN_AI_CAPABILITIES["imagegen"]
    assert parse_manifest(_BASE).ai is None


@pytest.mark.parametrize("bad", [
    '[ai]\nroutes = "/generate"\n',
    '[ai]\nroutes = ["generate"]\n',
    f'[ai]\ndoes = "{"x" * 61}"\n',
    '[[handlers]]\nname = "pics"\nband = 400\nlabel = "P"\nneeds_ai = "yes"\n',
])
def test_malformed_ai_declarations_are_refused(bad) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(_BASE + "\n" + bad)


def test_the_contract_cross_checks_needs_ai() -> None:
    from domovoi.plugins_runtime.contracts import ContractReport, check_manifest_drift

    m = parse_manifest(_BASE + '\n[[handlers]]\nname = "zebrapaint"\nband = 100\nlabel = "zebrapaint"\nneeds_ai = true\n')
    report = ContractReport()
    check_manifest_drift("pics", m, [_handler("zebrapaint", needs_ai=False)], {}, [], [], report)
    assert any("needs_ai" in e for e in report.errors), report.errors


# ─── N4: the plugin route gates (core and web) ───────────────────────────

def _router() -> APIRouter:
    r = APIRouter()

    @r.post("/generate")
    @webkit.open_endpoint
    async def generate() -> dict:
        return {"ok": True}

    @r.post("/jobs/{job_id}/cancel")
    @webkit.open_endpoint
    async def cancel(job_id: str) -> dict:
        return {"ok": job_id}

    @r.get("/generate")
    async def history() -> dict:
        return {"ok": "read"}

    return r


@pytest.fixture
def core_app():
    app = FastAPI()
    plugin_http.mount_plugin_router(app, "pics", _router())
    ai_features.set_plugin_ai("pics", ai_features.KNOWN_AI_CAPABILITIES["imagegen"])
    try:
        yield TestClient(app)
    finally:
        ai_features.set_plugin_ai("pics", None)
        plugin_http._plugin_enabled.pop("pics", None)
        plugin_http._mounted.pop("pics", None)


def _refused(r) -> None:
    assert r.status_code == ai_features.PLUGIN_ROUTE_REFUSAL_STATUS == 423
    assert r.headers.get("X-Domovoi-Refusal") == "ai-off"
    assert r.json()["detail"] == PICTURES_LINE


def test_core_gate_refuses_a_listed_route_with_ai_off(core_app) -> None:
    with ai_features.override(False):
        _refused(core_app.post("/v1/plugins/pics/generate"))
        assert core_app.post("/v1/plugins/pics/jobs/7/cancel").json() == {"ok": "7"}
        assert core_app.get("/v1/plugins/pics/generate").json() == {"ok": "read"}
    with ai_features.override(True):
        assert core_app.post("/v1/plugins/pics/generate").json() == {"ok": True}


@pytest.fixture
def web_app():
    from web.backend.plugin_host import PluginHost

    host = PluginHost()
    host.rows["imagegen"] = {
        "slug": "imagegen", "enabled": True,
        # The shipped image plugin's manifest: no [ai] table.
        "manifest": {"capabilities": {"provides": ["imagegen"]}},
    }
    app = FastAPI()
    app.include_router(_router(), prefix="/api/plugins/imagegen",
                       dependencies=[Depends(host._slug_gate("imagegen"))])
    return TestClient(app), host


def test_web_gate_covers_the_shipped_image_plugin(web_app) -> None:
    client, _ = web_app
    with ai_features.override(False):
        _refused(client.post("/api/plugins/imagegen/generate"))
        assert client.get("/api/plugins/imagegen/generate").json() == {"ok": "read"}
        assert client.post("/api/plugins/imagegen/jobs/x/cancel").json() == {"ok": "x"}
    with ai_features.override(True):
        assert client.post("/api/plugins/imagegen/generate").json() == {"ok": True}


def test_web_gate_reads_a_declared_ai_table(web_app) -> None:
    client, host = web_app
    host.rows["imagegen"]["manifest"] = {
        "capabilities": {"provides": ["imagegen"]},
        "ai": {"does": "paint", "routes": ["/jobs/{job_id}/cancel"]},
    }
    with ai_features.override(False):
        # The table wins over the capability: /generate isn't listed now.
        assert client.post("/api/plugins/imagegen/generate").json() == {"ok": True}
        r = client.post("/api/plugins/imagegen/jobs/x/cancel")
    assert r.status_code == 423
    assert r.json()["detail"] == f"AI is off on this house, so I can't paint. {AI_LINE_TAIL}"


def test_a_plugin_that_declares_nothing_is_untouched() -> None:
    assert not ai_features.route_needs_ai(None, "POST", "/generate")
    assert ai_features.plugin_route_refusal(
        ai_features.plugin_ai_from_manifest({"capabilities": {"provides": ["stations"]}}),
        "POST", "/generate",
    ) is None
