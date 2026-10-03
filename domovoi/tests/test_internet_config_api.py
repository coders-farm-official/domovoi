"""The internet answer over HTTP: ``GET /v1/admin/config`` row flags,
``POST /v1/admin/config`` with ``internet_access`` / ``follow_internet``,
``GET /v1/admin/internet``, ``GET /v1/connectivity``, and the web side
(``GET /api/config``'s ``internet_access``, the ``/api/config/internet``
proxy, ``follow_internet`` on the PATCH).

DB-free: the auth primitives are faked (``install_fake_db``), the intent
log write is stubbed, ``.env`` lives in tmp, and the live settings
singleton is only changed through ``monkeypatch``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import config as config_mod
from domovoi import config_env_writer, egress, internet_profile
from domovoi.config import PROFILE_DEFAULTS, Settings, settings
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import COOKIE, bearer, install_fake_db
from web.backend.main import app as web_app

TOKEN = "admin-token"
NAMES = [pd.name for pd in PROFILE_DEFAULTS]


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                       base_url="http://test")


def _web() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app, raise_app_exceptions=False),
                       base_url="http://test", headers={"X-Requested-With": "domovoi-tests"})


@pytest.fixture
def box(tmp_path, monkeypatch):
    """An unanswered box: profile fields at their defaults, .env in tmp,
    an admin session, no DB."""
    import importlib.util

    import domovoi.db.repositories as repos
    import domovoi.main as main_mod

    env = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env)
    for name in [*NAMES, "internet_access", "acoustid_api_key", "HF_HUB_OFFLINE"]:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr(settings, "internet_access", "")
    monkeypatch.setattr(settings, "acoustid_api_key", "")
    for name in NAMES:
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)
    monkeypatch.setattr(config_mod, "HF_HUB_OFFLINE_SET_BY_PROFILE", False)
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n, *a, **k: object() if n == "shazamio" else real(n, *a, **k))
    install_fake_db(monkeypatch, admin=True, sessions={TOKEN})
    core_app.state.config_apply_lock = asyncio.Lock()

    @asynccontextmanager
    async def fake_scope():
        yield object()

    class FakeLog:
        def __init__(self, _s):
            pass

        async def log(self, **kw):
            logged.append(kw.get("transcript"))

    logged: list[str] = []
    monkeypatch.setattr(main_mod, "session_scope", fake_scope)
    monkeypatch.setattr(repos, "IntentLogRepository", FakeLog)
    return {"env": env, "logged": logged}


async def _post(body: dict[str, Any]) -> dict[str, Any]:
    async with _core() as c:
        r = await c.post("/v1/admin/config", json=body, headers=bearer(TOKEN))
    assert r.status_code == 200, r.text
    return r.json()


# ─── GET /v1/admin/config ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_rows_carry_the_internet_flags(box, monkeypatch) -> None:
    monkeypatch.setenv("BOT_NAME", "Jeeves")
    async with _core() as c:
        r = await c.get("/v1/admin/config", headers=bearer(TOKEN))
    assert r.status_code == 200
    rows = {f["name"]: f for f in r.json()["fields"]}
    for row in rows.values():
        for key in ("choice_labels", "needs_internet", "needs_internet_choices",
                    "internet_profile", "follows_internet", "set_in_environment"):
            assert key in row, (row["name"], key)
    ia = rows["internet_access"]
    assert ia["group"] == "Internet" and ia["tier"] == "reapply"
    assert ia["choices"] == ["always", "sometimes", "never"]
    assert ia["choice_labels"]["never"] == "No, keep everything in the house"
    assert ia["value"] == ""
    assert rows["tts_engine"]["needs_internet_choices"] == ["edge"]
    for name in ("news_auto_fetch", "news_location", "news_enabled", "music_alias_fetch_enabled", "searxng_url"):
        assert rows[name]["needs_internet"] is True, name
    assert rows["bot_name"]["needs_internet"] is False
    assert rows["bot_name"]["set_in_environment"] is True
    assert rows["news_enabled"]["internet_profile"] is True
    assert rows["news_enabled"]["follows_internet"] is False      # unanswered: nothing follows
    assert rows["bot_name"]["internet_profile"] is False

    monkeypatch.setattr(settings, "internet_access", "always")
    box["env"].write_text("NEWS_ENABLED=true\n", encoding="utf-8")
    async with _core() as c:
        rows = {f["name"]: f for f in (await c.get("/v1/admin/config", headers=bearer(TOKEN))).json()["fields"]}
    assert rows["music_alias_fetch_enabled"]["follows_internet"] is True
    assert rows["news_enabled"]["follows_internet"] is False       # pinned in .env


# ─── POST internet_access ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_saving_an_answer_applies_live_followers_and_lists_restart_ones(box) -> None:
    body = await _post({"changes": {"internet_access": "always"}})
    assert body["rejected"] == {}
    assert "internet_access" in body["applied"]
    assert "music_alias_fetch_enabled" in body["applied"]
    assert settings.music_alias_fetch_enabled is True
    assert settings.internet_access == "always"
    assert set(body["restart_required"]) == {"podcast_feed_poller_enabled", "seed_voice_catalog"}
    assert body["followed"] == []
    assert "INTERNET_ACCESS=always" in box["env"].read_text(encoding="utf-8")
    # both processes read it from .env
    assert egress.read_policy(env_path=box["env"], environ={}) == "always"
    assert box["logged"] == ["[ui] update config internet_access"]


@pytest.mark.asyncio
async def test_crossing_never_lists_internet_access(box) -> None:
    await _post({"changes": {"internet_access": "sometimes"}})
    body = await _post({"changes": {"internet_access": "never"}})
    assert "internet_access" in body["restart_required"]
    assert body["normalized"]["internet_access"] == internet_profile.HF_RESTART_NOTE
    assert "Hugging Face" not in body["normalized"]["internet_access"]     # owner words
    assert settings.music_alias_fetch_enabled is False
    assert {"news_enabled", "library_enricher_enabled", "seed_voice_catalog"} <= set(body["restart_required"])


@pytest.mark.asyncio
async def test_only_the_three_answers_are_accepted(box) -> None:
    body = await _post({"changes": {"internet_access": "yes"}})
    assert "internet_access" in body["rejected"]
    body = await _post({"changes": {"internet_access": ""}})
    assert "internet_access" in body["rejected"]
    assert settings.internet_access == ""
    assert not box["env"].exists()


@pytest.mark.asyncio
async def test_an_answer_pinned_in_the_environment_is_refused(box, monkeypatch) -> None:
    monkeypatch.setenv("INTERNET_ACCESS", "sometimes")
    body = await _post({"changes": {"internet_access": "never", "bot_name": "Jeeves"}})
    assert body["rejected"]["internet_access"] == (
        "set in the server's environment (INTERNET_ACCESS); change it there"
    )
    assert settings.internet_access == ""
    assert "INTERNET_ACCESS" not in box["env"].read_text(encoding="utf-8")
    assert "bot_name" in body["restart_required"]                  # the rest still saved


@pytest.mark.asyncio
async def test_a_persist_failure_reverts_the_answer(box, monkeypatch) -> None:
    real = config_env_writer.write_env_values

    def flaky(changes, env_path=None):
        if "internet_access" in changes:
            raise OSError("disk full")
        return real(changes, env_path)

    monkeypatch.setattr(config_env_writer, "write_env_values", flaky)
    body = await _post({"changes": {"internet_access": "never", "music_alias_fetch_enabled": True}})
    assert body["rejected"]["internet_access"].startswith(".env write failed")
    assert "internet_access" not in body["applied"]
    assert settings.internet_access == ""                           # reverted
    assert settings.music_alias_fetch_enabled is True               # the other change stands
    assert "internet_access" not in body["restart_required"]


@pytest.mark.asyncio
async def test_a_follower_pinned_in_the_same_write_stays_pinned(box) -> None:
    body = await _post({"changes": {"internet_access": "always", "music_alias_fetch_enabled": False}})
    assert body["rejected"] == {}
    assert settings.music_alias_fetch_enabled is False


# ─── follow_internet ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_follow_internet_comments_the_line_and_rederives(box, monkeypatch) -> None:
    await _post({"changes": {"internet_access": "always"}})
    await _post({"changes": {"music_alias_fetch_enabled": False}})
    assert settings.music_alias_fetch_enabled is False
    monkeypatch.setenv("NEWS_ENABLED", "true")
    body = await _post({
        "changes": {},
        "follow_internet": ["music_alias_fetch_enabled", "news_enabled", "bot_name"],
    })
    assert body["followed"] == ["music_alias_fetch_enabled"]
    assert body["rejected"]["news_enabled"].startswith("set in the server's environment")
    assert body["rejected"]["bot_name"] == "not a setting that follows the internet answer"
    assert settings.music_alias_fetch_enabled is True
    text = box["env"].read_text(encoding="utf-8")
    assert "# MUSIC_ALIAS_FETCH_ENABLED=false  (follows INTERNET_ACCESS since" in text
    assert box["logged"][-1] == "[ui] update config music_alias_fetch_enabled"


@pytest.mark.asyncio
async def test_follow_internet_is_bounded(box) -> None:
    async with _core() as c:
        r = await c.post("/v1/admin/config", headers=bearer(TOKEN),
                         json={"changes": {}, "follow_internet": [f"x{i}" for i in range(17)]})
    assert r.status_code == 422


# ─── GET /v1/admin/internet ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_internet_shape(box, monkeypatch) -> None:
    class Probe:
        online, reason, target = True, "connected", "1.1.1.1:443"
        last_checked_at = last_online_at = None

    monkeypatch.setattr(core_app.state, "probe", Probe(), raising=False)
    async with _core() as c:
        r = await c.get("/v1/admin/internet")
        assert r.status_code == 401                                 # gated (claimed box, no credential)
        c.cookies.set(COOKIE, TOKEN)
        r = await c.get("/v1/admin/internet")                       # the cookie renders it
    assert r.status_code == 200, r.text
    doc = r.json()
    assert set(doc) == {"answer", "answer_locked", "answer_source", "choices", "privacy_note",
                        "connectivity", "hf_hub_offline", "features", "restart_required",
                        "search_helper", "network_plugins", "warnings"}
    assert set(doc["search_helper"]) == {"state", "detail", "at", "managed"}
    assert doc["warnings"] == []                                   # only under never
    assert doc["answer"] == "" and doc["answer_source"] == "unset"
    assert doc["connectivity"]["online"] is True and doc["connectivity"]["reason"] == "connected"
    feature = doc["features"][0]
    assert set(feature) == {"name", "label", "value", "next_boot_value", "answer_values", "follows",
                            "set_by", "applies", "condition", "condition_met"}
    with egress.override_policy("never"):
        async with _core() as c:
            doc = (await c.get("/v1/admin/internet", headers=bearer(TOKEN))).json()
    assert doc["connectivity"]["online"] is False and doc["connectivity"]["reason"] == "turned_off"


# ─── GET /v1/connectivity ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connectivity_reports_policy_and_reason(box, monkeypatch) -> None:
    class Probe:
        online, reason, target = True, "connected", "1.1.1.1:443"
        last_checked_at = last_online_at = None

    monkeypatch.setattr(core_app.state, "probe", Probe(), raising=False)
    async with _core() as c:
        body = (await c.get("/v1/connectivity")).json()
        assert body["policy"] == "" and body["reason"] == "connected" and body["online"] is True
        with egress.override_policy("sometimes"):
            assert (await c.get("/v1/connectivity")).json()["policy"] == "sometimes"
        with egress.override_policy("never"):
            body = (await c.get("/v1/connectivity")).json()
    assert body == {"online": False, "last_checked_at": None, "last_online_at": None,
                    "target": "1.1.1.1:443", "policy": "never", "reason": "turned_off"}


# ─── The web side ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_config_carries_the_answer(monkeypatch) -> None:
    import web.backend.api.config as web_config

    class Result:
        def all(self):
            return []

        def first(self):
            return None

    class Session:
        async def execute(self, *_a, **_k):
            return Result()

    @asynccontextmanager
    async def fake_scope():
        yield Session()

    monkeypatch.setattr(web_config, "session_scope", fake_scope)
    async with _web() as c:
        assert (await c.get("/api/config")).json()["internet_access"] == ""
        with egress.override_policy("never"):
            assert (await c.get("/api/config")).json()["internet_access"] == "never"


@pytest.mark.asyncio
async def test_web_internet_proxy_and_follow_internet_forwarding(box, monkeypatch) -> None:
    import web.backend.api.config as web_config

    gets: list[tuple[str, dict]] = []
    posts: list[tuple[str, dict, dict]] = []

    async def fake_get(path, timeout=10.0, headers=None):
        gets.append((path, dict(headers or {})))
        return 200, {"answer": "sometimes"}

    async def fake_post(path, body=None, timeout=30.0, headers=None):
        posts.append((path, body, dict(headers or {})))
        return 200, {"applied": [], "restart_required": [], "rejected": {}, "followed": ["news_enabled"]}

    monkeypatch.setattr(web_config, "get_admin", fake_get)
    monkeypatch.setattr(web_config, "post_admin", fake_post)
    async with _web() as c:
        r = await c.get("/api/config/internet")
        assert r.status_code == 401                                  # gated like /config/editable
        r = await c.get("/api/config/internet", headers=bearer(TOKEN))
        assert r.status_code == 200 and r.json() == {"answer": "sometimes"}
        assert gets[-1][0] == "/v1/admin/internet"
        assert gets[-1][1].get("Authorization") == f"Bearer {TOKEN}"

        r = await c.patch("/api/config/editable", headers=bearer(TOKEN),
                          json={"changes": {}, "follow_internet": ["news_enabled"]})
        assert r.status_code == 200, r.text
        assert posts[-1][0] == "/v1/admin/config"
        assert posts[-1][1] == {"changes": {}, "follow_internet": ["news_enabled"]}
        r = await c.patch("/api/config/editable", headers=bearer(TOKEN),
                          json={"changes": {"internet_access": "never"}})
        assert posts[-1][1] == {"changes": {"internet_access": "never"}}
        r = await c.patch("/api/config/editable", headers=bearer(TOKEN),
                          json={"changes": {}, "follow_internet": ["x"] * 17})
        assert r.status_code == 422


# ─── A real lifespan under never (D14: the probe starts before the seed) ──

from domovoi.tests.conftest import requires_db  # noqa: E402


@requires_db
@pytest.mark.asyncio
async def test_a_core_booted_under_never_never_dials_and_says_so(monkeypatch) -> None:
    import domovoi.main as main_mod
    from domovoi import connectivity

    real_open = asyncio.open_connection
    dialled: list = []

    async def guarded(host=None, port=None, *a, **k):
        if (host, port) == ("1.1.1.1", 443):
            dialled.append((host, port))
            raise AssertionError("the probe dialled 1.1.1.1 under never")
        return await real_open(host, port, *a, **k)

    monkeypatch.setattr(asyncio, "open_connection", guarded)
    real_seed = main_mod.seed_voices
    seen_at_seed: dict = {}

    async def seed(*a, **k):
        probe = connectivity.current_probe()
        seen_at_seed["probe"] = None if probe is None else (probe.online, probe.reason)
        return await real_seed(*a, **k)

    monkeypatch.setattr(main_mod, "seed_voices", seed)
    with egress.override_policy("never"):
        async with main_mod.app.router.lifespan_context(main_mod.app):
            async with _core() as c:
                body = (await c.get("/v1/connectivity")).json()
    assert dialled == []
    assert seen_at_seed == {"probe": (False, "turned_off")}       # started before the voice seed
    assert body["policy"] == "never" and body["reason"] == "turned_off" and body["online"] is False
