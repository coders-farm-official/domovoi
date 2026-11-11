"""Room music players that stopped and stayed stopped are started again
(2026-11-11).

After an update restarted a Windows install's WSL VM, Docker restored the
five room players and every start failed (exit code 128): a failed start
is never retried under ``--restart unless-stopped``. The guard paged "A
container failed" for each, and 14 hours later a play still answered
"could not connect to MPD at 127.0.0.1:6653: Connection refused".

Three places now start such a room again, and none of them while a tamper
incident freezes the house's risky operations:

* the core's start: rooms its warm couldn't start are tried again in the
  background, a bounded number of times (mpd_provisioner.retry_warm);
* a music command that can't reach its room's player: the room is started
  once, then the command is tried again (clients.mpd.RealMPDClient,
  mpd_provisioner.heal_room);
* the container guard: one start, with backoff, before a stopped room is
  reported; a start that works is a note, not an incident, so no phone is
  told (container_guard.engine._auto_start).

Docker is a fake throughout; the guard's incidents and the warm's rooms are
the lane database's.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import mpd_provisioner
from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import RealMPDClient
from domovoi.clients.mpd_protocol import MPDConnectionError
from domovoi.container_guard import freeze, holds, store
from domovoi.container_guard.engine import ContainerGuard, GuardConfig
from domovoi.container_guard.expected import ExpectedSet, mpd_expectations
from domovoi.container_guard.model import (
    RUNNING,
    TAMPER,
    DockerView,
    Expected,
    Finding,
    Listed,
    Observed,
)
from domovoi.handlers.music import MusicHandler
from domovoi.models import Context
from domovoi.tests.conftest import requires_db

HOST = "127.0.0.1"
UNREACHABLE = "I couldn't reach the music player."


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    mpd_provisioner.reset_self_heal()
    holds.clear()
    freeze.forget()
    monkeypatch.setattr(mpd_module, "_room_ports", dict(mpd_module._room_ports))
    monkeypatch.setattr(mpd_module, "_clients", dict(mpd_module._clients))
    yield
    mpd_provisioner.reset_self_heal()
    holds.clear()
    freeze.forget()


def free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


async def fake_mpd(port: int) -> asyncio.AbstractServer:
    """A daemon that greets and says OK to every command."""

    async def serve(reader, writer):
        writer.write(b"OK MPD 0.23.5\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                if line.strip() == b"close":
                    break
                writer.write(b"OK\n")
                await writer.drain()
        finally:
            writer.close()

    return await asyncio.start_server(serve, HOST, port)


MPD_ID = "sha256:" + "a" * 64
MOUNTS = (("/var/lib/docker/volumes/v/_data", "/var/lib/mpd"), ("/srv/media/music", "/music"),
          ("/srv/media/audiobooks", "/music/audiobooks"),
          ("/opt/domovoi/domovoi/mpd.conf", "/etc/mpd.conf"))


def room_player(name: str, status: str = "exited", **kw) -> Observed:
    """A room's music player as the provisioner makes it."""
    base = dict(id=f"id-{name}", name=name, status=status, running=status == "running",
                paused=status == "paused", exit_code=128 if status == "exited" else 0,
                config_image="domovoi-mpd:latest", image_id=MPD_ID, mounts=MOUNTS,
                restart_policy="unless-stopped")
    base.update(kw)
    return Observed(**base)


class FakeRooms:
    """Containers by name with a state (and, if set, a shape); every start
    (by ID), create and post-start recorded."""

    def __init__(self, monkeypatch, **states: str | None) -> None:
        self.states = dict(states)
        self.shape: dict[str, dict] = {}
        self.starts: list[str] = []
        self.started_ids: list[str] = []
        self.creates: list[str] = []
        self.after: list[tuple[str | None, int]] = []
        self.fail_with: str | None = None
        self.frozen: str | None = None
        monkeypatch.setattr(mpd_provisioner, "_container_state", self.state)
        monkeypatch.setattr(mpd_provisioner, "_inspect_room", self.inspect)
        monkeypatch.setattr(mpd_provisioner, "_ensure_container", self.ensure)
        monkeypatch.setattr(mpd_provisioner, "_create_container", self.create)
        monkeypatch.setattr(mpd_provisioner, "_start_container", self.start)
        monkeypatch.setattr(mpd_provisioner, "_wait_for_mpd", self.wait)
        monkeypatch.setattr(mpd_provisioner, "_after_start", self.after_start)
        monkeypatch.setattr(mpd_provisioner, "frozen_by", self.frozen_by)
        monkeypatch.setattr(mpd_provisioner, "_trusted_image_ids", self.trusted)

    def _name(self, target: str) -> str:
        return target[3:] if target.startswith("id-") else target

    async def state(self, name):
        return self.states.get(self._name(name))

    async def inspect(self, target):
        name = self._name(target)
        st = self.states.get(name)
        return None if st is None else room_player(name, st, **self.shape.get(name, {}))

    async def start(self, target):
        name = self._name(target)
        self.starts.append(name)
        self.started_ids.append(target)
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.states[name] = "running"

    async def create(self, *, name, **_kw):
        self.creates.append(name)
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.states[name] = "running"

    async def ensure(self, *, name, **_kw):
        if self.states.get(name) is None:
            await self.create(name=name)
        elif self.states.get(name) != "running":
            await self.start(name)

    async def wait(self, *_a, **_k):
        return True

    async def after_start(self, room_id, port):
        self.after.append((room_id, port))

    async def frozen_by(self):
        return self.frozen

    async def trusted(self):
        return {MPD_ID}


# ─── the core's start ─────────────────────────────────────────────────────

ROOMS = ("zz_heal_office", "zz_heal_garage")


@pytest_asyncio.fixture
async def room_rows():
    from domovoi.db.session import session_scope

    async def clear():
        async with session_scope() as s:
            await s.execute(text("DELETE FROM mpd_rooms WHERE room_id = ANY(:r)"), {"r": list(ROOMS)})

    await clear()
    async with session_scope() as s:
        for i, room in enumerate(ROOMS):
            await s.execute(
                text("INSERT INTO mpd_rooms (room_id, control_port, http_port, container_name) "
                     "VALUES (:r, :c, :h, :n)"),
                {"r": room, "c": 16650 + i, "h": 18050 + i, "n": f"domovoi-mpd-{room}"},
            )
    yield
    await clear()


@requires_db
async def test_the_start_retries_in_the_background_the_rooms_its_warm_could_not_start(
    monkeypatch, room_rows,
) -> None:
    rooms = FakeRooms(monkeypatch, **{f"domovoi-mpd-{r}": "exited" for r in ROOMS})

    async def ensure(*, name, **kw):
        if name.endswith("office"):
            raise RuntimeError('make mountpoint "/music/podcasts": read-only file system')
        await rooms.start(name)

    monkeypatch.setattr(mpd_provisioner, "_ensure_container", ensure)
    scheduled: list = []
    monkeypatch.setattr(mpd_provisioner, "schedule_warm_retry", scheduled.append)

    assert await mpd_provisioner.warm_known_rooms() == 1
    assert [[r[0] for r in batch] for batch in scheduled] == [["zz_heal_office"]]
    assert scheduled[0][0] == ("zz_heal_office", 16650, 18050, "domovoi-mpd-zz_heal_office")
    # The port map is filled for the room that didn't start too: a command
    # to it can heal it later.
    assert mpd_module._room_ports["zz_heal_office"] == (16650, 18050)


async def test_the_retry_starts_a_room_once_what_it_needs_is_there(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    rooms.fail_with = "bind source path does not exist: /srv/media/music"
    seen: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(sec):
        seen.append(sec)
        if len(seen) == 2:          # the mount is there by the second look
            rooms.fail_with = None
        await real_sleep(0)

    monkeypatch.setattr(mpd_provisioner.asyncio, "sleep", sleep)
    still = await mpd_provisioner.retry_warm([("office", 6650, 8050, "domovoi-mpd-office")],
                                             delays=(10.0, 20.0, 40.0))
    assert still == []
    assert rooms.states["domovoi-mpd-office"] == "running"
    assert seen == [10.0, 20.0]     # stops looking once every room runs
    assert rooms.started_ids[-1] == "id-domovoi-mpd-office"     # by its ID
    assert rooms.after == [("office", 6650)]                     # the post-start hook, once


async def test_the_retry_is_bounded(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    rooms.fail_with = "port is already allocated"

    async def sleep(_sec):
        return None

    monkeypatch.setattr(mpd_provisioner.asyncio, "sleep", sleep)
    still = await mpd_provisioner.retry_warm([("office", 6650, 8050, "domovoi-mpd-office")],
                                             delays=(1.0, 1.0, 1.0))
    assert still == ["office"]
    assert len(rooms.starts) == 3


async def test_the_retry_starts_nothing_while_frozen(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    rooms.frozen = "incident #7"

    async def sleep(_sec):
        return None

    monkeypatch.setattr(mpd_provisioner.asyncio, "sleep", sleep)
    still = await mpd_provisioner.retry_warm([("office", 6650, 8050, "domovoi-mpd-office")],
                                             delays=(1.0, 1.0))
    assert still == ["office"] and rooms.starts == []


async def test_the_retry_runs_in_the_background_one_at_a_time(monkeypatch) -> None:
    gate = asyncio.Event()

    async def retry(rooms, delays=()):
        await gate.wait()
        return []

    monkeypatch.setattr(mpd_provisioner, "retry_warm", retry)
    first = mpd_provisioner.schedule_warm_retry([("office", 6650, 8050, "c")])
    second = mpd_provisioner.schedule_warm_retry([("garage", 6651, 8051, "d")])
    assert first is second and not first.done()
    gate.set()
    await first


# ─── a music command that can't reach its room ────────────────────────────

async def test_heal_starts_a_stopped_room_once_per_cooldown(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    mpd_module._room_ports["office"] = (6650, 8050)
    assert await mpd_provisioner.heal_room("office") is True
    assert rooms.starts == ["domovoi-mpd-office"]
    rooms.states["domovoi-mpd-office"] = "exited"       # stopped again at once
    assert await mpd_provisioner.heal_room("office") is False
    assert rooms.starts == ["domovoi-mpd-office"]       # inside the cooldown: no docker


async def test_heal_shares_one_start_between_commands_arriving_together(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    mpd_module._room_ports["office"] = (6650, 8050)
    results = await asyncio.gather(*(mpd_provisioner.heal_room("office") for _ in range(4)))
    assert results == [True] * 4 and rooms.starts == ["domovoi-mpd-office"]


@pytest.mark.parametrize("state", ["running", None])
async def test_heal_leaves_a_running_or_unknown_room_alone(monkeypatch, state) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": state})
    if state is not None:
        mpd_module._room_ports["office"] = (6650, 8050)
    assert await mpd_provisioner.heal_room("office") is False
    assert rooms.starts == []


async def test_heal_starts_nothing_while_frozen(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    rooms.frozen = "incident #7"
    mpd_module._room_ports["office"] = (6650, 8050)
    assert await mpd_provisioner.heal_room("office") is False
    assert rooms.starts == []


async def test_heal_that_docker_refuses_is_false_and_logged(monkeypatch, caplog) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    rooms.fail_with = 'make mountpoint "/music/podcasts": read-only file system'
    mpd_module._room_ports["office"] = (6650, 8050)
    assert await mpd_provisioner.heal_room("office") is False
    assert "read-only file system" in caplog.text


async def test_a_refused_command_starts_the_room_and_goes_through(monkeypatch) -> None:
    port = free_port()
    servers: list = []

    async def heal(room_id):
        assert room_id == "office"
        servers.append(await fake_mpd(port))
        return True

    monkeypatch.setattr(mpd_provisioner, "heal_room", heal)
    try:
        await RealMPDClient(HOST, port, room_id="office").pause()
    finally:
        for s in servers:
            s.close()
            await s.wait_closed()
    assert len(servers) == 1


async def test_a_refused_command_whose_room_won_t_start_fails_as_before(monkeypatch) -> None:
    port = free_port()
    asked: list[str] = []

    async def heal(room_id):
        asked.append(room_id)
        return False

    monkeypatch.setattr(mpd_provisioner, "heal_room", heal)
    with pytest.raises(MPDConnectionError, match="could not connect to MPD"):
        await RealMPDClient(HOST, port, room_id="office").pause()
    assert asked == ["office"]


async def test_a_client_with_no_room_never_heals(monkeypatch) -> None:
    async def heal(room_id):
        raise AssertionError("no room, no heal")

    monkeypatch.setattr(mpd_provisioner, "heal_room", heal)
    with pytest.raises(MPDConnectionError):
        await RealMPDClient(HOST, free_port()).pause()


def test_the_room_s_client_knows_its_room(monkeypatch) -> None:
    monkeypatch.setattr(mpd_module.settings, "use_stubs", False)
    mpd_module._room_ports["office"] = (6650, 8050)
    client = mpd_module.get_mpd_client_for("office")
    assert isinstance(client, RealMPDClient) and client.room_id == "office"


@pytest.mark.parametrize("starts", [True, False])
async def test_the_handler_s_pause_after_the_room_is_started(monkeypatch, starts) -> None:
    """What the room hears: "Paused." when the player could be started
    again, the old "I couldn't reach the music player." when it couldn't."""
    port = free_port()
    servers: list = []

    async def heal(_room_id):
        if starts:
            servers.append(await fake_mpd(port))
        return starts

    monkeypatch.setattr(mpd_provisioner, "heal_room", heal)
    mpd_module._clients["zz_heal_office"] = RealMPDClient(HOST, port, room_id="zz_heal_office")
    ctx = Context(session_id=None, room_id="zz_heal_office", online=True)
    try:
        reply = await MusicHandler()._simple_ack("pause", ctx)
    finally:
        for s in servers:
            s.close()
            await s.wait_closed()
    if starts:
        assert reply.text == "Paused." and reply.failure is None
    else:
        assert reply.text == UNREACHABLE and reply.failure == "unreachable"


# ─── the container guard ──────────────────────────────────────────────────

CFG = GuardConfig(failure_grace_sec=120, daemon_grace_sec=300, tamper_confirm_sec=25,
                  boot_grace_sec=0, interval_sec=30)
NAME = "cgtest-mpd-office"


PLAYER_ID = "m" * 64


def stopped_player(**kw) -> Observed:
    base = dict(id=PLAYER_ID, name=NAME, status="exited", running=False, exit_code=128,
                config_image="domovoi-mpd:latest", image_id=MPD_ID, mounts=MOUNTS,
                restart_policy="unless-stopped")
    base.update(kw)
    return Observed(**base)


def running_player() -> Observed:
    return stopped_player(status="running", running=True, exit_code=0)


class FakeDocker:
    def __init__(self) -> None:
        self.view = DockerView(ok=True)

    async def observe(self, *, claim, image_refs=()):
        return self.view

    def show(self, *observed: Observed) -> None:
        self.view = DockerView(ok=True)
        for o in observed:
            self.view.listed[o.name] = Listed(id=o.id, name=o.name)
            self.view.containers[o.name] = o


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def player_expected(**kw) -> Expected:
    (e,) = mpd_expectations([("office", NAME)], tag_ref="domovoi-mpd:latest", image_id=MPD_ID,
                            warm_done=True)
    return e if not kw else Expected(**{**e.__dict__, **kw})


def test_the_room_s_player_carries_its_room() -> None:
    assert player_expected().room_id == "office"


@pytest_asyncio.fixture
async def guard_env():
    from domovoi.db.session import engine as db_engine

    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE container_incidents, container_guard_baselines RESTART IDENTITY"))
    docker, clock = FakeDocker(), Clock()
    expected = ExpectedSet()
    expected.add([player_expected()])
    starts: list[tuple[str, str]] = []
    behaviour = {"result": "started", "error": None}

    async def starter(room_id, container):
        starts.append((room_id, container))
        if behaviour["error"]:
            raise RuntimeError(behaviour["error"])
        docker.show(running_player())
        return behaviour["result"]

    async def gather(_guard, _cfg):
        return expected

    async def no_hold(_guard, _cfg):
        return None

    guard = ContainerGuard(docker=docker, config=lambda: CFG, clock=clock, wall=lambda: 1.8e9,
                           gather=gather, global_hold=no_hold, starter=starter)
    yield guard, docker, clock, expected, starts, behaviour
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE container_incidents, container_guard_baselines RESTART IDENTITY"))


async def _check(guard, clock, seconds=30):
    clock.t += seconds
    freeze.forget()
    return await guard.check(force=True)


async def _open_rows():
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return await store.open_incidents(s)


@requires_db
async def test_a_stopped_player_the_guard_starts_is_a_note_not_an_incident(guard_env) -> None:
    guard, docker, clock, _, starts, _ = guard_env
    docker.show(stopped_player())
    status = await _check(guard, clock)
    assert starts == [("office", PLAYER_ID)]
    assert status["findings"] == [] and status["pending"] == []
    for _ in range(6):                      # three minutes: past every grace
        await _check(guard, clock)
    assert await _open_rows() == []         # nothing for a phone to post
    (note,) = guard.status["restarted"]
    assert note["room"] == "office" and note["exit_code"] == 128
    assert note["summary"] == ("The music player for office had stopped (exit code 128); "
                               "the guard started it again.")
    assert starts == [("office", PLAYER_ID)]


@requires_db
async def test_a_player_that_won_t_start_is_reported_with_docker_s_reason(guard_env) -> None:
    guard, docker, clock, _, starts, behaviour = guard_env
    behaviour["error"] = 'make mountpoint "/music/podcasts": read-only file system'
    docker.show(stopped_player())
    await _check(guard, clock)                      # t=30: tried once, failed
    assert len(starts) == 1
    await _check(guard, clock)                      # t=60: waits out the backoff
    assert len(starts) == 1
    await _check(guard, clock)                      # t=90: 60 s on, tried again
    assert len(starts) == 2
    for _ in range(3):
        await _check(guard, clock)
    (inc,) = await _open_rows()
    assert (inc["class"], inc["kind"], inc["container"]) == ("failure", "stopped", NAME)
    assert "exit code 128" in inc["summary"]
    assert "Starting it again failed: make mountpoint" in inc["summary"]
    assert inc["found"]["start_error"].endswith("read-only file system")
    assert guard.status["restarted"] == []
    # The next try waits longer (120 s after the second), and one that
    # works resolves the incident by itself.
    behaviour["error"] = None
    for _ in range(6):
        await _check(guard, clock)
    assert len(starts) == 3
    assert await _open_rows() == []


@requires_db
async def test_nothing_is_started_while_a_tamper_incident_freezes_the_house(guard_env) -> None:
    from domovoi.db.session import session_scope

    guard, docker, clock, _, starts, _ = guard_env
    async with session_scope() as s:
        await store.open_incident(s, Finding(
            key="tamper:image:cgtest-db:" + "sha256:" + "2" * 64, cls=TAMPER, kind="image",
            severity="error", container="cgtest-db", owner="core", label="the database",
            summary="The database is running a different image than the one Domovoi expects."))
    docker.show(stopped_player())
    for _ in range(6):
        await _check(guard, clock)
    assert starts == []
    kinds = sorted((r["class"], r["kind"]) for r in await _open_rows())
    assert kinds == [("failure", "stopped"), ("tamper", "image")]


@requires_db
async def test_only_a_room_s_stopped_player_is_started(guard_env) -> None:
    guard, docker, clock, expected, starts, _ = guard_env
    db = Expected(name="cgtest-db", owner="core", role="postgres", label="the database",
                  state=RUNNING, required=True, image_ref="postgres:16")
    expected.add([db])
    docker.show(
        stopped_player(status="paused", running=True, paused=True),
        Observed(id="d" * 64, name="cgtest-db", status="exited", running=False, exit_code=1,
                 config_image="postgres:16", image_id="sha256:" + "d" * 64),
    )
    for _ in range(5):
        await _check(guard, clock)
    assert starts == []
    assert {r["container"] for r in await _open_rows()} == {"cgtest-db", NAME}


@requires_db
async def test_a_held_room_is_not_started_by_the_guard(guard_env) -> None:
    guard, docker, clock, expected, starts, _ = guard_env
    expected.containers.clear()
    expected.add([player_expected(hold="the music player for office is starting")])
    docker.show(stopped_player())
    for _ in range(3):
        await _check(guard, clock)
    assert starts == [] and await _open_rows() == []


async def test_the_guard_s_default_starter_only_starts_and_holds_the_room(monkeypatch) -> None:
    from domovoi.container_guard import engine

    rooms = FakeRooms(monkeypatch, **{NAME: "exited"})
    mpd_module._room_ports["office"] = (6650, 8050)
    assert await engine.default_room_starter("office", f"id-{NAME}") == "started"
    assert rooms.started_ids == [f"id-{NAME}"]
    assert holds.held("room:office")                # settling for a minute
    for _ in range(3):
        await asyncio.sleep(0)
    assert rooms.after == [("office", 6650)]        # scheduled, kept referenced
    rooms.frozen = "incident #9"
    rooms.states[NAME] = "exited"
    assert await engine.default_room_starter("office", f"id-{NAME}") == "frozen"
    assert rooms.starts == [NAME]


# ─── the cause: a nested mount's placeholder gone from the music folder ───

@pytest.fixture
def media(monkeypatch, tmp_path):
    """A music folder whose ``audiobooks`` placeholder was deleted, with
    the spoken-audio folders the rooms mount inside it."""
    music, books, pods = tmp_path / "Music", tmp_path / "Audiobooks", tmp_path / "podcasts"
    for d in (music, books, pods, music / "podcasts"):
        d.mkdir()
    monkeypatch.setattr(mpd_provisioner.settings, "music_dir", str(music))
    monkeypatch.setattr(mpd_provisioner.settings, "audiobooks_dir", str(books))
    monkeypatch.setattr(mpd_provisioner.settings, "podcasts_dir", str(pods))
    return music


def _docker_that_needs_the_placeholders(monkeypatch, music, state="exited"):
    """``docker start`` fails as Docker does (exit code 128) unless both
    mountpoints exist in the music folder."""
    starts: list[bool] = []

    async def run_docker(*args, timeout=60.0):
        if args[:1] == ("inspect",) and "{{.State.Status}}" in args:
            return 0, state + "\n", ""
        if args[:1] == ("inspect",):
            return 0, '{"6600/tcp": [{"HostIp": "127.0.0.1", "HostPort": "6650"}]}', ""
        if args[:1] == ("start",):
            ok = (music / "audiobooks").is_dir() and (music / "podcasts").is_dir()
            starts.append(ok)
            if not ok:
                return 1, "", ('Error response from daemon: ... make mountpoint "/music/audiobooks": '
                               'mkdirat ...: read-only file system')
            return 0, args[1] + "\n", ""
        raise AssertionError(f"unexpected docker {args}")

    async def after(_room, _port):
        return None

    monkeypatch.setattr(mpd_provisioner, "_run_docker", run_docker)
    monkeypatch.setattr(mpd_provisioner, "_after_start", after)
    return starts


async def test_a_start_makes_a_deleted_placeholder_again(monkeypatch, media) -> None:
    """The incident: Music/audiobooks gone, every start failed with 128.
    The start (the warm's, the retry's, a command's heal) now makes it
    first, and the start works."""
    starts = _docker_that_needs_the_placeholders(monkeypatch, media)
    await mpd_provisioner._ensure_container(
        name="domovoi-mpd-office", image="domovoi-mpd:latest", control_port=6650,
        http_port=8050, music_dir=str(media), volume_name="v",
    )
    assert starts == [True]
    assert (media / "audiobooks").is_dir()
    assert list((media / "audiobooks").iterdir()) == []      # an empty mountpoint, nothing else


async def test_the_guard_s_start_makes_it_again_too(monkeypatch, media) -> None:
    starts = _docker_that_needs_the_placeholders(monkeypatch, media)

    async def not_frozen():
        return None

    async def inspect(target):
        return room_player("domovoi-mpd-office")

    async def trusted():
        return {MPD_ID}

    monkeypatch.setattr(mpd_provisioner, "frozen_by", not_frozen)
    monkeypatch.setattr(mpd_provisioner, "_inspect_room", inspect)
    monkeypatch.setattr(mpd_provisioner, "_trusted_image_ids", trusted)
    assert await mpd_provisioner.start_stopped_room("office", "domovoi-mpd-office",
                                                    why="test") == "started"
    assert starts == [True]


async def test_a_placeholder_that_can_t_be_made_is_logged_and_the_start_says_why(
    monkeypatch, media, caplog,
) -> None:
    """A music folder that isn't mounted yet is root's empty mount point:
    the folder can't be made, the start fails with Docker's words, and a
    later try (once it is mounted) works."""
    starts = _docker_that_needs_the_placeholders(monkeypatch, media)
    real_mkdir = mpd_provisioner.Path.mkdir

    def refuse(self, *a, **k):
        if self.name == "audiobooks":
            raise PermissionError(13, "Permission denied", str(self))
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(mpd_provisioner.Path, "mkdir", refuse)
    with pytest.raises(RuntimeError, match="read-only file system"):
        await mpd_provisioner._ensure_container(
            name="domovoi-mpd-office", image="domovoi-mpd:latest", control_port=6650,
            http_port=8050, music_dir=str(media), volume_name="v",
        )
    assert starts == [False]
    assert "could not create mountpoint" in caplog.text


# ─── review round 1: only Domovoi's own stopped player is ever started ────

@pytest.mark.parametrize("shape, why", [
    ({"privileged": True}, "privileged"),
    ({"mounts": MOUNTS + (("/var/run/docker.sock", "/var/run/docker.sock"),)}, "docker_socket"),
    ({"image_id": "sha256:" + "e" * 64, "config_image": "evil:latest"}, "another image"),
    ({"restart_policy": "no"}, "restart policy"),
    ({"mounts": MOUNTS + (("/", "/host"),)}, "root_mount"),
    ({"mounts": MOUNTS[1:]}, "lacks the /var/lib/mpd"),
])
async def test_a_stopped_container_that_isn_t_a_room_player_is_never_started(
    monkeypatch, caplog, shape, why,
) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    rooms.shape["domovoi-mpd-office"] = shape
    assert await mpd_provisioner.start_stopped_room("office", why="test") == "refused"
    assert rooms.starts == []
    assert why in caplog.text


async def test_the_start_goes_by_the_inspected_container_s_id(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    assert await mpd_provisioner.start_stopped_room("office", why="test") == "started"
    assert rooms.started_ids == ["id-domovoi-mpd-office"]


async def test_the_retry_and_heal_never_recreate_a_running_container(monkeypatch) -> None:
    """A running player is left exactly as it is (whatever it mounts or
    binds): recreating is the warm's call at the core's next start."""
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "running"})
    assert await mpd_provisioner._reconcile_room("office", 6650, 8050,
                                                 "domovoi-mpd-office") == "running"
    mpd_module._room_ports["office"] = (6650, 8050)
    assert await mpd_provisioner.heal_room("office") is False
    assert rooms.starts == [] and rooms.creates == [] and rooms.after == []


async def test_a_missing_container_is_created_and_said_so(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch)
    assert await mpd_provisioner._reconcile_room("office", 6650, 8050,
                                                 "domovoi-mpd-office") == "created"
    assert rooms.creates == ["domovoi-mpd-office"] and rooms.after == [("office", 6650)]


async def test_a_room_another_operation_holds_is_left_alone(monkeypatch) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    mpd_module._room_ports["office"] = (6650, 8050)
    with holds.holding("room:office", "the music player for office is being removed",
                       settle_sec=0):
        assert await mpd_provisioner._reconcile_room(
            "office", 6650, 8050, "domovoi-mpd-office") == "busy"
        assert await mpd_provisioner.heal_room("office") is False
    lock = asyncio.Lock()
    monkeypatch.setattr(mpd_provisioner, "_REBUILD_LOCK", lock)
    async with lock:
        assert await mpd_provisioner.start_stopped_room("office", why="test") == "busy"
    assert rooms.starts == []


async def test_the_freeze_check_fails_closed(monkeypatch) -> None:
    async def unreadable(_s):
        raise RuntimeError("database is away")

    monkeypatch.setattr(store, "frozen_by", unreadable)
    why = await mpd_provisioner.frozen_by()
    assert why is not None and "can't be read" in why


@requires_db
async def test_an_open_tamper_incident_is_a_freeze() -> None:
    from domovoi.db.session import engine as db_engine
    from domovoi.db.session import session_scope

    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE container_incidents RESTART IDENTITY"))
    try:
        assert await mpd_provisioner.frozen_by() is None
        async with session_scope() as s:
            iid, _ = await store.open_incident(s, Finding(
                key="tamper:image:x:y", cls=TAMPER, kind="image", severity="error",
                container="x", owner="core", label="x", summary="x"))
        assert await mpd_provisioner.frozen_by() == f"incident #{iid}"
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(text("TRUNCATE container_incidents RESTART IDENTITY"))


@pytest.mark.parametrize("never", [True, False])
async def test_after_a_start_internet_streams_leave_the_queue_when_internet_is_never(
    monkeypatch, never,
) -> None:
    from domovoi import egress, mpd_internet

    pins: list[int] = []
    purged: list[str] = []

    async def pin(port):
        pins.append(port)

    async def purge(room_id, client):
        purged.append(room_id)
        if len(purged) == 1:
            raise ConnectionRefusedError("still starting")
        return 2

    monkeypatch.setattr(mpd_provisioner, "_pin_startup_volume", pin)
    monkeypatch.setattr(mpd_internet, "purge_room", purge)
    monkeypatch.setattr(mpd_provisioner, "PURGE_RETRY_SEC", 0.0)
    monkeypatch.setattr(egress, "internet_turned_off", lambda: never)
    await mpd_provisioner._after_start("office", 6650)
    assert pins == [6650]
    assert purged == (["office", "office"] if never else [])


async def test_a_heal_that_keeps_failing_warns_once_an_hour(monkeypatch, caplog) -> None:
    rooms = FakeRooms(monkeypatch, **{"domovoi-mpd-office": "exited"})
    rooms.fail_with = 'make mountpoint "/music/audiobooks": read-only file system'
    mpd_module._room_ports["office"] = (6650, 8050)
    for _ in range(3):
        mpd_provisioner._heal_last.clear()          # past the cooldown each time
        assert await mpd_provisioner.heal_room("office") is False
    assert len(rooms.starts) == 3
    warned = [r for r in caplog.records
              if r.levelname == "WARNING" and "read-only file system" in r.getMessage()]
    assert len(warned) == 1


async def test_a_room_s_container_is_made_from_the_local_image_only(monkeypatch, tmp_path) -> None:
    seen: list[tuple] = []

    async def run_docker(*args, timeout=60.0):
        seen.append(args)
        return 0, "id\n", ""

    monkeypatch.setattr(mpd_provisioner, "_run_docker", run_docker)
    monkeypatch.setattr(mpd_provisioner.settings, "podcasts_dir", str(tmp_path / "nope1"))
    monkeypatch.setattr(mpd_provisioner.settings, "audiobooks_dir", str(tmp_path / "nope2"))
    await mpd_provisioner._create_container(
        name="domovoi-mpd-office", image="domovoi-mpd:latest", control_port=6650,
        http_port=8050, music_dir=str(tmp_path), volume_name="v")
    (run,) = seen
    assert run[:2] == ("run", "-d") and run[run.index("--pull") + 1] == "never"


# ─── the guard: the reviewer's scenario and its kin ───────────────────────

@requires_db
async def test_a_foreign_privileged_container_under_a_room_s_name_is_never_started(
    guard_env,
) -> None:
    """Review 2026-11-11 (B1), as proved: a --privileged container with the
    Docker socket mounted, named like the room's player, its payload
    exiting at once. Before the fix the guard started it 20 times and
    reported nothing. Now: never started, reported as tampering."""
    guard, docker, clock, _, starts, _ = guard_env
    evil = stopped_player(id="e" * 64, config_image="evil:latest", image_id="sha256:" + "e" * 64,
                          privileged=True, exit_code=0, restart_policy="no",
                          mounts=(("/var/run/docker.sock", "/var/run/docker.sock"),))
    docker.show(evil)
    for _ in range(20):
        await _check(guard, clock)
    assert starts == []
    rows = await _open_rows()
    assert ("tamper", "image") in {(r["class"], r["kind"]) for r in rows}
    stopped = [r for r in rows if r["kind"] == "stopped"]
    assert stopped and "Not started" in stopped[0]["summary"]


@requires_db
async def test_domovoi_s_image_with_a_risky_setting_is_not_started_either(guard_env) -> None:
    guard, docker, clock, _, starts, _ = guard_env
    docker.show(stopped_player(
        mounts=MOUNTS + (("/var/run/docker.sock", "/var/run/docker.sock"),)))
    for _ in range(3):
        await _check(guard, clock)
    assert starts == []
    assert ("tamper", "config") in {(r["class"], r["kind"]) for r in await _open_rows()}


@requires_db
async def test_another_shape_keeps_the_stopped_finding_with_the_reason(guard_env) -> None:
    guard, docker, clock, _, starts, _ = guard_env
    docker.show(stopped_player(restart_policy="no"))
    for _ in range(6):
        await _check(guard, clock)
    assert starts == []
    (inc,) = await _open_rows()
    assert inc["kind"] == "stopped" and "Not started: its restart policy is 'no'" in inc["summary"]


@requires_db
async def test_a_player_that_keeps_stopping_is_reported_after_three_starts(guard_env) -> None:
    guard, docker, clock, _, starts, _ = guard_env
    for _ in range(8):
        docker.show(stopped_player())          # down again at every look
        await _check(guard, clock)
    assert len(starts) == 3
    (inc,) = await _open_rows()
    assert inc["kind"] == "stopped"
    assert "Domovoi started it 3 times in the last 30 minutes; it keeps stopping." in inc["summary"]
    # Half an hour later the count has run out: it is tried again.
    clock.t += 30 * 60
    docker.show(stopped_player())
    await _check(guard, clock)
    assert len(starts) == 4


@requires_db
async def test_the_guard_starts_nothing_when_the_freeze_can_t_be_read(
    guard_env, monkeypatch,
) -> None:
    guard, docker, clock, _, starts, _ = guard_env

    async def unreadable(_s):
        raise RuntimeError("database is away")

    monkeypatch.setattr(store, "frozen_by", unreadable)
    docker.show(stopped_player())
    for _ in range(3):
        await _check(guard, clock)
    assert starts == []
