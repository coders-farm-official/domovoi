"""The household's AI switch in the dashboard (owner direction 2026-10-21).

* ``GET /api/capabilities`` publishes ``features.ai`` (live, from .env like
  the core) for the dashboard and the app, which hide Chat when it is
  false; voice chat mode (``features.chat``) needs it too.
* Settings → Models: ``GET /api/models/ai`` says whether AI is on and what
  turning it on would download (the role models Ollama doesn't have, with
  their sizes); ``POST /api/models/ai`` saves the switch through the
  core's config path and, asked to, starts those downloads as ordinary
  pull jobs (progress on the page).
* The chat refuses a send or a picture with 409 ai-off while it is off;
  the threads already there stay readable.
* The page half: the launchers drop Chat (the sidebar, the phone strip,
  Home's grid), #chat says why, and the Models card says what turning AI
  off does and what turning it on downloads — driven in the Node interact
  harness (needs ``node``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domovoi import ai_features
from domovoi.tests.conftest import requires_db

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

QA, TOOL, VISION = "llama3.2:3b-instruct-q4_K_M", "qwen3:8b-q4_K_M", "qwen3-vl:2b-instruct-q4_K_M"
FIELDS = [
    {"name": "ollama_model", "value": QA, "tier": "reapply"},
    {"name": "ollama_tool_model", "value": TOOL, "tier": "reapply"},
    {"name": "ollama_vision_model", "value": VISION, "tier": "reapply"},
]


def _client():
    from web.backend.main import app

    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


# ─── /api/capabilities ────────────────────────────────────────────────────


def test_capabilities_publish_the_switch(monkeypatch) -> None:
    from domovoi.config import settings
    from web.backend.api import capabilities

    monkeypatch.setattr(settings, "chat_mode_enabled", True)
    with ai_features.override(True):
        f = capabilities._features()
        assert f["ai"] is True and f["chat"] is True
    with ai_features.override(False):
        f = capabilities._features()
        assert f["ai"] is False
        assert f["chat"] is False       # voice chat mode is the model too


# ─── /api/models/ai ───────────────────────────────────────────────────────


@pytest.fixture
def core(monkeypatch):
    import web.backend.api.models as models_api

    state = {"fields": list(FIELDS), "tags": [{"name": QA}], "posted": [], "pulled": [],
             "post_answer": None}

    async def _get_admin(path, headers=None, **_kw):
        if path == "/v1/admin/config":
            return 200, {"fields": state["fields"]}
        return 404, {"detail": "not scripted"}

    async def _post_admin(path, body, headers=None, **_kw):
        state["posted"].append((path, body))
        return 200, state["post_answer"] or {"applied": ["ai_features"], "restart_required": [], "rejected": {}}

    async def _tags():
        return state["tags"]

    async def _start_pull(body):
        state["pulled"].append(body.model)
        return {"job": {"id": len(state["pulled"]), "model": body.model, "status": "pending"},
                "attached": False}

    monkeypatch.setattr(models_api, "get_admin", _get_admin)
    monkeypatch.setattr(models_api, "post_admin", _post_admin)
    monkeypatch.setattr(models_api, "auth_forward_headers", lambda _r: {})
    monkeypatch.setattr(models_api.ollama_client, "tags_or_none", _tags)
    monkeypatch.setattr(models_api, "start_pull", _start_pull)
    return state


@pytest.mark.asyncio
async def test_what_turning_ai_on_would_download(core) -> None:
    import web.backend.api.models as models_api

    with ai_features.override(False):
        ai = await models_api.get_ai_features(None)
    assert ai["enabled"] is False and ai["locked"] is False and ai["ollama_reachable"] is True
    assert [(m["role"], m["model"], m["installed"]) for m in ai["models"]] == [
        ("qa", QA, True), ("tool", TOOL, False), ("vision", VISION, False)]
    assert ai["missing"] == [TOOL, VISION]
    assert ai["missing_gb"] == 7.1          # 5.2 + 1.9, the installer's sizes


@pytest.mark.asyncio
async def test_one_model_in_two_roles_downloads_once_and_unknown_sizes_say_so(core) -> None:
    import web.backend.api.models as models_api

    core["fields"] = [dict(f, value=TOOL) if f["name"] == "ollama_model" else f for f in FIELDS]
    core["tags"] = []
    ai = await models_api.get_ai_features(None)
    assert ai["missing"] == [TOOL, VISION]
    core["fields"] = [dict(f, value="mystery:7b") if f["name"] == "ollama_model" else f for f in FIELDS]
    ai = await models_api.get_ai_features(None)
    assert ai["missing_gb"] is None
    core["tags"] = None                       # Ollama couldn't be asked
    ai = await models_api.get_ai_features(None)
    assert ai["ollama_reachable"] is False and ai["missing_gb"] is None


@pytest.mark.asyncio
async def test_turning_it_on_saves_the_switch_then_downloads(core) -> None:
    import web.backend.api.models as models_api

    out = await models_api.set_ai_features(models_api.AiFeaturesBody(enabled=True, install=True), None)
    assert core["posted"] == [("/v1/admin/config", {"changes": {"ai_features": True}})]
    assert core["pulled"] == [TOOL, VISION]
    assert [j["model"] for j in out["pulls"]] == [TOOL, VISION] and out["pull_errors"] == {}


@pytest.mark.asyncio
async def test_turning_it_off_downloads_nothing_and_a_refusal_stops_there(core) -> None:
    import web.backend.api.models as models_api

    await models_api.set_ai_features(models_api.AiFeaturesBody(enabled=False, install=True), None)
    assert core["pulled"] == []
    core["post_answer"] = {"applied": [], "restart_required": [],
                           "rejected": {"ai_features": ai_features.ANSWER_LOCKED}}
    out = await models_api.set_ai_features(models_api.AiFeaturesBody(enabled=True, install=True), None)
    assert core["pulled"] == [] and out["rejected"]["ai_features"] == ai_features.ANSWER_LOCKED


@pytest.mark.asyncio
async def test_a_download_that_cant_start_is_reported_not_raised(core, monkeypatch) -> None:
    from fastapi import HTTPException

    import web.backend.api.models as models_api

    async def _refused(body):
        raise HTTPException(status_code=409, detail="internet access is turned off")

    monkeypatch.setattr(models_api, "start_pull", _refused)
    out = await models_api.set_ai_features(models_api.AiFeaturesBody(enabled=True, install=True), None)
    assert out["pull_errors"] == {TOOL: "internet access is turned off", VISION: "internet access is turned off"}


def test_turning_it_on_or_off_is_admin_only() -> None:
    from domovoi.admin_auth import require_admin_mutation
    from web.backend.api.models import router

    route = next(r for r in router.routes if r.path == "/api/models/ai" and "POST" in r.methods)
    assert any(d.call is require_admin_mutation for d in route.dependant.dependencies)


# ─── the chat refuses ─────────────────────────────────────────────────────


@requires_db
def test_the_chat_refuses_a_send_with_ai_off(monkeypatch) -> None:
    import web.backend.api.chat as chat_api

    async def _stream(*a, **k):
        raise AssertionError("asked the model with AI off")
        yield ""

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    with _client() as c:
        t = c.post("/api/chat/threads", json={}).json()
        with ai_features.override(False):
            r = c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": "hello"})
            assert r.status_code == 409
            assert r.json()["detail"] == ai_features.TURNED_OFF_REASON
            assert r.headers["X-Domovoi-Refusal"] == "ai-off"
            up = c.post("/api/chat/uploads", files={"file": ("a.png", b"x", "image/png")})
            assert up.status_code == 409
            # What is there stays readable.
            assert c.get(f"/api/chat/threads/{t['id']}/messages").status_code == 200
        assert c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"] == []
        c.delete(f"/api/chat/threads/{t['id']}")


# ─── the page half ────────────────────────────────────────────────────────

COMPONENTS = "web/static/components.jsx"
_SANDBOX = r"""
window.__ls = new Map(Object.entries(__LS));
localStorage = {
  getItem: (k) => (window.__ls.has(k) ? window.__ls.get(k) : null),
  setItem: (k, v) => { window.__ls.set(k, String(v)); },
  removeItem: (k) => { window.__ls.delete(k); },
};
DeviceIdentity = { id: () => 'dev-1', name: () => 'phone', sharedScreen: () => false,
  canRegister: () => true, subscribeShared: () => () => {} };
window.DomovoiPluginManifest = { plugins: [] };
"""
_NAV = ("h.tree().filter((e) => String((e.props && e.props.className) || '').split(' ').includes('nav-item'))"
        ".map((e) => ({ route: e.props.key, primary: String(e.props.className).split(' ').includes('primary') }))")


def _sidebar(caps, ls=None) -> dict:
    return {
        "files": [COMPONENTS], "component": "Sidebar",
        "props": {"route": "music", "counts": {}, "manifest": {"plugins": []}, "badges": {}},
        "fnProps": ["setRoute"],
        "api": {"GET /api/capabilities": caps},
        "setup": f"const __LS = {json.dumps(ls or {})};\n" + _SANDBOX,
        "script": ("h.render(); for (let i = 0; i < 4; i++) { await h.settle(); h.rerender(); }"
                   " return { nav: " + _NAV + ", stored: h.global('window').__ls.get('domovoi-ai-features') || null };"),
    }


_AI_OFF = {"enabled": False, "locked": False, "ollama_reachable": True, "internet_off": False,
           "models": [{"role": "qa", "model": QA, "installed": False, "download_gb": 2.0, "duplicate": False},
                      {"role": "tool", "model": "qwen3:4b-instruct-2507-q4_K_M", "installed": False,
                       "download_gb": 2.5, "duplicate": False},
                      {"role": "vision", "model": VISION, "installed": False, "download_gb": 1.9, "duplicate": False}],
           "missing": [QA, "qwen3:4b-instruct-2507-q4_K_M", VISION], "missing_gb": 6.4}

SCENARIOS: dict[str, dict] = {
    "on": _sidebar({"features": {"ai": True}}),
    "older_server": _sidebar({"features": {"chat": False}}),
    "unanswered": _sidebar(None),
    "off": _sidebar({"features": {"ai": False}}),
    "remembered_off": _sidebar(None, ls={"domovoi-ai-features": "off"}),
    "chat_page_off": {
        "files": [COMPONENTS, "web/static/chat_markdown.js", "web/static/chat_details.js",
                  "web/static/chat.jsx"], "component": "ChatPage",
        # This browser already knows AI is off; the server says so too.
        "api": {"GET /api/capabilities": {"features": {"ai": False}}},
        "setup": "const __LS = {'domovoi-ai-features': 'off'};\n" + _SANDBOX,
        "script": ("h.render(); for (let i = 0; i < 4; i++) { await h.settle(); h.rerender(); }"
                   " return { text: h.text().join(' '), links: h.findAll({ type: 'a' }).map((e) => e.props.href) };"),
    },
    "models_card_off": {
        "files": [COMPONENTS, "web/static/models.jsx"], "component": "AiFeaturesCard",
        "props": {"ai": _AI_OFF, "internetOff": False},
        "fnProps": ["onSet"],
        "setup": "const __LS = {};\n" + _SANDBOX,
        "script": """
          h.render(); await h.settle();
          const before = h.text().join(' ');
          await h.click({ type: 'button', text: 'turn on' });
          const asking = h.text().join(' ');
          await h.click({ type: 'button', text: 'and turn on' });
          return { before, asking, sets: h.fnCalls };
        """,
    },
    "models_card_on": {
        "files": [COMPONENTS, "web/static/models.jsx"], "component": "AiFeaturesCard",
        "props": {"ai": dict(_AI_OFF, enabled=True, missing=[], missing_gb=0.0,
                             models=[dict(m, installed=True) for m in _AI_OFF["models"]])},
        "fnProps": ["onSet"],
        "setup": "const __LS = {};\n" + _SANDBOX,
        "script": """
          h.render(); await h.settle();
          const text = h.text().join(' ');
          await h.click({ type: 'button', text: 'turn off' });
          return { text, sets: h.fnCalls };
        """,
    },
    # Review round 1: an install with --no-vision-model has AI on and only
    # the vision model missing; the card must not say questions break.
    "models_card_vision_only": {
        "files": [COMPONENTS, "web/static/models.jsx"], "component": "AiFeaturesCard",
        "props": {"ai": dict(_AI_OFF, enabled=True, missing=[VISION], missing_gb=1.9,
                             models=[dict(m, installed=m["role"] != "vision") for m in _AI_OFF["models"]])},
        "fnProps": ["onSet"],
        "setup": "const __LS = {};\n" + _SANDBOX,
        "script": "h.render(); await h.settle(); return { text: h.text().join(' ') };",
    },
    "models_card_all_missing_on": {
        "files": [COMPONENTS, "web/static/models.jsx"], "component": "AiFeaturesCard",
        "props": {"ai": dict(_AI_OFF, enabled=True)},
        "fnProps": ["onSet"],
        "setup": "const __LS = {};\n" + _SANDBOX,
        "script": "h.render(); await h.settle(); return { text: h.text().join(' ') };",
    },
    # The manual with AI off: no phrase the models answer, no Double-Check.
    "manual_say_off": {
        "files": [COMPONENTS, "web/static/manual.jsx"], "component": "UmSay",
        "props": {"phrases": [
            {"text": "set a timer for 10 minutes", "handler": "timer", "path": "fast"},
            {"text": "what's the weather tomorrow", "handler": None, "path": "qa", "needs": "ai"},
            {"text": "yes", "then": True, "needs": "ai"},
            {"text": "are you sure?", "handler": "double_check", "path": "fast", "needs": "ai"}],
            "ctx": {"live": {"features": {"ai": False}}, "doc": {"labels": {"pending": {}}}}},
        "setup": "const __LS = {};\n" + _SANDBOX,
        "script": "h.render(); await h.settle(); return { text: h.text().join(' ') };",
    },
    "manual_say_on": {
        "files": [COMPONENTS, "web/static/manual.jsx"], "component": "UmSay",
        "props": {"phrases": [
            {"text": "set a timer for 10 minutes", "handler": "timer", "path": "fast"},
            {"text": "are you sure?", "handler": "double_check", "path": "fast", "needs": "ai"}],
            "ctx": {"live": {"features": {}}, "doc": {"labels": {"pending": {}}}}},
        "setup": "const __LS = {};\n" + _SANDBOX,
        "script": "h.render(); await h.settle(); return { text: h.text().join(' ') };",
    },
    "manual_everything_off": {
        "files": [COMPONENTS, "web/static/manual.jsx"], "component": "UmThingsToSay",
        "props": {"ctx": {
            "live": {"caps": {"handlers": [
                {"name": "double_check", "display": {"label": "Double-Check"}, "example_phrases": [],
                 "requires_network": "yes", "origin": "core"},
                {"name": "timer", "display": {"label": "Timers"}, "example_phrases": ["set a timer"],
                 "requires_network": "no", "origin": "core"}]},
                "features": {"ai": False, "chat": False}},
            "doc": {"handler_needs_feature": json.loads(
                (REPO_ROOT / "web/static/manual-content.json").read_text(encoding="utf-8"))["handler_needs_feature"],
                "labels": {"net": {}, "say_none": "-", "plugin": {"text": "plugin"}}},
            "phrasesByHandler": {"double_check": ["are you sure?"]}}},
        "setup": "const __LS = {};\n" + _SANDBOX,
        "script": "h.render(); await h.settle(); return { text: h.text().join(' ') };",
    },
    # A send refused with 409 ai-off reads as a plain sentence.
    "chat_send_ai_off": {
        "files": [COMPONENTS, "web/static/chat_markdown.js", "web/static/chat_details.js", "web/static/chat.jsx"],
        "component": "(() => { window.__send = chatSendStream; return ChatAiOff; })()",
        "setup": ("const __LS = {};\n" + _SANDBOX
                  + "apiFetchRaw = () => Promise.resolve({ status: 409, ok: false, statusText: 'Conflict',"
                    " body: {}, headers: { get: (k) => (k === 'X-Domovoi-Refusal' ? 'ai-off' : null) } });\n"),
        "script": """
          h.render();
          try { await h.global('window').__send(1, { content: 'hi' }, () => {}); return { error: null }; }
          catch (e) { return { error: String(e.message) }; }
        """,
    },
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("ai-features") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def _routes(driven, name) -> tuple[set[str], list[str]]:
    nav = driven[name]["nav"]
    return {r["route"] for r in nav}, [r["route"] for r in nav if r["primary"]]


@pytest.mark.parametrize("name", ["on", "older_server", "unanswered"])
def test_chat_is_offered_unless_the_server_says_ai_is_off(driven, name) -> None:
    routes, strip = _routes(driven, name)
    assert "chat" in routes and "chat" in strip


@pytest.mark.parametrize("name", ["off", "remembered_off"])
def test_ai_off_takes_chat_off_every_launcher(driven, name) -> None:
    routes, strip = _routes(driven, name)
    assert "chat" not in routes
    # The default bar without it: home · music · videos.
    assert strip == ["home", "music", "videos"]


def test_the_answer_is_remembered_for_the_next_first_paint(driven) -> None:
    assert driven["off"]["stored"] == "off"
    assert driven["on"]["stored"] == "on"


def test_chat_by_its_address_says_why(driven) -> None:
    out = driven["chat_page_off"]
    assert "chat is off" in out["text"] and "Voice commands still work" in out["text"]
    assert "#settings/models" in out["links"]


def test_the_models_card_says_what_off_means_and_what_on_downloads(driven) -> None:
    out = driven["models_card_off"]
    assert "Off: nothing uses the AI models" in out["before"]
    assert "Voice commands still work" in out["before"] and "Chat is hidden" in out["before"]
    # The size first, then the download.
    assert "downloads about 6.4 GB" in out["asking"]
    assert "about 2.0 GB" in out["asking"] and "about 1.9 GB" in out["asking"]
    assert "download about 6.4 GB and turn on" in out["asking"]
    assert [c["name"] for c in out["sets"]] == ["onSet"]


def test_turning_it_off_is_one_press(driven) -> None:
    out = driven["models_card_on"]
    assert "On: Domovoi answers questions" in out["text"]
    assert "turn off" in out["text"]
    assert [c["name"] for c in out["sets"]] == ["onSet"]


# ─── review round 1 ───────────────────────────────────────────────────────


def test_the_card_names_only_what_is_missing(driven) -> None:
    text = driven["models_card_vision_only"]["text"]
    assert "One model isn’t downloaded yet" in text
    assert "pictures in chat" in text and "Chat can’t read pictures" in text
    assert "question-answering" not in text and "questions and Chat say" not in text
    allm = driven["models_card_all_missing_on"]["text"]
    assert "These models aren’t downloaded yet" in allm
    assert "questions and Chat say the model isn’t installed" in allm
    assert "go to the question model instead" in allm


def test_the_manual_leaves_out_what_the_models_answer(driven) -> None:
    off = driven["manual_say_off"]["text"]
    assert "set a timer for 10 minutes" in off
    assert "weather" not in off and "are you sure" not in off and "yes" not in off
    on = driven["manual_say_on"]["text"]
    assert "are you sure?" in on          # an older server, or AI on
    everything = driven["manual_everything_off"]["text"]
    assert "Timers" in everything and "Double-Check" not in everything


def test_the_manual_marks_every_phrase_the_models_answer() -> None:
    doc = json.loads((REPO_ROOT / "web/static/manual-content.json").read_text(encoding="utf-8"))
    assert doc["handler_needs_feature"]["double_check"] == "ai"
    blocks = list(doc["start"]["blocks"]) + [b for p in doc["pieces"] for b in p.get("blocks", [])]
    phrases = [p for b in blocks if b["type"] == "say" for p in b["phrases"]]
    for i, p in enumerate(phrases):
        if p.get("then"):
            continue
        # A fast phrase whose turn ends at the model is marked too: a letter
        # taken by voice is written up by it (documents plan D7).
        ai = (p.get("path") in ("qa", "llm") or p.get("handler") in ("double_check", "chat_mode")
              or p.get("text") == "take a letter to the landlord")
        assert (p.get("needs") == "ai") is ai or p.get("handler") == "chat_mode", p
        nxt = phrases[i + 1] if i + 1 < len(phrases) else None
        if nxt and nxt.get("then"):
            assert nxt.get("needs") == p.get("needs"), (p, nxt)
    assert any(p["text"] == "are you sure?" and p["needs"] == "ai" for p in phrases)


def test_a_send_refused_for_ai_off_says_so_plainly(driven) -> None:
    assert driven["chat_send_ai_off"]["error"] == "AI chat is turned off on your home server"
