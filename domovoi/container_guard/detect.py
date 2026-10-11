"""The detection rules: the expected set against one look at Docker.

Pure — no Docker, no database, no clock of its own — so every rule is a
unit test. Rule names are the ones docs/CONTAINER_GUARD.md uses.

Failure class (alert; everything else keeps working):

* **F1 daemon** — Docker doesn't answer while something is expected to
  run. While it doesn't, no other rule runs: a dead daemon is one
  problem, not one per container.
* **F2 missing** — an expected container doesn't exist.
* **F3 stopped** — it exists but isn't running (exited, created, dead,
  paused).
* **F4 restarting** — it is restarting, or Docker restarted it 3 or more
  times in the last 10 minutes (a crash loop).
* **F5 unhealthy** — it runs and its health check says unhealthy.
* **F6 one-shot failed** — a one-shot (Flyway) exited non-zero since
  shortly before this core started.
* **F7 outdated** (amber) — a container of Domovoi's runs an image of the
  right repository, pulled from a registry, but not the pinned one: it was
  made from an earlier pin and never recreated ("recreate it").

Tamper class (alert and freeze until an admin clears it):

* **T1 image** — a running container of Domovoi's (expected or not right
  now) runs an image of another repository, or one with no registry
  provenance (built or loaded locally), where Domovoi pins a digest or a
  tag; an image ID other than the one the core built (the music players);
  for a disabled plugin's container, another repository. Also the music
  player image TAG pointing at an image the core didn't build. One-off
  runs of a compose service (``compose run``) are held to its pin too.
* **T2 unknown** — a RUNNING container in Domovoi's namespace that nothing
  claims: the compose project's label with a service the compose file
  doesn't have, or the music player prefix with no ``mpd_rooms`` row
  (a leftover of a removed room running the music player image is not
  tampering, and is listed as unrecognized instead).
* **T3 config** — a running container of Domovoi's with privileged mode,
  host networking, the host's PID namespace, a bind mount of the Docker
  socket or of the host's root, when its declaration allows none of that.

Held containers (a room starting, a plugin upgrading) are skipped by
every rule; :mod:`domovoi.container_guard.engine` applies the time-based
debounce on top.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domovoi.container_guard.expected import RETIRED_COMPOSE_SERVICES, ExpectedSet
from domovoi.container_guard.model import (
    FAILURE,
    KNOWN,
    ONESHOT,
    RUNNING,
    SEVERITY_ERROR,
    SEVERITY_WARN,
    STOPPED,
    TAMPER,
    DockerView,
    Expected,
    Finding,
    Observed,
    capitalized,
    normalize_ref,
    ref_repository,
)

DOCKER_KEY = "failure:docker"
_FRACTION_RE = re.compile(r"\.(\d+)")

# F4: this many automatic restarts inside the window is a crash loop.
RESTART_LOOP_COUNT = 3
RESTART_LOOP_WINDOW_SEC = 600.0
# F6: a one-shot's non-zero exit counts when it finished no earlier than
# this long before the core started (an old failure is history).
ONESHOT_LOOKBACK_SEC = 3600.0

# Names that look like Domovoi's but that nothing here claims. Listed on
# the status page; never an incident (old harness or plugin containers
# would page people for nothing).
_DOMOVOI_LOOKALIKE = ("domovoi-", "domovoi_")


class RestartMemory:
    """Docker's RestartCount per container ID over the last window (F4)."""

    def __init__(self, window_sec: float = RESTART_LOOP_WINDOW_SEC) -> None:
        self.window_sec = window_sec
        self._samples: dict[str, deque[tuple[float, int]]] = {}

    def note(self, obs: Observed, now: float) -> int:
        """Record ``obs``'s count; returns the increase inside the window."""
        samples = self._samples.setdefault(obs.id, deque())
        samples.append((now, obs.restart_count))
        while samples and now - samples[0][0] > self.window_sec:
            samples.popleft()
        return max(0, obs.restart_count - min(c for _, c in samples))

    def keep_only(self, ids: set[str]) -> None:
        for cid in [c for c in self._samples if c not in ids]:
            del self._samples[cid]


@dataclass
class Detection:
    daemon_ok: bool
    findings: list[Finding] = field(default_factory=list)
    # Expected containers whose failure rules ran this check (not held).
    evaluated: set[str] = field(default_factory=set)
    held: dict[str, str] = field(default_factory=dict)
    unrecognized: list[str] = field(default_factory=list)
    error: str | None = None
    # Containers whose image (or absence) this check could judge: a cleared
    # tamper finding's acceptance ends, and an "outdated" failure resolves,
    # only when the guard has looked and the finding is gone.
    image_evaluated: set[str] = field(default_factory=set)
    # The music player image tag was looked at this check.
    tag_evaluated: bool = False


def _parse_time(value: str | None) -> float | None:
    if not value or value.startswith("0001-01-01"):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Docker writes nanoseconds; datetime takes microseconds.
    m = _FRACTION_RE.search(text)
    if m:
        text = f"{text[:m.start()]}.{m.group(1)[:6]}{text[m.end():]}"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _failure(e: Expected, kind: str, summary: str, found: dict[str, Any]) -> Finding:
    return Finding(
        key=f"failure:{e.name}",
        cls=FAILURE,
        kind=kind,
        severity=e.severity,
        container=e.name,
        owner=e.owner,
        label=e.label,
        summary=summary,
        expected={"state": e.state, "image": e.image_ref, "required": e.required},
        found=found,
    )


def _exit_words(obs: Observed) -> str:
    if obs.paused:
        return "is paused"
    if obs.exit_code not in (None, 0):
        return f"stopped (exit code {obs.exit_code})"
    return "stopped"


def _failure_rules(e: Expected, obs: Observed | None, loop: int, boot_wall: float) -> Finding | None:
    who = capitalized(e.label)
    if e.state == RUNNING:
        if obs is None:
            return _failure(e, "missing", f"{who} is not running: its container is missing.",
                            {"status": "missing"})
        if obs.restarting or loop >= RESTART_LOOP_COUNT:
            found = obs.brief() | {"restarts_in_window": loop}
            return _failure(e, "restarting", f"{who} keeps restarting.", found)
        if not obs.running or obs.paused:
            return _failure(e, "stopped", f"{who} {_exit_words(obs)}.", obs.brief())
        if (obs.health or "").lower() == "unhealthy":
            return _failure(e, "unhealthy", f"{who} is running but its health check fails.",
                            obs.brief())
        return None
    if e.state == ONESHOT and obs is not None and not obs.running:
        if obs.exit_code not in (None, 0):
            finished = _parse_time(obs.finished_at)
            if finished is not None and finished >= boot_wall - ONESHOT_LOOKBACK_SEC:
                return Finding(
                    key=f"failure:{e.name}",
                    cls=FAILURE,
                    kind="oneshot_failed",
                    severity=SEVERITY_WARN,
                    container=e.name,
                    owner=e.owner,
                    label=e.label,
                    summary=f"{who} failed (exit code {obs.exit_code}).",
                    expected={"state": e.state, "exit_code": 0},
                    found=obs.brief(),
                )
    return None


def _tamper(kind: str, key: str, name: str | None, owner: str, label: str, summary: str,
            expected: dict[str, Any], found: dict[str, Any]) -> Finding:
    return Finding(
        key=key, cls=TAMPER, kind=kind, severity=SEVERITY_ERROR, container=name,
        owner=owner, label=label, summary=summary, expected=expected, found=found,
    )


OK = "ok"
OUTDATED = "outdated"
FOREIGN = "foreign"
NO_DATA = "unknown"


def _digest_repository(repo_digest: str) -> str:
    return ref_repository(repo_digest.split("@", 1)[0])


def image_verdict(e: Expected, obs: Observed, view: DockerView) -> str:
    """T1 and its failure twin (see the module docstring):

    * ``ok`` — the container runs what Domovoi expects;
    * ``outdated`` — not the pinned image, but the same repository, pulled
      from a registry (an earlier pin the container was made from, never
      recreated): a failure, "recreate it";
    * ``foreign`` — another repository, or no registry provenance at all
      (built or loaded locally): tampering;
    * ``unknown`` — the image's repo digests couldn't be read this look: no
      data, no finding.

    The music player image (pinned by the ID the core built) has no
    registry provenance by design: any other ID is ``foreign``.
    """
    if e.image_id:
        return OK if obs.image_id == e.image_id else FOREIGN
    if not (e.image_digests or e.image_ref):
        return OK
    if e.image_digests and obs.image_id in e.image_digests:
        return OK
    if not e.repo_only and not e.image_digests and e.image_ref:
        if normalize_ref(obs.config_image) == normalize_ref(e.image_ref):
            return OK
    if obs.image_id in view.unknown_images:
        return NO_DATA
    digests = view.repo_digests.get(obs.image_id, ())
    if any(d.rsplit("@", 1)[-1] in e.image_digests for d in digests):
        return OK
    want = ref_repository(e.image_ref)
    from_registry = any(_digest_repository(d) == want for d in digests)
    if e.repo_only:
        return OK if from_registry else FOREIGN
    return OUTDATED if from_registry else FOREIGN


def image_matches(e: Expected, obs: Observed, view: DockerView) -> bool:
    """True when :func:`image_verdict` finds nothing to report."""
    return image_verdict(e, obs, view) in (OK, NO_DATA)


def _image_finding(e: Expected, obs: Observed, view: DockerView) -> Finding:
    return _tamper(
        "image",
        f"tamper:image:{e.name}:{obs.image_id}",
        e.name, e.owner, e.label,
        f"{capitalized(e.label)} is running a different image than the one Domovoi expects.",
        {"image": e.image_ref, "digests": sorted(e.image_digests), "image_id": e.image_id,
         "pin": e.pin},
        {"image": obs.config_image, "image_id": obs.image_id,
         "repo_digests": list(view.repo_digests.get(obs.image_id, ())), "id": obs.id[:12]},
    )


def _outdated_finding(e: Expected, obs: Observed, view: DockerView) -> Finding:
    fix = (f"docker compose up -d {e.compose_service} (from domovoi/)" if e.compose_service
           else "recreate the container")
    return Finding(
        key=f"failure:outdated:{e.name}",
        cls=FAILURE,
        kind="outdated",
        severity=SEVERITY_WARN,
        container=e.name,
        owner=e.owner,
        label=e.label,
        summary=(f"{capitalized(e.label)} is running an older {ref_repository(e.image_ref)} "
                 f"image than the one Domovoi pins: recreate it."),
        expected={"image": e.image_ref, "digests": sorted(e.image_digests), "fix": fix},
        found={"image": obs.config_image, "image_id": obs.image_id,
               "repo_digests": list(view.repo_digests.get(obs.image_id, ())), "id": obs.id[:12]},
    )


def _image_rule(e: Expected, obs: Observed, view: DockerView, det: "Detection") -> None:
    """T1 / outdated for one running container; records whether it had data."""
    verdict = image_verdict(e, obs, view)
    if verdict == NO_DATA:
        return
    det.image_evaluated.add(e.name)
    if verdict == OUTDATED:
        det.findings.append(_outdated_finding(e, obs, view))
    elif verdict == FOREIGN:
        det.findings.append(_image_finding(e, obs, view))


def _config_finding(e: Expected, obs: Observed, extra: list[str]) -> Finding:
    words = ", ".join(_SECURITY_WORDS.get(a, a) for a in extra)
    return _tamper(
        "config",
        f"tamper:config:{e.name}:{obs.id[:12]}:{','.join(extra)}",
        e.name, e.owner, e.label,
        f"{capitalized(e.label)} is running with settings Domovoi never gives it ({words}).",
        {"allowed": sorted(e.allow)},
        {"settings": extra, "image": obs.config_image, "id": obs.id[:12]},
    )


_SECURITY_WORDS = {
    "privileged": "privileged mode",
    "host_network": "the host's network",
    "host_pid": "the host's processes",
    "docker_socket": "the Docker socket",
    "root_mount": "the host's whole disk",
}


def detect(
    exp: ExpectedSet,
    view: DockerView,
    *,
    now_wall: float,
    boot_wall: float,
    memory: RestartMemory | None = None,
    now_mono: float | None = None,
) -> Detection:
    """``now_wall`` dates a one-shot's exit (Docker's timestamps are wall
    clock); ``now_mono`` (default: ``now_wall``) times the crash-loop
    window, so a wall clock stepped back can't stretch it (F4)."""
    memory = memory or RestartMemory()
    mono = now_wall if now_mono is None else now_mono
    if not view.ok:
        det = Detection(daemon_ok=False, error=view.error)
        if not view.cli_missing and exp.anything_expected():
            running = [e for e in exp.containers.values() if e.state in (RUNNING, ONESHOT)]
            severity = SEVERITY_ERROR if any(e.required for e in running) else SEVERITY_WARN
            det.findings.append(
                Finding(
                    key=DOCKER_KEY, cls=FAILURE, kind="daemon", severity=severity,
                    container=None, owner="core", label="Docker",
                    summary="Docker isn't answering, so Domovoi can't run or check its containers.",
                    expected={"daemon": "answering"},
                    found={"error": view.error},
                )
            )
        return det

    det = Detection(daemon_ok=True)
    live_ids: set[str] = set()

    for name in sorted(exp.containers):
        e = exp.containers[name]
        obs = view.containers.get(name)
        if obs is not None:
            live_ids.add(obs.id)
        if e.hold:
            det.held[name] = e.hold
            continue
        if obs is None and name in view.listed:
            # Listed but gone by the time it was inspected: no data this
            # check, so neither a finding nor a clean bill.
            continue
        if e.state in (RUNNING, ONESHOT):
            det.evaluated.add(name)
            loop = memory.note(obs, mono) if obs is not None else 0
            finding = _failure_rules(e, obs, loop, boot_wall)
            if finding is not None:
                det.findings.append(finding)
        if obs is None or not obs.running:
            # Gone or stopped: nothing runs that could be the wrong image.
            det.image_evaluated.add(name)
            continue
        # Every running container of Domovoi's runs the image it should —
        # the not-currently-expected ones too (M4): a stopped-chat Letta or
        # a disabled plugin's server is no licence to run anything.
        _image_rule(e, obs, view, det)
        extra = sorted(set(obs.security()) - e.allow)
        if extra:
            det.findings.append(_config_finding(e, obs, extra))

    # T1, the music player image itself: the tag every new room's container
    # is created from must still be the image the core built.
    if exp.mpd_tag_ref and exp.mpd_image_id and not exp.mpd_tag_hold and exp.mpd_tag_ref in view.tag_ids:
        det.tag_evaluated = True
        tag_id = view.tag_ids.get(exp.mpd_tag_ref)
        if tag_id and tag_id != exp.mpd_image_id:
            det.findings.append(
                _tamper(
                    "image", f"tamper:mpd-image:{tag_id}", None, "core", "the music player image",
                    "The music player image was replaced outside Domovoi.",
                    {"image": exp.mpd_tag_ref, "image_id": exp.mpd_image_id},
                    {"image": exp.mpd_tag_ref, "image_id": tag_id},
                )
            )

    by_service = {e.compose_service: e for e in exp.containers.values() if e.compose_service}
    for name in sorted(view.listed):
        if name in exp.containers:
            continue
        listed = view.listed[name]
        obs = view.containers.get(name)
        if obs is not None:
            live_ids.add(obs.id)
        in_compose = bool(exp.compose_project) and listed.compose_project == exp.compose_project
        in_mpd = bool(exp.mpd_prefix) and name.startswith(exp.mpd_prefix or "")
        if in_compose and (
            listed.compose_service in exp.compose_services
            or listed.compose_service in RETIRED_COMPOSE_SERVICES
        ):
            # A one-off run of a known service (`compose run --rm flyway`)
            # or a retired one: Domovoi's. It runs its service's image (L2),
            # and never privileged.
            if obs is None:
                continue
            det.image_evaluated.add(name)
            if not obs.running:
                continue
            svc = by_service.get(listed.compose_service)
            stand_in = Expected(
                name=name, owner="core", role="compose",
                label=f"a one-off {listed.compose_service} container ({name})", state=KNOWN,
                image_ref=svc.image_ref if svc else None,
                image_digests=svc.image_digests if svc else frozenset(),
                compose_service=listed.compose_service,
            )
            if svc is not None:
                _image_rule(stand_in, obs, view, det)
            if obs.security():
                det.findings.append(_config_finding(stand_in, obs, sorted(obs.security())))
            continue
        if not (in_compose or in_mpd):
            if name.lower().startswith(_DOMOVOI_LOOKALIKE):
                det.unrecognized.append(name)
            continue
        if obs is None:
            continue
        det.image_evaluated.add(name)
        if not obs.running:
            # A stopped container does nothing; if it starts, the next
            # check sees it.
            continue
        if in_mpd and not in_compose and not obs.security() and (
            normalize_ref(obs.config_image) == normalize_ref(exp.mpd_tag_ref)
            and (exp.mpd_image_id is None or obs.image_id == exp.mpd_image_id)
        ):
            # A removed room's music player, left running: ours, harmless.
            det.unrecognized.append(name)
            continue
        where = (f"the compose project {exp.compose_project!r}" if in_compose
                 else f"the music player prefix {exp.mpd_prefix!r}")
        det.findings.append(
            _tamper(
                "unknown", f"tamper:unknown:{name}:{obs.image_id}", name, "core",
                f"an unknown container ({name})",
                f"An unknown container ({name}) is running under Domovoi's names.",
                {"namespace": where},
                {"image": obs.config_image, "image_id": obs.image_id, "status": obs.status,
                 "compose_service": listed.compose_service or None,
                 "settings": obs.security(), "id": obs.id[:12]},
            )
        )

    memory.keep_only(live_ids)
    return det
