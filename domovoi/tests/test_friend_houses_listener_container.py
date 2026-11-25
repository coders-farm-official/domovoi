"""Friend Houses: the inbound listener runs in a container of its own.

Audit R6 FH-12 found the listener, the code that reads what other houses
send, ran as a plain process under the core's user: a defect in it could
read anything that user can and reach the network. These tests pin the
container that replaced that (domovoi/friend_houses/listener_runtime.py).

The last group builds the real image and replays the card's proof of
concept inside the real container (read the host, reach the network, write
the house keys). They need Docker on Linux and the internet (the image
installs from Debian); set DOMOVOI_SKIP_DOCKER_TESTS=1 to skip them.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from domovoi.container_guard import expected as gx
from domovoi.container_guard.model import RESERVED_CONTAINER_NAMES, STOPPED
from domovoi.friend_houses import listener_runtime as lrt
from domovoi.friend_houses import service as svc
from domovoi.friend_houses import tor_config as tc
from domovoi.tests.friend_houses_testkit import no_tasks_left_behind  # noqa: F401  (autouse)

# ─── the image: only the listener's own modules ──────────────────────────


def test_the_build_context_is_the_dockerfile_and_the_listeners_modules_only(tmp_path) -> None:
    names = tarfile.open(fileobj=io.BytesIO(lrt.context_tar())).getnames()
    assert names == ["Dockerfile"] + [f"app/domovoi/{rel}" for rel in lrt.SOURCES]
    # Nothing that holds settings, the database or the rest of the core.
    assert not any(n.endswith(("config.py", "admin_auth.py", "service.py")) for n in names)


def test_the_build_context_is_the_same_bytes_every_time() -> None:
    # Its hash decides whether the image is current: it must not drift.
    assert lrt.context_tar() == lrt.context_tar()
    assert lrt.build_sha() == lrt.build_sha(lrt.context_tar())


def test_the_listener_imports_with_only_the_copied_modules(tmp_path) -> None:
    # A new import in the listener (or what it imports) that isn't in
    # SOURCES would only fail inside the container; catch it here.
    with tarfile.open(fileobj=io.BytesIO(lrt.context_tar())) as tar:
        for member in tar.getmembers():  # our own tar: plain files, relative names
            target = tmp_path / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
    app = str(tmp_path / "app")
    # Every Domovoi module the listener loads must come from the copy (the
    # repo may be on the path too: an editable install).
    import cryptography

    libs = str(Path(cryptography.__file__).parent.parent)  # wherever it is installed
    code = (f"import sys; sys.path.insert(0, {app!r}); sys.path.append({libs!r}); "
            "import domovoi.friend_houses.listener; "
            "bad = [n for n, m in sys.modules.items() if n.split('.')[0] == 'domovoi' "
            f"and not (getattr(m, '__file__', None) or {app!r}).startswith({app!r})]; "
            "assert not bad, bad")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         cwd=tmp_path, env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
    assert out.returncode == 0, out.stderr


def test_the_image_is_rebuilt_when_the_code_changes_or_it_is_old() -> None:
    now = datetime.now(timezone.utc)
    fresh = lrt.ImageInfo("sha256:x", lrt.build_sha(), now - timedelta(days=2))
    assert fresh.stale(now=now) is None
    assert lrt.ImageInfo("sha256:x", "0" * 64, now).stale(now=now) == "the listener's code changed"
    assert "days old" in lrt.ImageInfo("sha256:x", lrt.build_sha(), now - timedelta(days=31)).stale(now=now)


def test_a_rebuild_runs_apt_again() -> None:
    args = lrt.build_args("abc")
    assert "--no-cache" in args and "--pull" in args and args[-1] == "-"


# ─── the container: the wall FH-12 asked for ─────────────────────────────


def _args(tmp_path: Path) -> list[str]:
    return lrt.run_args(image_id="sha256:img", keys=tmp_path / "house-keys.json",
                        files=tmp_path / "listener", sockets=tmp_path / "run" / "listen",
                        uid=1000, gid=1000)


def _pairs(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_the_container_has_no_network_and_no_privileges(tmp_path) -> None:
    args = _args(tmp_path)
    assert _pairs(args, "--network") == ["none"]
    assert "--read-only" in args
    assert _pairs(args, "--cap-drop") == ["ALL"]
    assert _pairs(args, "--security-opt") == ["no-new-privileges"]
    assert _pairs(args, "--user") == ["1000:1000"]
    assert not {"--privileged", "--cap-add", "--pid", "--ipc", "--device", "-e", "--env",
                "--env-file"} & set(args)
    assert _pairs(args, "--log-driver") == ["none"]  # an event line can hold a message
    assert "noexec" in _pairs(args, "--tmpfs")[0]
    assert args[:3] == ["run", "-i", "--rm"]  # ends with the core, leaves nothing behind


def test_the_container_sees_three_things_from_the_host(tmp_path) -> None:
    mounts = _pairs(_args(tmp_path), "-v")
    assert mounts == [
        f"{tmp_path / 'house-keys.json'}:{lrt.KEYS_MOUNT}:ro",
        f"{tmp_path / 'listener'}:{lrt.FILES_MOUNT}:ro",
        f"{tmp_path / 'run' / 'listen'}:{lrt.SOCKETS_MOUNT}",
    ]
    assert not any("docker.sock" in m for m in mounts)


def test_the_listener_sockets_have_a_folder_of_their_own(tmp_path) -> None:
    # The listener's container mounts only this folder, never the one with
    # Tor's SOCKS socket (which would be a way out to the internet).
    folder = tc.TorFolder(tmp_path / "tor")
    assert folder.main_socket.parent == folder.pairing_socket.parent == folder.listen
    assert folder.socks_path.parent == folder.run != folder.listen
    torrc = tc.render_torrc(tc.wanted(enabled=True, internet_policy="always", friends=1, open_invites=1))
    ports = [line for line in torrc.splitlines() if line.startswith("HiddenServicePort")]
    assert ports == [f"HiddenServicePort 80 unix:{tc.RUN_MOUNT}/listen/main.sock",
                     f"HiddenServicePort 80 unix:{tc.RUN_MOUNT}/listen/pairing.sock"]


@pytest.mark.skipif(sys.platform == "win32", reason="Friend Houses runs on Linux")
def test_the_socket_folder_is_private(tmp_path) -> None:
    folder = tc.TorFolder(tmp_path / "tor")
    folder.ensure()
    assert (folder.listen.stat().st_mode & 0o777) == 0o700


def test_core_starts_it_through_docker_with_none_of_its_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://domovoi:secret@db/domovoi")
    monkeypatch.setenv("DOMOVOI_ADMIN_TOKEN", "x")
    monkeypatch.setattr(svc, "_root", lambda: tmp_path / "friend-houses")
    monkeypatch.setitem(svc._LISTENER, "image", "sha256:img")
    command = svc._listener_command(tc.TorFolder(tmp_path / "tor"))
    assert command[0] == "docker" and command[1:4] == ["run", "-i", "--rm"]
    assert f"{tmp_path / 'tor' / 'run' / 'listen'}:{lrt.SOCKETS_MOUNT}" in command
    env = svc._listener_env()
    assert "DATABASE_URL" not in env and "DOMOVOI_ADMIN_TOKEN" not in env
    assert set(env) <= set(svc._DOCKER_ENV)


# ─── the core's side: setting it up, saying so when it can't ─────────────


@pytest.fixture
def listener(monkeypatch):
    monkeypatch.setitem(svc._LISTENER, "proc", None)
    monkeypatch.setitem(svc._LISTENER, "task", None)
    monkeypatch.setitem(svc._LISTENER, "stopping", False)
    monkeypatch.setitem(svc._LISTENER, "problem", None)
    monkeypatch.setitem(svc._STATE, "status", "on")
    monkeypatch.setitem(svc._STATE, "detail", None)
    real_sleep = asyncio.sleep

    async def fast_sleep(delay, *a, **k):
        return await real_sleep(min(delay, 0.01), *a, **k)

    monkeypatch.setattr(svc.asyncio, "sleep", fast_sleep)


async def test_a_listener_that_cant_be_set_up_is_said_plainly_and_tried_again(listener, monkeypatch,
                                                                              tmp_path) -> None:
    from domovoi.friend_houses.tor_runtime import DockerFailed

    tries: list[int] = []
    started = asyncio.Event()

    async def prepare():
        tries.append(1)
        if len(tries) < 3:
            raise DockerFailed("building the Friend Houses listener failed: no internet")
        svc._LISTENER["image"] = "sha256:img"
        started.set()

    seen: list[str | None] = []

    def command(folder):
        seen.append(svc.status()["detail"])
        svc._LISTENER["stopping"] = True
        return [sys.executable, "-c", "pass"]

    monkeypatch.setattr(svc, "_prepare_listener", prepare)
    monkeypatch.setattr(svc, "_listener_command", command)
    monkeypatch.setattr(svc, "firewall_state", lambda: "in_place")
    task = asyncio.get_running_loop().create_task(svc._run_listener(tc.TorFolder(tmp_path)))
    # While it fails, the page says why friends can't reach the house.
    for _ in range(200):
        if tries and svc._LISTENER.get("problem"):
            break
        await asyncio.sleep(0)
    assert svc.status()["detail"] == svc.LISTENER_PROBLEM
    await asyncio.wait_for(task, 10)
    assert len(tries) == 3 and seen == [None]  # cleared once it could start


async def test_stopping_a_stuck_listener_removes_its_container(listener, monkeypatch) -> None:
    removed: list[int] = []

    async def remove():
        removed.append(1)

    monkeypatch.setattr(lrt, "remove", remove)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                              "print('up', flush=True); time.sleep(60)",
        stdout=asyncio.subprocess.PIPE)
    await proc.stdout.readline()
    monkeypatch.setitem(svc._LISTENER, "proc", proc)
    real_wait_for = asyncio.wait_for

    async def short_wait_for(aw, timeout):
        return await real_wait_for(aw, min(timeout, 0.5))

    monkeypatch.setattr(svc.asyncio, "wait_for", short_wait_for)
    await svc._stop_listener()
    await proc.wait()
    if sys.platform != "win32":  # Windows has no SIGTERM to ignore: terminate already ended it
        assert removed == [1]


# ─── the container guard ─────────────────────────────────────────────────


def test_the_guard_pins_the_listener_image_and_reserves_its_name() -> None:
    e = gx.listener_expectation(image_id="sha256:built")
    assert (e.name, e.owner, e.state, e.image_id, e.pin) == (lrt.CONTAINER, "core", STOPPED, "sha256:built", "id")
    assert lrt.CONTAINER in RESERVED_CONTAINER_NAMES
    assert gx.LISTENER_CONTAINER == lrt.CONTAINER and gx.LISTENER_IMAGE_TAG == lrt.IMAGE_TAG


# ─── the real container (Docker on Linux) ────────────────────────────────

def _docker_ok() -> bool:
    if sys.platform != "linux" or os.environ.get("DOMOVOI_SKIP_DOCKER_TESTS") == "1":
        return False
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0


_skip = pytest.mark.skipif(not _docker_ok(), reason="needs Docker on Linux (and the internet to build)")
# One container name for the house: under pytest -n these share a worker.
_one_worker = pytest.mark.xdist_group(name="fh-listener-container")


def needs_docker(fn):
    return _skip(_one_worker(fn))


@pytest.fixture(scope="module")
def image() -> str:
    from domovoi.friend_houses.tor_runtime import DockerFailed

    async def build() -> str:
        info = await lrt.image_info()
        if info is not None and info.stale() is None:
            return info.image_id
        return await lrt.build_image()

    try:
        return asyncio.run(build())
    except DockerFailed as e:
        pytest.skip(f"the listener image couldn't be built here: {e}")


@pytest.fixture
def mounts(tmp_path):
    from domovoi.friend_houses import identity as fid

    fid.create(tmp_path / "keys" / "house-keys.json")
    (tmp_path / "listener").mkdir()
    (tmp_path / "listener" / "friends.json").write_text("{}")
    sockets = tmp_path / "listen"
    sockets.mkdir(mode=0o700)
    return tmp_path / "keys" / "house-keys.json", tmp_path / "listener", sockets


@needs_docker
def test_fh12_replay_inside_the_real_container_nothing_gets_out(image, mounts) -> None:
    # The card: a defect in the listener could read what the core's user
    # can and reach the network. Run code of our choosing in the listener's
    # real container (same arguments, another entrypoint) and try.
    keys, files, sockets = mounts
    args = lrt.run_args(image_id=image, keys=keys, files=files, sockets=sockets,
                        uid=os.getuid(), gid=os.getgid())
    script = r'''
import json, os, socket
out = {}
def tried(name, fn):
    try:
        fn(); out[name] = "allowed"
    except Exception:
        out[name] = "blocked"
tried("internet", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
tried("dns", lambda: socket.getaddrinfo("example.com", 443))
tried("write_root", lambda: open("/evil", "w"))
tried("change_keys", lambda: open("/etc/fh/house-keys.json", "a"))
tried("change_friends", lambda: open("/var/lib/fh-listener/friends.json", "w"))
out["home"] = os.path.exists(os.path.expanduser("~/.domovoi"))
out["env"] = sorted(k for k in os.environ if k.startswith(("DATABASE", "DOMOVOI")))
out["caps"] = [l.split()[1] for l in open("/proc/self/status") if l.startswith("CapEff")][0]
print(json.dumps(out))
'''
    at = args.index(image)
    run = subprocess.run(["docker", *args[:at], "--entrypoint", "python3", image, "-c", script],
                         capture_output=True, text=True, timeout=120)
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert got == {"internet": "blocked", "dns": "blocked", "write_root": "blocked",
                   "change_keys": "blocked", "change_friends": "blocked", "home": False,
                   "env": [], "caps": "0000000000000000"}, run.stderr


@needs_docker
def test_the_real_listener_answers_on_its_socket_and_ends_with_core(image, mounts) -> None:
    import socket

    keys, files, sockets = mounts
    args = lrt.run_args(image_id=image, keys=keys, files=files, sockets=sockets,
                        uid=os.getuid(), gid=os.getgid())
    subprocess.run(["docker", "rm", "-f", lrt.CONTAINER], capture_output=True)
    proc = subprocess.Popen(["docker", *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(proc.stdout.readline()) == {"event": "listening"}
        # Junk on the house's socket (what Tor hands over): refused, and the
        # listener carries on.
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(sockets / "main.sock"))
        s.settimeout(30)
        try:
            # The listener may refuse and close before all 100 bytes are
            # written: the send then breaks (EPIPE) or is reset. Either is
            # the refusal this checks, not a fault (main CI flaked on
            # BrokenPipeError here twice, 2026-11-25).
            s.sendall(b"x" * 100)
            assert s.recv(10) == b""
        except (BrokenPipeError, ConnectionResetError):
            pass
        s.close()
        assert proc.poll() is None
        # Core going away (its pipe closing) ends the listener; --rm removes it.
        t = time.monotonic()
        proc.stdin.close()
        assert proc.wait(30) == 0 and time.monotonic() - t < 30
        left = subprocess.run(["docker", "ps", "-aq", "-f", f"name=^{lrt.CONTAINER}$"],
                              capture_output=True, text=True).stdout.strip()
        assert left == ""
    finally:
        if proc.poll() is None:
            proc.kill()
        subprocess.run(["docker", "rm", "-f", lrt.CONTAINER], capture_output=True)
