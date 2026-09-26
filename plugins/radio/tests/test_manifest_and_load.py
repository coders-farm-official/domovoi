"""Manifest validity + the full loader/contract-check path + routing.

Green here means the boot-time bundled discovery (and a dashboard
install) would accept this plugin: manifest parses, layout validates,
migrations lint clean, web import hygiene holds, register() satisfies
every §13.2 contract check, and the corpus phrases route to their
owners through the merged registry.
"""

from __future__ import annotations

import pytest

from domovoi.plugins_runtime.lockfile import normalize_name, parse_lockfile
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


def test_direct_pins_shared_with_the_core_lock_match_it() -> None:
    """A direct pin the core's requirements.lock also pins must be the same
    version with the same hashes: the installer's dry-run refuses a lock that
    would change a dist already in the core's environment
    (requirements_conflict), and the sleep plugin's tests hold its numpy pin
    to this lock as well as the core's."""
    manifest = parse_manifest_dir(PLUGIN_DIR)
    core_lock = PLUGIN_DIR.parents[1] / "requirements.lock"
    core = {
        r.key: r for r in parse_lockfile(core_lock.read_text(encoding="utf-8"))
    }
    ours = {
        r.key: r
        for r in parse_lockfile(
            (PLUGIN_DIR / "requirements.lock").read_text(encoding="utf-8")
        )
    }
    direct = (
        normalize_name(req.split("[")[0].split("==")[0])
        for req in manifest.python_requirements
    )
    shared = [key for key in direct if key in core]
    assert "numpy" in shared
    for key in shared:
        assert ours[key].version == core[key].version, (
            f"{key}: radio pins {ours[key].version}, core lock "
            f"{core[key].version}"
        )
        assert set(ours[key].hashes) == set(core[key].hashes), key


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


@requires_db
async def test_state_route_reads_the_live_sdk_after_re_enable(db_session) -> None:
    """Disable then enable runs ``register()`` again against a fresh SDK,
    and the core ``/state`` route must read THAT load's ``sdk.state``. The
    first enable's route used to keep answering, from the torn-down SDK's
    orphaned state: a tuner the disable had stopped, an FCC job nothing
    updates any more."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from domovoi.plugins_runtime.loader import PluginLoader

    class _Tuner:
        def __init__(self, mhz: float) -> None:
            self.current_frequency_mhz = mhz

        async def stop(self) -> None:
            pass

    manifest = parse_manifest_dir(PLUGIN_DIR)
    loader = PluginLoader()
    app = FastAPI()
    loader.bind_app(app)

    async def _load():
        return await loader.load_plugin(
            slug="radio", install_dir=PLUGIN_DIR, manifest=manifest,
            foreign_corpus=[], foreign_web_routes=[],
            update_registry_status=False,
        )

    async def _state() -> dict:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/v1/plugins/radio/state")
        assert r.status_code == 200, r.text
        return r.json()

    try:
        first = await _load()
        first.sdk.state["sdr_tuner"] = _Tuner(88.1)
        assert (await _state())["sdr_frequency_mhz"] == 88.1

        await loader.unload_plugin("radio")
        second = await _load()
        second.sdk.state["sdr_tuner"] = _Tuner(97.5)
        body = await _state()
        assert body["sdr_available"] is True
        assert body["sdr_frequency_mhz"] == 97.5
    finally:
        if "radio" in loader.loaded:
            await loader.unload_plugin("radio")
