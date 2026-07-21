"""Manifest validity + the full loader/contract-check path + routing.

Green here means the boot-time bundled discovery (and a dashboard
install) would accept this plugin: manifest parses, layout validates,
migrations lint clean, web import hygiene holds, register() satisfies
every §13.2 contract check, and the corpus phrases route to their
owners through the merged registry.
"""

from __future__ import annotations

import pytest

from domovoi.plugins_runtime.manifest import (
    check_web_import_hygiene,
    parse_manifest_dir,
)
from domovoi.plugins_runtime.migrations import PluginMigrationRunner
from domovoi.tests.conftest import requires_db

from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_manifest_parses_and_layout_validates() -> None:
    manifest = parse_manifest_dir(PLUGIN_DIR)
    assert manifest.slug == "radio"
    assert manifest.publisher == "Coders Farm"
    assert manifest.license == "MIT"
    assert manifest.entry_core == "domovoi_plugin_radio.core"
    assert manifest.entry_web == "domovoi_plugin_radio.web"
    # §2.3 capability declarations.
    assert "now-playing-source:radio" in manifest.provides
    assert "now-playing-matcher" in manifest.provides
    assert manifest.consumes == ("media-acquisition-queue",)
    # Handler declaration mirrors the code (contract-checked at load).
    (handler,) = manifest.handlers
    assert handler.band == 280
    assert handler.requires_network == "degraded"
    assert "play 97.5 fm" in handler.corpus
    # Worker declarations.
    kinds = {w.name: w.kind for w in manifest.workers}
    assert kinds == {
        "radio_sampler": "poll",
        "radio_icy_poller": "poll",
        "radio_detections_reaper": "poll",
        "fcc_import": "startup",
        "simulcast_backfill": "startup",
        "track_fingerprinter": "poll",
    }
    # Realtime channels carry the mandatory plugin_radio_ prefix.
    assert all(
        r.notify_channel.startswith("plugin_radio_") for r in manifest.realtime
    )


def test_lockfile_pins_match_manifest() -> None:
    manifest = parse_manifest_dir(PLUGIN_DIR)
    lock_text = (PLUGIN_DIR / "requirements.lock").read_text(encoding="utf-8")
    assert "--hash=" in lock_text
    for req in manifest.python_requirements:
        name, _, version = req.partition("==")
        assert f"{name.lower()}=={version}" in lock_text.lower().replace(" ", "")


def test_migrations_pass_sql_lint() -> None:
    runner = PluginMigrationRunner(
        "radio", PLUGIN_DIR / "migrations", database_urls=["unused"]
    )
    runner.lint_all()    # raises SqlLintError on any violation


def test_web_import_hygiene_clean() -> None:
    manifest = parse_manifest_dir(PLUGIN_DIR)
    assert check_web_import_hygiene(PLUGIN_DIR, manifest) == []


@requires_db
async def test_plugin_loads_through_real_loader(loaded_plugin) -> None:
    from domovoi.handlers import HANDLER_BY_NAME, HANDLERS

    h = HANDLER_BY_NAME["radio"]
    assert h.plugin_slug == "radio"
    assert h.priority_band == 280
    # Registry stays band-sorted with the plugin slotted in.
    bands = [x.priority_band for x in HANDLERS]
    assert bands == sorted(bands)
    # No system-tool degradation beyond the optional rtl_fm (ffmpeg is
    # required — if this fails on a dev box, install ffmpeg).
    assert all("rtl_fm" not in r for r in loaded_plugin.loaded.degraded_reasons)


@requires_db
async def test_capabilities_registered_during_register(loaded_plugin) -> None:
    names = loaded_plugin.loaded.context.registered_capability_names()
    assert "now-playing-source:radio" in names
    assert "now-playing-matcher" in names


@requires_db
async def test_corpus_and_neighbor_routing(loaded_plugin) -> None:
    # The plugin's own corpus phrases.
    loaded_plugin.assert_routes("play 97.5 fm", "radio")
    loaded_plugin.assert_routes("tune to the news station", "radio")
    loaded_plugin.assert_routes("stream kexp", "radio")
    loaded_plugin.assert_routes("stop the radio", "radio")
    # Neighbors keep their territory (the §4.2 band contract).
    loaded_plugin.assert_routes("play the beatles", "music")
    loaded_plugin.assert_routes("play my favorites", "playlist")
    loaded_plugin.assert_routes("find creep in my library", "library")
    loaded_plugin.assert_routes("what's the news", "news")


@requires_db
async def test_workers_registered_matching_manifest(loaded_plugin) -> None:
    from domovoi.plugins_runtime.workers import WORKERS

    assert WORKERS.worker_names("radio") == {
        "radio_sampler": "poll",
        "radio_icy_poller": "poll",
        "radio_detections_reaper": "poll",
        "track_fingerprinter": "poll",
    }
    hooks = set(WORKERS.hook_names("radio"))
    assert {"fcc_import", "simulcast_backfill"} <= hooks


@requires_db
async def test_unload_is_clean(db_session) -> None:
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.now_playing import NOW_PLAYING
    from domovoi.plugins_runtime.loader import LOADER
    from domovoi.plugins_runtime.manifest import parse_manifest_dir as _parse

    manifest = _parse(PLUGIN_DIR)
    await LOADER.load_plugin(
        slug="radio", install_dir=PLUGIN_DIR, manifest=manifest,
        foreign_corpus=[], update_registry_status=False,
    )
    assert "radio" in HANDLER_BY_NAME
    assert "radio" in NOW_PLAYING.sources()
    await LOADER.unload_plugin("radio")
    assert "radio" not in HANDLER_BY_NAME
    assert "radio" not in NOW_PLAYING.sources()
