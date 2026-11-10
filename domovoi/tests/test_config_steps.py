"""How far one arrow press moves a number setting, and which settings are
sliders (2026-11-09).

The dashboard drew every decimal setting as ``<input type="number"
step="any">``, and with ``step="any"`` the browser's arrows and spinner
move by 1: the satellite's "Wake sensitivity" (0-1, default 0.5) went to
the top of its range in one click. Now every int/float field carries a
``step`` (its own, or one picked from its range in ONE place,
``config_schema.effective_step``) and the server sends it with the field,
so the dashboard, Android and iOS read the same number.

* every float field in the server and satellite registries steps by at most
  a tenth of its range, and every int field by a whole number;
* a slider field has both ends and words for each end, and the words for
  ``wake.threshold`` say what the ends DO (higher = fewer wakes);
* both config reads send ``step``, ``widget`` and ``slider_labels``;
* typing stays free: an off-step value inside the range saves, an
  out-of-range one is refused with the server's own wording (the dashboard
  shows the same words under the field and holds Save; see
  test_web_config_steps.py);
* the plugin config bridge's rows never reach ConfigField (no dashboard
  code reads ``plugin_fields``) and carry no range, so they have no step.

No DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import config_schema as core
from domovoi import satellite_config_schema as sat
from domovoi.config_schema import auto_step, effective_step, widget_payload

ALL = [("core", s) for s in core.EDITABLE_FIELDS] + [("satellite", s) for s in sat.EDITABLE_FIELDS]
NUMERIC = [(where, s) for where, s in ALL if s.type in ("int", "float")]
FLOATS = [(where, s) for where, s in NUMERIC if s.type == "float"]
SLIDERS = [(where, s) for where, s in ALL if s.widget == "slider"]
REPO_ROOT = Path(__file__).resolve().parents[2]


def _id(p) -> str:
    return f"{p[0]}:{p[1].name}"


# ─── The step ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("entry", FLOATS, ids=_id)
def test_every_float_steps_by_at_most_a_tenth_of_its_range(entry) -> None:
    _where, spec = entry
    assert spec.min is not None and spec.max is not None, "a float needs a range to step in"
    step = effective_step(spec)
    assert step is not None and step > 0
    assert step <= (spec.max - spec.min) / 10 + 1e-9, (spec.name, step)


@pytest.mark.parametrize("entry", NUMERIC, ids=_id)
def test_every_number_has_a_whole_or_positive_step(entry) -> None:
    _where, spec = entry
    step = effective_step(spec)
    assert step is not None and step > 0
    if spec.type == "int":
        assert isinstance(step, int) and step >= 1, (spec.name, step)


def test_non_numbers_have_no_step() -> None:
    for _where, spec in ALL:
        if spec.type not in ("int", "float"):
            assert effective_step(spec) is None, spec.name


def test_the_reported_bug_moves_by_a_twentieth() -> None:
    # One arrow press from 0.5 goes to 0.55, not to 1.
    assert effective_step(sat.FIELD_BY_NAME["wake.threshold"]) == 0.05
    assert effective_step(sat.FIELD_BY_NAME["greeting.funny_chance"]) == 0.05


@pytest.mark.parametrize(
    ("lo", "hi", "integer", "want"),
    [
        (0, 1, False, 0.05),        # 1/20
        (0.5, 0.95, False, 0.02),   # 0.0225 -> 2e-2
        (0, 0.3, False, 0.02),      # 0.015 -> nearer 2e-2 than 1e-2 in ratio
        (0, 900, False, 50.0),      # 45 -> 5e1
        (30, 1800, False, 100.0),   # 88.5 -> 1e2
        (0, 3, True, 1),            # never below 1 for a whole number
        (1, 10, True, 1),
        (0, 255, True, 10),
        (0, 100000, True, 5000),
    ],
)
def test_auto_step_rounds_to_one_two_or_five(lo, hi, integer, want) -> None:
    got = auto_step(lo, hi, integer=integer)
    assert got == want
    assert type(got) is (int if integer else float)


def test_auto_step_never_exceeds_a_tenth_of_the_range() -> None:
    for span in [0.07, 0.1, 0.3, 0.45, 0.65, 1, 1.5, 2, 3.3, 7, 9.9, 13, 47, 99, 640, 7777]:
        assert auto_step(0, span, integer=False) <= span / 10 + 1e-12, span


def test_a_spec_step_wins_and_ints_stay_whole() -> None:
    spec = core.FieldSpec("x", "X", "G", "h", "float", min=0, max=1, step=0.1)
    assert effective_step(spec) == 0.1
    spec = core.FieldSpec("x", "X", "G", "h", "int", min=0, max=3, step=0.5)
    assert effective_step(spec) == 1
    assert effective_step(core.FieldSpec("x", "X", "G", "h", "int")) == 1


def test_speech_pause_steps_in_its_own_30_ms() -> None:
    # Its help text says "Whole 30 ms steps", and 240 (the default) is on
    # the grid that starts at its minimum of 90.
    spec = sat.FIELD_BY_NAME["listen.speech_pause_ms"]
    assert effective_step(spec) == 30
    assert (240 - spec.min) % 30 == 0


# ─── Sliders ─────────────────────────────────────────────────────────────


# Every bounded int/float in the COMMON section is a slider (owner,
# 2026-11-09) except these, each for the reason given. Advanced-section
# fields stay number boxes.
SLIDER_SKIPS = {
    "ollama_num_ctx": "0-262144 tokens, and 0 means 'the Ollama server's default', not 'smallest'",
    "ollama_tool_num_ctx": "0-262144 tokens, and 0 means 'the Ollama server's default'",
    "ollama_vision_num_ctx": "0-262144 tokens, and 0 means 'the Ollama server's default'",
    "ollama_chat_num_predict": "0-32768 tokens: too wide to aim with a thumb",
    "voice_profile_soft_tier_sec": "60-86400 s: too wide to aim with a thumb",
    "media_plays_retention_days": "0-3650 days, and 0 means 'keep forever', the opposite of its neighbour 1",
    "news_retention_days": "1-3650 days: too wide to aim with a thumb",
    "news_fetch_hour": "an hour of the day, not a more/less setting",
    "memory_extractor_loop_sec": "10-3600 s: too wide to aim with a thumb",
    "dropin_silence_timeout_sec": "0 means 'never end a quiet call', the opposite of its neighbour 5",
    "listen.response_deadline_sec": "0 turns the deadline off, the opposite of its neighbour 5",
    "listen.response_backstop_sec": "0 turns the backstop off, the opposite of its neighbour 5",
}


def test_every_common_bounded_number_is_a_slider_unless_skipped() -> None:
    bounded = {s.name for _w, s in NUMERIC
               if s.section == "common" and s.min is not None and s.max is not None}
    sliders = {s.name for _w, s in SLIDERS}
    assert SLIDER_SKIPS.keys() <= bounded, SLIDER_SKIPS.keys() - bounded
    assert sliders == bounded - SLIDER_SKIPS.keys()
    assert len(sliders) == 23


@pytest.mark.parametrize("entry", SLIDERS, ids=_id)
def test_a_slider_has_both_ends_and_words_for_them(entry) -> None:
    _where, spec = entry
    assert spec.type in ("int", "float")
    assert spec.min is not None and spec.max is not None and spec.min < spec.max
    assert spec.slider_labels is not None and len(spec.slider_labels) == 2
    left, right = spec.slider_labels
    assert left.strip() and right.strip() and left != right
    # Everyday settings only: the Advanced section stays number boxes.
    assert spec.section == "common", spec.name
    payload = widget_payload(spec)
    assert payload["widget"] == "slider"
    assert payload["slider_labels"] == [left, right]


def test_wake_sensitivity_labels_say_what_the_ends_do() -> None:
    # HIGHER threshold = FEWER wakes. Left (min) is the sensitive end.
    spec = sat.FIELD_BY_NAME["wake.threshold"]
    assert "higher = fewer false wakes" in spec.help.lower()
    assert spec.slider_labels == ("more sensitive", "fewer false wakes")


def test_a_slider_without_words_or_ends_is_a_number_box() -> None:
    no_words = core.FieldSpec("x", "X", "G", "h", "float", min=0, max=1, widget="slider")
    no_end = core.FieldSpec("x", "X", "G", "h", "float", min=0, widget="slider",
                            slider_labels=("a", "b"))
    for spec in (no_words, no_end):
        assert widget_payload(spec)["widget"] is None
        assert widget_payload(spec)["slider_labels"] is None


# ─── Typing stays free; out of range is refused ──────────────────────────


def test_an_off_step_value_inside_the_range_saves() -> None:
    assert sat.coerce_and_validate(sat.FIELD_BY_NAME["wake.threshold"], 0.53) == 0.53
    assert core.coerce_and_validate(core.FIELD_BY_NAME["tts_speed"], 1.07) == 1.07


def test_an_out_of_range_value_is_refused_not_capped() -> None:
    with pytest.raises(ValueError, match="must be at most 1.0"):
        sat.coerce_and_validate(sat.FIELD_BY_NAME["wake.threshold"], 1.5)
    with pytest.raises(ValueError, match="must be at least 0.5"):
        core.coerce_and_validate(core.FIELD_BY_NAME["tts_speed"], 0.2)


# ─── The reads send it ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_server_config_read_sends_step_and_widget(monkeypatch) -> None:
    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import bearer, install_fake_db

    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    async with AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False), base_url="http://test",
    ) as c:
        r = await c.get("/v1/admin/config", headers=bearer("admin-token"))
    assert r.status_code == 200
    by_name = {f["name"]: f for f in r.json()["fields"]}
    for spec in core.EDITABLE_FIELDS:
        row = by_name[spec.name]
        assert {"step", "widget", "slider_labels"} <= row.keys(), spec.name
        assert row["step"] == effective_step(spec), spec.name
    speed = by_name["tts_speed"]
    assert speed["step"] == 0.05
    assert speed["widget"] == "slider"
    assert speed["slider_labels"] == ["slower", "faster"]
    assert by_name["bot_name"]["step"] is None
    assert by_name["music_match_play_threshold"]["widget"] is None   # Advanced: a box


@pytest.mark.asyncio
async def test_the_satellite_config_read_sends_step_and_widget(monkeypatch) -> None:
    from domovoi import main

    monkeypatch.setattr(main.app.state, "active_sessions", {"office": object()}, raising=False)
    monkeypatch.setattr(main.app.state, "satellite_config",
                        {"office": {"wake.threshold": 0.5}}, raising=False)
    body = await main.admin_get_satellite_config("office")
    by_name = {f["name"]: f for f in body["fields"]}
    wake = by_name["wake.threshold"]
    assert wake["step"] == 0.05
    assert wake["widget"] == "slider"
    assert wake["slider_labels"] == ["more sensitive", "fewer false wakes"]
    assert by_name["listen.silence_timeout"]["step"] == 0.1
    assert by_name["listen.response_deadline_sec"]["widget"] is None   # 0 = off: a box
    assert by_name["leds.brightness"]["widget"] is None               # Advanced: a box
    assert by_name["log.level"]["step"] is None


# ─── The plugin bridge ───────────────────────────────────────────────────


def test_plugin_config_rows_never_reach_the_dashboard_editor() -> None:
    """The bridge's rows ride along as ``plugin_fields`` in the config read,
    but no dashboard script renders them (they have ``kind``, not ``type``,
    and no range), so there is no ConfigField step to give them. If a page
    starts reading them, give the bridge a range and a step first."""
    static = REPO_ROOT / "web" / "static"
    for path in list(static.glob("*.jsx")) + list(static.glob("*.js")):
        assert "plugin_fields" not in path.read_text(encoding="utf-8"), path.name
