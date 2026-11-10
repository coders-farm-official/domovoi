"""Settings → Health's memory row, the core side (domovoi/host_memory.py).

* /proc/meminfo: totals, programs (MemTotal − MemAvailable), file cache
  (Cached + Buffers − Shmem), shared memory, swap in use; WSL samples too.
* Each source has its own time limit; one that fails or hangs is listed
  in ``missing`` and the rest still answer. A non-Linux host answers
  "not available".
* Process names come from ``comm`` only: never the command line, which can
  hold a password.
* Containers get plain labels (the guard's, else by name).
* The read is an admin read at both hops.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest

from domovoi import admin_auth, host_memory
from domovoi.host_memory import Sources
from domovoi.tests.auth_testkit import (
    _db,  # noqa: F401 - fixture
    bearer,
    claim_admin,
    core_client,
    web_client,
)
from domovoi.tests.conftest import requires_db

GIB = 1024**3

# A small Ubuntu server, mostly idle.
MEMINFO_LINUX = """\
MemTotal:       16303428 kB
MemFree:         9123456 kB
MemAvailable:   13456789 kB
Buffers:          234567 kB
Cached:          4012345 kB
SwapCached:            0 kB
Active:          3456789 kB
Inactive:        2345678 kB
Shmem:            123456 kB
SwapTotal:       4194300 kB
SwapFree:        4194300 kB
HugePages_Total:       0
Hugepagesize:       2048 kB
"""

# The Windows installer's distro on a 128 GB PC (memory=64GB): most of it
# is file cache, the shape the owner saw.
MEMINFO_WSL_CACHE_HEAVY = """\
MemTotal:       65842876 kB
MemFree:         2345678 kB
MemAvailable:   54123456 kB
Buffers:          512340 kB
Cached:         50123456 kB
SwapCached:        12345 kB
Shmem:           1048576 kB
SReclaimable:    1234567 kB
SwapTotal:       4194304 kB
SwapFree:        3145728 kB
"""

# A small WSL VM (memory=10GB) that is genuinely short.
MEMINFO_WSL_LOW = """\
MemTotal:       10024680 kB
MemFree:          201234 kB
MemAvailable:     612345 kB
Buffers:           10240 kB
Cached:           700000 kB
Shmem:            300000 kB
SwapTotal:       4194304 kB
SwapFree:         104857 kB
"""

# A kernel older than 3.14 has no MemAvailable.
MEMINFO_OLD = """\
MemTotal:        2048000 kB
MemFree:          512000 kB
Buffers:          100000 kB
Cached:           400000 kB
"""


def kb(n: int) -> int:
    return n * 1024


# ─── /proc/meminfo ───────────────────────────────────────────────────────


def test_meminfo_is_read_in_bytes_and_odd_lines_are_skipped() -> None:
    mi = host_memory.parse_meminfo(MEMINFO_LINUX + "garbage line\nBroken:   x kB\n")
    assert mi["MemTotal"] == kb(16303428)
    assert mi["HugePages_Total"] == 0       # a count, no unit
    assert "Broken" not in mi


def test_linux_summary() -> None:
    s = host_memory.summarize(host_memory.parse_meminfo(MEMINFO_LINUX))
    assert s == {
        "total_bytes": kb(16303428),
        "programs_bytes": kb(16303428 - 13456789),
        "cache_bytes": kb(234567 + 4012345 - 123456),
        "shared_bytes": kb(123456),
        "free_bytes": kb(9123456),
        "available_bytes": kb(13456789),
        "swap_total_bytes": kb(4194300),
        "swap_used_bytes": 0,
    }


def test_wsl_cache_heavy_summary() -> None:
    s = host_memory.summarize(host_memory.parse_meminfo(MEMINFO_WSL_CACHE_HEAVY))
    assert s["programs_bytes"] == kb(65842876 - 54123456)          # ~11 GB
    assert s["cache_bytes"] == kb(512340 + 50123456 - 1048576)     # ~47 GB
    assert s["cache_bytes"] > s["programs_bytes"]
    assert s["swap_used_bytes"] == kb(4194304 - 3145728)
    assert 62 * GIB < s["total_bytes"] < 63 * GIB                   # a little under the 64 GB cap


def test_wsl_low_summary() -> None:
    s = host_memory.summarize(host_memory.parse_meminfo(MEMINFO_WSL_LOW))
    assert s["available_bytes"] < s["total_bytes"] * 0.1
    assert s["cache_bytes"] == kb(10240 + 700000 - 300000)


def test_a_kernel_without_memavailable_and_a_file_without_a_total() -> None:
    s = host_memory.summarize(host_memory.parse_meminfo(MEMINFO_OLD))
    assert s["available_bytes"] == kb(512000 + 100000 + 400000)
    assert s["swap_used_bytes"] == 0
    assert host_memory.summarize(host_memory.parse_meminfo("MemFree: 1 kB\n")) is None
    # Shmem larger than the cache never goes negative.
    odd = host_memory.summarize({"MemTotal": 100, "MemAvailable": 50, "Cached": 5, "Shmem": 40})
    assert odd["cache_bytes"] == 0


# ─── units, containers ───────────────────────────────────────────────────

SYSTEMCTL_OUT = """\
Id=domovoi-core.service
LoadState=loaded
ActiveState=active
MemoryCurrent=2147483648

Id=domovoi-web.service
LoadState=loaded
ActiveState=active
MemoryCurrent=536870912

Id=domovoi-update.service
LoadState=loaded
ActiveState=inactive
MemoryCurrent=[not set]

Id=ollama.service
LoadState=not-found
ActiveState=inactive
MemoryCurrent=[not set]
"""


def test_systemctl_show_keeps_running_units_with_a_value() -> None:
    rows = host_memory.parse_systemctl_show(SYSTEMCTL_OUT)
    assert rows == [{"unit": "domovoi-core.service", "bytes": 2 * GIB},
                    {"unit": "domovoi-web.service", "bytes": GIB // 2}]
    unset = "Id=x.service\nLoadState=loaded\nActiveState=active\nMemoryCurrent=18446744073709551615\n"
    assert host_memory.parse_systemctl_show(unset) == []


DOCKER_OUT = (
    "domovoi-postgres-1\t412.5MiB / 62.79GiB\n"
    "domovoi-mpd-kitchen\t38.2MiB / 62.79GiB\n"
    "domovoi-searxng-1\t1.2GiB / 62.79GiB\n"
    "domovoi-wake-train\t9.5GiB / 12GiB\n"
    "radio-helper\t100MiB / 62.79GiB\n"
    "someones-own-app\t2GB / 64GB\n"
    "broken line\n"
    "nonsense\tlots / more\n"
)


def test_docker_stats_sizes() -> None:
    rows = dict(host_memory.parse_docker_stats(DOCKER_OUT))
    assert rows["domovoi-postgres-1"] == int(412.5 * 1024**2)
    assert rows["domovoi-searxng-1"] == int(1.2 * GIB)
    assert rows["someones-own-app"] == 2 * 1000**3
    assert "nonsense" not in rows and len(rows) == 6
    assert host_memory.parse_size("0B") == 0
    assert host_memory.parse_size("12 parsecs") is None


@pytest.mark.parametrize(("name", "guard", "label"), [
    ("domovoi-mpd-kitchen", {}, "music player for kitchen"),
    ("domovoi-mpd-kitchen", {"domovoi-mpd-kitchen": "the music player for kitchen"}, "music player for kitchen"),
    ("domovoi-postgres-1", {}, "database"),
    ("domovoi-postgres-1", {"domovoi-postgres-1": "the database"}, "database"),
    ("domovoi-searxng-1", {}, "web search helper"),
    ("domovoi-flyway-run-1a2b", {}, "database migrations"),
    ("domovoi-wake-train", {}, "wake-word trainer"),
    ("domovoi-wake-warm", {}, "wake-word trainer"),
    ("radio-helper", {"radio-helper": "the Radio container"}, "Radio container"),
    ("radio-helper", {"radio-helper": "stream relay"}, "stream relay"),
    ("someones-own-app", {}, "other container"),
    ("domovoi-mpd-", {}, "other container"),
])
def test_container_labels(name, guard, label) -> None:
    assert host_memory.container_label(name, mpd_prefix="domovoi-mpd-", guard_labels=guard) == label


# ─── processes: names only ───────────────────────────────────────────────

SECRET = "--password=hunter2-SECRET"


def _proc(root: Path, pid: int, comm: str, rss_kb: int | None, cgroup: str = "0::/user.slice/session-1.scope") -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n", encoding="utf-8")
    status = f"Name:\t{comm}\nState:\tS (sleeping)\n"
    if rss_kb is not None:
        status += f"VmRSS:\t{rss_kb} kB\n"
    (d / "status").write_text(status, encoding="utf-8")
    (d / "cgroup").write_text(cgroup + "\n", encoding="utf-8")
    # What the scanner must never read.
    (d / "cmdline").write_bytes(f"{comm}\0{SECRET}\0".encode())
    (d / "environ").write_bytes(f"TOKEN={SECRET}\0".encode())


@pytest.fixture
def proc_root(tmp_path) -> Path:
    root = tmp_path / "proc"
    root.mkdir()
    (root / "meminfo").write_text(MEMINFO_WSL_CACHE_HEAVY, encoding="utf-8")
    (root / "self").mkdir()                       # not a pid
    _proc(root, 2, "kthreadd", None)              # kernel thread: no RSS
    _proc(root, 100, "python3", 400 * 1024)
    _proc(root, 101, "python3", 300 * 1024)
    _proc(root, 200, "node", 900 * 1024)
    _proc(root, 300, "tiny", 1024)
    _proc(root, 400, "uvicorn", 2 * 1024 * 1024, "0::/system.slice/domovoi-core.service")
    _proc(root, 500, "postgres", 3 * 1024 * 1024,
          "0::/system.slice/docker-0123456789abcdef.scope")
    _proc(root, 600, "mpd", 50 * 1024, "12:memory:/docker/0123abcd")
    _proc(root, 700, "ev\x1bil\nname", 100 * 1024)
    return root


def test_processes_by_name_without_the_unit_and_container_ones(proc_root) -> None:
    rows, complete = host_memory.scan_processes(proc_root)
    assert complete
    by = {r["name"]: r for r in rows}
    assert by["python3"] == {"name": "python3", "bytes": 700 * 1024 * 1024, "count": 2}
    assert by["node"]["bytes"] == 900 * 1024 * 1024
    # Counted with their unit / container instead, never twice.
    assert "uvicorn" not in by and "postgres" not in by and "mpd" not in by
    assert "kthreadd" not in by
    # Printable characters only.
    assert "evilname" in by
    assert [r["name"] for r in rows][:2] == ["node", "python3"]


def test_process_names_never_carry_arguments(proc_root, monkeypatch) -> None:
    read: list[str] = []
    real = Path.read_text

    def spy(self, *a, **kw):
        read.append(self.name)
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)
    rows, _ = host_memory.scan_processes(proc_root)
    assert set(read) <= {"status", "cgroup", "comm"}, set(read)
    assert SECRET not in json.dumps(rows) and "hunter2" not in json.dumps(rows)


def test_a_long_scan_stops_and_says_so(proc_root) -> None:
    ticks = iter(range(0, 1000, 5))
    rows, complete = host_memory.scan_processes(proc_root, budget_sec=7, clock=lambda: next(ticks))
    assert complete is False
    rows, complete = host_memory.scan_processes(proc_root, max_pids=2)
    assert complete is False
    assert host_memory.scan_processes(proc_root / "missing") == ([], False)


# ─── the whole snapshot: bounded, a failed source is a note ──────────────


def _ok_systemctl(args, timeout):
    assert "MemoryCurrent" in args and timeout <= host_memory.SYSTEMCTL_TIMEOUT_SEC
    return 0, SYSTEMCTL_OUT, ""


def _ok_docker(args, timeout):
    assert args[:2] == ["stats", "--no-stream"] and timeout <= host_memory.DOCKER_TIMEOUT_SEC
    return 0, DOCKER_OUT, ""


def _sources(proc_root, **kw) -> Sources:
    base = dict(proc_root=proc_root, systemctl=_ok_systemctl, docker=_ok_docker,
                guard_labels=lambda: {"radio-helper": "the Radio container"},
                mpd_prefix="domovoi-mpd-", host_kind="wsl")
    base.update(kw)
    return Sources(**base)


def test_snapshot_puts_it_all_together_largest_first(proc_root) -> None:
    doc = asyncio.run(host_memory.snapshot(_sources(proc_root)))
    assert doc["available"] is True and doc["host"] == "wsl"
    assert doc["missing"] == []
    assert doc["cache_bytes"] > doc["programs_bytes"]
    sizes = [r["bytes"] for r in doc["breakdown"]]
    assert sizes == sorted(sizes, reverse=True)
    labels = [(r["kind"], r["label"]) for r in doc["breakdown"]]
    assert labels[0] == ("container", "wake-word trainer")
    assert ("service", "Domovoi server") in labels and ("service", "dashboard") in labels
    assert ("container", "music player for kitchen") in labels
    assert ("container", "Radio container") in labels
    assert ("process", "node") in labels and ("process", "python3") in labels
    # Under 20 MB is not worth a line.
    assert ("process", "tiny") not in labels
    assert SECRET not in json.dumps(doc)


def test_a_failed_source_is_left_out_with_a_note(proc_root) -> None:
    def docker_down(args, timeout):
        return 1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.\n"

    def systemctl_raises(args, timeout):
        raise FileNotFoundError("systemctl")

    doc = asyncio.run(host_memory.snapshot(_sources(proc_root, docker=docker_down, systemctl=systemctl_raises)))
    assert doc["available"] is True
    missing = {m["source"]: m["why"] for m in doc["missing"]}
    assert missing["containers"].startswith("Cannot connect to the Docker daemon")
    assert "services" in missing
    assert {r["kind"] for r in doc["breakdown"]} == {"process"}

    doc = asyncio.run(host_memory.snapshot(_sources(proc_root, docker=None, systemctl=None)))
    missing = {m["source"]: m["why"] for m in doc["missing"]}
    assert missing == {"containers": "Docker isn't installed here",
                       "services": "systemd isn't running this server"}


def test_a_source_that_hangs_is_given_up_on(proc_root, monkeypatch) -> None:
    monkeypatch.setattr(host_memory, "DOCKER_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(host_memory, "GRACE_SEC", 0.1)

    def docker_hangs(args, timeout):
        time.sleep(1.0)
        return 0, DOCKER_OUT, ""

    def docker_times_out(args, timeout):
        raise subprocess.TimeoutExpired(["docker"], timeout)

    async def timed():
        started = time.monotonic()
        doc = await host_memory.snapshot(_sources(proc_root, docker=docker_hangs))
        return doc, time.monotonic() - started

    doc, took = asyncio.run(timed())
    assert took < 0.9, took
    assert {m["source"]: m["why"] for m in doc["missing"]} == {"containers": "it didn't answer in time"}
    assert any(r["kind"] == "service" for r in doc["breakdown"])

    doc = asyncio.run(host_memory.snapshot(_sources(proc_root, docker=docker_times_out)))
    assert {m["source"]: m["why"] for m in doc["missing"]} == {"containers": "it didn't answer in time"}


def test_guard_labels_failing_still_names_the_containers(proc_root) -> None:
    def boom():
        raise RuntimeError("no guard")

    doc = asyncio.run(host_memory.snapshot(_sources(proc_root, guard_labels=boom)))
    assert ("container", "other container") in [(r["kind"], r["label"]) for r in doc["breakdown"]
                                                  if r["name"] == "radio-helper"]
    assert doc["missing"] == []


@pytest.mark.parametrize("kind", ["windows", "darwin"])
def test_not_available_off_linux(proc_root, kind) -> None:
    doc = asyncio.run(host_memory.snapshot(_sources(proc_root, host_kind=kind)))
    assert doc == {"available": False, "host": kind, "checked_at": doc["checked_at"],
                   "why": "not available on this system"}


def test_unreadable_meminfo_is_not_available_never_an_error(tmp_path) -> None:
    doc = asyncio.run(host_memory.snapshot(_sources(tmp_path / "nothing", host_kind="linux")))
    assert doc["available"] is False and "couldn't be read" in doc["why"]


def test_the_cache_shares_one_look(monkeypatch) -> None:
    calls = []

    async def fake():
        calls.append(1)
        return {"available": False}

    monkeypatch.setattr(host_memory, "snapshot", fake)
    monkeypatch.setattr(host_memory, "_CACHE", None)
    monkeypatch.setattr(host_memory, "_LOCK", None)

    async def two_tabs():
        return await asyncio.gather(host_memory.cached_snapshot(), host_memory.cached_snapshot())

    asyncio.run(two_tabs())
    assert len(calls) == 1
    monkeypatch.setattr(host_memory, "_LOCK", None)
    monkeypatch.setattr(host_memory, "_CACHE", (time.monotonic() - host_memory.CACHE_SEC - 1, {}))
    asyncio.run(host_memory.cached_snapshot())
    assert len(calls) == 2


# ─── the gate ────────────────────────────────────────────────────────────


def _gates(app, method, path):
    from domovoi.tests.route_walk import iter_route_contexts

    for rc in iter_route_contexts(app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            found, stack = [], [getattr(rc, "dependant", None)]
            while stack:
                d = stack.pop()
                for dep in getattr(d, "dependencies", ()) if d else ():
                    found.append(dep.call)
                    stack.append(dep)
            return found
    raise AssertionError(f"{method} {path} not routed")


def test_an_admin_read_at_both_hops() -> None:
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    assert admin_auth.require_admin_read in _gates(core_app, "GET", "/v1/admin/memory")
    assert admin_auth.require_admin_read in _gates(web_app, "GET", "/api/models/memory")


@requires_db
@pytest.mark.asyncio
async def test_only_an_admin_reads_it(_db, monkeypatch) -> None:  # noqa: F811
    async def fake():
        return {"available": True, "host": "linux", "breakdown": []}

    monkeypatch.setattr(host_memory, "cached_snapshot", fake)
    async with web_client() as w:
        token = await claim_admin(w)
    async with web_client() as w:            # a fresh browser: no admin cookie
        assert (await w.get("/api/models/memory")).status_code == 401
    async with core_client() as c:
        assert (await c.get("/v1/admin/memory")).status_code == 401
        r = await c.get("/v1/admin/memory", headers=bearer(token))
        assert r.status_code == 200 and r.json()["available"] is True
