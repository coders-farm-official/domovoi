"""Radio plugin test harness (design §13.1, plugin-local).

The core exposes :func:`domovoi.sdk.testing.make_stub_sdk` today; the
packaged ``plugin_harness`` fixtures are still landing, so this conftest
builds their equivalents locally from core primitives:

* ``stub_sdk``      — no-DB, no-core recording double (handler units).
* ``plugin_db``     — the plugin's migrations applied to the test DB
                      (schema ``plugin_radio``), once per session.
* ``db_session``    — fresh session with core + plugin tables truncated.
* ``radio_sdk``     — a REAL ``PluginSDK`` wired to the process
                      singletons + test DB (worker integration tier).
* ``loaded_plugin`` — the plugin loaded through the real
                      ``PluginLoader`` (contract checks and all), with
                      an ``assert_routes`` helper.
* ``web_client``    — the plugin's web router mounted on a FastAPI
                      TestClient against a fake ``WebPluginContext``.

Importing ``domovoi.tests.conftest`` FIRST pins ``USE_STUBS=true`` and
derives the ``*_test`` DATABASE_URL (refusing to run otherwise) before
any other domovoi import — the same guard discipline as the core suite.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Env pinning + the _test-suffix refusal guard run at import time.
from domovoi.tests.conftest import (  # noqa: F401  (re-exported fixtures/markers)
    TABLES_TO_TRUNCATE,
    _isolate_admin_config_dir,
    requires_db,
)

import pytest
import pytest_asyncio
from sqlalchemy import text

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PLUGIN_DIR / "migrations"

# Unit tests import domovoi_plugin_radio directly (the loader inserts
# this path itself in the loaded_plugin tier).
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from domovoi import bootstrap                                  # noqa: E402
from domovoi.db.session import SessionLocal, engine            # noqa: E402
from domovoi.plugins_runtime.contracts import dry_run_winner   # noqa: E402
from domovoi.plugins_runtime.loader import LOADER              # noqa: E402
from domovoi.plugins_runtime.manifest import parse_manifest_dir  # noqa: E402
from domovoi.plugins_runtime.migrations import PluginMigrationRunner  # noqa: E402
from domovoi.sdk.facade import build_sdk                       # noqa: E402
from domovoi.sdk.testing import make_stub_sdk                  # noqa: E402

from domovoi_plugin_radio.settings import RadioSettings        # noqa: E402

PLUGIN_TABLES = [
    "plugin_radio.radio_detections",
    "plugin_radio.track_fingerprints",
    "plugin_radio.radio_stations",
]


@pytest.fixture(autouse=True)
def _dlls_registered():
    """The loader asserts the DLL bootstrap ran (design §4.1)."""
    bootstrap.register_nvidia_dlls()
    yield


@pytest.fixture(autouse=True)
def _fresh_client_singletons():
    """Reset the plugin clients' module-level singletons between tests
    so a test-injected stub can't leak into its neighbors."""
    yield
    from domovoi_plugin_radio.clients import (
        fcc_fm, icy_metadata, radio_browser, shazam_stream,
    )

    fcc_fm._set_fcc_fm_client_for_tests(None)
    icy_metadata._set_icy_client_for_tests(None)
    radio_browser._set_radio_browser_client_for_tests(None)
    shazam_stream._set_shazam_stream_client_for_tests(None)


@pytest.fixture
def radio_settings() -> RadioSettings:
    return RadioSettings()


@pytest.fixture
def stub_sdk(radio_settings):
    """No-DB, no-core PluginSDK double with the plugin's settings."""
    sdk = make_stub_sdk("radio")
    sdk.config = radio_settings
    sdk.now_playing.register_source("radio")
    return sdk


# ─── DB tier ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def plugin_db():
    """Apply the plugin's migrations to the test DB once per session
    (creates schema ``plugin_radio`` + its ledger). Sync fixture running
    its own loop — the runner creates and disposes its own engines."""
    runner = PluginMigrationRunner("radio", MIGRATIONS_DIR)
    asyncio.run(runner.apply_all())
    return runner


@pytest_asyncio.fixture
async def db_session(plugin_db):
    """Fresh session per test: core churn tables AND the plugin schema
    truncated first (the §6.3 per-schema truncation, plugin-local)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} "
                "RESTART IDENTITY CASCADE"
            )
        )
        await conn.execute(
            text(
                f"TRUNCATE {', '.join(PLUGIN_TABLES)} RESTART IDENTITY CASCADE"
            )
        )
    async with SessionLocal() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture
async def radio_sdk(db_session, radio_settings):
    """A REAL PluginSDK against the process singletons + test DB —
    the worker integration tier. Torn down after each test so
    registrations don't leak across tests."""
    sdk = build_sdk("radio", version="1.0.0", config=radio_settings)
    sdk.now_playing.register_source("radio")
    yield sdk
    sdk.teardown()


# ─── Loader tier (§13.1 ``loaded_plugin``) ───────────────────────────────


class LoadedPluginHelper:
    def __init__(self, loaded: Any) -> None:
        self.loaded = loaded

    def assert_routes(self, utterance: str, handler: str | None) -> None:
        """Route ``utterance`` through the merged registry dry-run and
        assert the winning handler (None ⇒ no fast path wins)."""
        from domovoi.handlers import HANDLERS

        winner = dry_run_winner(utterance, HANDLERS)
        assert winner == handler, (
            f"{utterance!r} routed to {winner!r}, expected {handler!r}"
        )


@pytest_asyncio.fixture
async def loaded_plugin(db_session):
    """The radio plugin loaded through the REAL loader: import,
    register(), §13.2 contract checks, worker registration (suppressed
    under stubs). Green here ⇒ the boot/install checks pass."""
    manifest = parse_manifest_dir(PLUGIN_DIR)
    lp = await LOADER.load_plugin(
        slug="radio",
        install_dir=PLUGIN_DIR,
        manifest=manifest,
        foreign_corpus=[],
        update_registry_status=False,
    )
    try:
        yield LoadedPluginHelper(lp)
    finally:
        await LOADER.unload_plugin("radio")


# ─── Web tier (§13.1 ``web_harness``) ────────────────────────────────────


class RecordingCoreClient:
    """WebPluginContext.core (core-proxy client) double — records proxy calls and
    returns canned responses (plugin-relative paths resolved the way
    the real client does)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, Any] = {}

    def _resolve(self, path: str) -> str:
        return path if path.startswith("/") else f"/v1/plugins/radio/{path}"

    async def get(self, path: str, *, params: dict | None = None) -> Any:
        full = self._resolve(path)
        self.calls.append({"method": "GET", "path": full, "params": params})
        return self.responses.get(full, {})

    async def post(self, path: str, *, json: Any = None) -> Any:
        full = self._resolve(path)
        self.calls.append({"method": "POST", "path": full, "json": json})
        return self.responses.get(full, {})

    async def post_admin(
        self, path: str, *, json: Any = None, forward_auth: bool = True
    ) -> Any:
        full = self._resolve(path)
        self.calls.append(
            {"method": "POST_ADMIN", "path": full, "json": json,
             "forward_auth": forward_auth}
        )
        return self.responses.get(full, {})


class FakeWebPluginContext:
    """The §5.1 WebPluginContext shape, wired to the test DB with the
    plugin's search_path preset."""

    def __init__(self) -> None:
        import logging

        self.slug = "radio"
        self.log = logging.getLogger("webplugin.radio")
        self.core = RecordingCoreClient()
        self.routers: list[Any] = []

    @asynccontextmanager
    async def db_session_scope(self):
        async with SessionLocal() as s:
            async with s.begin():
                await s.execute(
                    text('SET LOCAL search_path = "plugin_radio", public')
                )
                yield s

    def add_router(self, router: Any) -> None:
        self.routers.append(router)


@pytest.fixture
def web_ctx():
    return FakeWebPluginContext()


@pytest.fixture
def web_client(web_ctx, plugin_db):
    """TestClient with the plugin's web router mounted at its real
    prefix. Also runs the §3.2 step-5 import-hygiene tripwire so a
    web-module regression fails HERE, not at install."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from domovoi.plugins_runtime.manifest import check_web_import_hygiene
    from domovoi_plugin_radio import web as radio_web

    manifest = parse_manifest_dir(PLUGIN_DIR)
    hygiene = check_web_import_hygiene(PLUGIN_DIR, manifest)
    assert hygiene == [], f"web import hygiene violations: {hygiene}"

    radio_web.register_web(web_ctx)
    app = FastAPI()
    for router in web_ctx.routers:
        app.include_router(router, prefix="/api/plugins/radio")
    with TestClient(app) as client:
        client.ctx = web_ctx
        yield client
