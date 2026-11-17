"""Friend Houses in the running core (domovoi/friend_houses/service.py), the
Tor container's arguments (tor_runtime.py) and the guard's expectation.

Docker and the database are fakes here: what's pinned is what the core
DECIDES (off means nothing runs, "never" stops the container, the first
friend restarts rather than reloads) and the hardening it asks Docker for.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from domovoi.container_guard.expected import tor_expectation
from domovoi.container_guard.model import RESERVED_CONTAINER_NAMES, RUNNING, STOPPED
from domovoi.friend_houses import service as svc
from domovoi.friend_houses import store
from domovoi.friend_houses import tor_config as tc
from domovoi.friend_houses import tor_runtime as rt

linux_only = pytest.mark.skipif(sys.platform == "win32", reason="Friend Houses runs on Linux")


class _Docker:
    def __init__(self, state: str = "absent") -> None:
        self.calls: list[str] = []
        self.container = state

    async def state(self) -> str:
        self.calls.append("state")
        return self.container

    async def start(self, *, data, run) -> None:
        self.calls.append("start")
        self.container = "running"

    async def stop(self) -> None:
        self.calls.append("stop")
        self.container = "stopped"

    async def restart(self) -> None:
        self.calls.append("restart")

    async def reload(self) -> None:
        self.calls.append("reload")


def _friend(n: int) -> store.FriendRow:
    return store.FriendRow(f"f{n}", bytes([n]) * 32, bytes([n + 1]) * 32, bytes(range(32)),
                           bytes([n + 2]) * 32, bytes([n + 3]) * 32, f"Friend {n}", "active")


@pytest.fixture
def house(tmp_path, monkeypatch):
    from domovoi import egress
    from domovoi.config import settings
    from domovoi.friend_houses import identity as fid

    docker = _Docker()
    friends: list[store.FriendRow] = []
    listener = {"running": False}
    monkeypatch.setattr(svc, "_root", lambda: tmp_path / "friend-houses")
    monkeypatch.setattr(fid, "keys_path", lambda: tmp_path / "friend-houses" / "house-keys.json")
    for name in ("state", "start", "stop", "restart", "reload"):
        monkeypatch.setattr(rt, name, getattr(docker, name))

    @asynccontextmanager
    async def fake_scope():
        yield None

    async def fake_friends(_s):
        return list(friends)

    async def fake_onion(_folder):
        return bytes(range(32))

    async def fake_epoch():
        return b"\x05" * 8

    async def fake_ensure(_folder):
        listener["running"] = True

    async def fake_stop():
        listener["running"] = False

    monkeypatch.setattr("domovoi.db.session.session_scope", fake_scope)
    monkeypatch.setattr(store, "active_friends", fake_friends)
    monkeypatch.setattr(svc, "_wait_for_onion", fake_onion)
    monkeypatch.setattr(svc, "current_epoch", fake_epoch)
    monkeypatch.setattr(svc, "_ensure_listener", fake_ensure)
    monkeypatch.setattr(svc, "_stop_listener", fake_stop)
    monkeypatch.setattr(svc, "RELOAD_MIN_INTERVAL_SEC", 0.0)
    monkeypatch.setitem(svc._STATE, "applied", None)
    monkeypatch.setitem(svc._STATE, "last_reload", 0.0)
    monkeypatch.setattr(settings, "friend_houses_enabled", False, raising=False)
    policy = {"value": "always"}
    monkeypatch.setattr(egress, "policy", lambda: policy["value"])

    class House:
        pass

    h = House()
    h.docker, h.friends, h.listener, h.policy, h.root = docker, friends, listener, policy, tmp_path
    h.enable = lambda on=True: monkeypatch.setattr(settings, "friend_houses_enabled", on, raising=False)
    return h


@linux_only
async def test_off_by_default_nothing_runs_and_docker_is_never_asked(house) -> None:
    await svc.reconcile()
    assert house.docker.calls == []
    assert not (house.root / "friend-houses").exists()  # no keys, no folder
    assert svc.status()["status"] == "off"
    assert not svc.tor_wanted()


@linux_only
async def test_on_with_no_friends_still_runs_nothing(house) -> None:
    house.enable()
    await svc.reconcile()
    assert house.docker.calls == []
    assert svc.status()["status"] == "stopped"


@linux_only
async def test_the_first_friend_starts_tor_with_the_friend_already_authorized(house) -> None:
    house.enable()
    house.friends.append(_friend(1))
    await svc.reconcile()
    assert house.docker.calls == ["start"]
    tor = house.root / "friend-houses" / "tor" / "data"
    torrc = (tor / "torrc").read_text()
    assert "hs-main" in torrc and "ControlPort" not in torrc
    # The client key is on disk BEFORE the daemon first starts: never an
    # open onion (spike S1 finding 1).
    assert (tor / "hs-main" / "authorized_clients" / "f1.auth").exists()
    assert (tor / "clientauth" / "f1.auth_private").exists()
    assert house.listener["running"]
    assert svc.tor_wanted() and svc.status()["status"] == "on"
    assert (house.root / "friend-houses" / "house-keys.json").exists()


@linux_only
async def test_never_stops_the_container_and_the_listener(house) -> None:
    house.enable()
    house.friends.append(_friend(1))
    await svc.reconcile()
    house.policy["value"] = "never"
    await svc.reconcile()
    assert house.docker.calls == ["start", "stop"]
    assert not house.listener["running"]
    assert not svc.tor_wanted()
    assert "never" in svc.status()["detail"]


@linux_only
async def test_turning_it_off_stops_everything_but_keeps_the_keys(house) -> None:
    house.enable()
    house.friends.append(_friend(1))
    await svc.reconcile()
    house.enable(False)
    await svc.reconcile()
    assert house.docker.calls[-1] == "stop"
    assert (house.root / "friend-houses" / "house-keys.json").exists()
    assert svc.status()["status"] == "off"


@linux_only
async def test_a_container_left_running_is_stopped_at_boot_under_never(house) -> None:
    house.enable()
    house.friends.append(_friend(1))
    await svc.reconcile()            # sets the house up
    svc._STATE["applied"] = None     # a core restart: nothing applied yet
    house.docker.container = "running"
    house.policy["value"] = "never"
    await svc.reconcile()
    assert house.docker.calls[-1] == "stop"


@linux_only
async def test_friend_changes_reload_and_rewrite_the_key_files(house) -> None:
    house.enable()
    house.friends.extend([_friend(1), _friend(2)])
    await svc.reconcile()
    house.friends.pop(0)              # friend 1 removed
    await svc.reconcile()
    assert house.docker.calls == ["start", "reload"]
    auth = house.root / "friend-houses" / "tor" / "data" / "hs-main" / "authorized_clients"
    assert sorted(p.name for p in auth.iterdir()) == ["f2.auth"]
    listed = json.loads((house.root / "friend-houses" / "listener" / "friends.json").read_text())
    assert list(listed) == [bytes([3]).hex() * 32]  # only friend 2's session key


@linux_only
async def test_the_last_friend_leaving_stops_tor(house) -> None:
    house.enable()
    house.friends.append(_friend(1))
    await svc.reconcile()
    house.friends.clear()
    await svc.reconcile()
    assert house.docker.calls == ["start", "stop"]


# ─── the container itself ─────────────────────────────────────────────────


def test_the_container_is_locked_down() -> None:
    args = rt.run_args(image_id="sha256:abc", data=tc.Path("/d"), run=tc.Path("/r"), uid=990, gid=990)
    joined = " ".join(args)
    for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--user 990:990", "--network domovoi-fh", "--restart on-failure:5",
                 "--memory 256m", "--pids-limit 128"):
        assert flag in joined
    for forbidden in ("--privileged", "--network host", "-p ", "--publish", "docker.sock",
                      "unless-stopped", "always"):
        assert forbidden not in joined
    assert args[-1] == "sha256:abc"  # runs the exact image ID the core built


def test_the_image_is_rebuilt_when_the_dockerfile_changes_or_it_gets_old() -> None:
    now = datetime(2026, 12, 1, tzinfo=timezone.utc)
    fresh = rt.ImageInfo("sha256:x", rt.dockerfile_sha(), now - timedelta(days=3))
    assert fresh.stale(now=now) is None
    assert "changed" in rt.ImageInfo("sha256:x", "other", now).stale(now=now)
    assert "days old" in rt.ImageInfo("sha256:x", rt.dockerfile_sha(),
                                      now - timedelta(days=rt.MAX_AGE_DAYS)).stale(now=now)
    assert rt.ImageInfo("sha256:x", rt.dockerfile_sha(), None).stale(now=now)


def test_dockers_created_time_parses() -> None:
    parsed = rt._parse_created("2026-11-17T14:25:30.123456789Z")
    assert parsed == datetime(2026, 11, 17, 14, 25, 30, 123456, tzinfo=timezone.utc)
    assert rt._parse_created("") is None


def test_the_dockerfile_pins_its_base_by_digest() -> None:
    text = rt.DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in text.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 1 and "@sha256:" in from_lines[0]
    assert "ControlPort" not in text


# ─── the container guard ──────────────────────────────────────────────────


def test_the_guard_expects_tor_running_only_when_wanted() -> None:
    assert "domovoi-tor" in RESERVED_CONTAINER_NAMES
    on = tor_expectation(running=True, image_id="sha256:built")
    off = tor_expectation(running=False, image_id="sha256:built")
    assert on.state == RUNNING and on.required
    assert off.state == STOPPED and not off.required
    assert on.pin == "id"  # only the image the core built
