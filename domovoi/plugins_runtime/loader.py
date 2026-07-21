"""The plugin loader (design §3.7, §4.1) — core process only.

Import-order guarantees (dossier §7 invariant 1): plugin modules are
imported only inside :meth:`PluginLoader.load_plugin`, which asserts
``bootstrap.dlls_registered`` first — a refactor that reorders startup
fails loudly at boot, not silently at the first Whisper call.

Hot enable/disable never unloads modules (Python can't, and we don't
pretend to): the imported module stays cached; enable re-runs
``register()`` against a fresh :class:`PluginContext`, and disable is a
context teardown. **Code changes therefore need a core restart** —
stated plainly per design §3.4; the dev loop is ``domovoi plugin dev``
(§3.8).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi import bootstrap
from domovoi.config import settings
from domovoi.handlers import register_handler, unregister_handler, HANDLERS
from domovoi.handlers.base import Handler
from domovoi.plugin_http import mount_plugin_router, set_plugin_enabled
from domovoi.plugins_runtime import registry as reg
from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG, FieldSpec
from domovoi.plugins_runtime.contracts import ContractError, run_contract_checks
from domovoi.plugins_runtime.manifest import (
    ManifestError,
    PluginManifest,
    parse_manifest,
    validate_plugin_dir,
)
from domovoi.plugins_runtime.migrations import PluginMigrationRunner
from domovoi.plugins_runtime.workers import WORKERS, LongRunWorker, Worker
from domovoi.sdk.facade import PluginSDK, build_sdk

log = logging.getLogger(__name__)


def installed_root() -> Path:
    return Path.home() / ".domovoi" / "plugins" / "installed"


def bundled_root() -> Path:
    """Bundled plugins live at ``<repo>/plugins/`` (design §2.1) —
    ``settings.repo_dir`` is the repo root."""
    return Path(settings.repo_dir) / "plugins"


# Context providers (ctx.extras plumbing, design §4.1/§11): key → coroutine.
# The per-turn Context builder reads this registry; teardown removes a
# slug's keys.
CONTEXT_PROVIDERS: dict[str, tuple[str, Callable[[], Awaitable[Any]]]] = {}


class PluginContext:
    """The object handed to a plugin's ``register(ctx)`` (design §4.1).

    Everything registered through it is recorded against the slug, which
    is what makes disable a clean teardown (§3.4)."""

    def __init__(self, slug: str, sdk: PluginSDK) -> None:
        self.slug = slug
        self.sdk = sdk
        # Recorded registrations (the loader applies + tears these down).
        self.handlers: list[Handler] = []
        self.workers: list[Worker | LongRunWorker] = []
        self.routers: list[Any] = []
        self.capabilities: list[str] = []
        self.disable_callbacks: list[Callable[[], Awaitable[None]]] = []
        self._config_registered = False

    # ── voice plane ─────────────────────────────────────────────────────────

    def add_handler(self, handler: Handler) -> None:
        handler.plugin_slug = self.slug
        self.handlers.append(handler)
        register_handler(handler)

    # ── background work ─────────────────────────────────────────────────────

    def add_worker(self, worker: Worker | LongRunWorker) -> None:
        self.workers.append(worker)
        WORKERS.add_worker(
            worker, owner=self.slug, settings_source=self.sdk.config or settings
        )

    def add_startup_hook(
        self,
        fn: Callable[[], Awaitable[None]],
        *,
        name: str,
        requires_online: bool = False,
        after: str | None = None,
    ) -> None:
        WORKERS.add_startup_hook(
            fn, owner=self.slug, name=name,
            requires_online=requires_online, after=after,
        )

    # ── HTTP ────────────────────────────────────────────────────────────────

    def add_router(self, router: Any) -> None:
        """Queued; mounted at ``/v1/plugins/<slug>`` (behind the enable +
        default-DENY auth gates, §4.11) once the loader has an app."""
        self.routers.append(router)

    # ── capabilities ────────────────────────────────────────────────────────

    def add_capability(self, name: str, impl: object) -> None:
        from domovoi.capabilities import CAPABILITIES

        CAPABILITIES.register(name, impl, slug=self.slug)
        self.capabilities.append(name)

    # ── config ──────────────────────────────────────────────────────────────

    def add_config(self, model: type, fields: list[FieldSpec]) -> None:
        instance = PLUGIN_CONFIG.register(self.slug, model, fields)
        self.sdk.config = instance
        self._config_registered = True

    def on_reapply(self, field_name: str, cb: Callable[[], None]) -> None:
        PLUGIN_CONFIG.on_reapply(self.slug, field_name, cb)

    # ── lifecycle / misc ────────────────────────────────────────────────────

    def on_disable(self, cb: Callable[[], Awaitable[None]]) -> None:
        self.disable_callbacks.append(cb)

    def add_canned_sounds(self, sounds: list[Any]) -> None:
        self.sdk.assets.add_canned_sounds(sounds)

    def add_context_provider(
        self, key: str, fn: Callable[[], Awaitable[Any]]
    ) -> None:
        existing = CONTEXT_PROVIDERS.get(key)
        if existing is not None and existing[0] != self.slug:
            raise ValueError(
                f"context provider key {key!r} already registered by "
                f"plugin {existing[0]!r}"
            )
        CONTEXT_PROVIDERS[key] = (self.slug, fn)

    # ── registered-capability introspection for the contract checks ────────

    def registered_capability_names(self) -> list[str]:
        """Plain slugs from add_capability + the colon-namespaced ones the
        dedicated SDK calls recorded (design §2.2 provides mapping)."""
        from domovoi.acquisitions import ACQUISITIONS
        from domovoi.now_playing import NOW_PLAYING

        names = list(self.capabilities)
        sources = getattr(NOW_PLAYING, "_sources", {})
        for source_slug, owner in sources.items():
            if owner == self.slug:
                names.append(f"now-playing-source:{source_slug}")
        matchers = getattr(NOW_PLAYING, "_matchers", {})
        if any(owner == self.slug for owner, _fn in matchers.values()):
            names.append("now-playing-matcher")
        availability = ACQUISITIONS.availability()
        if self.slug in getattr(availability, "fulfillers", []):
            names.append("media-acquisition-fulfiller")
        return names


@dataclass
class LoadedPlugin:
    slug: str
    manifest: PluginManifest
    install_dir: Path
    module: Any
    sdk: PluginSDK
    context: PluginContext
    degraded_reasons: list[str] = field(default_factory=list)


class PluginLoader:
    """Owns the set of live (loaded) plugins in the core process."""

    def __init__(self) -> None:
        self.loaded: dict[str, LoadedPlugin] = {}
        self._app: Any | None = None
        self.unregistered_dirs: list[str] = []   # dashboard flag (§3.7 step 3)

    def bind_app(self, app: Any) -> None:
        self._app = app
        # Mount routers of anything loaded before the app existed.
        for lp in self.loaded.values():
            for router in lp.context.routers:
                mount_plugin_router(app, lp.slug, router)

    # ── load / unload ────────────────────────────────────────────────────────

    async def load_plugin(
        self,
        *,
        slug: str,
        install_dir: Path,
        manifest: PluginManifest,
        foreign_corpus: list[tuple[str, str]] | None = None,
        update_registry_status: bool = True,
    ) -> LoadedPlugin:
        """Import + register + contract-check one plugin (install step 13,
        enable, boot). Raises on failure AFTER tearing its registrations
        back down; the caller decides registry status handling (default:
        write ``ok``/``load_error`` to the plugins row)."""
        if not bootstrap.dlls_registered:
            raise RuntimeError(
                "PluginLoader.load_plugin called before "
                "bootstrap.register_nvidia_dlls() — the import order is "
                "load-bearing (design §4.1); fix the startup sequence"
            )
        if slug in self.loaded:
            log.info("plugin %s already loaded — re-registering", slug)
            await self.unload_plugin(slug)

        install_dir = install_dir.resolve()
        dir_str = str(install_dir)
        if dir_str not in sys.path:
            sys.path.insert(0, dir_str)

        cuda_before = _cuda_initialized()
        t0 = time.monotonic()
        try:
            module = importlib.import_module(manifest.entry_core)
        except Exception as e:
            await self._record_status(
                slug, "load_error", f"import failed: {e}",
                update=update_registry_status,
            )
            raise
        import_seconds = time.monotonic() - t0
        cuda_initialized = _cuda_initialized() and not cuda_before

        register_fn = getattr(module, "register", None)
        if not callable(register_fn):
            msg = f"{manifest.entry_core} does not expose register(ctx)"
            await self._record_status(
                slug, "load_error", msg, update=update_registry_status
            )
            raise ContractError(slug, [msg])

        sdk = build_sdk(
            slug,
            version=manifest.version,
            data_dir=Path.home() / ".domovoi" / "plugins" / "data" / slug,
        )
        ctx = PluginContext(slug, sdk)
        try:
            result = register_fn(ctx)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            await self._teardown_context(slug, ctx, sdk)
            await self._record_status(
                slug, "load_error", f"register() raised: {e}",
                update=update_registry_status,
            )
            raise

        report = run_contract_checks(
            slug=slug,
            manifest=manifest,
            handlers=ctx.handlers,
            merged_registry=HANDLERS,
            worker_names=WORKERS.worker_names(slug),
            hook_names=WORKERS.hook_names(slug),
            capability_names=ctx.registered_capability_names(),
            foreign_corpus=foreign_corpus
            if foreign_corpus is not None
            else await self._foreign_corpus(exclude=slug),
            import_seconds=import_seconds,
            cuda_initialized=cuda_initialized,
        )
        for warning in report.warnings:
            log.warning("plugin %s: %s", slug, warning)
        if not report.ok():
            await self._teardown_context(slug, ctx, sdk)
            await self._record_status(
                slug, "load_error", " | ".join(report.errors),
                update=update_registry_status,
            )
            raise ContractError(slug, report.errors, report.warnings)

        # System-tool probe (§2.2 [requirements].system): missing+required
        # ⇒ plugin loads DEGRADED with the help text surfaced, never crashes.
        degraded = _probe_system_tools(manifest)

        # Mount routers / start workers now that the contract holds.
        if self._app is not None:
            for router in ctx.routers:
                mount_plugin_router(self._app, slug, router)
        set_plugin_enabled(slug, True)
        try:
            await WORKERS.start_owner(slug)
        except RuntimeError:
            # No running event loop (sync test contexts) — workers stay
            # registered and start when the lifespan calls start_owner.
            pass

        lp = LoadedPlugin(
            slug=slug,
            manifest=manifest,
            install_dir=install_dir,
            module=module,
            sdk=sdk,
            context=ctx,
            degraded_reasons=degraded,
        )
        self.loaded[slug] = lp
        status = "degraded" if degraded else "ok"
        await self._record_status(
            slug, status, "; ".join(degraded) or None,
            update=update_registry_status,
        )
        log.info(
            "plugin %s v%s loaded (%d handler(s), %d worker(s)%s)",
            slug, manifest.version, len(ctx.handlers), len(ctx.workers),
            " — DEGRADED" if degraded else "",
        )
        return lp

    async def unload_plugin(self, slug: str) -> None:
        """Full §3.4 teardown: on_disable hooks → workers → handlers →
        routers (404 gate) → SDK teardown (capabilities, subscriptions,
        stamps, open-enum values, canned sounds, state) → config."""
        lp = self.loaded.pop(slug, None)
        if lp is None:
            set_plugin_enabled(slug, False)
            return
        for cb in lp.context.disable_callbacks:
            try:
                await cb()
            except Exception as e:  # noqa: BLE001 — teardown isolation
                log.warning("plugin %s on_disable callback failed: %s", slug, e)
        await WORKERS.stop_owner(slug)
        WORKERS.remove_owner(slug)
        for handler in lp.context.handlers:
            unregister_handler(handler)
        set_plugin_enabled(slug, False)
        lp.sdk.teardown()
        PLUGIN_CONFIG.unregister(slug)
        for key in [k for k, (owner, _) in CONTEXT_PROVIDERS.items() if owner == slug]:
            del CONTEXT_PROVIDERS[key]
        log.info("plugin %s unloaded", slug)

    async def shutdown(self) -> None:
        for slug in list(self.loaded):
            await self.unload_plugin(slug)

    # ── §3.7 boot-time discovery ─────────────────────────────────────────────

    async def discover_and_load_all(self) -> None:
        """Scan bundled + installed dirs, reconcile with the registry
        table, and load every enabled plugin in slug order. Each plugin
        is exception-isolated — a failing plugin degrades to
        ``load_error`` without affecting others or the core."""
        if not bootstrap.dlls_registered:
            raise RuntimeError(
                "discover_and_load_all before register_nvidia_dlls() — "
                "design §4.1 import order"
            )

        rows = {r.slug: r for r in await reg.list_plugins()}

        # 1. Bundled plugins with NO registry row at all → auto-register
        #    (enabled, migrations applied). A status='uninstalled' tombstone
        #    is respected — never auto-re-registered (§3.5/§3.7).
        broot = bundled_root()
        if broot.is_dir():
            for child in sorted(broot.iterdir()):
                manifest_path = child / "domovoi-plugin.toml"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = parse_manifest(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    errors = validate_plugin_dir(child, manifest)
                    if errors:
                        raise ManifestError("; ".join(errors))
                except ManifestError as e:
                    log.error("bundled plugin at %s: invalid manifest: %s", child, e)
                    continue
                row = rows.get(manifest.slug)
                if row is None:
                    log.info("auto-registering bundled plugin %s", manifest.slug)
                    runner = PluginMigrationRunner(
                        manifest.slug, child / manifest.migrations_dir
                    )
                    await runner.apply_all()
                    await reg.insert_plugin(
                        slug=manifest.slug,
                        name=manifest.name,
                        version=manifest.version,
                        publisher=manifest.publisher,
                        license=manifest.license,
                        domovoi_api=manifest.domovoi_api,
                        enabled=True,
                        bundled=True,
                        install_source="bundled",
                        source_ref=None,
                        install_dir=str(child.resolve()),
                        manifest=manifest.raw,
                    )
                    rows = {r.slug: r for r in await reg.list_plugins()}

        # 2. Installed dirs with no registry row → ignored + flagged
        #    (hand-copying is not an install path, §3.4/§3.7).
        self.unregistered_dirs = []
        iroot = installed_root()
        if iroot.is_dir():
            registered_dirs = {
                str(Path(r.install_dir).resolve()) for r in rows.values()
            }
            for child in sorted(iroot.iterdir()):
                if child.is_dir() and str(child.resolve()) not in registered_dirs:
                    self.unregistered_dirs.append(str(child))
                    log.warning(
                        "unregistered plugin dir %s — not loaded (use "
                        "`domovoi plugin dev` or the dashboard install flow)",
                        child,
                    )

        # 3. Load enabled rows in slug order (deterministic — §3.7 step 4).
        for slug in sorted(rows):
            row = rows[slug]
            if not row.enabled or row.status == "uninstalled":
                set_plugin_enabled(slug, False)
                continue
            install_dir = Path(row.install_dir)
            if row.bundled:
                # A bundled plugin's home is defined by THIS checkout's
                # plugins/ dir, not by the absolute path stored when the row
                # was first registered — that path goes stale whenever the
                # repo moves (or the row was written from another checkout).
                current = (bundled_root() / slug).resolve()
                if current.is_dir() and str(current) != row.install_dir:
                    log.info(
                        "plugin %s: healing bundled install dir %s -> %s",
                        slug, row.install_dir, current,
                    )
                    await reg.update_plugin(slug, install_dir=str(current))
                    install_dir = current
            if not install_dir.is_dir():
                log.error("plugin %s: install dir %s missing", slug, install_dir)
                await reg.set_status(
                    slug, "load_error", f"install dir missing: {install_dir}"
                )
                continue
            try:
                manifest = parse_manifest(
                    (install_dir / "domovoi-plugin.toml").read_text(encoding="utf-8")
                )
                # Migration catch-up (a newer version may have been copied
                # in while disabled; checksum drift refuses the load).
                runner = PluginMigrationRunner(
                    slug, install_dir / manifest.migrations_dir
                )
                await runner.apply_all()
                await self.load_plugin(
                    slug=slug, install_dir=install_dir, manifest=manifest
                )
            except Exception as e:  # noqa: BLE001 — per-plugin isolation
                log.error("plugin %s failed to load: %s", slug, e)
                try:
                    await reg.set_status(slug, "load_error", str(e))
                except Exception:  # pragma: no cover
                    pass

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _foreign_corpus(self, *, exclude: str) -> list[tuple[str, str]]:
        """Manifest-declared corpus phrases of every ENABLED plugin except
        the candidate (design §13.2 check 2)."""
        corpus: list[tuple[str, str]] = []
        try:
            for row in await reg.list_plugins():
                if row.slug == exclude or not row.enabled:
                    continue
                for hdecl in row.manifest.get("handlers", []) or []:
                    for utterance in hdecl.get("corpus", []) or []:
                        corpus.append((utterance, hdecl.get("name", row.slug)))
        except Exception as e:  # noqa: BLE001 — DB-less unit contexts
            log.debug("foreign corpus unavailable: %s", e)
        return corpus

    async def _teardown_context(
        self, slug: str, ctx: PluginContext, sdk: PluginSDK
    ) -> None:
        """Rollback of a partially-registered context (register() raised or
        contract checks failed)."""
        try:
            await WORKERS.stop_owner(slug)
        except Exception:  # pragma: no cover
            pass
        WORKERS.remove_owner(slug)
        for handler in ctx.handlers:
            unregister_handler(handler)
        set_plugin_enabled(slug, False)
        sdk.teardown()
        PLUGIN_CONFIG.unregister(slug)
        for key in [k for k, (owner, _) in CONTEXT_PROVIDERS.items() if owner == slug]:
            del CONTEXT_PROVIDERS[key]

    async def _record_status(
        self, slug: str, status: str, last_error: str | None, *, update: bool
    ) -> None:
        if not update:
            return
        try:
            if status == "load_error":
                await reg.update_plugin(
                    slug, status=status, last_error=last_error, enabled=False
                )
            else:
                await reg.set_status(slug, status, last_error)
        except Exception as e:  # noqa: BLE001 — status write is best-effort
            log.debug("could not write plugin %s status: %s", slug, e)


def _cuda_initialized() -> bool:
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        return bool(torch.cuda.is_initialized())
    except Exception:  # pragma: no cover
        return False


def _probe_system_tools(manifest: PluginManifest) -> list[str]:
    """§2.2 [requirements].system: never blocks install; missing+required
    ⇒ degraded status with the help text surfaced."""
    import shutil

    reasons: list[str] = []
    for req in manifest.system_requirements:
        if shutil.which(req.tool) is None and req.required:
            reasons.append(
                f"required system tool {req.tool!r} not found"
                + (f" — {req.help}" if req.help else "")
            )
    return reasons


LOADER = PluginLoader()
