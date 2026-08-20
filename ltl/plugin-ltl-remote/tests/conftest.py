"""LTL Remote plugin test harness.

Two tiers, and the split is deliberate.

**Pure tier** — ``test_crypto``, ``test_framing``, ``test_pairing``,
``test_allowlist``. These import only the plugin's own I/O-free modules
and run with nothing but ``cryptography`` installed. They are where the
protocol and the access-control decisions are actually pinned down, so
they must stay runnable in any checkout, including one without a
Domovoi environment.

**Integrated tier** — ``test_manifest_and_load``, ``test_handler``,
``test_web_api``. These need Domovoi importable and, for some of them,
the test database. They mirror the harness the bundled radio plugin
uses: ``domovoi.tests.conftest`` is imported FIRST so ``USE_STUBS=true``
and the ``*_test`` database guard are pinned before any other Domovoi
import.

When Domovoi is not importable the integrated fixtures skip rather than
erroring at collection — a contributor working on the wire protocol
should not need a Postgres container to run the tests that cover it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PLUGIN_DIR / "migrations"

# Unit tests import domovoi_plugin_ltl_remote directly (the loader
# inserts this path itself in the loaded_plugin tier).
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

PLUGIN_TABLES = [
    "plugin_ltl_remote.remote_access_log",
    "plugin_ltl_remote.remote_devices",
    "plugin_ltl_remote.link_state",
]

# ─── Does this checkout have a Domovoi environment? ──────────────────────

try:  # noqa: SIM105
    # Env pinning + the _test-suffix refusal guard run at import time.
    from domovoi.tests.conftest import (  # noqa: F401
        TABLES_TO_TRUNCATE,
        requires_db,
    )

    HAVE_DOMOVOI = True
except Exception:  # noqa: BLE001 — any import failure means "pure tier only"
    HAVE_DOMOVOI = False
    TABLES_TO_TRUNCATE: list[str] = []

needs_domovoi = pytest.mark.skipif(
    not HAVE_DOMOVOI,
    reason="needs an installed Domovoi environment (pip install -e '.[dev]')",
)


# ─── Pure tier ───────────────────────────────────────────────────────────


class FakeSettings:
    """The subset of ``LtlRemoteSettings`` the allowlist reads.

    A stand-in rather than the real model so the allowlist tests do not
    drag in pydantic-settings — and so a test can flip one switch
    without constructing a whole settings object.
    """

    def __init__(self, **overrides: object) -> None:
        self.read_only = False
        self.allow_core_admin = True
        self.allow_media_streaming = True
        self.dashboard_origin = "http://127.0.0.1:6369"
        self.core_origin = "http://127.0.0.1:6370"
        self.max_request_body_mb = 32
        self.request_timeout_sec = 30.0
        self.stream_idle_timeout_sec = 300.0
        for key, value in overrides.items():
            setattr(self, key, value)


@pytest.fixture
def settings() -> FakeSettings:
    return FakeSettings()


# ─── Integrated tier ─────────────────────────────────────────────────────


@pytest.fixture
def plugin_settings():
    """The real settings model. Skips without Domovoi (pydantic-settings
    and the SDK's FieldSpec both come from the core install)."""
    if not HAVE_DOMOVOI:
        pytest.skip("needs an installed Domovoi environment")
    from domovoi_plugin_ltl_remote.settings import LtlRemoteSettings

    return LtlRemoteSettings()


@pytest.fixture
def stub_sdk(plugin_settings):
    """No-DB, no-core PluginSDK double with the plugin's settings and a
    generated identity in ``sdk.state`` — the shape ``register()`` leaves
    behind, without touching disk."""
    from domovoi.sdk.testing import make_stub_sdk

    from domovoi_plugin_ltl_remote import crypto

    sdk = make_stub_sdk("ltl_remote")
    sdk.config = plugin_settings
    sdk.state["identity"] = crypto.Identity(
        dh=crypto.generate_keypair(), sig=crypto.generate_keypair()
    )
    return sdk


@pytest.fixture(scope="session")
def plugin_db():
    """Apply the plugin's migrations to the test DB once per session
    (creates schema ``plugin_ltl_remote`` + its ledger)."""
    if not HAVE_DOMOVOI:
        pytest.skip("needs an installed Domovoi environment")
    import asyncio

    from domovoi.plugins_runtime.migrations import PluginMigrationRunner

    runner = PluginMigrationRunner("ltl_remote", MIGRATIONS_DIR)
    asyncio.run(runner.apply_all())
    return runner


@pytest.fixture
def web_ctx():
    """A ``WebPluginContext``-shaped double wired to the test DB with the
    plugin's search_path preset."""
    if not HAVE_DOMOVOI:
        pytest.skip("needs an installed Domovoi environment")
    import logging
    from contextlib import asynccontextmanager
    from typing import Any

    from sqlalchemy import text

    from domovoi.db.session import SessionLocal

    class RecordingCoreClient:
        """Records proxy calls and returns canned responses, resolving
        plugin-relative paths the way the real client does."""

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.responses: dict[str, Any] = {}

        def _resolve(self, path: str) -> str:
            return path if path.startswith("/") else f"/v1/plugins/ltl_remote/{path}"

        async def post_admin(
            self, path: str, *, json: Any = None, forward_auth: bool = True,
            request: Any = None,
        ) -> Any:
            full = self._resolve(path)
            self.calls.append({"path": full, "json": json, "request": request})
            return self.responses.get(full, {})

    class FakeWebPluginContext:
        def __init__(self) -> None:
            self.slug = "ltl_remote"
            self.log = logging.getLogger("webplugin.ltl_remote")
            self.core = RecordingCoreClient()
            self.routers: list[Any] = []

        @asynccontextmanager
        async def db_session_scope(self):
            async with SessionLocal() as s:
                async with s.begin():
                    await s.execute(
                        text('SET LOCAL search_path = "plugin_ltl_remote", public')
                    )
                    yield s

        def add_router(self, router: Any) -> None:
            self.routers.append(router)

    return FakeWebPluginContext()


@pytest.fixture
def web_client(web_ctx, plugin_db):
    """TestClient with the plugin's web router mounted at its real
    prefix. Also runs the install-time import-hygiene tripwire, so a
    web-module regression fails HERE rather than at install."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from domovoi.plugins_runtime.manifest import (
        check_web_import_hygiene,
        parse_manifest_dir,
    )

    from domovoi_plugin_ltl_remote import web as ltl_web

    manifest = parse_manifest_dir(PLUGIN_DIR)
    hygiene = check_web_import_hygiene(PLUGIN_DIR, manifest)
    assert hygiene == [], f"web import hygiene violations: {hygiene}"

    ltl_web.register_web(web_ctx)
    app = FastAPI()
    for router in web_ctx.routers:
        app.include_router(router, prefix="/api/plugins/ltl_remote")
    with TestClient(app) as client:
        client.ctx = web_ctx
        yield client
