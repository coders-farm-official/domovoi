"""Access layer for the ``plugins`` registry table (design §3.1).

The table is the single source of truth shared with the WEB process
(which reads the ``manifest`` JSONB and never imports this package).
Every mutation fires ``pg_notify('plugins_changed', <slug>)`` in the
same transaction — the commit-coupled NOTIFY invariant (dossier §7
inv. 8) — so the web dashboard's listener picks changes up live.

Status vocabulary (app-validated, no CHECK): ``ok`` | ``degraded`` |
``load_error`` | ``uninstalled`` — the last being the bundled-plugin
tombstone (§3.5) that blocks §3.7 auto-re-registration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.session import session_scope

VALID_STATUS = frozenset({"ok", "degraded", "load_error", "uninstalled"})
VALID_INSTALL_SOURCE = frozenset({"bundled", "zip", "github", "dev"})


@dataclass(frozen=True)
class PluginRecord:
    slug: str
    name: str
    version: str
    publisher: str | None
    license: str | None
    domovoi_api: str
    enabled: bool
    bundled: bool
    install_source: str
    source_ref: str | None
    install_dir: str
    manifest: dict[str, Any]
    pip_report: dict[str, Any] | None
    status: str
    last_error: str | None
    installed_at: datetime | None
    updated_at: datetime | None


def _row_to_record(row: Any) -> PluginRecord:
    manifest = row.manifest
    if isinstance(manifest, str):
        manifest = json.loads(manifest)
    pip_report = row.pip_report
    if isinstance(pip_report, str):
        pip_report = json.loads(pip_report)
    return PluginRecord(
        slug=row.slug, name=row.name, version=row.version,
        publisher=row.publisher, license=row.license,
        domovoi_api=row.domovoi_api, enabled=bool(row.enabled),
        bundled=bool(row.bundled), install_source=row.install_source,
        source_ref=row.source_ref, install_dir=row.install_dir,
        manifest=manifest or {}, pip_report=pip_report,
        status=row.status, last_error=row.last_error,
        installed_at=row.installed_at, updated_at=row.updated_at,
    )


_SELECT = (
    "SELECT slug, name, version, publisher, license, domovoi_api, enabled, "
    "bundled, install_source, source_ref, install_dir, manifest, pip_report, "
    "status, last_error, installed_at, updated_at FROM plugins"
)


async def _notify(s: AsyncSession, slug: str) -> None:
    await s.execute(
        text("SELECT pg_notify('plugins_changed', :slug)"), {"slug": slug}
    )


async def get_plugin(slug: str, *, session: AsyncSession | None = None) -> PluginRecord | None:
    async def _run(s: AsyncSession) -> PluginRecord | None:
        row = (
            await s.execute(text(_SELECT + " WHERE slug = :slug"), {"slug": slug})
        ).first()
        return _row_to_record(row) if row is not None else None

    if session is not None:
        return await _run(session)
    async with session_scope() as s:
        return await _run(s)


async def list_plugins() -> list[PluginRecord]:
    async with session_scope() as s:
        rows = (await s.execute(text(_SELECT + " ORDER BY slug"))).all()
    return [_row_to_record(r) for r in rows]


async def insert_plugin(
    *,
    slug: str,
    name: str,
    version: str,
    publisher: str | None,
    license: str | None,
    domovoi_api: str,
    enabled: bool,
    bundled: bool,
    install_source: str,
    source_ref: str | None,
    install_dir: str,
    manifest: dict[str, Any],
    pip_report: dict[str, Any] | None = None,
    status: str = "ok",
) -> None:
    if install_source not in VALID_INSTALL_SOURCE:
        raise ValueError(f"invalid install_source {install_source!r}")
    if status not in VALID_STATUS:
        raise ValueError(f"invalid plugin status {status!r}")
    async with session_scope() as s:
        await s.execute(
            text(
                """
                INSERT INTO plugins (slug, name, version, publisher, license,
                    domovoi_api, enabled, bundled, install_source, source_ref,
                    install_dir, manifest, pip_report, status)
                VALUES (:slug, :name, :version, :publisher, :license,
                    :domovoi_api, :enabled, :bundled, :install_source,
                    :source_ref, :install_dir,
                    CAST(:manifest AS JSONB), CAST(:pip_report AS JSONB), :status)
                """
            ),
            {
                "slug": slug, "name": name, "version": version,
                "publisher": publisher, "license": license,
                "domovoi_api": domovoi_api, "enabled": enabled,
                "bundled": bundled, "install_source": install_source,
                "source_ref": source_ref, "install_dir": install_dir,
                "manifest": json.dumps(manifest),
                "pip_report": json.dumps(pip_report) if pip_report is not None else None,
                "status": status,
            },
        )
        await _notify(s, slug)


async def update_plugin(slug: str, **fields: Any) -> None:
    """Update arbitrary columns (+ ``updated_at``) and NOTIFY. ``manifest``
    and ``pip_report`` dicts are JSON-encoded automatically."""
    if not fields:
        return
    if "status" in fields and fields["status"] not in VALID_STATUS:
        raise ValueError(f"invalid plugin status {fields['status']!r}")
    sets: list[str] = []
    params: dict[str, Any] = {"slug": slug}
    for key, value in fields.items():
        if key in ("manifest", "pip_report") and value is not None:
            sets.append(f"{key} = CAST(:{key} AS JSONB)")
            params[key] = json.dumps(value)
        else:
            sets.append(f"{key} = :{key}")
            params[key] = value
    sets.append("updated_at = now()")
    async with session_scope() as s:
        await s.execute(
            text(f"UPDATE plugins SET {', '.join(sets)} WHERE slug = :slug"),
            params,
        )
        await _notify(s, slug)


async def set_enabled(slug: str, enabled: bool) -> None:
    await update_plugin(slug, enabled=enabled)


async def set_status(slug: str, status: str, last_error: str | None = None) -> None:
    await update_plugin(slug, status=status, last_error=last_error)


async def delete_plugin(slug: str) -> None:
    async with session_scope() as s:
        await s.execute(text("DELETE FROM plugins WHERE slug = :slug"), {"slug": slug})
        await _notify(s, slug)


async def tombstone_plugin(slug: str) -> None:
    """§3.5 bundled-uninstall tombstone: never delete the row — mark it
    ``uninstalled`` + disabled so §3.7 discovery can't resurrect it."""
    await update_plugin(slug, status="uninstalled", enabled=False, last_error=None)


async def plugin_schema_exists(slug: str) -> bool:
    """Orphan detection for §3.2 step 6 — a ``plugin_<slug>`` schema with
    no registry row (leftover from a purge crash)."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = :schema"
                ),
                {"schema": f"plugin_{slug}"},
            )
        ).first()
    return row is not None


async def mirror_registered_value(
    domain: str, value: str, owner: str
) -> None:
    """Write-through of an open-enum registration into the informational
    ``registered_values`` table (§6.4)."""
    async with session_scope() as s:
        await s.execute(
            text(
                "INSERT INTO registered_values (domain, value, owner) "
                "VALUES (:domain, :value, :owner) ON CONFLICT DO NOTHING"
            ),
            {"domain": domain, "value": value, "owner": owner},
        )


async def unmirror_registered_values(owner: str) -> None:
    async with session_scope() as s:
        await s.execute(
            text("DELETE FROM registered_values WHERE owner = :owner"),
            {"owner": owner},
        )
