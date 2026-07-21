"""WifiHandler tests — fast-path matching, action vs diagnostic, and the
status-from-cache plumbing.

The diagnostic path consumes Context.wifi_status, which the streaming
layer stamps from app.state.wifi_status before routing. These tests
exercise the handler in isolation by passing wifi_status directly into
Context — separate streaming-layer tests cover the cache → ctx pipeline.
"""

from __future__ import annotations

import pytest

from domovoi.handlers.wifi import (
    WifiHandler,
    _ACTION_RE,
    _DIAGNOSTIC_RE,
    _format_rate,
)
from domovoi.models import Context


# ─── Format helper ──────────────────────────────────────────────────────────

def test_format_rate_whole_megabits() -> None:
    assert _format_rate(43.0) == "43 megabits per second"
    assert _format_rate(1.0) == "1 megabits per second"


def test_format_rate_fractional_below_ten() -> None:
    """Below 10 Mbit/s, fractional values surface (1.5 isn't 2)."""
    assert _format_rate(1.5) == "1.5 megabits per second"
    assert _format_rate(7.3) == "7.3 megabits per second"


def test_format_rate_above_ten_rounds() -> None:
    """At 10+ we round — '43 megabits per second' beats '43.3'."""
    assert _format_rate(43.3) == "43 megabits per second"
    assert _format_rate(72.7) == "73 megabits per second"


# ─── Action regex ──────────────────────────────────────────────────────────

def test_action_regex_canonical_phrasings() -> None:
    for s in (
        "fix the wifi",
        "fix wifi",
        "fix the wi-fi",
        "fix the wi fi",
        "reset the wifi",
        "reset wifi",
        "reconnect the wifi",
        "reconnect wifi",
        "refresh the wifi",
        "restart the wifi",
        "cycle the wifi",
        "bounce the wifi",
        "reassociate",
        "fix the network",
        "fix the connection",
        "fix the wireless",
        "please fix the wifi",
        "can you fix the wifi",
        "could you reset the wifi",
        "fix your wifi",
        "fix my wifi",
    ):
        assert _ACTION_RE.match(s), f"expected match: {s!r}"


def test_action_regex_does_not_match_qa() -> None:
    """QA phrasings must NOT match — they should fall through to Ollama."""
    for s in (
        "fix my code",
        "reset my password",
        "what's the wifi password",
        "tell me about wifi",
        "is the wifi working",  # diagnostic-ish but not action — falls to QA today
    ):
        assert not _ACTION_RE.match(s), f"unexpected action match: {s!r}"


# ─── Diagnostic regex ──────────────────────────────────────────────────────

def test_diagnostic_regex_canonical_phrasings() -> None:
    for s in (
        "how's the wifi",
        "how is the wifi",
        "how's your wifi",
        "how is your wifi",
        "how's the network",
        "how's the connection",
        "how's your connection",
        "what's the wifi rate",
        "what's your wifi speed",
        "what's the wifi signal",
        "are you having wifi issues",
        "are you having wifi trouble",
        "are you having network issues",
        "are you connected to wifi",
    ):
        assert _DIAGNOSTIC_RE.match(s), f"expected match: {s!r}"


def test_diagnostic_does_not_swallow_action() -> None:
    """Action phrasings must take the action regex, not diagnostic."""
    for s in ("fix the wifi", "reset the wifi", "reassociate"):
        assert not _DIAGNOSTIC_RE.match(s)


# ─── Reassociate path ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reassociate_sets_pi_action() -> None:
    ctx = Context(room_id="kitchen", online=True)
    m = _ACTION_RE.match("fix the wifi")
    assert m
    response = await WifiHandler()._reassociate_from_match(m, ctx, None)
    assert response.text == "OK, reconnecting now."
    assert response.pi_action == "reassociate_wifi"
    assert response.matched_handler == "wifi"


@pytest.mark.asyncio
async def test_reassociate_via_tool_call() -> None:
    ctx = Context(room_id="kitchen", online=True)
    response = await WifiHandler().execute_from_tool({"action": "reassociate"}, ctx, None)
    assert response.pi_action == "reassociate_wifi"


# ─── Status path ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_with_full_cache() -> None:
    ctx = Context(
        room_id="kitchen",
        online=True,
        wifi_status={"rx_mbits": 43.3, "tx_mbits": 72.2, "ssid": "Kamber Wifi 2.0"},
    )
    m = _DIAGNOSTIC_RE.match("how's your wifi")
    assert m
    response = await WifiHandler()._status_from_match(m, ctx, None)
    assert response.text == "WiFi is connected at 43 megabits per second to Kamber Wifi 2.0."
    assert response.pi_action is None  # diagnostic doesn't trigger an action


@pytest.mark.asyncio
async def test_status_without_ssid() -> None:
    ctx = Context(room_id="kitchen", online=True, wifi_status={"rx_mbits": 5.0})
    m = _DIAGNOSTIC_RE.match("how's the wifi")
    assert m
    response = await WifiHandler()._status_from_match(m, ctx, None)
    assert response.text == "WiFi is connected at 5 megabits per second."


@pytest.mark.asyncio
async def test_status_no_cache_yet() -> None:
    """Pi just connected — domovoi hasn't received a wifi_status
    push yet. Should return a friendly 'come back later' rather than
    crash on a None lookup."""
    ctx = Context(room_id="kitchen", online=True)
    m = _DIAGNOSTIC_RE.match("how's your wifi")
    assert m
    response = await WifiHandler()._status_from_match(m, ctx, None)
    assert "don't have a recent" in response.text.lower()
    assert "minute" in response.text.lower()


@pytest.mark.asyncio
async def test_status_with_no_rate_in_cache() -> None:
    """Watcher pushed a partial status (e.g., link down — rx_mbits is
    None). Handler should still respond, suggesting a fix."""
    ctx = Context(
        room_id="kitchen", online=True,
        wifi_status={"rx_mbits": None, "tx_mbits": None, "ssid": None},
    )
    m = _DIAGNOSTIC_RE.match("how's your wifi")
    assert m
    response = await WifiHandler()._status_from_match(m, ctx, None)
    assert "unhealthy" in response.text.lower() or "trouble" in response.text.lower()


# ─── Handler shape / registration ───────────────────────────────────────────

def test_handler_declares_local_only() -> None:
    """Action is fully Pi-local; routing decision shouldn't gate on
    domovoi reachability."""
    assert WifiHandler().requires_network == "no"


def test_tool_schema_has_required_action() -> None:
    schema = WifiHandler().tool_schema
    assert "action" in schema["parameters"]["properties"]
    assert schema["parameters"]["required"] == ["action"]


@pytest.mark.asyncio
async def test_execute_default_asks_for_clarification() -> None:
    """Plain `execute` (no fast-path match, no tool args) should prompt
    rather than guess — matches the IntercomHandler / VoiceProfileHandler
    pattern."""
    ctx = Context(room_id="kitchen", online=True)
    response = await WifiHandler().execute(None, ctx, None)
    assert response.matched_handler == "wifi"
    assert "?" in response.text  # asks something


@pytest.mark.asyncio
async def test_execute_from_tool_unknown_action() -> None:
    ctx = Context(room_id="kitchen", online=True)
    response = await WifiHandler().execute_from_tool({"action": "explode"}, ctx, None)
    assert "explode" in response.text
    assert response.matched_handler == "wifi"
