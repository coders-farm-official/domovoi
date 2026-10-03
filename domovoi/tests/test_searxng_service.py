"""The search helper (SearXNG) follows the internet answer (fix B8).

``domovoi/searxng_service.py`` starts the compose service for always /
sometimes, stops the container for never, and leaves it alone while the
question is unanswered. It is the ``internet_access`` reapply hook, and is
never run at core boot. Every docker call is faked here (``_run``); DB-free.
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
from pathlib import Path

import pytest

from domovoi import egress, reapply, searxng_service as svc


class _Docker:
    """Records docker argv; answers from a script of (returncode, stdout,
    stderr) by the argv's second word, or raises what it's told."""

    def __init__(self, *, running: str | None = "false", fail: dict | None = None,
                 raises: dict | None = None) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.running = running
        self.fail = fail or {}
        self.raises = raises or {}

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        verb = argv[1]
        if verb in self.raises:
            raise self.raises[verb]
        if verb in self.fail:
            return subprocess.CompletedProcess(argv, 1, "", self.fail[verb])
        if verb == "inspect":
            if self.running is None:
                return subprocess.CompletedProcess(argv, 1, "", "Error: No such object: domovoi-searxng")
            return subprocess.CompletedProcess(argv, 0, self.running + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def verbs(self) -> list[str]:
        return [a[1] for a, _ in self.calls]


@pytest.fixture
def docker(monkeypatch, tmp_path):
    fake = _Docker()
    monkeypatch.setattr(svc, "_run", fake)
    monkeypatch.delenv(svc.MANAGE_ENV, raising=False)
    # A repo dir with a compose file, so the start path has one to name.
    (tmp_path / "domovoi").mkdir()
    (tmp_path / "domovoi" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(svc.settings, "repo_dir", str(tmp_path))
    return fake


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["always", "sometimes", "yes", "metered"])
async def test_yes_or_sometimes_starts_the_compose_service(docker, tmp_path, answer) -> None:
    result = await svc.reconcile(answer)
    assert (result.action, result.ok) == ("start", True)
    (argv, timeout), = docker.calls
    compose = tmp_path / "domovoi" / "docker-compose.yml"
    assert argv == [
        "docker", "compose", "-f", str(compose),
        "--project-directory", str(compose.parent),
        "up", "-d", "--no-deps", "searxng",
    ]
    # The first start pulls the pinned image.
    assert timeout == 600


@pytest.mark.asyncio
async def test_never_stops_a_running_container(docker) -> None:
    docker.running = "true"
    result = await svc.reconcile("never")
    assert (result.action, result.ok) == ("stop", True)
    assert [a for a, _ in docker.calls] == [
        ["docker", "inspect", "-f", "{{.State.Running}}", "domovoi-searxng"],
        ["docker", "stop", "domovoi-searxng"],
    ]
    assert docker.calls[1][1] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("running", ["false", None])   # stopped, or no such container
async def test_never_leaves_a_stopped_or_missing_container_alone(docker, running) -> None:
    docker.running = running
    result = await svc.reconcile("never")
    assert (result.action, result.ok) == ("none", True)
    assert docker.verbs() == ["inspect"]


@pytest.mark.asyncio
async def test_never_when_docker_cannot_say(docker) -> None:
    docker.fail = {"inspect": "Cannot connect to the Docker daemon"}
    result = await svc.reconcile("never")
    assert (result.action, result.ok) == ("stop", False)
    assert docker.verbs() == ["inspect"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["", "unset", "banana"])
async def test_unanswered_touches_nothing(docker, answer) -> None:
    result = await svc.reconcile(answer)
    assert (result.action, result.ok) == ("none", True)
    assert docker.calls == []


@pytest.mark.asyncio
async def test_default_answer_is_the_live_policy(docker) -> None:
    with egress.override_policy("sometimes"):
        assert (await svc.reconcile()).action == "start"
    with egress.override_policy(""):
        assert (await svc.reconcile()).action == "none"
    assert docker.verbs() == ["compose"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "false", "NO", "off"])
async def test_opt_out_never_touches_docker(docker, monkeypatch, value) -> None:
    monkeypatch.setenv(svc.MANAGE_ENV, value)
    for answer in ("always", "never"):
        result = await svc.reconcile(answer)
        assert (result.action, result.ok) == ("skipped", True)
    assert docker.calls == []


@pytest.mark.asyncio
async def test_a_failed_start_is_reported_not_raised(docker) -> None:
    docker.fail = {"compose": "line 1\nError response from daemon: pull access denied"}
    result = await svc.reconcile("always")
    assert (result.action, result.ok) == ("start", False)
    assert "pull access denied" in result.detail


@pytest.mark.asyncio
async def test_timeout_and_missing_docker_are_reported(docker) -> None:
    docker.raises = {"compose": subprocess.TimeoutExpired(["docker"], 600)}
    timed_out = await svc.reconcile("always")
    assert (timed_out.ok, "timed out" in timed_out.detail) == (False, True)
    docker.raises = {"compose": FileNotFoundError("docker")}
    missing = await svc.reconcile("always")
    assert (missing.ok, "not installed" in missing.detail) == (False, True)


@pytest.mark.asyncio
async def test_anything_unexpected_never_raises(docker) -> None:
    docker.raises = {"inspect": RuntimeError("weird")}
    result = await svc.reconcile("never")
    assert result.ok is False


@pytest.mark.asyncio
async def test_a_missing_compose_file_is_reported(docker, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(svc.settings, "repo_dir", str(tmp_path / "nowhere"))
    result = await svc.reconcile("always")
    assert (result.action, result.ok) == ("start", False)
    assert docker.calls == []


def test_compose_file_is_the_repos() -> None:
    assert svc.compose_file() == Path(svc.settings.repo_dir) / "domovoi" / "docker-compose.yml"
    assert svc.CONTAINER == "domovoi-searxng"


def test_the_pinned_image_is_a_digest() -> None:
    """Every box runs the same SearXNG, and a first start pulls exactly
    that (B8: started automatically, so it must be pinned)."""
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    block = compose.split("\n  searxng:\n", 1)[1].split("\n  #", 1)[0]
    image = next(l.split("image:", 1)[1].strip() for l in block.splitlines() if "image:" in l)
    assert image.startswith("searxng/searxng:")
    assert "@sha256:" in image and len(image.split("@sha256:", 1)[1]) == 64
    assert "container_name: domovoi-searxng" in block


# ─── The reapply hook: saved answer → background reconcile; never at boot ─


def test_schedule_without_a_loop_is_a_no_op(docker) -> None:
    svc.schedule_reconcile()
    assert docker.calls == []


@pytest.mark.asyncio
async def test_saving_the_answer_runs_the_hook(monkeypatch) -> None:
    from domovoi import main

    seen: list[str | None] = []

    async def fake_reconcile(answer=None):
        seen.append(answer)
        return svc.SearxngAction("none", True, "test")

    monkeypatch.setattr(svc, "reconcile", fake_reconcile)
    main._register_core_reapply_hooks()
    assert reapply._HOOKS["internet_access"]["searxng"] is svc.schedule_reconcile
    # Registering ran nothing.
    assert seen == []
    ran = reapply.run_for(["internet_access"])
    assert "internet_access:searxng" in ran
    for _ in range(3):
        await asyncio.sleep(0)
    assert seen == [None]          # reads the live policy when it runs
    assert not svc._PENDING        # the finished task was released


def test_never_run_at_boot() -> None:
    """D11: no docker call may slow or break a start. The lifespan only
    registers the hook; nothing at boot calls reconcile."""
    from domovoi import main

    lifespan_src = inspect.getsource(main.lifespan)
    assert "reconcile" not in lifespan_src
    assert "searxng" not in lifespan_src.lower()
    hooks_src = inspect.getsource(main._register_core_reapply_hooks)
    assert "schedule_reconcile, key=\"searxng\"" in hooks_src.replace("\n", " ").replace("  ", " ") \
        or 'searxng_service.schedule_reconcile, key="searxng"' in hooks_src
