"""Looking at Docker through its CLI — read-only, a few calls per check.

One check costs at most four short ``docker`` calls, whatever else runs
on the host:

1. ``docker ps -a`` with a fixed format: every container's id, name and
   compose labels (no inspection of anything that isn't Domovoi's);
2. one ``docker inspect`` of the containers the guard claims (its expected
   set, the compose project, the music player prefix, plugin names);
3. one ``docker image inspect`` for the repo digests of images it has not
   seen before (an image ID never changes what it is, so this is cached);
4. one ``docker image inspect`` of the music player image tag.

Nothing here starts, stops, pulls or removes anything. The CLI runs in a
worker thread with a timeout (the docker CLI is what the rest of the core
already drives — mpd_provisioner, searxng_service — so no new dependency),
and with ``CREATE_NO_WINDOW`` on Windows so a console-less core does not
flash a window per call. Errors become a :class:`DockerView` with
``ok=False``; nothing raises into the guard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from collections.abc import Iterable
from typing import Any

from domovoi.container_guard.model import DockerView, Listed, Observed

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PS_FORMAT = (
    '{{.ID}}\t{{.Names}}\t{{.Label "com.docker.compose.project"}}'
    '\t{{.Label "com.docker.compose.service"}}\t{{.State}}'
)

# Repo digests by image ID. Immutable facts, so kept; bounded so a host
# that churns images can't grow it without end.
_DIGEST_CACHE: dict[str, tuple[str, ...]] = {}
_DIGEST_CACHE_MAX = 256


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:300]
    return ""


class DockerCli:
    """The guard's only way to Docker. Tests hand the guard a fake with the
    same :meth:`observe` instead."""

    def __init__(self, exe: str = "docker", timeout_sec: float = 20.0) -> None:
        self.exe = exe
        self.timeout_sec = timeout_sec

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_sec,
            check=False,
            creationflags=_NO_WINDOW,
        )

    async def _call(self, args: list[str]) -> tuple[int, str, str]:
        try:
            proc = await asyncio.to_thread(self._run, args)
        except FileNotFoundError:
            return 127, "", "docker is not installed or not on PATH"
        except subprocess.TimeoutExpired:
            return 124, "", f"docker {args[0]} did not answer within {self.timeout_sec:.0f} s"
        except OSError as e:
            return 126, "", f"docker could not run: {e}"
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    async def observe(
        self,
        *,
        claim: "Claimer",
        image_refs: Iterable[str] = (),
    ) -> DockerView:
        if shutil.which(self.exe) is None:
            return DockerView(ok=False, cli_missing=True, error="docker is not installed or not on PATH")
        rc, out, err = await self._call(["ps", "-a", "--no-trunc", "--format", _PS_FORMAT])
        if rc != 0:
            return DockerView(ok=False, error=_first_line(err) or f"docker ps exited {rc}")
        listed = parse_ps(out)
        view = DockerView(ok=True, listed=listed)
        wanted = [c.id for c in listed.values() if claim(c)]
        if wanted:
            rc, out, err = await self._call(["inspect", *wanted])
            # A container removed between ps and inspect makes inspect exit
            # non-zero but still print the rest; read what came back.
            for obs in parse_inspect(out):
                view.containers[obs.name] = obs
            if rc != 0 and not view.containers:
                return DockerView(ok=False, error=_first_line(err) or f"docker inspect exited {rc}")
        image_ids = sorted({o.image_id for o in view.containers.values() if o.image_id})
        missing = [i for i in image_ids if i not in _DIGEST_CACHE]
        if missing:
            rc, out, _ = await self._call(
                ["image", "inspect", "--format", "{{.Id}}\t{{json .RepoDigests}}", *missing]
            )
            for line in out.splitlines():
                image_id, _, digests = line.partition("\t")
                try:
                    values = tuple(str(d) for d in (json.loads(digests) or []))
                except ValueError:
                    values = ()
                if image_id.strip():
                    if len(_DIGEST_CACHE) >= _DIGEST_CACHE_MAX:
                        _DIGEST_CACHE.clear()
                    _DIGEST_CACHE[image_id.strip()] = values
        for image_id in image_ids:
            view.repo_digests[image_id] = _DIGEST_CACHE.get(image_id, ())
        for ref in sorted(set(image_refs)):
            rc, out, _ = await self._call(["image", "inspect", "--format", "{{.Id}}", ref])
            view.tag_ids[ref] = (out.strip() or None) if rc == 0 else None
        return view


class Claimer:
    """Decides which listed containers the guard inspects (the callable
    :meth:`DockerCli.observe` takes)."""

    def __init__(
        self,
        names: Iterable[str],
        *,
        compose_project: str | None,
        prefixes: Iterable[str] = (),
    ) -> None:
        self.names = frozenset(names)
        self.compose_project = compose_project
        self.prefixes = tuple(p for p in prefixes if p)

    def __call__(self, c: Listed) -> bool:
        if c.name in self.names:
            return True
        if self.compose_project and c.compose_project == self.compose_project:
            return True
        return any(c.name.startswith(p) for p in self.prefixes)


def parse_ps(out: str) -> dict[str, Listed]:
    listed: dict[str, Listed] = {}
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0].strip():
            continue
        parts += [""] * (5 - len(parts))
        name = parts[1].split(",")[0].strip().lstrip("/")
        if not name:
            continue
        listed[name] = Listed(
            id=parts[0].strip(),
            name=name,
            compose_project=parts[2].strip(),
            compose_service=parts[3].strip(),
            state=parts[4].strip(),
        )
    return listed


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_inspect(out: str) -> list[Observed]:
    try:
        docs = json.loads(out or "[]")
    except ValueError:
        return []
    if not isinstance(docs, list):
        return []
    result: list[Observed] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        state = doc.get("State") or {}
        config = doc.get("Config") or {}
        host = doc.get("HostConfig") or {}
        health = state.get("Health") or {}
        mounts = tuple(
            (str(m.get("Source") or ""), str(m.get("Destination") or ""))
            for m in (doc.get("Mounts") or [])
            if isinstance(m, dict)
        )
        name = str(doc.get("Name") or "").lstrip("/")
        if not name:
            continue
        result.append(
            Observed(
                id=str(doc.get("Id") or ""),
                name=name,
                status=str(state.get("Status") or "unknown"),
                running=bool(state.get("Running")),
                restarting=bool(state.get("Restarting")),
                paused=bool(state.get("Paused")),
                exit_code=_int(state.get("ExitCode")),
                started_at=state.get("StartedAt") or None,
                finished_at=state.get("FinishedAt") or None,
                restart_count=_int(doc.get("RestartCount")) or 0,
                health=(health.get("Status") or None) if isinstance(health, dict) else None,
                config_image=str(config.get("Image") or ""),
                image_id=str(doc.get("Image") or ""),
                privileged=bool(host.get("Privileged")),
                network_mode=str(host.get("NetworkMode") or ""),
                pid_mode=str(host.get("PidMode") or ""),
                mounts=mounts,
                labels={str(k): str(v) for k, v in (config.get("Labels") or {}).items()},
            )
        )
    return result
