"""The Linux satellite's own Python on the core (domovoi/satellite_linux.py
and the /v1/satellite-linux routes): the lock's shape (the same plain-text
lock install-pc.sh reads with bash), a cache that serves only pinned bytes,
a fetch held to every pin and refused when internet access is off, and the
routes' tiers. No DB, no network: the download is a fake and the cache a
tmp dir.
"""

from __future__ import annotations

import hashlib
import urllib.parse
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import main, satellite_linux as sl, satellite_windows as sw

REPO = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO / "satellite" / sl.LOCK_REL

X86 = "cpython-3.14.8+20261009-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
ARM = "cpython-3.13.16+20261009-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"
FILES = {X86: b"X" * 300, ARM: b"A" * 200}


def line(machine: str, version: str, name: str, body: bytes, *, url: str | None = None,
         size: int | None = None, sha: str | None = None) -> str:
    url = url or sl.UPSTREAM + "20261009/" + urllib.parse.quote(name)
    return " ".join(["python", machine, version, name, str(len(body) if size is None else size),
                     sha or hashlib.sha256(body).hexdigest(), url])


def tiny_lock_text(**over) -> str:
    return ("# a comment\n\n" + line("x86_64", "3.14.8", X86, FILES[X86], **over) + "\n"
            + line("aarch64", "3.13.16", ARM, FILES[ARM]) + "\n")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    from domovoi.satellite_media import cache as media_cache

    monkeypatch.setattr(media_cache, "CACHE_ROOT", tmp_path / "cache")
    sw._verified.clear()
    return tmp_path / "cache" / "linux-python"


def test_the_tree_s_lock_loads_and_has_a_list_for_each_python():
    lock = sl.load_lock(LOCK_PATH)
    machines = {e["machine"]: e for e in sl.entries(lock)}
    assert set(machines) == {"x86_64", "aarch64"}
    for e in machines.values():
        tag = "cp" + "".join(e["version"].split(".")[:2])
        assert (REPO / "satellite" / "locks" / f"pc-{tag}-{e['machine']}.txt").is_file(), e
        assert e["url"].startswith(sl.UPSTREAM) and e["size"] > 10_000_000


def test_the_lock_is_in_the_bundle_a_laptop_downloads():
    from domovoi import satellite_bundle

    files = satellite_bundle.manifest(main.SATELLITE_CODE_DIR, main._is_allowed_satellite_code)
    assert sl.LOCK_REL in files and "scripts/install-pc.sh" in files


@pytest.mark.parametrize("text", [
    "",                                                                  # names nothing
    "python x86_64 3.14.8\n",                                            # too few fields
    tiny_lock_text() + "extra\n",                                        # a stray line
    tiny_lock_text(url="https://evil.example/" + X86),                   # another host
    tiny_lock_text(url=sl.UPSTREAM + "20261009/" + X86),                 # not the quoted name
    tiny_lock_text(url=sl.UPSTREAM + "20261010/" + urllib.parse.quote(X86)),  # another release
    tiny_lock_text(sha="zz"),
    tiny_lock_text(size=0),
    tiny_lock_text(size=sl.MAX_FILE_BYTES + 1),
    line("x86_64", "3.14.7", X86, FILES[X86]),                           # version is not the file's
    line("aarch64", "3.14.8", X86, FILES[X86]),                          # machine is not the file's
    line("x86_64", "3.14.8", "../" + X86, FILES[X86]),
    line("x86_64", "3.14.8", X86.replace("install_only_stripped", "install_only"), FILES[X86]),
    line("x86_64", "3.14.8", X86, FILES[X86]) + "\n" + line("x86_64", "3.14.8", X86, FILES[X86]),  # twice
    line("x86_64", "3.14.8", X86, FILES[X86]) + " extra",
])
def test_a_malformed_lock_is_refused(text):
    with pytest.raises(sl.LockError):
        sl.parse_lock(text)


def test_only_listed_and_pinned_bytes_are_served(cache):
    lock = sl.parse_lock(tiny_lock_text())
    cache.mkdir(parents=True)
    for name, body in FILES.items():
        (cache / name).write_bytes(body)
    assert sl.cached_file(lock, X86) == cache / X86
    assert sl.status(lock)["ready"] is True
    (cache / X86).write_bytes(b"Y" * 300)          # same size, other bytes
    assert sl.cached_file(lock, X86) is None
    for name in ("evil.tar.gz", "../" + X86, X86 + "/../x", "linux-python.txt"):
        assert sl.cached_file(lock, name) is None
    st = sl.status(lock)
    assert st["ready"] is False and st["missing"] == [X86]
    assert st["bytes_total"] == 500 and st["bytes_cached"] == 200
    assert {p["machine"] for p in st["pythons"]} == {"x86_64", "aarch64"}


def test_a_link_in_the_cache_is_not_served(cache, tmp_path):
    lock = sl.parse_lock(tiny_lock_text())
    cache.mkdir(parents=True)
    real = tmp_path / "elsewhere"
    real.write_bytes(FILES[X86])
    try:
        (cache / X86).symlink_to(real)
    except OSError:
        pytest.skip("no symlinks here")
    assert sl.cached_file(lock, X86) is None


def test_the_fetch_keeps_only_pinned_files_and_drops_strays(cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    lock = sl.parse_lock(tiny_lock_text())
    monkeypatch.setattr(fetchers, "_turned_off", lambda what: None)
    fetched = []

    def fetch_pinned(url, dest, *, sha256, size):
        fetched.append((url, sha256, size))
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        if name == ARM:
            raise ValueError("checksum mismatch")
        dest.write_bytes(FILES[name])

    monkeypatch.setattr(fetchers, "_fetch_pinned", fetch_pinned)
    cache.mkdir(parents=True)
    (cache / "cpython-3.14.7+20260929-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz").write_bytes(b"old")
    ok, message = sl.fetch(lock)
    assert not ok and ARM in message and "1 of 2" in message
    assert sorted(p.name for p in cache.iterdir()) == [X86]
    # Every download is GitHub's release URL, held to the lock's own pin.
    assert all(u.startswith(sl.UPSTREAM) for u, _, _ in fetched)
    assert {(s, n) for _, s, n in fetched} == {(hashlib.sha256(b).hexdigest(), len(b)) for b in FILES.values()}
    assert sl.status(lock)["missing"] == [ARM]
    # A second fetch downloads only what is missing.
    fetched.clear()
    sl.fetch(lock)
    assert [urllib.parse.unquote(u.rsplit("/", 1)[-1]) for u, _, _ in fetched] == [ARM]


def test_a_cached_file_that_changed_is_fetched_again(cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    lock = sl.parse_lock(tiny_lock_text())
    monkeypatch.setattr(fetchers, "_turned_off", lambda what: None)
    fetched = []

    def fetch_pinned(url, dest, *, sha256, size):
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        fetched.append(name)
        dest.write_bytes(FILES[name])

    monkeypatch.setattr(fetchers, "_fetch_pinned", fetch_pinned)
    cache.mkdir(parents=True)
    (cache / X86).write_bytes(b"Y" * 300)          # the pinned name, other bytes
    (cache / ARM).write_bytes(FILES[ARM])
    ok, _ = sl.fetch(lock)
    assert ok and fetched == [X86] and (cache / X86).read_bytes() == FILES[X86]


def test_no_fetch_when_internet_access_is_off(cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    monkeypatch.setattr(fetchers, "_turned_off", lambda what: (False, "internet access is turned off; using what is already cached"))
    monkeypatch.setattr(fetchers, "_fetch_pinned", lambda *a, **k: pytest.fail("fetched with internet off"))
    ok, message = sl.fetch(sl.parse_lock(tiny_lock_text()))
    assert not ok and "turned off" in message


def test_the_real_pinned_fetcher_refuses_other_bytes(cache, monkeypatch):
    """The fetch goes through the media cache's pinned download (outbound
    gate, size cap, SHA-256), which leaves nothing behind on a mismatch."""
    from domovoi.satellite_media import fetchers

    lock = sl.parse_lock(tiny_lock_text())
    monkeypatch.setattr(fetchers, "_turned_off", lambda what: None)

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def iter_bytes(self, n):
            yield self.body

        def close(self):
            pass

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    bodies = {X86: b"Z" * 300, ARM: b"A" * 999}     # wrong bytes; too many bytes
    monkeypatch.setattr(fetchers, "_oww_http_client", lambda: Client())
    monkeypatch.setattr(fetchers.net_safety, "open_stream_sync",
                        lambda client, url: Response(bodies[urllib.parse.unquote(url.rsplit("/", 1)[-1])]))
    ok, message = sl.fetch(lock)
    assert not ok and "checksum mismatch" in message and "larger than the pinned" in message
    assert list(cache.iterdir()) == []


async def test_the_file_route_serves_cached_pinned_files_and_404s_the_rest(cache, monkeypatch):
    async def open_channel(*a, **k):
        return None

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", open_channel)
    lock = sl.parse_lock(tiny_lock_text())
    monkeypatch.setattr(main, "_linux_lock", lambda: lock)
    cache.mkdir(parents=True)
    (cache / ARM).write_bytes(FILES[ARM])
    resp = await main.satellite_linux_file(ARM)
    assert Path(resp.path) == cache / ARM
    for name in (X86, "..\\linux-python.txt", "client.py", "../" + ARM):
        with pytest.raises(HTTPException) as e:
            await main.satellite_linux_file(name)
        assert e.value.status_code == 404


async def test_a_frozen_channel_serves_no_linux_python(monkeypatch):
    async def frozen(*a, **k):
        raise HTTPException(status_code=423, detail="frozen")

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", frozen)
    with pytest.raises(HTTPException) as e:
        await main.satellite_linux_file(X86)
    assert e.value.status_code == 423


def test_a_broken_lock_is_a_503_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "SATELLITE_CODE_DIR", tmp_path)
    with pytest.raises(HTTPException) as e:
        main._linux_lock()
    assert e.value.status_code == 503


def test_status_and_fetch_are_an_admin_s_and_the_files_are_public():
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    routes = {r.path: r for r in main.app.routes if hasattr(r, "dependant")}
    status = routes["/v1/admin/satellite-linux"]
    fetch = routes["/v1/admin/satellite-linux/fetch"]
    assert any(d.dependency is require_admin_read for d in status.dependencies)
    assert any(d.dependency is require_admin_mutation for d in fetch.dependencies)
    assert "POST" in fetch.methods and "GET" not in fetch.methods
    assert not routes["/v1/satellite-linux/files/{name}"].dependencies


def test_the_installer_and_the_core_read_the_lock_alike():
    """install-pc.sh's read_pin checks the same things as parse_lock: the
    file-name pattern and the URL the release serves it at."""
    script = (REPO / "satellite" / "scripts" / "install-pc.sh").read_text(encoding="utf-8")
    assert "PBS_RELEASES=" + sl.UPSTREAM + "\n" in script
    assert "PY_LOCK_REL=satellite/" + sl.LOCK_REL + "\n" in script
    assert f"local file_re='{sl._FILE_RE.pattern}'\n" in script
    assert '[ "$u" = "$PBS_RELEASES$rel/${f//+/%2B}" ]' in script


def test_the_lock_is_the_generator_s_text():
    """scripts/dev/linux_runtime_lock.py writes the lock: its header and
    line format (a hand edit shows), and the one module it lets through
    with a non-permissive licence is the one install-pc.sh deletes."""
    import runpy

    tool = runpy.run_path(str(REPO / "scripts" / "dev" / "linux_runtime_lock.py"), run_name="lock_tool")
    text = LOCK_PATH.read_text(encoding="utf-8")
    release = sl.entries(sl.load_lock(LOCK_PATH))[0]["release"]
    assert text.startswith(tool["HEADER"].format(variant=tool["VARIANT"], release=release))
    for e in sl.entries(sl.load_lock(LOCK_PATH)):
        assert e["file"] == tool["file_name"](e["release"], e["machine"], e["version"])
        assert e["url"] == tool["url_for"](e["release"], e["file"])
    assert set(tool["REMOVED"]) == {"_dbm"} and not set(tool["PERMISSIVE"]) & {"GPL-3.0", "GPL-2.0", "Sleepycat", "LGPL-2.1"}
    script = (REPO / "satellite" / "scripts" / "install-pc.sh").read_text(encoding="utf-8")
    assert "-path '*/lib-dynload/_dbm.*.so' -type f -delete" in script
