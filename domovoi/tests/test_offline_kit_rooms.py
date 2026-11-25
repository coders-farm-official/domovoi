"""Rooms on the offline kit (release 1.5.0, phase P6; docs/OFFLINE_KIT.md
"Adding rooms with no internet").

The kit builder puts what a new room installs under ``rooms/``, each file
held to its pin in the release commit; ``provision.sh --offline-kit`` hands
those files to domovoi/kit_rooms.py, which places them where the core
already serves them; a room then installs from the server with no
internet. Fakes stand in for PyPI, the pins' real files and the network;
nothing is downloaded. No DB.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import egress, kit_rooms, main, satellite_linux as sl, satellite_setup, satellite_windows as sw
from domovoi.kit_builder import parts, pins, plan, wheels
from domovoi.kit_builder.parts import Release
from domovoi.offline_kit import verify
from domovoi.satellite_media import cache as media_cache, fetchers
from domovoi.tests.test_offline_kit_builder import _FolderImage, _tidy_folder_images, kit_home  # noqa: F401
from domovoi.usb_stick import image as stick_image

REPO = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
VERSION = "1.5.0"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def repo_read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


# ─── a small release: its room pins and the files they name ──────────────

WIN_PY = b"python-embed" * 10
WIN_WHL = b"numpy-win" * 10
WIN_PIP = b"pip-wheel" * 10
LNX_PY = b"cpython-x86" * 10
WHL_313 = b"numpy-cp313-x86_64" * 10
WHL_ANY = b"tqdm-pure" * 10
DEB = b"mpg123-deb" * 10
PI_WHL = b"numpy-aarch64" * 10
ISO = b"debian-netinst" * 10
SETUP = b"MZ" + b"satellite-setup" * 10
OWW = {"embedding_model.onnx": b"emb" * 10, "melspectrogram.onnx": b"mel" * 10,
       "silero_vad.onnx": b"vad" * 10, "hey_jarvis_v0.1.onnx": b"jarvis" * 10, "alexa_v0.1.onnx": b"alexa" * 10}
LNX_NAME = "cpython-3.14.8+20261009-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
NUMPY_WHL = "numpy-2.3.0-cp313-cp313-manylinux_2_28_x86_64.whl"
TQDM_WHL = "tqdm-4.67.1-py3-none-any.whl"


def windows_lock() -> dict:
    return {
        "lock_version": 1,
        "python": {"version": "3.14.8", "file": "python-3.14.8-embed-amd64.zip", "size": len(WIN_PY),
                   "sha256": sha(WIN_PY), "url": "https://www.python.org/ftp/python/3.14.8/python-3.14.8-embed-amd64.zip"},
        "pip": {"file": "pip-25.3-py3-none-any.whl", "size": len(WIN_PIP), "sha256": sha(WIN_PIP),
                "url": "https://files.pythonhosted.org/packages/bb/pip-25.3-py3-none-any.whl"},
        "wheels": [{"file": "numpy-2.3.0-cp314-cp314-win_amd64.whl", "size": len(WIN_WHL),
                               "sha256": sha(WIN_WHL),
                               "url": "https://files.pythonhosted.org/packages/aa/numpy-2.3.0-cp314-cp314-win_amd64.whl"}],
    }


def linux_python_text() -> str:
    url = sl.UPSTREAM + "20261009/" + LNX_NAME.replace("+", "%2B")
    return f"python x86_64 3.14.8 {LNX_NAME} {len(LNX_PY)} {sha(LNX_PY)} {url}\n"


def pc_lock_text() -> str:
    return (f"numpy==2.3.0 \\\n    --hash=sha256:{sha(WHL_313)} \\\n    --hash=sha256:{'0' * 64}\n"
            f"tqdm==4.67.1 \\\n    --hash=sha256:{sha(WHL_ANY)}\n")


@pytest.fixture
def room_home(tmp_path, monkeypatch, kit_home):  # noqa: F811
    """A server: its media cache, its checkout's room locks, the Debian and
    openWakeWord pins (small fakes), and the kit record folder."""
    monkeypatch.setattr(media_cache, "CACHE_ROOT", tmp_path / "cache")
    sw._verified.clear()
    sl._wheel_verdicts.clear()
    monkeypatch.setattr(fetchers, "OWW_MODEL_FILES", {n: (sha(b), len(b)) for n, b in OWW.items()})
    pin = {"pin_version": 1, "name": "debian-13.2.0-amd64-netinst.iso", "version": "13.2.0", "size": len(ISO),
           "sha256": sha(ISO), "urls": ["https://cdimage.debian.org/x/debian-13.2.0-amd64-netinst.iso"]}
    monkeypatch.setattr(stick_image, "load_pin", lambda path=None: dict(pin))
    monkeypatch.setattr(kit_rooms, "record_path", lambda: tmp_path / "offline_kit" / kit_rooms.RECORD)
    code = tmp_path / "code"
    (code / "satellite" / "locks").mkdir(parents=True)
    (code / "satellite" / "windows-runtime.json").write_text(json.dumps(windows_lock()), encoding="utf-8")
    (code / "satellite" / "locks" / "linux-python.txt").write_text(linux_python_text(), encoding="utf-8")
    (code / "satellite" / "locks" / "pc-cp313-x86_64.txt").write_text(pc_lock_text(), encoding="utf-8")
    monkeypatch.setattr(main, "SATELLITE_CODE_DIR", code / "satellite")
    return code


def small_pins() -> pins.Pins:
    p = pins.load(repo_read, COMMIT)
    return dataclasses.replace(p, windows_runtime=windows_lock(), linux_python=linux_python_text(),
                               pc_locks={"pc-cp313-x86_64": pc_lock_text()})


def online_server_holds_everything(root: Path) -> None:
    """The making server's caches, as it has them after adding rooms
    online (so nothing is downloaded while the kit is made)."""
    lock = windows_lock()
    for e, body in ((lock["python"], WIN_PY), (lock["pip"], WIN_PIP), (lock["wheels"][0], WIN_WHL)):
        (root / "windows" / "3.14.8").mkdir(parents=True, exist_ok=True)
        (root / "windows" / "3.14.8" / e["file"]).write_bytes(body)
    (root / "linux-python").mkdir(parents=True)
    (root / "linux-python" / LNX_NAME).write_bytes(LNX_PY)
    (root / "linux-wheels").mkdir(parents=True)
    (root / "linux-wheels" / NUMPY_WHL).write_bytes(WHL_313)
    (root / "linux-wheels" / TQDM_WHL).write_bytes(WHL_ANY)
    (root / "oww_models").mkdir(parents=True)
    for n, b in OWW.items():
        (root / "oww_models" / n).write_bytes(b)
    (root / "wheels" / "3.13").mkdir(parents=True)
    (root / "wheels" / "3.13" / "numpy-2.3.0-cp313-cp313-manylinux_2_28_aarch64.whl").write_bytes(PI_WHL)
    (root / "debs" / "trixie").mkdir(parents=True)
    for pkg in fetchers.BASE_APT_PACKAGES:
        (root / "debs" / "trixie" / f"{pkg}_1.0_arm64.deb").write_bytes(DEB + pkg.encode())
    (root / "usb_stick" / "13.2.0").mkdir(parents=True)
    (root / "usb_stick" / "13.2.0" / "debian-13.2.0-amd64-netinst.iso").write_bytes(ISO)
    (root / "satellite_setup").mkdir(parents=True)
    (root / "satellite_setup" / "DomovoiSatelliteSetup.exe").write_bytes(SETUP)


def picks() -> list[wheels.Pick]:
    return [wheels.Pick("numpy", "2.3.0", NUMPY_WHL, "https://files.pythonhosted.org/x/" + NUMPY_WHL,
                        sha(WHL_313), len(WHL_313)),
            wheels.Pick("tqdm", "4.67.1", TQDM_WHL, "https://files.pythonhosted.org/x/" + TQDM_WHL,
                        sha(WHL_ANY), len(WHL_ANY))]


# ─── pins ────────────────────────────────────────────────────────────────

def test_the_release_s_room_pins_are_read_from_its_commit():
    p = pins.load(repo_read, COMMIT)
    assert p.windows_runtime and p.windows_runtime["lock_version"] == 1
    assert sl.parse_lock(p.linux_python)["pythons"]
    on_disk = sorted(f.stem for f in (REPO / "satellite" / "locks").glob("pc-*.txt"))
    assert sorted(pins.PC_LOCKS) == on_disk, "pins.PC_LOCKS lists every spare-computer lock"
    assert sorted(p.pc_locks) == on_disk and all(wheels.parse_lock(t) for t in p.pc_locks.values())


def test_a_release_without_room_locks_still_loads():
    def read(path):
        if path.startswith("satellite/"):
            raise pins.PinError("absent")
        return repo_read(path)

    p = pins.load(read, COMMIT)
    assert p.windows_runtime is None and p.linux_python == "" and p.pc_locks == {}


# ─── which wheel a Linux room gets ────────────────────────────────────────

def test_a_room_s_wheel_is_built_for_the_oldest_glibc_the_installer_accepts():
    lk = wheels.Locked("numpy", "2.3.0", frozenset({"a" * 64, "b" * 64, "c" * 64, "d" * 64}))
    files = [
        {"filename": "numpy-2.3.0-cp313-cp313-manylinux_2_34_x86_64.whl", "digests": {"sha256": "a" * 64},
         "url": "u1", "size": 1},
        {"filename": "numpy-2.3.0-cp313-cp313-manylinux_2_28_x86_64.whl", "digests": {"sha256": "b" * 64},
         "url": "u2", "size": 2},
        {"filename": "numpy-2.3.0-cp314-cp314-manylinux_2_28_x86_64.whl", "digests": {"sha256": "c" * 64},
         "url": "u3", "size": 3},
        {"filename": "numpy-2.3.0.tar.gz", "digests": {"sha256": "d" * 64}, "url": "u4", "size": 4},
    ]
    assert wheels.choose_pc(lk, files, "pc-cp313-x86_64").filename.endswith("cp313-manylinux_2_28_x86_64.whl")
    assert wheels.choose_pc(lk, files, "pc-cp314-x86_64").filename.startswith("numpy-2.3.0-cp314")
    with pytest.raises(wheels.WheelError):   # the sdist never: install-pc.sh is --only-binary
        wheels.choose_pc(lk, files[3:], "pc-cp313-x86_64")
    # Only a newer build: taken (a newer computer can still use it).
    assert wheels.choose_pc(lk, files[:1], "pc-cp313-x86_64").url == "u1"


def test_a_lock_s_picks_are_looked_up_once_and_cached(kit_home):  # noqa: F811
    seen = []

    def files_of(lk):
        seen.append(lk.name)
        h = next(iter(sorted(lk.hashes)))
        name = NUMPY_WHL if lk.name == "numpy" else TQDM_WHL
        return [{"filename": name, "digests": {"sha256": h}, "url": "https://x/" + name, "size": 9}]

    text = pc_lock_text()
    assert wheels.cached_pc_picks("pc-cp313-x86_64", text) is None
    first = wheels.resolve_pc("pc-cp313-x86_64", text, files_of)
    assert [p.filename for p in first] == [NUMPY_WHL, TQDM_WHL] and seen == ["numpy", "tqdm"]
    assert wheels.resolve_pc("pc-cp313-x86_64", text, files_of) == first and len(seen) == 2


# ─── the kit builder ──────────────────────────────────────────────────────

def test_the_rooms_tick_box_brings_every_room_piece_with_its_pin(room_home, monkeypatch):
    online_server_holds_everything(media_cache.CACHE_ROOT)
    monkeypatch.setattr(wheels, "cached_pc_picks", lambda name, text: picks())
    rel = Release(VERSION, "v1.5.0", COMMIT, assets={"DomovoiSatelliteSetup.exe": {"size": len(SETUP),
                                                                                    "sha256": sha(SETUP)}})
    ps = {p.id: p for p in parts.rooms_parts(rel, small_pins())}
    assert set(ps) == {"rooms:windows-setup", "rooms:debian", "rooms:windows-runtime", "rooms:linux-python",
                       "rooms:linux-wheels", "rooms:oww:alexa_v0.1.onnx", "rooms:pi-cache"}
    assert all(p.group == "rooms" and p.kind == "rooms" for p in ps.values())
    assert ps["rooms:windows-runtime"].pinned_by == "satellite/windows-runtime.json"
    assert ps["rooms:linux-python"].pinned_by == "satellite/locks/linux-python.txt"
    assert ps["rooms:linux-wheels"].pinned_by.startswith("satellite/locks/pc-")
    assert ps["rooms:debian"].pinned_by == "domovoi/usb_stick/debian-image.json"
    assert ps["rooms:oww:alexa_v0.1.onnx"].licence == "CC-BY-NC-SA-4.0"
    # The Pi's packages have no pin, and say so.
    assert ps["rooms:pi-cache"].pinned_by is None and "from the house" in ps["rooms:pi-cache"].note
    # The server holds everything: exact sizes, nothing to download.
    assert all(p.exact and p.download == 0 for p in ps.values()), {p.id: p.download for p in ps.values()}
    assert ps["rooms:linux-wheels"].size == len(WHL_313) + len(WHL_ANY)


def test_a_server_that_lacks_a_room_piece_says_it_must_download_it(room_home, monkeypatch):
    rel = Release(VERSION, "v1.5.0", COMMIT)
    ps = {p.id: p for p in parts.rooms_parts(rel, small_pins())}
    assert ps["rooms:windows-runtime"].download == len(WIN_PY) + len(WIN_PIP) + len(WIN_WHL)
    assert not ps["rooms:linux-wheels"].exact and ps["rooms:linux-wheels"].download > 0
    assert ps["rooms:pi-cache"].download > 0 and ps["rooms:pi-cache"].label == "the rooms' parts"


def test_under_never_a_missing_room_piece_is_a_plain_refusal(room_home, monkeypatch):
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    rel = Release(VERSION, "v1.5.0", COMMIT)
    cat = plan.Catalogue(release=rel, parts=parts.rooms_parts(rel, small_pins()))
    out = plan.plan(cat, plan.Selection(), tools_ok=lambda: True, store_problem=lambda: None)
    assert out["refusal"]["reason"] == "internet_off"
    assert out["refusal"]["message"] == ("This server has no internet, so it can't fetch: the rooms' parts. "
                                         "Untick them, or allow internet for a while.")
    # Rooms unticked: nothing of them is asked for.
    out = plan.plan(cat, plan.Selection(includes=("ai",)), tools_ok=lambda: True, store_problem=lambda: None)
    assert out["refusal"] is None or out["refusal"]["reason"] != "internet_off"


def build_kit(monkeypatch) -> Path:
    from domovoi.kit_builder import fatimage, jobs

    online_server_holds_everything(media_cache.CACHE_ROOT)
    monkeypatch.setattr(wheels, "cached_pc_picks", lambda name, text: picks())
    monkeypatch.setattr(wheels, "resolve_pc", lambda name, text, files_of=None: picks())
    monkeypatch.setattr(fatimage, "FatImage", _FolderImage)
    monkeypatch.setattr(plan, "free_bytes", lambda p: 10 ** 13)
    rel = Release(VERSION, "v1.5.0", COMMIT, assets={"DomovoiSatelliteSetup.exe": {"size": len(SETUP),
                                                                                    "sha256": sha(SETUP)}})
    from domovoi.offline_kit import Source

    release_sums = parts.Part(
        id="release:sums", group="base", kind="release", name="SHA256SUMS", version=VERSION, licence="MIT",
        size=0, exact=False, download=0,
        acquire=lambda ctx: [Source("release/SHA256SUMS", f"{sha(SETUP)}  DomovoiSatelliteSetup.exe\n".encode())])
    jobs.build(rel, plan.Selection(), [release_sums, *parts.rooms_parts(rel, small_pins())])
    return _FolderImage.last


def test_a_kit_with_rooms_carries_them_and_passes_the_checker(room_home, monkeypatch):
    folder = build_kit(monkeypatch)
    report = verify(folder, release_key=None)
    assert report.ok, report.errors
    kit = json.loads((folder / "kit.json").read_text())
    listed = {f["path"]: c for c in kit["components"] for f in c["files"]}
    for path in ("rooms/DomovoiSatelliteSetup.exe", "rooms/debian-13.2.0-amd64-netinst.iso",
                 "rooms/windows/3.14.8/python-3.14.8-embed-amd64.zip", f"rooms/linux-python/{LNX_NAME}",
                 f"rooms/linux-wheels/{NUMPY_WHL}", "rooms/satellite/oww_models/alexa_v0.1.onnx",
                 "rooms/satellite/wheels/3.13/numpy-2.3.0-cp313-cp313-manylinux_2_28_aarch64.whl",
                 "rooms/satellite/debs/trixie/mpg123_1.0_arm64.deb"):
        assert path in listed, path
    assert listed[f"rooms/linux-wheels/{NUMPY_WHL}"]["pinned_by"].startswith("satellite/locks/pc-")
    assert listed["rooms/satellite/debs/trixie/mpg123_1.0_arm64.deb"]["pinned_by"] is None
    assert (folder / "rooms" / "linux-wheels" / NUMPY_WHL).read_bytes() == WHL_313


# ─── the offline server takes them in, and serves them ──────────────────

def install_from(folder: Path, includes=("rooms",)) -> dict:
    """What provision.sh does: the files staged off the stick (a copy),
    then kit_rooms.install as the service user, with the wake models a kit
    always has in wake/."""
    stage = folder.parent / (folder.name + "-stage")
    shutil.copytree(folder, stage)
    (stage / "wake").mkdir(exist_ok=True)
    for n in kit_rooms.OWW_ON_KIT:
        (stage / "wake" / n).write_bytes(OWW[n])
    return kit_rooms.install(stage, version=VERSION, includes=list(includes), code_dir=_code(folder))


def _code(folder: Path) -> Path:
    return main.SATELLITE_CODE_DIR.parent


def wipe_cache() -> None:
    shutil.rmtree(media_cache.CACHE_ROOT)


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)

    async def open_channel(*a, **k):
        return None

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", open_channel)


async def test_an_offline_server_serves_every_room_piece_from_the_kit(room_home, monkeypatch, offline):
    folder = build_kit(monkeypatch)
    wipe_cache()   # a different server: the one installed from the stick
    doc = install_from(folder)
    assert doc["missing"] == [] and doc["refused"] == [], doc
    assert kit_rooms.done(VERSION)
    # Windows: the Setup (its record says where it came from) and its runtime.
    assert satellite_setup.cached_file() is not None
    st = satellite_setup.status(probe=False)
    assert st["ready"] and st["release"] == f"offline kit v{VERSION}" and st["kit_note"] is None
    resp = await main.satellite_windows_file("numpy-2.3.0-cp314-cp314-win_amd64.whl")
    assert Path(resp.path).read_bytes() == WIN_WHL
    # Linux: the Python and the packages, as pip's --find-links page.
    resp = await main.satellite_linux_file(LNX_NAME)
    assert Path(resp.path).read_bytes() == LNX_PY
    page = (await main.satellite_linux_wheel_index("pc-cp313-x86_64")).body.decode()
    assert re.findall(r'href="([^"]+)"', page) == [NUMPY_WHL, TQDM_WHL]
    resp = await main.satellite_linux_wheel("pc-cp313-x86_64", NUMPY_WHL)
    assert Path(resp.path).read_bytes() == WHL_313
    # A Pi's card: every pinned wake model, the wheels and the base debs.
    assert fetchers.missing_oww_models() == []
    assert fetchers.base_debs_missing("trixie") == []
    assert media_cache.status("3.13", "trixie")["wheels"]["ok"]
    # The room stick: Debian's image, held to its pin.
    assert stick_image.iso_matches(media_cache.CACHE_ROOT / "usb_stick" / "13.2.0" /
                                   "debian-13.2.0-amd64-netinst.iso", stick_image.load_pin())


def test_a_windows_room_installs_from_the_server_with_no_internet(room_home, monkeypatch, offline, tmp_path):
    """satellite/win_install.fetch_pinned (what the Setup runs) against this
    server, with the internet unreachable: every file from the core."""
    import asyncio
    import urllib.error

    from satellite import win_install

    folder = build_kit(monkeypatch)
    wipe_cache()
    install_from(folder)

    class Answer(io.BytesIO):
        status = 200

    def opener(url, timeout):
        if not url.startswith("http://192.168.1.20:6370/"):
            raise urllib.error.URLError("no internet")
        name = url.rsplit("/", 1)[-1]
        try:
            resp = asyncio.run(main.satellite_windows_file(name))
        except HTTPException as e:
            raise urllib.error.URLError(f"HTTP {e.status_code}") from None
        return Answer(Path(resp.path).read_bytes())

    dest = tmp_path / "laptop"
    for e in sw.entries(windows_lock()):
        assert win_install.fetch_pinned(e, dest, core="http://192.168.1.20:6370", opener=opener,
                                        log=lambda s: None) == "core"
    assert (dest / "python-3.14.8-embed-amd64.zip").read_bytes() == WIN_PY


def test_a_linux_room_takes_its_packages_from_the_server_when_it_serves_them():
    script = (REPO / "satellite" / "scripts" / "install-pc.sh").read_text(encoding="utf-8")
    assert 'WHEELS_URL="$CORE/v1/satellite-linux/wheels/$LOCK_NAME/"' in script
    assert 'PIP_FROM=(--no-index --find-links "$WHEELS_URL")' in script
    pip = script[script.index('as_sat "$VENV/bin/pip" install'):]
    pip = pip[:pip.index("\nOWW_DIR=")]
    assert '${PIP_FROM[@]+"${PIP_FROM[@]}"}' in pip and "--require-hashes" in pip, "hashes either way"
    assert "offline kit made with Rooms ticked" in pip, "a plain message when neither source works"


async def test_the_server_serves_only_wheels_its_locks_allow(room_home, offline):
    wd = sl.wheel_dir()
    wd.mkdir(parents=True)
    (wd / NUMPY_WHL).write_bytes(WHL_313)
    # Not complete (no tqdm): no page, so install-pc.sh goes to PyPI.
    with pytest.raises(HTTPException) as e:
        await main.satellite_linux_wheel_index("pc-cp313-x86_64")
    assert e.value.status_code == 404
    (wd / TQDM_WHL).write_bytes(b"something else")
    with pytest.raises(HTTPException):
        await main.satellite_linux_wheel_index("pc-cp313-x86_64")
    with pytest.raises(HTTPException):
        await main.satellite_linux_wheel("pc-cp313-x86_64", TQDM_WHL)
    for lock, name in (("pc-cp313-x86_64", "../" + NUMPY_WHL), ("../pc-cp313-x86_64", NUMPY_WHL),
                       ("pc-cp399-sparc", NUMPY_WHL), ("pc-cp313-x86_64", "linux-python.txt")):
        with pytest.raises(HTTPException) as e:
            await main.satellite_linux_wheel(lock, name)
        assert e.value.status_code == 404


def test_a_file_that_isn_t_its_pin_is_refused_and_named(room_home, monkeypatch, offline):
    folder = build_kit(monkeypatch)
    wipe_cache()
    stage = folder.parent / (folder.name + "-bad")
    shutil.copytree(folder, stage)
    (stage / "rooms" / "linux-python" / LNX_NAME).write_bytes(b"x" * len(LNX_PY))
    (stage / "rooms" / "linux-wheels" / "evil-1.0-py3-none-any.whl").write_bytes(b"evil")
    (stage / "rooms" / "DomovoiSatelliteSetup.exe").write_bytes(b"MZ-not-the-release")
    (stage / "rooms" / "satellite" / "etc").mkdir()
    (stage / "rooms" / "satellite" / "etc" / "passwd").write_bytes(b"root")
    doc = kit_rooms.install(stage, version=VERSION, includes=["rooms"], code_dir=main.SATELLITE_CODE_DIR.parent)
    refused = " ".join(doc["refused"])
    for what in (LNX_NAME, "evil-1.0", "DomovoiSatelliteSetup.exe", "rooms/satellite/etc/passwd"):
        assert what in refused, what
    assert {"linux-python", "windows-setup"} <= set(doc["missing"])
    assert not (sl.bucket() / LNX_NAME).exists() and not (sl.wheel_dir() / "evil-1.0-py3-none-any.whl").exists()
    assert not (media_cache.CACHE_ROOT / "etc").exists()
    assert "refused" in kit_rooms.summary(doc)


# ─── an update from a newer kit (P5) refreshes them ─────────────────────

def test_held_names_where_install_puts_each_rooms_file(room_home, monkeypatch, offline):
    """The update from a kit sends only the rooms' files the server lacks
    (kit_update.plan_need asks kit_rooms.held): every file install places
    is held afterwards, none before, and a changed byte isn't."""
    folder = build_kit(monkeypatch)
    kit = json.loads((folder / "kit.json").read_text(encoding="utf-8"))
    rooms = [f for c in kit["components"] for f in c["files"] if f["path"].startswith("rooms/")]
    assert len(rooms) > 8
    wipe_cache()
    assert not any(kit_rooms.held(f["path"], f["sha256"], f["size"]) for f in rooms)
    doc = install_from(folder)
    assert doc["refused"] == []
    for f in rooms:
        assert kit_rooms.held(f["path"], f["sha256"], f["size"]), f["path"]
    for n in kit_rooms.OWW_ON_KIT:
        assert kit_rooms.held(f"wake/{n}", sha(OWW[n]), len(OWW[n]))
    first = rooms[0]
    assert not kit_rooms.held(first["path"], "0" * 64, first["size"])
    for path in ("rooms/../etc/passwd", "rooms/satellite/etc/passwd", "wake/domovoi_v1.onnx", "kit.json",
                 "rooms/other-13.2.0-amd64-netinst.iso"):
        assert kit_rooms.server_copy(path) is None, path


def test_the_command_line_takes_the_version_and_tick_boxes_from_kit_json(room_home, monkeypatch, offline,
                                                                         capsys, tmp_path):
    """apply-update.sh (offline_rooms) runs ``install <stage>`` alone after
    an update from a kit: kit.json says which release and what was ticked."""
    folder = build_kit(monkeypatch)
    wipe_cache()
    stage = tmp_path / "update-stage"
    shutil.copytree(folder, stage)
    assert kit_rooms._main(["install", str(stage), "--code-dir", str(room_home)]) == 0
    rec = kit_rooms.read_record()
    assert rec["version"] == VERSION and "rooms" in rec["includes"]
    assert "room files from the offline kit" in capsys.readouterr().out
    assert satellite_setup.cached_file() is not None
    (stage / "kit.json").unlink()
    assert kit_rooms._main(["install", str(stage), "--code-dir", str(room_home)]) == 1
    assert "kit.json can't be read" in capsys.readouterr().err


# ─── plain messages for what the kit didn't have ─────────────────────────

def test_a_kit_without_rooms_says_so_where_internet_would_be_asked_for(room_home, monkeypatch, offline):
    stage = room_home.parent / "bare-kit"
    (stage / "wake").mkdir(parents=True)
    doc = kit_rooms.install(stage, version=VERSION, includes=["ai"], code_dir=room_home)
    assert set(doc["missing"]) >= {"windows-setup", "windows-runtime", "linux-python", "linux-wheels", "pi",
                                   "debian"}
    note = kit_rooms.note("the Windows room Setup", ready=False)
    assert note and "without Rooms ticked" in note and "update from it" in note
    assert kit_rooms.note("anything", ready=True) is None
    # Each place a room's file would be fetched says it.
    assert satellite_setup.status(probe=False)["kit_note"] == note
    assert "without Rooms ticked" in satellite_setup.fetch()[1]
    assert "without Rooms ticked" in fetchers._turned_off("wheels")[1]
    from domovoi.usb_stick import jobs as stick_jobs

    monkeypatch.setattr(stick_jobs, "can_repack", lambda: True)
    why = stick_jobs.refusal(stick_image.load_pin())
    assert why["reason"] == "internet_off" and "without Rooms ticked" in why["message"]
    assert "not on the kit: " in kit_rooms.summary(doc) and "the Windows room Setup" in kit_rooms.summary(doc)


def test_a_kit_with_rooms_but_a_missing_piece_names_it(room_home, offline):
    stage = room_home.parent / "rooms-kit"
    (stage / "rooms").mkdir(parents=True)
    kit_rooms.install(stage, version=VERSION, includes=["rooms"], code_dir=room_home)
    note = kit_rooms.note("a Linux room's own Python", ready=False)
    assert note.startswith("The offline kit this server was set up from (Domovoi 1.5.0) has no a Linux") \
        or "has no a Linux room's own Python" in note


def test_a_server_not_installed_from_a_kit_keeps_today_s_words(room_home, offline):
    assert kit_rooms.note("the Windows room Setup", ready=False) is None
    assert satellite_setup.fetch()[1].startswith("internet access is off")
    assert fetchers._turned_off("wheels")[1].endswith("using what is already cached")


# ─── the install script and the routes' tiers ────────────────────────────

def test_provision_takes_the_rooms_in_with_the_phone_app_and_never_fails_on_them():
    s = (REPO / "scripts" / "linux" / "provision.sh").read_text(encoding="utf-8")
    start = s.index("\nkit_rooms() {\n")
    body = s[start:s.index("\n}\n", start)]
    assert "svc_py -m domovoi.kit_rooms install" in body and "die " not in body
    assert "rooms/*" in body and "release/SHA256SUMS" in body and 'chown -R "$SVC_USER"' in body
    do = s[s.index("\ndo_phone_app() {\n"):]
    assert do.splitlines()[3].strip() == "if kit; then kit_rooms; fi"
    check = s[s.index("\ncheck_phone_app() {\n"):]
    assert "if kit && ! kit_rooms_done; then return 1; fi" in check[:400]


def test_the_wheel_routes_are_open_like_the_python_and_the_status_an_admin_read():
    from domovoi.admin_auth import require_admin_read

    routes = {r.path: r for r in main.app.routes if hasattr(r, "dependant")}
    for path in ("/v1/satellite-linux/wheels/{lock}/", "/v1/satellite-linux/wheels/{lock}/{name}"):
        assert not routes[path].dependencies and routes[path].methods == {"GET"}
    assert any(d.dependency is require_admin_read for d in routes["/v1/admin/satellite-linux"].dependencies)


async def test_a_frozen_channel_serves_no_wheels(monkeypatch):
    async def frozen(*a, **k):
        raise HTTPException(status_code=423, detail="frozen")

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", frozen)
    for call in (main.satellite_linux_wheel_index("pc-cp313-x86_64"),
                 main.satellite_linux_wheel("pc-cp313-x86_64", NUMPY_WHL)):
        with pytest.raises(HTTPException) as e:
            await call
        assert e.value.status_code == 423
