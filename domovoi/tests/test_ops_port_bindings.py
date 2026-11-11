"""Host-side port exposure of the containers the core runs (OPS-1).

Pure file / argv checks — no Docker, no DB, no ``requires_db`` — so they can
never skip. Two contracts:

* ``docker-compose.yml`` publishes Postgres and Letta on ``127.0.0.1`` only
  (the core, the web backend, Flyway and pytest are their only clients and
  all run on this host); the Postgres password comes from ``.env`` with the
  historical default as the fallback so an existing install keeps working.
* ``mpd_provisioner`` publishes each room's MPD control port on
  ``127.0.0.1`` and leaves the HTTP stream port LAN-published, because the
  satellites pull the audio stream from it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from domovoi import mpd_provisioner

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "domovoi" / "docker-compose.yml"


# ─── docker-compose.yml ───────────────────────────────────────────────────

def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _published(service: str) -> list[str]:
    return [str(p) for p in _compose()["services"][service].get("ports", [])]


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("postgres", "127.0.0.1:6432:5432"),
        ("letta", "127.0.0.1:6283:8283"),
        ("searxng", "127.0.0.1:6888:8080"),
    ],
)
def test_compose_publishes_host_only_services_on_loopback(service: str, expected: str) -> None:
    """A published port with no host address means every interface. The
    services nothing on the LAN talks to say 127.0.0.1 explicitly."""
    ports = _published(service)
    assert ports == [expected], f"{service} publishes {ports}"


def test_compose_publishes_nothing_on_all_interfaces() -> None:
    """Belt and braces for any service added later: every published port
    in the file names a host address."""
    for name, svc in _compose()["services"].items():
        for port in svc.get("ports", []):
            spec = str(port)
            assert spec.count(":") == 2 and spec.split(":")[0] == "127.0.0.1", (
                f"service {name!r} publishes {spec!r} without a loopback host address"
            )


def test_compose_reads_postgres_password_from_env_with_legacy_fallback() -> None:
    """``POSTGRES_PASSWORD`` is interpolated from ``.env`` (what the fresh
    bootstrap writes) and falls back to the template default so an install
    whose ``.env`` predates the bootstrap still matches its volume."""
    env = _compose()["services"]["postgres"]["environment"]
    assert env["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD:-domovoi}"
    for svc in ("flyway", "flyway-test"):
        cmd = _compose()["services"][svc]["command"]
        assert "-password=${POSTGRES_PASSWORD:-domovoi}" in cmd, svc
        assert "-password=domovoi" not in cmd.replace("${POSTGRES_PASSWORD:-domovoi}", ""), svc


# ─── mpd_provisioner argv ─────────────────────────────────────────────────

def _pairs(argv: list[str], flag: str) -> list[str]:
    """Values following every occurrence of ``flag`` in ``argv``."""
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


@pytest.fixture
def docker_calls(monkeypatch):
    """Capture every ``docker …`` argv the provisioner issues; ``docker run``
    succeeds, ``inspect`` reports the container as absent."""
    calls: list[list[str]] = []

    async def fake_run_docker(*args: str, timeout: float = 60.0):
        calls.append(list(args))
        if args[0] == "inspect":
            return 1, "", "No such object"
        return 0, "", ""

    monkeypatch.setattr(mpd_provisioner, "_run_docker", fake_run_docker)
    return calls


async def test_create_container_binds_control_port_to_loopback_and_stream_to_lan(
    docker_calls, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(mpd_provisioner.settings, "podcasts_dir", str(tmp_path / "no-such-podcasts"))
    monkeypatch.setattr(mpd_provisioner.settings, "audiobooks_dir", str(tmp_path / "no-such-audiobooks"))

    await mpd_provisioner._create_container(
        name="domovoi-mpd-kitchen",
        image="domovoi-mpd:latest",
        control_port=6650,
        http_port=8050,
        music_dir=tmp_path.as_posix(),
        volume_name="domovoi-mpd-kitchen-data",
    )

    runs = [c for c in docker_calls if c[:2] == ["run", "-d"]]
    assert len(runs) == 1
    published = _pairs(runs[0], "-p")
    assert "127.0.0.1:6650:6600" in published, published
    assert "8050:8001" in published, published
    # Nothing else is published, and the stream port carries no host address.
    assert sorted(published) == ["127.0.0.1:6650:6600", "8050:8001"]


async def test_ensure_container_recreates_a_container_whose_control_port_is_wide_open(
    monkeypatch, tmp_path
) -> None:
    """A container made before the loopback bind existed keeps its old
    ``-p 6650:6600`` binding forever (docker can't edit it in place), so the
    provisioner removes it and runs a fresh one. The data volume is reused."""
    calls: list[list[str]] = []
    removed = False

    async def fake_run_docker(*args: str, timeout: float = 60.0):
        nonlocal removed
        calls.append(list(args))
        if args[0] == "inspect" and "{{.State.Status}}" in args:
            return (1, "", "gone") if removed else (0, "running\n", "")
        if args[0] == "inspect" and "{{json .HostConfig.PortBindings}}" in args:
            return 0, '{"6600/tcp":[{"HostIp":"","HostPort":"6650"}],"8001/tcp":[{"HostIp":"","HostPort":"8050"}]}\n', ""
        if args[:2] == ["rm", "-f"]:
            removed = True
        return 0, "", ""

    async def no_pin(_port: int) -> None:
        return None

    monkeypatch.setattr(mpd_provisioner, "_run_docker", fake_run_docker)
    monkeypatch.setattr(mpd_provisioner, "_pin_startup_volume", no_pin)
    monkeypatch.setattr(mpd_provisioner.settings, "podcasts_dir", str(tmp_path / "np"))
    monkeypatch.setattr(mpd_provisioner.settings, "audiobooks_dir", str(tmp_path / "na"))

    await mpd_provisioner._ensure_container(
        name="domovoi-mpd-kitchen",
        image="domovoi-mpd:latest",
        control_port=6650,
        http_port=8050,
        music_dir=tmp_path.as_posix(),
        volume_name="domovoi-mpd-kitchen-data",
    )

    assert ["rm", "-f", "domovoi-mpd-kitchen"] in calls
    runs = [c for c in calls if c[:2] == ["run", "-d"]]
    assert len(runs) == 1
    assert "127.0.0.1:6650:6600" in _pairs(runs[0], "-p")
    assert "domovoi-mpd-kitchen-data:/var/lib/mpd" in _pairs(runs[0], "-v")


async def test_ensure_container_leaves_a_loopback_bound_running_container_alone(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    async def fake_run_docker(*args: str, timeout: float = 60.0):
        calls.append(list(args))
        if args[0] == "inspect" and "{{.State.Status}}" in args:
            return 0, "running\n", ""
        if args[0] == "inspect" and "{{json .HostConfig.PortBindings}}" in args:
            return 0, '{"6600/tcp":[{"HostIp":"127.0.0.1","HostPort":"6650"}],"8001/tcp":[{"HostIp":"","HostPort":"8050"}]}\n', ""
        raise AssertionError(f"unexpected docker call {args}")

    monkeypatch.setattr(mpd_provisioner, "_run_docker", fake_run_docker)

    await mpd_provisioner._ensure_container(
        name="domovoi-mpd-kitchen",
        image="domovoi-mpd:latest",
        control_port=6650,
        http_port=8050,
        music_dir="/music",
        volume_name="domovoi-mpd-kitchen-data",
    )

    assert all(c[0] == "inspect" for c in calls), calls


async def test_ensure_container_does_not_recreate_when_bindings_are_unreadable(
    monkeypatch,
) -> None:
    """An inspect that fails (docker hiccup) is not evidence of a wide-open
    port; the running container is left alone."""
    calls: list[list[str]] = []

    async def fake_run_docker(*args: str, timeout: float = 60.0):
        calls.append(list(args))
        if args[0] == "inspect" and "{{.State.Status}}" in args:
            return 0, "running\n", ""
        if args[0] == "inspect":
            return 1, "", "error"
        raise AssertionError(f"unexpected docker call {args}")

    monkeypatch.setattr(mpd_provisioner, "_run_docker", fake_run_docker)

    await mpd_provisioner._ensure_container(
        name="domovoi-mpd-kitchen",
        image="domovoi-mpd:latest",
        control_port=6650,
        http_port=8050,
        music_dir="/music",
        volume_name="domovoi-mpd-kitchen-data",
    )
    assert not any(c[:2] == ["rm", "-f"] for c in calls)
