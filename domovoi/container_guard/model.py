"""The shapes the container guard's parts hand each other.

Nothing here talks to Docker or the database: an :class:`Expected` says
what Domovoi expects of one container, an :class:`Observed` is what Docker
said about one, and a :class:`Finding` is one problem the rules found.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# Incident classes. A failure alerts and keeps everything else working; a
# tamper finding alerts AND freezes the risky operations until an admin
# clears it (domovoi/container_guard/freeze.py).
FAILURE = "failure"
TAMPER = "tamper"
CLASSES = frozenset({FAILURE, TAMPER})

# Severity of a failure: a required container is red, an optional one
# amber. Tamper is always "error".
SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"

# What an expectation demands.
RUNNING = "running"   # must exist and run: every failure rule applies
STOPPED = "stopped"   # Domovoi's, meant to be stopped right now: no failure
                      # rules, but if it runs it must run the right image
ONESHOT = "oneshot"   # runs, exits 0 and may be removed (Flyway)
KNOWN = "known"       # Domovoi's name with nothing expected of it right now
                      # (Letta while chat mode is off): never "unknown"
STATES = frozenset({RUNNING, STOPPED, ONESHOT, KNOWN})

# Security-relevant settings no Domovoi container has unless its
# declaration allows them (tamper rule T3). The vocabulary plugins use in
# a [[containers]] entry's ``allow`` list.
ALLOWANCES = frozenset({"privileged", "host_network", "host_pid", "docker_socket", "root_mount"})

# Container names the core's own containers use: a plugin may not declare
# them (domovoi/plugins_runtime/manifest.py). The compose services'
# container_name values, and the music player prefix's default.
RESERVED_CONTAINER_NAMES = frozenset({
    "domovoi-postgres", "domovoi-flyway", "domovoi-flyway-test",
    "domovoi-searxng", "domovoi-letta",
})
RESERVED_CONTAINER_PREFIXES = ("domovoi-mpd-",)
# A Docker container name.
CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_REF_DIGEST_RE = re.compile(r"@(sha256:[0-9a-f]{64})$")


def is_host_root(source: str, system_drive: str | None = None) -> bool:
    r"""A bind mount of the host's whole filesystem: ``/``, or on Windows the
    SYSTEM drive's root in any of the forms Docker Desktop reports it as
    (``C:\``, ``C:/``, ``/run/desktop/mnt/host/c``). Another drive's root
    is not: a music library at ``E:\`` is a household's own choice."""
    raw = (source or "").strip()
    if raw in ("/", "\\"):
        return True
    src = raw.replace("\\", "/").lower().rstrip("/")
    if src == "":
        return False
    drive = (system_drive or os.environ.get("SystemDrive") or "C:").strip()[:1].lower() or "c"
    return src in (
        f"{drive}:",
        f"/run/desktop/mnt/host/{drive}",
        f"/mnt/host/{drive}",
        f"/host_mnt/{drive}",
    )


def ref_digest(ref: str | None) -> str | None:
    """The ``sha256:…`` an image reference pins, or None for a tag-only
    reference (``jellyfin/jellyfin:latest``)."""
    m = _REF_DIGEST_RE.search(ref or "")
    return m.group(1) if m else None


def normalize_ref(ref: str | None) -> str:
    """An image reference without its digest and without the implicit
    registry and namespace Docker adds: ``docker.io/library/postgres:16``
    and ``postgres:16@sha256:…`` both become ``postgres:16``. A reference
    with no tag gets ``:latest``, which is what Docker runs for it."""
    value = (ref or "").strip()
    value = value.split("@", 1)[0]
    for prefix in ("docker.io/library/", "index.docker.io/library/", "docker.io/", "index.docker.io/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    last = value.rsplit("/", 1)[-1]
    if value and ":" not in last:
        value = f"{value}:latest"
    return value


def ref_repository(ref: str | None) -> str:
    """The repository an image reference names, without tag or digest:
    ``postgres:16@sha256:…`` and ``docker.io/library/postgres`` are both
    ``postgres``; ``localhost:5000/x:1`` is ``localhost:5000/x``."""
    value = normalize_ref(ref)
    head, slash, last = value.rpartition("/")
    if ":" in last:
        last = last.split(":", 1)[0]
    return f"{head}{slash}{last}"


def capitalized(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


@dataclass(frozen=True)
class Expected:
    """What Domovoi expects of one container right now."""

    name: str
    owner: str                     # "core" or "plugin:<slug>"
    role: str                      # postgres | flyway | searxng | letta | mpd | plugin | compose
    label: str                     # plain words: "the database", "the music player for kitchen"
    state: str                     # one of STATES
    required: bool = True
    # The image as declared (shown to an admin) and what may match it:
    # digests from a ``@sha256:`` pin (any of them), or one exact local
    # image ID (the music player image the core built). Neither: the
    # declared reference itself is compared (a tag-only pin).
    image_ref: str | None = None
    image_digests: frozenset[str] = frozenset()
    image_id: str | None = None
    # The music player image recorded before ``image_id`` (Domovoi's own
    # previous build, kept when a rebuild Domovoi vouched for moved the
    # baseline): a room still on it waits to be recreated, it isn't
    # tampering. The tag itself must be ``image_id``.
    image_id_previous: str | None = None
    allow: frozenset[str] = frozenset()
    # Why checks of this container wait right now (a room starting, a
    # plugin upgrading or recreating it). None: it is checked.
    hold: str | None = None
    compose_service: str | None = None
    # Compare the repository only (and that it came from a registry): a
    # disabled plugin's container, whose applied tag Domovoi can't know
    # while the plugin's code isn't loaded.
    repo_only: bool = False

    @property
    def pin(self) -> str:
        """``id`` (exact local image), ``digest``, ``repository`` (any tag of
        the declared repository: a disabled plugin's container, a resolver
        that keeps failing), ``tag`` or ``none``."""
        if self.image_id:
            return "id"
        if self.image_digests:
            return "digest"
        if self.repo_only and self.image_ref:
            return "repository"
        if self.image_ref:
            return "tag"
        return "none"

    @property
    def severity(self) -> str:
        return SEVERITY_ERROR if self.required else SEVERITY_WARN

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "role": self.role,
            "label": self.label,
            "state": self.state,
            "required": self.required,
            "image": self.image_ref,
            "image_id": self.image_id,
            "pin": self.pin,
            "allow": sorted(self.allow),
            "hold": self.hold,
        }


@dataclass(frozen=True)
class Observed:
    """What ``docker inspect`` said about one container."""

    id: str
    name: str
    status: str                    # running | exited | restarting | paused | created | dead | removing
    running: bool = False
    restarting: bool = False
    paused: bool = False
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    restart_count: int = 0
    health: str | None = None      # healthy | unhealthy | starting | None
    config_image: str = ""         # the reference it was created from
    image_id: str = ""             # sha256:… of the image it runs
    privileged: bool = False
    network_mode: str = ""
    pid_mode: str = ""
    mounts: tuple[tuple[str, str], ...] = ()   # (source, destination)
    labels: dict[str, str] = field(default_factory=dict, hash=False, compare=False)

    def security(self) -> list[str]:
        """The security-relevant settings this container runs with, in the
        :data:`ALLOWANCES` vocabulary."""
        out: list[str] = []
        if self.privileged:
            out.append("privileged")
        if (self.network_mode or "").lower() == "host":
            out.append("host_network")
        if (self.pid_mode or "").lower() == "host":
            out.append("host_pid")
        socket = root = False
        for source, dest in self.mounts:
            src = (source or "").strip().replace("\\", "/").lower()
            dst = (dest or "").strip().replace("\\", "/").lower()
            if src.endswith("docker.sock") or dst.endswith("docker.sock") or "docker_engine" in src:
                socket = True
            if is_host_root(source):
                root = True
        if socket:
            out.append("docker_socket")
        if root:
            out.append("root_mount")
        return out

    def brief(self) -> dict[str, Any]:
        """What an incident records as "found" for a container."""
        return {
            "id": self.id[:12],
            "status": self.status,
            "exit_code": self.exit_code,
            "health": self.health,
            "restart_count": self.restart_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "image": self.config_image,
            "image_id": self.image_id,
        }


@dataclass(frozen=True)
class Listed:
    """One line of ``docker ps -a``: enough to decide whether to inspect."""

    id: str
    name: str
    compose_project: str = ""
    compose_service: str = ""
    state: str = ""


@dataclass
class DockerView:
    """One look at Docker."""

    ok: bool
    error: str | None = None
    cli_missing: bool = False
    listed: dict[str, Listed] = field(default_factory=dict)
    containers: dict[str, Observed] = field(default_factory=dict)
    # image ID -> the repo digests Docker records for it
    repo_digests: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # image reference -> the local image ID it resolves to (None: absent)
    tag_ids: dict[str, str | None] = field(default_factory=dict)
    # image IDs whose repo digests couldn't be read this look (no data)
    unknown_images: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Finding:
    """One problem one check found. ``key`` names the distinct problem: one
    open incident per key."""

    key: str
    cls: str
    kind: str
    severity: str
    container: str | None
    owner: str
    label: str
    summary: str
    expected: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
    found: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
