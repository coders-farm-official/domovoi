"""``domovoi-plugin.toml`` — parse + validate the FULL manifest spec (design §2.2).

Everything an installable plugin declares lives here: identity, entry
points, capabilities, pinned requirements (+ hashed lockfile), handler
and worker cross-check declarations, config env prefix, the permission
trust-warning surface, web pages/scripts/player sources, realtime
NOTIFY wiring, Android capability strings, and asset dir overrides.

Two layers:

* :func:`parse_manifest` — TOML text → :class:`PluginManifest`, raising
  :class:`ManifestError` on any spec violation that is decidable from
  the manifest alone.
* :func:`validate_plugin_dir` — directory-level checks (package layout,
  entry-point files, migration filename lint + gapless sequence) used by
  the installer's stage phase and the ``domovoi plugin`` CLI.
* :func:`check_web_import_hygiene` — the §3.2 step-5 AST tripwire: the
  web entry module (and the plugin-internal modules it imports) may
  import only ``domovoi.webkit``, stdlib, and declared requirements —
  never the core runtime or the plugin's own core entry module.

The **manifest/code drift rule** (§2.2) means most values here are
*cross-checked declarations* — code is authoritative; the install-time
contract checks (:mod:`.contracts`) fail hard on any value mismatch.
"""

from __future__ import annotations

import ast
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The core SDK semver the `domovoi_api` range is checked against.
from domovoi.sdk import API_VERSION

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
MIGRATION_FILE_RE = re.compile(r"^V(\d{3})__[A-Za-z0-9_]+\.sql$")
_PINNED_REQ_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\],-]*==[A-Za-z0-9.!+*]+$")
# Debian package-name charset (policy §5.6.1: lowercase alnum, +, -, .).
_APT_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
_SPECIFIER_RE = re.compile(r"^(>=|<=|==|!=|>|<|~=)\s*(\d+(?:\.\d+)*)$")

RESERVED_SLUGS = frozenset({"core", "domovoi", "admin", "test", "public"})

WORKER_KINDS = frozenset({"poll", "longrun", "startup"})
REQUIRES_NETWORK_VALUES = frozenset({"no", "degraded", "yes"})

# [[media_libraries]] (design §7.1): per-plugin id + the fixed base vocabulary
# the web-side resolver maps to an absolute path.
MEDIA_LIBRARY_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MEDIA_LIBRARY_BASES = frozenset(
    {"install_dir", "data_dir", "music_dir", "absolute", "config"}
)

# Plugin-usable priority bands (design §4.2 named ranges): everything in
# 100–999; 0–99 stays core-reserved. The greedy-catch-all ≥900 rule is a
# contract check (it needs the compiled regexes, not the manifest).
PLUGIN_BAND_MIN = 100
PLUGIN_BAND_MAX = 999


class ManifestError(ValueError):
    """A manifest (or plugin-dir layout) violates the §2.2 spec."""


# ─── domovoi_api range evaluation ───────────────────────────────────────────

def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def _cmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    # Pad to equal length so ">=1.0" matches "1.0.0".
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def api_range_satisfied(range_str: str, version: str = API_VERSION) -> bool:
    """Evaluate a PEP-440-style comma-separated specifier set (the subset
    plugin manifests need: ``>= <= == != > < ~=``) against ``version``.
    Raises :class:`ManifestError` on an unparseable specifier."""
    try:
        current = _version_tuple(version)
    except ValueError as e:  # pragma: no cover — API_VERSION is ours
        raise ManifestError(f"bad core version {version!r}: {e}") from e
    for clause in range_str.split(","):
        clause = clause.strip()
        if not clause:
            continue
        m = _SPECIFIER_RE.match(clause)
        if not m:
            raise ManifestError(
                f"domovoi_api: unparseable specifier {clause!r} "
                f"(supported: >=, <=, ==, !=, >, <, ~=)"
            )
        op, target_s = m.group(1), m.group(2)
        target = _version_tuple(target_s)
        c = _cmp(current, target)
        if op == ">=":
            ok = c >= 0
        elif op == "<=":
            ok = c <= 0
        elif op == "==":
            ok = c == 0
        elif op == "!=":
            ok = c != 0
        elif op == ">":
            ok = c > 0
        elif op == "<":
            ok = c < 0
        else:  # "~=" — ~=X.Y ⇒ >=X.Y,<X+1 ; ~=X.Y.Z ⇒ >=X.Y.Z,<X.Y+1
            if len(target) < 2:
                raise ManifestError(
                    f"domovoi_api: ~= needs at least two version components "
                    f"({clause!r})"
                )
            upper = target[:-2] + (target[-2] + 1,)
            ok = c >= 0 and _cmp(current, upper) < 0
        if not ok:
            return False
    return True


# ─── parsed-manifest dataclasses ────────────────────────────────────────────

@dataclass(frozen=True)
class HandlerDecl:
    name: str
    band: int
    requires_network: str = "no"
    chat_exposed: bool = False
    label: str = ""
    tone: str = "neutral"
    icon: str | None = None
    corpus: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkerDecl:
    name: str
    kind: str  # poll | longrun | startup


@dataclass(frozen=True)
class SystemRequirement:
    tool: str
    required: bool = False
    help: str = ""


@dataclass(frozen=True)
class WebPageDecl:
    route: str
    page: str
    nav_label: str
    nav_icon: str | None = None
    nav_order: int = 50
    badge: dict[str, str] | None = None


@dataclass(frozen=True)
class PlayerSourceDecl:
    kind: str
    stream_url_template: str


@dataclass(frozen=True)
class RealtimeDecl:
    notify_channel: str
    realtime_channel: str
    snapshot: str | None = None


@dataclass(frozen=True)
class MediaLibraryDecl:
    """A ``[[media_libraries]]`` entry (design §7.1): a browsable media root the
    Files tab exposes. Rides ``manifest.raw`` into ``plugins.manifest`` JSONB
    (no registry change); the web-side resolver reads it via
    ``fetch_plugin_rows()`` and maps ``base`` to an absolute path without
    importing plugin code."""

    id: str
    label: str
    base: str  # install_dir | data_dir | music_dir | absolute | config
    path: str  # base=config → the config KEY; else a rel/abs path
    icon: str = "puzzle"
    separator: str | None = None  # base=config only: split multi-path values
    read_only: bool = True
    extensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SatelliteDecl:
    """The optional ``[satellite]`` payload section: what this plugin wants
    installed ON the satellites (synced over ``/v1/satellite-plugins`` and
    baked into prepared media). ``post_install`` / ``apt_packages`` run as
    ROOT on every satellite — they require the ``satellite_root`` permission
    plus at least one ``permissions.warnings`` entry (the same honesty
    contract as subprocess/hardware), surfaced at install-confirm time."""

    apt_packages: tuple[str, ...] = ()
    pip_requirements: tuple[str, ...] = ()
    pip_lockfile: str | None = None
    files_dir: str | None = None
    post_install: str | None = None
    max_payload_mb: int = 64


@dataclass(frozen=True)
class PluginManifest:
    # [plugin]
    slug: str
    name: str
    version: str
    publisher: str
    license: str
    description: str
    domovoi_api: str
    homepage: str | None = None
    # [entry_points]
    entry_core: str = ""
    entry_web: str | None = None
    # [capabilities]
    provides: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    consumes_optional: tuple[str, ...] = ()
    # [requirements]
    python_requirements: tuple[str, ...] = ()
    lockfile: str | None = None
    system_requirements: tuple[SystemRequirement, ...] = ()
    # [[handlers]] / [[workers]]
    handlers: tuple[HandlerDecl, ...] = ()
    workers: tuple[WorkerDecl, ...] = ()
    # [config]
    env_prefix: str = ""
    # [permissions]
    permissions: dict[str, bool] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    # [web]
    web_scripts: tuple[str, ...] = ()
    web_pages: tuple[WebPageDecl, ...] = ()
    player_sources: tuple[PlayerSourceDecl, ...] = ()
    # [[realtime]]
    realtime: tuple[RealtimeDecl, ...] = ()
    # [[media_libraries]]
    media_libraries: tuple[MediaLibraryDecl, ...] = ()
    # [satellite]
    satellite: SatelliteDecl | None = None
    # [android]
    android_capabilities: tuple[str, ...] = ()
    # [assets]
    migrations_dir: str = "migrations"
    sounds_dir: str = "sounds"
    # The raw parsed TOML — persisted verbatim into plugins.manifest JSONB
    # (the web process and /api/capabilities read THIS, design §3.1/§8).
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def package_name(self) -> str:
        return f"domovoi_plugin_{self.slug}"


# ─── field helpers ──────────────────────────────────────────────────────────

def _req_str(table: dict, key: str, where: str, *, max_len: int | None = None) -> str:
    val = table.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ManifestError(f"{where}.{key} is required and must be a non-empty string")
    if max_len is not None and len(val) > max_len:
        raise ManifestError(f"{where}.{key} exceeds {max_len} chars")
    return val


def _opt_str(table: dict, key: str, where: str) -> str | None:
    val = table.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise ManifestError(f"{where}.{key} must be a string")
    return val


def _str_list(table: dict, key: str, where: str) -> tuple[str, ...]:
    val = table.get(key, [])
    if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
        raise ManifestError(f"{where}.{key} must be an array of strings")
    return tuple(val)


# ─── the parser ─────────────────────────────────────────────────────────────

def parse_manifest(toml_text: str) -> PluginManifest:
    """Parse + validate a ``domovoi-plugin.toml``. Raises ManifestError
    with a human-readable message on the FIRST violation found."""
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"manifest is not valid TOML: {e}") from e

    plugin = data.get("plugin")
    if not isinstance(plugin, dict):
        raise ManifestError("missing required [plugin] table")

    slug = _req_str(plugin, "slug", "plugin")
    if not SLUG_RE.match(slug):
        raise ManifestError(
            f"plugin.slug {slug!r} must match ^[a-z][a-z0-9_]{{1,31}}$"
        )
    if slug in RESERVED_SLUGS:
        raise ManifestError(f"plugin.slug {slug!r} is reserved")

    name = _req_str(plugin, "name", "plugin", max_len=64)
    version = _req_str(plugin, "version", "plugin")
    if not SEMVER_RE.match(version):
        raise ManifestError(f"plugin.version {version!r} must be strict semver X.Y.Z")
    publisher = _req_str(plugin, "publisher", "plugin")
    license_ = _req_str(plugin, "license", "plugin")
    description = _req_str(plugin, "description", "plugin")
    homepage = _opt_str(plugin, "homepage", "plugin")
    domovoi_api = _req_str(plugin, "domovoi_api", "plugin")
    if not api_range_satisfied(domovoi_api):
        raise ManifestError(
            f"plugin.domovoi_api {domovoi_api!r} is not satisfiable by this "
            f"core's SDK version {API_VERSION} — the plugin targets a "
            f"different Domovoi release"
        )

    entries = data.get("entry_points")
    if not isinstance(entries, dict):
        raise ManifestError("missing required [entry_points] table")
    entry_core = _req_str(entries, "core", "entry_points")
    expected_core = f"domovoi_plugin_{slug}.core"
    if entry_core != expected_core:
        raise ManifestError(
            f"entry_points.core must be {expected_core!r}, got {entry_core!r}"
        )
    entry_web = _opt_str(entries, "web", "entry_points")
    if entry_web is not None and entry_web != f"domovoi_plugin_{slug}.web":
        raise ManifestError(
            f"entry_points.web must be 'domovoi_plugin_{slug}.web', got {entry_web!r}"
        )

    caps = data.get("capabilities", {})
    if not isinstance(caps, dict):
        raise ManifestError("[capabilities] must be a table")
    provides = _str_list(caps, "provides", "capabilities")
    consumes = _str_list(caps, "consumes", "capabilities")
    consumes_optional = _str_list(caps, "consumes_optional", "capabilities")

    reqs = data.get("requirements", {})
    if not isinstance(reqs, dict):
        raise ManifestError("[requirements] must be a table")
    python_reqs = _str_list(reqs, "python", "requirements")
    for r in python_reqs:
        if not _PINNED_REQ_RE.match(r):
            raise ManifestError(
                f"requirements.python entry {r!r} must be an exact pin "
                f"(name==version) — non-pinned specs are rejected at install"
            )
    lockfile = _opt_str(reqs, "lockfile", "requirements")
    if python_reqs and lockfile is None:
        lockfile = "requirements.lock"  # documented default (§2.2)
    system_raw = reqs.get("system", [])
    if not isinstance(system_raw, list):
        raise ManifestError("requirements.system must be an array of tables")
    system: list[SystemRequirement] = []
    for entry in system_raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("tool"), str):
            raise ManifestError(
                "each requirements.system entry needs at least {tool = \"...\"}"
            )
        system.append(
            SystemRequirement(
                tool=entry["tool"],
                required=bool(entry.get("required", False)),
                help=str(entry.get("help", "")),
            )
        )

    handlers_raw = data.get("handlers", [])
    if not isinstance(handlers_raw, list):
        raise ManifestError("[[handlers]] must be an array of tables")
    handlers: list[HandlerDecl] = []
    for h in handlers_raw:
        if not isinstance(h, dict):
            raise ManifestError("each [[handlers]] entry must be a table")
        hname = _req_str(h, "name", "handlers")
        band = h.get("band")
        if not isinstance(band, int) or isinstance(band, bool):
            raise ManifestError(f"handlers.{hname}.band must be an integer")
        if not (PLUGIN_BAND_MIN <= band <= PLUGIN_BAND_MAX):
            raise ManifestError(
                f"handlers.{hname}.band {band} is outside the plugin-usable "
                f"ranges ({PLUGIN_BAND_MIN}-{PLUGIN_BAND_MAX}; 0-99 is core-reserved)"
            )
        rn = h.get("requires_network", "no")
        if rn not in REQUIRES_NETWORK_VALUES:
            raise ManifestError(
                f"handlers.{hname}.requires_network must be one of "
                f"{sorted(REQUIRES_NETWORK_VALUES)}, got {rn!r}"
            )
        label = _req_str(h, "label", f"handlers.{hname}")
        handlers.append(
            HandlerDecl(
                name=hname,
                band=band,
                requires_network=rn,
                chat_exposed=bool(h.get("chat_exposed", False)),
                label=label,
                tone=str(h.get("tone", "neutral")),
                icon=_opt_str(h, "icon", f"handlers.{hname}"),
                corpus=tuple(_str_list(h, "corpus", f"handlers.{hname}")),
            )
        )

    workers_raw = data.get("workers", [])
    if not isinstance(workers_raw, list):
        raise ManifestError("[[workers]] must be an array of tables")
    workers: list[WorkerDecl] = []
    for w in workers_raw:
        if not isinstance(w, dict):
            raise ManifestError("each [[workers]] entry must be a table")
        wname = _req_str(w, "name", "workers")
        kind = w.get("kind")
        if kind not in WORKER_KINDS:
            raise ManifestError(
                f"workers.{wname}.kind must be one of {sorted(WORKER_KINDS)}, "
                f"got {kind!r}"
            )
        workers.append(WorkerDecl(name=wname, kind=kind))

    config = data.get("config", {})
    if not isinstance(config, dict):
        raise ManifestError("[config] must be a table")
    env_prefix = config.get("env_prefix", f"{slug.upper()}_")
    if not isinstance(env_prefix, str) or not env_prefix:
        raise ManifestError("config.env_prefix must be a non-empty string")

    perms_raw = data.get("permissions", {})
    if not isinstance(perms_raw, dict):
        raise ManifestError("[permissions] must be a table")
    warnings = _str_list(perms_raw, "warnings", "permissions")
    permissions: dict[str, bool] = {}
    for key in (
        "network",
        "subprocess",
        "hardware",
        "filesystem_outside_data",
        "satellite_root",
    ):
        val = perms_raw.get(key, False)
        if not isinstance(val, bool):
            raise ManifestError(f"permissions.{key} must be a boolean")
        permissions[key] = val

    web = data.get("web", {})
    if not isinstance(web, dict):
        raise ManifestError("[web] must be a table")
    web_scripts = _str_list(web, "scripts", "web")
    pages_raw = web.get("pages", [])
    if not isinstance(pages_raw, list):
        raise ManifestError("[[web.pages]] must be an array of tables")
    web_pages: list[WebPageDecl] = []
    for p in pages_raw:
        if not isinstance(p, dict):
            raise ManifestError("each [[web.pages]] entry must be a table")
        badge = p.get("badge")
        if badge is not None and (
            not isinstance(badge, dict)
            or not isinstance(badge.get("endpoint"), str)
            or not isinstance(badge.get("key"), str)
        ):
            raise ManifestError("web.pages badge must be {endpoint = ..., key = ...}")
        nav_order = p.get("nav_order", 50)
        if not isinstance(nav_order, int) or isinstance(nav_order, bool):
            raise ManifestError("web.pages.nav_order must be an integer")
        web_pages.append(
            WebPageDecl(
                route=_req_str(p, "route", "web.pages"),
                page=_req_str(p, "page", "web.pages"),
                nav_label=_req_str(p, "nav_label", "web.pages"),
                nav_icon=_opt_str(p, "nav_icon", "web.pages"),
                nav_order=nav_order,
                badge=badge,
            )
        )
    sources_raw = web.get("player_sources", [])
    if not isinstance(sources_raw, list):
        raise ManifestError("[[web.player_sources]] must be an array of tables")
    player_sources = tuple(
        PlayerSourceDecl(
            kind=_req_str(s, "kind", "web.player_sources"),
            stream_url_template=_req_str(
                s, "stream_url_template", "web.player_sources"
            ),
        )
        for s in sources_raw
        if isinstance(s, dict) or _bad_table("web.player_sources")
    )

    realtime_raw = data.get("realtime", [])
    if not isinstance(realtime_raw, list):
        raise ManifestError("[[realtime]] must be an array of tables")
    realtime: list[RealtimeDecl] = []
    channel_prefix = f"plugin_{slug}_"
    for r in realtime_raw:
        if not isinstance(r, dict):
            raise ManifestError("each [[realtime]] entry must be a table")
        notify_channel = _req_str(r, "notify_channel", "realtime")
        if not notify_channel.startswith(channel_prefix):
            raise ManifestError(
                f"realtime.notify_channel {notify_channel!r} must start with "
                f"{channel_prefix!r}"
            )
        realtime.append(
            RealtimeDecl(
                notify_channel=notify_channel,
                realtime_channel=_req_str(r, "realtime_channel", "realtime"),
                snapshot=_opt_str(r, "snapshot", "realtime"),
            )
        )

    media_libraries_raw = data.get("media_libraries", [])
    if not isinstance(media_libraries_raw, list):
        raise ManifestError("[[media_libraries]] must be an array of tables")
    media_libraries: list[MediaLibraryDecl] = []
    seen_lib_ids: set[str] = set()
    for ml in media_libraries_raw:
        if not isinstance(ml, dict):
            raise ManifestError("each [[media_libraries]] entry must be a table")
        ml_id = _req_str(ml, "id", "media_libraries")
        if not MEDIA_LIBRARY_ID_RE.match(ml_id):
            raise ManifestError(
                f"media_libraries.id {ml_id!r} must match ^[a-z][a-z0-9_]{{0,31}}$"
            )
        if ml_id in seen_lib_ids:
            raise ManifestError(
                f"media_libraries.id {ml_id!r} is declared more than once "
                f"(must be unique per plugin)"
            )
        seen_lib_ids.add(ml_id)
        label = _req_str(ml, "label", f"media_libraries.{ml_id}")
        base = _req_str(ml, "base", f"media_libraries.{ml_id}")
        if base not in MEDIA_LIBRARY_BASES:
            raise ManifestError(
                f"media_libraries.{ml_id}.base {base!r} must be one of "
                f"{sorted(MEDIA_LIBRARY_BASES)}"
            )
        path = _req_str(ml, "path", f"media_libraries.{ml_id}")
        separator = _opt_str(ml, "separator", f"media_libraries.{ml_id}")
        read_only = ml.get("read_only", True)
        if not isinstance(read_only, bool):
            raise ManifestError(
                f"media_libraries.{ml_id}.read_only must be a boolean"
            )
        extensions = _str_list(ml, "extensions", f"media_libraries.{ml_id}")
        media_libraries.append(
            MediaLibraryDecl(
                id=ml_id,
                label=label,
                base=base,
                path=path,
                icon=_opt_str(ml, "icon", f"media_libraries.{ml_id}") or "puzzle",
                separator=separator,
                read_only=read_only,
                extensions=extensions,
            )
        )

    satellite_raw = data.get("satellite")
    satellite: SatelliteDecl | None = None
    if satellite_raw is not None:
        if not isinstance(satellite_raw, dict):
            raise ManifestError("[satellite] must be a table")
        apt_packages = _str_list(satellite_raw, "apt_packages", "satellite")
        for pkg_name_ in apt_packages:
            if not _APT_PKG_RE.match(pkg_name_):
                raise ManifestError(
                    f"satellite.apt_packages entry {pkg_name_!r} is not a valid "
                    f"Debian package name"
                )
        sat_pips = _str_list(satellite_raw, "pip_requirements", "satellite")
        for r in sat_pips:
            if not _PINNED_REQ_RE.match(r):
                raise ManifestError(
                    f"satellite.pip_requirements entry {r!r} must be an exact "
                    f"pin (name==version)"
                )
        sat_lock = _opt_str(satellite_raw, "pip_lockfile", "satellite")
        if sat_pips and not sat_lock:
            raise ManifestError(
                "satellite.pip_requirements declared without satellite."
                "pip_lockfile (hashed lockfile required, like [requirements])"
            )
        files_dir = _opt_str(satellite_raw, "files_dir", "satellite")
        post_install = _opt_str(satellite_raw, "post_install", "satellite")
        max_mb = satellite_raw.get("max_payload_mb", 64)
        if not isinstance(max_mb, int) or isinstance(max_mb, bool) or max_mb < 1:
            raise ManifestError("satellite.max_payload_mb must be a positive integer")
        # Root-on-satellite honesty contract: installing system packages or
        # running a post_install script is arbitrary root code on every
        # satellite — the plugin must say so, loudly.
        if (apt_packages or post_install) and not permissions.get("satellite_root"):
            raise ManifestError(
                "satellite.apt_packages / satellite.post_install require "
                "permissions.satellite_root = true"
            )
        if permissions.get("satellite_root") and not warnings:
            raise ManifestError(
                "permissions.satellite_root requires at least one "
                "permissions.warnings entry explaining what runs on satellites"
            )
        if not (apt_packages or sat_pips or files_dir or post_install):
            raise ManifestError(
                "[satellite] is present but declares nothing — remove the "
                "table or declare a payload"
            )
        satellite = SatelliteDecl(
            apt_packages=apt_packages,
            pip_requirements=sat_pips,
            pip_lockfile=sat_lock,
            files_dir=files_dir,
            post_install=post_install,
            max_payload_mb=max_mb,
        )

    android = data.get("android", {})
    if not isinstance(android, dict):
        raise ManifestError("[android] must be a table")
    android_caps = _str_list(android, "capabilities", "android")

    assets = data.get("assets", {})
    if not isinstance(assets, dict):
        raise ManifestError("[assets] must be a table")
    migrations_dir = assets.get("migrations_dir", "migrations")
    sounds_dir = assets.get("sounds_dir", "sounds")
    if not isinstance(migrations_dir, str) or not isinstance(sounds_dir, str):
        raise ManifestError("assets.migrations_dir / assets.sounds_dir must be strings")

    return PluginManifest(
        slug=slug,
        name=name,
        version=version,
        publisher=publisher,
        license=license_,
        description=description,
        domovoi_api=domovoi_api,
        homepage=homepage,
        entry_core=entry_core,
        entry_web=entry_web,
        provides=provides,
        consumes=consumes,
        consumes_optional=consumes_optional,
        python_requirements=python_reqs,
        lockfile=lockfile,
        system_requirements=tuple(system),
        handlers=tuple(handlers),
        workers=tuple(workers),
        env_prefix=env_prefix,
        permissions=permissions,
        warnings=warnings,
        web_scripts=web_scripts,
        web_pages=tuple(web_pages),
        player_sources=player_sources,
        realtime=tuple(realtime),
        media_libraries=tuple(media_libraries),
        satellite=satellite,
        android_capabilities=android_caps,
        migrations_dir=migrations_dir,
        sounds_dir=sounds_dir,
        raw=data,
    )


def _bad_table(where: str) -> bool:
    raise ManifestError(f"each [[{where}]] entry must be a table")


def parse_manifest_dir(root: Path) -> PluginManifest:
    """Parse + fully validate a plugin DIRECTORY: manifest + layout."""
    manifest_path = root / "domovoi-plugin.toml"
    if not manifest_path.is_file():
        raise ManifestError(f"no domovoi-plugin.toml at {root}")
    manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    errors = validate_plugin_dir(root, manifest)
    if errors:
        raise ManifestError("; ".join(errors))
    return manifest


# ─── directory-level validation ─────────────────────────────────────────────

def validate_plugin_dir(root: Path, manifest: PluginManifest) -> list[str]:
    """Layout checks (§3.2 step 4): package dir is exactly
    ``domovoi_plugin_<slug>``, entry-point files exist, migration files
    match the filename lint and form a gapless V001.. sequence, declared
    lockfile exists. Returns a list of error strings (empty = OK)."""
    errors: list[str] = []
    pkg = root / manifest.package_name
    if not pkg.is_dir():
        errors.append(f"missing required package dir {manifest.package_name}/")
    else:
        if not (pkg / "__init__.py").is_file():
            errors.append(f"{manifest.package_name}/__init__.py missing")
        if not (pkg / "core.py").is_file():
            errors.append(
                f"core entry module {manifest.package_name}/core.py missing"
            )
        if manifest.entry_web and not (pkg / "web.py").is_file():
            errors.append(
                f"web entry module {manifest.package_name}/web.py declared "
                f"but missing"
            )
    # Stray extra top-level python packages would collide on sys.path.
    for child in root.iterdir() if root.is_dir() else ():
        if (
            child.is_dir()
            and (child / "__init__.py").is_file()
            and child.name != manifest.package_name
        ):
            errors.append(
                f"unexpected top-level python package {child.name}/ — the only "
                f"package allowed at the plugin root is {manifest.package_name}/"
            )

    migrations = root / manifest.migrations_dir
    if migrations.is_dir():
        versions: list[int] = []
        for f in sorted(migrations.iterdir()):
            if not f.is_file():
                errors.append(f"migrations/{f.name} is not a regular file")
                continue
            m = MIGRATION_FILE_RE.match(f.name)
            if not m:
                errors.append(
                    f"migration filename {f.name!r} must match V###__name.sql"
                )
                continue
            versions.append(int(m.group(1)))
        versions.sort()
        if versions and versions != list(range(1, len(versions) + 1)):
            errors.append(
                f"migration versions must be gapless from V001 — found "
                f"{['V%03d' % v for v in versions]}"
            )

    if manifest.python_requirements:
        lock = root / (manifest.lockfile or "requirements.lock")
        if not lock.is_file():
            errors.append(
                f"requirements.python declared but lockfile {lock.name!r} is "
                f"missing (pip-compile --generate-hashes output required)"
            )
        else:
            lock_text = lock.read_text(encoding="utf-8", errors="replace")
            if "--hash=" not in lock_text:
                errors.append(
                    f"lockfile {lock.name!r} carries no --hash= entries — "
                    f"install runs pip with --require-hashes and would reject "
                    f"every dist"
                )
            # Every direct dep must appear in the lockfile at the same version.
            lock_lower = lock_text.lower()
            for req in manifest.python_requirements:
                pin = req.split("[")[0].split("==")[0].lower() + "=="
                ver = req.split("==", 1)[1].lower()
                if f"{pin}{ver}" not in lock_lower.replace(" ", ""):
                    errors.append(
                        f"direct requirement {req!r} not found at that version "
                        f"in {lock.name}"
                    )

    sat = manifest.satellite
    if sat is not None:
        if sat.files_dir:
            fd = root / sat.files_dir
            if not fd.is_dir() or not _inside(root, fd):
                errors.append(
                    f"satellite.files_dir {sat.files_dir!r} missing or outside "
                    f"the plugin root"
                )
            else:
                total = 0
                for f in fd.rglob("*"):
                    if f.is_symlink():
                        errors.append(
                            f"satellite.files_dir contains a symlink "
                            f"({f.relative_to(root)}) — not allowed"
                        )
                        break
                    if f.is_file():
                        total += f.stat().st_size
                if total > sat.max_payload_mb * 1024 * 1024:
                    errors.append(
                        f"satellite.files_dir is {total // (1024 * 1024)} MiB — "
                        f"over the {sat.max_payload_mb} MiB payload cap"
                    )
        if sat.post_install:
            ps = root / sat.post_install
            if not ps.is_file() or not _inside(root, ps):
                errors.append(
                    f"satellite.post_install {sat.post_install!r} missing or "
                    f"outside the plugin root"
                )
            else:
                head = ps.read_text(encoding="utf-8", errors="replace")[:2]
                if head != "#!":
                    errors.append(
                        f"satellite.post_install {sat.post_install!r} must "
                        f"start with a shebang"
                    )
        if sat.pip_requirements:
            slock = root / (sat.pip_lockfile or "")
            if not sat.pip_lockfile or not slock.is_file():
                errors.append(
                    f"satellite.pip_lockfile {sat.pip_lockfile!r} is missing"
                )
            elif "--hash=" not in slock.read_text(encoding="utf-8", errors="replace"):
                errors.append(
                    f"satellite.pip_lockfile {sat.pip_lockfile!r} carries no "
                    f"--hash= entries"
                )
    return errors


def _inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ─── §3.2 step 5 — web-entry import hygiene (AST tripwire) ─────────────────

def check_web_import_hygiene(root: Path, manifest: PluginManifest) -> list[str]:
    """AST-walk ``web.py`` and every plugin-internal module it imports;
    reject any import resolving to ``domovoi.*`` outside ``domovoi.webkit``,
    or to the plugin's core entry module. This is a TRIPWIRE (design §5.1)
    — the web process's runtime meta_path guard is the real enforcement."""
    if not manifest.entry_web:
        return []
    pkg_name = manifest.package_name
    pkg_dir = root / pkg_name
    errors: list[str] = []
    seen: set[str] = set()
    queue: list[str] = ["web"]

    def module_path(rel_mod: str) -> Path | None:
        parts = rel_mod.split(".")
        as_file = pkg_dir.joinpath(*parts).with_suffix(".py")
        if as_file.is_file():
            return as_file
        as_pkg = pkg_dir.joinpath(*parts, "__init__.py")
        if as_pkg.is_file():
            return as_pkg
        return None

    while queue:
        rel_mod = queue.pop()
        if rel_mod in seen:
            continue
        seen.add(rel_mod)
        path = module_path(rel_mod)
        if path is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append(f"{pkg_name}.{rel_mod}: syntax error: {e}")
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                targets = [node.module]
                if node.module == pkg_name:
                    # `from domovoi_plugin_x import helpers` — the imported
                    # names may be submodules; walk them too.
                    targets += [f"{pkg_name}.{a.name}" for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level > 0:
                # relative import — plugin-internal; walk it too.
                base = rel_mod.split(".")[: -node.level] if node.level <= rel_mod.count(".") + 1 else []
                mod = ".".join(base + ([node.module] if node.module else []))
                if mod:
                    queue.append(mod)
                continue
            for target in targets:
                if target == "domovoi.webkit" or target.startswith("domovoi.webkit."):
                    continue
                if target == "domovoi" or target.startswith("domovoi."):
                    errors.append(
                        f"{pkg_name}.{rel_mod} imports {target!r} — web entry "
                        f"modules may import only domovoi.webkit, stdlib, and "
                        f"declared requirements (design §3.2 step 5)"
                    )
                elif target == f"{pkg_name}.core" or target.startswith(
                    f"{pkg_name}.core."
                ):
                    errors.append(
                        f"{pkg_name}.{rel_mod} imports the core entry module "
                        f"{target!r} — web.py must not transitively import core.py"
                    )
                elif target == pkg_name:
                    queue.append("__init__")
                elif target.startswith(f"{pkg_name}."):
                    queue.append(target.removeprefix(f"{pkg_name}."))
    return errors
