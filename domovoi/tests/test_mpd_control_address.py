"""The address the MPD control port is PUBLISHED on and the address every
client DIALS must be the same value (F-046).

OPS-1 narrowed the per-room MPD control port from every interface to
``-p 127.0.0.1:<port>:6600`` — correct, and it stays — but ``mpd_host``,
which is what the core, the provisioner and the web backend all dial, was
still the dual-stack name ``localhost``. ``localhost`` resolves to ``::1``
first, nothing listens there, and on Windows that attempt TIMES OUT rather
than refusing. The core has no connect deadline so it merely waited ~2 s;
the dashboard's ``_read_mpd`` gives up after 1.5 s and reports the room as
stopped, so ``GET /api/music/now-playing`` answered ``state: "stop"``,
``song: null`` for every room while MPD was genuinely playing.

The cure is one value in one place: ``config.MPD_CONTROL_BIND``, which the
provisioner binds to and which ``mpd_host`` is normalised to. These tests
pin that agreement from both ends, plus the shipped ``.env.example``.

Pure argv / string / settings checks — no Docker, no DB, no
``requires_db`` — so they can never skip.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from domovoi import mpd_provisioner
from domovoi.config import MPD_CONTROL_BIND, Settings, settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / "domovoi" / ".env.example"


# ─── bind address ≡ dial address ──────────────────────────────────────────

def test_the_address_clients_dial_is_the_address_the_control_port_publishes_on() -> None:
    """The whole finding in one line. ``settings.mpd_host`` is what
    ``RealMPDClient``, ``_wait_for_tcp`` and the web's ``_read_mpd`` are
    handed; ``_CONTROL_BIND`` is what ``docker run -p`` publishes."""
    assert settings.mpd_host == mpd_provisioner._CONTROL_BIND


def test_the_provisioner_does_not_keep_its_own_copy_of_the_bind_address() -> None:
    """Two literals that must match are a defect waiting to happen — that
    is precisely how this one happened. One constant, imported."""
    assert mpd_provisioner._CONTROL_BIND is MPD_CONTROL_BIND


def test_the_control_bind_is_an_ipv4_literal_not_a_name() -> None:
    """A name here would reintroduce the resolver in front of a bind that
    exists on exactly one address family."""
    assert MPD_CONTROL_BIND == "127.0.0.1"


# ─── mpd_host normalisation ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "configured",
    ["localhost", "LocalHost", " localhost ", "localhost.localdomain",
     "ip6-localhost", "::1", "[::1]", ""],
)
def test_a_dual_stack_loopback_host_is_pinned_to_the_control_bind(configured: str) -> None:
    """Every install made before OPS-1 has ``MPD_HOST=localhost`` in its
    .env, and so did ``.env.example``. Those installs must heal on the
    next restart without an operator editing anything."""
    assert Settings(mpd_host=configured).mpd_host == MPD_CONTROL_BIND


@pytest.mark.parametrize("configured", ["127.0.0.1", "10.0.0.5", "mpd.example.lan"])
def test_a_host_that_is_not_a_loopback_alias_is_left_exactly_as_written(configured: str) -> None:
    """Normalising loopback NAMES is a repair; rewriting an address the
    operator chose would be a surprise."""
    assert Settings(mpd_host=configured).mpd_host == configured


# ─── the argv the container is actually created with ──────────────────────

def _pairs(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


@pytest.fixture
def docker_calls(monkeypatch):
    """Capture every ``docker …`` argv; ``inspect`` says "no such object"
    so ``_create_container`` runs its happy path."""
    calls: list[list[str]] = []

    async def fake_run_docker(*args: str, timeout: float = 60.0):
        calls.append(list(args))
        if args[0] == "inspect":
            return 1, "", "No such object"
        return 0, "", ""

    monkeypatch.setattr(mpd_provisioner, "_run_docker", fake_run_docker)
    return calls


async def test_docker_run_publishes_the_control_port_on_the_address_the_core_dials(
    docker_calls, tmp_path, monkeypatch
) -> None:
    """The durable form of the assertion: read the published host address
    straight out of the ``docker run`` argv and compare it with
    ``settings.mpd_host``. It fails whichever of the two drifts."""
    monkeypatch.setattr(mpd_provisioner.settings, "podcasts_dir", str(tmp_path / "np"))
    monkeypatch.setattr(mpd_provisioner.settings, "audiobooks_dir", str(tmp_path / "na"))

    await mpd_provisioner._create_container(
        name="domovoi-mpd-kitchen",
        image="domovoi-mpd:latest",
        control_port=6650,
        http_port=8050,
        music_dir=tmp_path.as_posix(),
        volume_name="domovoi-mpd-kitchen-data",
    )

    run = next(c for c in docker_calls if c[:2] == ["run", "-d"])
    control = next(p for p in _pairs(run, "-p") if p.endswith(":6600"))
    host_ip, _, rest = control.rpartition(":6600")[0].rpartition(":")
    assert host_ip == settings.mpd_host, (
        f"control port published on {host_ip!r} but clients dial "
        f"{settings.mpd_host!r} (argv fragment {control!r})"
    )
    assert rest == "6650"

    # OPS-1's other half, unchanged: the stream port stays LAN-published
    # because the satellites fetch the audio over the LAN.
    assert "8050:8001" in _pairs(run, "-p")


# ─── the shipped example file ─────────────────────────────────────────────

def _example_values() -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def test_env_example_ships_the_control_bind_as_mpd_host() -> None:
    """A fresh box copies ``.env.example`` to ``.env``, so the example IS
    the effective default. Shipping ``localhost`` there would hand every
    new install the failure the validator then has to repair."""
    env = _example_values()
    assert env.get("MPD_HOST") == MPD_CONTROL_BIND, env.get("MPD_HOST")


def test_env_example_still_points_the_pi_stream_at_a_lan_name() -> None:
    """Guard against over-applying the fix: MPD_HTTP_BASE is fetched by
    the Pis, not by this host, and must not become a loopback address."""
    base = _example_values().get("MPD_HTTP_BASE", "")
    assert base and "localhost" not in base and "127.0.0.1" not in base, base


# ─── the dashboard no longer fails silently ───────────────────────────────

def test_an_unreadable_daemon_is_warned_about_once_per_outage(caplog) -> None:
    """``_read_mpd`` answers ``state: "stop"`` for a daemon it cannot
    read, which looks exactly like an idle room. F-046 hid behind that for
    a whole verification run because the reason was logged at debug. It
    warns now — once, not once per poll, because the dashboard polls."""
    from web.backend.api import music as music_api

    key = ("127.0.0.1", 6650)
    music_api._MPD_READ_FAILING.discard(key)
    try:
        with caplog.at_level(logging.DEBUG, logger=music_api.log.name):
            music_api._note_mpd_read_failure(*key, "connect", TimeoutError())
            music_api._note_mpd_read_failure(*key, "connect", TimeoutError())
            music_api._note_mpd_read_failure(*key, "connect", TimeoutError())

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        assert "6650" in warnings[0].getMessage()
        assert len([r for r in caplog.records if r.levelno == logging.DEBUG]) == 2

        # A daemon that comes back re-arms the warning for the next outage.
        caplog.clear()
        music_api._MPD_READ_FAILING.discard(key)
        with caplog.at_level(logging.DEBUG, logger=music_api.log.name):
            music_api._note_mpd_read_failure(*key, "connect", TimeoutError())
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    finally:
        music_api._MPD_READ_FAILING.discard(key)
