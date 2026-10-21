"""The guard itself: one check = gather the expected set, look at Docker,
run the rules, debounce, and write incidents.

**Debounce — conservative about what counts, loud about what does.**

* Nothing is judged in the first ``container_guard_boot_grace_sec``
  (180 s) after the core starts, while a restart or update is under way
  (the update unit stops and starts everything), or for two minutes after
  Docker comes back from being unreachable (``restart: unless-stopped``
  containers are coming back up). Open incidents are left as they are.
* Held containers (a room starting, a plugin being installed, upgraded or
  recreating its container) are skipped (domovoi/container_guard/holds.py).
* A failure opens only after it was seen on two checks in a row AND has
  lasted ``container_guard_failure_grace_sec`` (120 s): a ``docker
  restart`` or a crash that ``restart: unless-stopped`` heals in seconds
  never pages anyone. Docker itself not answering needs
  ``container_guard_daemon_grace_sec`` (5 min): Docker Desktop restarting
  is not an outage.
* A tamper finding opens after two checks in a row at least
  ``container_guard_tamper_confirm_sec`` (25 s) apart.
* A failure resolves after two clean checks in a row, or at once when the
  container is no longer expected (its plugin was disabled, its room
  removed, chat mode turned off). A tamper incident never resolves by
  itself: an admin clears it. A cleared finding that is still there stays
  accepted instead of reopening; anything different opens anew.

When Docker doesn't answer the check backs off (doubling, at most five
minutes between looks) — cheap on a Pi, and Docker that is down for
longer than that is already an open incident.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi import git_run
from domovoi.container_guard import holds, store
from domovoi.container_guard.compose import ComposeService, parse_services, project_name, read_services
from domovoi.container_guard.detect import DOCKER_KEY, Detection, RestartMemory, detect
from domovoi.container_guard.docker_cli import Claimer, DockerCli
from domovoi.container_guard.expected import (
    ExpectedSet,
    compose_expectations,
    compose_is_ours,
    failing_resolvers,
    mpd_expectations,
    plugin_expectations,
)
from domovoi.container_guard.model import FAILURE, ONESHOT, RUNNING, TAMPER, Finding

log = logging.getLogger(__name__)

MPD_BASELINE_KEY = "mpd_image_id"
MAX_BACKOFF_SEC = 300.0
# Resolved incidents past the retention are pruned about once a day.
PRUNE_EVERY_SEC = 24 * 3600.0


@dataclass(frozen=True)
class GuardConfig:
    enabled: bool = True
    interval_sec: float = 30.0
    boot_grace_sec: float = 180.0
    failure_grace_sec: float = 120.0
    daemon_grace_sec: float = 300.0
    tamper_confirm_sec: float = 25.0
    docker_recover_sec: float = 120.0
    recover_checks: int = 2
    compose: str = "auto"

    @classmethod
    def from_settings(cls, s: Any | None = None) -> "GuardConfig":
        if s is None:
            from domovoi.config import settings as s  # noqa: N813
        return cls(
            enabled=bool(getattr(s, "container_guard_enabled", True)),
            interval_sec=float(getattr(s, "container_guard_interval_sec", 30.0)),
            boot_grace_sec=float(getattr(s, "container_guard_boot_grace_sec", 180.0)),
            failure_grace_sec=float(getattr(s, "container_guard_failure_grace_sec", 120.0)),
            daemon_grace_sec=float(getattr(s, "container_guard_daemon_grace_sec", 300.0)),
            tamper_confirm_sec=float(getattr(s, "container_guard_tamper_confirm_sec", 25.0)),
            compose=str(getattr(s, "container_guard_compose", "auto")),
        )


# ─── debounce ─────────────────────────────────────────────────────────────

@dataclass
class _Pending:
    finding: Finding
    first_seen: float
    count: int = 0


@dataclass
class Plan:
    """What one check writes."""

    to_open: list[Finding] = field(default_factory=list)
    to_touch: list[tuple[int, Finding, bool]] = field(default_factory=list)
    to_resolve: list[tuple[int, str]] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.to_open or self.to_touch or self.to_resolve or self.accepted)


class Debouncer:
    """Turns findings over time into opens, touches and resolutions."""

    def __init__(self, cfg: GuardConfig) -> None:
        self.cfg = cfg
        self.pending: dict[str, _Pending] = {}
        self.clean: dict[int, int] = {}

    def snapshot(self) -> tuple[dict, dict]:
        return copy.copy(self.pending), dict(self.clean)

    def restore(self, snap: tuple[dict, dict]) -> None:
        self.pending, self.clean = snap

    def _grace(self, f: Finding) -> float:
        if f.cls == TAMPER:
            return self.cfg.tamper_confirm_sec
        if f.kind == "daemon":
            return self.cfg.daemon_grace_sec
        return self.cfg.failure_grace_sec

    def plan(
        self,
        det: Detection,
        open_rows: list[dict[str, Any]],
        expected: ExpectedSet,
        now: float,
        accepted: set[str] | None = None,
    ) -> Plan:
        accepted = accepted or set()
        plan = Plan()
        by_key = {f.key: f for f in det.findings}
        open_by_key = {r["incident_key"]: r for r in open_rows}

        for key, row in open_by_key.items():
            iid = int(row["id"])
            f = by_key.get(key)
            if f is not None:
                plan.to_touch.append((iid, f, f.kind != row["kind"]))
                self.clean.pop(iid, None)
                continue
            if row["class"] == TAMPER:
                continue
            if key == DOCKER_KEY:
                if det.daemon_ok:
                    self._count_clean(iid, plan, "recovered")
                continue
            if not det.daemon_ok:
                continue
            if row.get("kind") == "outdated":
                # An older image on a container that may not be expected at
                # all (M4): it resolves when the guard has looked and the
                # container is gone, stopped or recreated on the pin.
                if row.get("container") in det.image_evaluated:
                    self._count_clean(iid, plan, "recovered")
                continue
            e = expected.containers.get(row.get("container") or "")
            if e is None or e.state not in (RUNNING, ONESHOT):
                plan.to_resolve.append((iid, "no_longer_expected"))
                self.clean.pop(iid, None)
                continue
            if e.name in det.evaluated:
                self._count_clean(iid, plan, "recovered")

        open_ids = {int(r["id"]) for r in open_rows}
        for iid in [i for i in self.clean if i not in open_ids]:
            del self.clean[iid]

        seen: set[str] = set()
        for f in det.findings:
            if f.key in open_by_key:
                continue
            if f.cls == TAMPER and f.key in accepted:
                plan.accepted.append(f.key)
                continue
            seen.add(f.key)
            p = self.pending.get(f.key)
            if p is None:
                p = self.pending[f.key] = _Pending(finding=f, first_seen=now)
            p.count += 1
            p.finding = f
            if p.count >= 2 and now - p.first_seen >= self._grace(f):
                plan.to_open.append(f)
                del self.pending[f.key]
        for key in [k for k in self.pending if k not in seen]:
            del self.pending[key]
        return plan

    def _count_clean(self, iid: int, plan: Plan, resolution: str) -> None:
        n = self.clean.get(iid, 0) + 1
        if n >= self.cfg.recover_checks:
            plan.to_resolve.append((iid, resolution))
            self.clean.pop(iid, None)
        else:
            self.clean[iid] = n


# ─── the guard ────────────────────────────────────────────────────────────

SessionFactory = Callable[[], Any]


def _default_session_factory() -> Any:
    from domovoi.db.session import session_scope

    return session_scope()


class ContainerGuard:
    """One per core process (:func:`get_guard`). Tests build their own with
    a fake Docker, a fixed clock and their own expected-set source."""

    def __init__(
        self,
        *,
        docker: Any | None = None,
        config: Callable[[], GuardConfig] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        session_factory: SessionFactory | None = None,
        gather: Callable[["ContainerGuard", GuardConfig], Awaitable[ExpectedSet]] | None = None,
        global_hold: Callable[["ContainerGuard", GuardConfig], Awaitable[str | None]] | None = None,
    ) -> None:
        self.docker = docker or DockerCli()
        self._config = config or GuardConfig.from_settings
        self.clock = clock
        self.wall = wall
        self.session_factory = session_factory or _default_session_factory
        self._gather = gather or gather_expected
        self._global_hold = global_hold or default_global_hold
        self.started_mono = clock()
        self.started_wall = wall()
        self.debouncer = Debouncer(self._config())
        self.memory = RestartMemory()
        self.compose_boot: dict[str, ComposeService] | None = None
        self._compose_now: tuple[float, dict[str, ComposeService]] | None = None
        self._git_cache: tuple[str, dict[str, ComposeService]] | None = None
        self._last_prune: float | None = None
        # What the boot found about the music player image (H1), for the status.
        self.mpd_note: str | None = None
        self._docker_was_ok = True
        self._last_listed: set[str] = set()
        self._fail_streak = 0
        self._next_due = 0.0
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self.pending_baseline: tuple[str, str] | None = None
        self._warned: dict[str, str] = {}
        self.status: dict[str, Any] = {"state": "starting", "checked_at": None}

    def _warn_once(self, what: str, message: str) -> None:
        """A database that stays away (or V024 not applied yet) is logged
        once, not every 30 seconds; the next success re-arms it."""
        if self._warned.get(what) != message:
            self._warned[what] = message
            log.warning("%s", message)
        else:
            log.debug("%s", message)

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    # ── the compose file, as it was at boot and as it is now ──

    def compose_file(self) -> Path:
        from domovoi.config import settings

        return Path(settings.repo_dir) / "domovoi" / "docker-compose.yml"

    def compose_services(self) -> tuple[dict[str, ComposeService], dict[str, ComposeService]]:
        """``(at boot, now)``: the compose file's services as COMMITTED —
        ``git show HEAD:domovoi/docker-compose.yml`` when this process
        started, and at HEAD now (a pull moves HEAD before the update unit
        recreates the containers, so both are accepted). An uncommitted
        edit of the file is ignored: editing a pin on disk and running
        ``compose up`` must not make another image look right (M5). Without
        git (a checkout that isn't a clone), the file on disk is all there
        is. Blocking (git): callers on the event loop use a thread."""
        committed = self._git_compose()
        if committed is not None:
            if self.compose_boot is None:
                self.compose_boot = committed
            return self.compose_boot, committed
        path = self.compose_file()
        if self.compose_boot is None:
            self.compose_boot = read_services(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return self.compose_boot, {}
        if self._compose_now is None or self._compose_now[0] != mtime:
            self._compose_now = (mtime, read_services(path))
        return self.compose_boot, self._compose_now[1]

    def _git_compose(self) -> dict[str, ComposeService] | None:
        """The compose file's services at HEAD, cached per HEAD; None when
        git can't say (no git, not a clone, the file not committed)."""
        repo = self.compose_file().parent.parent
        try:
            head = git_run.run(["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=10)
            if head.returncode != 0 or not head.stdout.strip():
                return None
            sha = head.stdout.strip()
            if self._git_cache is not None and self._git_cache[0] == sha:
                return self._git_cache[1]
            show = git_run.run(
                ["git", "-C", str(repo), "show", "HEAD:./domovoi/docker-compose.yml"], timeout=10
            )
            if show.returncode != 0 or not show.stdout.strip():
                return None
        except (OSError, subprocess.TimeoutExpired):
            return None
        services = parse_services(show.stdout)
        self._git_cache = (sha, services)
        return services

    # ── one check ──

    async def check(self, *, force: bool = False) -> dict[str, Any]:
        async with self._get_lock():
            return await self._check(force=force)

    async def _check(self, *, force: bool) -> dict[str, Any]:
        cfg = self._config()
        self.debouncer.cfg = cfg
        if not cfg.enabled:
            self.status = {"state": "disabled", "checked_at": None}
            return self.status
        now = self.clock()
        if not force and now < self._next_due:
            return self.status

        hold = await self._global_hold(self, cfg)
        db_ok = True
        try:
            expected = await self._gather(self, cfg)
        except Exception as e:  # noqa: BLE001 — the DB may be the thing that's down
            self._warn_once("read", f"container guard: could not read what Domovoi expects: {e}")
            db_ok = False
            expected = await gather_expected(self, cfg, with_db=False)
        view = await self.docker.observe(
            claim=Claimer(
                expected.claimed_names(),
                compose_project=expected.compose_project,
                prefixes=expected.prefixes(),
            ),
            image_refs=expected.image_refs(),
        )
        self._last_listed = set(view.listed) if view.ok else self._last_listed
        if view.ok and not self._docker_was_ok:
            holds.hold("docker", cfg.docker_recover_sec, "Docker just came back")
        self._docker_was_ok = view.ok or view.cli_missing
        recovering = holds.held("docker") if view.ok else None

        if hold is not None:
            # Booting, or a restart/update under way: judge nothing, write
            # nothing, leave every open incident as it is.
            det = Detection(daemon_ok=view.ok, error=view.error)
            self.debouncer.pending.clear()
        elif recovering is not None:
            # Docker is back and its containers are coming back up: the
            # daemon incident may resolve, every container waits.
            det = Detection(daemon_ok=True, held={n: recovering for n in expected.containers})
            self.debouncer.pending.clear()
        else:
            await self._hold_vouched_tag(expected, view)
            det = detect(expected, view, now_wall=self.wall(), boot_wall=self.started_wall,
                         memory=self.memory, now_mono=self.clock())

        written: dict[str, Any] = {"opened": [], "resolved": []}
        if hold is None and db_ok:
            snap = self.debouncer.snapshot()
            try:
                written = await self._persist(det, expected, now)
            except Exception as e:  # noqa: BLE001 — try again next check
                self.debouncer.restore(snap)
                db_ok = False
                self._warn_once("write", f"container guard: could not record incidents: {e}")

        if db_ok:
            self._warned.clear()
        if not view.ok and not view.cli_missing:
            self._fail_streak += 1
            self._next_due = now + min(cfg.interval_sec * (2 ** self._fail_streak), MAX_BACKOFF_SEC)
        else:
            self._fail_streak = 0
            self._next_due = 0.0

        self.status = self._status(cfg, expected, view, det, hold, db_ok, written)
        return self.status

    async def _hold_vouched_tag(self, exp: ExpectedSet, view: Any) -> None:
        """The tag moved while this core runs, and the installer's record
        vouches for the new image (a repair by Setup rebuilt it): hold the
        tag check instead of reporting it. The rooms keep being compared to
        the recorded image they still run; provision.sh restarts the core
        right after its build, and that start adopts the new image
        (:func:`note_mpd_image`). Anything the record doesn't name is still
        reported."""
        ref, baseline = exp.mpd_tag_ref, exp.mpd_image_id
        if not ref or not baseline or exp.mpd_tag_hold or not view.ok:
            return
        tag_id = view.tag_ids.get(ref)
        if not tag_id or tag_id == baseline:
            return
        try:
            vouched = await asyncio.to_thread(provision_evidence, exp.mpd_image_at, tag_id)
        except Exception as e:  # noqa: BLE001 — no evidence, then
            log.debug("container guard: installer record unreadable: %s", e)
            vouched = False
        if vouched:
            exp.mpd_tag_hold = (
                "Domovoi's installer rebuilt the music player image; the core takes it on "
                "when it restarts"
            )

    async def _persist(self, det: Detection, expected: ExpectedSet, now: float) -> dict[str, Any]:
        opened: list[int] = []
        resolved: list[int] = []
        async with self.session_factory() as s:
            if self.pending_baseline is not None:
                await store.set_baseline(s, MPD_BASELINE_KEY, *self.pending_baseline)
                self.pending_baseline = None
            open_rows = await store.open_incidents(s)
            accepted_rows = await store.accepted(s)
            accepted = {k for k, _ in accepted_rows}
            plan = self.debouncer.plan(det, open_rows, expected, now, accepted)
            # A cleared finding stays accepted until the guard has looked and
            # it is gone (M3): the container judged without it, or not there
            # at all; the music player tag judged without it.
            present = {f.key for f in det.findings}
            release = [
                key for key, container in accepted_rows
                if key not in present and det.daemon_ok and (
                    (container is None and det.tag_evaluated)
                    or (container is not None and (container in det.image_evaluated
                                                   or container not in self._last_listed))
                )
            ]
            await store.release_acceptance(s, release)
            for f in plan.to_open:
                iid, created = await store.open_incident(s, f)
                if created:
                    opened.append(iid)
                    (log.error if f.cls == TAMPER else log.warning)(
                        "container guard: incident #%s opened (%s/%s): %s",
                        iid, f.cls, f.kind, f.summary,
                    )
            for iid, f, changed in plan.to_touch:
                await store.touch_incident(s, iid, f, changed=changed)
            for iid, resolution in plan.to_resolve:
                if await store.resolve_incident(s, iid, resolution):
                    resolved.append(iid)
                    log.info("container guard: incident #%s resolved (%s)", iid, resolution)
            await store.touch_accepted(s, plan.accepted)
            if self._last_prune is None or now - self._last_prune >= PRUNE_EVERY_SEC:
                pruned = await store.prune(s)
                self._last_prune = now
                if pruned:
                    log.info("container guard: pruned %d resolved incidents", pruned)
        return {"opened": opened, "resolved": resolved}

    def _status(self, cfg: GuardConfig, expected: ExpectedSet, view: Any, det: Detection,
                hold: str | None, db_ok: bool, written: dict[str, Any]) -> dict[str, Any]:
        watched = []
        for name in sorted(expected.containers):
            e = expected.containers[name]
            obs = view.containers.get(name) if view.ok else None
            entry = e.as_dict()
            entry["observed"] = (
                {"status": obs.status, "health": obs.health, "image": obs.config_image,
                 "image_id": obs.image_id, "restart_count": obs.restart_count}
                if obs is not None else ({"status": "missing"} if view.ok else None)
            )
            watched.append(entry)
        state = "ok"
        if view.cli_missing:
            state = "docker_missing"
        elif not view.ok:
            state = "docker_unreachable"
        elif hold is not None or det.held and not det.evaluated and not det.findings:
            state = "holding"
        return {
            "state": state,
            "checked_at": self.wall(),
            "interval_sec": cfg.interval_sec,
            "hold": hold,
            "holds": holds.active(),
            "database_ok": db_ok,
            "docker": {"ok": view.ok, "error": view.error, "cli_missing": view.cli_missing},
            "compose_project": expected.compose_project,
            "watched": watched,
            "held": det.held,
            "unrecognized": det.unrecognized,
            "unwatched_plugins": list(expected.unwatched_plugins),
            # L5: a plugin resolver that kept failing: its container is known,
            # not watched, until the resolver answers again.
            "failing_resolvers": failing_resolvers(),
            # H1: how the boot placed the music player image.
            "mpd_image": self.mpd_note,
            "pending": [
                {"key": k, "class": p.finding.cls, "kind": p.finding.kind,
                 "container": p.finding.container, "seen": p.count,
                 "for_sec": round(self.clock() - p.first_seen, 1)}
                for k, p in sorted(self.debouncer.pending.items())
            ],
            "findings": [
                {"key": f.key, "class": f.cls, "kind": f.kind, "container": f.container,
                 "summary": f.summary}
                for f in det.findings
            ],
            "last_opened": written.get("opened", []),
            "last_resolved": written.get("resolved", []),
        }


# ─── what the running core expects, and when it holds everything ──────────

async def default_global_hold(guard: ContainerGuard, cfg: GuardConfig) -> str | None:
    up = guard.clock() - guard.started_mono
    if up < cfg.boot_grace_sec:
        return f"the core started {int(up)} s ago"
    try:
        from domovoi import self_restart

        if await asyncio.to_thread(self_restart.underway):
            return "a restart or update is under way"
    except Exception as e:  # noqa: BLE001
        log.debug("container guard: restart state unknown: %s", e)
    return None


async def gather_expected(guard: ContainerGuard, cfg: GuardConfig, *, with_db: bool = True) -> ExpectedSet:
    """The expected set of the running core (module docstring of
    :mod:`domovoi.container_guard.expected`). Raises when the database
    can't be read and ``with_db`` is set; the caller then asks again
    without it and records nothing that check."""
    from domovoi import egress, searxng_service
    from domovoi.config import settings

    exp = ExpectedSet()
    if compose_is_ours(cfg.compose, settings.database_url):
        boot, now = await asyncio.to_thread(guard.compose_services)
        project = project_name(guard.compose_file())
        searxng_hold = holds.held("core:searxng")
        if searxng_hold is None and await asyncio.to_thread(_transient_unit_active, SEARXNG_START_UNIT):
            # The update unit hands a first SearXNG start (a pull) to a
            # transient unit and doesn't wait for it (apply-update.sh).
            searxng_hold = "the update is starting the web search helper"
        try:
            status = searxng_service.status()
            if status.get("state") in ("starting", "stopping"):
                searxng_hold = searxng_hold or "the web search helper is being started or stopped"
        except Exception:  # noqa: BLE001
            pass
        exp.compose_project = project
        exp.compose_services = frozenset(boot) | frozenset(now)
        exp.add(
            compose_expectations(
                boot, now, project=project, internet_policy=egress.policy(),
                manage_searxng=searxng_service.managed(),
                chat_mode=bool(settings.chat_mode_enabled), searxng_hold=searxng_hold,
            )
        )
    exp.mpd_prefix = settings.mpd_container_prefix or None
    exp.mpd_tag_ref = settings.mpd_image_tag or None
    exp.mpd_tag_hold = holds.held("mpd-image")
    if not with_db:
        return exp

    from sqlalchemy import text

    from domovoi.plugins_runtime import registry as reg
    from domovoi.plugins_runtime.loader import LOADER

    async with guard.session_factory() as s:
        rooms = [
            (str(r[0]), str(r[1]))
            for r in await s.execute(
                text("SELECT room_id, container_name FROM mpd_rooms ORDER BY room_id")
            )
        ]
        baseline = await store.get_baseline(s, MPD_BASELINE_KEY)
    exp.mpd_image_id = (baseline or {}).get("value") or None
    exp.mpd_image_at = (baseline or {}).get("recorded_at")
    exp.add(
        mpd_expectations(
            rooms, tag_ref=settings.mpd_image_tag, image_id=exp.mpd_image_id, warm_done=True,
        )
    )
    plugin_rows = await reg.list_plugins()
    expected, names, unwatched = await plugin_expectations(
        plugin_rows, dict(LOADER.loaded),
        pending_restart=set(LOADER.pending_restart),
        image_present=getattr(guard.docker, "image_present", None),
    )
    exp.add(expected)
    exp.plugin_names = names
    exp.unwatched_plugins = unwatched
    return exp


# ─── the music player image baseline ──────────────────────────────────────

async def note_mpd_image(image_id: str | None, *, built: bool) -> str:
    """Record or check the music player image the core runs rooms from.

    Called by ``mpd_provisioner.ensure_image`` at boot. ``built``: the core
    just built it, so it is the baseline. Otherwise the existing image is
    adopted when there is no baseline yet (the first boot with the guard),
    or when Domovoi's installer or the update unit rebuilt it
    (:func:`mpd_rebuild_evidence`: a root-owned record says so, or on an
    update-unit host the unit's record can't be read). Any other change is
    left for the guard's check to report: the image under the tag is not
    the one Domovoi built. Returns what it did ("built", "adopted", "same",
    "provision", "update", "unverified", "changed", "unrecorded")."""
    if not image_id:
        return "unrecorded"
    guard = get_guard()
    try:
        async with guard.session_factory() as s:
            if built:
                await store.set_baseline(s, MPD_BASELINE_KEY, image_id, "core-build")
                return "built"
            current = await store.get_baseline(s, MPD_BASELINE_KEY)
            if current is None:
                await store.set_baseline(s, MPD_BASELINE_KEY, image_id, "first-seen")
                return "adopted"
            if current["value"] == image_id:
                return "same"
            evidence = await asyncio.to_thread(
                mpd_rebuild_evidence, current.get("recorded_at"), image_id,
            )
            if evidence == "provision":
                await store.set_baseline(s, MPD_BASELINE_KEY, image_id, "provision")
                guard.mpd_note = "rebuilt by Domovoi's installer (provision.sh); adopted"
                return "provision"
            if evidence in ("finished", "in-flight"):
                await store.set_baseline(s, MPD_BASELINE_KEY, image_id, f"update-unit:{evidence}")
                guard.mpd_note = f"rebuilt by the update unit ({evidence}); adopted"
                return "update"
            if evidence is not None:
                await store.set_baseline(s, MPD_BASELINE_KEY, image_id, f"unverified:{evidence}")
                guard.mpd_note = (
                    "changed while the core was down; the update unit's record of the "
                    f"run can't be read ({evidence}), so it was adopted unverified"
                )
                log.warning("container guard: music player image %s adopted unverified (%s)",
                            image_id[:19], evidence)
                return "unverified"
            guard.mpd_note = "changed outside Domovoi (reported)"
            log.error(
                "container guard: the music player image (%s) is not the one Domovoi "
                "built (%s); the next check reports it", image_id[:19], str(current["value"])[:19],
            )
            return "changed"
    except Exception as e:  # noqa: BLE001 — the first check writes it instead
        log.warning("container guard: music player image not recorded yet: %s", e)
        if built:
            guard.pending_baseline = (image_id, "core-build")
        return "unrecorded"


_UNIT_BUSY = frozenset({"activating", "active", "deactivating", "reloading"})


def mpd_rebuild_evidence(recorded_at: Any, image_id: str | None = None) -> str | None:
    """Blocking. Why a changed music player image (``image_id``, what the
    tag names now) is Domovoi's own rebuild, or None (no evidence: the
    guard reports it).

    * ``provision`` — Domovoi's installer (scripts/linux/provision.sh, run
      by Setup on Windows, a repair included) recorded building exactly
      this image after the baseline was recorded
      (:func:`provision_evidence`);
    * ``finished`` — the update unit's record of a finished run says it rebuilt
      the image (``mpd_changed``) after the baseline was recorded;
    * ``in-flight`` — the record of the run under way (``status: running``,
      which the unit writes before it rebuilds and starts the core) says
      ``mpd_changed`` and names the SHA this core runs: ``to_sha`` for the
      update itself, ``from_sha`` for its rollback (rollback-mpd rebuilds
      the old image and the core starts on the previous SHA);
    * ``record-missing:<why>`` (``unit-running`` when systemd says the unit
      is running) — this host has the update unit but its record can't be
      read. Adopted without paging, and said so on the status. This opens
      no hole an attacker without root has: the record lives in the
      root-owned /var/lib/domovoi-update, so only root can remove or spoil
      it, and replacing the image needs Docker access, which is root's
      equal and outside what the guard can stop anyway; a change while the
      core runs is still caught by the tag and per-room checks.

    A READABLE record that doesn't say so is no evidence: an update that
    didn't touch the music player never vouches for a new image, and the
    installer's record vouches only for the image it names."""
    from domovoi import git_version, self_restart

    if image_id and provision_evidence(recorded_at, image_id):
        return "provision"
    try:
        last, problem = git_version.load_last_update()
    except Exception:  # noqa: BLE001
        last, problem = None, "unreadable"
    if isinstance(last, dict):
        if not last.get("mpd_changed"):
            return None
        if last.get("status") == "running":
            running = git_version.boot_sha().removesuffix("-dirty")
            if running and running != "unknown":
                for sha in (last.get("to_sha"), last.get("from_sha")):
                    if isinstance(sha, str) and sha.startswith(running):
                        return "in-flight"
            return None
        return "finished" if _stamped_after(last.get("finished_at"), recorded_at) else None
    try:
        installed = self_restart.update_unit_installed()
    except Exception:  # noqa: BLE001
        installed = False
    if not installed:
        return None
    try:
        state = self_restart.update_unit_state()
    except Exception:  # noqa: BLE001
        state = None
    if state in _UNIT_BUSY:
        return "record-missing:unit-running"
    return f"record-missing:{problem or 'unreadable'}"


def _stamped_after(stamp: Any, recorded_at: Any) -> bool:
    """Whether ``stamp`` (``YYYY-MM-DDTHH:MM:SSZ``, as the root scripts write
    it) is at or after the baseline's ``recorded_at`` (None: no baseline
    time to compare, so any valid stamp is after)."""
    from datetime import datetime, timezone

    if not isinstance(stamp, str):
        return False
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    if recorded_at is None:
        return True
    if isinstance(recorded_at, datetime):
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        return when >= recorded_at
    return False


# What scripts/linux/provision.sh writes each time it builds the music
# player image (root's, 0644, in its root-owned state directory). Not a
# setting: the .env is the service user's, and the evidence must stay where
# only root writes.
PROVISION_MPD_RECORD = "/var/lib/domovoi-provision/mpd-image.json"
_RECORD_MAX_BYTES = 4096
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _root_only(st: os.stat_result) -> bool:
    """A file or directory only root can change: root's, and not writable
    by its group or anyone else."""
    return st.st_uid == 0 and not (st.st_mode & 0o022)


def _trusted_record(path: Path) -> bool:
    """Blocking. Whether ``path`` is a regular file (not a link) that only
    root can have written: it and its directory are root's alone. Never on
    Windows, which has no provision.sh and no such owners."""
    if sys.platform == "win32":
        return False
    try:
        st = os.lstat(path)
        parent = os.stat(path.parent)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and _root_only(st) and _root_only(parent)


def provision_evidence(recorded_at: Any, image_id: str) -> bool:
    """Blocking. True when the installer's record (:data:`PROVISION_MPD_RECORD`)
    says it built exactly ``image_id``, at or after the baseline's
    ``recorded_at``. Anything else is no evidence: a missing, unreadable or
    malformed record, one root doesn't own alone, one that names another
    image (a ``docker build`` by hand after the installer's) or an older
    build (the installer's image re-tagged after a later one)."""
    path = Path(PROVISION_MPD_RECORD)
    if not _IMAGE_ID_RE.match(image_id or "") or not _trusted_record(path):
        return False
    try:
        with path.open("rb") as f:
            raw = f.read(_RECORD_MAX_BYTES + 1)
        doc = json.loads(raw.decode("utf-8")) if len(raw) <= _RECORD_MAX_BYTES else None
    except (OSError, ValueError):
        return False
    if not isinstance(doc, dict) or doc.get("by") != "provision":
        return False
    if doc.get("image_id") != image_id:
        return False
    return _stamped_after(doc.get("built_at"), recorded_at)


SEARXNG_START_UNIT = "domovoi-searxng-start.service"


def _transient_unit_active(unit: str) -> bool:
    """Blocking. Whether a systemd unit is running (a plain read, no sudo);
    False without systemd."""
    import shutil
    import sys

    if sys.platform == "win32":
        return False
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return False
    try:
        proc = subprocess.run(
            [systemctl, "show", "--property=ActiveState", "--value", unit],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() in _UNIT_BUSY


_GUARD: ContainerGuard | None = None


def get_guard() -> ContainerGuard:
    """The core process's guard (created on first use)."""
    global _GUARD
    if _GUARD is None:
        _GUARD = ContainerGuard()
    return _GUARD


def reset_guard(guard: ContainerGuard | None = None) -> None:
    """Replace the process's guard (tests; a re-entered lifespan)."""
    global _GUARD
    _GUARD = guard
