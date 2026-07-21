"""Handler-contract checks, generalized (design §13.2).

These are the same checks the plugin loader runs at install/enable/boot
time; here they run over the live core registry so a core regression
fails in CI exactly like a bad plugin fails at install. No DB needed.
"""

from __future__ import annotations

import inspect
import random
import re

from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.handlers.base import (
    FastPath,
    Handler,
    HandlerDisplay,
    registry_sort_key,
)

# The design §4.2 band table for core handlers — EXACT values. A drifted
# band is a silent-misrouting hazard (dossier §7 invariant 6), so this is
# pinned as data, not derived.
CORE_BANDS = {
    "dismiss": 100,
    "voice_profile": 110,
    "wifi": 120,
    "voice": 130,
    "reminder": 140,
    "calculator": 150,
    "timer": 160,
    "clock": 170,
    "repeat": 180,
    "double_check": 190,
    "dropin": 200,
    "intercom": 210,
    "chat_mode": 220,
    "voice_notes": 230,
    "memory": 240,
    "homelab": 250,
    "news": 260,
    "spoken_audio": 270,
    # 280 reserved: the bundled radio plugin declares it (before music so
    # "play 97.5 fm" isn't poached).
    "playlist": 290,
    "music": 300,
    "library": 310,
    # 900 reserved: a media-provider plugin's greedy `^find (.+)$` catch-all.
}

VALID_TONES = {"neutral", "media", "device", "info", "comms"}


def test_registry_covers_exactly_the_core_band_table() -> None:
    assert {h.name for h in HANDLERS} == set(CORE_BANDS), (
        "core registry does not match the design §4.2 handler set"
    )
    for h in HANDLERS:
        assert h.priority_band == CORE_BANDS[h.name], (
            f"{h.name}: band {h.priority_band} != design table "
            f"{CORE_BANDS[h.name]}"
        )


def test_registry_is_band_sorted_and_indexed() -> None:
    assert HANDLERS == sorted(HANDLERS, key=registry_sort_key)
    bands = [h.priority_band for h in HANDLERS]
    assert bands == sorted(bands)
    assert HANDLER_BY_NAME == {h.name: h for h in HANDLERS}


def test_registry_order_is_registration_order_independent() -> None:
    """Shuffle-determinism (design §13.3): whatever order handlers get
    registered in, the sort key reproduces the exact same dispatch
    order — no position-in-list dependence survives."""
    for seed in (1, 7, 42):
        shuffled = list(HANDLERS)
        random.Random(seed).shuffle(shuffled)
        assert sorted(shuffled, key=registry_sort_key) == HANDLERS


def test_every_networked_handler_overrides_fallback_offline() -> None:
    base_method = Handler.fallback_offline
    for handler in HANDLERS:
        if handler.requires_network == "no":
            continue
        own_method = type(handler).fallback_offline
        assert own_method is not base_method, (
            f"{handler.name} declares requires_network={handler.requires_network!r} "
            f"but does not override fallback_offline(). Local-first contract violated."
        )


def test_handlers_have_required_attributes() -> None:
    for h in HANDLERS:
        assert isinstance(h.name, str) and h.name
        assert isinstance(h.tool_schema, dict) and h.tool_schema
        assert isinstance(h.fast_paths, list)
        assert h.requires_network in ("no", "degraded", "yes")
        assert inspect.iscoroutinefunction(h.execute)
        assert isinstance(h.priority_band, int)
        assert h.plugin_slug is None  # core registry — no plugin handlers yet


def test_tool_schema_name_matches_handler_name() -> None:
    # §13.2 check 1: name == tool_schema["name"] — the qwen tool router
    # and the chat proxy tools both key on it.
    for h in HANDLERS:
        assert h.tool_schema.get("name") == h.name, (
            f"{h.name}: tool_schema name is {h.tool_schema.get('name')!r}"
        )


def test_every_handler_declares_display_metadata() -> None:
    for h in HANDLERS:
        assert isinstance(h.display, HandlerDisplay), f"{h.name}: no display"
        assert h.display.label.strip(), f"{h.name}: empty display label"
        assert h.display.tone in VALID_TONES, (
            f"{h.name}: tone {h.display.tone!r} not in {sorted(VALID_TONES)}"
        )


def test_fast_paths_are_normalized_and_compiled() -> None:
    for h in HANDLERS:
        for fp in h.fast_paths:
            assert isinstance(fp, FastPath), (
                f"{h.name}: fast path {fp!r} was not normalized to FastPath"
            )
            assert isinstance(fp.pattern, re.Pattern)
            assert callable(fp.method)
            # FastPath keeps 2-tuple unpacking sugar for tuple-style iteration.
            pattern, method = fp
            assert pattern is fp.pattern and method is fp.method


def test_offline_ok_only_meaningful_on_degraded_handlers() -> None:
    # §4.3: offline_ok defaults True for degraded handlers and MUST be
    # unset (None) everywhere else.
    for h in HANDLERS:
        for fp in h.fast_paths:
            if h.requires_network == "degraded":
                assert fp.offline_ok in (None, True, False)
            else:
                assert fp.offline_ok is None, (
                    f"{h.name} (requires_network={h.requires_network!r}) "
                    f"declares offline_ok={fp.offline_ok!r} on {fp.pattern.pattern!r}"
                )


def test_confirmation_kinds_are_core_namespaced() -> None:
    # §4.3: kinds are namespaced — "core.<kind>" for core handlers.
    for h in HANDLERS:
        for kind in h.confirmation_kinds:
            assert kind.startswith("core."), (
                f"{h.name}: confirmation kind {kind!r} is not namespaced"
            )
        if h.confirmation_kinds:
            own = type(h).handle_confirmation
            assert own is not Handler.handle_confirmation, (
                f"{h.name} declares confirmation_kinds but does not "
                f"override handle_confirmation"
            )


def test_declared_confirmation_kinds_cover_known_flows() -> None:
    # The core confirmation vocabulary — a park site for an undeclared
    # kind now raises in domovoi.confirmations.request_confirmation, so
    # this doubles as documentation of the live set.
    expected = {
        "voice_profile": {"core.self_intro", "core.third_party_intro_confirm"},
        "double_check": {"core.self_doubt_offer", "core.prefs_offer"},
        "memory": {"core.pending_memory_offer"},
        "playlist": {"core.playlist_choice", "core.playlist_add_choice"},
        "news": {"core.news_fetch", "core.news_fetch_topics"},
        "dropin": {"core.dropin_invite"},
    }
    for name, kinds in expected.items():
        assert set(HANDLER_BY_NAME[name].confirmation_kinds) == kinds
    for h in HANDLERS:
        if h.name not in expected:
            assert h.confirmation_kinds == (), (
                f"{h.name} grew confirmation_kinds — add it to this table"
            )


def test_greedy_catchall_stays_in_the_last_core_media_band() -> None:
    """The mechanical "prove you don't poach `play X`" rule (§4.2): the
    only unanchored single-capture catch-all core owns is music's
    ``^play (.+)$``, and every handler in a LATER band must not declare
    one that could shadow an earlier anchored phrase. (Plugins get the
    band ≥ 900 rule at install time; core's equivalent is this pin.)"""
    music = HANDLER_BY_NAME["music"]
    greedy = [fp.pattern.pattern for fp in music.fast_paths if fp.pattern.pattern == r"^play (.+)$"]
    assert greedy, "music lost its ^play (.+)$ catch-all"
    # Everything banded after music must be anchored beyond a bare verb.
    for h in HANDLERS:
        if h.priority_band <= music.priority_band:
            continue
        for fp in h.fast_paths:
            assert fp.pattern.pattern != r"^play (.+)$"
