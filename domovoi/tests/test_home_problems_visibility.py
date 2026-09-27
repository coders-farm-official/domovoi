"""HOME_PROBLEMS_VISIBILITY — who sees Home's "needs attention" rows.

Owner decision 2026-09-26 (HOME-PLAN.md): option A — split by who can act
— is the default, and option C's household setting lets an admin make the
page quieter:

* ``everyone`` (default): a household member sees the problems that
  explain what they notice; an admin sees every row;
* ``summary``: household members see one neutral line;
* ``admins``: only a signed-in admin sees any.

The page learns the value from the OPEN ``GET /api/config`` — the one
household setting that response carries. It is edited through the core
(tier ``hot``), which leaves the web process's own settings copy stale, so
the web reads the LIVE value from the core's admin snapshot (the one its
poll loop refreshes every 1.5 s) and falls back to its own copy only while
the core has not answered.

DB-free except the one end-to-end read of ``/api/config`` (``requires_db``:
it lists the provisioned rooms).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.config import HOME_PROBLEMS_VISIBILITY_CHOICES, Settings, settings
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate
from domovoi.tests.conftest import requires_db
from web.backend import domovoi_client
from web.backend.api import config as config_api
from web.backend.schemas import ConfigResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / "domovoi" / ".env.example"


# ─── the setting ─────────────────────────────────────────────────────────


def test_the_default_is_option_a() -> None:
    assert HOME_PROBLEMS_VISIBILITY_CHOICES == ("everyone", "summary", "admins")
    assert Settings.model_fields["home_problems_visibility"].default == "everyone"


@pytest.mark.parametrize(
    ("raw", "read", "warned"),
    [("summary", "summary", False), ("  Admins ", "admins", False), ("EVERYONE", "everyone", False),
     ("", "everyone", False),
     # A near miss reads as the choice it names — never as the most open one.
     ("admin", "admins", True), ("Admin-Only", "admins", True), ("admins_only", "admins", True),
     ("none", "admins", True), ("nobody", "admins", True), ("summary  only", "summary", True),
     ("all", "everyone", True),
     # Anything else: the quieter "summary", said in the log, and a typo
     # still never stops the voice server booting over a web-page detail.
     ("sometimes", "summary", True), ("everyone!", "summary", True)],
)
def test_env_values_are_forgiven_and_never_fatal(monkeypatch, caplog, raw, read, warned) -> None:
    monkeypatch.setenv("HOME_PROBLEMS_VISIBILITY", raw)
    with caplog.at_level("WARNING", logger="domovoi.config"):
        assert Settings(_env_file=None).home_problems_visibility == read
    said = [r.getMessage() for r in caplog.records if r.name == "domovoi.config"]
    if warned:
        assert said and "HOME_PROBLEMS_VISIBILITY" in said[0] and repr(raw) in said[0], said
    else:
        assert not said, said


def test_env_example_carries_it_commented_at_its_default() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    assert "# HOME_PROBLEMS_VISIBILITY=everyone" in lines
    assert not any(line.startswith("HOME_PROBLEMS_VISIBILITY=") for line in lines)


def test_an_admin_edits_it_live_in_the_dashboard() -> None:
    spec = FIELD_BY_NAME["home_problems_visibility"]
    assert spec.type == "choice" and spec.choices == list(HOME_PROBLEMS_VISIBILITY_CHOICES)
    assert spec.tier == "hot" and spec.section == "common"
    assert coerce_and_validate(spec, "summary") == "summary"
    with pytest.raises(ValueError):
        coerce_and_validate(spec, "nobody")


# ─── how the page reads it ───────────────────────────────────────────────


def test_the_core_snapshot_carries_the_live_value(monkeypatch) -> None:
    """A config save setattr()s the CORE's singleton; the snapshot the web
    polls reads that singleton, not .env."""
    import asyncio

    from domovoi import main as core_main

    for name in ("active_sessions", "resumable_music", "wifi_status", "current_playlist",
                 "active_dropins", "satellite_full_duplex", "satellite_sat_type",
                 "satellite_mic_enabled", "satellite_display", "satellite_voice",
                 "satellite_volume", "satellite_synced_sha"):
        monkeypatch.setattr(core_main.app.state, name, {}, raising=False)
    monkeypatch.setattr(settings, "home_problems_visibility", "admins")
    snap = asyncio.run(core_main.admin_snapshot())
    assert snap["home_problems_visibility"] == "admins"


@pytest.mark.parametrize(
    ("snapshot", "own", "read"),
    [
        ({"home_problems_visibility": "summary"}, "everyone", "summary"),  # live wins
        (None, "admins", "admins"),                                        # core not answered
        ({"active_rooms": []}, "summary", "summary"),                      # core from before the field
        ({"home_problems_visibility": "loud"}, "everyone", "everyone"),    # never a value outside the set
    ],
    ids=["live", "no-snapshot", "older-core", "junk"],
)
def test_the_web_reads_the_live_value_and_falls_back_to_its_own(
    monkeypatch, snapshot, own, read
) -> None:
    monkeypatch.setattr(domovoi_client, "_snapshot", snapshot)
    monkeypatch.setattr(config_api.core_settings, "home_problems_visibility", own)
    assert config_api.home_problems_visibility() == read


def test_api_config_exposes_this_and_no_other_setting() -> None:
    """The response is open. Its field set is pinned so a new key is a
    decision, not a drive-by."""
    assert set(ConfigResponse.model_fields) == {
        "bot_name", "tts_voice", "rooms", "web_version", "wake_word_min_clips",
        "home_problems_visibility",
    }


@requires_db
@pytest.mark.asyncio
async def test_api_config_answers_it_to_anyone(monkeypatch) -> None:
    monkeypatch.setattr(domovoi_client, "_snapshot", {"home_problems_visibility": "summary"})
    from web.backend.main import app as web_app

    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
        r = await c.get("/api/config")
    assert r.status_code == 200, r.text
    assert r.json()["home_problems_visibility"] == "summary"
