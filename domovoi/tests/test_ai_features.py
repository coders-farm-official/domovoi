"""The household's "AI features" switch (domovoi/ai_features.py).

Owner direction (2026-10-21): "AI chat is optional". With the switch off
nothing in Domovoi asks a language model; voice commands keep working on
their fixed phrases, speech recognition stays. Pinned here, DB-free:

* where the answer comes from (an override, the environment, ``.env``, on)
  and that both processes see a dashboard save at once;
* the setting is in the editable registry, in the Models group, and a value
  pinned in the environment is refused from the dashboard;
* every background model user stops: the warm-up, memory extraction, the
  news briefing summary, the intro classifier, chat mode;
* a plugin's request to Ollama through ``sdk.http`` (and the web plugin
  host's ``http()``) is refused with ``AITurnedOff``, while ``/api/tags`` and
  every other host stay open; ``sdk.core_config.ai_features`` says so;
* the download sizes the installers and Settings show match the models
  the pin manifest names.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from domovoi import ai_features, config_env_writer, llm_warmup
from domovoi.config import Settings, settings
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", path)
    monkeypatch.delenv("AI_FEATURES", raising=False)
    monkeypatch.delenv("ai_features", raising=False)
    return path


# ─── the answer ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,want", [
    (None, True), ("", True), ("true", True), ("on", True), ("yes", True), ("maybe", True),
    ("false", False), ("FALSE", False), (" off ", False), ("no", False), ("0", False), (False, False),
])
def test_only_a_clear_no_turns_it_off(raw, want) -> None:
    assert ai_features.parse(raw) is want


def test_on_by_default_for_an_install_from_before_the_switch(env_file) -> None:
    assert not env_file.exists()
    assert ai_features.enabled() is True
    assert Settings.model_fields["ai_features"].default is True


def test_a_dashboard_save_is_seen_at_once(env_file) -> None:
    env_file.write_text("AI_FEATURES=false\n", encoding="utf-8")
    assert ai_features.enabled() is False and ai_features.turned_off()
    # The writer the dashboard uses; the mtime+size cache sees the change.
    config_env_writer.write_env_values({"ai_features": True})
    assert ai_features.enabled() is True
    assert ai_features.read() is True
    assert not ai_features.locked()


def test_the_environment_wins_and_locks_it(env_file, monkeypatch) -> None:
    env_file.write_text("AI_FEATURES=true\n", encoding="utf-8")
    monkeypatch.setenv("AI_FEATURES", "false")
    assert ai_features.enabled() is False
    assert ai_features.locked()


def test_an_override_wins_and_nests(env_file) -> None:
    with ai_features.override(False):
        assert ai_features.turned_off()
        with ai_features.override(True):
            assert ai_features.enabled()
        with ai_features.override(None):
            assert ai_features.turned_off()
    assert ai_features.enabled()


def test_the_setting_is_in_the_models_group_and_applies_live() -> None:
    spec = FIELD_BY_NAME["ai_features"]
    assert spec.group == "Models" and spec.type == "bool" and spec.tier == "reapply"
    assert spec.section == "common"
    # It says what turning it off does and what still works, in plain words.
    for words in ("Off", "Voice commands", "still work", "speech recognition", "Chat is hidden"):
        assert words in spec.help
    assert coerce_and_validate(spec, "false") is False


def test_the_reapply_hook_takes_the_saved_value_and_warms_up_on_on(env_file, monkeypatch) -> None:
    from domovoi import reapply
    from domovoi.main import _register_core_reapply_hooks

    _register_core_reapply_hooks()
    warmed: list[str] = []
    monkeypatch.setattr(llm_warmup, "schedule_warm_up", lambda reason: warmed.append(reason))
    monkeypatch.setattr(settings, "ai_features", True)
    # A save whose .env write failed: the live value follows .env, not the
    # value the endpoint set first.
    env_file.write_text("AI_FEATURES=false\n", encoding="utf-8")
    reapply.run_for(["ai_features"])
    assert settings.ai_features is False and warmed == []
    env_file.write_text("AI_FEATURES=true\n", encoding="utf-8")
    reapply.run_for(["ai_features"])
    assert settings.ai_features is True and warmed


# ─── background model users ───────────────────────────────────────────────


def test_no_warm_up_with_ai_off(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(settings, "ollama_warmup", True)
    with ai_features.override(False):
        assert asyncio.run(llm_warmup.warm_up_models("boot")) == []
        assert llm_warmup.schedule_warm_up("boot") is None


@pytest.mark.asyncio
async def test_memory_extraction_skips_its_pass(monkeypatch) -> None:
    from domovoi.workers import memory_extractor

    def boom():
        raise AssertionError("no database read, no model call with AI off")

    monkeypatch.setattr(memory_extractor, "session_scope", boom)
    with ai_features.override(False):
        assert await memory_extractor.MemoryExtractor().tick() == 0


@pytest.mark.asyncio
async def test_the_news_briefing_is_not_summarised(monkeypatch) -> None:
    from domovoi import news_service

    class _NoSession:
        async def execute(self, *a, **k):
            raise AssertionError("not read with AI off")

    with ai_features.override(False):
        assert await news_service.summarize_person_briefing(_NoSession(), 1) is None


@pytest.mark.asyncio
async def test_the_intro_classifier_says_cant_tell_without_asking(monkeypatch) -> None:
    from domovoi import voice_classifier

    def boom():
        raise AssertionError("asked the model with AI off")

    monkeypatch.setattr(voice_classifier, "get_ollama_client", boom)
    with ai_features.override(False):
        score = await voice_classifier.classify_third_party_match(
            name="sam", intro_text="this is my friend sam", candidate_text="hello there",
        )
    assert score == 0.5


@pytest.mark.asyncio
async def test_chat_mode_needs_the_ai_features() -> None:
    from domovoi.handlers.chat_mode import ChatModeHandler
    from domovoi.models import Context

    h = ChatModeHandler()
    with ai_features.override(False):
        r = await h._enter(Context(room_id="kitchen"), None)
        assert "AI features" in r.text and "turned off" in r.text
        assert h.example_phrases == ()


def test_the_fact_check_phrase_is_not_suggested_with_ai_off() -> None:
    from domovoi.handlers.double_check import DoubleCheckHandler

    h = DoubleCheckHandler()
    assert h.example_phrases == ("are you sure?",)
    with ai_features.override(False):
        assert h.example_phrases == ()


# ─── plugins: sdk.http and the web plugin host ────────────────────────────


@pytest.mark.parametrize("url,model", [
    ("http://127.0.0.1:11434/api/generate", True),
    ("http://localhost:11434/api/chat", True),
    ("http://nas.local:11434/api/embed", True),
    ("http://127.0.0.1:11434/api/pull", True),
    ("http://127.0.0.1:11434/api/tags", False),
    ("http://127.0.0.1:11434/api/ps", False),
    ("http://127.0.0.1:11434/api/version", False),
    # The review's proof (round 1): Ollama's OpenAI-compatible paths and an
    # odd spelling of a native one. Denied by host and port, not by path.
    ("http://localhost:11434/v1/chat/completions", True),
    ("http://localhost:11434/v1/completions", True),
    ("http://localhost:11434/v1/embeddings", True),
    ("http://127.0.0.1:11434//api/generate", True),
    ("http://127.0.0.1:11434/api/show", True),
    ("http://127.0.0.1:11434/api/tags/../generate", True),
    ("http://127.0.0.1:6370/api/generate", False),
    ("https://example.org/api/chat", False),
])
def test_what_counts_as_a_model_request(env_file, url, model) -> None:
    assert ai_features.is_model_request(url) is model


def test_the_configured_ollama_counts_too(env_file, monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    env_file.write_text("OLLAMA_URL=http://192.168.1.20:8080\n", encoding="utf-8")
    assert ai_features.is_model_request("http://192.168.1.20:8080/api/generate")
    assert not ai_features.is_model_request("http://192.168.1.20:8081/api/generate")
    assert ai_features.is_model_request("http://192.168.1.20:8080/v1/chat/completions")
    assert not ai_features.is_model_request("http://192.168.1.20:8080/api/tags")
    env_file.write_text("OLLAMA_URL=http://localhost:8080\n", encoding="utf-8")
    assert ai_features.is_model_request("http://127.0.0.1:8080/api/chat")


def _answer(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"response": "a summary"})


@pytest.mark.asyncio
async def test_sdk_http_refuses_a_model_request_with_ai_off(env_file) -> None:
    from domovoi.sdk.http import HttpFactory

    url = "http://127.0.0.1:11434/api/generate"
    async with HttpFactory().client(transport=httpx.MockTransport(_answer)) as c:
        assert (await c.post(url, json={})).status_code == 200
        with ai_features.override(False):
            with pytest.raises(ai_features.AITurnedOff) as e:
                await c.post(url, json={})
            assert "AI features are turned off" in str(e.value)
            # Also an httpx.ConnectError: a plugin that only handles
            # httpx errors sees "the model isn't reachable", not a 500.
            assert isinstance(e.value, httpx.ConnectError)
            assert isinstance(e.value, ai_features.AITurnedOffConnectError)
            # Asking what is installed, and every other host, stay open.
            assert (await c.get("http://127.0.0.1:11434/api/tags")).status_code == 200
            assert (await c.get("http://127.0.0.1:6370/v1/health")).status_code == 200


@pytest.mark.asyncio
async def test_a_plugins_own_request_hooks_still_run(env_file) -> None:
    from domovoi.sdk.http import HttpFactory

    seen: list[str] = []

    async def mine(request):
        seen.append(str(request.url))

    async with HttpFactory().client(transport=httpx.MockTransport(_answer),
                                    event_hooks={"request": [mine]}) as c:
        await c.get("http://127.0.0.1:11434/api/tags")
    assert seen == ["http://127.0.0.1:11434/api/tags"]


@pytest.mark.asyncio
async def test_the_web_plugin_hosts_http_refuses_too(env_file) -> None:
    from web.backend.plugin_host import WebPluginContext

    ctx = WebPluginContext("kiwix")
    async with ctx.http(transport=httpx.MockTransport(_answer)) as c:
        assert (await c.post("http://localhost:11434/api/generate", json={})).status_code == 200
        with ai_features.override(False):
            with pytest.raises(ai_features.AITurnedOff):
                await c.post("http://localhost:11434/api/generate", json={})


def test_core_config_tells_plugins(env_file) -> None:
    from domovoi.sdk import API_VERSION
    from domovoi.sdk.coreconfig import CoreConfigView

    view = CoreConfigView()
    assert view.ai_features is True
    with ai_features.override(False):
        assert view.ai_features is False
    assert tuple(int(x) for x in API_VERSION.split(".")) >= (1, 7, 0)


# ─── the download sizes ───────────────────────────────────────────────────


def _manifest() -> dict[str, str]:
    out = {}
    for line in (REPO / "scripts/linux/provision-manifest.conf").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(tier_\w+?)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_every_installer_model_has_a_size_and_the_totals_add_up() -> None:
    m = _manifest()
    for tier in ("floor", "standard"):
        models = {m[f"tier_{tier}_{role}_model"] for role in ("qa", "tool", "vision")}
        for name in models:
            assert name in ai_features.MODEL_DOWNLOAD_GB, name
        total = sum(ai_features.MODEL_DOWNLOAD_GB[n] for n in models)
        assert round(total, 1) == ai_features.TIER_DOWNLOAD_GB[tier]


def test_the_installers_say_the_same_sizes() -> None:
    floor = f"{ai_features.TIER_DOWNLOAD_GB['floor']} GB"
    standard = f"{ai_features.TIER_DOWNLOAD_GB['standard']} GB"
    for path in ("scripts/linux/provision.sh", "windows/installer/DomovoiSetup.iss"):
        text = (REPO / path).read_text(encoding="utf-8")
        assert floor in text and standard in text, path
