"""The expected set: which containers should exist, in what state, running
what — at this moment.

Three sources, each with its own rules (docs/CONTAINER_GUARD.md, "What is
watched"):

* **The compose stack** (``domovoi/docker-compose.yml``), when this install
  runs it (:func:`compose_is_ours`). Postgres must run. Flyway is a
  one-shot: it may have exited 0 or be gone; only a recent non-zero exit
  counts. SearXNG must run only while the internet answer is Yes or
  Sometimes and the core manages it (``DOMOVOI_MANAGE_SEARXNG``). Letta
  must run only in chat mode. A service not expected right now is still
  Domovoi's ("known"): never reported missing, never "unknown".
* **The music players**: one container per ``mpd_rooms`` row, running the
  image the core built (its ID, recorded in ``container_guard_baselines``).
  A room the provisioner is starting, warming or retiring is held.
* **Plugins**: every ``[[containers]]`` entry of every LOADED plugin, as
  its ``ctx.add_container`` resolver reports it right now. A disabled or
  failed plugin's containers are known, not expected. A plugin the
  installer is working on is held.

The pure builders take plain values so the rules are testable without
Docker, the database or a loaded plugin; :func:`gather` collects those
values from the running core.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from domovoi.container_guard import holds
from domovoi.container_guard.compose import ComposeService
from domovoi.container_guard.model import (
    ALLOWANCES,
    KNOWN,
    ONESHOT,
    RUNNING,
    STOPPED,
    Expected,
    ref_digest,
)

log = logging.getLogger(__name__)

# Services this compose file used to have. A box that ran them may still
# hold their containers (docker-compose.yml, "Office engines: RETIRED"):
# they are Domovoi's, not unknown.
RETIRED_COMPOSE_SERVICES = frozenset({"onlyoffice", "collabora"})

# The compose Postgres's published address: a DATABASE_URL pointing here
# means this install runs the compose stack (compose_is_ours "auto").
COMPOSE_PG_PORT = 6432
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

# A plugin that says it is busy is believed this long, then checked anyway.
PLUGIN_BUSY_MAX_SEC = 30 * 60.0
# ...and past that while the image it is moving to is still being pulled,
# up to this long in all: a pull that never finishes doesn't hold forever.
PLUGIN_PULL_MAX_SEC = 6 * 3600.0
# How long a plugin's resolver may take before the guard holds its
# container instead ("the plugin couldn't say").
RESOLVER_TIMEOUT_SEC = 5.0

_CORE_SERVICE_LABELS = {
    "postgres": "the database",
    "flyway": "the database migrations",
    "flyway-test": "the test database migrations",
    "searxng": "the web search helper",
    "letta": "the chat mode server",
}


@dataclass
class ExpectedSet:
    """Everything one check compares Docker against."""

    containers: dict[str, Expected] = field(default_factory=dict)
    # The compose project whose containers are Domovoi's (None: this
    # install doesn't run the compose stack).
    compose_project: str | None = None
    compose_services: frozenset[str] = frozenset()
    # The music player prefix and image (the tag the core builds, and the
    # image ID it recorded when it built or adopted it).
    mpd_prefix: str | None = None
    mpd_tag_ref: str | None = None
    mpd_image_id: str | None = None
    mpd_tag_hold: str | None = None
    # Every container name a plugin declares, whatever the plugin's state.
    plugin_names: frozenset[str] = frozenset()
    # Plugins that need Docker but declare no containers (not watched).
    unwatched_plugins: tuple[str, ...] = ()

    def add(self, items: Iterable[Expected]) -> None:
        for e in items:
            # The first claim wins; the core's own are added first.
            self.containers.setdefault(e.name, e)

    def claimed_names(self) -> frozenset[str]:
        return frozenset(self.containers) | self.plugin_names

    def prefixes(self) -> tuple[str, ...]:
        return (self.mpd_prefix,) if self.mpd_prefix else ()

    def image_refs(self) -> tuple[str, ...]:
        return (self.mpd_tag_ref,) if self.mpd_tag_ref else ()

    def anything_expected(self) -> bool:
        return any(e.state in (RUNNING, ONESHOT) for e in self.containers.values())


# ─── the compose stack ────────────────────────────────────────────────────

def compose_is_ours(setting: str | None, database_url: str | None) -> bool:
    """Whether this install runs ``domovoi/docker-compose.yml``.

    ``on`` / ``off`` say so outright. ``auto`` (the default) answers yes
    when the database is the compose Postgres's published address —
    loopback, port 6432 — which is how every documented install runs, and
    no for anything else: a native Postgres, or a throwaway test core on
    its own database that must not claim the real install's containers.
    """
    value = (setting or "auto").strip().lower()
    if value in ("on", "true", "yes", "1"):
        return True
    if value in ("off", "false", "no", "0"):
        return False
    try:
        parts = urlsplit(database_url or "")
        return (parts.hostname or "") in _LOOPBACK and parts.port == COMPOSE_PG_PORT
    except ValueError:
        return False


def compose_expectations(
    boot: dict[str, ComposeService],
    current: dict[str, ComposeService] | None = None,
    *,
    project: str,
    internet_policy: str,
    manage_searxng: bool,
    chat_mode: bool,
    searxng_hold: str | None = None,
) -> list[Expected]:
    """One :class:`Expected` per compose service.

    ``boot`` is the compose file as it was when this process started (what
    the running containers were made from); ``current`` the file on disk
    now. A pull that moves an image pin changes ``current`` before the
    update unit recreates the container, so a digest from EITHER is
    accepted: the swap a pull is about to make never reads as tampering.
    """
    current = current or {}
    out: list[Expected] = []
    for service in sorted(set(boot) | set(current)):
        spec = boot.get(service) or current[service]
        other = current.get(service)
        name = spec.container_name or f"{project}-{service}-1"
        digests = {d for d in (ref_digest(spec.image), ref_digest(other.image if other else None)) if d}
        label = _CORE_SERVICE_LABELS.get(service, f"the {service} helper")
        state, required, hold = KNOWN, False, None
        if service == "postgres":
            state, required = RUNNING, True
        elif service == "flyway":
            state = ONESHOT
        elif service == "searxng":
            if manage_searxng and internet_policy in ("always", "sometimes"):
                state = RUNNING
            hold = searxng_hold
        elif service == "letta":
            if chat_mode:
                state = RUNNING
        out.append(
            Expected(
                name=name,
                owner="core",
                role=service if service in _CORE_SERVICE_LABELS else "compose",
                label=label,
                state=state,
                required=required,
                image_ref=spec.image,
                image_digests=frozenset(digests),
                hold=hold,
                compose_service=service,
            )
        )
    return out


# ─── the music players ────────────────────────────────────────────────────

def mpd_expectations(
    rooms: Iterable[tuple[str, str]],
    *,
    tag_ref: str,
    image_id: str | None,
    warm_done: bool,
) -> list[Expected]:
    """One running music player per ``(room_id, container_name)`` row."""
    out: list[Expected] = []
    for room_id, container in rooms:
        hold = holds.held(f"room:{room_id}")
        if hold is None and not warm_done:
            hold = "the music players are starting with the core"
        out.append(
            Expected(
                name=container,
                owner="core",
                role="mpd",
                label=f"the music player for {room_id}",
                state=RUNNING,
                required=True,
                image_ref=tag_ref,
                image_id=image_id,
                hold=hold,
            )
        )
    return out


# ─── plugins ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DeclaredContainer:
    """A ``[[containers]]`` entry as the registry's manifest JSONB holds it
    (validated at install; read leniently here)."""

    id: str
    name: str
    image: str
    required: bool = True
    label: str = ""
    allow: frozenset[str] = frozenset()


def declared_containers(manifest: dict[str, Any] | None) -> list[DeclaredContainer]:
    out: list[DeclaredContainer] = []
    for c in (manifest or {}).get("containers", []) or []:
        if not isinstance(c, dict):
            continue
        cid, name, image = c.get("id"), c.get("name"), c.get("image")
        if not (isinstance(cid, str) and isinstance(name, str) and isinstance(image, str)):
            continue
        allow = c.get("allow") or []
        out.append(
            DeclaredContainer(
                id=cid,
                name=name,
                image=image,
                required=c.get("required", True) is not False,
                label=str(c.get("label") or "").strip(),
                allow=frozenset(a for a in allow if isinstance(a, str)) & ALLOWANCES,
            )
        )
    return out


def needs_docker(manifest: dict[str, Any] | None) -> bool:
    """The manifest lists ``docker`` among its system requirements."""
    reqs = (manifest or {}).get("requirements") or {}
    for entry in reqs.get("system", []) or []:
        if isinstance(entry, dict) and str(entry.get("tool", "")).lower() == "docker":
            return True
    return False


# (slug, container id) -> monotonic time the plugin first said "busy"
_BUSY_SINCE: dict[tuple[str, str], float] = {}
# (slug, container id) -> monotonic time its resolver started failing
_FAILING_SINCE: dict[tuple[str, str], float] = {}

# A resolver that keeps raising this long stops holding its container: the
# container is shown as "not watched: its resolver keeps failing" and only
# its repository and settings are checked (L5: every hold ends on its own).
RESOLVER_FAIL_MAX_SEC = 30 * 60.0


def failing_resolvers(now: float | None = None) -> list[dict[str, Any]]:
    """``[{slug, container, for_sec}]``: resolvers past the bound (status)."""
    now = time.monotonic() if now is None else now
    return [
        {"slug": slug, "container": cid, "for_sec": round(now - since)}
        for (slug, cid), since in sorted(_FAILING_SINCE.items())
        if now - since >= RESOLVER_FAIL_MAX_SEC
    ]


def plugin_expectation(
    slug: str,
    plugin_name: str,
    decl: DeclaredContainer,
    reported: Any,
    *,
    resolver_failed: bool = False,
    pending_restart: bool = False,
    image_pulling: bool = False,
    now: float | None = None,
) -> Expected | None:
    """The :class:`Expected` for one declared container of a LOADED plugin,
    given what its resolver reported (a ContainerExpectation, or None for
    "no resolver: the manifest's values"). None when the plugin says it
    doesn't manage the container right now.

    * ``pending_restart`` — the plugin was upgraded and its new code loads
      at the next restart; the manifest the registry holds is already the
      new one while the old code runs, so nothing is judged until then (M2).
    * ``image_pulling`` — the image it is moving to isn't local yet: a busy
      plugin is believed past the 30-minute cap while the pull goes on, up
      to 6 hours in all (L4).
    * a resolver that fails holds its container for 30 minutes, then the
      container is known but not expected: "not watched" on the status (L5).
    """
    now = time.monotonic() if now is None else now
    key = (slug, decl.id)
    name, image, state, busy, reason = decl.name, decl.image, RUNNING, False, None
    if resolver_failed:
        since = _FAILING_SINCE.setdefault(key, now)
        if now - since >= RESOLVER_FAIL_MAX_SEC:
            return Expected(
                name=decl.name, owner=f"plugin:{slug}", role="plugin",
                label=decl.label or f"the {plugin_name} container",
                state=KNOWN, required=decl.required, image_ref=decl.image,
                allow=decl.allow, repo_only=True,
            )
    else:
        _FAILING_SINCE.pop(key, None)
    if reported is not None:
        rstate = getattr(reported, "state", RUNNING)
        if rstate == "unmanaged":
            _BUSY_SINCE.pop(key, None)
            return None
        name = getattr(reported, "name", None) or decl.name
        image = getattr(reported, "image", None) or decl.image
        state = STOPPED if rstate == "stopped" else RUNNING
        busy = bool(getattr(reported, "busy", False))
        reason = getattr(reported, "reason", None)
    hold = holds.held(f"plugin:{slug}")
    if pending_restart:
        hold = hold or f"the {plugin_name} plugin's upgrade waits for a restart"
    if resolver_failed:
        hold = hold or f"the {plugin_name} plugin couldn't say what it expects"
    if busy:
        since = _BUSY_SINCE.setdefault(key, now)
        if now - since < PLUGIN_BUSY_MAX_SEC or (image_pulling and now - since < PLUGIN_PULL_MAX_SEC):
            hold = hold or reason or f"the {plugin_name} plugin is working on it"
    else:
        _BUSY_SINCE.pop(key, None)
    return Expected(
        name=name,
        owner=f"plugin:{slug}",
        role="plugin",
        label=decl.label or f"the {plugin_name} container",
        state=state,
        required=decl.required,
        image_ref=image,
        image_digests=frozenset({d} if (d := ref_digest(image)) else set()),
        allow=decl.allow,
        hold=hold,
    )


async def _resolve(resolver: Any) -> tuple[Any, bool]:
    """``(reported, failed)``: a resolver's answer, bounded in time."""
    if resolver is None:
        return None, False
    try:
        result = resolver()
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(result, timeout=RESOLVER_TIMEOUT_SEC)
        return result, False
    except Exception as e:  # noqa: BLE001 — a plugin's bug must not stop the guard
        log.warning("container guard: a plugin's container resolver failed: %s", e)
        return None, True


ImagePresent = Callable[[str], Awaitable["bool | None"]]


async def plugin_expectations(
    rows: Iterable[Any],
    loaded: dict[str, Any],
    *,
    pending_restart: Iterable[str] = (),
    image_present: ImagePresent | None = None,
) -> tuple[list[Expected], frozenset[str], tuple[str, ...]]:
    """``(expected, every declared name, unwatched slugs)``.

    ``rows`` are registry records (``slug``, ``name``, ``enabled``,
    ``status``, ``manifest``); ``loaded`` maps a slug to its loaded
    plugin (``.context.containers``); ``pending_restart`` the slugs whose
    upgrade waits for a restart; ``image_present`` answers whether an
    image is local yet (asked only for a busy plugin past its cap)."""
    expected: list[Expected] = []
    names: set[str] = set()
    unwatched: list[str] = []
    pending = set(pending_restart)
    for row in rows:
        manifest = getattr(row, "manifest", None) or {}
        decls = declared_containers(manifest)
        names.update(d.name for d in decls)
        status = getattr(row, "status", "")
        if not decls:
            if getattr(row, "enabled", False) and status != "uninstalled" and needs_docker(manifest):
                unwatched.append(row.slug)
            continue
        lp = loaded.get(row.slug)
        if lp is None:
            # Disabled, failed to load or uninstalled: its containers are
            # Domovoi's (a disabled plugin leaves its server running on
            # purpose) but nothing is expected of them. If one runs, it runs
            # the declared repository (its tag was the plugin's to choose).
            for d in decls:
                expected.append(
                    Expected(
                        name=d.name, owner=f"plugin:{row.slug}", role="plugin",
                        label=d.label or f"the {getattr(row, 'name', row.slug)} container",
                        state=KNOWN, required=d.required, image_ref=d.image,
                        allow=d.allow, repo_only=True,
                    )
                )
            continue
        resolvers = getattr(getattr(lp, "context", None), "containers", {}) or {}
        plugin_name = getattr(row, "name", row.slug)
        for d in decls:
            reported, failed = await _resolve(resolvers.get(d.id))
            pulling = False
            busy_since = _BUSY_SINCE.get((row.slug, d.id))
            if (
                image_present is not None and reported is not None
                and getattr(reported, "busy", False) and busy_since is not None
                and time.monotonic() - busy_since >= PLUGIN_BUSY_MAX_SEC
            ):
                # The image it is moving to (SDK 1.5 ``pulling``), else
                # the one it reports: still absent means still pulling.
                ref = getattr(reported, "pulling", None) or getattr(reported, "image", None) or d.image
                try:
                    pulling = (await image_present(ref)) is False
                except Exception:  # noqa: BLE001 — can't tell: the cap applies
                    pulling = False
            exp = plugin_expectation(
                row.slug, plugin_name, d, reported,
                resolver_failed=failed, pending_restart=row.slug in pending,
                image_pulling=pulling,
            )
            if exp is not None:
                names.add(exp.name)
                expected.append(exp)
            else:
                # Unmanaged right now: the household runs that server itself.
                names.discard(d.name)
    return expected, frozenset(names), tuple(sorted(unwatched))
