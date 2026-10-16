"""A room id used again after "Retire room", in the same core process, gets a
music player with its ``mpd_rooms`` row (2026-10-13).

``mpd_provisioner.remove_room`` dropped the container and the row but not the
process's port cache (``clients.mpd._room_ports``). The next connect of a
room with the same id took the cached ports, started a container and wrote
no row; the next retire then found no row and left that container running,
and its ports looked free to the allocator. Found by the Windows
satellite's end-to-end run (enrol, retire, enrol again as the same room).

Docker is a fake here; the rows are the lane database's.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi import mpd_provisioner
from domovoi.clients import mpd as mpd_module
from domovoi.tests.conftest import requires_db

pytestmark = requires_db

ROOM = "zz_reuse_after_retire"


@pytest.fixture
def docker(monkeypatch):
    """Containers as a set of names; every docker call recorded."""
    running: set[str] = set()
    calls: list[tuple[str, ...]] = []

    async def ensure_container(*, name, **kw):
        calls.append(("ensure", name, kw["control_port"], kw["http_port"]))
        running.add(name)

    async def container_state(name):
        return "running" if name in running else None

    async def run_docker(*args, timeout=60.0):
        calls.append(args)
        if args[:2] == ("rm", "-f"):
            running.discard(args[2])
        return 0, "", ""

    async def wait_for_mpd(*a, **k):
        return True

    monkeypatch.setattr(mpd_provisioner, "_ensure_container", ensure_container)
    monkeypatch.setattr(mpd_provisioner, "_container_state", container_state)
    monkeypatch.setattr(mpd_provisioner, "_run_docker", run_docker)
    monkeypatch.setattr(mpd_provisioner, "_wait_for_mpd", wait_for_mpd)
    monkeypatch.setattr(mpd_module, "_room_ports", dict(mpd_module._room_ports))
    monkeypatch.setattr(mpd_module, "_clients", dict(mpd_module._clients))
    return running, calls


async def _row(room_id: str):
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return (await s.execute(text("SELECT control_port, http_port FROM mpd_rooms WHERE room_id = :r"),
                                {"r": room_id})).first()


@pytest.fixture(autouse=True)
async def no_leftover_row():
    from domovoi.db.session import session_scope

    async def clear():
        async with session_scope() as s:
            await s.execute(text("DELETE FROM mpd_rooms WHERE room_id = :r"), {"r": ROOM})
    await clear()
    yield
    await clear()


async def test_retire_forgets_the_room_s_ports_in_this_process(docker):
    running, _ = docker
    await mpd_provisioner.ensure_room(ROOM)
    assert ROOM in mpd_module._room_ports and await _row(ROOM) is not None
    mpd_module._clients[ROOM] = object()
    got = await mpd_provisioner.remove_room(ROOM)
    assert got == {"container": True, "row": True}
    assert ROOM not in mpd_module._room_ports and ROOM not in mpd_module._clients
    assert not running


async def test_a_room_id_used_again_after_retire_gets_its_row_and_can_be_retired_again(docker):
    running, _ = docker
    first = await mpd_provisioner.ensure_room(ROOM)
    await mpd_provisioner.remove_room(ROOM)
    second = await mpd_provisioner.ensure_room(ROOM)
    row = await _row(ROOM)
    assert row is not None, "the reused room's music player has its row"
    assert (int(row[0]), int(row[1])) == second
    assert mpd_module._room_ports[ROOM] == second
    got = await mpd_provisioner.remove_room(ROOM)
    assert got == {"container": True, "row": True}, "the second retire finds and removes it"
    assert not running
    del first
