"""What ``GET /v1/admin/config`` reads back, and to whom (CORE-6).

The config read renders the settings gear, so it has always been
cookie-readable. But cookies are host-scoped rather than port-scoped, and
before first-run setup the read keeps its LAN grace — so "whoever can
render the page" is a wider audience than "the admin". The values that
are credentials (the database URL with its password, the third-party API
keys) are therefore masked for everyone but a live admin Bearer, and the
advanced block — the infrastructure knobs — comes back only for that
caller.

DB-free: the handler reads the settings singleton and the plugin config
registry, and the classification is faked with ``install_fake_db``.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.config_schema import (
    EDITABLE_FIELDS,
    SECRET_SETTING_NAMES,
    is_secret_setting,
    mask_secret,
)
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import COOKIE, bearer, install_fake_db

ADMIN_TOKEN = "admin-token"
MASK = "•••• set"


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _by_name(payload: dict) -> dict[str, dict]:
    return {f["name"]: f for f in payload["fields"]}


# ─── The registry knows which settings are credentials ────────────────────


def test_the_three_credential_settings_are_recognized() -> None:
    for name in ("database_url", "acoustid_api_key", "letta_token"):
        assert is_secret_setting(name), name
    assert SECRET_SETTING_NAMES >= {"database_url", "acoustid_api_key", "letta_token"}
    assert not is_secret_setting("bot_name")


def test_editable_fieldspecs_report_their_own_secrecy() -> None:
    by_name = {spec.name: spec for spec in EDITABLE_FIELDS}
    assert by_name["database_url"].secret is True
    assert by_name["bot_name"].secret is False


def test_a_mask_says_whether_the_value_is_set_without_saying_what_it_is() -> None:
    assert mask_secret("postgresql://domovoi:hunter2@localhost/domovoi") == MASK
    assert mask_secret("") == "not set"
    assert mask_secret(None) == "not set"


# ─── The read itself ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_cookie_only_caller_never_reads_a_credential(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client() as c:
        c.cookies.set(COOKIE, ADMIN_TOKEN)
        r = await c.get("/v1/admin/config")
    assert r.status_code == 200
    payload = r.json()
    for field in payload["fields"]:
        if is_secret_setting(field["name"]):
            assert field["value"] in (MASK, "not set"), field
            assert field["masked"] is True
    # Nothing that looks like a DSN survives anywhere in the answer.
    assert "postgresql" not in r.text


@pytest.mark.asyncio
async def test_an_admin_bearer_reads_the_real_values(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client() as c:
        r = await c.get("/v1/admin/config", headers=bearer(ADMIN_TOKEN))
    assert r.status_code == 200
    from domovoi.config import settings

    field = _by_name(r.json())["database_url"]
    assert field["value"] == settings.database_url
    assert field["masked"] is False
    assert field["secret"] is True


@pytest.mark.asyncio
async def test_the_advanced_section_needs_a_bearer(monkeypatch) -> None:
    """A cookie-only caller gets the common settings and a straight
    refusal for the advanced block — not an empty list it would have to
    guess at."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client() as c:
        c.cookies.set(COOKIE, ADMIN_TOKEN)
        whole = await c.get("/v1/admin/config")
        asked = await c.get("/v1/admin/config?section=advanced")
    assert whole.status_code == 200
    assert whole.json()["advanced_available"] is False
    assert all(f["section"] != "advanced" for f in whole.json()["fields"])
    assert "database_url" not in _by_name(whole.json())
    assert asked.status_code == 401

    async with _client() as c:
        whole = await c.get("/v1/admin/config", headers=bearer(ADMIN_TOKEN))
        asked = await c.get(
            "/v1/admin/config?section=advanced", headers=bearer(ADMIN_TOKEN)
        )
    assert whole.json()["advanced_available"] is True
    assert "database_url" in _by_name(whole.json())
    assert asked.status_code == 200
    assert {f["section"] for f in asked.json()["fields"]} == {"advanced"}


@pytest.mark.asyncio
async def test_before_setup_the_page_renders_but_the_secrets_do_not(
    monkeypatch,
) -> None:
    """A fresh install has no Bearer to present, so the gear still draws
    (including the advanced block it needs for first-run infrastructure
    edits) — with the credentials masked, because "pre-setup" means
    anyone on the LAN."""
    install_fake_db(monkeypatch, admin=False)
    async with _client() as c:
        r = await c.get("/v1/admin/config")
    assert r.status_code == 200
    payload = r.json()
    assert payload["advanced_available"] is True
    assert _by_name(payload)["database_url"]["value"] == MASK
    assert "postgresql" not in r.text


@pytest.mark.asyncio
async def test_an_unknown_section_is_refused(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client() as c:
        r = await c.get("/v1/admin/config?section=sekrit", headers=bearer(ADMIN_TOKEN))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_the_read_still_refuses_a_caller_with_nothing(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    async with _client() as c:
        assert (await c.get("/v1/admin/config")).status_code == 401
