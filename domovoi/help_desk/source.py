"""The help index as a lookup source, and the plugin help files it reads.

:class:`DomovoiHelpSource` is the first source registered under
``domovoi.lookup.LOOKUP_SOURCE`` (class LOCAL: it never leaves the box, so
it needs no permission and works under internet "never"). The core
registers it at start-up (:func:`register_core_source`); the web process
uses its own instance with the plugin help it read from the database.

Plugin help (SDK 1.8): a plugin may name a short Markdown file in its
manifest (``[plugin] help = "HELP.md"``, checked by
``domovoi.plugins_runtime.manifest``). :func:`plugin_help_from_rows` reads
it for every enabled plugin from the ``plugins`` rows both processes
already hold: its install folder, its manifest. A file that is missing,
outside the plugin, too large or not UTF-8 is skipped with a log line.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

from domovoi.help_desk.index import (
    PLUGIN_HELP_MAX_BYTES,
    HelpIndex,
    PluginHelp,
    first_words,
    get_index,
)
from domovoi.lookup import LOCAL, LOOKUP_SOURCE, LookupDocument, LookupHit

log = logging.getLogger(__name__)

SLUG = "domovoi-help"
NAME = "Domovoi help"


def _page_link(manifest: dict[str, Any]) -> str:
    pages = ((manifest.get("web") or {}).get("pages")) or []
    for p in pages:
        if isinstance(p, dict) and isinstance(p.get("route"), str) and p["route"]:
            return f"#{p['route']}"
    return "#plugins"


def read_plugin_help(slug: str, name: str, install_dir: str | Path, manifest: dict[str, Any]) -> PluginHelp | None:
    """One plugin's help file, or None (no ``help`` declared, or unusable)."""
    rel = ((manifest or {}).get("plugin") or {}).get("help")
    if not isinstance(rel, str) or not rel.strip():
        return None
    root = Path(install_dir)
    try:
        path = (root / rel).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            log.warning("plugin %s: help file %r is missing or outside the plugin", slug, rel)
            return None
        data = path.read_bytes()[: PLUGIN_HELP_MAX_BYTES + 1]
        if len(data) > PLUGIN_HELP_MAX_BYTES:
            log.warning("plugin %s: help file %r is over %d bytes; skipped", slug, rel, PLUGIN_HELP_MAX_BYTES)
            return None
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as e:
        log.warning("plugin %s: help file %r unreadable: %s", slug, rel, e)
        return None
    return PluginHelp(slug=slug, name=name or slug, text=text, link=_page_link(manifest))


def plugin_help_from_rows(rows: Iterable[dict[str, Any]]) -> list[PluginHelp]:
    """Help for each enabled, working plugin in ``rows`` (``plugins`` rows:
    slug, name, enabled, status, install_dir, manifest)."""
    out: list[PluginHelp] = []
    for r in rows:
        if not r.get("enabled") or r.get("status") not in ("ok", "degraded"):
            continue
        manifest = r.get("manifest") or {}
        if isinstance(manifest, str):
            import json

            try:
                manifest = json.loads(manifest)
            except ValueError:
                continue
        h = read_plugin_help(str(r.get("slug")), str(r.get("name") or r.get("slug")),
                             str(r.get("install_dir") or ""), manifest)
        if h is not None:
            out.append(h)
    return out


async def plugin_rows(session: Any) -> list[dict[str, Any]]:
    """The ``plugins`` rows help reads (both processes share the table)."""
    from sqlalchemy import text

    try:
        res = await session.execute(text(
            "SELECT slug, name, version, enabled, status, install_dir, manifest FROM plugins ORDER BY slug"
        ))
        return [dict(r) for r in res.mappings().all()]
    except Exception as e:  # noqa: BLE001 — help without plugins beats no help
        log.warning("help: plugins could not be read: %s", e)
        return []


class DomovoiHelpSource:
    """Domovoi's manual, FAQ, troubleshooting, error codes and plugin help."""

    slug = SLUG
    name = NAME
    lookup_class = LOCAL

    def __init__(self, helps: Iterable[PluginHelp] = (), *, installed_plugins: set[str] | None = None,
                 index: HelpIndex | None = None) -> None:
        self._helps = tuple(helps)
        self._installed = installed_plugins
        self._index = index

    def index(self) -> HelpIndex:
        if self._index is not None:
            return self._index
        return get_index(self._helps, installed_plugins=self._installed)

    async def search(self, query: str, *, limit: int = 3) -> list[LookupHit]:
        hits = self.index().search(query, limit=limit)
        return [
            LookupHit(id=h.passage.id, title=h.passage.title, snippet=first_words(h.passage.text, 40),
                      url=h.passage.link, section=h.passage.section, score=h.score)
            for h in hits if h.confident()
        ]

    async def fetch(self, id: str) -> LookupDocument | None:
        p = self.index().get(id)
        if p is None:
            return None
        return LookupDocument(id=p.id, title=p.title, text=p.text, url=p.link, section=p.section)


# The core's own source: local without being registered as such.
from domovoi import lookup as _lookup  # noqa: E402

_lookup.mark_core_type(DomovoiHelpSource)


def register_core_source(registry: Any | None = None) -> DomovoiHelpSource:
    """Register the core's help source (slug "core"): the core reads
    plugin help through its own lookups, so this one is the manual and
    docs; the core handler builds its own with plugin help per turn."""
    from domovoi import lookup

    source = DomovoiHelpSource()
    lookup.register(source, slug=lookup.CORE_SLUG, core=True, registry=registry)
    return source
