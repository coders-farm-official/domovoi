"""Settings → Models, the role rows (``GET /api/models/active`` and
``models.jsx`` ActiveRow) — the three things found on the Beelink's phone
view on 2026-10-12:

1. A role's model that isn't installed looked installed. Each row now says
   ``installed`` (Ollama's list, compared the way the chat's vision check
   compares) and the page offers Install on the row — the catalog's pull
   job, with its progress — then "ready" once it is there. The chat reads
   its models from the dashboard process's settings, so a model saved
   since the dashboard started needs a restart before the chat uses it
   (``chat_restart_required``); an install doesn't.
2. Speech-to-text read "—" with "tiny" preselected. A tab holding only the
   dashboard cookie (a reload drops the in-memory Bearer) gets the config
   registry without its advanced section (CORE-6), Whisper included; the
   row now takes the Whisper settings the core read at startup from
   ``/v1/admin/hardware``, says "unknown" when nobody says, preselects
   nothing then, and Switch stays off until the pick differs.
3. (layout, styles.css) — covered by the screenshots, not here.

The API half is DB-free and never reaches Ollama (``tags_or_none`` is
stubbed). The page half drives models.jsx in the Node interact harness
(needs ``node``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

VISION = "qwen3-vl:2b-instruct-q4_K_M"

# ─── API: GET /api/models/active ─────────────────────────────────────────────

COMMON = [
    {"name": "ollama_model", "value": "llama3.2:3b", "tier": "reapply"},
    {"name": "ollama_tool_model", "value": "qwen3:8b", "tier": "reapply"},
    {"name": "ollama_vision_model", "value": VISION, "tier": "reapply"},
]
ADVANCED = [
    {"name": "whisper_model", "value": "small.en", "tier": "restart"},
    {"name": "whisper_device", "value": "cpu", "tier": "restart"},
    {"name": "whisper_compute_type", "value": "int8", "tier": "restart"},
]
HARDWARE = {
    "gpus": [],
    "stt": {
        "state": "ok",
        "configured": {"model": "small.en", "device": "cpu", "compute_type": "int8",
                       "compute_type_resolved": "int8"},
        "loaded": {"model": "small.en", "device": "cpu", "compute_type": "int8"},
    },
}
TAGS = [{"name": "llama3.2:3b"}, {"name": "qwen2.5:7b"}, {"name": "qwen3:8b"}]


@pytest.fixture
def core(monkeypatch):
    """Script the core hop, Ollama's list and the Whisper cache. Returns the
    knobs plus the list of core paths asked for."""
    import domovoi.whisper_cache as wc
    import web.backend.api.models as models_api

    state = {
        "fields": COMMON + ADVANCED,
        "hardware": (200, HARDWARE),
        "tags": TAGS,
        "cached": {"small.en": True},
        "asked": [],
    }

    async def _get_admin(path, headers=None, **_kw):
        state["asked"].append(path)
        if path == "/v1/admin/config":
            return 200, {"fields": state["fields"]}
        if path == "/v1/admin/hardware":
            return state["hardware"]
        return 404, {"detail": "not scripted"}

    async def _tags():
        return state["tags"]

    monkeypatch.setattr(models_api, "get_admin", _get_admin)
    monkeypatch.setattr(models_api, "auth_forward_headers", lambda _r: {})
    monkeypatch.setattr(models_api.ollama_client, "tags_or_none", _tags)
    monkeypatch.setattr(wc, "whisper_model_cached", lambda m: state["cached"].get(m, False))
    # The dashboard process's own copy (what the chat reads): the defaults
    # the core has too, unless a test changes them.
    monkeypatch.setattr(models_api.core_settings, "ollama_model", "llama3.2:3b")
    monkeypatch.setattr(models_api.core_settings, "ollama_vision_model", VISION)
    return state


async def _roles() -> dict[str, dict]:
    import web.backend.api.models as models_api

    return {r["role"]: r for r in (await models_api.get_active(None))["roles"]}


@pytest.mark.asyncio
async def test_a_bearer_read_reports_the_live_config(core) -> None:
    roles = await _roles()
    stt = roles["stt"]
    assert (stt["model"], stt["device"], stt["compute_type"]) == ("small.en", "cpu", "int8")
    assert stt["model_source"] == "config" and stt["tier"] == "restart"
    assert all(r["model_source"] == "config" for r in roles.values())
    # The live registry answered: the core's hardware wasn't asked.
    assert core["asked"] == ["/v1/admin/config"]


@pytest.mark.asyncio
async def test_a_cookie_only_read_takes_whisper_from_what_the_core_loaded(core) -> None:
    """The Beelink's "—": CORE-6 leaves the advanced section (whisper_*)
    out for a caller with only the cookie."""
    core["fields"] = COMMON
    stt = (await _roles())["stt"]
    assert stt["model"] == "small.en"
    assert (stt["device"], stt["compute_type"]) == ("cpu", "int8")
    assert stt["model_source"] == "startup"
    # The tier comes from the schema when the read left the field out.
    assert stt["tier"] == "restart"
    assert stt["installed"] is True
    assert "/v1/admin/hardware" in core["asked"]


@pytest.mark.asyncio
@pytest.mark.parametrize("hardware", [
    (502, None),
    (200, {"gpus": []}),
    (200, {"stt": {"state": "not_loaded", "configured": {"model": None}}}),
])
async def test_the_stt_model_is_unknown_when_nobody_says(core, hardware) -> None:
    core["fields"] = COMMON
    core["hardware"] = hardware
    stt = (await _roles())["stt"]
    assert stt["model"] is None and stt["model_source"] is None
    assert stt["installed"] is None
    assert stt["device"] is None and stt["compute_type"] is None


@pytest.mark.asyncio
async def test_each_ollama_role_says_whether_its_model_is_installed(core) -> None:
    roles = await _roles()
    assert roles["qa"]["installed"] is True
    assert roles["tool"]["installed"] is True
    assert roles["vision"]["installed"] is False     # the Beelink's vision row
    # The way Ollama names them: a bare name is :latest, case aside.
    core["fields"] = [{"name": "ollama_model", "value": "Llama3.2", "tier": "reapply"}]
    core["tags"] = [{"name": "llama3.2:latest"}]
    assert (await _roles())["qa"]["installed"] is True


@pytest.mark.asyncio
async def test_installed_is_unknown_when_ollama_cant_be_asked(core) -> None:
    core["tags"] = None
    roles = await _roles()
    assert roles["qa"]["installed"] is None and roles["vision"]["installed"] is None


@pytest.mark.asyncio
async def test_the_stt_row_says_whether_whisper_is_on_disk(core) -> None:
    core["cached"] = {}
    assert (await _roles())["stt"]["installed"] is False
    core["cached"] = {"small.en": None}               # a name the cache module doesn't know
    assert (await _roles())["stt"]["installed"] is None


@pytest.mark.asyncio
async def test_the_chat_roles_say_when_the_chat_needs_a_restart(core, monkeypatch) -> None:
    import web.backend.api.models as models_api

    roles = await _roles()
    assert roles["vision"]["chat_model"] == VISION
    assert roles["vision"]["chat_restart_required"] is False
    assert roles["qa"]["chat_restart_required"] is False
    assert "chat_model" not in roles["tool"] and "chat_model" not in roles["stt"]

    # A vision model saved through the core since the dashboard started:
    # the chat keeps the one it read at startup.
    monkeypatch.setattr(models_api.core_settings, "ollama_vision_model", "qwen2.5vl:7b")
    vision = (await _roles())["vision"]
    assert vision["chat_model"] == "qwen2.5vl:7b"
    assert vision["chat_restart_required"] is True

    # One model under two spellings is the same model.
    monkeypatch.setattr(models_api.core_settings, "ollama_vision_model", VISION.upper())
    assert (await _roles())["vision"]["chat_restart_required"] is False


@pytest.mark.asyncio
async def test_a_refused_config_read_is_passed_through(core, monkeypatch) -> None:
    import web.backend.api.models as models_api

    async def _refused(path, headers=None, **_kw):
        return 401, {"detail": "admin session required"}

    monkeypatch.setattr(models_api, "get_admin", _refused)
    res = await models_api.get_active(None)
    assert res.status_code == 401


# ─── Page: models.jsx ActiveRow ──────────────────────────────────────────────

INSTALLED = [
    {"name": "llama3.2:3b", "size_bytes": 2_000_000_000, "quant": "Q4_K_M", "loaded": True},
    {"name": "qwen2.5:7b", "size_bytes": 4_700_000_000, "quant": "Q4_K_M", "loaded": False},
    {"name": "qwen3:8b", "size_bytes": 5_200_000_000, "quant": "Q4_K_M", "loaded": False},
]
WHISPER = [
    {"name": "tiny", "compute": "auto", "accuracy": "rough", "est_vram_gb": 0.5, "cached": True},
    {"name": "small.en", "compute": "auto", "accuracy": "good", "est_vram_gb": 1, "cached": True},
    {"name": "medium.en", "compute": "auto", "accuracy": "better", "est_vram_gb": 2, "cached": False},
]
CATALOG = {"ollama": [{"name": VISION, "role": "vision", "desc": "small vision", "est_vram_gb": 3.3}],
           "whisper": WHISPER}


def _role_rows(**over) -> list[dict]:
    rows = {
        "qa": {"role": "qa", "field": "ollama_model", "model": "llama3.2:3b", "tier": "reapply",
               "model_source": "config", "installed": True, "chat_model": "llama3.2:3b",
               "chat_restart_required": False},
        "tool": {"role": "tool", "field": "ollama_tool_model", "model": "qwen3:8b", "tier": "reapply",
                 "model_source": "config", "installed": True},
        "vision": {"role": "vision", "field": "ollama_vision_model", "model": VISION, "tier": "reapply",
                   "model_source": "config", "installed": False, "chat_model": VISION,
                   "chat_restart_required": False},
        "stt": {"role": "stt", "field": "whisper_model", "model": "small.en", "tier": "restart",
                "model_source": "startup", "device": "cpu", "compute_type": "int8", "installed": True},
    }
    for role, patch in over.items():
        rows[role] = {**rows[role], **patch}
    return list(rows.values())


# Row helpers: a row is the .model-role-row around a role's label; the
# state of a row is its select (value + options), buttons and texts.
HELPERS = r"""
const row = (label) => {
  const lab = h.find((el) => el.type === 'div' && el.text === label);
  if (!lab) return null;
  return h.ancestors(lab).find((a) => String((a.props || {}).className || '').includes('model-role-row')) || null;
};
const inRow = (label) => { const r = row(label); return r ? h.findAll((el) => h.ancestors(el).includes(r)) : []; };
const state = (label) => {
  const els = inRow(label);
  const sel = els.find((el) => el.type === 'select');
  return {
    value: sel ? sel.props.value : null,
    opts: els.filter((el) => el.type === 'option')
      .map((o) => ({ value: o.props.value, disabled: !!o.props.disabled, text: o.text })),
    btns: els.filter((el) => el.type === 'button')
      .map((b) => ({ text: b.text, disabled: !!b.props.disabled, title: b.props.title || null })),
    texts: els.map((el) => el.text).filter(Boolean),
    bars: els.filter((el) => el.type === 'div' && el.props.style && el.props.style.transition).length,
  };
};
const pick = (label, value) => h.change((el) => el.type === 'select' && h.ancestors(el).includes(row(label)), value);
h.render(); await h.settle(); h.rerender();
"""


def _scenario(roles: list[dict], *, jobs: list[dict] | None = None, internet: str = "",
              installed: list[dict] | None = None, hardware: dict | None = None,
              script: str) -> dict:
    return {
        "files": ["web/static/components.jsx", "web/static/models.jsx"],
        "component": "ModelsPanel",
        "api": {
            "GET /api/config": {"bot_name": "Domovoi", "internet_access": internet},
            "GET /api/models/installed": {"installed": INSTALLED if installed is None else installed,
                                          "ollama_reachable": True},
            "GET /api/models/catalog": CATALOG,
            "GET /api/models/active": {"roles": roles},
            "GET /api/models/hardware": hardware or {"gpus": [], "ram": None},
            "GET /api/models/jobs": {"jobs": jobs or []},
            "POST /api/models/pull": {"job": {"id": 9, "model": VISION, "status": "pending"},
                                      "attached": False},
            "POST /api/models/active": {"restart_required": [], "rejected": {}},
        },
        "script": HELPERS + script,
    }


ALL_STATES = ("return { qa: state('Q&A'), tool: state('Tool routing'), vision: state('Vision'),"
              " stt: state('Speech-to-text'), calls: h.calls };")

SCENARIOS = {
    "known": _scenario(_role_rows(), script=(
        "const s0 = { qa: state('Q&A'), tool: state('Tool routing'), vision: state('Vision'),"
        "  stt: state('Speech-to-text') };"
        " await pick('Speech-to-text', 'tiny'); const s1 = state('Speech-to-text');"
        " await pick('Speech-to-text', 'small.en'); const s2 = state('Speech-to-text');"
        " await pick('Q&A', 'qwen2.5:7b'); const q1 = state('Q&A');"
        " await h.click((el) => el.type === 'button' && el.text === 'Install'"
        "   && h.ancestors(el).includes(row('Vision')));"
        " return { s0, s1, s2, q1, calls: h.calls };")),
    "unknown_stt": _scenario(
        _role_rows(stt={"model": None, "model_source": None, "device": None,
                        "compute_type": None, "installed": None}),
        script=("const s0 = state('Speech-to-text');"
                " await pick('Speech-to-text', 'tiny'); const s1 = state('Speech-to-text');"
                " return { s0, s1 };")),
    "pulling": _scenario(_role_rows(), jobs=[
        {"id": 3, "model": VISION, "status": "running", "pct": 40, "status_text": "pulling 3f5a"},
    ], script=ALL_STATES),
    "failed": _scenario(_role_rows(), jobs=[
        {"id": 4, "model": VISION, "status": "failed", "pct": 10, "error": "pull model manifest: file does not exist"},
    ], script=ALL_STATES),
    "ready": _scenario(_role_rows(vision={"installed": True}), jobs=[
        {"id": 3, "model": VISION, "status": "done", "pct": 100},
    ], script=ALL_STATES),
    "chat_restart": _scenario(
        _role_rows(vision={"installed": True, "chat_model": "qwen2.5vl:7b", "chat_restart_required": True}),
        script=ALL_STATES),
    "internet_off": _scenario(_role_rows(), internet="never", script=ALL_STATES),
    "stt_not_on_disk": _scenario(_role_rows(stt={"model": "medium.en", "installed": False}),
                                 script=ALL_STATES),
    # This process's cache check says no, but the core is transcribing
    # with it (its own disk): no false alarm.
    "stt_loaded": _scenario(
        _role_rows(stt={"model": "small.en", "installed": False}),
        hardware={"gpus": [], "ram": None,
                  "stt": {"state": "ok", "loaded": {"model": "small.en", "device": "cpu",
                                                     "compute_type": "int8"}}},
        script=ALL_STATES),
    "ollama_alias": _scenario(
        _role_rows(qa={"model": "llama3.2"}),
        installed=[{"name": "llama3.2:latest", "size_bytes": 2_000_000_000, "loaded": False},
                   {"name": "qwen3:8b", "size_bytes": 5_200_000_000, "loaded": False}],
        script=("const s0 = state('Q&A'); await pick('Q&A', 'qwen3:8b'); const s1 = state('Q&A');"
                " return { s0, s1 };")),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    scen = tmp_path_factory.mktemp("roles") / "scenarios.json"
    scen.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{scen}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def _switch(st: dict) -> dict:
    return next(b for b in st["btns"] if b["text"] == "Switch")


def _install(st: dict) -> dict | None:
    return next((b for b in st["btns"] if b["text"] == "Install"), None)


def test_the_current_stt_model_is_preselected_and_switch_waits_for_a_change(driven) -> None:
    k = driven["known"]
    stt = k["s0"]["stt"]
    assert "small.en" in stt["texts"]
    assert stt["value"] == "small.en"                 # not the first option ("tiny")
    assert _switch(stt)["disabled"]
    assert not _switch(k["s1"])["disabled"]           # picked tiny: now it would change something
    assert _switch(k["s2"])["disabled"]               # back to the current one: nothing to do
    # The same rule on every row.
    for role in ("qa", "tool", "vision"):
        assert _switch(k["s0"][role])["disabled"], role
    assert not _switch(k["q1"])["disabled"]


def test_an_unknown_stt_model_says_so_and_preselects_nothing(driven) -> None:
    u = driven["unknown_stt"]
    assert "unknown" in u["s0"]["texts"] and "—" not in u["s0"]["texts"]
    assert u["s0"]["value"] == ""
    placeholder = u["s0"]["opts"][0]
    assert placeholder["value"] == "" and placeholder["disabled"]
    assert "tiny" in [o["value"] for o in u["s0"]["opts"]]
    assert _switch(u["s0"])["disabled"]
    assert not _switch(u["s1"])["disabled"]           # a deliberate pick


def test_a_role_model_that_isnt_installed_says_so_with_install(driven) -> None:
    k = driven["known"]
    vision = k["s0"]["vision"]
    assert VISION in vision["texts"]
    assert "not installed" in vision["texts"]
    install = _install(vision)
    assert install and not install["disabled"]
    # Installed rows offer no Install and read "ready".
    for role in ("qa", "tool"):
        assert _install(k["s0"][role]) is None
        assert "ready" in k["s0"][role]["texts"]
    # The row's Install is the catalog's pull.
    pulls = [c for c in k["calls"] if c["method"] == "POST" and c["path"] == "/api/models/pull"]
    assert pulls == [{"method": "POST", "path": "/api/models/pull", "body": {"model": VISION}}]


def test_a_running_install_shows_its_progress_on_the_row(driven) -> None:
    vision = driven["pulling"]["vision"]
    assert "installing 40%" in vision["texts"]
    assert "pulling 3f5a" in vision["texts"]
    assert vision["bars"] >= 1
    assert _install(vision) is None and "not installed" not in vision["texts"]


def test_a_failed_install_says_why_and_offers_install_again(driven) -> None:
    vision = driven["failed"]["vision"]
    assert any("install failed" in t and "file does not exist" in t for t in vision["texts"])
    assert _install(vision) is not None


def test_an_installed_role_model_reads_ready(driven) -> None:
    vision = driven["ready"]["vision"]
    assert "ready" in vision["texts"]
    assert "not installed" not in vision["texts"] and _install(vision) is None
    assert "restart for chat" not in vision["texts"]


def test_the_row_says_when_the_chat_needs_a_restart(driven) -> None:
    vision = driven["chat_restart"]["vision"]
    assert "restart for chat" in vision["texts"]
    assert "installed" in vision["texts"] and "ready" not in vision["texts"]


def test_install_on_the_row_is_greyed_with_the_internet_off(driven) -> None:
    install = _install(driven["internet_off"]["vision"])
    assert install["disabled"] and install["title"] == "needs internet · Settings → Internet"


def test_a_whisper_model_not_on_disk_says_so(driven) -> None:
    stt = driven["stt_not_on_disk"]["stt"]
    assert "not downloaded" in stt["texts"]
    assert _install(stt) is None                      # Whisper downloads when it loads
    assert "not downloaded" not in driven["stt_loaded"]["stt"]["texts"]


def test_two_spellings_of_one_ollama_model_are_one_choice(driven) -> None:
    a = driven["ollama_alias"]
    # "llama3.2" (the setting) and "llama3.2:latest" (Ollama's list) are one
    # model: listed once, under the current spelling, and nothing to switch.
    assert [o["value"] for o in a["s0"]["opts"]] == ["llama3.2", "qwen3:8b"]
    assert a["s0"]["value"] == "llama3.2"
    assert _switch(a["s0"])["disabled"]
    assert not _switch(a["s1"])["disabled"]
